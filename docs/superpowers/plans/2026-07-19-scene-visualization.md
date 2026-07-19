# Scene Visualization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An engine-rendered, self-refreshing `campaigns/<slug>/scene.html` — a combat band diagram / out-of-combat scene card that is a pure function of campaign state, re-materialized after every successful command, plus `add_scene_prop`/`remove_scene_prop` commands so the DM can pin narrative furniture into it.

**Architecture:** Three stages with strict boundaries (spec: `docs/superpowers/specs/2026-07-19-scene-visualization-design.md`): `build_scene_view(store) -> SceneView` (pure builder, pydantic, JSON-serializable, carries **no monster HP numbers** — enforced by a model validator), `render_scene_html(view) -> str` (pure, deterministic, f-string SVG), `materialize_scene(store) -> Path` (thin file-write adapter). The registry's existing post-command sheet hook calls the adapter. A future live server ("approach C") reuses builder+renderer untouched.

**Tech Stack:** Python ≥3.12, pydantic v2, sqlite3, stdlib only (no new dependencies). Tests: pytest, existing `ctx`/`party` fixtures from `tests/conftest.py`.

## Global Constraints

- FC-1: every command returns the `CommandResult` envelope; illegal input → `ok=False` + `refusal`, never an exception.
- FC-3: `registry.execute` is the only mutation path; new commands are `@command("name")` functions `fn(ctx, ..., **kwargs) -> CommandResult` in a module imported by `commands/__init__.py` (`world.py` already is).
- FC-4: bands are exactly `("engaged", "near", "far", "distant")` — import `BAND_ORDER` from `dm_engine.rules.bands`, never redeclare.
- Monster HP numbers are DM-screen only. `SceneView` must be safe to hand to a player verbatim: monster tokens carry a condition word (`fresh`/`wounded`/`bloodied`/`staggering`), never `hp`/`max_hp`. The fifth tier "near death" is deliberately NOT rendered (no deterministic formula).
- Determinism: same state → byte-identical HTML. No timestamps, no randomness, no dict-iteration-order dependence in the renderer.
- Renderer/builder exceptions propagate (engine bugs roll back the transaction) — never swallow.
- Lint: `uv run ruff check src tests` must pass (line length 100).
- Run tests with `uv run pytest <path> -v`. The rules DB auto-seeds in fixtures; the first test run in a session is slow — that's normal.
- Commit style: Conventional Commits, first line under 50 chars, ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: `scene_props` store layer

**Files:**
- Modify: `src/dm_engine/state/store.py` (table constant + 4 accessors)
- Test: `tests/state/test_scene_props_store.py` (create)

**Interfaces:**
- Consumes: existing `CampaignStore` (`conn`, `root`, constructor migration pattern of `ACTIVE_EFFECTS_TABLE`).
- Produces (Tasks 2 and 3 rely on these exact signatures):
  - `CampaignStore.upsert_scene_prop(name: str, band: str | None, note: str | None) -> None`
  - `CampaignStore.remove_scene_prop(name: str) -> bool` (False = unknown prop)
  - `CampaignStore.scene_props() -> list[dict]` (each: `{"id", "name", "band", "note"}`, ordered by id)
  - `CampaignStore.clear_scene_props() -> int` (count deleted)

- [ ] **Step 1: Write the failing tests**

Create `tests/state/test_scene_props_store.py`:

```python
"""scene_props store accessors: upsert-on-name, remove, clear, ordering,
and in-place migration for campaigns created before the table existed."""

import sqlite3

from dm_engine.state.store import SCENE_PROPS_TABLE, CampaignStore


def _store(tmp_path, slug="p"):
    return CampaignStore.create(
        tmp_path / "campaigns", slug=slug, name="P", death_mode="narrative",
        rng_seed=7, skeleton={"premise": "t"},
    )


def test_upsert_insert_and_update_on_name(tmp_path):
    store = _store(tmp_path)
    store.upsert_scene_prop("overturned wagon", "near", None)
    store.upsert_scene_prop("cliff edge", None, "sheer drop")
    props = store.scene_props()
    assert [(p["name"], p["band"], p["note"]) for p in props] == [
        ("overturned wagon", "near", None),
        ("cliff edge", None, "sheer drop"),
    ]

    # Upsert on the same name moves the prop; ordering (by id) is preserved.
    store.upsert_scene_prop("overturned wagon", "far", "now burning")
    props = store.scene_props()
    assert [(p["name"], p["band"], p["note"]) for p in props] == [
        ("overturned wagon", "far", "now burning"),
        ("cliff edge", None, "sheer drop"),
    ]


def test_remove_returns_false_for_unknown(tmp_path):
    store = _store(tmp_path)
    store.upsert_scene_prop("bonfire", "near", None)
    assert store.remove_scene_prop("bonfire") is True
    assert store.scene_props() == []
    assert store.remove_scene_prop("bonfire") is False


def test_clear_returns_deleted_count(tmp_path):
    store = _store(tmp_path)
    assert store.clear_scene_props() == 0
    store.upsert_scene_prop("a", None, None)
    store.upsert_scene_prop("b", "distant", None)
    assert store.clear_scene_props() == 2
    assert store.scene_props() == []


def test_invalid_band_rejected_by_check_constraint(tmp_path):
    # The store is dumb-and-safe: the CHECK constraint is the last line of
    # defense; command-level validation (Task 2) is the friendly one.
    import pytest

    store = _store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_scene_prop("ghost", "very-far", None)


def test_migration_adds_table_to_existing_campaign(tmp_path):
    # Simulate a pre-feature campaign: create, drop the table, reopen.
    store = _store(tmp_path)
    store.conn.execute("DROP TABLE scene_props")
    store.conn.commit()
    store.close()
    reopened = CampaignStore.open(tmp_path / "campaigns", "p")
    assert reopened.scene_props() == []  # table recreated by constructor
    reopened.close()


def test_table_constant_is_if_not_exists():
    assert "IF NOT EXISTS" in SCENE_PROPS_TABLE
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/state/test_scene_props_store.py -v`
Expected: FAIL — `ImportError: cannot import name 'SCENE_PROPS_TABLE'`.

- [ ] **Step 3: Implement the store layer**

In `src/dm_engine/state/store.py`, directly below the `ACTIVE_EFFECTS_TABLE` constant, add:

```python
# IF NOT EXISTS for the same reason as ACTIVE_EFFECTS_TABLE: doubles as the
# in-place migration for campaigns created before scene visualization.
SCENE_PROPS_TABLE = """
CREATE TABLE IF NOT EXISTS scene_props (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    band TEXT CHECK (band IN ('engaged','near','far','distant')),
    note TEXT
);
"""
```

Change the `SCHEMA` tail from:

```python
""" + ACTIVE_EFFECTS_TABLE
```

to:

```python
""" + ACTIVE_EFFECTS_TABLE + SCENE_PROPS_TABLE
```

In `CampaignStore.__init__`, change the migration line:

```python
        conn.executescript(ACTIVE_EFFECTS_TABLE)
```

to:

```python
        conn.executescript(ACTIVE_EFFECTS_TABLE + SCENE_PROPS_TABLE)
```

Add accessors after the `# -- world -----` section (before `# -- combat --`):

```python
    # -- scene props -------------------------------------------------------

    def upsert_scene_prop(self, name: str, band: str | None, note: str | None) -> None:
        self.conn.execute(
            "INSERT INTO scene_props (name, band, note) VALUES (?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET band = excluded.band,"
            " note = excluded.note",
            (name, band, note),
        )

    def remove_scene_prop(self, name: str) -> bool:
        cur = self.conn.execute("DELETE FROM scene_props WHERE name = ?", (name,))
        return cur.rowcount > 0

    def scene_props(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM scene_props ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def clear_scene_props(self) -> int:
        cur = self.conn.execute("DELETE FROM scene_props")
        return cur.rowcount
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/state/test_scene_props_store.py -v`
Expected: 6 PASS.

- [ ] **Step 5: Full suite + lint, then commit**

Run: `uv run pytest tests/state -q && uv run ruff check src tests`
Expected: all pass.

```bash
git add src/dm_engine/state/store.py tests/state/test_scene_props_store.py
git commit -m "feat(state): scene_props table and accessors"
```

---

### Task 2: prop commands + `set_scene` / `get_scene_state` integration

**Files:**
- Modify: `src/dm_engine/commands/world.py` (two new commands; `set_scene` clears props)
- Modify: `src/dm_engine/commands/combat.py:576-603` (`get_scene_state` gains `props`)
- Test: `tests/commands/test_scene_props.py` (create)

**Interfaces:**
- Consumes: Task 1 store accessors; `BAND_ORDER` from `dm_engine.rules.bands`; `refuse`/`CommandResult` from `dm_engine.commands.envelope`.
- Produces: registry commands `add_scene_prop(name, band=None, note=None)` and `remove_scene_prop(name)` (auto-exposed as MCP tools via signature introspection — no MCP changes needed); `set_scene` result gains `data.props_cleared: int`; `get_scene_state` result gains `data.props: list[dict]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/commands/test_scene_props.py`:

```python
"""add_scene_prop / remove_scene_prop, and their lifecycle hooks in
set_scene (clears) and get_scene_state (reports)."""

from dm_engine.commands import registry


def test_add_scene_prop_upserts_on_name(ctx):
    result = registry.execute("add_scene_prop", ctx, name="overturned wagon", band="near")
    assert result.ok, result.refusal
    assert result.digest == "Prop: overturned wagon (near)"
    assert result.data["props"] == [
        {"id": 1, "name": "overturned wagon", "band": "near", "note": None}
    ]

    # Same name again = move it, not duplicate it.
    result = registry.execute(
        "add_scene_prop", ctx, name="overturned wagon", band="far", note="burning"
    )
    assert result.ok
    assert result.data["props"] == [
        {"id": 1, "name": "overturned wagon", "band": "far", "note": "burning"}
    ]


def test_add_scene_prop_ambient_band_is_none(ctx):
    result = registry.execute("add_scene_prop", ctx, name="thick fog")
    assert result.ok
    assert result.digest == "Prop: thick fog (ambient)"
    assert result.data["props"][0]["band"] is None


def test_add_scene_prop_refuses_invalid_band(ctx):
    result = registry.execute("add_scene_prop", ctx, name="ghost", band="very-far")
    assert not result.ok
    assert "very-far" in result.refusal
    assert "engaged, near, far, distant" in result.refusal


def test_remove_scene_prop_refuses_unknown_listing_known(ctx):
    registry.execute("add_scene_prop", ctx, name="bonfire", band="near")
    result = registry.execute("remove_scene_prop", ctx, name="wagon")
    assert not result.ok
    assert "wagon" in result.refusal
    assert "bonfire" in result.refusal  # steers with the known props

    result = registry.execute("remove_scene_prop", ctx, name="bonfire")
    assert result.ok
    assert result.data["props"] == []


def test_remove_scene_prop_refusal_with_no_props(ctx):
    result = registry.execute("remove_scene_prop", ctx, name="wagon")
    assert not result.ok
    assert "none" in result.refusal


def test_set_scene_clears_props_and_reports_count(ctx):
    registry.execute("add_scene_prop", ctx, name="bonfire", band="near")
    registry.execute("add_scene_prop", ctx, name="thick fog")

    result = registry.execute("set_scene", ctx, description="The road at dawn")
    assert result.ok
    assert result.data["props_cleared"] == 2
    assert "(2 props cleared)" in result.digest
    assert ctx.store.scene_props() == []

    # No props → no clutter in the digest.
    result = registry.execute("set_scene", ctx, description="A quiet mill")
    assert result.data["props_cleared"] == 0
    assert "cleared" not in result.digest


def test_get_scene_state_includes_props(ctx):
    registry.execute("add_scene_prop", ctx, name="bonfire", band="near")
    result = registry.execute("get_scene_state", ctx)
    assert result.ok
    assert result.data["props"] == [
        {"id": 1, "name": "bonfire", "band": "near", "note": None}
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/commands/test_scene_props.py -v`
Expected: FAIL — first tests refuse with `unknown command 'add_scene_prop'`; the `set_scene`/`get_scene_state` tests fail on missing `props_cleared`/`props` keys.

- [ ] **Step 3: Implement the commands**

In `src/dm_engine/commands/world.py`, add the import at the top:

```python
from dm_engine.rules.bands import BAND_ORDER
```

Replace the body of `set_scene` (keep the signature) with:

```python
@command("set_scene")
def set_scene(
    ctx: CommandContext, description: str, location_slug: str | None = None, **kwargs
) -> CommandResult:
    if location_slug is not None and ctx.store.get_location(location_slug) is None:
        return refuse("set_scene", f"unknown location {location_slug!r}")

    # New scene, new furniture: props describe the *current* scene only.
    cleared = ctx.store.clear_scene_props()
    fields: dict = {"scene": description}
    if location_slug is not None:
        fields["location_slug"] = location_slug
    ctx.store.update_world_clock(**fields)
    suffix = f" ({cleared} props cleared)" if cleared else ""
    return CommandResult(
        ok=True, command="set_scene", digest=f"Scene set: {description}{suffix}",
        data={"scene": description, "location_slug": location_slug,
              "props_cleared": cleared},
    )
```

Add the two commands directly after `set_scene`:

```python
@command("add_scene_prop")
def add_scene_prop(
    ctx: CommandContext, name: str, band: str | None = None,
    note: str | None = None, **kwargs,
) -> CommandResult:
    """Pin a narrative scene feature (a wagon, a cliff) into the scene
    visualization. Upserts on name — call again to move or annotate it.
    band=None means ambient (scene-wide). Player-visible by definition:
    never put gm_only material in a prop."""
    if band is not None and band not in BAND_ORDER:
        return refuse(
            "add_scene_prop",
            f"unknown band {band!r} (valid bands: {', '.join(BAND_ORDER)})",
        )
    ctx.store.upsert_scene_prop(name, band, note)
    where = f"({band})" if band else "(ambient)"
    return CommandResult(
        ok=True, command="add_scene_prop", digest=f"Prop: {name} {where}",
        data={"name": name, "band": band, "note": note,
              "props": ctx.store.scene_props()},
    )


@command("remove_scene_prop")
def remove_scene_prop(ctx: CommandContext, name: str, **kwargs) -> CommandResult:
    """Remove a pinned scene prop (destroyed, left behind, no longer
    relevant)."""
    if not ctx.store.remove_scene_prop(name):
        known = ", ".join(p["name"] for p in ctx.store.scene_props()) or "none"
        return refuse(
            "remove_scene_prop", f"unknown prop {name!r} (known: {known})"
        )
    return CommandResult(
        ok=True, command="remove_scene_prop", digest=f"Prop removed: {name}",
        data={"name": name, "props": ctx.store.scene_props()},
    )
```

In `src/dm_engine/commands/combat.py`, inside `get_scene_state`, add `"props"` to the returned `data` dict (after `"npcs_present"`):

```python
        data={
            "clock": clock,
            "location": location,
            "scene": clock.get("scene"),
            "npcs_present": npcs_present,
            "props": ctx.store.scene_props(),
            "combat": combat_payload,
        },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/commands/test_scene_props.py -v`
Expected: 7 PASS.

- [ ] **Step 5: Full command suite + lint, then commit**

Run: `uv run pytest tests/commands tests/test_mcp_schema.py -q && uv run ruff check src tests`
Expected: all pass (new commands auto-surface as MCP tools; no schema test pins the tool list).

```bash
git add src/dm_engine/commands/world.py src/dm_engine/commands/combat.py tests/commands/test_scene_props.py
git commit -m "feat(commands): scene prop pin/remove commands"
```

---

### Task 3: `SceneView` models + builder

**Files:**
- Create: `src/dm_engine/state/scene.py`
- Test: `tests/state/test_scene_view.py` (create)

**Interfaces:**
- Consumes: `CampaignStore` accessors (`campaign_meta`, `world_clock`, `get_location`, `npcs`, `party`, `get_resources`, `get_character_by_id`, `scene_props`, `combat`, `next_event_id`); combatant dict shape from `combat_state.combatants` (keys: `key`, `name`, `kind`, `character_id`/`monster_slug`, `band`, `engaged_with`, `hp`, `max_hp`, `conditions`, `defeated`).
- Produces (Task 4 renders these; Task 5 materializes):
  - `monster_condition_word(hp: int, max_hp: int) -> str`
  - pydantic models `PropView`, `PartyRow`, `TokenView`, `InitiativeEntry`, `CombatView`, `SceneView` (fields exactly as below)
  - `build_scene_view(store: CampaignStore) -> SceneView`

- [ ] **Step 1: Write the failing tests**

Create `tests/state/test_scene_view.py`:

```python
"""build_scene_view: pure projection of campaign state into the
player-visible SceneView. The secrecy guarantee lives HERE: monster HP
numbers never enter the model (validator-enforced), so no renderer or
future server can leak what the view doesn't carry."""

import pydantic
import pytest

from dm_engine.commands import registry
from dm_engine.state.scene import (
    SceneView,
    TokenView,
    build_scene_view,
    monster_condition_word,
)


def _start_goblin_fight(ctx, bands=("near", "near")):
    monsters = [{"slug": "goblin", "band": b} for b in bands]
    result = registry.execute(
        "start_combat", ctx, monsters=monsters, pc_initiative=15
    )
    assert result.ok, result.refusal
    return result


# -- monster condition word (computable tiers only; "near death" is a DM
# judgment call and deliberately has no formula here) ----------------------

@pytest.mark.parametrize("hp,max_hp,word", [
    (10, 10, "fresh"),
    (9, 10, "wounded"),
    (6, 10, "wounded"),     # just above half
    (5, 10, "bloodied"),    # exactly half
    (3, 10, "bloodied"),    # just above quarter
    (2, 10, "staggering"),  # at/below quarter (2.5 -> 2)
    (1, 10, "staggering"),
])
def test_monster_condition_word_thresholds(hp, max_hp, word):
    assert monster_condition_word(hp, max_hp) == word


# -- the secrecy invariant --------------------------------------------------

def test_monster_tokens_cannot_carry_hp_numbers():
    with pytest.raises(pydantic.ValidationError):
        TokenView(
            key="goblin-1", name="Goblin 1", kind="monster", band="near",
            engaged_with=[], conditions=[], defeated=False, active=False,
            hp=7, max_hp=7, condition_word="fresh",
        )
    with pytest.raises(pydantic.ValidationError):
        TokenView(
            key="goblin-1", name="Goblin 1", kind="monster", band="near",
            engaged_with=[], conditions=[], defeated=False, active=False,
            hp=None, max_hp=None, condition_word=None,  # word is mandatory
        )


def test_built_view_strips_monster_numbers_and_words_them(party):
    ctx = party
    _start_goblin_fight(ctx)
    view = build_scene_view(ctx.store)

    assert view.mode == "combat"
    monsters = [t for t in view.combat.tokens if t.kind == "monster"]
    assert len(monsters) == 2
    for tok in monsters:
        assert tok.hp is None and tok.max_hp is None
        assert tok.condition_word == "fresh"

    # And the whole model serializes without a monster number anywhere:
    # goblin HP is 7 in the SRD; give the party distinctive HP so a bare
    # "7" from a character row can't mask a leak.
    dumped = view.model_dump_json()
    assert '"hp":7' not in dumped and '"hp": 7' not in dumped


def test_character_tokens_carry_live_numbers(party):
    ctx = party
    _start_goblin_fight(ctx)
    kira = ctx.store.get_character("Kira")
    ctx.store.update_resources(kira["id"], hp=5, conditions=["poisoned"])
    ctx.store.conn.commit()

    view = build_scene_view(ctx.store)
    kira_tok = next(t for t in view.combat.tokens if t.name == "Kira")
    assert kira_tok.hp == 5
    assert kira_tok.max_hp == kira["max_hp"]
    assert kira_tok.conditions == ["poisoned"]
    assert kira_tok.condition_word is None


def test_combat_view_marks_active_and_initiative_order(party):
    ctx = party
    result = _start_goblin_fight(ctx)
    order = result.data["order"]  # start_combat reports initiative order
    view = build_scene_view(ctx.store)

    assert [e.key for e in view.combat.initiative] == [c["key"] for c in order]
    active = [e.key for e in view.combat.initiative if e.active]
    token_active = [t.key for t in view.combat.tokens if t.active]
    assert active == token_active
    assert len(active) == 1


def test_engaged_pair_at_far_keeps_far_band(party):
    ctx = party
    _start_goblin_fight(ctx, bands=("far", "near"))
    # Drive whoever's turn it is until Aldric can engage the far goblin.
    view = build_scene_view(ctx.store)
    goblin_far = next(t for t in view.combat.tokens if t.band == "far")

    combat = ctx.store.combat()
    for c in combat["combatants"]:
        if c["kind"] == "character" and c["name"] == "Brother Aldric":
            c["band"] = "far"
            c["engaged_with"] = [goblin_far.key]
        if c["key"] == goblin_far.key:
            c["engaged_with"] = ["Brother Aldric"]
    ctx.store.update_combat(combatants=combat["combatants"])
    ctx.store.conn.commit()

    view = build_scene_view(ctx.store)
    aldric = next(t for t in view.combat.tokens if t.name == "Brother Aldric")
    goblin = next(t for t in view.combat.tokens if t.key == goblin_far.key)
    assert aldric.band == "far" and goblin.band == "far"
    assert goblin.key in aldric.engaged_with
    assert "Brother Aldric" in goblin.engaged_with


def test_scene_mode_out_of_combat(party):
    ctx = party
    registry.execute(
        "create_location", ctx, slug="mill", name="The Old Mill",
        description="A ruined mill by the weir.",
    )
    registry.execute("create_npc", ctx, name="Maro", location_slug="mill")
    registry.execute(
        "set_scene", ctx, description="Rain hammers the roof", location_slug="mill"
    )
    registry.execute("add_scene_prop", ctx, name="grain hoist", band="near")

    view = build_scene_view(ctx.store)
    assert view.mode == "scene"
    assert view.combat is None
    assert view.location_name == "The Old Mill"
    assert view.scene_description == "Rain hammers the roof"
    assert view.npcs_present == ["Maro"]
    assert [p.name for p in view.props] == ["grain hoist"]
    assert [(m.name, m.hp) for m in view.party] == [
        ("Kira", view.party[0].hp), ("Brother Aldric", view.party[1].hp)
    ]
    assert view.event_id == ctx.store.next_event_id() - 1


def test_empty_campaign_builds(ctx):
    # Fresh campaign: no party, no location, no scene — must not crash.
    view = build_scene_view(ctx.store)
    assert view.mode == "scene"
    assert view.party == [] and view.props == [] and view.npcs_present == []
    assert view.location_name is None
    assert isinstance(view, SceneView)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/state/test_scene_view.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dm_engine.state.scene'`.

- [ ] **Step 3: Implement models + builder**

Create `src/dm_engine/state/scene.py`:

```python
"""Scene visualization: SceneView builder (this task), HTML/SVG renderer
and materializer (later tasks). Spec:
docs/superpowers/specs/2026-07-19-scene-visualization-design.md.

`build_scene_view` is a pure projection of campaign state into a
JSON-serializable, PLAYER-VISIBLE model: monster HP numbers never enter it
(validator-enforced), so no renderer — or future live-view server — can
leak what the view doesn't carry. The registry's post-command hook
materializes it to campaigns/<slug>/scene.html.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator

from dm_engine.state.store import CampaignStore


def monster_condition_word(hp: int, max_hp: int) -> str:
    """The dm-session skill's public monster-status ladder, computable
    tiers only ("near death" is a DM judgment call, deliberately absent).
    full -> fresh; >half -> wounded; <=half -> bloodied; <=quarter ->
    staggering."""
    if hp >= max_hp:
        return "fresh"
    if hp > max_hp / 2:
        return "wounded"
    if hp > max_hp / 4:
        return "bloodied"
    return "staggering"


class PropView(BaseModel):
    name: str
    band: str | None = None
    note: str | None = None


class PartyRow(BaseModel):
    name: str
    hp: int
    max_hp: int
    conditions: list[str]


class TokenView(BaseModel):
    key: str
    name: str
    kind: Literal["character", "monster"]
    band: str
    engaged_with: list[str]
    conditions: list[str]
    defeated: bool
    active: bool
    hp: int | None = None              # characters only
    max_hp: int | None = None          # characters only
    condition_word: str | None = None  # monsters only

    @model_validator(mode="after")
    def _monster_numbers_stay_behind_the_screen(self) -> "TokenView":
        if self.kind == "monster":
            if self.hp is not None or self.max_hp is not None:
                raise ValueError(
                    "monster HP numbers are DM-screen only and never enter SceneView"
                )
            if self.condition_word is None:
                raise ValueError("monster tokens must carry a condition word")
        return self


class InitiativeEntry(BaseModel):
    key: str
    name: str
    active: bool


class CombatView(BaseModel):
    round: int
    tokens: list[TokenView]        # initiative order
    initiative: list[InitiativeEntry]


class SceneView(BaseModel):
    mode: Literal["combat", "scene"]
    campaign_name: str
    event_id: int
    day: int
    minutes: int
    location_name: str | None
    scene_description: str | None
    npcs_present: list[str]
    party: list[PartyRow]
    props: list[PropView]
    combat: CombatView | None = None


def build_scene_view(store: CampaignStore) -> SceneView:
    meta = store.campaign_meta()
    clock = store.world_clock()

    location = None
    npcs_present: list[str] = []
    if clock.get("location_slug"):
        location = store.get_location(clock["location_slug"])
        npcs_present = [n["name"] for n in store.npcs(clock["location_slug"])]

    party = []
    for char in store.party():
        res = store.get_resources(char["id"])
        party.append(PartyRow(
            name=char["name"], hp=res["hp"], max_hp=char["max_hp"],
            conditions=res["conditions"],
        ))

    props = [
        PropView(name=p["name"], band=p["band"], note=p["note"])
        for p in store.scene_props()
    ]

    combat_row = store.combat()
    combat = _build_combat_view(store, combat_row) if combat_row["active"] else None

    return SceneView(
        mode="combat" if combat else "scene",
        campaign_name=meta["name"],
        event_id=store.next_event_id() - 1,
        day=clock["day"],
        minutes=clock["minutes"],
        location_name=location["name"] if location else None,
        scene_description=clock.get("scene"),
        npcs_present=npcs_present,
        party=party,
        props=props,
        combat=combat,
    )


def _build_combat_view(store: CampaignStore, combat: dict) -> CombatView:
    active_key = combat["combatants"][combat["turn_index"]]["key"]
    tokens = []
    for c in combat["combatants"]:
        if c["kind"] == "character":
            res = store.get_resources(c["character_id"])
            char = store.get_character_by_id(c["character_id"])
            hp, max_hp, word = res["hp"], char["max_hp"], None
            conditions = res["conditions"]
        else:
            # The one place monster numbers get worded before they can
            # reach a player-visible surface.
            hp, max_hp = None, None
            word = monster_condition_word(c["hp"], c["max_hp"])
            conditions = c["conditions"]
        tokens.append(TokenView(
            key=c["key"], name=c["name"], kind=c["kind"], band=c["band"],
            engaged_with=list(c["engaged_with"]), conditions=list(conditions),
            defeated=bool(c["defeated"]), active=(c["key"] == active_key),
            hp=hp, max_hp=max_hp, condition_word=word,
        ))
    initiative = [
        InitiativeEntry(key=c["key"], name=c["name"], active=(c["key"] == active_key))
        for c in combat["combatants"]
    ]
    return CombatView(round=combat["round"], tokens=tokens, initiative=initiative)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/state/test_scene_view.py -v`
Expected: all PASS. If `test_built_view_strips_monster_numbers_and_words_them` fails on the serialization assertion because a *character* legitimately has 7 HP, fix the TEST (set Kira's HP to something distinctive first), never the validator.

- [ ] **Step 5: Lint + commit**

Run: `uv run pytest tests/state -q && uv run ruff check src tests`

```bash
git add src/dm_engine/state/scene.py tests/state/test_scene_view.py
git commit -m "feat(state): SceneView model and builder"
```

---

### Task 4: deterministic HTML/SVG renderer

**Files:**
- Modify: `src/dm_engine/state/scene.py` (append renderer)
- Test: `tests/state/test_scene_render.py` (create)

**Interfaces:**
- Consumes: Task 3 models (`SceneView`, `TokenView`, `PropView`); `BAND_ORDER` from `dm_engine.rules.bands`.
- Produces: `render_scene_html(view: SceneView) -> str` (full HTML document, self-contained, deterministic).

- [ ] **Step 1: Write the failing tests**

Create `tests/state/test_scene_render.py`:

```python
"""render_scene_html: deterministic, self-contained, and structurally
faithful — engaged pairs render in their shared band track, tokens don't
move unless band/engagement changes, and nothing numeric about monsters
survives into the page."""

from dm_engine.state.scene import (
    CombatView,
    InitiativeEntry,
    PartyRow,
    PropView,
    SceneView,
    TokenView,
    render_scene_html,
)


def _token(key, kind="monster", band="near", *, name=None, engaged=(),
           active=False, defeated=False, conditions=(), hp=None, max_hp=None,
           word=None):
    if kind == "monster" and word is None:
        word = "fresh"
    return TokenView(
        key=key, name=name or key, kind=kind, band=band,
        engaged_with=list(engaged), conditions=list(conditions),
        defeated=defeated, active=active, hp=hp, max_hp=max_hp,
        condition_word=word,
    )


def _view(tokens, *, props=(), round_=1):
    initiative = [
        InitiativeEntry(key=t.key, name=t.name, active=t.active) for t in tokens
    ]
    return SceneView(
        mode="combat", campaign_name="T", event_id=42, day=1, minutes=480,
        location_name="The Mill", scene_description="Rain", npcs_present=[],
        party=[], props=list(props),
        combat=CombatView(round=round_, tokens=tokens, initiative=initiative),
    )


def test_determinism_same_view_same_bytes():
    view = _view([
        _token("Kira", kind="character", band="engaged", hp=11, max_hp=12,
               active=True, engaged=["goblin-1"]),
        _token("goblin-1", band="engaged", engaged=["Kira"]),
    ])
    assert render_scene_html(view) == render_scene_html(view)


def test_no_jitter_active_turn_change_moves_nothing():
    def tokens(active_key):
        return [
            _token("Kira", kind="character", band="near", hp=11, max_hp=12,
                   active=active_key == "Kira"),
            _token("goblin-1", band="far", active=active_key == "goblin-1"),
        ]

    import re
    coords = re.compile(r'<rect x="[\d.]+" y="[\d.]+" rx')
    a = coords.findall(render_scene_html(_view(tokens("Kira"))))
    b = coords.findall(render_scene_html(_view(tokens("goblin-1"))))
    assert a == b  # token rects at identical coordinates


def test_engaged_pair_at_far_renders_in_far_track_with_link():
    view = _view([
        _token("Aldric", kind="character", band="far", hp=24, max_hp=24,
               engaged=["goblin-1"], active=True),
        _token("goblin-1", band="far", engaged=["Aldric"]),
        _token("goblin-2", band="engaged"),
    ])
    html = render_scene_html(view)
    # Band tracks appear in FC-4 order; both engaged-at-far tokens land
    # between the FAR and DISTANT labels.
    far_track = html.split("FAR")[1].split("DISTANT")[0]
    assert "Aldric" in far_track and "goblin-1" in far_track
    assert 'class="melee"' in html  # exactly the one link
    assert html.count('class="melee"') == 1


def test_monster_shows_word_never_numbers():
    view = _view([
        _token("boss", band="near", word="bloodied", conditions=["poisoned"]),
        _token("Kira", kind="character", band="near", hp=9973, max_hp=9974),
    ])
    html = render_scene_html(view)
    assert "bloodied" in html
    assert "poisoned" in html
    assert "9973/9974" in html  # party numbers are public


def test_defeated_monster_reads_down_not_staggering():
    view = _view([_token("goblin-1", band="near", word="staggering", defeated=True)])
    html = render_scene_html(view)
    assert "down" in html


def test_props_render_on_their_band_and_ambient_strip():
    view = _view(
        [_token("Kira", kind="character", band="engaged", hp=1, max_hp=1)],
        props=[
            PropView(name="overturned wagon", band="near"),
            PropView(name="thick fog", band=None),
        ],
    )
    html = render_scene_html(view)
    near_track = html.split("NEAR")[1].split("FAR")[0]
    assert "overturned wagon" in near_track
    assert "thick fog" in html.split("<svg")[0]  # ambient strip above the map


def test_names_are_escaped():
    view = _view([
        _token('<script>alert("x")</script>', kind="character", band="near",
               hp=1, max_hp=1, name='<script>alert("x")</script>'),
    ])
    html = render_scene_html(view)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_scene_mode_card():
    view = SceneView(
        mode="scene", campaign_name="T", event_id=7, day=2, minutes=1140,
        location_name="The Old Mill", scene_description="Rain hammers the roof",
        npcs_present=["Maro"],
        party=[PartyRow(name="Kira", hp=11, max_hp=12, conditions=["poisoned"])],
        props=[PropView(name="grain hoist", band="near", note="creaking")],
    )
    html = render_scene_html(view)
    for expected in ("The Old Mill", "Rain hammers the roof", "Maro",
                     "11/12", "poisoned", "grain hoist", "creaking",
                     "day 2", "19:00", "as of event #7"):
        assert expected in html
    assert "<svg" not in html  # no band map out of combat


def test_page_is_self_contained_and_self_refreshing():
    view = SceneView(
        mode="scene", campaign_name="T", event_id=1, day=1, minutes=480,
        location_name=None, scene_description=None, npcs_present=[],
        party=[], props=[],
    )
    html = render_scene_html(view)
    assert html.startswith("<!DOCTYPE html>")
    assert '<meta http-equiv="refresh" content="2">' in html
    assert "http://" not in html and "https://" not in html  # no external fetches
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/state/test_scene_render.py -v`
Expected: FAIL — `ImportError: cannot import name 'render_scene_html'`.

- [ ] **Step 3: Implement the renderer**

Append to `src/dm_engine/state/scene.py` (add `import html as _html` and the `BAND_ORDER` import at the top of the file):

```python
import html as _html

from dm_engine.rules.bands import BAND_ORDER
```

then the renderer:

```python
# -- renderer ---------------------------------------------------------------
# Deterministic by construction: layout is a pure function of the view
# (no timestamps, no randomness, iteration in model/list order only).

_TOKEN_W, _TOKEN_H = 150, 64
_TOKEN_GAP = 12
_PER_ROW = 5
_BAND_H = 170            # fits two token rows + a prop line
_LABEL_W = 90
_SVG_W = _LABEL_W + _PER_ROW * (_TOKEN_W + _TOKEN_GAP) + 20

_CSS = """
body { background: #16181d; color: #d8d4c8; font: 14px/1.45 -apple-system,
       "Segoe UI", sans-serif; margin: 1.2rem auto; max-width: 940px; }
header { font-size: 1.05rem; letter-spacing: .04em; margin-bottom: .6rem;
         color: #e8e3d3; }
footer { margin-top: .8rem; color: #6f6a5e; font-size: .8rem; }
.round { font-weight: 700; margin: .4rem 0 .2rem; color: #e0a626; }
.initiative { margin-bottom: .5rem; }
.initiative .init { display: inline-block; padding: .1rem .5rem;
    margin-right: .3rem; border: 1px solid #3a3d45; border-radius: 999px;
    font-size: .8rem; color: #a9a494; }
.initiative .init.active { border-color: #e0a626; color: #e0a626;
    font-weight: 700; }
.ambient { color: #8f8975; font-style: italic; margin-bottom: .4rem; }
svg .track { fill: #1d2027; stroke: #2a2e37; }
svg .bandlabel { fill: #6f7787; font: 700 12px sans-serif;
    letter-spacing: .12em; }
svg .token rect { rx: 8; }
svg .token.character rect { fill: #2e5d8a; }
svg .token.monster rect { fill: #8a3232; }
svg .token.defeated rect { fill: #3a3a3a; }
svg .token.defeated text { fill: #8a8578; }
svg .token.active rect { stroke: #e0a626; stroke-width: 3; }
svg .name { fill: #f0ece0; font: 700 13px sans-serif; }
svg .sub { fill: #d8d4c8; font: 11px sans-serif; }
svg .hpback { fill: #14161a; }
svg .hpfill { fill: #79a56a; }
svg .melee { stroke: #d8d4c8; stroke-width: 1.5; stroke-dasharray: 4 3; }
svg .swords { fill: #e0a626; font-size: 13px; }
svg .prop { fill: #b0a274; font: italic 12px sans-serif; }
.card { background: #1d2027; border: 1px solid #2a2e37; border-radius: 10px;
        padding: 1rem 1.2rem; }
.card h1 { margin: 0 0 .4rem; font-size: 1.3rem; color: #e8e3d3; }
.card .scene { color: #c9c4b4; }
.card .npcs, .card .props { color: #a9a494; }
.card table { border-collapse: collapse; margin-top: .6rem; }
.card td, .card th { padding: .2rem .8rem .2rem 0; text-align: left; }
.card th { color: #6f7787; font-size: .8rem; letter-spacing: .08em;
           text-transform: uppercase; }
"""


def render_scene_html(view: SceneView) -> str:
    body = _render_combat(view) if view.mode == "combat" else _render_scene_card(view)
    time = f"day {view.day}, {view.minutes // 60:02d}:{view.minutes % 60:02d}"
    header_bits = [_html.escape(view.campaign_name), time]
    if view.location_name:
        header_bits.append(_html.escape(view.location_name))
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta http-equiv="refresh" content="2">\n'
        f"<title>{_html.escape(view.campaign_name)} — scene</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        f"<header>{' · '.join(header_bits)}</header>\n"
        f"{body}\n"
        f"<footer>as of event #{view.event_id}</footer>\n"
        "</body>\n</html>\n"
    )


def _render_scene_card(view: SceneView) -> str:
    parts = ['<section class="card">']
    parts.append(f"<h1>{_html.escape(view.location_name or 'Somewhere')}</h1>")
    if view.scene_description:
        parts.append(f'<p class="scene">{_html.escape(view.scene_description)}</p>')
    if view.npcs_present:
        names = ", ".join(_html.escape(n) for n in view.npcs_present)
        parts.append(f'<p class="npcs">Present: {names}</p>')
    if view.props:
        items = []
        for p in view.props:
            where = f" ({p.band})" if p.band else ""
            note = f" — {_html.escape(p.note)}" if p.note else ""
            items.append(f"<li>{_html.escape(p.name)}{where}{note}</li>")
        parts.append('<ul class="props">' + "".join(items) + "</ul>")
    if view.party:
        rows = "".join(
            f"<tr><td>{_html.escape(m.name)}</td><td>{m.hp}/{m.max_hp}</td>"
            f"<td>{_html.escape(', '.join(m.conditions)) or '—'}</td></tr>"
            for m in view.party
        )
        parts.append(
            '<table class="party"><tr><th>Party</th><th>HP</th>'
            f"<th>Conditions</th></tr>{rows}</table>"
        )
    parts.append("</section>")
    return "\n".join(parts)


def _render_combat(view: SceneView) -> str:
    combat = view.combat
    strip = "".join(
        f'<span class="init{" active" if e.active else ""}">'
        f"{_html.escape(e.name)}</span>"
        for e in combat.initiative
    )
    ambient = [p for p in view.props if p.band is None]
    ambient_html = ""
    if ambient:
        names = " · ".join(_html.escape(p.name) for p in ambient)
        ambient_html = f'<div class="ambient">{names}</div>\n'
    svg = _render_band_svg(combat.tokens, [p for p in view.props if p.band])
    return (
        f'<div class="round">Round {combat.round}</div>\n'
        f'<div class="initiative">{strip}</div>\n'
        f"{ambient_html}{svg}"
    )


def _band_layout(tokens: list[TokenView]) -> list[TokenView]:
    """Engaged clusters adjacent, then initiative order (input order IS
    initiative order). A token's slot changes only when its band or its
    engagement set changes — the no-jitter rule."""
    clusters: list[list[TokenView]] = []
    home_of: dict[str, list[TokenView]] = {}
    for tok in tokens:
        home = None
        for partner in tok.engaged_with:
            if partner in home_of:
                home = home_of[partner]
                break
        if home is None:
            home = []
            clusters.append(home)
        home.append(tok)
        home_of[tok.key] = home
    return [t for cluster in clusters for t in cluster]


def _token_svg(tok: TokenView, x: float, y: float) -> str:
    cls = tok.kind
    if tok.defeated:
        cls += " defeated"
    if tok.active:
        cls += " active"
    if tok.kind == "character":
        frac = max(0.0, min(1.0, tok.hp / tok.max_hp)) if tok.max_hp else 0.0
        bits = f"{tok.hp}/{tok.max_hp}"
        if tok.conditions:
            bits += " · " + ", ".join(tok.conditions)
        status = (
            f'<rect x="{x + 8}" y="{y + 32}" width="{_TOKEN_W - 16}" height="6"'
            ' class="hpback"/>'
            f'<rect x="{x + 8}" y="{y + 32}" width="{(_TOKEN_W - 16) * frac:.1f}"'
            ' height="6" class="hpfill"/>'
            f'<text x="{x + 8}" y="{y + 54}" class="sub">{_html.escape(bits)}</text>'
        )
    else:
        word = "down" if tok.defeated else tok.condition_word
        bits = ", ".join([word, *tok.conditions])
        status = (
            f'<text x="{x + 8}" y="{y + 44}" class="sub">{_html.escape(bits)}</text>'
        )
    return (
        f'<g class="token {cls}">'
        f'<rect x="{x}" y="{y}" rx="8" width="{_TOKEN_W}" height="{_TOKEN_H}"/>'
        f'<text x="{x + 8}" y="{y + 22}" class="name">{_html.escape(tok.name)}</text>'
        f"{status}</g>"
    )


def _render_band_svg(tokens: list[TokenView], band_props: list[PropView]) -> str:
    height = 4 * _BAND_H + 10
    parts = [
        f'<svg width="{_SVG_W}" height="{height}"'
        f' viewBox="0 0 {_SVG_W} {height}" xmlns="http://www.w3.org/2000/svg"'
        ' role="img">'
    ]
    positions: dict[str, tuple[float, float]] = {}
    token_svgs: list[str] = []
    for i, band in enumerate(BAND_ORDER):
        y0 = 5 + i * _BAND_H
        parts.append(
            f'<rect x="2" y="{y0}" width="{_SVG_W - 4}" height="{_BAND_H - 8}"'
            ' class="track"/>'
        )
        parts.append(
            f'<text x="14" y="{y0 + 22}" class="bandlabel">{band.upper()}</text>'
        )
        laid_out = _band_layout([t for t in tokens if t.band == band])
        for slot, tok in enumerate(laid_out):
            row, col = divmod(slot, _PER_ROW)
            x = _LABEL_W + col * (_TOKEN_W + _TOKEN_GAP)
            y = y0 + 12 + row * (_TOKEN_H + 8)
            positions[tok.key] = (x, y)
            token_svgs.append(_token_svg(tok, x, y))
        for j, prop in enumerate(p for p in band_props if p.band == band):
            px = _LABEL_W + j * 180
            py = y0 + _BAND_H - 20
            parts.append(
                f'<text x="{px}" y="{py}" class="prop">'
                f"◆ {_html.escape(prop.name)}</text>"
            )

    links = sorted({
        tuple(sorted((tok.key, partner)))
        for tok in tokens
        for partner in tok.engaged_with
        if tok.key in positions and partner in positions
    })
    for a, b in links:
        ax, ay = positions[a]
        bx, by = positions[b]
        ax_c, ay_c = ax + _TOKEN_W / 2, ay + _TOKEN_H / 2
        bx_c, by_c = bx + _TOKEN_W / 2, by + _TOKEN_H / 2
        parts.append(
            f'<line x1="{ax_c}" y1="{ay_c}" x2="{bx_c}" y2="{by_c}" class="melee"/>'
        )
        mx, my = (ax_c + bx_c) / 2, (ay_c + by_c) / 2
        parts.append(
            f'<text x="{mx}" y="{my}" class="swords" text-anchor="middle">⚔</text>'
        )

    parts.extend(token_svgs)  # tokens paint over links
    parts.append("</svg>")
    return "".join(parts)
```

Note the generator in the prop loop: `enumerate(p for p in band_props if p.band == band)` — index `j` counts props *on this band*, so each band's prop line starts at the left edge.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/state/test_scene_render.py tests/state/test_scene_view.py -v`
Expected: all PASS.

- [ ] **Step 5: Eyeball one real page (manual sanity, not a test)**

Run:

```bash
uv run python - <<'EOF'
from pathlib import Path
from dm_engine.state.scene import (CombatView, InitiativeEntry, PropView,
                                   SceneView, TokenView, render_scene_html)
tokens = [
    TokenView(key="Kira", name="Kira", kind="character", band="engaged",
              engaged_with=["goblin-1"], conditions=[], defeated=False,
              active=True, hp=9, max_hp=12),
    TokenView(key="goblin-1", name="Goblin 1", kind="monster", band="engaged",
              engaged_with=["Kira"], conditions=["prone"], defeated=False,
              active=False, condition_word="bloodied"),
    TokenView(key="Brother Aldric", name="Brother Aldric", kind="character",
              band="far", engaged_with=["goblin-2"], conditions=[],
              defeated=False, active=False, hp=24, max_hp=24),
    TokenView(key="goblin-2", name="Goblin 2", kind="monster", band="far",
              engaged_with=["Brother Aldric"], conditions=[], defeated=False,
              active=False, condition_word="fresh"),
]
view = SceneView(mode="combat", campaign_name="Smoke Test", event_id=1,
                 day=1, minutes=980, location_name="The Ford",
                 scene_description=None, npcs_present=[], party=[],
                 props=[PropView(name="overturned wagon", band="near")],
                 combat=CombatView(round=2, tokens=tokens,
                                   initiative=[InitiativeEntry(key=t.key, name=t.name, active=t.active) for t in tokens]))
Path("/tmp/scene-smoke.html").write_text(render_scene_html(view))
print("open /tmp/scene-smoke.html")
EOF
```

Open the file in a browser; verify: four tracks, Kira⚔Goblin-1 linked in ENGAGED, Aldric⚔Goblin-2 linked in FAR, wagon diamond on NEAR, gold ring on Kira, no monster numbers anywhere. Report what you see.

- [ ] **Step 6: Lint + commit**

Run: `uv run pytest tests/state -q && uv run ruff check src tests`

```bash
git add src/dm_engine/state/scene.py tests/state/test_scene_render.py
git commit -m "feat(state): deterministic scene HTML renderer"
```

---

### Task 5: materialization hook + integration tests

**Files:**
- Modify: `src/dm_engine/state/scene.py` (append `materialize_scene`)
- Modify: `src/dm_engine/commands/registry.py:113-114` (hook)
- Test: `tests/integration/test_e2e_scene_html.py` (create)

**Interfaces:**
- Consumes: Tasks 3-4 (`build_scene_view`, `render_scene_html`); `CampaignStore.root`; registry post-command hook (`if result.ok: sheets.write_party_sheets(...)`).
- Produces: `materialize_scene(store: CampaignStore) -> Path` writing `campaigns/<slug>/scene.html`; the hook calls it after `write_party_sheets` on every successful command.

- [ ] **Step 1: Write the failing tests**

Create `tests/integration/test_e2e_scene_html.py`:

```python
"""scene.html is materialized by the registry hook after every successful
command, switches modes with combat, and is untouched by refusals."""

from dm_engine.commands import registry


def _scene_path(ctx):
    return ctx.store.root / "scene.html"


def test_success_materializes_and_combat_flips_modes(party):
    ctx = party
    result = registry.execute("set_scene", ctx, description="A misty ford")
    assert result.ok
    path = _scene_path(ctx)
    assert path.exists()
    html = path.read_text()
    assert "A misty ford" in html
    assert "<svg" not in html  # scene mode

    result = registry.execute(
        "start_combat", ctx,
        monsters=[{"slug": "goblin", "band": "near"}], pc_initiative=15,
    )
    assert result.ok, result.refusal
    html = path.read_text()
    assert "<svg" in html          # combat mode
    assert "Round 1" in html
    assert "Kira" in html

    result = registry.execute("end_combat", ctx)
    assert result.ok
    assert "<svg" not in path.read_text()  # back to scene mode


def test_refusal_does_not_rewrite_the_file(party):
    ctx = party
    registry.execute("set_scene", ctx, description="Before")
    path = _scene_path(ctx)
    before = path.read_text()

    result = registry.execute("travel", ctx, destination_slug="nowhere", hours=1)
    assert not result.ok  # unknown location -> refusal
    assert path.read_text() == before


def test_footer_tracks_the_committing_event(party):
    ctx = party
    result = registry.execute("set_scene", ctx, description="Now")
    assert f"as of event #{result.event_ids[0]}" in _scene_path(ctx).read_text()


def test_prop_commands_show_up_immediately(party):
    ctx = party
    registry.execute("add_scene_prop", ctx, name="grain hoist", band="near")
    assert "grain hoist" in _scene_path(ctx).read_text()
    registry.execute("remove_scene_prop", ctx, name="grain hoist")
    assert "grain hoist" not in _scene_path(ctx).read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_e2e_scene_html.py -v`
Expected: FAIL — `scene.html` never created.

- [ ] **Step 3: Implement adapter + hook**

Append to `src/dm_engine/state/scene.py`:

```python
def materialize_scene(store: CampaignStore) -> Path:
    """The thin I/O adapter (registry post-command hook). A future live
    web view replaces this file write with an HTTP response and reuses
    build_scene_view/render_scene_html unchanged."""
    path = store.root / "scene.html"
    path.write_text(render_scene_html(build_scene_view(store)), encoding="utf-8")
    return path
```

Add `from pathlib import Path` to the imports of `scene.py` if not already present.

In `src/dm_engine/commands/registry.py`, add the import next to the sheets import:

```python
from dm_engine.state import scene, sheets
```

(replacing `from dm_engine.state import sheets`), and extend the success hook in `execute`:

```python
            if result.ok:
                sheets.write_party_sheets(ctx.store, ctx.rules)
                scene.materialize_scene(ctx.store)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_e2e_scene_html.py -v`
Expected: 4 PASS.

- [ ] **Step 5: FULL suite + lint (the hook touches every command), then commit**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: entire suite passes. If any pre-existing integration test asserts an exact `sheets/` directory listing or file count, it may now also see `scene.html` — fix the assertion, not the hook (scene.html lives in the campaign root, not `sheets/`, so this is unlikely).

```bash
git add src/dm_engine/state/scene.py src/dm_engine/commands/registry.py tests/integration/test_e2e_scene_html.py
git commit -m "feat: materialize scene.html after every command"
```

---

### Task 6: docs + dm-session skill + README

**Files:**
- Modify: `docs/SCHEMA.md` (scene_props table, scene.html alongside sheets)
- Modify: `.claude/skills/dm-session/SKILL.md` (scene view + prop discipline)
- Modify: `README.md` (player-facing blurb)

**Interfaces:**
- Consumes: everything landed in Tasks 1-5. No code changes.

- [ ] **Step 1: `docs/SCHEMA.md`**

In the intro paragraph that lists what sits alongside each campaign store (`sheets/` and `snapshots/`), extend the sentence to also name `scene.html` (rendered scene visualization, re-materialized after every successful command).

Add a table section after `### `npcs` / `locations` / `quests``:

```markdown
### `scene_props` — pinned scene furniture

Narrative props the DM pins into the scene visualization
(`add_scene_prop` / `remove_scene_prop`; `set_scene` clears the table —
new scene, new furniture). Rendered into `campaigns/<slug>/scene.html`
and reported by `get_scene_state`. (`CREATE TABLE IF NOT EXISTS` runs on
every store open, migrating older campaigns in place.)

| Column | Notes |
|---|---|
| `name` | display text, UNIQUE — upsert key (`"overturned wagon"`) |
| `band` | `engaged/near/far/distant`, or NULL for ambient (scene-wide) |
| `note` | optional free text shown as a subtitle |
```

- [ ] **Step 2: dm-session `SKILL.md`**

Three edits, matching the skill's existing voice:

1. In **The character sheet** section, after the "keep their sheet open" sentence, add:

```markdown
The engine also materializes `campaigns/<slug>/scene.html` after every
command — a live, self-refreshing scene view (combat band map in combat,
scene card otherwise). At session start, alongside the sheet reminder,
tell the player once to open it in a browser tab.
```

2. In **Iron rules** rule 4 (persistence triggers), extend the first bullet's persistence list:

```markdown
     Notable scene furniture the fiction establishes (an overturned
     wagon, a cliff edge, a bonfire) → `add_scene_prop` (with its band)
     in the same breath as narrating it, so the scene view shows what
     the prose says; `remove_scene_prop` when it's destroyed or left
     behind. Props are player-visible by definition — never put
     `gm_only` material in one.
```

3. In **Combat procedure** step 1, after the initiative-announcement sentence, add:

```markdown
   Terrain that matters tactically (cover, hazards, the thing worth
   fighting over) should already be pinned as scene props — the band map
   the player is watching only shows what you pinned.
```

- [ ] **Step 3: README.md**

Read `README.md` first. Add a short feature bullet/paragraph in the player-facing features area (match surrounding style), e.g.:

```markdown
- **Live scene view** — the engine renders `campaigns/<slug>/scene.html`
  after every command: a combat band map (positions, engagements, party
  HP, monster condition words — never their numbers) or an
  out-of-combat scene card. Keep it open in a browser tab; it refreshes
  itself. The DM pins scene furniture into it with
  `add_scene_prop`/`remove_scene_prop`.
```

- [ ] **Step 4: Verify + commit**

Run: `uv run pytest -q && uv run ruff check src tests` (unchanged code — belt and braces).

```bash
git add docs/SCHEMA.md .claude/skills/dm-session/SKILL.md README.md
git commit -m "docs: scene visualization schema, skill, README"
```

---

## Plan self-review notes (already applied)

- Spec coverage: props table+commands (T1-T2), builder+secrecy validator (T3), renderer with corrected engaged-at-far handling (T4), registry hook + refusal/crash behavior (T5), docs/skill/README (T6). "Near death" tier exclusion honored in T3.
- The spec's hypothesis-style property test is realized as the model-validator + validation tests (stronger: the invariant is enforced at construction, not sampled).
- Type consistency: `scene_props()` dict keys (`id`/`name`/`band`/`note`) match T2's asserted payloads; `materialize_scene(store)` takes only the store (no rules DB needed).
