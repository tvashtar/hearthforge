"""Layer-1 mechanical metrics: pure SQL/JSON over event log + transcript."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

POLLING_COMMANDS = ("get_scene_state", "get_character_sheet")


def _connect(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def max_event_id(db_path: Path) -> int:
    with _connect(db_path) as db:
        row = db.execute("SELECT COALESCE(MAX(id), 0) FROM event_log").fetchone()
    return row[0]


def _matcher_clauses(done_when: dict) -> tuple[str, list]:
    """Extra SQL predicates for the optional done_when matchers: `inputs`
    (exact match per key against the inputs column), `result` (exact match
    per json.path against the result column), and `refusal_contains`
    (substring match against result.refusal).

    Matcher keys/paths are interpolated into the SQL via f-string (not
    parameterized) because they come from checked-in scenario YAML, which is
    trusted input, not user-supplied at runtime; only the *values* are bound
    as query parameters.
    """
    clauses: list[str] = []
    params: list = []
    for key, val in (done_when.get("inputs") or {}).items():
        clauses.append(f"AND json_extract(inputs, '$.{key}') = ?")
        params.append(val)
    for path, val in (done_when.get("result") or {}).items():
        clauses.append(f"AND json_extract(result, '$.{path}') = ?")
        params.append(val)
    if done_when.get("refusal_contains"):
        clauses.append("AND json_extract(result, '$.refusal') LIKE ?")
        params.append(f"%{done_when['refusal_contains']}%")
    return " ".join(clauses), params


def beat_done(db_path: Path, done_when: dict, *, after_id: int) -> bool:
    ok = 1 if done_when.get("ok", True) else 0
    extra_sql, extra_params = _matcher_clauses(done_when)
    with _connect(db_path) as db:
        row = db.execute(
            "SELECT COUNT(*) FROM event_log WHERE id > ? AND command = ?"
            f" AND json_extract(result, '$.ok') = ? {extra_sql}",
            (after_id, done_when["command"], ok, *extra_params),
        ).fetchone()
    return row[0] > 0


def campaign_open(db_path: Path) -> bool:
    """True once a successful open_campaign event lands in the event log.

    Same shape as beat_done: a pure query over the event log, no transcript
    parsing. after_id=0 because the opening handshake has no prior marker —
    any successful open_campaign in the campaign's history counts.
    """
    return beat_done(db_path, {"command": "open_campaign", "ok": True}, after_id=0)


def classify_beat_failure(db_path: Path, done_when: dict, *, after_id: int) -> dict:
    """Classify why a beat failed to reach done_when within its message budget.

    Queryable from the event log only (no transcript parsing):
    - "not_attempted": no event-log row for done_when's command after the
      beat's marker — the DM never tried the mechanical action at all.
    - "refused": the command was attempted but never satisfied done_when's
      full criteria (ok plus any inputs/result/refusal_contains matchers,
      per beat_done's semantics); the most recent attempt's refusal/digest
      is surfaced for triage.

    Callers add the "timeout" reason themselves for the turn/run-timeout
    paths, which abort before a beat can be classified this way.

    INVARIANT: this deliberately does NOT re-apply done_when's matchers — it
    only splits "command never ran" from "command ran but the beat is still
    unmet". It is therefore only correct when called after beat_done has
    already returned False for the same after_id/done_when (the sole caller,
    evals/runner.py, does exactly this). Consequence for a would-be new
    caller: the "refused" branch surfaces the last matching-command row's
    refusal-or-digest, so if a beat failed only an inputs/result matcher
    (not on ok), that last row may be an ok=True row and this returns its
    digest, not a refusal. That is intentional triage output under the
    caller invariant — do not call this standalone and trust `reason` blindly.
    """
    with _connect(db_path) as db:
        rows = db.execute(
            "SELECT result FROM event_log WHERE id > ? AND command = ? ORDER BY id",
            (after_id, done_when["command"]),
        ).fetchall()
    if not rows:
        return {"reason": "not_attempted"}
    last = json.loads(rows[-1][0])
    return {"reason": "refused", "refusal": last.get("refusal") or last.get("digest")}


# Real commands name the acting character differently per command.
_ACTOR_KEYS = ("actor", "caster", "character", "attacker")


def compute_metrics(db_path: Path, transcript_path: Path, pc_name: str = "Kira") -> dict:
    with _connect(db_path) as db:
        events = db.execute(
            "SELECT id, command, inputs, result, rolls FROM event_log ORDER BY id"
        ).fetchall()

    refusals = sum(
        1 for _, _, _, res, _ in events if json.loads(res).get("ok") is False
    )
    crashes = sum(
        1 for _, _, _, res, _ in events
        if str(json.loads(res).get("digest", "")).startswith("ENGINE CRASH")
    )
    retry_loops = 0
    for prev, cur in zip(events, events[1:]):
        same = prev[1] == cur[1] and prev[2] == cur[2]
        if same and not json.loads(prev[3]).get("ok") and not json.loads(cur[3]).get("ok"):
            retry_loops += 1
    orphaned = 0
    for eid, cmd, _, res, _ in events:
        data = json.loads(res).get("data") or {}
        if cmd == "cast_spell" and data.get("needs_ruling"):
            followed = any(e[1] == "dm_ruling" and e[0] > eid for e in events)
            if not followed:
                orphaned += 1
    polling = sum(1 for _, cmd, _, _, _ in events if cmd in POLLING_COMMANDS)
    supplied_violations = 0
    for _, _, inputs, _, rolls_json in events:
        parsed_inputs = json.loads(inputs)
        actor = next(
            (parsed_inputs[k] for k in _ACTOR_KEYS if parsed_inputs.get(k)), None
        )
        rolls = json.loads(rolls_json or "[]")
        supplied = any(isinstance(r, dict) and r.get("player_supplied") for r in rolls)
        if actor and actor != pc_name and supplied:
            supplied_violations += 1

    player_messages = tool_calls = schema_rejections = 0
    for line in transcript_path.read_text().splitlines():
        entry = json.loads(line)
        if entry["type"] == "player_message":
            player_messages += 1
        elif entry["type"] == "tool_call":
            tool_calls += 1
            if entry.get("is_error"):  # legacy pre-TVA-32 transcript shape
                schema_rejections += 1
        elif entry["type"] == "tool_result" and entry.get("is_error"):
            schema_rejections += 1

    return {
        "refusals": refusals,
        "refusal_retry_loops": retry_loops,
        "crashes": crashes,
        "orphaned_tier2": orphaned,
        "player_supplied_violations": supplied_violations,
        "schema_rejections": schema_rejections,
        "polling_reads": polling,
        "player_messages": player_messages,
        "tool_calls": tool_calls,
        "tool_calls_per_player_message": (
            round(tool_calls / player_messages, 2) if player_messages else 0.0
        ),
    }
