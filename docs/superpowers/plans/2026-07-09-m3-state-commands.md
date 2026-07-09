# M3 — State & Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The campaign SQLite store with append-only event log, the FC-1/FC-3 command registry, and the ~30 v1 commands — everything an LLM DM needs to run a campaign headlessly through `registry.execute`.

**Architecture:** `dm_engine.state` owns persistence: `CampaignStore` (campaign.sqlite per FC-5/FC-6, snapshot-on-open, single-transaction writes) and the markdown sheet renderer. `dm_engine.commands` owns the API: the FC-1 `CommandResult` envelope, the FC-3 registry (`@command` + `execute` = the ONLY mutation path), a `CommandContext` carrying the store + a `RecordingRoller` (wraps M2's `SeededDiceRoller`, captures every `Roll` for the event log, persists draw counts for resume determinism) + the M1 `RulesDB`. Command handlers validate → resolve via M2's pure rules functions → persist via the store → return. Illegal actions are `ok=False` refusals; engine bugs raise and roll the transaction back.

**Tech Stack:** Python ≥3.12, pydantic v2, sqlite3 (stdlib), typer, pytest.

**Plan style note:** Tasks 2–3 (store, registry — the architecture) contain complete code. Command tasks (4–10) contain complete *contracts* — exact signatures, validation order, refusal texts, data payloads, digest examples — plus binding test code; the implementer writes handler bodies to those contracts using strict TDD (binding tests first, plus one mutation+event test per command they implement). Contracts are frozen; handler internals are implementer judgment.

## Global Constraints

- Branch: all M3 work on `feat/m3-state-commands`; never commit to `main`; never push.
- FC-1 (verbatim, frozen — `dm_engine/commands/envelope.py`):
  ```python
  class CommandResult(BaseModel):
      ok: bool                       # False = structured refusal (never an exception for illegal actions)
      command: str                   # registry name, e.g. "attack"
      refusal: str | None = None     # human-readable reason when ok=False
      digest: str                    # one-line narration hook
      data: dict[str, Any]           # command-specific structured payload
      gm_only: bool = False          # True → hide from player
      event_ids: list[int] = []      # event_log rows appended by this command
  ```
- FC-3 (frozen): commands registered by name via `@command("attack")`, signature `fn(ctx: CommandContext, **kwargs) -> CommandResult`; `registry.execute(name, ctx, **kwargs)` is the ONLY mutation path; illegal/invalid actions return `ok=False` refusals; engine bugs raise.
- FC-5 (frozen): campaigns at `campaigns/<slug>/campaign.sqlite`; snapshots to `campaigns/<slug>/snapshots/<ISO-timestamp>.sqlite` at session start (= store open of an existing campaign); `campaigns/` stays gitignored; the `campaign` table records the edition.
- FC-6 (verbatim, frozen — event_log schema):
  ```sql
  CREATE TABLE event_log (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      command TEXT NOT NULL,
      inputs TEXT NOT NULL,        -- JSON kwargs as received
      result TEXT NOT NULL,        -- JSON CommandResult
      rolls TEXT NOT NULL,         -- JSON list[Roll], player_supplied/gm_only flags included
      is_ruling INTEGER NOT NULL DEFAULT 0,
      rationale TEXT               -- REQUIRED (non-null, non-empty) when is_ruling=1
  );
  ```
  Every command execution = exactly one SQLite transaction covering state-table updates + its event row. Refused commands also append an event row (they make no state changes). A raising handler = engine bug: transaction rolls back, nothing is logged, exception propagates.
- FC-2 consumption: one RNG per campaign — `rng_seed` stored at creation, `rng_draws` (count of individual dice drawn) persisted after every command; reopening fast-forwards a fresh `SeededDiceRoller(seed)` by `rng_draws` single-die draws so engine rolls continue deterministically. All PC dice accept `player_value` (raw die total, flagged `player_supplied`); companion/monster dice never do. Hidden DM rolls set `gm_only`.
- FC-7: death modes `narrative`/`hardcore` — death-save mechanics identical; the third failure sets character status `defeated` (narrative) or `dead` (hardcore). XP is engine-awarded; encounter budget is advisory (computed & reported, never refuses).
- Character sheets: engine-rendered markdown, player-visible fields only, materialized to `campaigns/<slug>/sheets/<character-name-slug>.md` after **every** successful command (re-render all party sheets; it is cheap and idempotent).
- M2 rules functions are the ONLY mechanics source — command handlers never reimplement dice/check/damage/condition math. M3 must OR `attack_interaction(...).auto_crit_on_hit` into crit detection (M2 handoff note).
- Player-reported d20 naturals must be validated 1–20 (and damage totals ≥ 0) at the command layer before reaching rules functions; out-of-range → refusal.
- Names: characters/monsters in combat are addressed by **combatant key**: characters by their unique name (e.g. `"Kira"`), monsters by `slug-N` (e.g. `"goblin-1"`, 1-based per encounter).
- Conventional commits <50 chars; `uv run pytest` + `uv run ruff check .` before every commit. Command/integration tests live in `tests/commands/` and `tests/integration/`.

## File Map

```
src/dm_engine/state/__init__.py
src/dm_engine/state/store.py        # CampaignStore: schema, open/create, transaction, accessors
src/dm_engine/state/models.py       # Combatant + small state models
src/dm_engine/state/sheets.py       # markdown sheet renderer
src/dm_engine/commands/__init__.py  # imports all command modules (registration side effect)
src/dm_engine/commands/envelope.py  # FC-1
src/dm_engine/commands/registry.py  # @command, execute, CommandContext, RecordingRoller, open_campaign_context
src/dm_engine/commands/campaign.py  # create_campaign, get_campaign_brief, end_session, checkpoint
src/dm_engine/commands/world.py     # set_scene, travel, create_npc, create_location, update_quest
src/dm_engine/commands/characters.py# create_character, get_character_sheet, award_xp
src/dm_engine/commands/checks.py    # skill_check, saving_throw, death_save (+ SKILL_ABILITIES)
src/dm_engine/commands/combat.py    # start_combat, next_turn, move, end_combat, get_scene_state
src/dm_engine/commands/attacks.py   # attack, apply_condition, remove_condition
src/dm_engine/commands/spells.py    # cast_spell (tiered), SPELLCASTING_ABILITY
src/dm_engine/commands/resources.py # rest, use_item, add_item, remove_item
src/dm_engine/commands/queries.py   # lookup_rule, lookup_monster, lookup_spell
src/dm_engine/commands/rulings.py   # dm_ruling
src/dm_engine/cli/app.py            # + dm audit, dm sheet, dm cmd
```

---

### Task 1: Seed `class_levels` (slots & features by class level)

**Files:**
- Modify: `src/dm_engine/content/seed.py`, `src/dm_engine/content/lookup.py`
- Test: extend `tests/test_seed.py`, `tests/test_lookup.py`

**Interfaces:**
- Consumes: existing `build_rules_db`, `RulesDB`, vendored `data/srd/2014/structured/5e-SRD-Levels.json` (290 records: 240 base-class rows — those without a `"subclass"` key — plus 50 subclass rows to be skipped).
- Produces (frozen for later tasks):
  - New table `class_levels(class_slug TEXT, level INTEGER, prof_bonus INTEGER, spellcasting TEXT, features TEXT, data TEXT, PRIMARY KEY (class_slug, level))` — `spellcasting` is the raw upstream spellcasting object JSON (or `NULL` when absent), `features` the raw features list JSON, `data` the full raw record.
  - `RulesDB.get_class_level(class_slug: str, level: int) -> dict | None` (parsed full record)
  - `RulesDB.spell_slots_for(class_slug: str, level: int) -> dict[int, int]` — `{slot_level: count}` for slot levels with count > 0, `{}` for non-casters/no record. (Warlock pact slots appear under `class_specific`, not `spellcasting`; v1 reads `spellcasting` only — warlock returns whatever `spellcasting` carries, which is `{}`/absent at most levels. Acceptable; note in the docstring.)

- [ ] **Step 1: Create branch** `git checkout -b feat/m3-state-commands`

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_seed.py`:
```python
def test_class_levels_table(rules_db):
    import contextlib
    with contextlib.closing(sqlite3.connect(rules_db)) as conn:
        (n,) = conn.execute("SELECT COUNT(*) FROM class_levels").fetchone()
        assert n == 240  # 12 classes x 20 levels, subclass rows excluded
        row = conn.execute(
            "SELECT prof_bonus, spellcasting FROM class_levels"
            " WHERE class_slug='cleric' AND level=1"
        ).fetchone()
        assert row[0] == 2
        assert json.loads(row[1])["spell_slots_level_1"] == 2
```

Append to `tests/test_lookup.py`:
```python
def test_spell_slots_for_cleric(rules_db):
    with RulesDB(rules_db) as db:
        assert db.spell_slots_for("cleric", 1) == {1: 2}
        assert db.spell_slots_for("cleric", 5) == {1: 4, 2: 3, 3: 2}
        assert db.spell_slots_for("fighter", 5) == {}
        assert db.spell_slots_for("cleric", 99) == {}


def test_get_class_level(rules_db):
    with RulesDB(rules_db) as db:
        record = db.get_class_level("wizard", 3)
        assert record is not None and record["prof_bonus"] == 2
        assert db.get_class_level("wizard", 21) is None
```

- [ ] **Step 3: Verify failure, implement, verify pass**

Seeder: read `5e-SRD-Levels.json`, skip records containing a `"subclass"` key, insert `(r["class"]["index"], r["level"], r["prof_bonus"], json.dumps(r.get("spellcasting")) if present else None, json.dumps(r.get("features", [])), json.dumps(r))`. Add `class_levels` to the schema and the counts dict. Lookup: `spell_slots_for` parses the `spellcasting` JSON and returns `{int(k.rsplit("_", 1)[1]): v for k, v in ... if k.startswith("spell_slots_level_") and v > 0}`.

Run: `uv run pytest tests/test_seed.py tests/test_lookup.py -v` → PASS; full suite + ruff.

- [ ] **Step 4: Commit** `git commit -m "feat: seed class_levels with slots"`

---

### Task 2: CampaignStore

**Files:**
- Create: `src/dm_engine/state/__init__.py` (empty), `src/dm_engine/state/store.py`, `src/dm_engine/state/models.py`
- Test: `tests/state/__init__.py` (empty), `tests/state/test_store.py`

**Interfaces:**
- Produces (frozen): `CampaignStore` with the exact API below; `Combatant` model. Every command handler mutates state ONLY through these methods.

- [ ] **Step 1: Write `src/dm_engine/state/models.py`**

```python
"""State models shared by the store and command handlers."""

from __future__ import annotations

from pydantic import BaseModel

from dm_engine.rules.bands import Band


class Combatant(BaseModel):
    """One entry in combat_state.combatants (JSON).

    Characters keep hp/conditions in their own tables (source of truth);
    monster instances carry theirs here. `key` addresses the combatant in
    every combat command: the character's name, or '<slug>-<n>' for monsters.
    """

    key: str
    kind: str  # "character" | "monster"
    name: str
    character_id: int | None = None
    monster_slug: str | None = None
    initiative: int
    dex_modifier: int
    ac: int
    hp: int | None = None       # monsters only
    max_hp: int | None = None   # monsters only
    xp: int = 0                 # monsters only: XP value for the award
    band: Band = "near"
    engaged_with: list[str] = []
    conditions: list[str] = []  # monsters only
    defeated: bool = False
    # current-turn action economy, reset by next_turn (JSON of rules TurnBudget)
    budget: dict | None = None
```

- [ ] **Step 2: Write the failing store tests**

`tests/state/test_store.py`:
```python
import json
import sqlite3
from pathlib import Path

import pytest

from dm_engine.state.store import CampaignStore


@pytest.fixture()
def campaigns_dir(tmp_path) -> Path:
    return tmp_path / "campaigns"


def _create(campaigns_dir) -> CampaignStore:
    return CampaignStore.create(
        campaigns_dir, slug="test-camp", name="Test Campaign",
        death_mode="narrative", rng_seed=42,
        skeleton={"premise": "save the valley", "acts": [], "factions": []},
    )


def test_create_builds_layout_and_meta(campaigns_dir):
    store = _create(campaigns_dir)
    try:
        assert (campaigns_dir / "test-camp" / "campaign.sqlite").exists()
        meta = store.campaign_meta()
        assert meta["slug"] == "test-camp"
        assert meta["edition"] == "2014"
        assert meta["death_mode"] == "narrative"
        assert meta["rng_seed"] == 42
        assert meta["skeleton"]["premise"] == "save the valley"
        clock = store.world_clock()
        assert clock["day"] == 1
    finally:
        store.close()


def test_create_refuses_existing_slug(campaigns_dir):
    _create(campaigns_dir).close()
    with pytest.raises(FileExistsError):
        _create(campaigns_dir)


def test_open_snapshots_and_reads(campaigns_dir):
    _create(campaigns_dir).close()
    store = CampaignStore.open(campaigns_dir, "test-camp")
    try:
        snaps = list((campaigns_dir / "test-camp" / "snapshots").glob("*.sqlite"))
        assert len(snaps) == 1
        assert store.campaign_meta()["name"] == "Test Campaign"
    finally:
        store.close()


def test_transaction_commits_and_rolls_back(campaigns_dir):
    store = _create(campaigns_dir)
    try:
        with store.transaction():
            store.upsert_location("greenhollow", "Greenhollow", "A sleepy town", region="valley")
        assert store.get_location("greenhollow")["name"] == "Greenhollow"

        with pytest.raises(RuntimeError):
            with store.transaction():
                store.upsert_location("doomed", "Doomed", "never lands", region=None)
                raise RuntimeError("engine bug")
        assert store.get_location("doomed") is None
    finally:
        store.close()


def test_event_log_is_append_only_fc6(campaigns_dir):
    store = _create(campaigns_dir)
    try:
        with store.transaction():
            event_id = store.append_event(
                command="skill_check", inputs={"character": "Kira"},
                result={"ok": True}, rolls=[{"notation": "1d20", "total": 14}],
            )
        assert event_id == 1
        row = store.conn.execute(
            "SELECT command, inputs, rolls, is_ruling, rationale FROM event_log"
        ).fetchone()
        assert row[0] == "skill_check"
        assert json.loads(row[1]) == {"character": "Kira"}
        assert row[3] == 0 and row[4] is None

        with store.transaction():
            rid = store.append_event(
                command="dm_ruling", inputs={}, result={"ok": True}, rolls=[],
                is_ruling=True, rationale="rope trick edge case",
            )
        assert rid == 2
        # rationale required when is_ruling
        with pytest.raises(ValueError):
            with store.transaction():
                store.append_event(command="dm_ruling", inputs={}, result={},
                                   rolls=[], is_ruling=True, rationale="")
    finally:
        store.close()


def test_rng_draws_persist(campaigns_dir):
    store = _create(campaigns_dir)
    try:
        with store.transaction():
            store.set_rng_draws(17)
        assert store.campaign_meta()["rng_draws"] == 17
    finally:
        store.close()


def test_character_and_resources_roundtrip(campaigns_dir):
    store = _create(campaigns_dir)
    try:
        with store.transaction():
            cid = store.insert_character(
                name="Kira", role="pc", class_slug="fighter", race_slug="human",
                level=1, abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
                max_hp=12, ac=16, speed=30,
                proficiencies={"skills": ["athletics"], "saves": ["str", "con"]},
                attacks=[{"name": "longsword", "ranged": False, "range_ft": 5,
                          "long_range_ft": None, "damage": "1d8", "damage_type": "slashing",
                          "ability": "str", "proficient": True}],
                spells_known=[], spell_slots={},
            )
        char = store.get_character("Kira")
        assert char["id"] == cid and char["level"] == 1 and char["max_hp"] == 12
        res = store.get_resources(cid)
        assert res["hp"] == 12 and res["hit_dice_remaining"] == 1
        with store.transaction():
            store.update_resources(cid, hp=5, conditions=["prone"])
        assert store.get_resources(cid)["hp"] == 5
        assert store.get_resources(cid)["conditions"] == ["prone"]
        assert store.get_character("nobody") is None
        assert [c["name"] for c in store.party()] == ["Kira"]
    finally:
        store.close()
```

- [ ] **Step 3: Run to verify failure** (`ModuleNotFoundError`), then write the store

`src/dm_engine/state/store.py`:
```python
"""Campaign persistence: one sqlite file per campaign (FC-5, FC-6).

The store is dumb and safe: typed accessors over the schema, one
`transaction()` context manager, snapshot-on-open. All game logic lives in
the command handlers; nothing here rolls dice or interprets rules.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE campaign (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    edition TEXT NOT NULL,
    death_mode TEXT NOT NULL CHECK (death_mode IN ('narrative','hardcore')),
    rng_seed INTEGER NOT NULL,
    rng_draws INTEGER NOT NULL DEFAULT 0,
    skeleton TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('pc','companion')),
    class_slug TEXT NOT NULL,
    race_slug TEXT NOT NULL,
    level INTEGER NOT NULL,
    xp INTEGER NOT NULL DEFAULT 0,
    abilities TEXT NOT NULL,
    max_hp INTEGER NOT NULL,
    ac INTEGER NOT NULL,
    speed INTEGER NOT NULL,
    proficiencies TEXT NOT NULL,
    attacks TEXT NOT NULL,
    spells_known TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','defeated','dead','departed'))
);
CREATE TABLE resources (
    character_id INTEGER PRIMARY KEY REFERENCES characters(id),
    hp INTEGER NOT NULL,
    temp_hp INTEGER NOT NULL DEFAULT 0,
    hit_dice_remaining INTEGER NOT NULL,
    spell_slots TEXT NOT NULL DEFAULT '{}',
    conditions TEXT NOT NULL DEFAULT '[]',
    exhaustion INTEGER NOT NULL DEFAULT 0,
    death_saves TEXT NOT NULL
        DEFAULT '{"successes": 0, "failures": 0, "stable": false, "dead": false}',
    concentration TEXT
);
CREATE TABLE inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL REFERENCES characters(id),
    name TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    equipped INTEGER NOT NULL DEFAULT 0,
    attuned INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);
CREATE TABLE npcs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    disposition TEXT NOT NULL DEFAULT 'neutral',
    location_slug TEXT,
    notes TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE locations (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    region TEXT,
    discovered INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE quests (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','active','completed','failed','abandoned')),
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE combat_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    active INTEGER NOT NULL DEFAULT 0,
    round INTEGER NOT NULL DEFAULT 0,
    turn_index INTEGER NOT NULL DEFAULT 0,
    combatants TEXT NOT NULL DEFAULT '[]',
    encounter_xp INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE session_recaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    kind TEXT NOT NULL CHECK (kind IN ('session_end','checkpoint')),
    content TEXT NOT NULL
);
CREATE TABLE world_clock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    day INTEGER NOT NULL DEFAULT 1,
    minutes INTEGER NOT NULL DEFAULT 480,
    location_slug TEXT,
    scene TEXT
);
CREATE TABLE event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    command TEXT NOT NULL,
    inputs TEXT NOT NULL,
    result TEXT NOT NULL,
    rolls TEXT NOT NULL,
    is_ruling INTEGER NOT NULL DEFAULT 0,
    rationale TEXT
);
"""

_JSON_CHARACTER_FIELDS = {"abilities", "proficiencies", "attacks", "spells_known"}
_JSON_RESOURCE_FIELDS = {"spell_slots", "conditions", "death_saves", "concentration"}


class CampaignStore:
    def __init__(self, conn: sqlite3.Connection, root: Path):
        self.conn = conn
        self.root = root  # campaigns/<slug>/
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")

    # -- lifecycle -----------------------------------------------------

    @classmethod
    def create(
        cls,
        campaigns_dir: Path,
        *,
        slug: str,
        name: str,
        death_mode: str,
        rng_seed: int,
        skeleton: dict,
        edition: str = "2014",
    ) -> "CampaignStore":
        root = Path(campaigns_dir) / slug
        db_path = root / "campaign.sqlite"
        if db_path.exists():
            raise FileExistsError(f"campaign {slug!r} already exists at {db_path}")
        root.mkdir(parents=True, exist_ok=True)
        (root / "sheets").mkdir(exist_ok=True)
        (root / "snapshots").mkdir(exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO campaign (id, slug, name, edition, death_mode, rng_seed, skeleton)"
            " VALUES (1, ?, ?, ?, ?, ?, ?)",
            (slug, name, edition, death_mode, rng_seed, json.dumps(skeleton)),
        )
        conn.execute("INSERT INTO world_clock (id) VALUES (1)")
        conn.execute("INSERT INTO combat_state (id) VALUES (1)")
        conn.commit()
        return cls(conn, root)

    @classmethod
    def open(cls, campaigns_dir: Path, slug: str) -> "CampaignStore":
        """Open an existing campaign; copies a session-start snapshot (FC-5)."""
        root = Path(campaigns_dir) / slug
        db_path = root / "campaign.sqlite"
        if not db_path.exists():
            raise FileNotFoundError(f"no campaign at {db_path}")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        (root / "snapshots").mkdir(exist_ok=True)
        shutil.copy2(db_path, root / "snapshots" / f"{stamp}.sqlite")
        return cls(sqlite3.connect(db_path), root)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self):
        """One transaction per command (FC-6). Commits on success, rolls
        back and re-raises on any exception."""
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    # -- campaign / clock ----------------------------------------------

    def campaign_meta(self) -> dict:
        row = self.conn.execute("SELECT * FROM campaign WHERE id = 1").fetchone()
        meta = dict(row)
        meta["skeleton"] = json.loads(meta["skeleton"])
        return meta

    def set_rng_draws(self, draws: int) -> None:
        self.conn.execute("UPDATE campaign SET rng_draws = ? WHERE id = 1", (draws,))

    def world_clock(self) -> dict:
        return dict(self.conn.execute("SELECT * FROM world_clock WHERE id = 1").fetchone())

    def update_world_clock(self, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE world_clock SET {cols} WHERE id = 1", tuple(fields.values()))

    # -- event log (FC-6) ------------------------------------------------

    def append_event(
        self,
        command: str,
        inputs: dict,
        result: dict,
        rolls: list[dict],
        *,
        is_ruling: bool = False,
        rationale: str | None = None,
    ) -> int:
        if is_ruling and not (rationale or "").strip():
            raise ValueError("dm_ruling events require a non-empty rationale")
        cur = self.conn.execute(
            "INSERT INTO event_log (command, inputs, result, rolls, is_ruling, rationale)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (command, json.dumps(inputs), json.dumps(result), json.dumps(rolls),
             int(is_ruling), rationale),
        )
        return int(cur.lastrowid)

    def event_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]

    def rulings(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, created_at, command, rationale, result FROM event_log"
            " WHERE is_ruling = 1 ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- characters & resources ------------------------------------------

    def insert_character(
        self,
        *,
        name: str,
        role: str,
        class_slug: str,
        race_slug: str,
        level: int,
        abilities: dict,
        max_hp: int,
        ac: int,
        speed: int,
        proficiencies: dict,
        attacks: list[dict],
        spells_known: list[str],
        spell_slots: dict,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO characters (name, role, class_slug, race_slug, level,"
            " abilities, max_hp, ac, speed, proficiencies, attacks, spells_known)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, role, class_slug, race_slug, level, json.dumps(abilities),
             max_hp, ac, speed, json.dumps(proficiencies), json.dumps(attacks),
             json.dumps(spells_known)),
        )
        cid = int(cur.lastrowid)
        slots = {str(k): {"max": v, "remaining": v} for k, v in spell_slots.items()}
        self.conn.execute(
            "INSERT INTO resources (character_id, hp, hit_dice_remaining, spell_slots)"
            " VALUES (?, ?, ?, ?)",
            (cid, max_hp, level, json.dumps(slots)),
        )
        return cid

    def get_character(self, name: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM characters WHERE name = ?", (name,)
        ).fetchone()
        return self._parse_character(row) if row else None

    def get_character_by_id(self, cid: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM characters WHERE id = ?", (cid,)).fetchone()
        return self._parse_character(row) if row else None

    def _parse_character(self, row: sqlite3.Row) -> dict:
        char = dict(row)
        for field in _JSON_CHARACTER_FIELDS:
            char[field] = json.loads(char[field])
        return char

    def party(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM characters WHERE status IN ('active','defeated') ORDER BY id"
        ).fetchall()
        return [self._parse_character(r) for r in rows]

    def update_character(self, cid: int, **fields: Any) -> None:
        payload = {
            k: json.dumps(v) if k in _JSON_CHARACTER_FIELDS else v
            for k, v in fields.items()
        }
        cols = ", ".join(f"{k} = ?" for k in payload)
        self.conn.execute(
            f"UPDATE characters SET {cols} WHERE id = ?", (*payload.values(), cid)
        )

    def get_resources(self, cid: int) -> dict:
        row = self.conn.execute(
            "SELECT * FROM resources WHERE character_id = ?", (cid,)
        ).fetchone()
        res = dict(row)
        for field in _JSON_RESOURCE_FIELDS:
            if res[field] is not None:
                res[field] = json.loads(res[field])
        return res

    def update_resources(self, cid: int, **fields: Any) -> None:
        payload = {
            k: (json.dumps(v) if k in _JSON_RESOURCE_FIELDS and v is not None else v)
            for k, v in fields.items()
        }
        cols = ", ".join(f"{k} = ?" for k in payload)
        self.conn.execute(
            f"UPDATE resources SET {cols} WHERE character_id = ?",
            (*payload.values(), cid),
        )

    # -- inventory --------------------------------------------------------

    def add_item(self, cid: int, name: str, quantity: int, notes: str | None = None) -> int:
        row = self.conn.execute(
            "SELECT id, quantity FROM inventory WHERE character_id = ? AND name = ?",
            (cid, name),
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE inventory SET quantity = quantity + ? WHERE id = ?",
                (quantity, row["id"]),
            )
            return int(row["id"])
        cur = self.conn.execute(
            "INSERT INTO inventory (character_id, name, quantity, notes) VALUES (?, ?, ?, ?)",
            (cid, name, quantity, notes),
        )
        return int(cur.lastrowid)

    def remove_item(self, cid: int, name: str, quantity: int) -> bool:
        """Decrement (deleting at zero). Returns False if not enough held."""
        row = self.conn.execute(
            "SELECT id, quantity FROM inventory WHERE character_id = ? AND name = ?",
            (cid, name),
        ).fetchone()
        if row is None or row["quantity"] < quantity:
            return False
        if row["quantity"] == quantity:
            self.conn.execute("DELETE FROM inventory WHERE id = ?", (row["id"],))
        else:
            self.conn.execute(
                "UPDATE inventory SET quantity = quantity - ? WHERE id = ?",
                (quantity, row["id"]),
            )
        return True

    def items_for(self, cid: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM inventory WHERE character_id = ? ORDER BY name", (cid,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- world -----------------------------------------------------------

    def upsert_npc(self, name: str, disposition: str, location_slug: str | None,
                   notes: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO npcs (name, disposition, location_slug, notes) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET disposition = excluded.disposition,"
            " location_slug = excluded.location_slug, notes = excluded.notes",
            (name, disposition, location_slug, json.dumps(notes)),
        )
        row = self.conn.execute("SELECT id FROM npcs WHERE name = ?", (name,)).fetchone()
        return int(row["id"])

    def upsert_location(self, slug: str, name: str, description: str,
                        region: str | None) -> None:
        self.conn.execute(
            "INSERT INTO locations (slug, name, description, region) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(slug) DO UPDATE SET name = excluded.name,"
            " description = excluded.description, region = excluded.region",
            (slug, name, description, region),
        )

    def get_location(self, slug: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM locations WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def upsert_quest(self, slug: str, title: str, status: str, notes: str) -> None:
        self.conn.execute(
            "INSERT INTO quests (slug, title, status, notes) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(slug) DO UPDATE SET title = excluded.title,"
            " status = excluded.status, notes = excluded.notes",
            (slug, title, status, notes),
        )

    def quests(self, statuses: tuple[str, ...] = ("open", "active")) -> list[dict]:
        marks = ",".join("?" * len(statuses))
        rows = self.conn.execute(
            f"SELECT * FROM quests WHERE status IN ({marks}) ORDER BY slug", statuses
        ).fetchall()
        return [dict(r) for r in rows]

    # -- combat ------------------------------------------------------------

    def combat(self) -> dict:
        row = dict(self.conn.execute("SELECT * FROM combat_state WHERE id = 1").fetchone())
        row["combatants"] = json.loads(row["combatants"])
        return row

    def update_combat(self, **fields: Any) -> None:
        payload = {
            k: (json.dumps(v) if k == "combatants" else v) for k, v in fields.items()
        }
        cols = ", ".join(f"{k} = ?" for k in payload)
        self.conn.execute(f"UPDATE combat_state SET {cols} WHERE id = 1",
                          tuple(payload.values()))

    # -- recaps --------------------------------------------------------------

    def add_recap(self, kind: str, content: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO session_recaps (kind, content) VALUES (?, ?)", (kind, content)
        )
        return int(cur.lastrowid)

    def latest_recap(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM session_recaps ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
```

- [ ] **Step 4: Run tests to verify pass** (`uv run pytest tests/state -v`), full suite + ruff

- [ ] **Step 5: Commit** `git commit -m "feat: add campaign store (FC-5/FC-6)"`

---

### Task 3: Envelope, registry, context (FC-1/FC-3)

**Files:**
- Create: `src/dm_engine/commands/__init__.py`, `src/dm_engine/commands/envelope.py`, `src/dm_engine/commands/registry.py`
- Test: `tests/commands/__init__.py` (empty), `tests/commands/test_registry.py`, `tests/commands/conftest.py`

**Interfaces:**
- Consumes: `CampaignStore` (Task 2), `SeededDiceRoller`/`Roll`/`DiceRoller` (M2 dice), `RulesDB` (M1).
- Produces (frozen — every command task builds on these exact names):
  - `CommandResult` (FC-1 verbatim).
  - `@command("name")` decorator; `execute(name: str, ctx: CommandContext, **kwargs) -> CommandResult`; `registered_commands() -> dict[str, Callable]` (name → handler, for the M4 MCP adapter).
  - `CommandContext(store: CampaignStore, roller: RecordingRoller, rules: RulesDB)`.
  - `RecordingRoller(seed: int, initial_draws: int = 0)` — implements the FC-2 `DiceRoller` protocol; `.draws` counts individual engine-rolled dice (player-supplied rolls draw nothing); `.begin_capture()` / `.captured() -> list[Roll]`.
  - `open_campaign_context(campaigns_dir: Path, slug: str, rules_db_path: Path) -> CommandContext` — opens the store (snapshot side effect), fast-forwards a fresh `SeededDiceRoller(rng_seed)` by `rng_draws` single d20 draws, wraps in `RecordingRoller`.
  - Post-command hook: after every `ok=True` execution, the registry calls `dm_engine.state.sheets.write_party_sheets(store)` — Task 3 stubs this import with a no-op module attribute check (`getattr`) so Task 5 can drop the real renderer in without touching the registry. Concretely: registry does `from dm_engine.state import sheets` and calls `sheets.write_party_sheets(store)`; Task 3 creates `src/dm_engine/state/sheets.py` containing a documented no-op `def write_party_sheets(store) -> list[Path]: return []`.

- [ ] **Step 1: Write `envelope.py`** — FC-1 verbatim:

```python
"""FC-1: the command result envelope. MCP and CLI serialize it verbatim."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CommandResult(BaseModel):
    ok: bool
    command: str
    refusal: str | None = None
    digest: str
    data: dict[str, Any]
    gm_only: bool = False
    event_ids: list[int] = []


def refuse(command: str, reason: str) -> CommandResult:
    """Structured refusal helper — the ONLY way handlers report illegal actions."""
    return CommandResult(ok=False, command=command, refusal=reason,
                         digest=f"Refused: {reason}", data={})
```

- [ ] **Step 2: Write the failing registry tests**

`tests/commands/conftest.py` (shared by all command-task tests):
```python
import shutil
from pathlib import Path

import pytest

from dm_engine.commands.registry import CommandContext, RecordingRoller
from dm_engine.content.lookup import RulesDB
from dm_engine.state.store import CampaignStore

REPO_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(scope="session")
def rules_path(rules_db):
    # reuse the session-scoped seeded rules.sqlite from tests/conftest.py
    return rules_db


@pytest.fixture()
def ctx(tmp_path, rules_path):
    store = CampaignStore.create(
        tmp_path / "campaigns", slug="t", name="T", death_mode="narrative",
        rng_seed=99, skeleton={"premise": "test"},
    )
    context = CommandContext(
        store=store, roller=RecordingRoller(99), rules=RulesDB(rules_path)
    )
    yield context
    store.close()
```

(The root `tests/conftest.py` already defines the session-scoped `rules_db` fixture that seeds a temp rules.sqlite.)

`tests/commands/test_registry.py`:
```python
import pytest

from dm_engine.commands import registry
from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.commands.registry import RecordingRoller, command, execute


@command("_test_echo")
def _echo(ctx, **kwargs) -> CommandResult:
    return CommandResult(ok=True, command="_test_echo", digest="echoed", data=kwargs)


@command("_test_refuse")
def _refuse(ctx, **kwargs) -> CommandResult:
    return refuse("_test_refuse", "not allowed")


@command("_test_boom")
def _boom(ctx, **kwargs) -> CommandResult:
    ctx.store.upsert_location("half", "Half", "should roll back", region=None)
    raise RuntimeError("engine bug")


@command("_test_roll")
def _roll(ctx, **kwargs) -> CommandResult:
    r = ctx.roller.roll("2d6", player_value=kwargs.get("player_value"))
    return CommandResult(ok=True, command="_test_roll", digest=f"rolled {r.total}",
                         data={"total": r.total})


def test_execute_appends_event_and_sets_event_ids(ctx):
    result = execute("_test_echo", ctx, x=1)
    assert result.ok and result.event_ids == [1]
    row = ctx.store.conn.execute(
        "SELECT command, inputs FROM event_log WHERE id = 1"
    ).fetchone()
    assert row["command"] == "_test_echo"
    assert '"x": 1' in row["inputs"]


def test_unknown_command_is_a_refusal_not_an_error(ctx):
    result = execute("no_such_command", ctx)
    assert result.ok is False
    assert "no_such_command" in result.refusal
    assert ctx.store.event_count() == 1  # refusals are logged too


def test_refusals_are_logged(ctx):
    result = execute("_test_refuse", ctx)
    assert result.ok is False
    assert ctx.store.event_count() == 1


def test_handler_exception_rolls_back_everything(ctx):
    with pytest.raises(RuntimeError):
        execute("_test_boom", ctx)
    assert ctx.store.get_location("half") is None   # state rolled back
    assert ctx.store.event_count() == 0             # no event row either


def test_rolls_are_captured_and_draws_persisted(ctx):
    result = execute("_test_roll", ctx)
    assert result.ok
    row = ctx.store.conn.execute("SELECT rolls FROM event_log WHERE id = 1").fetchone()
    assert '"notation": "2d6"' in row["rolls"]
    assert ctx.store.campaign_meta()["rng_draws"] == 2  # two engine dice drawn


def test_player_value_draws_nothing_and_is_flagged(ctx):
    result = execute("_test_roll", ctx, player_value=7)
    assert result.ok and result.data["total"] == 7
    row = ctx.store.conn.execute("SELECT rolls FROM event_log WHERE id = 1").fetchone()
    assert '"player_supplied": true' in row["rolls"]
    assert ctx.store.campaign_meta()["rng_draws"] == 0


def test_recording_roller_fast_forward_is_deterministic():
    a = RecordingRoller(7)
    first = [a.roll("1d20").total for _ in range(5)]
    b = RecordingRoller(7, initial_draws=3)
    assert [b.roll("1d20").total for _ in range(2)] == first[3:]


def test_registered_commands_lists_names():
    assert "_test_echo" in registry.registered_commands()
```

- [ ] **Step 3: Verify failure, write `registry.py`**

```python
"""FC-3: the command registry — the ONLY mutation path into a campaign.

execute() wraps every handler in one store transaction (FC-6): handler
mutations, the event row, the persisted RNG draw count, and (on success)
sheet re-rendering all land atomically. Refusals (ok=False) are logged as
events; handler exceptions roll everything back and propagate — they are
engine bugs, never gameplay.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.content.lookup import RulesDB
from dm_engine.rules.dice import Roll, SeededDiceRoller
from dm_engine.state import sheets
from dm_engine.state.store import CampaignStore

_COMMANDS: dict[str, Callable[..., CommandResult]] = {}


def command(name: str) -> Callable:
    def register(fn: Callable[..., CommandResult]) -> Callable[..., CommandResult]:
        if name in _COMMANDS:
            raise ValueError(f"duplicate command name: {name}")
        _COMMANDS[name] = fn
        return fn
    return register


def registered_commands() -> dict[str, Callable[..., CommandResult]]:
    return dict(_COMMANDS)


class RecordingRoller:
    """FC-2 DiceRoller that counts engine draws and captures Rolls per command."""

    def __init__(self, seed: int, initial_draws: int = 0):
        self._inner = SeededDiceRoller(seed)
        self.draws = 0
        self._captured: list[Roll] = []
        for _ in range(initial_draws):
            self._inner.roll("1d20")
        self.draws = initial_draws

    def roll(self, notation: str, *, player_value: int | None = None,
             gm_only: bool = False) -> Roll:
        result = self._inner.roll(notation, player_value=player_value, gm_only=gm_only)
        if not result.player_supplied:
            self.draws += len(result.rolls)
        self._captured.append(result)
        return result

    def begin_capture(self) -> None:
        self._captured = []

    def captured(self) -> list[Roll]:
        return list(self._captured)


class CommandContext:
    def __init__(self, store: CampaignStore, roller: RecordingRoller, rules: RulesDB):
        self.store = store
        self.roller = roller
        self.rules = rules


def execute(name: str, ctx: CommandContext, **kwargs) -> CommandResult:
    handler = _COMMANDS.get(name)
    ctx.roller.begin_capture()
    with ctx.store.transaction():
        if handler is None:
            result = refuse(name, f"unknown command {name!r}")
        else:
            result = handler(ctx, **kwargs)
        rolls = [r.model_dump() for r in ctx.roller.captured()]
        event_id = ctx.store.append_event(
            command=name,
            inputs=kwargs,
            result=result.model_dump(),
            rolls=rolls,
            is_ruling=(name == "dm_ruling" and result.ok),
            rationale=kwargs.get("rationale") if name == "dm_ruling" else None,
        )
        result.event_ids = [event_id]
        ctx.store.set_rng_draws(ctx.roller.draws)
        if result.ok:
            sheets.write_party_sheets(ctx.store)
    return result


def open_campaign_context(
    campaigns_dir: Path, slug: str, rules_db_path: Path
) -> CommandContext:
    store = CampaignStore.open(campaigns_dir, slug)
    meta = store.campaign_meta()
    roller = RecordingRoller(meta["rng_seed"], initial_draws=meta["rng_draws"])
    return CommandContext(store=store, roller=roller, rules=RulesDB(rules_db_path))
```

`src/dm_engine/state/sheets.py` (Task 3 stub — Task 5 replaces the body):
```python
"""Materialized markdown character sheets. Task 5 implements the renderer;
until then the registry's post-command hook is a no-op."""

from __future__ import annotations

from pathlib import Path

from dm_engine.state.store import CampaignStore


def write_party_sheets(store: CampaignStore) -> list[Path]:
    return []
```

`src/dm_engine/commands/__init__.py` (grows as command modules land; Task 3 version):
```python
"""Command registration: importing this package registers every command."""

from dm_engine.commands import registry  # noqa: F401
```

Note on `dm_ruling` in `execute()`: the `is_ruling`/`rationale` columns are stamped from the registry so the audit trail cannot be forgotten by a handler; the `dm_ruling` handler itself (Task 10) validates the rationale and refuses when it is missing, so `append_event`'s ValueError backstop is never hit in normal play (a refused dm_ruling logs with `is_ruling=0` because `result.ok` is False — the refusal is auditable but is not itself a ruling).

- [ ] **Step 4: Run tests** (`uv run pytest tests/commands -v`) → PASS; full suite + ruff

- [ ] **Step 5: Commit** `git commit -m "feat: add command registry (FC-1/FC-3)"`

---

### Task 4: Campaign & session commands

**Files:**
- Create: `src/dm_engine/commands/campaign.py`, `src/dm_engine/commands/world.py`
- Modify: `src/dm_engine/commands/__init__.py` (add `from dm_engine.commands import campaign, world  # noqa: F401`)
- Test: `tests/commands/test_campaign.py`, `tests/commands/test_world.py`

**Interfaces:**
- Consumes: registry/envelope (Task 3), store accessors (Task 2).
- Produces — command contracts (frozen):

| command | kwargs | behavior |
|---|---|---|
| `create_campaign` | `name: str`, `slug: str`, `death_mode: str = "narrative"`, `skeleton: dict`, `starting_region: dict \| None = None`, `seed: int \| None = None` | **Special case:** `create_campaign` is NOT a registry command — there is no open store yet when it runs. Campaign creation happens via `CampaignStore.create` + a `bootstrap_campaign(campaigns_dir, rules_db_path, *, slug, name, death_mode, skeleton, starting_region=None, seed=None) -> CommandContext` helper in `campaign.py` that creates the store (seed defaults to a `random.SystemRandom().randrange(2**31)`), writes `starting_region` locations/NPCs via store upserts, appends a synthetic `create_campaign` event row (inputs = the kwargs, result = an ok CommandResult dump), and returns a ready CommandContext. M4's MCP tool `create_campaign` calls this helper. |
| `get_campaign_brief` | *(none)* | `data` = `{"campaign": {name, slug, edition, death_mode}, "skeleton": …, "clock": world_clock row, "scene": clock.scene, "party": [{name, role, class_slug, level, xp, hp, max_hp, conditions, status, spell_slots}], "quests": open+active quests, "recap": latest recap content or None, "combat_active": bool}`. digest e.g. `"Campaign brief: 2 party members, day 3, combat inactive"`. Read-only. |
| `end_session` | `recap: str` | refuse if `recap.strip()` empty. Adds `session_recaps(kind='session_end')`. digest `"Session ended; recap saved"`. |
| `checkpoint` | `content: str` | same but `kind='checkpoint'`; `gm_only=True` on the result. |
| `set_scene` (in world.py) | `description: str`, `location_slug: str \| None = None` | updates `world_clock.scene` (and `location_slug` if given; refuse if the slug names no known location). |
| `travel` | `destination_slug: str`, `hours: int = 0`, `days: int = 0` | refuse if destination unknown or `hours + days <= 0` combined ≤ 0. Advances clock (`minutes += hours*60; day += days + overflow`), sets `location_slug`, clears `scene`. data includes new clock. |
| `create_npc` | `name`, `disposition: str = "neutral"`, `location_slug=None`, `notes: dict \| None = None` | upsert; digest `"NPC Mara recorded (friendly, at greenhollow)"`. |
| `create_location` | `slug`, `name`, `description`, `region=None` | upsert. |
| `update_quest` | `slug`, `title`, `status: str = "open"`, `notes: str = ""` | refuse on invalid status (the store CHECK list). |

- Binding tests (must appear verbatim in the task's test files, alongside the implementer's own per-command mutation+event tests):

```python
# tests/commands/test_campaign.py
from dm_engine.commands import registry
from dm_engine.commands.campaign import bootstrap_campaign


def test_bootstrap_creates_store_and_logs_event(tmp_path, rules_path):
    ctx = bootstrap_campaign(
        tmp_path / "campaigns", rules_path, slug="valley", name="Valley of Ash",
        death_mode="hardcore", skeleton={"premise": "stop the cult"},
        starting_region={
            "locations": [{"slug": "greenhollow", "name": "Greenhollow",
                           "description": "A sleepy town", "region": "valley"}],
            "npcs": [{"name": "Mara", "disposition": "friendly",
                      "location_slug": "greenhollow", "notes": {"role": "innkeep"}}],
        },
    )
    try:
        assert ctx.store.campaign_meta()["death_mode"] == "hardcore"
        assert ctx.store.get_location("greenhollow") is not None
        row = ctx.store.conn.execute(
            "SELECT command FROM event_log WHERE id = 1").fetchone()
        assert row["command"] == "create_campaign"
    finally:
        ctx.store.close()


def test_brief_reflects_state_and_recap(ctx):
    registry.execute("end_session", ctx, recap="The party reached Greenhollow.")
    brief = registry.execute("get_campaign_brief", ctx)
    assert brief.ok
    assert brief.data["recap"] == "The party reached Greenhollow."
    assert brief.data["campaign"]["edition"] == "2014"
    assert brief.data["combat_active"] is False


def test_end_session_requires_recap(ctx):
    result = registry.execute("end_session", ctx, recap="   ")
    assert result.ok is False
```

```python
# tests/commands/test_world.py
from dm_engine.commands import registry


def test_travel_advances_clock_and_moves_party(ctx):
    registry.execute("create_location", ctx, slug="mill", name="Old Mill",
                     description="Creaky", region="valley")
    result = registry.execute("travel", ctx, destination_slug="mill", hours=30)
    assert result.ok
    clock = ctx.store.world_clock()
    assert clock["location_slug"] == "mill"
    assert clock["day"] == 2 and clock["minutes"] == 480 + 30 * 60 - 1440


def test_travel_to_unknown_location_refused(ctx):
    result = registry.execute("travel", ctx, destination_slug="atlantis", hours=1)
    assert result.ok is False and "atlantis" in result.refusal
```

- [ ] Steps: binding tests + your own per-command tests first (RED) → implement handlers → GREEN → full suite + ruff → commit `feat: add campaign and world commands`.

---

### Task 5: Character commands & sheet renderer

**Files:**
- Create: `src/dm_engine/commands/characters.py`
- Replace: `src/dm_engine/state/sheets.py` (real renderer)
- Modify: `src/dm_engine/commands/__init__.py` (import `characters`), `src/dm_engine/cli/app.py` (add `dm sheet`)
- Test: `tests/commands/test_characters.py`, `tests/state/test_sheets.py`

**Interfaces:**
- Consumes: registry, store, `RulesDB.get_class_level`/`spell_slots_for` (Task 1), M2 `progression.max_hp_for_level`/`level_for_xp`/`level_up_hp_gain`, `checks.ability_modifier`/`proficiency_bonus`.
- Produces — command contracts (frozen):

| command | kwargs | behavior |
|---|---|---|
| `create_character` | `name: str`, `role: str` ("pc"/"companion"), `class_slug: str`, `race_slug: str`, `abilities: dict` (six keys str/dex/con/int/wis/cha, each 1–30), `ac: int`, `speed: int = 30`, `proficiencies: dict` (`{"skills": [...], "saves": [...]}`), `attacks: list[dict]` (spec per Task 2 test), `spells_known: list[str] = []` | Validations, in order: unique name; role valid; class exists in rules DB (`classes` table via `get_class_level(slug, 1)`); abilities complete & in range; at most one `role="pc"` character alive per campaign. Derives: level 1, `max_hp = max_hp_for_level(hit_die, con_mod, 1)` (hit_die from rules `classes` table), spell slots = `spell_slots_for(class_slug, 1)`. digest e.g. `"Kira the fighter joins the party (HP 12, AC 16)"`. data = full sheet payload (same as get_character_sheet). |
| `get_character_sheet` | `name: str` | refuse if unknown. data = `{"character": {...typed fields...}, "resources": {...}, "inventory": [...], "markdown": rendered sheet}`. Read-only. |
| `award_xp` | `amount: int`, `reason: str` | refuse if `amount <= 0` or no active party. Splits `amount` evenly (floor) across characters with status `active`; each recipient's xp increases; any whose `level_for_xp(new_xp)` exceeds current level levels up immediately (possibly multiple levels): level, `max_hp += level_up_hp_gain` per level, hit_dice pool total = level (remaining += levels gained), spell slot maxima re-derived from `spell_slots_for(class_slug, new_level)` (remaining slots for each slot level are topped up by the delta in max, never reduced below current remaining). data = `{"per_member": int, "recipients": [{"name", "xp", "level", "leveled_up": bool, "new_max_hp"}]}`. digest announces level-ups, e.g. `"Awarded 300 XP (150 each) — Kira reaches level 2!"`. |

- Sheet renderer (frozen): `render_character_sheet(store, character_id) -> str` and `write_party_sheets(store) -> list[Path]` in `dm_engine/state/sheets.py`. Sheets are written to `store.root / "sheets" / f"{name.lower().replace(' ', '-')}.md"` for every party() member. Player-visible only: name, role, class/race/level, XP + XP-to-next (via `xp_to_next_level`), abilities with modifiers, AC/speed, HP `current/max` (+temp), hit dice, spell slots remaining/max per level, conditions & exhaustion, death-save pips when dying, proficiencies, attacks (name + to-hit + damage), spells known, inventory. NO `gm_only` material exists in these tables, so render everything. Markdown format: h1 name, h2 sections; keep it stable (tests assert substrings, not exact layout).
- CLI: `dm sheet <character> --campaign <slug> [--campaigns-dir PATH]` prints the rendered markdown (reads the DB directly through the store; no registry needed for a read).

- Binding tests:

```python
# tests/commands/test_characters.py
from dm_engine.commands import registry

FIGHTER_KWARGS = dict(
    name="Kira", role="pc", class_slug="fighter", race_slug="human",
    abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
    ac=16, proficiencies={"skills": ["athletics", "intimidation"], "saves": ["str", "con"]},
    attacks=[{"name": "longsword", "ranged": False, "range_ft": 5, "long_range_ft": None,
              "damage": "1d8", "damage_type": "slashing", "ability": "str",
              "proficient": True}],
)


def test_create_character_derives_hp_and_slots(ctx):
    result = registry.execute("create_character", ctx, **FIGHTER_KWARGS)
    assert result.ok, result.refusal
    char = ctx.store.get_character("Kira")
    assert char["max_hp"] == 12  # d10 fighter, +2 CON
    result2 = registry.execute(
        "create_character", ctx, name="Brother Aldric", role="companion",
        class_slug="cleric", race_slug="hill-dwarf",
        abilities={"str": 14, "dex": 8, "con": 15, "int": 10, "wis": 15, "cha": 12},
        ac=18, proficiencies={"skills": ["medicine", "religion"], "saves": ["wis", "cha"]},
        attacks=[{"name": "mace", "ranged": False, "range_ft": 5, "long_range_ft": None,
                  "damage": "1d6", "damage_type": "bludgeoning", "ability": "str",
                  "proficient": True}],
        spells_known=["cure-wounds", "bless", "guiding-bolt", "sacred-flame"],
    )
    assert result2.ok, result2.refusal
    cleric = ctx.store.get_character("Brother Aldric")
    res = ctx.store.get_resources(cleric["id"])
    assert res["spell_slots"] == {"1": {"max": 2, "remaining": 2}}


def test_second_pc_refused(ctx):
    registry.execute("create_character", ctx, **FIGHTER_KWARGS)
    dupe = {**FIGHTER_KWARGS, "name": "Zed"}
    result = registry.execute("create_character", ctx, **dupe)
    assert result.ok is False and "pc" in result.refusal.lower()


def test_award_xp_levels_up_and_updates_sheet(ctx):
    registry.execute("create_character", ctx, **FIGHTER_KWARGS)
    result = registry.execute("award_xp", ctx, amount=300, reason="quest: rats")
    assert result.ok
    char = ctx.store.get_character("Kira")
    assert char["level"] == 2 and char["xp"] == 300
    assert char["max_hp"] == 20
    assert "level 2" in result.digest.lower() or "reaches level 2" in result.digest
    sheet = (ctx.store.root / "sheets" / "kira.md").read_text()
    assert "20" in sheet and "300" in sheet
```

```python
# tests/state/test_sheets.py
from dm_engine.commands import registry
from dm_engine.state.sheets import render_character_sheet


def test_sheet_renders_core_fields(ctx):
    registry.execute(
        "create_character", ctx, name="Kira", role="pc", class_slug="fighter",
        race_slug="human",
        abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        ac=16, proficiencies={"skills": ["athletics"], "saves": ["str", "con"]},
        attacks=[{"name": "longsword", "ranged": False, "range_ft": 5,
                  "long_range_ft": None, "damage": "1d8", "damage_type": "slashing",
                  "ability": "str", "proficient": True}],
    )
    md = render_character_sheet(ctx.store, ctx.store.get_character("Kira")["id"])
    for expected in ("# Kira", "fighter", "12 / 12", "AC", "16", "longsword", "+5"):
        assert expected in md  # +5 = STR +3 and proficiency +2
    files = list((ctx.store.root / "sheets").glob("*.md"))
    assert len(files) == 1  # registry hook already materialized it
```

Note: `ctx` fixture comes from `tests/commands/conftest.py`; give `tests/state/test_sheets.py` access by importing the fixture module in `tests/state/conftest.py` (`from tests.commands.conftest import ctx, rules_path  # noqa: F401`) or by moving the fixture to the root conftest — implementer's choice, documented in the report.

- [ ] Steps: binding tests + own tests (RED) → implement `characters.py` + real `sheets.py` + CLI `dm sheet` → GREEN → full suite + ruff → commit `feat: add character commands and sheets`.

---

### Task 6: Check commands

**Files:**
- Create: `src/dm_engine/commands/checks.py`
- Modify: `src/dm_engine/commands/__init__.py`
- Test: `tests/commands/test_checks.py`

**Interfaces:**
- Consumes: M2 `checks.resolve_check`/`ability_modifier`/`proficiency_bonus`/`combine_advantage`, `death.apply_death_save`, store, registry.
- Produces — command contracts (frozen):
  - `SKILL_ABILITIES: dict[str, str]` — all 18 RAW skills: acrobatics/dex, animal-handling/wis, arcana/int, athletics/str, deception/cha, history/int, insight/wis, intimidation/cha, investigation/int, medicine/wis, nature/int, perception/wis, performance/cha, persuasion/cha, religion/int, sleight-of-hand/dex, stealth/dex, survival/wis.
  - `skill_check(character: str, skill: str, dc: int, advantage: bool = False, disadvantage: bool = False, player_value: int | None = None, gm_only: bool = False)` — refusals: unknown character; unknown skill; dc < 1; player_value not in 1–20; `player_value` supplied for a non-PC (companions/monsters are engine-rolled). Modifier = ability mod + proficiency (if skill in character's proficiencies.skills). Mode via `combine_advantage`. data = `{"skill", "modifier", "dc", "natural", "total", "success", "margin"}`. digest e.g. `"Kira Athletics check: 17 vs DC 15 — success"`. Result `gm_only` mirrors the kwarg (hidden DM checks).
  - `saving_throw(character: str, ability: str, dc: int, ...same flags...)` — proficiency from proficiencies.saves; auto-fail str/dex saves when `effects_for(conditions).auto_fail_str_dex_saves` (no dice rolled; data notes `"auto_fail": true`); disadvantage merged in from conditions (`saves_have_disadvantage`, and `dex_saves_have_disadvantage` for dex).
  - `death_save(character: str, player_value: int | None = None)` — refusals: character not at 0 HP/dying (i.e. hp > 0, or already stable/dead/defeated). PC may pass player_value (1–20); companions engine-rolled. Applies `apply_death_save` to resources.death_saves; on `regained_hp` sets hp=1 and clears unconscious (stays prone); on third failure sets character status per campaign death mode (`defeated`/`dead`) and, in combat, marks the combatant defeated. data = death-save state + event name.

- Binding tests:

```python
# tests/commands/test_checks.py
import pytest

from dm_engine.commands import registry

pytestmark = pytest.mark.usefixtures("party")  # implementer adds a fixture creating Kira (PC) + Brother Aldric (companion) via registry


def test_skill_check_applies_proficiency(ctx):
    result = registry.execute("skill_check", ctx, character="Kira",
                              skill="athletics", dc=10, player_value=12)
    assert result.ok
    assert result.data["modifier"] == 5  # STR +3, prof +2
    assert result.data["total"] == 17 and result.data["success"] is True


def test_player_value_refused_for_companion(ctx):
    result = registry.execute("skill_check", ctx, character="Brother Aldric",
                              skill="medicine", dc=10, player_value=12)
    assert result.ok is False and "player" in result.refusal.lower()


def test_player_value_out_of_range_refused(ctx):
    result = registry.execute("skill_check", ctx, character="Kira",
                              skill="athletics", dc=10, player_value=21)
    assert result.ok is False


def test_gm_only_stealth_check_flags_everything(ctx):
    result = registry.execute("skill_check", ctx, character="Brother Aldric",
                              skill="stealth", dc=12, gm_only=True)
    assert result.ok and result.gm_only is True
    row = ctx.store.conn.execute(
        "SELECT rolls FROM event_log ORDER BY id DESC LIMIT 1").fetchone()
    assert '"gm_only": true' in row["rolls"]


def test_death_save_sequence_narrative_defeat(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 0 WHERE character_id = ?",
                           (kira["id"],))
    ctx.store.conn.commit()
    registry.execute("death_save", ctx, character="Kira", player_value=9)   # fail 1
    registry.execute("death_save", ctx, character="Kira", player_value=8)   # fail 2
    result = registry.execute("death_save", ctx, character="Kira", player_value=2)
    assert result.ok
    assert ctx.store.get_character("Kira")["status"] == "defeated"  # narrative mode


def test_death_save_nat20_recovers(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 0 WHERE character_id = ?",
                           (kira["id"],))
    ctx.store.conn.commit()
    result = registry.execute("death_save", ctx, character="Kira", player_value=20)
    assert result.ok
    assert ctx.store.get_resources(kira["id"])["hp"] == 1
```

- [ ] Steps: binding tests + own tests (RED) → implement → GREEN → full suite + ruff → commit `feat: add check and save commands`.

---

### Task 7: Combat core

**Files:**
- Create: `src/dm_engine/commands/combat.py`
- Modify: `src/dm_engine/commands/__init__.py`
- Test: `tests/commands/test_combat.py`

**Interfaces:**
- Consumes: M2 `initiative.roll_initiative`, `encounters.assess_encounter`, `action_economy` (new_turn/spend/spend_movement/dash), `bands` (movement_cost_ft, provokes_opportunity_attacks, distance_band), `checks.ability_modifier`; store combat accessors; `Combatant` model; `RulesDB.get_monster`.
- Produces — command contracts (frozen):

| command | kwargs | behavior |
|---|---|---|
| `start_combat` | `monsters: list[dict]` (each `{"slug": str, "count": int = 1, "band": Band = "near"}`), `pc_initiative: int \| None = None` (player-reported natural), `surprise: list[str] = []` (combatant keys that are surprised) | Refusals: combat already active; empty monsters; unknown slug. Builds combatants: every `active` party member (band "near", hp from resources) + monster instances keyed `slug-N` with hp = record hit_points, ac from record, dex mod via `ability_modifier(record.dexterity)`, xp from record. Initiative via `roll_initiative` (PC uses pc_initiative when given; others engine-rolled — monster initiative rolls are `gm_only`). Sets round=1, turn_index=0, budget for the first combatant (`new_turn(speed)` — monster speed parsed from record walk speed, default 30). Surprised combatants get `conditions += ["surprised"]`?? — NO: surprise is v1-simplified as `budget = None` on their first turn (data notes it); do not add fake conditions. data = `{"order": [{key, initiative, kind}], "round": 1, "active": key_of_first, "encounter": assess_encounter(...) fields}` — the advisory difficulty is ALWAYS computed and included (FC-7); log-only, never refuses. digest e.g. `"Combat! 2 goblins ambush the party — initiative: goblin-1 17, Kira 15, … (deadly, 200 adj. XP)"`. |
| `next_turn` | *(none)* | Refuse if no active combat. Advances turn_index (wrapping to round+1), skips `defeated` combatants, resets the new actor's budget via `new_turn(speed)` (characters: speed × conditions `speed_multiplier`, 0 if `can_move` false → movement_remaining 0 but budget still granted), clears `surprised-skip` after round 1. data = `{"round", "active": key, "kind", "budget": {...}}`. digest `"Round 2 — goblin-2's turn"`. |
| `move` | `combatant: str`, `to_band: Band`, `disengage: bool = False` | Refusals: no combat; not this combatant's turn; unknown band; conditions forbid movement (`can_move` false); insufficient movement (`movement_cost_ft` vs budget.movement_remaining — Dash first via `action: "dash"` param? No: separate `dash: bool = False` kwarg spends the action for extra movement before the cost check); disengage requires an available action (spends it). Movement: leaves `engaged_with` (both directions) when departing engaged; `provokes_opportunity_attacks` computes provokers when not disengaged — the move SUCCEEDS and data lists `{"opportunity_attacks_from": [keys]}` for the DM to resolve as reaction `attack` commands. Updates band + budget. |
| `engage` | `combatant: str`, `target: str` | Moves combatant into melee with target: both must be within reach after movement — v1 rule: combatant must be able to afford `movement_cost_ft(band, target_band)`; sets both bands to target's band, adds each to the other's `engaged_with`. Spends movement. Refuse when not their turn / cannot move / cannot afford. |
| `end_combat` | *(none)* | Refuse if no active combat. Sums `xp` of defeated monsters + `encounter_xp` accumulator, divides evenly among active party (floor), applies xp + level-ups via the same helper `characters.award_party_xp(ctx, total, reason)` used by award_xp (refactor it to share). Clears combat_state (active=0, combatants=[], round=0), clears `surprised` markers. data = `{"xp_awarded", "per_member", "recipients": [...], "defeated": [keys]}`. Works when all monsters dead or when the DM ends early (fled/negotiated — XP still awarded for defeated only; non-combat resolution XP goes through `award_xp`). |
| `get_scene_state` | *(none)* | Read-only. data = `{"clock", "location", "scene", "combat": null or {"round", "turn_index", "active": key, "order": [full combatant dumps — characters get live hp/conditions merged from tables], "budgets"}}`. This is the resume-rehydration payload for mid-combat crashes. |

- Binding tests:

```python
# tests/commands/test_combat.py
import pytest

from dm_engine.commands import registry

pytestmark = pytest.mark.usefixtures("party")  # Kira PC + Brother Aldric companion


def _start(ctx, **over):
    kwargs = dict(monsters=[{"slug": "goblin", "count": 2, "band": "near"}],
                  pc_initiative=15)
    kwargs.update(over)
    return registry.execute("start_combat", ctx, **kwargs)


def test_start_combat_builds_order_and_reports_difficulty(ctx):
    result = _start(ctx)
    assert result.ok, result.refusal
    order = result.data["order"]
    assert {o["key"] for o in order} == {"Kira", "Brother Aldric", "goblin-1", "goblin-2"}
    totals = [o["initiative"] for o in order]
    assert totals == sorted(totals, reverse=True)
    assert result.data["encounter"]["difficulty"] in ("easy", "medium", "hard", "deadly", "trivial")
    assert result.data["encounter"]["adjusted_xp"] > 0


def test_start_combat_twice_refused(ctx):
    _start(ctx)
    assert _start(ctx).ok is False


def test_monster_initiative_is_gm_only(ctx):
    _start(ctx)
    row = ctx.store.conn.execute(
        "SELECT rolls FROM event_log ORDER BY id DESC LIMIT 1").fetchone()
    assert '"gm_only": true' in row["rolls"]


def test_move_out_of_engaged_provokes(ctx):
    _start(ctx)
    combat = ctx.store.combat()
    # force a known setup: it's Kira's turn, engaged with goblin-1
    for c in combat["combatants"]:
        if c["key"] == "Kira":
            c["band"] = "engaged"; c["engaged_with"] = ["goblin-1"]
        if c["key"] == "goblin-1":
            c["band"] = "engaged"; c["engaged_with"] = ["Kira"]
    turn_index = next(i for i, c in enumerate(combat["combatants"]) if c["key"] == "Kira")
    ctx.store.update_combat(combatants=combat["combatants"], turn_index=turn_index)
    ctx.store.conn.commit()
    result = registry.execute("move", ctx, combatant="Kira", to_band="near")
    assert result.ok
    assert result.data["opportunity_attacks_from"] == ["goblin-1"]

    # disengage path: no OA (fresh turn needed for the action)
    registry.execute("next_turn", ctx)  # give the turn away and come back around
    # (implementer: also cover disengage in an isolated test with a fresh setup)


def test_next_turn_advances_and_resets_budget(ctx):
    _start(ctx)
    first = ctx.store.combat()
    result = registry.execute("next_turn", ctx)
    assert result.ok
    after = ctx.store.combat()
    assert after["turn_index"] == (first["turn_index"] + 1) % len(first["combatants"])
    assert result.data["budget"]["action_available"] is True


def test_end_combat_awards_xp_for_defeated(ctx):
    _start(ctx)
    combat = ctx.store.combat()
    for c in combat["combatants"]:
        if c["kind"] == "monster":
            c["defeated"] = True; c["hp"] = 0
    ctx.store.update_combat(combatants=combat["combatants"])
    ctx.store.conn.commit()
    result = registry.execute("end_combat", ctx)
    assert result.ok
    assert result.data["xp_awarded"] == 100  # 2 goblins x 50
    assert result.data["per_member"] == 50
    assert ctx.store.get_character("Kira")["xp"] == 50
    assert ctx.store.combat()["active"] == 0


def test_scene_state_rehydrates_combat(ctx):
    _start(ctx)
    state = registry.execute("get_scene_state", ctx)
    assert state.ok
    combat = state.data["combat"]
    assert combat["round"] == 1 and len(combat["order"]) == 4
    kira = next(c for c in combat["order"] if c["key"] == "Kira")
    assert kira["hp"] == 12  # merged live from resources
```

- [ ] Steps: binding tests + own per-command tests (RED) → implement → GREEN → full suite + ruff → commit `feat: add combat core commands`.

---

### Task 8: Attack & conditions

**Files:**
- Create: `src/dm_engine/commands/attacks.py`
- Modify: `src/dm_engine/commands/__init__.py`
- Test: `tests/commands/test_attacks.py`

**Interfaces:**
- Consumes: M2 `attacks.resolve_attack_roll`/`roll_damage`, `damage.apply_mitigation`, `conditions.effects_for`/`attack_interaction`/`CONDITIONS`, `checks.ability_modifier`/`proficiency_bonus`/`combine_advantage`, `bands.distance_band`/`weapon_range_legality`, `death.apply_damage_while_dying`, `action_economy.spend`; combat state (Task 7 layout); `RulesDB.get_monster`.
- Produces — command contracts (frozen):

**`attack(attacker: str, target: str, attack_name: str, spend: str = "action", player_attack_value: int | None = None, player_damage_value: int | None = None, advantage: bool = False, disadvantage: bool = False)`**

Validation order (each failure = refusal):
1. combat active; attacker & target exist and not defeated.
2. `spend` in `("action", "reaction", "none")`. `"action"` requires it to be the attacker's turn AND `budget.action_available` (spends it). `"reaction"` requires `budget.reaction_available` on the attacker's stored budget if it is their turn, otherwise reactions off-turn are allowed once per round — v1 simplification: track `reaction_used: bool` on the Combatant model (add the field, default False; reset by next_turn at the top of each round for all combatants). `"none"` skips economy (Extra Attack / Multiattack follow-ups; digest notes it).
3. Attack spec resolution: characters — `attack_name` must match an entry in the character's `attacks` JSON (refusal lists available names); attack_bonus = ability mod (+ proficiency_bonus(level) if proficient), damage notation = `f"{spec['damage']}{sign}{ability_mod}"` (ability mod added to damage per RAW; if spec damage already has a modifier keep both — v1: specs store bare dice like "1d8", engine appends the ability mod). Monsters — `attack_name` must match a record action with `attack_bonus` and `damage[0].damage_dice`/`damage_type`; ranged + range parsed: `"range (\d+)/(\d+) ft"` in desc → ranged with those ranges; `"reach (\d+) ft"` → melee with that reach. Monster attack + damage rolls are engine-rolled, `gm_only=False` (results are public once they happen).
4. `player_attack_value`/`player_damage_value` only for PC attackers (1–20 for attack; damage ≥ 0).
5. Range legality: `engaged = target.key in attacker.engaged_with`; `dist = distance_band(attacker.band, target.band, mutually_engaged=engaged)`; `weapon_range_legality(dist, range_ft, long_range_ft, ranged=spec_ranged, attacker_engaged=bool(attacker.engaged_with))` — `out_of_range` → refusal naming the distance and weapon (e.g. `"dagger (5 ft) cannot reach a target at far"`); `disadvantage` → merged in as a disadvantage source.
6. Advantage math: `attack_interaction(effects_for(attacker_conditions, attacker_exhaustion), effects_for(target_conditions, target_exhaustion), engaged=engaged)` — its mode contributes; plus kwargs advantage/disadvantage; plus range disadvantage. Merge = `combine_advantage(any_advantage_source, any_disadvantage_source)`.
7. Resolve: `resolve_attack_roll(...)`; crit = `result.critical_hit or (result.hit and interaction.auto_crit_on_hit)` (M2 handoff). On hit: `roll_damage(notation, critical=crit, player_value=player_damage_value)` then `apply_mitigation(total, damage_type, resistances/vulnerabilities/immunities from monster record (fields `damage_resistances` etc., lowercased type match) or petrified `resist_all_damage` for characters → resistances={damage_type})`.
8. Apply damage: monster target — hp −= final; at ≤ 0 → `defeated=True`, hp 0, removed from every `engaged_with`, add its `xp` to `combat_state.encounter_xp` accumulator… (already carried on the combatant; end_combat sums defeated). Character target — resources.hp −= final; if damage ≥ hp before hit AND overflow (final − hp_before) ≥ max_hp → instant death (status per death mode, death_saves dead); else at 0 → hp 0, conditions += unconscious (which implies prone via effects), death_saves reset to fresh dying state; if ALREADY at 0 (dying) → `apply_damage_while_dying(state, final, max_hp, critical=crit)` (melee within 5 ft of an unconscious target already auto-crits via step 7). Concentration: a concentrating character who takes damage gets `data["concentration_check"] = {"dc": concentration_save_dc(final)}` — the DM must follow up with `saving_throw` and, on failure, `remove_condition`-style cleanup via `break_concentration` (see apply_condition section below); the engine does not auto-roll it (PC dice belong to the player).
9. data = `{"attack_roll": {natural, total, mode, target_ac}, "hit", "critical", "damage": {"raw", "final", "type", "applied"} | None, "target": {"key", "hp", "status"?}, "opportunity"?, "concentration_check"?}`. digest e.g. `"Goblin 1 hits Kira for 5 slashing (17 vs AC 15)"` / `"Kira crits goblin-2 for 13 slashing — it drops!"`.

**`apply_condition(target: str, condition: str, source: str = "", exhaustion_delta: int = 0)`** — target = combatant key in combat, else character name out of combat. Refusals: unknown condition (must be in `CONDITIONS`); unknown target; exhaustion handled via `exhaustion_delta` (±1..6 clamp 0–6, condition name must be "exhaustion" then). Monsters: condition appended to combatant conditions. Characters: appended to resources.conditions (idempotent — applying twice is a refusal "already X"). If the condition incapacitates (`effects_for` → `can_take_actions` False) and target concentrating → concentration broken automatically (data notes it).

**`remove_condition(target: str, condition: str)`** — inverse; refuse if not present.

**`break_concentration(character: str)`** — clears resources.concentration; refuse if not concentrating. (The DM calls this after a failed concentration save; cast_spell also calls the same internal helper when a new concentration spell replaces an old one.)

- Binding tests:

```python
# tests/commands/test_attacks.py
import pytest

from dm_engine.commands import registry

pytestmark = pytest.mark.usefixtures("party")


@pytest.fixture()
def combat(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 2, "band": "near"}],
                     pc_initiative=15)
    return ctx.store.combat()


def _force_turn(ctx, key, *, engaged_with=None, band=None):
    combat = ctx.store.combat()
    for c in combat["combatants"]:
        if c["key"] == key:
            if band: c["band"] = band
            if engaged_with is not None: c["engaged_with"] = engaged_with
            c["budget"] = {"speed": 30, "movement_remaining": 30,
                           "action_available": True, "bonus_action_available": True,
                           "reaction_available": True}
    idx = next(i for i, c in enumerate(combat["combatants"]) if c["key"] == key)
    ctx.store.update_combat(combatants=combat["combatants"], turn_index=idx)
    ctx.store.conn.commit()


def test_melee_refused_from_near(ctx, combat):
    _force_turn(ctx, "Kira", band="near")
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="longsword")
    assert result.ok is False and "reach" in result.refusal.lower()


def test_player_supplied_attack_hits_and_damages(ctx, combat):
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    combatants = ctx.store.combat()["combatants"]
    for c in combatants:
        if c["key"] == "goblin-1":
            c["band"] = "engaged"; c["engaged_with"] = ["Kira"]
    ctx.store.update_combat(combatants=combatants); ctx.store.conn.commit()
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="longsword",
                              player_attack_value=15, player_damage_value=6)
    assert result.ok, result.refusal
    assert result.data["hit"] is True          # 15 + 5 = 20 vs AC 15
    assert result.data["damage"]["final"] == 9  # 6 + STR 3
    goblin = next(c for c in ctx.store.combat()["combatants"] if c["key"] == "goblin-1")
    assert goblin["defeated"] is True and goblin["hp"] == 0  # 9 dmg vs 7 hp
    row = ctx.store.conn.execute(
        "SELECT rolls FROM event_log ORDER BY id DESC LIMIT 1").fetchone()
    assert '"player_supplied": true' in row["rolls"]


def test_attack_consumes_action_and_second_refused(ctx, combat):
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                     attack_name="longsword", player_attack_value=2,
                     player_damage_value=1)
    second = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="longsword", player_attack_value=15,
                              player_damage_value=6)
    assert second.ok is False and "action" in second.refusal.lower()


def test_monster_attack_drops_pc_to_dying(ctx, combat):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 1 WHERE character_id = ?",
                           (kira["id"],))
    ctx.store.conn.commit()
    _force_turn(ctx, "goblin-1", band="engaged", engaged_with=["Kira"])
    combatants = ctx.store.combat()["combatants"]
    for c in combatants:
        if c["key"] == "Kira":
            c["band"] = "engaged"; c["engaged_with"] = ["goblin-1"]
    ctx.store.update_combat(combatants=combatants); ctx.store.conn.commit()
    # engine-rolled monster attack; loop until a hit lands (seeded, deterministic)
    for _ in range(10):
        result = registry.execute("attack", ctx, attacker="goblin-1", target="Kira",
                                  attack_name="Scimitar", spend="none")
        if result.ok and result.data["hit"]:
            break
    res = ctx.store.get_resources(kira["id"])
    assert res["hp"] == 0
    assert "unconscious" in res["conditions"]
    assert res["death_saves"]["failures"] == 0


def test_apply_condition_validates_and_breaks_concentration(ctx):
    aldric = ctx.store.get_character("Brother Aldric")
    ctx.store.conn.execute(
        "UPDATE resources SET concentration = '{\"spell\": \"bless\"}'"
        " WHERE character_id = ?", (aldric["id"],))
    ctx.store.conn.commit()
    bad = registry.execute("apply_condition", ctx, target="Brother Aldric",
                           condition="sleepy")
    assert bad.ok is False
    result = registry.execute("apply_condition", ctx, target="Brother Aldric",
                              condition="stunned")
    assert result.ok
    assert ctx.store.get_resources(aldric["id"])["concentration"] is None
    assert result.data.get("concentration_broken") is True
```

- [ ] Steps: binding tests + own tests (attack refusal matrix, OA reaction spend, crit path via player_value=20, mitigation vs a resistant monster — pick one from the DB, e.g. check a monster with damage_resistances in its record) (RED) → implement → GREEN → full suite + ruff → commit `feat: add attack and condition commands`.

---

### Task 9: Spells, rest, items

**Files:**
- Create: `src/dm_engine/commands/spells.py`, `src/dm_engine/commands/resources.py`
- Modify: `src/dm_engine/commands/__init__.py`
- Test: `tests/commands/test_spells.py`, `tests/commands/test_resources.py`

**Interfaces:**
- Consumes: `RulesDB.get_spell` (full record via `model_extra`), M2 `attacks.resolve_attack_roll`/`roll_damage`, `damage.apply_mitigation`, `checks.resolve_check`/`ability_modifier`/`proficiency_bonus`, `concentration.concentration_save_dc`, `rests.spend_hit_dice`/`long_rest`/`HitDicePool`, `bands.aoe_targets`, combat/attack helpers (Task 7/8: turn+budget spending, damage application to monsters/characters — refactor shared damage-application into `attacks.apply_damage_to_target(ctx, key, amount, damage_type, *, critical) -> dict` and reuse).
- Produces — command contracts (frozen):

**`SPELLCASTING_ABILITY = {"bard": "cha", "cleric": "wis", "druid": "wis", "paladin": "cha", "ranger": "wis", "sorcerer": "cha", "warlock": "cha", "wizard": "int"}`**

**AoE cluster cap:** `max_targets = max(1, aoe_size_ft // 5)`, capped at 8 (burning hands 15-ft cone → 3; fireball 20-ft sphere → 4). Document in the module docstring; this is the FC-4 `data.max_targets` rule for v1.

**`cast_spell(caster: str, spell_slug: str, slot_level: int | None = None, targets: list[str] = [], band: str | None = None, spend: str = "action", player_attack_value: int | None = None, player_damage_value: int | None = None, player_save_values: dict[str, int] | None = None)`**

Validation order:
1. caster exists (character; monsters cast via dm_ruling in v1); spell exists in rules DB; spell in caster's `spells_known` (cantrips too).
2. Level ≥ 1 spells: `slot_level = slot_level or spell.level`; refuse if `slot_level < spell.level`; refuse if no remaining slot at slot_level — refusal text exactly like FC-1's example style: `"<name> has no <n>-level slots remaining"` (e.g. "Brother Aldric has no 2nd-level slots remaining" — ordinal formatting helper). Cantrips ignore slots.
3. In combat: `spend` economy exactly as attack's (action/reaction/none + bonus-action support via `spend="bonus_action"`).
4. Consume the slot (decrement remaining).
5. Concentration: if record.concentration — break caster's existing concentration (data notes replaced spell), set `resources.concentration = {"spell": slug, "since_event": <next event id unknown → store round/day instead: {"spell", "day", "minutes", "duration": record.duration}}`.
6. **Tier 1** (record has `damage` or `heal_at_slot_level`): resolve mechanically:
   - Heal (`heal_at_slot_level`): dice = entry for slot_level with `"MOD"` replaced by spellcasting ability mod (e.g. "1d8 + MOD" at WIS 15 → roll "1d8+2"). One target (first of `targets`, must be party/combatant). player_value allowed for PC caster. HP capped at max; healing a dying character from 0: fresh death_saves, remove unconscious, hp = healed amount.
   - Damage with `attack_type` (spell attack): attack bonus = prof + ability mod; resolve vs target AC like attack (adv/dis from conditions interaction); damage dice from `damage_at_slot_level[slot_level]` or `damage_at_character_level` (highest key ≤ caster level); crit doubles.
   - Damage with `dc` (save spell): DC = 8 + prof + ability mod. Targets: explicit `targets` list (each must be in the stated `band` when AoE — validate ≤ max_targets, all same band) or auto-cluster `aoe_targets(positions, band, max_targets)` when `targets` empty and record has `area_of_effect`. Each target saves (engine-rolled for monsters — NOT gm_only, saves are public; `player_save_values` for character targets); `dc_success == "half"` → half damage (floor) on success, `"none"` → zero. Damage rolled ONCE, applied per-target with per-target mitigation.
   - data = `{"tier": 1, "slot_used", "effect": "damage"|"heal", "per_target": [{key, save?, damage|healed, hp}], "concentration"?}`.
7. **Tier 2** (no damage/heal fields): slot consumed, concentration/duration set as above, NO mechanical effect. data = `{"tier": 2, "needs_ruling": True, "spell_text": record.desc, "duration": record.duration}`; digest `"Hold Person cast (2nd-level slot) — resolve effect via dm_ruling"`. ok=True.
8. Out of combat casting: allowed (no economy); useful for cure-wounds after a fight.

**`rest(kind: str, hit_dice: dict[str, int] | None = None, player_hit_die_values: list[int] | None = None)`** (resources.py)
- Refuse: unknown kind (`"short"`/`"long"`); combat active.
- Short: `hit_dice` maps character name → dice count; each character's spend runs `spend_hit_dice` (PC may pass `player_hit_die_values` — applies to the PC's dice only, in order); heal applied (cap max_hp); pool decrements persist. Refusals from over-spends surface as refusal (validate all before rolling any).
- Long: every active character: hp → max, hit dice regain via `long_rest` (pool from level + hit_dice_remaining), slots restored to max, exhaustion −1, concentration cleared, death_saves reset; world clock +8h (advance day on overflow). Defeated/dead characters untouched.
- data per character: healed/regained amounts. digest e.g. `"Long rest — the party wakes on day 4 fully restored"`.

**`use_item(character: str, item: str, heal: str | None = None)`** — refuse if not held (store.remove_item returns False → refusal, nothing consumed). Consumes 1. If `heal` notation given (e.g. "2d4+2" for a healing potion) the engine rolls it (player_value NOT accepted — potion dice are engine dice in v1) and applies healing (same helper as cure wounds). Otherwise data = `{"needs_ruling": True}` and the digest says to resolve via dm_ruling.

**`add_item(character: str, item: str, quantity: int = 1, notes: str | None = None)`** / **`remove_item(character: str, item: str, quantity: int = 1)`** — thin wrappers over the store; refusals for unknown character / not enough held.

- Binding tests:

```python
# tests/commands/test_spells.py
import pytest

from dm_engine.commands import registry

pytestmark = pytest.mark.usefixtures("party")  # Aldric knows cure-wounds, bless, guiding-bolt, sacred-flame, burning-hands (add burning-hands + hold-person to his spells_known in the fixture for these tests)


def test_cure_wounds_heals_and_consumes_slot(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 3 WHERE character_id = ?",
                           (kira["id"],))
    ctx.store.conn.commit()
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="cure-wounds", targets=["Kira"])
    assert result.ok, result.refusal
    assert result.data["tier"] == 1 and result.data["effect"] == "heal"
    healed = result.data["per_target"][0]["healed"]
    assert 3 <= healed <= 10  # 1d8 + WIS 2
    aldric = ctx.store.get_character("Brother Aldric")
    slots = ctx.store.get_resources(aldric["id"])["spell_slots"]
    assert slots["1"]["remaining"] == slots["1"]["max"] - 1


def test_no_slots_left_is_a_structured_refusal(ctx):
    aldric = ctx.store.get_character("Brother Aldric")
    res = ctx.store.get_resources(aldric["id"])
    slots = res["spell_slots"]; slots["1"]["remaining"] = 0
    ctx.store.update_resources(aldric["id"], spell_slots=slots)
    ctx.store.conn.commit()
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="cure-wounds", targets=["Kira"])
    assert result.ok is False
    assert "1st-level slots remaining" in result.refusal


def test_burning_hands_clusters_and_save_halves(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 3, "band": "near"}],
                     pc_initiative=15)
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="burning-hands", band="near", spend="none")
    assert result.ok, result.refusal
    per = result.data["per_target"]
    assert len(per) == 3  # 15-ft cone -> max 3 targets, all 3 goblins in band
    for entry in per:
        assert entry["save"]["dc"] == 8 + 2 + 2  # prof 2 + WIS 2
        if entry["save"]["success"]:
            assert entry["damage"] == entry["damage_rolled"] // 2


def test_tier2_spell_directs_to_dm_ruling(ctx):
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="hold-person", targets=[])
    assert result.ok
    assert result.data["needs_ruling"] is True and result.data["tier"] == 2
    aldric = ctx.store.get_character("Brother Aldric")
    res = ctx.store.get_resources(aldric["id"])
    assert res["concentration"]["spell"] == "hold-person"
    assert res["spell_slots"]["2"]["remaining"] == res["spell_slots"]["2"]["max"] - 1
```

Note: the `party` fixture must create Brother Aldric at level 3 (cleric — has 2nd-level slots) OR give him `xp` for level 3 via award_xp so hold-person is castable; implementer picks the cleaner setup and keeps `test_cure_wounds` expectations consistent (level 3 cleric slots: {1: 4, 2: 2}; adjust the slot assertions accordingly — the binding assertions above use max-relative checks except burning-hands' DC which uses prof 2, valid at levels 1–4).

```python
# tests/commands/test_resources.py
import pytest

from dm_engine.commands import registry

pytestmark = pytest.mark.usefixtures("party")


def test_long_rest_restores_everything_and_advances_clock(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute(
        "UPDATE resources SET hp = 2, exhaustion = 2 WHERE character_id = ?",
        (kira["id"],))
    ctx.store.conn.commit()
    before = ctx.store.world_clock()
    result = registry.execute("rest", ctx, kind="long")
    assert result.ok
    res = ctx.store.get_resources(kira["id"])
    assert res["hp"] == kira["max_hp"] and res["exhaustion"] == 1
    after = ctx.store.world_clock()
    assert (after["day"], after["minutes"]) != (before["day"], before["minutes"])


def test_short_rest_spends_hit_dice_with_player_values(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 4 WHERE character_id = ?",
                           (kira["id"],))
    ctx.store.conn.commit()
    result = registry.execute("rest", ctx, kind="short", hit_dice={"Kira": 1},
                              player_hit_die_values=[8])
    assert result.ok
    res = ctx.store.get_resources(kira["id"])
    assert res["hp"] == 4 + 8 + 2  # roll 8 + CON 2
    assert res["hit_dice_remaining"] == 0


def test_use_item_requires_holding_it(ctx):
    result = registry.execute("use_item", ctx, character="Kira", item="healing potion")
    assert result.ok is False
    registry.execute("add_item", ctx, character="Kira", item="healing potion",
                     quantity=2)
    result = registry.execute("use_item", ctx, character="Kira",
                              item="healing potion", heal="2d4+2")
    assert result.ok
    assert ctx.store.items_for(ctx.store.get_character("Kira")["id"])[0]["quantity"] == 1
```

- [ ] Steps: binding tests + own tests (upcast damage at higher slot, spell-attack cantrip fire-bolt vs monster AC, concentration replacement, rest-during-combat refusal) (RED) → implement → GREEN → full suite + ruff → commit `feat: add spells, rest, and item commands`.

---

### Task 10: Queries, dm_ruling, CLI

**Files:**
- Create: `src/dm_engine/commands/queries.py`, `src/dm_engine/commands/rulings.py`
- Modify: `src/dm_engine/commands/__init__.py`, `src/dm_engine/cli/app.py` (add `dm audit`, `dm cmd`)
- Test: `tests/commands/test_queries.py`, `tests/commands/test_rulings.py`, extend `tests/test_cli.py`

**Interfaces:**
- Consumes: `RulesDB.lookup_rule/get_monster/search_monsters/get_spell/search_spells`, store, registry, `open_campaign_context`.
- Produces — command contracts (frozen):
  - `lookup_rule(query: str, limit: int = 5)` — data = `{"hits": [{source, heading_path, heading, snippet}]}`; read-only; never refuses (empty hits is ok=True).
  - `lookup_monster(slug: str)` — refuse unknown; data = the full raw record (`model_extra` merged — return the record as stored: `ctx.rules.get_monster(slug).model_dump(by_alias=True)`); `gm_only=True` (stat blocks are DM screen material).
  - `lookup_spell(slug: str)` — refuse unknown; data = full record dump.
  - `dm_ruling(description: str, rationale: str, effects: list[dict] = [], gm_only: bool = False)` — refuse when `rationale.strip()` empty (FC-7: mandatory rationale) or `description.strip()` empty. `effects` op language, each op applied in order, all-or-nothing (any invalid op = refusal, nothing applied):
    - `{"op": "adjust_hp", "target": <key/name>, "delta": int}` (clamped 0..max for characters; monsters clamped 0.., defeat at 0)
    - `{"op": "set_condition", "target", "condition"}` / `{"op": "clear_condition", "target", "condition"}` (validated vs CONDITIONS)
    - `{"op": "adjust_slot", "character", "slot_level": int, "delta": int}` (clamp 0..max)
    - `{"op": "set_exhaustion", "target", "level": 0..6}`
    - `{"op": "adjust_xp", "character", "delta": int}` (no auto level-up — rulings are surgical; digest reminds to check)
    - `{"op": "note", "text": str}` (no state change; lands in the event record)
    Registry already stamps `is_ruling=1` + rationale (Task 3). data echoes applied ops. digest = description.
- CLI additions (typer):
  - `dm audit --campaign <slug> [--campaigns-dir campaigns]` — prints every ruling: `#<event_id> <created_at> — <rationale>` + digest line.
  - `dm cmd <name> --campaign <slug> [--campaigns-dir campaigns] [--db data/build/rules.sqlite] --json '<kwargs JSON>'` — opens a context via `open_campaign_context`, executes, prints `result.model_dump_json(indent=2)`. Exit code 0 even for refusals (refusals are results); exit 1 only for unknown campaign/db errors.
- Binding tests:

```python
# tests/commands/test_rulings.py
import pytest

from dm_engine.commands import registry

pytestmark = pytest.mark.usefixtures("party")


def test_ruling_requires_rationale(ctx):
    result = registry.execute("dm_ruling", ctx, description="Kira swings on the rope",
                              rationale="   ")
    assert result.ok is False
    row = ctx.store.conn.execute(
        "SELECT is_ruling FROM event_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["is_ruling"] == 0  # refusal is logged but is not a ruling


def test_ruling_applies_effects_atomically(ctx):
    kira = ctx.store.get_character("Kira")
    good = registry.execute(
        "dm_ruling", ctx, description="Falling rocks", rationale="trap sprung, RAW silent",
        effects=[{"op": "adjust_hp", "target": "Kira", "delta": -4},
                 {"op": "set_condition", "target": "Kira", "condition": "prone"}])
    assert good.ok
    assert ctx.store.get_resources(kira["id"])["hp"] == kira["max_hp"] - 4
    bad = registry.execute(
        "dm_ruling", ctx, description="Bad op batch", rationale="testing",
        effects=[{"op": "adjust_hp", "target": "Kira", "delta": -1},
                 {"op": "set_condition", "target": "Kira", "condition": "sleepy"}])
    assert bad.ok is False
    assert ctx.store.get_resources(kira["id"])["hp"] == kira["max_hp"] - 4  # unchanged


def test_rulings_listed_for_audit(ctx):
    registry.execute("dm_ruling", ctx, description="X", rationale="because RAW gap",
                     effects=[])
    rulings = ctx.store.rulings()
    assert len(rulings) == 1 and rulings[0]["rationale"] == "because RAW gap"
```

- [ ] Steps: binding tests + own tests (lookup commands round-trip; `dm audit` CLI over a temp campaign; `dm cmd` executes skill_check end-to-end) (RED) → implement → GREEN → full suite + ruff → commit `feat: add queries, dm_ruling, and CLI`.

---

### Task 11: Headless combat integration test (milestone gate)

**Files:**
- Create: `tests/integration/__init__.py` (empty), `tests/integration/test_combat_headless.py`, `tests/integration/conftest.py` (party fixture reuse)

**Interfaces:** consumes everything; produces the M3 gate evidence.

- [ ] **Step 1: Write the scripted multi-round combat test** — a single test module that, through `registry.execute` ONLY (no direct store writes except reading for assertions):
  1. bootstrap campaign (fixed seed 1234), create PC fighter Kira + companion cleric Brother Aldric (the Task 5 kwargs), add "healing potion" x1 to Kira.
  2. `start_combat` 2 goblins at near, pc_initiative=15; assert order, advisory difficulty reported.
  3. Drive rounds with explicit commands, whoever's turn it is (a helper `def act(ctx)` dispatches: goblins engage+attack Kira via engine rolls; on Kira's turn: engage goblin-1 then attack with player-supplied values; on Aldric's turn: cast sacred-flame or guiding-bolt at a goblin (engine save/attack) or cure-wounds if Kira is dying).
  4. Assert along the way: a refused illegal action (attack out of reach) logged; an OA fires when a goblin leaves engaged without disengage (data lists provokers, resolved as a reaction attack); player-supplied flags in the event log; band legality (Aldric's guiding-bolt fine from near; Kira's longsword refused from near).
  5. Force the dying sequence: script until Kira drops (deterministic with the seed — if the seed never drops her in 6 rounds, use `dm_ruling` adjust_hp to set her low first with rationale "test scripting"), then death_save fail → cure-wounds mid-sequence → back up.
  6. Kill both goblins, `end_combat`, assert XP division and event-log completeness (`event_count` equals the number of registry.execute calls made — track in the test).
  7. Reopen the store fresh (`open_campaign_context`) and assert `get_scene_state`/`get_campaign_brief` reflect the post-combat world (no active combat, XP awarded, HP as left).
- [ ] **Step 2: Run the full suite + ruff**; iterate until green.
- [ ] **Step 3: M3 gate check** (run and record output):
  ```bash
  uv run pytest -q                       # everything green
  uv run pytest tests/commands tests/integration -v  # gate suites
  uv run ruff check .
  ```
  Gate = every command has at least one mutation+event test; refusal paths covered; headless combat runs multi-round through the registry only.
- [ ] **Step 4: Commit** `git commit -m "test: headless multi-round combat gate"`
- [ ] **Step 5: Merge** `feat/m3-state-commands` into `main` (no push) after the final whole-branch review.
