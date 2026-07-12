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


def beat_done(db_path: Path, done_when: dict, *, after_id: int) -> bool:
    ok = 1 if done_when.get("ok", True) else 0
    with _connect(db_path) as db:
        row = db.execute(
            "SELECT COUNT(*) FROM event_log WHERE id > ? AND command = ?"
            " AND json_extract(result, '$.ok') = ?",
            (after_id, done_when["command"], ok),
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
    - "refused": the command was attempted but never with the desired `ok`
      (per beat_done's semantics); the most recent attempt's refusal/digest
      is surfaced for triage.

    Callers add the "timeout" reason themselves for the turn/run-timeout
    paths, which abort before a beat can be classified this way.
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
