# Model Eval Harness (TVA-30) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `uv run dm-eval` harness that plays a fixed campaign scenario across a matrix of (model × effort) DM configurations via claude-agent-sdk, times each run, archives transcripts, computes mechanical metrics from the engine event log, grades transcripts with a blind LLM judge, and emits a comparison report.

**Architecture:** Top-level `evals/` package (never shipped in dm-engine). Per cell: harness builds an identical seeded campaign via `bootstrap_campaign` + `registry.execute`, spawns a DM session through `claude-agent-sdk` (real dm-engine MCP server, dm-session skill injected into the system prompt, no built-in tools), drives it with a beat-scripted Haiku player agent over the plain Anthropic API, then grades with SQL metrics + a blind Opus judge.

**Tech Stack:** Python 3.12, `claude-agent-sdk` (DM sessions), `anthropic` (player + judge), `pyyaml` (scenario), sqlite3 stdlib (metrics), pytest.

**Spec:** `docs/superpowers/specs/2026-07-11-model-eval-harness-design.md` — read it first; its "Settled decisions" section is binding.

## Global Constraints

- Default matrix: `haiku`, `sonnet`, `opus`, `fable` family aliases at `medium` effort — never pinned version ids; record resolved ids for provenance.
- Run/launch order is ALWAYS ascending model ability: haiku → sonnet → opus → fable.
- DM tool surface during evals: dm-engine MCP tools only (no Bash/file tools).
- Player agent fixed: `claude-haiku-4-5`, no thinking. Judge fixed: `claude-opus-4-8`, adaptive thinking, effort high.
- `evals/runs/` is gitignored. Scratch campaigns under `campaigns/` are deleted after their bundle is copied (live-data rule).
- No LLM calls in CI tests. `--smoke` is the manual end-to-end check.
- Line length 100 (ruff). Conventional commits, first line < 50 chars.
- Engine interfaces consumed (do not modify dm_engine): `bootstrap_campaign(campaigns_dir, rules_db_path, *, slug, name, skeleton, seed)` → `CommandContext`; `registry.execute(name, ctx, **kw)`; `open_campaign_context(campaigns_dir, slug, rules_db_path)`; `ensure_rules_db()` from `dm_engine.content.seed`.

---

### Task 1: Package scaffolding, deps, cell model + CLI skeleton

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Create: `evals/__init__.py`, `evals/cells.py`, `evals/run.py`
- Test: `tests/evals/test_cells.py` (NO `__init__.py` in tests/evals — `tests/` is not a
  package, so an `__init__.py` there would import as top-level `evals` and shadow the real
  package)

**Interfaces:**
- Produces: `Cell` dataclass (`model: str`, `effort: str`, `slug` property), `parse_cells(spec: str | None) -> list[Cell]` (sorted ascending ability), `ABILITY_ORDER`, `dm-eval` console script with `--cells --reps --parallel --serial --smoke --judge-only` flags.

- [ ] **Step 1: pyproject + gitignore**

In `pyproject.toml`:
- Add to `[project.scripts]`: `dm-eval = "evals.run:main"`
- Add dependency group and make uv install it by default:

```toml
[dependency-groups]
dev = [ ...unchanged... ]
eval = [
    "anthropic>=0.116",
    "claude-agent-sdk>=0.1.0",
    "pyyaml>=6",
]

[tool.uv]
default-groups = ["dev", "eval"]
```

- Change `[tool.hatch.build.targets.wheel]` to `packages = ["src/dm_engine", "evals"]`
- Add `"evals"` to `[tool.ruff] src`.

Append to `.gitignore`: `evals/runs/`

Run: `uv sync` — expect it to resolve and install anthropic, claude-agent-sdk, pyyaml.

- [ ] **Step 2: Write the failing test**

`tests/evals/test_cells.py`:

```python
import pytest

from evals.cells import ABILITY_ORDER, Cell, parse_cells


def test_default_matrix_is_all_families_medium_ascending():
    cells = parse_cells(None)
    assert [c.model for c in cells] == ["haiku", "sonnet", "opus", "fable"]
    assert all(c.effort == "medium" for c in cells)


def test_explicit_cells_are_resorted_ascending():
    cells = parse_cells("fable:high,haiku,opus:low")
    assert [(c.model, c.effort) for c in cells] == [
        ("haiku", "medium"), ("opus", "low"), ("fable", "high"),
    ]


def test_slug_is_filesystem_safe():
    assert Cell("opus", "medium").slug == "opus-medium"


def test_unknown_model_rejected():
    with pytest.raises(ValueError, match="unknown model"):
        parse_cells("gpt5:high")


def test_unknown_effort_rejected():
    with pytest.raises(ValueError, match="unknown effort"):
        parse_cells("opus:ultra")


def test_ability_order_covers_default_matrix():
    assert ABILITY_ORDER == ["haiku", "sonnet", "opus", "fable"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/evals/test_cells.py -v` — expect FAIL (ModuleNotFoundError).

- [ ] **Step 4: Implement `evals/cells.py`**

```python
"""Eval matrix cells: (model family alias, thinking effort)."""

from __future__ import annotations

from dataclasses import dataclass

ABILITY_ORDER = ["haiku", "sonnet", "opus", "fable"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
DEFAULT_EFFORT = "medium"


@dataclass(frozen=True)
class Cell:
    model: str  # family alias, resolved to latest by the CLI at run time
    effort: str

    @property
    def slug(self) -> str:
        return f"{self.model}-{self.effort}"


def parse_cells(spec: str | None) -> list[Cell]:
    """Parse 'model[:effort],...' into cells, always sorted ascending ability."""
    if not spec:
        cells = [Cell(m, DEFAULT_EFFORT) for m in ABILITY_ORDER]
    else:
        cells = []
        for part in spec.split(","):
            model, _, effort = part.strip().partition(":")
            effort = effort or DEFAULT_EFFORT
            if model not in ABILITY_ORDER:
                raise ValueError(f"unknown model {model!r}; choose from {ABILITY_ORDER}")
            if effort not in EFFORTS:
                raise ValueError(f"unknown effort {effort!r}; choose from {EFFORTS}")
            cells.append(Cell(model, effort))
    return sorted(cells, key=lambda c: ABILITY_ORDER.index(c.model))
```

`evals/run.py` skeleton (argument parsing only for now; orchestration lands in Task 9):

```python
"""dm-eval CLI: run the model evaluation matrix."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dm-eval", description="Timed, graded DM model evals")
    p.add_argument("--cells", help="model[:effort],... (default: full family matrix at medium)")
    p.add_argument("--reps", type=int, default=1, help="runs per cell, fresh seed per rep")
    p.add_argument("--parallel", type=int, default=3, help="max concurrent cells")
    p.add_argument("--serial", action="store_true", help="run cells one at a time")
    p.add_argument("--smoke", action="store_true", help="one haiku cell, first 2 beats only")
    p.add_argument("--judge-only", metavar="RUN_DIR", help="re-grade existing bundles")
    return p


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(f"not implemented yet: {args}")


if __name__ == "__main__":
    main()
```

Create empty `evals/__init__.py`. Do NOT create `tests/evals/__init__.py` (see Files note).

- [ ] **Step 5: Run tests, lint, commit**

Run: `uv run pytest tests/evals/ -v` — expect PASS.
Run: `uv run ruff check src tests evals` — expect clean.
Run: `uv run dm-eval --help` — expect usage text.

```bash
git add pyproject.toml .gitignore evals tests/evals uv.lock
git commit -m "feat: eval harness scaffolding and cell matrix"
```

---

### Task 2: Scenario spec + campaign builder

**Files:**
- Create: `evals/scenarios/caravan_ambush.yaml`, `evals/scenario.py`
- Test: `tests/evals/test_scenario.py`

**Interfaces:**
- Consumes: `bootstrap_campaign`, `registry.execute`, `Cell` (Task 1).
- Produces: `Scenario` dataclass (`name`, `premise`, `player_persona: str`, `pc_name: str`, `beats: list[Beat]`), `Beat` dataclass (`id`, `goal`, `notes: str | None`, `max_player_messages: int`, `done_when: dict`), `load_scenario(path: Path) -> Scenario`, `build_campaign(scenario, campaigns_dir, rules_db_path, slug, seed) -> None`.

- [ ] **Step 1: Write the scenario YAML**

`evals/scenarios/caravan_ambush.yaml`:

```yaml
name: The Missing Caravan
premise: >
  A trade caravan bound for Dunmere vanished on the Old Fen Road three days
  ago. The merchants' guild in the village of Bracken Hollow is offering 50
  gold for answers. Bandits led by a brute called Varrik the Red are
  suspected.
player_persona: >
  You are playing Kira, a pragmatic human fighter, blunt and brave, traveling
  with Brother Aldric, a dwarf cleric companion. You speak in first person,
  in-character, 1-3 sentences per message. You roll your own physical dice
  when the DM asks: report the raw number the notes for the current goal give
  you; if the notes give no number, say "/roll" to let the DM roll for you.
  Never narrate outcomes yourself; state intent and let the DM resolve it.
pc_name: Kira
party:
  - name: Kira
    role: pc
    class_slug: fighter
    race_slug: human
    abilities: {str: 16, dex: 14, con: 14, int: 10, wis: 12, cha: 8}
    ac: 16
    proficiencies: {skills: [athletics, intimidation]}
    attacks: [{weapon: longsword, name: longsword}]
  - name: Brother Aldric
    role: companion
    class_slug: cleric
    race_slug: hill-dwarf
    level: 3
    abilities: {str: 14, dex: 8, con: 15, int: 10, wis: 15, cha: 12}
    ac: 18
    proficiencies: {skills: [medicine, religion]}
    attacks: [{weapon: mace, name: mace}]
starting_region:
  locations:
    - slug: bracken-hollow
      name: Bracken Hollow
      description: A fenland trade village of stilt-houses and peat smoke.
      region: The Fenmarch
    - slug: old-fen-road
      name: The Old Fen Road
      description: A raised causeway through the marsh, wide enough for carts.
      region: The Fenmarch
  npcs:
    - name: Marla Underbough
      disposition: friendly
      location_slug: bracken-hollow
      notes: {role: innkeeper of the Heron's Rest, knows caravan gossip}
    - name: Guildmaster Fenn
      disposition: neutral
      location_slug: bracken-hollow
      notes: {role: merchants' guild master, posted the 50gp reward}
quest:
  name: The Missing Caravan
  description: Find out what happened to the Dunmere caravan on the Old Fen Road.
scene:
  location_slug: bracken-hollow
  description: >
    Evening in the common room of the Heron's Rest. Marla Underbough tends
    the bar; the guild reward notice is nailed by the door.
beats:
  - id: question-innkeeper
    goal: Ask Marla the innkeeper what she knows about the missing caravan.
      Press her for details about the Old Fen Road and Varrik the Red.
    notes: If the DM asks for a d20 roll, report 17.
    max_player_messages: 4
    done_when: {command: skill_check, ok: true}
  - id: buy-supplies
    goal: Buy a healing potion or basic supplies for the road before leaving.
    max_player_messages: 3
    done_when: {command: add_item, ok: true}
  - id: travel-to-ambush
    goal: Set out along the Old Fen Road toward where the caravan vanished.
    max_player_messages: 3
    done_when: {command: travel, ok: true}
  - id: investigate-wreckage
    goal: Search the wrecked caravan carefully for clues about the attackers
      and any survivors.
    notes: If the DM asks for a d20 roll, report 14.
    max_player_messages: 4
    done_when: {command: skill_check, ok: true}
  - id: fight-bandits
    goal: Bandits attack! Fight them. Engage the nearest bandit in melee with
      your longsword.
    notes: If asked for initiative report 12. If asked for an attack roll
      report 18, damage 7. On later attacks say "/roll".
    max_player_messages: 8
    done_when: {command: attack, ok: true}
  - id: tier1-heal
    goal: Brother Aldric is hurt. Ask him to cast Healing Word on himself
      (or on you if you are hurt instead).
    max_player_messages: 4
    done_when: {command: cast_spell, ok: true}
  - id: tier2-spell
    goal: Ask Brother Aldric to cast Bless on the party to turn the tide.
    max_player_messages: 4
    done_when: {command: cast_spell, ok: true}
  - id: illegal-action
    goal: A bandit archer is far away across the marsh. Try to hit him with
      your longsword from where you stand, without moving.
    max_player_messages: 3
    done_when: {command: attack, ok: false}
  - id: wrap-up
    goal: Finish the fight, loot the bodies, and then tell the DM you want to
      end the session here.
    notes: Say "/roll" for any remaining dice. After looting, say clearly
      "let's end the session here".
    max_player_messages: 10
    done_when: {command: end_session, ok: true}
```

- [ ] **Step 2: Write the failing test**

`tests/evals/test_scenario.py`:

```python
import sqlite3
from pathlib import Path

from evals.scenario import build_campaign, load_scenario

SCENARIO = Path(__file__).parents[2] / "evals" / "scenarios" / "caravan_ambush.yaml"


def test_load_scenario_parses_beats_in_order():
    sc = load_scenario(SCENARIO)
    assert sc.pc_name == "Kira"
    assert [b.id for b in sc.beats][:2] == ["question-innkeeper", "buy-supplies"]
    assert sc.beats[7].done_when == {"command": "attack", "ok": False}
    assert all(b.max_player_messages > 0 for b in sc.beats)


def test_build_campaign_creates_identical_starting_state(tmp_path, rules_path):
    sc = load_scenario(SCENARIO)
    build_campaign(sc, tmp_path, rules_path, slug="eval-t", seed=1234)
    db = sqlite3.connect(tmp_path / "eval-t" / "campaign.sqlite")
    npcs = {r[0] for r in db.execute("SELECT name FROM npcs")}
    assert {"Marla Underbough", "Guildmaster Fenn"} <= npcs
    chars = {r[0] for r in db.execute("SELECT name FROM characters")}
    assert {"Kira", "Brother Aldric"} <= chars
    quests = db.execute("SELECT COUNT(*) FROM quests").fetchone()[0]
    assert quests >= 1
```

(`rules_path` is the session-scoped fixture from `tests/conftest.py`; pytest resolves it because `tests/evals/` shares the root conftest.) If a table name differs, check `docs/SCHEMA.md` and fix the TEST, not the schema.

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/evals/test_scenario.py -v` — expect FAIL (no `evals.scenario`).

- [ ] **Step 4: Implement `evals/scenario.py`**

```python
"""Load the scenario spec and build an identical starting campaign state."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from dm_engine.commands import registry
from dm_engine.commands.campaign import bootstrap_campaign


@dataclass(frozen=True)
class Beat:
    id: str
    goal: str
    done_when: dict
    notes: str | None = None
    max_player_messages: int = 4


@dataclass(frozen=True)
class Scenario:
    name: str
    premise: str
    player_persona: str
    pc_name: str
    party: list[dict]
    starting_region: dict
    quest: dict
    scene: dict
    beats: list[Beat] = field(default_factory=list)


def load_scenario(path: Path) -> Scenario:
    raw = yaml.safe_load(path.read_text())
    beats = [Beat(**b) for b in raw.pop("beats")]
    return Scenario(beats=beats, **raw)


def build_campaign(
    scenario: Scenario, campaigns_dir: Path, rules_db_path: Path, *, slug: str, seed: int
) -> None:
    """Create the seeded scratch campaign with the full starting state.

    Everything the DM under test should find on open_campaign is created here,
    through the engine's own bootstrap + registry commands (audited, legal).
    """
    ctx = bootstrap_campaign(
        campaigns_dir,
        rules_db_path,
        slug=slug,
        name=scenario.name,
        skeleton={"premise": scenario.premise},
        starting_region=scenario.starting_region,
        seed=seed,
    )
    try:
        for member in scenario.party:
            registry.execute("create_character", ctx, **member)
        registry.execute("update_quest", ctx, **scenario.quest, status="active")
        registry.execute("set_scene", ctx, **scenario.scene)
    finally:
        ctx.store.close()
```

If `update_quest`/`set_scene` reject a kwarg, check the handler signature in
`src/dm_engine/commands/` (`rg '@command\("update_quest"\)' -A5 src`) and adapt the kwargs
here — never the handler.

- [ ] **Step 5: Run tests, lint, commit**

Run: `uv run pytest tests/evals/ -v && uv run ruff check evals tests` — expect PASS/clean.

```bash
git add evals tests/evals
git commit -m "feat: eval scenario spec and campaign builder"
```

---

### Task 3: Beat predicates + mechanical metrics

**Files:**
- Create: `evals/metrics.py`
- Test: `tests/evals/test_metrics.py`

**Interfaces:**
- Consumes: `Beat.done_when` dicts (Task 2).
- Produces: `beat_done(db_path: Path, done_when: dict, after_id: int) -> bool`; `max_event_id(db_path: Path) -> int`; `compute_metrics(db_path: Path, transcript_path: Path, pc_name: str = "Kira") -> dict` returning keys `refusals`, `refusal_retry_loops`, `crashes`, `orphaned_tier2`, `player_supplied_violations`, `schema_rejections`, `polling_reads`, `tool_calls`, `player_messages`, `tool_calls_per_player_message`.

- [ ] **Step 1: Write the failing test with a planted-defect fixture**

`tests/evals/test_metrics.py`:

```python
import json
import sqlite3
from pathlib import Path

import pytest

from evals.metrics import beat_done, compute_metrics, max_event_id


@pytest.fixture()
def fixture_db(tmp_path) -> Path:
    """Hand-crafted event_log with one planted defect per metric."""
    db_path = tmp_path / "campaign.sqlite"
    db = sqlite3.connect(db_path)
    db.execute(
        "CREATE TABLE event_log (id INTEGER PRIMARY KEY, command TEXT,"
        " inputs TEXT, result TEXT, created_at TEXT DEFAULT '')"
    )
    rows = [
        ("open_campaign", "{}", '{"ok": true}'),
        ("skill_check", '{"actor": "Kira"}', '{"ok": true}'),
        # refusal retry loop: same command, identical inputs, consecutive
        ("attack", '{"actor": "Kira", "target": "archer"}',
         '{"ok": false, "refusal": "target is distant"}'),
        ("attack", '{"actor": "Kira", "target": "archer"}',
         '{"ok": false, "refusal": "target is distant"}'),
        # crash
        ("cast_spell", "{}", '{"ok": false, "digest": "ENGINE CRASH: KeyError"}'),
        # orphaned tier-2: needs_ruling with no later dm_ruling
        ("cast_spell", '{"caster": "Brother Aldric"}',
         '{"ok": true, "needs_ruling": true}'),
        # player_supplied roll on a non-PC actor (must never happen)
        ("attack", '{"actor": "Brother Aldric", "player_attack_value": 15}',
         '{"ok": true, "rolls": [{"player_supplied": true}]}'),
        # polling reads
        ("get_scene_state", "{}", '{"ok": true}'),
        ("get_scene_state", "{}", '{"ok": true}'),
    ]
    db.executemany(
        "INSERT INTO event_log (command, inputs, result) VALUES (?, ?, ?)", rows
    )
    db.commit()
    db.close()
    return db_path


@pytest.fixture()
def fixture_transcript(tmp_path) -> Path:
    path = tmp_path / "transcript.jsonl"
    lines = [
        {"type": "player_message", "text": "I attack"},
        {"type": "tool_call", "name": "mcp__dm-engine__attack", "is_error": False},
        {"type": "tool_call", "name": "mcp__dm-engine__attack", "is_error": True},
        {"type": "dm_text", "text": "You swing..."},
        {"type": "player_message", "text": "again"},
        {"type": "tool_call", "name": "mcp__dm-engine__attack", "is_error": False},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines))
    return path


def test_beat_done_respects_after_id_and_ok(fixture_db):
    assert beat_done(fixture_db, {"command": "skill_check", "ok": True}, after_id=0)
    assert not beat_done(fixture_db, {"command": "skill_check", "ok": True}, after_id=2)
    assert beat_done(fixture_db, {"command": "attack", "ok": False}, after_id=0)
    assert not beat_done(fixture_db, {"command": "end_session", "ok": True}, after_id=0)


def test_max_event_id(fixture_db):
    assert max_event_id(fixture_db) == 9


def test_metrics_catch_each_planted_defect(fixture_db, fixture_transcript):
    m = compute_metrics(fixture_db, fixture_transcript)
    assert m["refusals"] == 3          # 2 attack refusals + 1 crash row (ok=false)
    assert m["refusal_retry_loops"] == 1
    assert m["crashes"] == 1
    assert m["orphaned_tier2"] == 1
    assert m["player_supplied_violations"] == 1
    assert m["schema_rejections"] == 1
    assert m["polling_reads"] == 2
    assert m["player_messages"] == 2
    assert m["tool_calls"] == 3
    assert m["tool_calls_per_player_message"] == 1.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/test_metrics.py -v` — expect FAIL.

- [ ] **Step 3: Implement `evals/metrics.py`**

```python
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


def compute_metrics(db_path: Path, transcript_path: Path, pc_name: str = "Kira") -> dict:
    with _connect(db_path) as db:
        events = db.execute(
            "SELECT id, command, inputs, result FROM event_log ORDER BY id"
        ).fetchall()

    refusals = sum(
        1 for _, _, _, res in events if json.loads(res).get("ok") is False
    )
    crashes = sum(
        1 for _, _, _, res in events
        if str(json.loads(res).get("digest", "")).startswith("ENGINE CRASH")
    )
    retry_loops = 0
    for prev, cur in zip(events, events[1:]):
        same = prev[1] == cur[1] and prev[2] == cur[2]
        if same and not json.loads(prev[3]).get("ok") and not json.loads(cur[3]).get("ok"):
            retry_loops += 1
    orphaned = 0
    for eid, cmd, _, res in events:
        if cmd == "cast_spell" and json.loads(res).get("needs_ruling"):
            followed = any(e[1] == "dm_ruling" and e[0] > eid for e in events)
            if not followed:
                orphaned += 1
    polling = sum(1 for _, cmd, _, _ in events if cmd in POLLING_COMMANDS)
    supplied_violations = 0
    for _, _, inputs, res in events:
        actor = json.loads(inputs).get("actor") or json.loads(inputs).get("caster")
        if actor and actor != pc_name and '"player_supplied": true' in res:
            supplied_violations += 1

    player_messages = tool_calls = schema_rejections = 0
    for line in transcript_path.read_text().splitlines():
        entry = json.loads(line)
        if entry["type"] == "player_message":
            player_messages += 1
        elif entry["type"] == "tool_call":
            if entry.get("is_error"):
                schema_rejections += 1
            else:
                tool_calls += 1

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

- [ ] **Step 4: Run tests, lint, commit**

Run: `uv run pytest tests/evals/test_metrics.py -v && uv run ruff check evals tests` — PASS/clean.

```bash
git add evals/metrics.py tests/evals/test_metrics.py
git commit -m "feat: beat predicates and mechanical metrics"
```

---

### Task 4: Player agent

**Files:**
- Create: `evals/player.py`
- Test: `tests/evals/test_player.py`

**Interfaces:**
- Consumes: `Scenario`, `Beat` (Task 2).
- Produces: `build_player_prompt(scenario, beat, narration: list[str]) -> tuple[str, str]` (system, user — pure, unit-tested); `next_player_message(client, scenario, beat, narration) -> str` (thin API call, not unit-tested).

- [ ] **Step 1: Write the failing test**

`tests/evals/test_player.py`:

```python
from evals.player import build_player_prompt
from evals.scenario import Beat, Scenario


def _scenario() -> Scenario:
    return Scenario(
        name="T", premise="p", player_persona="You are Kira. Be blunt.",
        pc_name="Kira", party=[], starting_region={}, quest={}, scene={}, beats=[],
    )


def _beat() -> Beat:
    return Beat(id="b1", goal="Ask the innkeeper about the caravan.",
                done_when={"command": "skill_check"}, notes="Report 17 on a d20.")


def test_prompt_contains_persona_goal_and_notes():
    system, user = build_player_prompt(_scenario(), _beat(), ["The inn is warm."])
    assert "You are Kira" in system
    assert "Ask the innkeeper" in user
    assert "Report 17" in user
    assert "The inn is warm." in user


def test_narration_is_truncated_to_recent_tail():
    narration = [f"chunk {i} " + "x" * 500 for i in range(50)]
    _, user = build_player_prompt(_scenario(), _beat(), narration)
    assert len(user) < 9000
    assert "chunk 49" in user      # newest kept
    assert "chunk 0 " not in user  # oldest dropped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/test_player.py -v` — expect FAIL.

- [ ] **Step 3: Implement `evals/player.py`**

```python
"""Beat-scripted player agent: one fixed cheap model, constant across cells."""

from __future__ import annotations

import anthropic

from evals.scenario import Beat, Scenario

PLAYER_MODEL = "claude-haiku-4-5"
MAX_NARRATION_CHARS = 6000

_SYSTEM_TEMPLATE = """{persona}

You are the PLAYER in a dungeons-and-dragons game; the other party is the DM.
Reply with your character's next message only: 1-3 sentences, first person,
no narration of outcomes, no out-of-character commentary. Always push toward
your current goal. If the DM asks a question, answer it and keep pushing."""

_USER_TEMPLATE = """Recent DM narration (newest last):
{narration}

Your current goal: {goal}
{notes}
Write your next message to the DM."""


def build_player_prompt(
    scenario: Scenario, beat: Beat, narration: list[str]
) -> tuple[str, str]:
    tail: list[str] = []
    total = 0
    for chunk in reversed(narration):
        total += len(chunk)
        if total > MAX_NARRATION_CHARS:
            break
        tail.append(chunk)
    text = "\n\n".join(reversed(tail)) or "(the session is just beginning)"
    notes = f"Dice notes: {beat.notes}\n" if beat.notes else ""
    system = _SYSTEM_TEMPLATE.format(persona=scenario.player_persona.strip())
    user = _USER_TEMPLATE.format(narration=text, goal=beat.goal.strip(), notes=notes)
    return system, user


def next_player_message(
    client: anthropic.Anthropic, scenario: Scenario, beat: Beat, narration: list[str]
) -> str:
    system, user = build_player_prompt(scenario, beat, narration)
    response = client.messages.create(
        model=PLAYER_MODEL,
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return next(b.text for b in response.content if b.type == "text").strip()
```

- [ ] **Step 4: Run tests, lint, commit**

Run: `uv run pytest tests/evals/test_player.py -v && uv run ruff check evals tests` — PASS/clean.

```bash
git add evals/player.py tests/evals/test_player.py
git commit -m "feat: beat-scripted player agent"
```

---

### Task 5: DM runner

**Files:**
- Create: `evals/runner.py`
- Test: `tests/evals/test_runner.py`

**Interfaces:**
- Consumes: `Cell`, `Scenario`, `beat_done`, `max_event_id`, `next_player_message`.
- Produces: `RunResult` dataclass (`cell`, `resolved_model: str | None`, `beats_completed: list[str]`, `beats_failed: list[str]`, `complete: bool`, `error: str | None`, `wall_clock_s: float`, `turns: list[dict]`); `run_cell(cell, scenario, *, repo_root, campaigns_dir, rules_db_path, bundle_dir, seed, beats_limit=None, turn_timeout_s=600, run_timeout_s=5400) -> RunResult` (async); `TranscriptWriter` (unit-tested).

- [ ] **Step 1: Write the failing test (pure parts only)**

`tests/evals/test_runner.py`:

```python
import json

from evals.runner import TranscriptWriter, dm_options


def test_transcript_writer_appends_jsonl(tmp_path):
    path = tmp_path / "transcript.jsonl"
    w = TranscriptWriter(path)
    w.write({"type": "player_message", "text": "hi"})
    w.write({"type": "dm_text", "text": "hello"})
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert [x["type"] for x in lines] == ["player_message", "dm_text"]


def test_dm_options_pins_tool_surface_and_cell(tmp_path):
    skill = "IRON RULES..."
    opts = dm_options(model="opus", effort="medium", repo_root=tmp_path, skill_text=skill)
    assert opts.model == "opus"
    assert opts.effort == "medium"
    assert opts.tools == []                      # no built-in tools
    assert opts.permission_mode == "bypassPermissions"
    assert opts.setting_sources == []            # deterministic context
    assert "dm-engine" in opts.mcp_servers
    assert opts.system_prompt["append"].endswith(skill)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/test_runner.py -v` — expect FAIL.

- [ ] **Step 3: Implement `evals/runner.py`**

```python
"""One eval cell: a DM session driven turn-by-turn by the player agent."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from evals.cells import Cell
from evals.metrics import beat_done, max_event_id
from evals.player import next_player_message
from evals.scenario import Scenario, build_campaign

SKILL_PATH = Path(".claude/skills/dm-session/SKILL.md")


class TranscriptWriter:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    def write(self, entry: dict) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(entry) + "\n")


def dm_options(
    *, model: str, effort: str, repo_root: Path, skill_text: str
) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=str(repo_root),
        model=model,
        effort=effort,
        permission_mode="bypassPermissions",
        setting_sources=[],  # no CLAUDE.md / project settings: deterministic context
        tools=[],  # no built-in tools; MCP tools remain available
        mcp_servers={"dm-engine": {"command": "uv", "args": ["run", "dm", "mcp"]}},
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": "You are running a solo D&D session. Follow this skill exactly:\n\n"
            + skill_text,
        },
    )


@dataclass
class RunResult:
    cell: Cell
    resolved_model: str | None = None
    beats_completed: list[str] = field(default_factory=list)
    beats_failed: list[str] = field(default_factory=list)
    complete: bool = False
    error: str | None = None
    wall_clock_s: float = 0.0
    turns: list[dict] = field(default_factory=list)


async def _dm_turn(
    client: ClaudeSDKClient, message: str, transcript: TranscriptWriter, result: RunResult
) -> list[str]:
    """Send one player message, stream the DM's full response, return narration."""
    transcript.write({"type": "player_message", "text": message})
    start = time.monotonic()
    narration: list[str] = []
    await client.query(message)
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            if result.resolved_model is None and getattr(msg, "model", None):
                result.resolved_model = msg.model
            for block in msg.content:
                if isinstance(block, TextBlock):
                    narration.append(block.text)
                    transcript.write({"type": "dm_text", "text": block.text})
                elif isinstance(block, ToolUseBlock):
                    transcript.write(
                        {"type": "tool_call", "name": block.name,
                         "input": block.input, "is_error": False}
                    )
        elif isinstance(msg, UserMessage):
            for block in msg.content if isinstance(msg.content, list) else []:
                if isinstance(block, ToolResultBlock) and block.is_error:
                    transcript.write(
                        {"type": "tool_call", "name": "(result)", "is_error": True,
                         "content": str(block.content)[:500]}
                    )
        elif isinstance(msg, ResultMessage):
            result.turns.append(
                {"wall_s": round(time.monotonic() - start, 2),
                 "duration_api_ms": msg.duration_api_ms, "usage": msg.usage}
            )
    return narration


async def run_cell(
    cell: Cell,
    scenario: Scenario,
    *,
    repo_root: Path,
    campaigns_dir: Path,
    rules_db_path: Path,
    bundle_dir: Path,
    seed: int,
    beats_limit: int | None = None,
    turn_timeout_s: float = 600,
    run_timeout_s: float = 5400,
) -> RunResult:
    slug = f"eval-{cell.slug}-{seed}"
    result = RunResult(cell=cell)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    transcript = TranscriptWriter(bundle_dir / "transcript.jsonl")
    build_campaign(scenario, campaigns_dir, rules_db_path, slug=slug, seed=seed)
    db_path = campaigns_dir / slug / "campaign.sqlite"
    player = anthropic.Anthropic()
    skill_text = (repo_root / SKILL_PATH).read_text()
    beats = scenario.beats[:beats_limit] if beats_limit else scenario.beats
    started = time.monotonic()

    try:
        options = dm_options(
            model=cell.model, effort=cell.effort, repo_root=repo_root, skill_text=skill_text
        )
        async with ClaudeSDKClient(options=options) as client:
            narration = await asyncio.wait_for(
                _dm_turn(
                    client,
                    f"Open the campaign '{slug}' and start the session. Set the scene "
                    "for me, then wait for my action.",
                    transcript,
                    result,
                ),
                timeout=turn_timeout_s,
            )
            for beat in beats:
                if time.monotonic() - started > run_timeout_s:
                    raise TimeoutError("run timeout")
                marker = max_event_id(db_path)
                done = False
                for _ in range(beat.max_player_messages):
                    msg = await asyncio.to_thread(
                        next_player_message, player, scenario, beat, narration
                    )
                    narration += await asyncio.wait_for(
                        _dm_turn(client, msg, transcript, result), timeout=turn_timeout_s
                    )
                    if beat_done(db_path, beat.done_when, after_id=marker):
                        done = True
                        break
                (result.beats_completed if done else result.beats_failed).append(beat.id)
        result.complete = not result.beats_failed
    except (TimeoutError, asyncio.TimeoutError):
        result.error = "timeout"
    except Exception as exc:  # cell-level failure must not sink other cells
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.wall_clock_s = round(time.monotonic() - started, 1)
        (bundle_dir / "timing.json").write_text(
            json.dumps(
                {"cell": cell.slug, "resolved_model": result.resolved_model,
                 "wall_clock_s": result.wall_clock_s, "turns": result.turns,
                 "beats_completed": result.beats_completed,
                 "beats_failed": result.beats_failed, "error": result.error},
                indent=2,
            )
        )
        if db_path.exists():
            shutil.copy2(db_path, bundle_dir / "campaign.sqlite")
            shutil.rmtree(campaigns_dir / slug)  # live-data rule: no scratch slugs linger
    return result
```

Import names (`TextBlock`, `ToolUseBlock`, `ToolResultBlock`, `UserMessage`…) must be
verified against the installed SDK: run
`uv run python -c "import claude_agent_sdk as s; print([n for n in dir(s) if 'Block' in n or 'Message' in n])"`
and adjust imports to what exists — do not guess.

- [ ] **Step 4: Run tests, lint, commit**

Run: `uv run pytest tests/evals/test_runner.py -v && uv run ruff check evals tests` — PASS/clean.

```bash
git add evals/runner.py tests/evals/test_runner.py
git commit -m "feat: DM session runner via claude-agent-sdk"
```

---

### Task 6: Blind judge

**Files:**
- Create: `evals/judge.py`
- Test: `tests/evals/test_judge.py`

**Interfaces:**
- Consumes: bundle dir contents (transcript.jsonl, timing.json).
- Produces: `anonymize(text: str, model_ids: list[str]) -> str`; `JudgeScores` pydantic model (4 dimensions, each `score` 1–5 + `justification`, plus `overall_comments`); `judge_transcript(client, transcript_text, scenario_yaml, skill_text) -> JudgeScores | None` (retries once, returns None on double failure).

- [ ] **Step 1: Write the failing test**

`tests/evals/test_judge.py`:

```python
import pytest
from pydantic import ValidationError

from evals.judge import JudgeScores, anonymize


def test_anonymize_strips_model_ids_and_aliases():
    text = 'model "claude-opus-4-8" (alias opus) rolled via claude-haiku-4-5'
    out = anonymize(text, ["claude-opus-4-8", "claude-haiku-4-5"])
    assert "claude-opus-4-8" not in out and "claude-haiku-4-5" not in out
    assert "opus" not in out and "haiku" not in out
    assert "[MODEL]" in out


def test_judge_scores_bounds_enforced():
    dim = {"score": 5, "justification": "ok"}
    JudgeScores(narrative_quality=dim, mechanical_fidelity=dim,
                ruling_quality=dim, player_experience=dim, overall_comments="x")
    with pytest.raises(ValidationError):
        JudgeScores(narrative_quality={"score": 6, "justification": "no"},
                    mechanical_fidelity=dim, ruling_quality=dim,
                    player_experience=dim, overall_comments="x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/test_judge.py -v` — expect FAIL.

- [ ] **Step 3: Implement `evals/judge.py`**

```python
"""Layer-2 grading: a fixed, blind judge scores each transcript on the rubric."""

from __future__ import annotations

import re

import anthropic
from pydantic import BaseModel, Field

from evals.cells import ABILITY_ORDER

JUDGE_MODEL = "claude-opus-4-8"


class DimensionScore(BaseModel):
    score: int = Field(ge=1, le=5)
    justification: str


class JudgeScores(BaseModel):
    narrative_quality: DimensionScore
    mechanical_fidelity: DimensionScore
    ruling_quality: DimensionScore
    player_experience: DimensionScore
    overall_comments: str


def anonymize(text: str, model_ids: list[str]) -> str:
    for mid in model_ids:
        if mid:
            text = text.replace(mid, "[MODEL]")
    for alias in ABILITY_ORDER:
        text = re.sub(rf"\b{alias}\b", "[MODEL]", text, flags=re.IGNORECASE)
    return text


_RUBRIC = """You are grading an AI Dungeon Master's play session transcript. The DM was
required to follow the skill document below exactly. Score each dimension 1-5
(5 = excellent) and cite specific transcript moments in each justification.

1. narrative_quality: prose, pacing, NPC voice, continuity with established facts.
2. mechanical_fidelity: never states a mechanical number without a preceding tool
   result; treats refusals as steering (narrates around them, never invents
   outcomes); keeps gm_only material hidden.
3. ruling_quality: dm_ruling rationales are sensible; Tier-2 spells get a
   follow-up ruling; improvised NPCs/locations/quests are persisted via commands.
4. player_experience: responsive to player intent; prompts the player for their
   own dice per the etiquette; keeps stakes and options clear.

You do not know which model produced this transcript. Grade only what is here."""


def judge_transcript(
    client: anthropic.Anthropic, transcript_text: str, scenario_yaml: str, skill_text: str
) -> JudgeScores | None:
    user = (
        f"## The skill the DM must follow\n{skill_text}\n\n"
        f"## The scenario being played\n{scenario_yaml}\n\n"
        f"## Transcript\n{transcript_text}"
    )
    for _ in range(2):  # one retry on malformed output
        try:
            response = client.messages.parse(
                model=JUDGE_MODEL,
                max_tokens=4000,
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                system=_RUBRIC,
                messages=[{"role": "user", "content": user}],
                output_format=JudgeScores,
            )
            return response.parsed_output
        except Exception:
            continue
    return None
```

- [ ] **Step 4: Run tests, lint, commit**

Run: `uv run pytest tests/evals/test_judge.py -v && uv run ruff check evals tests` — PASS/clean.

```bash
git add evals/judge.py tests/evals/test_judge.py
git commit -m "feat: blind judge with structured rubric scores"
```

---

### Task 7: Report

**Files:**
- Create: `evals/report.py`
- Test: `tests/evals/test_report.py`

**Interfaces:**
- Consumes: per-cell dicts `{"cell", "resolved_model", "wall_clock_s", "median_turn_s", "output_tokens", "beats_completed", "beats_failed", "error", "metrics": {...}, "judge": JudgeScores | None}`.
- Produces: `render_report(results: list[dict], judge_model: str) -> str` (markdown).

- [ ] **Step 1: Write the failing test**

`tests/evals/test_report.py`:

```python
from evals.judge import JudgeScores
from evals.report import render_report

DIM = {"score": 4, "justification": "solid"}


def _result(cell="haiku-medium", judge=True):
    return {
        "cell": cell,
        "resolved_model": "claude-haiku-4-5",
        "wall_clock_s": 812.0,
        "median_turn_s": 24.5,
        "output_tokens": 20000,
        "beats_completed": ["a", "b"],
        "beats_failed": [],
        "error": None,
        "metrics": {"refusals": 2, "crashes": 0, "tool_calls_per_player_message": 3.1,
                    "refusal_retry_loops": 0, "orphaned_tier2": 0,
                    "schema_rejections": 0, "polling_reads": 1,
                    "player_messages": 10, "tool_calls": 31},
        "judge": JudgeScores(
            narrative_quality=DIM, mechanical_fidelity=DIM, ruling_quality=DIM,
            player_experience=DIM, overall_comments="fine",
        ) if judge else None,
    }


def test_report_has_table_row_per_cell_and_provenance():
    md = render_report([_result(), _result(cell="opus-medium")], judge_model="claude-opus-4-8")
    assert md.count("haiku-medium") >= 1 and md.count("opus-medium") >= 1
    assert "claude-haiku-4-5" in md          # resolved id recorded
    assert "Judge: claude-opus-4-8" in md
    assert "24.5" in md and "20000" in md    # median turn latency + output tokens


def test_judge_failure_is_flagged_not_hidden():
    md = render_report([_result(judge=False)], judge_model="claude-opus-4-8")
    assert "judge-failed" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/test_report.py -v` — expect FAIL.

- [ ] **Step 3: Implement `evals/report.py`**

```python
"""Assemble the comparison report from per-cell results."""

from __future__ import annotations


def _judge_cells(judge) -> list[str]:
    if judge is None:
        return ["judge-failed"] * 5
    dims = [judge.narrative_quality, judge.mechanical_fidelity,
            judge.ruling_quality, judge.player_experience]
    avg = sum(d.score for d in dims) / 4
    return [str(d.score) for d in dims] + [f"{avg:.2f}"]


def render_report(results: list[dict], *, judge_model: str) -> str:
    lines = [
        "# DM Model Eval Report", "",
        f"Judge: {judge_model} (fixed across all cells; scores comparable within "
        "this report only)", "",
        "| cell | resolved model | wall clock (s) | median turn (s) | out tokens "
        "| beats done | refusals | crashes | retry loops | orphaned T2 | supplied viol "
        "| schema rej | tools/msg | narr | mech | ruling | player | judge avg |",
        "|" + "---|" * 18,
    ]
    for r in results:
        m = r["metrics"]
        beats = f"{len(r['beats_completed'])}/{len(r['beats_completed']) + len(r['beats_failed'])}"
        if r.get("error"):
            beats += f" (INCOMPLETE: {r['error']})"
        row = [
            r["cell"], r.get("resolved_model") or "?", f"{r['wall_clock_s']:.0f}",
            str(r.get("median_turn_s", "?")), str(r.get("output_tokens", "?")), beats,
            str(m["refusals"]), str(m["crashes"]), str(m["refusal_retry_loops"]),
            str(m["orphaned_tier2"]), str(m["player_supplied_violations"]),
            str(m["schema_rejections"]),
            str(m["tool_calls_per_player_message"]), *_judge_cells(r["judge"]),
        ]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    for r in results:
        lines += [f"## {r['cell']}", ""]
        if r["judge"] is not None:
            for name in ("narrative_quality", "mechanical_fidelity",
                         "ruling_quality", "player_experience"):
                dim = getattr(r["judge"], name)
                lines.append(f"- **{name}** ({dim.score}/5): {dim.justification}")
            lines += ["", f"> {r['judge'].overall_comments}", ""]
        else:
            lines += ["- judge-failed: mechanical metrics only", ""]
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests, lint, commit**

Run: `uv run pytest tests/evals/test_report.py -v && uv run ruff check evals tests` — PASS/clean.

```bash
git add evals/report.py tests/evals/test_report.py
git commit -m "feat: eval comparison report"
```

---

### Task 8: Orchestration — wire `dm-eval` end to end

**Files:**
- Modify: `evals/run.py`
- Test: `tests/evals/test_run.py`

**Interfaces:**
- Consumes: everything above.
- Produces: working `main()`: build cells (ascending), rep seeds, semaphore-parallel `run_cell` launched in ascending order, then metrics + judge + report written to `evals/runs/<UTC timestamp>/`; `--judge-only RUN_DIR` re-grades existing bundles; `--smoke` = haiku:medium only, `beats_limit=2`.

- [ ] **Step 1: Write the failing test (pure helpers)**

`tests/evals/test_run.py`:

```python
from evals.cells import Cell
from evals.run import plan_runs


def test_plan_runs_orders_ascending_and_seeds_per_rep():
    runs = plan_runs([Cell("opus", "medium"), Cell("haiku", "medium")], reps=2, base_seed=100)
    assert [(r.cell.model, r.seed) for r in runs] == [
        ("haiku", 100), ("haiku", 101), ("opus", 100), ("opus", 101),
    ]
    assert runs[0].bundle_name == "haiku-medium-r0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evals/test_run.py -v` — expect FAIL.

- [ ] **Step 3: Implement orchestration in `evals/run.py`**

Replace the stub with:

```python
"""dm-eval CLI: run the model evaluation matrix."""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
from dataclasses import dataclass
from pathlib import Path

import anthropic

from dm_engine.content.seed import ensure_rules_db
from evals.cells import Cell, parse_cells
from evals.judge import JUDGE_MODEL, anonymize, judge_transcript
from evals.metrics import compute_metrics
from evals.report import render_report
from evals.runner import run_cell
from evals.scenario import load_scenario

REPO_ROOT = Path(__file__).parents[1]
SCENARIO_PATH = REPO_ROOT / "evals" / "scenarios" / "caravan_ambush.yaml"
RUNS_DIR = REPO_ROOT / "evals" / "runs"
BASE_SEED = 20260711


@dataclass(frozen=True)
class PlannedRun:
    cell: Cell
    seed: int
    rep: int

    @property
    def bundle_name(self) -> str:
        return f"{self.cell.slug}-r{self.rep}"


def plan_runs(cells: list[Cell], *, reps: int, base_seed: int) -> list[PlannedRun]:
    """Ascending-ability launch order is load-bearing: weakest models first."""
    ordered = parse_cells(",".join(f"{c.model}:{c.effort}" for c in cells))
    return [PlannedRun(c, base_seed + r, r) for c in ordered for r in range(reps)]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dm-eval", description="Timed, graded DM model evals")
    p.add_argument("--cells", help="model[:effort],... (default: full family matrix at medium)")
    p.add_argument("--reps", type=int, default=1, help="runs per cell, fresh seed per rep")
    p.add_argument("--parallel", type=int, default=3, help="max concurrent cells")
    p.add_argument("--serial", action="store_true", help="run cells one at a time")
    p.add_argument("--smoke", action="store_true", help="one haiku cell, first 2 beats only")
    p.add_argument("--judge-only", metavar="RUN_DIR", help="re-grade existing bundles")
    return p


async def _run_all(runs, run_dir: Path, parallel: int, beats_limit: int | None):
    scenario = load_scenario(SCENARIO_PATH)
    rules = ensure_rules_db()
    sem = asyncio.Semaphore(parallel)

    async def one(pr: PlannedRun):
        async with sem:
            return pr, await run_cell(
                pr.cell, scenario,
                repo_root=REPO_ROOT, campaigns_dir=REPO_ROOT / "campaigns",
                rules_db_path=rules, bundle_dir=run_dir / pr.bundle_name,
                seed=pr.seed, beats_limit=beats_limit,
            )

    # created in ascending order; the semaphore admits them in creation order
    return await asyncio.gather(*(one(pr) for pr in runs))


def grade_run_dir(run_dir: Path) -> list[dict]:
    scenario_yaml = SCENARIO_PATH.read_text()
    skill_text = (REPO_ROOT / ".claude/skills/dm-session/SKILL.md").read_text()
    client = anthropic.Anthropic()
    results = []
    for bundle in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        timing = json.loads((bundle / "timing.json").read_text())
        metrics = compute_metrics(bundle / "campaign.sqlite", bundle / "transcript.jsonl")
        transcript = anonymize(
            (bundle / "transcript.jsonl").read_text(), [timing.get("resolved_model") or ""]
        )
        judge = judge_transcript(client, transcript, scenario_yaml, skill_text)
        turns = timing.get("turns", [])
        walls = sorted(t["wall_s"] for t in turns) or [0.0]
        out_tokens = sum(
            (t.get("usage") or {}).get("output_tokens", 0) for t in turns
        )
        results.append({
            "cell": timing["cell"], "resolved_model": timing.get("resolved_model"),
            "wall_clock_s": timing["wall_clock_s"],
            "median_turn_s": walls[len(walls) // 2],
            "output_tokens": out_tokens,
            "beats_completed": timing["beats_completed"],
            "beats_failed": timing["beats_failed"], "error": timing.get("error"),
            "metrics": metrics, "judge": judge,
        })
    return results


def main() -> None:
    args = build_parser().parse_args()
    if args.judge_only:
        run_dir = Path(args.judge_only)
    else:
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
        run_dir = RUNS_DIR / stamp
        run_dir.mkdir(parents=True)
        cells = parse_cells("haiku:medium" if args.smoke else args.cells)
        runs = plan_runs(cells, reps=args.reps, base_seed=BASE_SEED)
        parallel = 1 if args.serial else args.parallel
        beats_limit = 2 if args.smoke else None
        outcomes = asyncio.run(_run_all(runs, run_dir, parallel, beats_limit))
        for pr, res in outcomes:
            status = res.error or f"{len(res.beats_completed)} beats"
            print(f"{pr.bundle_name}: {status} in {res.wall_clock_s}s")
    results = grade_run_dir(run_dir)
    report = render_report(results, judge_model=JUDGE_MODEL)
    (run_dir / "report.md").write_text(report)
    print(f"\nreport: {run_dir / 'report.md'}")
```

- [ ] **Step 4: Run tests, lint, commit**

Run: `uv run pytest tests/evals/ -v && uv run ruff check evals tests` — PASS/clean.
Run: `uv run pytest` — full suite must still pass.

```bash
git add evals/run.py tests/evals/test_run.py
git commit -m "feat: dm-eval orchestration and judge-only mode"
```

---

### Task 9: Smoke run — iterate until green

This is the goal's "run a test and iterate on errors til test is complete."

- [ ] **Step 1: Verify SDK option/import names**

Run: `uv run python -c "import claude_agent_sdk as s; print(sorted(n for n in dir(s) if not n.startswith('_')))"`
Fix any import/option-name mismatches in `evals/runner.py` (e.g. if `effort` or `tools`
isn't a `ClaudeAgentOptions` field in the installed version, find the actual field with
`uv run python -c "from claude_agent_sdk import ClaudeAgentOptions; import dataclasses;
print([f.name for f in dataclasses.fields(ClaudeAgentOptions)])"`). If effort is truly
absent, fall back to `extra_args={"effort": cell.effort}` and note it in the report.

- [ ] **Step 2: Run the smoke test**

Run: `uv run dm-eval --smoke`
Expected: one haiku cell plays beats 1-2, bundle written under `evals/runs/<ts>/haiku-medium-r0/`
with non-empty `transcript.jsonl`, `timing.json`, `campaign.sqlite`; judge scores in
`report.md`; scratch campaign removed from `campaigns/`.

- [ ] **Step 3: Iterate on failures**

For each failure: read the traceback / transcript / `~/Library/Caches/claude-cli-nodejs/
<project-dir>/mcp-logs-dm-engine/` (MCP server stderr), fix the harness (never dm_engine),
re-run `--smoke`. Repeat until the smoke run completes end-to-end. Commit each fix:
`fix: <what>`.

- [ ] **Step 4: Full test suite + lint gate**

Run: `uv run pytest && uv run ruff check src tests evals` — all green.

- [ ] **Step 5: Commit final state**

```bash
git add -A && git commit -m "fix: smoke-run hardening for dm-eval"
```

---

### Task 10: Docs

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add an "Model evals" section to README.md** documenting: what `dm-eval`
measures, the default matrix (family aliases at medium, ascending order), the CLI flags
(`--cells --reps --parallel --serial --smoke --judge-only`), that `ANTHROPIC_API_KEY` (or
an `ant auth login` profile) must be available for the player/judge, and that bundles land
in `evals/runs/` (gitignored). Match the README's existing tone and heading style.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document dm-eval harness"
```
