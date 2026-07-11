# Eval Event-Shape Fixes (TVA-31, TVA-32) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the eval harness read the event shapes production actually writes (TVA-31) and record successful tool results in transcripts so the judge can grade mechanical fidelity (TVA-32).

**Architecture:** Two independent fixes to `evals/`. TVA-31 rewrites `compute_metrics` to select the real `rolls` column, read `needs_ruling` from `result.data`, and recognize all actor kwarg names; its tests are rebuilt to create events through `registry.execute()` against a real campaign store. TVA-32 changes `_dm_turn` in the runner to write every `ToolResultBlock` as a `tool_result` transcript entry linked by `tool_use_id`.

**Tech Stack:** Python 3.12, pytest, sqlite3, claude-agent-sdk dataclasses (`ToolUseBlock`, `ToolResultBlock`).

## Global Constraints

- FC-1 envelope is frozen: `{ok, command, refusal, digest, data, gm_only, event_ids}` — metrics adapt to it, never the reverse.
- `event_log` schema is frozen (docs/SCHEMA.md): `id, created_at, command, inputs, result, rolls, is_ruling, rationale`.
- Tests must go through `registry.execute()` (repo convention for integration-level assertions); test-only commands follow the register/deregister fixture pattern from `tests/commands/test_registry.py`.
- Old bundles (pre-TVA-32 transcripts) must still grade without crashing: keep legacy `tool_call`+`is_error` handling in metrics.
- Lint: `uv run ruff check src tests evals` (line length 100).

## Verified facts the plan relies on

- `registry.execute()` stores roll dumps in the `rolls` column (`registry.py:96-101`), never in `result`.
- `needs_ruling` lives in `result.data` (`spells.py:434`, `resources.py:183`).
- `attack` outside combat refuses with "no combat is active" → deterministic refusal-retry rows.
- `cast_spell(ctx, caster="Brother Aldric", spell_slug="bless")` works with no targets (see `tests/commands/test_spells.py:108-113`) and returns Tier-2 `data.needs_ruling=True`.
- Engine refuses `player_value` for non-PCs (`checks.py:117-125`), so the violation metric guards an engine regression; the test plants it via a test-registered command that rolls with `player_value` — still a real event row through `registry.execute()`.
- Real command actor kwargs: `attacker` (attack), `character` (skill_check/saving_throw/death_save), `caster` (cast_spell), `actor` (some others).
- SDK dataclasses: `ToolUseBlock(id, name, input)`, `ToolResultBlock(tool_use_id, content: str|list[dict]|None, is_error: bool|None)`, `AssistantMessage(content, model, ...)`, `UserMessage(content, ...)`, `ResultMessage(subtype, duration_ms, duration_api_ms, is_error, num_turns, session_id, ..., usage)`.

---

### Task 1: TVA-31 — metrics read production event shapes

**Files:**
- Modify: `evals/metrics.py:33-90` (`compute_metrics`)
- Rewrite: `tests/evals/test_metrics.py`

**Interfaces:**
- Consumes: `tests/conftest.py` fixtures `ctx`/`party` (root conftest applies to `tests/evals/`).
- Produces: `compute_metrics(db_path, transcript_path, pc_name="Kira") -> dict` (signature unchanged); transcript entries of the NEW shape (`tool_call` without `is_error`, separate `tool_result` with `is_error`) plus legacy shape both parse. Task 2's runner writes the new shape.

- [x] **Step 1: Rewrite the test file (failing tests)**

Replace `tests/evals/test_metrics.py` entirely:

```python
"""Metric tests build events through registry.execute() — production schema,
real FC-1 envelopes, real rolls column — so eval metrics can never drift from
what the engine actually writes (TVA-31)."""

import json

import pytest

from dm_engine.commands import registry
from dm_engine.commands.envelope import CommandResult
from evals.metrics import beat_done, compute_metrics, max_event_id


def _boom(ctx, **kwargs) -> CommandResult:
    raise RuntimeError("planted crash")


def _supplied_roll(ctx, **kwargs) -> CommandResult:
    r = ctx.roller.roll("1d20", player_value=kwargs.get("player_value"))
    return CommandResult(ok=True, command="_eval_supplied_roll",
                         digest=f"rolled {r.total}", data={"total": r.total})


@pytest.fixture(autouse=True)
def _register_test_commands():
    handlers = {"_eval_boom": _boom, "_eval_supplied_roll": _supplied_roll}
    registry._COMMANDS.update(handlers)
    yield
    for name in handlers:
        registry._COMMANDS.pop(name, None)


@pytest.fixture()
def event_db(party):
    """One planted defect per metric, all through registry.execute()."""
    ctx = party  # events 1-2: create_character x2 (party fixture)
    ok = registry.execute("skill_check", ctx, character="Kira",
                          skill="athletics", dc=10)                    # 3
    assert ok.ok
    # refusal retry loop: same command, identical inputs, consecutive
    registry.execute("attack", ctx, attacker="Kira", target="Bandit")  # 4
    registry.execute("attack", ctx, attacker="Kira", target="Bandit")  # 5
    # crash row, committed by _append_crash_event
    with pytest.raises(RuntimeError):
        registry.execute("_eval_boom", ctx)                            # 6
    # orphaned tier-2: needs_ruling with no later dm_ruling
    cast = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                            spell_slug="bless")                        # 7
    assert cast.ok and cast.data["needs_ruling"]
    # player_supplied roll attributed to a non-PC (engine-regression guard)
    registry.execute("_eval_supplied_roll", ctx, actor="Brother Aldric",
                     player_value=15)                                  # 8
    # PC-supplied roll: must NOT count as a violation
    registry.execute("skill_check", ctx, character="Kira",
                     skill="athletics", dc=10, player_value=15)        # 9
    registry.execute("get_scene_state", ctx)                           # 10
    registry.execute("get_scene_state", ctx)                           # 11
    return ctx.store.root / "campaign.sqlite"


@pytest.fixture()
def fixture_transcript(tmp_path):
    """New-shape entries (tool_result carries is_error) plus one legacy
    pre-TVA-32 error entry that must still count."""
    path = tmp_path / "transcript.jsonl"
    lines = [
        {"type": "player_message", "text": "I attack"},
        {"type": "tool_call", "id": "tu_1", "name": "mcp__dm-engine__attack",
         "input": {"attacker": "Kira"}},
        {"type": "tool_result", "tool_use_id": "tu_1", "is_error": True,
         "content": "invalid params"},
        {"type": "tool_call", "id": "tu_2", "name": "mcp__dm-engine__attack",
         "input": {"attacker": "Kira"}},
        {"type": "tool_result", "tool_use_id": "tu_2", "is_error": False,
         "content": [{"type": "text", "text": "{\"ok\": true}"}]},
        {"type": "dm_text", "text": "You swing..."},
        {"type": "player_message", "text": "again"},
        # legacy shape: error results were logged as tool_call entries
        {"type": "tool_call", "name": "(result)", "is_error": True,
         "content": "schema mismatch"},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines))
    return path


def test_max_event_id(event_db):
    assert max_event_id(event_db) == 11


def test_beat_done_respects_after_id_and_ok(event_db):
    assert beat_done(event_db, {"command": "cast_spell", "ok": True}, after_id=0)
    assert not beat_done(event_db, {"command": "cast_spell", "ok": True}, after_id=7)
    assert beat_done(event_db, {"command": "attack", "ok": False}, after_id=0)
    assert not beat_done(event_db, {"command": "end_session", "ok": True}, after_id=0)


def test_metrics_catch_each_planted_defect(event_db, fixture_transcript):
    m = compute_metrics(event_db, fixture_transcript)
    assert m["refusals"] == 3          # 2 attack refusals + 1 crash row (ok=false)
    assert m["refusal_retry_loops"] == 1
    assert m["crashes"] == 1
    assert m["orphaned_tier2"] == 1
    assert m["player_supplied_violations"] == 1  # Aldric only; Kira's is legal
    assert m["polling_reads"] == 2
    assert m["schema_rejections"] == 2  # 1 new-shape + 1 legacy
    assert m["player_messages"] == 2
    assert m["tool_calls"] == 3        # legacy "(result)" rows count as before
    assert m["tool_calls_per_player_message"] == 1.5
```

- [x] **Step 2: Run to verify the right failures**

Run: `uv run pytest tests/evals/test_metrics.py -v`
Expected: `test_metrics_catch_each_planted_defect` FAILS on `orphaned_tier2` (0 != 1) — proving the old code misses production shapes. Other tests may pass/fail incidentally.

- [x] **Step 3: Rewrite `compute_metrics`**

Replace `compute_metrics` in `evals/metrics.py` (lines 33-90) with:

```python
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
```

(`beat_done`, `max_event_id`, `_connect`, `POLLING_COMMANDS` unchanged.)

- [x] **Step 4: Run tests and lint**

Run: `uv run pytest tests/evals/test_metrics.py -v && uv run ruff check evals tests`
Expected: all PASS, no lint errors.

- [x] **Step 5: Full suite**

Run: `uv run pytest`
Expected: all pass (other eval tests don't touch these shapes).

- [x] **Step 6: Commit**

```bash
git add evals/metrics.py tests/evals/test_metrics.py
git commit -m "fix: eval metrics read production event shapes

compute_metrics now selects the real event_log.rolls column, reads
needs_ruling from result.data (FC-1), and recognizes all actor kwarg
names (actor/caster/character/attacker). Metric tests build events
through registry.execute() so mock/production divergence cannot recur.

Fixes TVA-31."
```

---

### Task 2: TVA-32 — record all tool results in eval transcripts

**Files:**
- Modify: `evals/runner.py:32-108` (`TranscriptWriter.write`, `_dm_turn`)
- Modify: `tests/evals/test_runner.py` (add `_dm_turn` capture test)

**Interfaces:**
- Consumes: transcript entry shapes established in Task 1's metrics parsing (`tool_call` without `is_error`; `tool_result` with `tool_use_id`/`is_error`/`content`).
- Produces: transcript.jsonl entries: `{"type": "tool_call", "id", "name", "input"}` and `{"type": "tool_result", "tool_use_id", "is_error", "content"}` for every tool round-trip, success or error.

- [x] **Step 1: Write the failing test**

Append to `tests/evals/test_runner.py`:

```python
import asyncio

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from evals.runner import RunResult, _dm_turn


class _StubClient:
    """Replays canned SDK messages; shape-compatible with ClaudeSDKClient."""

    def __init__(self, messages):
        self._messages = messages

    async def query(self, prompt):
        pass

    async def receive_response(self):
        for m in self._messages:
            yield m


def test_dm_turn_records_successful_tool_results(tmp_path):
    envelope = json.dumps({"ok": True, "digest": "Kira hits", "gm_only": False})
    messages = [
        AssistantMessage(
            model="m",
            content=[
                TextBlock(text="Rolling..."),
                ToolUseBlock(id="tu_1", name="mcp__dm-engine__attack",
                             input={"attacker": "Kira", "target": "bandit"}),
            ],
        ),
        UserMessage(content=[
            ToolResultBlock(tool_use_id="tu_1", is_error=None,
                            content=[{"type": "text", "text": envelope}]),
        ]),
        AssistantMessage(model="m", content=[TextBlock(text="You hit!")]),
        ResultMessage(subtype="success", duration_ms=10, duration_api_ms=8,
                      is_error=False, num_turns=1, session_id="s",
                      usage={"output_tokens": 5}),
    ]
    from evals.runner import TranscriptWriter
    transcript = TranscriptWriter(tmp_path / "t.jsonl")
    result = RunResult(cell=None)
    narration = asyncio.run(
        _dm_turn(_StubClient(messages), "I attack", transcript, result)
    )
    entries = [json.loads(x) for x in (tmp_path / "t.jsonl").read_text().splitlines()]
    kinds = [e["type"] for e in entries]
    assert kinds == ["player_message", "dm_text", "tool_call", "tool_result", "dm_text"]
    call = entries[2]
    assert call == {"type": "tool_call", "id": "tu_1",
                    "name": "mcp__dm-engine__attack",
                    "input": {"attacker": "Kira", "target": "bandit"}}
    res = entries[3]
    assert res["tool_use_id"] == "tu_1"
    assert res["is_error"] is False
    assert envelope in json.dumps(res["content"])  # full envelope, untruncated
    assert narration == ["Rolling...", "You hit!"]
    assert result.turns[0]["usage"] == {"output_tokens": 5}
```

- [x] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/evals/test_runner.py -v`
Expected: FAIL — no `tool_result` entry is written (kinds mismatch), and `tool_call` still carries `is_error`.

- [x] **Step 3: Implement the capture**

In `evals/runner.py`:

a) Make `TranscriptWriter.write` defensive against non-JSON content:

```python
    def write(self, entry: dict) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(entry, default=repr) + "\n")
```

b) In `_dm_turn`, replace the `ToolUseBlock` entry (drop `is_error`, add the id):

```python
                elif isinstance(block, ToolUseBlock):
                    transcript.write(
                        {"type": "tool_call", "id": block.id,
                         "name": block.name, "input": block.input}
                    )
```

c) Replace the `UserMessage` branch to record every result, success or error, with the full structured content:

```python
        elif isinstance(msg, UserMessage):
            for block in msg.content if isinstance(msg.content, list) else []:
                if isinstance(block, ToolResultBlock):
                    transcript.write(
                        {"type": "tool_result", "tool_use_id": block.tool_use_id,
                         "is_error": bool(block.is_error), "content": block.content}
                    )
```

- [x] **Step 4: Run tests and lint**

Run: `uv run pytest tests/evals/ -v && uv run ruff check evals tests`
Expected: all PASS (Task 1's metrics tests already accept the new shape), no lint errors.

- [x] **Step 5: Full suite**

Run: `uv run pytest`
Expected: all pass.

- [x] **Step 6: Commit**

```bash
git add evals/runner.py tests/evals/test_runner.py
git commit -m "fix: record all tool results in eval transcripts

_dm_turn only logged ToolResultBlocks flagged is_error, so successful
command envelopes (totals, refusals, gm_only, needs_ruling) never
reached the judge, which grades exactly those properties. Every result
is now a tool_result entry linked by tool_use_id with its full content.

Fixes TVA-32."
```

---

## Post-plan notes (not tasks)

- README/docs: `docs/` documents the dm-eval harness; if it describes transcript entry shapes, update in the PR (merge convention).
- After merge, rerun the matrix; `dm-eval --judge-only evals/runs/20260711-194517` can recompute mechanical metrics for the aborted run's haiku/sonnet bundles, but judge scores need fresh transcripts.
