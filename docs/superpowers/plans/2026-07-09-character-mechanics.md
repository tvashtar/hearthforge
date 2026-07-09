# Character Mechanics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Character mechanical facts (attack math, save proficiencies) become engine-derived from SRD data and validated at creation; expertise works for skills and tool checks; the sheet renders like a real 5e character sheet; existing campaigns migrate on open.

**Architecture:** New pydantic models (`models/character.py`) define the only valid stored shape for attacks/proficiencies. A pure derivation module (`rules/character_build.py`) converts SRD records into those shapes at `create_character` time (Approach A: resolve-at-creation, stored resolved, no rules-DB dependency during combat). Shared modifier helpers are used by both the attack resolver and the sheet renderer so they can never diverge. An idempotent normalizer runs on campaign open.

**Tech Stack:** Python 3.13, pydantic v2, sqlite3, pytest. Package manager: `uv` (run everything as `uv run pytest ...`).

**Spec:** `docs/superpowers/specs/2026-07-09-character-mechanics-design.md`

## Global Constraints

- Frozen contracts (FC-1..7, `docs/superpowers/plans/2026-07-08-roadmap.md`): bad *input* → structured `ok=False` refusal via `refuse()`, never an exception; engine invariant violations raise; commands are the only mutation path; every command = one transaction.
- Canonical skill slugs are hyphenated (`sleight-of-hand`); normalize caller slugs by lowercasing and mapping `_`/space → `-`.
- `AttackSpec.damage` is base dice only (`1d6`) — modifiers are always computed at use.
- Callers may never supply `saves` (or `saving_throws`) in proficiencies — refusal.
- Branch: `feat/character-mechanics` (already exists, spec committed). Conventional commits, first line < 50 chars.
- Run tests with `uv run pytest <path> -v`. The full suite is `uv run pytest` (expect 347+ passing at the end; requires `data/build/rules.sqlite` — run `uv run dm seed` first if missing).

---

### Task 1: Character models (`models/character.py`)

**Files:**
- Create: `src/dm_engine/models/character.py`
- Modify: `src/dm_engine/commands/checks.py:22-41` (import `SKILL_ABILITIES` instead of defining it)
- Test: `tests/test_character_models.py`

**Interfaces:**
- Produces: `Ability` (Literal of 6 abilities), `SKILL_ABILITIES: dict[str, str]` (18 skills → ability), `normalize_slug(value: str) -> str`, `class AttackSpec(BaseModel)`, `class Proficiencies(BaseModel)` — exact fields below. Every later task imports from here.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_character_models.py
"""AttackSpec/Proficiencies are the only valid stored character-mechanics
shapes; these tests pin the validation behavior everything else relies on."""

import pytest
from pydantic import ValidationError

from dm_engine.models.character import (
    SKILL_ABILITIES,
    AttackSpec,
    Proficiencies,
    normalize_slug,
)


def test_skill_abilities_has_all_18_canonical_skills():
    assert len(SKILL_ABILITIES) == 18
    assert SKILL_ABILITIES["sleight-of-hand"] == "dex"
    assert SKILL_ABILITIES["athletics"] == "str"


def test_normalize_slug_maps_underscores_and_case():
    assert normalize_slug("Thieves_Tools") == "thieves-tools"
    assert normalize_slug("sleight of hand") == "sleight-of-hand"


def test_attack_spec_accepts_valid_melee_spec():
    spec = AttackSpec(
        name="Shortsword", source="srd:shortsword", ability="dex",
        damage="1d6", damage_type="piercing", ranged=False, range_ft=5,
        properties=["finesse", "light"],
    )
    assert spec.proficient is True          # default
    assert spec.long_range_ft is None       # default


def test_attack_spec_rejects_damage_with_baked_in_modifier():
    with pytest.raises(ValidationError, match="base dice only"):
        AttackSpec(
            name="Shortsword", source="custom", ability="dex",
            damage="1d6+4", damage_type="piercing", ranged=False, range_ft=5,
        )


def test_attack_spec_rejects_missing_ability():
    with pytest.raises(ValidationError):
        AttackSpec(
            name="Shortsword", source="custom",
            damage="1d6", damage_type="piercing", ranged=False, range_ft=5,
        )


def test_proficiencies_normalizes_slugs():
    p = Proficiencies(saves=["dex"], skills=["Stealth"], tools=["thieves_tools"],
                      expertise=["stealth", "thieves_tools"])
    assert p.skills == ["stealth"]
    assert p.tools == ["thieves-tools"]
    assert p.expertise == ["stealth", "thieves-tools"]


def test_proficiencies_rejects_unknown_skill():
    with pytest.raises(ValidationError, match="unknown skills: lockpicking"):
        Proficiencies(saves=[], skills=["lockpicking"])


def test_proficiencies_rejects_expertise_outside_skills_and_tools():
    with pytest.raises(ValidationError, match="expertise not covered"):
        Proficiencies(saves=[], skills=["stealth"], expertise=["athletics"])


def test_proficiencies_rejects_bad_save_ability():
    with pytest.raises(ValidationError):
        Proficiencies(saves=["luck"], skills=[])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_character_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dm_engine.models.character'`

- [ ] **Step 3: Write the models**

```python
# src/dm_engine/models/character.py
"""Validated shapes for character mechanics.

These models are the single valid shape for the `characters.attacks` and
`characters.proficiencies` JSON columns. `create_character` (and the
open-time normalizer in state/migrate.py) guarantee every stored row
conforms, so downstream readers (attack resolver, sheet renderer) may rely
on every field being present and well-formed.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

Ability = Literal["str", "dex", "con", "int", "wis", "cha"]

# The canonical 18 skills (hyphenated slugs) and their governing ability.
# Moved here from commands/checks.py so models and rules can share it
# without importing the command layer.
SKILL_ABILITIES: dict[str, str] = {
    "acrobatics": "dex",
    "animal-handling": "wis",
    "arcana": "int",
    "athletics": "str",
    "deception": "cha",
    "history": "int",
    "insight": "wis",
    "intimidation": "cha",
    "investigation": "int",
    "medicine": "wis",
    "nature": "int",
    "perception": "wis",
    "performance": "cha",
    "persuasion": "cha",
    "religion": "int",
    "sleight-of-hand": "dex",
    "stealth": "dex",
    "survival": "wis",
}

_BASE_DICE_RE = re.compile(r"^\d+d\d+$")


def normalize_slug(value: str) -> str:
    """Canonical slug form: lowercase, underscores/spaces → hyphens."""
    return value.strip().lower().replace("_", "-").replace(" ", "-")


class AttackSpec(BaseModel):
    """One resolved attack. Derivation only ever produces str/dex abilities;
    the wider Ability set is for validated custom attacks (e.g. a WIS-based
    shillelagh) — the resolver handles any ability key."""

    name: str
    source: str = "custom"  # "srd:<weapon-slug>" | "custom"
    ability: Ability
    proficient: bool = True
    damage: str  # base dice only, e.g. "1d6" — modifiers computed at use
    damage_type: str
    ranged: bool
    range_ft: int
    long_range_ft: int | None = None
    properties: list[str] = []

    @field_validator("damage")
    @classmethod
    def _damage_is_base_dice(cls, v: str) -> str:
        if not _BASE_DICE_RE.match(v):
            raise ValueError(
                f"damage must be base dice only, e.g. '1d6' (got {v!r}); "
                "ability modifiers are computed at use"
            )
        return v


class Proficiencies(BaseModel):
    """`saves` is derived from class (callers may not supply it — the
    command layer enforces that); the rest are declared player choices."""

    saves: list[Ability]
    skills: list[str] = []
    expertise: list[str] = []
    tools: list[str] = []
    languages: list[str] = []

    @field_validator("skills", "expertise", "tools", mode="before")
    @classmethod
    def _normalize(cls, v: list[str]) -> list[str]:
        return [normalize_slug(s) for s in v]

    @field_validator("skills")
    @classmethod
    def _known_skills(cls, v: list[str]) -> list[str]:
        unknown = [s for s in v if s not in SKILL_ABILITIES]
        if unknown:
            raise ValueError(f"unknown skills: {', '.join(unknown)}")
        return v

    @model_validator(mode="after")
    def _expertise_covered(self) -> "Proficiencies":
        pool = set(self.skills) | set(self.tools)
        bad = [e for e in self.expertise if e not in pool]
        if bad:
            raise ValueError(
                "expertise not covered by skills/tools: " + ", ".join(bad)
            )
        return self
```

- [ ] **Step 4: Point `commands/checks.py` at the shared skill map**

In `src/dm_engine/commands/checks.py`, delete the `SKILL_ABILITIES` dict literal (lines 22–41) and add to the imports:

```python
from dm_engine.models.character import SKILL_ABILITIES
```

(Keep the name `SKILL_ABILITIES` — other code in that module references it.)

- [ ] **Step 5: Run new tests + existing checks tests**

Run: `uv run pytest tests/test_character_models.py tests/commands/test_checks.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/dm_engine/models/character.py src/dm_engine/commands/checks.py tests/test_character_models.py
git commit -m "feat: add validated character mechanics models"
```

---

### Task 2: Derivation & modifier helpers (`rules/character_build.py`)

**Files:**
- Create: `src/dm_engine/rules/character_build.py`
- Modify: `src/dm_engine/content/lookup.py` (add `get_equipment`)
- Test: `tests/rules/test_character_build.py`

**Interfaces:**
- Consumes: `AttackSpec`, `Proficiencies`, `SKILL_ABILITIES`, `normalize_slug` from Task 1; `ability_modifier`, `proficiency_bonus` from `dm_engine.rules.checks`.
- Produces (later tasks call these with EXACTLY these signatures):
  - `RulesDB.get_equipment(slug: str) -> dict | None`
  - `derive_saves(class_record: dict) -> list[str]`
  - `derive_attack(weapon_record: dict, abilities: dict, class_record: dict, *, name: str | None = None, proficient: bool | None = None) -> list[AttackSpec]`
  - `build_proficiencies(declared: dict, class_record: dict) -> Proficiencies` (raises `ValueError` on caller-supplied saves; pydantic `ValidationError` on bad choices)
  - `attack_to_hit(spec: dict, abilities: dict, level: int) -> int`
  - `attack_damage_mod(spec: dict, abilities: dict) -> int`
  - `skill_modifier(skill: str, proficiencies: dict, abilities: dict, level: int) -> int` (expertise-aware)
  - `tool_bonus(tool: str, proficiencies: dict, level: int) -> int` (expertise-aware, proficiency component only)

- [ ] **Step 1: Add `get_equipment` to the rules DB**

In `src/dm_engine/content/lookup.py`, after `get_class_level` (line 128), add:

```python
    def get_equipment(self, slug: str) -> dict | None:
        row = self._conn.execute(
            "SELECT data FROM equipment WHERE slug=?", (slug,)
        ).fetchone()
        return json.loads(row[0]) if row else None
```

- [ ] **Step 2: Write the failing tests**

SRD record facts these tests rely on (verified against the seeded DB):
dagger = Simple/Melee, 1d4 piercing, props finesse+light+thrown+monk, throw_range 20/60;
longsword = Martial/Melee 1d8; shortbow = Simple/Ranged 1d6, range 80/320;
rogue saves = dex,int; cleric saves = wis,cha; wizard proficiencies include
`daggers` but not `martial-weapons`; fighter has `simple-weapons` + `martial-weapons`.

```python
# tests/rules/test_character_build.py
"""Creation-time derivation: SRD records in, validated specs out."""

import pytest

from dm_engine.content.lookup import RulesDB
from dm_engine.rules.character_build import (
    attack_damage_mod,
    attack_to_hit,
    build_proficiencies,
    derive_attack,
    derive_saves,
    skill_modifier,
    tool_bonus,
)

ROGUE_ABILITIES = {"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10}
BRUTE_ABILITIES = {"str": 18, "dex": 10, "con": 16, "int": 8, "wis": 10, "cha": 8}


@pytest.fixture(scope="module")
def rules(rules_path):
    return RulesDB(rules_path)


def test_derive_saves_from_class(rules):
    assert derive_saves(rules.get_class("rogue")) == ["dex", "int"]
    assert derive_saves(rules.get_class("cleric")) == ["wis", "cha"]


def test_finesse_picks_higher_of_str_dex(rules):
    dagger = rules.get_equipment("dagger")
    rogue_cls = rules.get_class("rogue")
    assert derive_attack(dagger, ROGUE_ABILITIES, rogue_cls)[0].ability == "dex"
    assert derive_attack(dagger, BRUTE_ABILITIES, rogue_cls)[0].ability == "str"


def test_melee_uses_str_ranged_uses_dex(rules):
    fighter = rules.get_class("fighter")
    sword = derive_attack(rules.get_equipment("longsword"), BRUTE_ABILITIES, fighter)[0]
    bow = derive_attack(rules.get_equipment("shortbow"), ROGUE_ABILITIES, fighter)[0]
    assert (sword.ability, sword.ranged, sword.range_ft) == ("str", False, 5)
    assert (bow.ability, bow.ranged, bow.range_ft, bow.long_range_ft) == ("dex", True, 80, 320)
    assert sword.damage == "1d8" and bow.damage == "1d6"


def test_thrown_melee_weapon_emits_second_spec(rules):
    specs = derive_attack(rules.get_equipment("dagger"), ROGUE_ABILITIES, rules.get_class("rogue"))
    assert [s.name for s in specs] == ["Dagger", "Dagger (thrown)"]
    thrown = specs[1]
    assert (thrown.ranged, thrown.range_ft, thrown.long_range_ft) == (True, 20, 60)
    assert thrown.ability == specs[0].ability  # thrown finesse keeps the melee ability


def test_proficiency_matching_category_specific_and_override(rules):
    fighter, wizard = rules.get_class("fighter"), rules.get_class("wizard")
    sword = rules.get_equipment("longsword")
    dagger = rules.get_equipment("dagger")
    assert derive_attack(sword, BRUTE_ABILITIES, fighter)[0].proficient is True    # martial-weapons
    assert derive_attack(sword, BRUTE_ABILITIES, wizard)[0].proficient is False    # no match
    assert derive_attack(dagger, ROGUE_ABILITIES, wizard)[0].proficient is True    # specific: daggers
    forced = derive_attack(sword, BRUTE_ABILITIES, wizard, proficient=True)[0]
    assert forced.proficient is True                                               # override


def test_derive_attack_name_override_and_source(rules):
    spec = derive_attack(
        rules.get_equipment("longsword"), BRUTE_ABILITIES, rules.get_class("fighter"),
        name="Heirloom Blade",
    )[0]
    assert spec.name == "Heirloom Blade"
    assert spec.source == "srd:longsword"


def test_build_proficiencies_refuses_caller_saves(rules):
    with pytest.raises(ValueError, match="derived from class"):
        build_proficiencies({"skills": ["stealth"], "saves": ["cha"]}, rules.get_class("rogue"))
    with pytest.raises(ValueError, match="derived from class"):
        build_proficiencies({"saving_throws": ["cha"]}, rules.get_class("rogue"))


def test_build_proficiencies_derives_saves_and_validates_choices(rules):
    p = build_proficiencies(
        {"skills": ["stealth", "Sleight_of_Hand"], "tools": ["thieves_tools"],
         "expertise": ["stealth", "thieves_tools"], "languages": ["common"]},
        rules.get_class("rogue"),
    )
    assert p.saves == ["dex", "int"]
    assert p.skills == ["stealth", "sleight-of-hand"]
    assert p.expertise == ["stealth", "thieves-tools"]


def test_attack_to_hit_and_damage_mod():
    spec = {"ability": "dex", "proficient": True}
    assert attack_to_hit(spec, ROGUE_ABILITIES, level=1) == 6   # +4 dex, +2 prof
    assert attack_to_hit({**spec, "proficient": False}, ROGUE_ABILITIES, 1) == 4
    assert attack_damage_mod(spec, ROGUE_ABILITIES) == 4


def test_skill_modifier_tiers():
    profs = {"skills": ["stealth", "acrobatics"], "expertise": ["stealth"]}
    assert skill_modifier("stealth", profs, ROGUE_ABILITIES, 1) == 8      # 4 + 2*2
    assert skill_modifier("acrobatics", profs, ROGUE_ABILITIES, 1) == 6   # 4 + 2
    assert skill_modifier("athletics", profs, ROGUE_ABILITIES, 1) == -1   # -1, no prof


def test_tool_bonus_tiers():
    profs = {"tools": ["thieves-tools", "poisoners-kit"], "expertise": ["thieves-tools"]}
    assert tool_bonus("thieves-tools", profs, 1) == 4
    assert tool_bonus("poisoners-kit", profs, 1) == 2
    assert tool_bonus("herbalism-kit", profs, 1) == 0
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_character_build.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dm_engine.rules.character_build'`

- [ ] **Step 4: Write the module**

```python
# src/dm_engine/rules/character_build.py
"""Creation-time derivation of character mechanics from SRD records, plus
the shared modifier math used by BOTH the attack/check resolvers and the
sheet renderer (so displayed and rolled numbers can never diverge).

Pure functions: records/dicts in, values out. No I/O, no store access — the
command layer fetches records and converts ValueError/ValidationError into
structured refusals.
"""

from __future__ import annotations

from dm_engine.models.character import SKILL_ABILITIES, AttackSpec, Proficiencies
from dm_engine.rules.checks import ability_modifier, proficiency_bonus


def derive_saves(class_record: dict) -> list[str]:
    """Save proficiencies are a rules fact: straight from the SRD class."""
    return [s["index"] for s in class_record.get("saving_throws", [])]


def _weapon_proficient(weapon_record: dict, class_record: dict) -> bool:
    """Category match (simple-weapons/martial-weapons) or specific match
    (SRD proficiency indexes are pluralized weapon slugs, e.g. 'daggers')."""
    profs = {p["index"] for p in class_record.get("proficiencies", [])}
    category = weapon_record.get("weapon_category", "").lower()  # simple|martial
    if f"{category}-weapons" in profs:
        return True
    return f"{weapon_record['index']}s" in profs


def derive_attack(
    weapon_record: dict,
    abilities: dict,
    class_record: dict,
    *,
    name: str | None = None,
    proficient: bool | None = None,
) -> list[AttackSpec]:
    props = [p["index"] for p in weapon_record.get("properties", [])]
    is_ranged = weapon_record.get("weapon_range") == "Ranged"
    if "finesse" in props:
        ability = (
            "dex"
            if ability_modifier(abilities["dex"]) >= ability_modifier(abilities["str"])
            else "str"
        )
    else:
        ability = "dex" if is_ranged else "str"
    dmg = weapon_record["damage"]
    rng = weapon_record.get("range", {})
    base = AttackSpec(
        name=name or weapon_record["name"],
        source=f"srd:{weapon_record['index']}",
        ability=ability,
        proficient=(
            _weapon_proficient(weapon_record, class_record)
            if proficient is None
            else proficient
        ),
        damage=dmg["damage_dice"],
        damage_type=dmg["damage_type"]["index"],
        ranged=is_ranged,
        range_ft=rng.get("normal", 5),
        long_range_ft=rng.get("long"),
        properties=props,
    )
    specs = [base]
    throw = weapon_record.get("throw_range")
    if throw and not is_ranged:
        # The resolver's spec shape is single-mode, so a thrown melee weapon
        # is two specs; the thrown profile keeps the melee ability (RAW).
        specs.append(base.model_copy(update={
            "name": f"{base.name} (thrown)",
            "ranged": True,
            "range_ft": throw["normal"],
            "long_range_ft": throw["long"],
        }))
    return specs


def build_proficiencies(declared: dict, class_record: dict) -> Proficiencies:
    if "saves" in declared or "saving_throws" in declared:
        raise ValueError(
            "save proficiencies are derived from class; do not supply them"
        )
    return Proficiencies(
        saves=derive_saves(class_record),
        skills=declared.get("skills", []),
        expertise=declared.get("expertise", []),
        tools=declared.get("tools", []),
        languages=declared.get("languages", []),
    )


# -- shared modifier math (resolvers + sheet renderer) ---------------------


def attack_to_hit(spec: dict, abilities: dict, level: int) -> int:
    mod = ability_modifier(abilities[spec["ability"]])
    return mod + (proficiency_bonus(level) if spec.get("proficient") else 0)


def attack_damage_mod(spec: dict, abilities: dict) -> int:
    return ability_modifier(abilities[spec["ability"]])


def skill_modifier(
    skill: str, proficiencies: dict, abilities: dict, level: int
) -> int:
    modifier = ability_modifier(abilities[SKILL_ABILITIES[skill]])
    if skill in proficiencies.get("skills", []):
        bonus = proficiency_bonus(level)
        if skill in proficiencies.get("expertise", []):
            bonus *= 2
        modifier += bonus
    return modifier


def tool_bonus(tool: str, proficiencies: dict, level: int) -> int:
    """Proficiency component only — the per-check ability is chosen by the
    tool_check command, so the ability modifier is added there."""
    if tool not in proficiencies.get("tools", []):
        return 0
    bonus = proficiency_bonus(level)
    if tool in proficiencies.get("expertise", []):
        bonus *= 2
    return bonus
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_character_build.py tests/test_lookup.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/dm_engine/rules/character_build.py src/dm_engine/content/lookup.py tests/rules/test_character_build.py
git commit -m "feat: derive attacks and saves from SRD data"
```

---

### Task 3: `create_character` derives and validates

**Files:**
- Modify: `src/dm_engine/commands/characters.py` (new attack/proficiency handling in `create_character`)
- Modify: `tests/conftest.py:44-80` (`party` fixture → new input format)
- Test: `tests/commands/test_characters.py` (add refusal + derivation tests)

**Interfaces:**
- Consumes: everything Task 2 produces.
- Produces: the new `create_character` input contract used by all later tasks and by the dm-session skill: `attacks` entries are `{"weapon": <slug>, "name"?: str, "proficient"?: bool}` or `{"custom": {<AttackSpec fields except source>}}`; `proficiencies` = `{skills?, expertise?, tools?, languages?}` (no `saves`). Stored rows conform to `AttackSpec.model_dump()` / `Proficiencies.model_dump()`.

- [ ] **Step 1: Write the failing tests** (append to `tests/commands/test_characters.py`; follow the module's existing style of executing via `registry.execute`)

```python
# --- character mechanics: derived attacks/saves (append) -------------------

ROGUE_KWARGS = dict(
    name="Sable", role="pc", class_slug="rogue", race_slug="wood-elf",
    abilities={"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10},
    ac=15, speed=35,
    proficiencies={"skills": ["stealth", "acrobatics"], "tools": ["thieves_tools"],
                   "expertise": ["stealth", "thieves_tools"]},
    attacks=[{"weapon": "shortsword"}, {"weapon": "dagger"}],
)


def test_create_character_derives_weapon_attacks(ctx):
    result = registry.execute("create_character", ctx, **ROGUE_KWARGS)
    assert result.ok
    char = ctx.store.get_character("Sable")
    by_name = {a["name"]: a for a in char["attacks"]}
    # dagger is thrown → two specs
    assert set(by_name) == {"Shortsword", "Dagger", "Dagger (thrown)"}
    sword = by_name["Shortsword"]
    assert (sword["ability"], sword["proficient"], sword["damage"]) == ("dex", True, "1d6")
    assert sword["source"] == "srd:shortsword"
    assert by_name["Dagger (thrown)"]["range_ft"] == 20


def test_create_character_derives_saves_from_class(ctx):
    registry.execute("create_character", ctx, **ROGUE_KWARGS)
    profs = ctx.store.get_character("Sable")["proficiencies"]
    assert profs["saves"] == ["dex", "int"]
    assert profs["expertise"] == ["stealth", "thieves-tools"]  # normalized


def test_create_character_refuses_caller_saves(ctx):
    kwargs = {**ROGUE_KWARGS,
              "proficiencies": {"skills": ["stealth"], "saves": ["cha"]}}
    result = registry.execute("create_character", ctx, **kwargs)
    assert not result.ok
    assert "derived from class" in result.refusal


def test_create_character_refuses_unknown_weapon(ctx):
    kwargs = {**ROGUE_KWARGS, "attacks": [{"weapon": "vorpal-zweihander"}]}
    result = registry.execute("create_character", ctx, **kwargs)
    assert not result.ok
    assert "vorpal-zweihander" in result.refusal


def test_create_character_refuses_unknown_skill(ctx):
    kwargs = {**ROGUE_KWARGS, "proficiencies": {"skills": ["lockpicking"]}}
    result = registry.execute("create_character", ctx, **kwargs)
    assert not result.ok
    assert "lockpicking" in result.refusal


def test_create_character_accepts_valid_custom_attack(ctx):
    kwargs = {**ROGUE_KWARGS, "attacks": [{"custom": {
        "name": "Cursed Fang", "ability": "dex", "damage": "1d6",
        "damage_type": "necrotic", "ranged": False, "range_ft": 5}}]}
    result = registry.execute("create_character", ctx, **kwargs)
    assert result.ok
    atk = ctx.store.get_character("Sable")["attacks"][0]
    assert atk["source"] == "custom"


def test_create_character_refuses_malformed_custom_attack(ctx):
    kwargs = {**ROGUE_KWARGS, "attacks": [{"custom": {
        "name": "Bad", "ability": "dex", "damage": "1d6+4",
        "damage_type": "piercing", "ranged": False, "range_ft": 5}}]}
    result = registry.execute("create_character", ctx, **kwargs)
    assert not result.ok
    assert "base dice only" in result.refusal


def test_create_character_refuses_attack_entry_without_weapon_or_custom(ctx):
    kwargs = {**ROGUE_KWARGS, "attacks": [{"name": "Shortsword", "attack_bonus": 6}]}
    result = registry.execute("create_character", ctx, **kwargs)
    assert not result.ok
    assert "'weapon' or 'custom'" in result.refusal
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/commands/test_characters.py -v`
Expected: the new tests FAIL (old create_character stores blobs / doesn't refuse); pre-existing tests still pass.

- [ ] **Step 3: Implement in `commands/characters.py`**

Add imports:

```python
from pydantic import ValidationError

from dm_engine.models.character import AttackSpec, normalize_slug
from dm_engine.rules.character_build import build_proficiencies, derive_attack
```

Add this helper above `create_character`:

```python
def _resolve_attacks(
    ctx: CommandContext, entries: list, abilities: dict, class_record: dict
) -> tuple[list[dict] | None, str | None]:
    """Resolve declared attack entries into stored AttackSpec dicts.
    Returns (specs, None) on success, (None, refusal_reason) on bad input."""
    specs: list[AttackSpec] = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            return None, f"attacks[{i}] must be an object"
        if "weapon" in entry:
            slug = normalize_slug(entry["weapon"])
            record = ctx.rules.get_equipment(slug)
            if record is None or "damage" not in record:
                return None, (
                    f"unknown weapon {entry['weapon']!r} — no SRD equipment "
                    "record with damage; use a {'custom': {...}} attack instead"
                )
            specs.extend(derive_attack(
                record, abilities, class_record,
                name=entry.get("name"), proficient=entry.get("proficient"),
            ))
        elif "custom" in entry:
            try:
                specs.append(AttackSpec(**{**entry["custom"], "source": "custom"}))
            except ValidationError as exc:
                first = exc.errors()[0]
                return None, (
                    f"attacks[{i}] invalid custom spec: "
                    f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}"
                )
        else:
            return None, f"attacks[{i}] must have a 'weapon' or 'custom' key"
    names = [s.name for s in specs]
    if len(names) != len(set(names)):
        return None, f"duplicate attack names: {', '.join(sorted(set(n for n in names if names.count(n) > 1)))}"
    return [s.model_dump() for s in specs], None
```

In `create_character`, after the existing ability validations (line 67) and before the PC-uniqueness check, add:

```python
    try:
        profs = build_proficiencies(proficiencies, class_record)
    except (ValueError, ValidationError) as exc:
        msg = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
        return refuse("create_character", f"invalid proficiencies: {msg}")
    resolved_attacks, reason = _resolve_attacks(ctx, attacks, abilities, class_record)
    if reason:
        return refuse("create_character", reason)
```

and change the `insert_character` call to store the resolved forms:

```python
        proficiencies=profs.model_dump(), attacks=resolved_attacks,
```

- [ ] **Step 4: Update the `party` fixture in `tests/conftest.py`**

Replace the two `create_character` calls' `proficiencies`/`attacks` kwargs (keep the lowercase attack names via `name` overrides so existing `attack_name="longsword"` assertions elsewhere keep working):

```python
        proficiencies={"skills": ["athletics", "intimidation"]},
        attacks=[{"weapon": "longsword", "name": "longsword"}],
```

for Kira, and for Brother Aldric:

```python
        proficiencies={"skills": ["medicine", "religion"]},
        attacks=[{"weapon": "mace", "name": "mace"}],
```

(Fighter derives saves str/con and cleric wis/cha — identical to what the old fixture declared, so no downstream assertions change. Longsword/mace damage dice and STR ability also match the old inline specs.)

- [ ] **Step 5: Run command + state suites**

Run: `uv run pytest tests/commands tests/state -v`
Expected: all PASS. If a test fails because it constructed characters with raw attack dicts directly (not via the fixture), update that construction to the new format in this step — the sweep of *integration* tests happens in Task 8.

- [ ] **Step 6: Commit**

```bash
git add src/dm_engine/commands/characters.py tests/conftest.py tests/commands/test_characters.py
git commit -m "feat: create_character derives and validates"
```

---

### Task 4: Expertise in `skill_check`

**Files:**
- Modify: `src/dm_engine/commands/checks.py:169-172` (use shared `skill_modifier`)
- Test: `tests/commands/test_checks.py` (append)

**Interfaces:**
- Consumes: `skill_modifier` from Task 2; new create_character format from Task 3 (test fixture).

- [ ] **Step 1: Write the failing test** (append to `tests/commands/test_checks.py`)

```python
def test_skill_check_expertise_doubles_proficiency(ctx):
    registry.execute(
        "create_character", ctx, name="Sable", role="pc",
        class_slug="rogue", race_slug="wood-elf",
        abilities={"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10},
        ac=15, speed=35,
        proficiencies={"skills": ["stealth", "acrobatics"], "expertise": ["stealth"]},
        attacks=[{"weapon": "shortsword"}],
    )
    # player_value pins the d20 so the assertion is pure modifier math
    expert = registry.execute("skill_check", ctx, character="Sable",
                              skill="stealth", dc=10, player_value=10)
    assert expert.data["modifier"] == 8          # +4 dex +2 prof ×2
    assert expert.data["total"] == 18
    merely_proficient = registry.execute("skill_check", ctx, character="Sable",
                                         skill="acrobatics", dc=10, player_value=10)
    assert merely_proficient.data["modifier"] == 6
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/commands/test_checks.py::test_skill_check_expertise_doubles_proficiency -v`
Expected: FAIL — `assert 6 == 8` (expertise ignored)

- [ ] **Step 3: Use the shared helper**

In `src/dm_engine/commands/checks.py`, add import `from dm_engine.rules.character_build import skill_modifier` and replace lines 169–172:

```python
    ability = SKILL_ABILITIES[skill]
    modifier = ability_modifier(char["abilities"][ability])
    if skill in char["proficiencies"].get("skills", []):
        modifier += proficiency_bonus(char["level"])
```

with:

```python
    modifier = skill_modifier(skill, char["proficiencies"], char["abilities"], char["level"])
```

(The `ability` local is only used for the modifier; delete it if now unused.)

- [ ] **Step 4: Run the checks suite**

Run: `uv run pytest tests/commands/test_checks.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/dm_engine/commands/checks.py tests/commands/test_checks.py
git commit -m "feat: expertise doubles skill proficiency"
```

---

### Task 5: `tool_check` command

**Files:**
- Modify: `src/dm_engine/commands/checks.py` (new command, after `skill_check`)
- Test: `tests/commands/test_checks.py` (append)

**Interfaces:**
- Consumes: `tool_bonus` (Task 2), `normalize_slug` (Task 1), existing `_validate_player_value`, `_ABILITIES`, `_label`, `resolve_check`.
- Produces: registry command `tool_check(character: str, tool: str, ability: str, dc: int, advantage=False, disadvantage=False, player_value=None, gm_only=False)`. MCP/CLI expose it automatically (tools are generated from the registry, `mcp/server.py:144-152`).

- [ ] **Step 1: Write the failing tests** (append to `tests/commands/test_checks.py`)

```python
def _make_rogue(ctx, expertise=("thieves_tools",)):
    registry.execute(
        "create_character", ctx, name="Sable", role="pc",
        class_slug="rogue", race_slug="wood-elf",
        abilities={"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10},
        ac=15, speed=35,
        proficiencies={"skills": ["stealth"], "tools": ["thieves_tools"],
                       "expertise": list(expertise)},
        attacks=[{"weapon": "shortsword"}],
    )


def test_tool_check_expertise_and_explicit_ability(ctx):
    _make_rogue(ctx)
    result = registry.execute("tool_check", ctx, character="Sable",
                              tool="thieves_tools", ability="dex", dc=15,
                              player_value=10)
    assert result.ok
    assert result.data["modifier"] == 8            # +4 dex, +2 prof ×2
    assert result.data["total"] == 18
    assert result.data["success"] is True
    # same tool, different ability: recalling trap designs with INT
    brainy = registry.execute("tool_check", ctx, character="Sable",
                              tool="thieves_tools", ability="int", dc=10,
                              player_value=10)
    assert brainy.data["modifier"] == 4            # +0 int, +2 prof ×2


def test_tool_check_unproficient_gets_bare_ability(ctx):
    _make_rogue(ctx, expertise=())
    result = registry.execute("tool_check", ctx, character="Sable",
                              tool="herbalism_kit", ability="wis", dc=10,
                              player_value=10)
    assert result.data["modifier"] == 1            # bare WIS


def test_tool_check_refuses_bad_inputs(ctx):
    _make_rogue(ctx)
    assert not registry.execute("tool_check", ctx, character="Nobody",
                                tool="thieves_tools", ability="dex", dc=10).ok
    assert not registry.execute("tool_check", ctx, character="Sable",
                                tool="thieves_tools", ability="luck", dc=10).ok
    assert not registry.execute("tool_check", ctx, character="Sable",
                                tool="thieves_tools", ability="dex", dc=0).ok
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/commands/test_checks.py -k tool_check -v`
Expected: FAIL — `KeyError: 'tool_check'` (unknown command)

- [ ] **Step 3: Implement** (in `src/dm_engine/commands/checks.py`, after `skill_check`; add imports `from dm_engine.models.character import SKILL_ABILITIES, normalize_slug` — merging with the Task 1 import — and `from dm_engine.rules.character_build import skill_modifier, tool_bonus`)

```python
@command("tool_check")
def tool_check(
    ctx: CommandContext,
    character: str,
    tool: str,
    ability: str,
    dc: int,
    advantage: bool = False,
    disadvantage: bool = False,
    player_value: int | None = None,
    gm_only: bool = False,
    **kwargs,
) -> CommandResult:
    """Tool proficiency check. Tools have no fixed ability in RAW (thieves'
    tools + DEX to pick a lock, + INT to recall trap designs), so the
    ability is an explicit argument."""
    char = ctx.store.get_character(character)
    if char is None:
        return refuse("tool_check", f"no character named {character!r}")
    if ability not in _ABILITIES:
        return refuse("tool_check", f"unknown ability {ability!r}")
    if dc < 1:
        return refuse("tool_check", f"dc must be >= 1 (got {dc})")
    reason = _validate_player_value(char, player_value)
    if reason:
        return refuse("tool_check", reason)

    tool_slug = normalize_slug(tool)
    modifier = ability_modifier(char["abilities"][ability]) + tool_bonus(
        tool_slug, char["proficiencies"], char["level"]
    )
    mode = combine_advantage(advantage, disadvantage)
    check = resolve_check(
        ctx.roller, modifier, dc, mode, player_value=player_value, gm_only=gm_only
    )
    data = {
        "tool": tool_slug,
        "ability": ability,
        "modifier": modifier,
        "dc": dc,
        "natural": check.d20.natural,
        "total": check.d20.total,
        "success": check.success,
        "margin": check.margin,
    }
    outcome = "success" if check.success else "failure"
    digest = (
        f"{character} {_label(tool_slug)} ({ability.upper()}) check: "
        f"{check.d20.total} vs DC {dc} — {outcome}"
    )
    return CommandResult(
        ok=True, command="tool_check", digest=digest, data=data, gm_only=gm_only
    )
```

- [ ] **Step 4: Run checks suite + MCP smoke**

Run: `uv run pytest tests/commands/test_checks.py tests/test_mcp_schema.py tests/integration/test_mcp_smoke.py -v`
Expected: all PASS (MCP tools are generated from the registry, so `tool_check` appears automatically; if a test pins an exact tool COUNT, bump it by one).

- [ ] **Step 5: Commit**

```bash
git add src/dm_engine/commands/checks.py tests/commands/test_checks.py
git commit -m "feat: add tool_check command"
```

---

### Task 6: Attack resolver uses shared math + refuses invalid stored specs

**Files:**
- Modify: `src/dm_engine/commands/attacks.py:245-264` (character attack-spec branch)
- Test: `tests/commands/test_attacks.py` (append one test)

**Interfaces:**
- Consumes: `attack_to_hit`, `attack_damage_mod` (Task 2), `AttackSpec` (Task 1).
- Produces: attack resolution numerically identical to before for valid specs (existing tests are the regression harness); invalid stored specs → refusal, not KeyError.

- [ ] **Step 1: Write the failing test** (append to `tests/commands/test_attacks.py`; use the module's existing combat-setup helpers/fixtures)

```python
def test_attack_with_invalid_stored_spec_refuses_not_crashes(party):
    """A stored spec that predates validation (or survived migration
    untouched) must refuse cleanly on use, never KeyError mid-combat."""
    ctx = party
    kira = ctx.store.get_character("Kira")
    ctx.store.update_character(
        kira["id"],
        attacks=[{"name": "haunted-blade", "attack_bonus": 6, "damage": "1d6+4"}],
    )
    ctx.store.conn.commit()
    _start_goblin_combat(ctx)  # use this module's existing combat-setup helper
    result = registry.execute("attack", ctx, attacker="Kira",
                              target="goblin-1", attack_name="haunted-blade")
    assert not result.ok
    assert "haunted-blade" in result.refusal and "Kira" in result.refusal
    assert "invalid" in result.refusal
```

(Adapt the combat-setup line to whatever helper `tests/commands/test_attacks.py` already uses to enter combat — read the file first; do not invent a new helper.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/commands/test_attacks.py -k invalid_stored_spec -v`
Expected: FAIL — `KeyError: 'ability'` escapes (crash, not refusal)

- [ ] **Step 3: Refactor the character branch** (`src/dm_engine/commands/attacks.py`)

Add imports:

```python
from pydantic import ValidationError

from dm_engine.models.character import AttackSpec
from dm_engine.rules.character_build import attack_damage_mod, attack_to_hit
```

Replace lines 254–260 (from `abil_mod = ...` through `damage_type = spec["damage_type"]`):

```python
        try:
            AttackSpec(**spec)
        except ValidationError:
            return refuse(
                "attack",
                f"{attacker}'s attack {attack_name!r} has an invalid stored "
                "spec (pre-validation data?); recreate it or fix via migration",
            )
        attack_bonus = attack_to_hit(spec, char["abilities"], char["level"])
        dmg_mod = attack_damage_mod(spec, char["abilities"])
        sign = "+" if dmg_mod >= 0 else "-"
        damage_notation = f"{spec['damage']}{sign}{abs(dmg_mod)}"
        damage_type = spec["damage_type"]
```

(Leave the subsequent `spec_ranged`/`range_ft`/`long_range_ft`/`is_pc` lines untouched. Remove the now-unused `proficiency_bonus` import if nothing else in the module uses it.)

- [ ] **Step 4: Run the attack + combat suites (regression)**

Run: `uv run pytest tests/commands/test_attacks.py tests/commands/test_combat.py -v`
Expected: all PASS — identical numbers for valid specs, refusal for the invalid one.

- [ ] **Step 5: Commit**

```bash
git add src/dm_engine/commands/attacks.py tests/commands/test_attacks.py
git commit -m "feat: attack resolver shares to-hit math"
```

---

### Task 7: Full character sheet

**Files:**
- Modify: `src/dm_engine/state/sheets.py:115-140` (Proficiencies + Attacks sections → Saving Throws / Skills / Tools / Attacks)
- Test: `tests/state/test_sheets.py`

**Interfaces:**
- Consumes: `skill_modifier`, `tool_bonus`, `attack_to_hit`, `attack_damage_mod` (Task 2), `SKILL_ABILITIES` (Task 1). Stored rows are valid post-Task-3, and `render_character_sheet` keeps its "store state only, no rules DB" property.

- [ ] **Step 1: Write the failing tests** (replace/extend `tests/state/test_sheets.py`'s sheet-content assertions; read the existing file and keep its fixture pattern)

```python
def test_sheet_renders_full_saves_skills_tools_attacks(party):
    ctx = party
    registry.execute(  # replace the PC-less fixture char with a rich rogue? No —
        # party() already has Kira; create the rogue as companion for rendering.
        "create_character", ctx, name="Sable", role="companion",
        class_slug="rogue", race_slug="wood-elf",
        abilities={"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10},
        ac=15, speed=35,
        proficiencies={"skills": ["stealth", "acrobatics", "perception"],
                       "tools": ["thieves_tools"],
                       "expertise": ["stealth", "thieves_tools"]},
        attacks=[{"weapon": "shortsword"}, {"weapon": "shortbow"}],
    )
    md = render_character_sheet(ctx.store, ctx.store.get_character("Sable")["id"])

    # Saving throws: all six, proficient first with filled markers
    assert "## Saving Throws" in md
    assert "◉ DEX +6" in md and "◉ INT +2" in md
    assert "○ STR -1" in md and "○ CON +1" in md and "○ WIS +1" in md and "○ CHA +0" in md

    # Skills: all 18, expertise/proficient/plain tiers, passive perception
    assert "## Skills" in md
    assert "◉◉ Stealth +8 (expertise)" in md
    assert "◉ Acrobatics +6" in md
    assert "○ Athletics -1" in md
    assert md.count("◉") >= 6 and "Animal Handling" in md   # full 18 present
    assert "Passive Perception: 13" in md                   # 10 + (1 wis + 2 prof)

    # Tools
    assert "## Tools" in md
    assert "◉◉ thieves-tools (prof +4)" in md

    # Attacks: computed to-hit, annotations
    assert "Shortsword: +6 to hit, 1d6+4 piercing (finesse)" in md
    assert "Shortbow: +6 to hit, 1d6+4 piercing (80/320)" in md


def test_sheet_saves_section_replaces_old_proficiencies_block(party):
    ctx = party
    md = render_character_sheet(ctx.store, ctx.store.get_character("Kira")["id"])
    assert "## Proficiencies" not in md
    assert "◉ STR" in md and "◉ CON" in md      # fighter's derived saves
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/state/test_sheets.py -v`
Expected: new tests FAIL (old compact sections)

- [ ] **Step 3: Implement the new sections** (`src/dm_engine/state/sheets.py`)

Add imports:

```python
from dm_engine.models.character import SKILL_ABILITIES
from dm_engine.rules.character_build import (
    attack_damage_mod,
    attack_to_hit,
    skill_modifier,
    tool_bonus,
)
```

Replace the `# Proficiencies` and `# Attacks` blocks (lines 115–140) with:

```python
    profs = char["proficiencies"]

    # Saving throws — all six, proficient first
    save_profs = profs.get("saves", [])
    lines.append("## Saving Throws")
    def _save_entry(ability: str) -> str:
        marker = "◉" if ability in save_profs else "○"
        mod = ability_modifier(abilities[ability]) + (
            prof if ability in save_profs else 0
        )
        return f"{marker} {ability.upper()} {_fmt_mod(mod)}"
    proficient = [a for a in _ABILITY_ORDER if a in save_profs]
    plain = [a for a in _ABILITY_ORDER if a not in save_profs]
    if proficient:
        lines.append("- " + "   ".join(_save_entry(a) for a in proficient))
    lines.append("- " + "   ".join(_save_entry(a) for a in plain))
    lines.append("")

    # Skills — all 18: expertise, then proficient, then the rest
    skill_list = profs.get("skills", [])
    expertise = profs.get("expertise", [])
    lines.append("## Skills")
    def _skill_rank(s: str) -> tuple:
        return (s not in expertise, s not in skill_list, s)
    for skill in sorted(SKILL_ABILITIES, key=_skill_rank):
        mod = skill_modifier(skill, profs, abilities, level)
        label = skill.replace("-", " ").title()
        if skill in expertise:
            lines.append(f"- ◉◉ {label} {_fmt_mod(mod)} (expertise)")
        elif skill in skill_list:
            lines.append(f"- ◉ {label} {_fmt_mod(mod)}")
        else:
            lines.append(f"- ○ {label} {_fmt_mod(mod)}")
    passive = 10 + skill_modifier("perception", profs, abilities, level)
    lines.append(f"- Passive Perception: {passive}")
    lines.append("")

    # Tools — proficiency component only (ability chosen per check)
    tools = profs.get("tools", [])
    if tools:
        lines.append("## Tools")
        for tool in tools:
            bonus = tool_bonus(tool, profs, level)
            marker = "◉◉" if tool in expertise else "◉"
            lines.append(f"- {marker} {tool} (prof {_fmt_mod(bonus)})")
        lines.append("")

    # Attacks — computed exactly as the resolver computes them
    lines.append("## Attacks")
    if char["attacks"]:
        for atk in char["attacks"]:
            to_hit = attack_to_hit(atk, abilities, level)
            dmg_mod = attack_damage_mod(atk, abilities)
            damage = atk["damage"] + (_fmt_mod(dmg_mod) if dmg_mod else "")
            if atk.get("ranged") and atk.get("long_range_ft"):
                annot = f" ({atk['range_ft']}/{atk['long_range_ft']})"
            elif "finesse" in atk.get("properties", []):
                annot = " (finesse)"
            else:
                annot = ""
            lines.append(
                f"- {atk['name']}: {_fmt_mod(to_hit)} to hit, "
                f"{damage} {atk['damage_type']}{annot}"
            )
    else:
        lines.append("- none")
    lines.append("")
```

(Delete the old `# Proficiencies` block entirely; languages can ride in Tools' section later if wanted — YAGNI now. Note the docstring's "no rules DB" property still holds: everything comes from stored state.)

- [ ] **Step 4: Run state + command suites**

Run: `uv run pytest tests/state tests/commands -v`
Expected: all PASS (fix any command tests that asserted the old sheet markdown, e.g. in `create_character` payload assertions).

- [ ] **Step 5: Commit**

```bash
git add src/dm_engine/state/sheets.py tests/state/test_sheets.py
git commit -m "feat: render full 5e character sheet"
```

---

### Task 8: Open-time migration normalizer

**Files:**
- Create: `src/dm_engine/state/migrate.py`
- Modify: `src/dm_engine/commands/registry.py:110-123` (`open_campaign_context` runs it)
- Test: `tests/state/test_migrate.py`

**Interfaces:**
- Consumes: `AttackSpec`, `Proficiencies`, `normalize_slug` (Task 1); `derive_attack`, `derive_saves` (Task 2); `CampaignStore`, `RulesDB`.
- Produces: `normalize_characters(store: CampaignStore, rules: RulesDB) -> list[str]` (human-readable change notes; empty list = nothing to do). Runs inside `open_campaign_context` after `CampaignStore.open`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/state/test_migrate.py
"""Pre-validation campaign rows are normalized once on open. Idempotent;
rows that can't be fixed are left for a clean on-use refusal (Task 6)."""

import json

import pytest

from dm_engine.content.lookup import RulesDB
from dm_engine.state.migrate import normalize_characters
from dm_engine.state.store import CampaignStore


@pytest.fixture()
def old_campaign(tmp_path):
    store = CampaignStore.create(
        tmp_path / "campaigns", slug="old", name="Old", death_mode="narrative",
        rng_seed=7, skeleton={"premise": "t"},
    )
    # Insert a row exactly as the pre-fix engine stored it: monster-style
    # attacks, `saving_throws` key, underscore slugs.
    store.conn.execute(
        "INSERT INTO characters (name, role, class_slug, race_slug, level,"
        " abilities, max_hp, ac, speed, proficiencies, attacks, spells_known)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("Algarve", "pc", "rogue", "wood-elf", 1,
         json.dumps({"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10}),
         9, 15, 35,
         json.dumps({"saving_throws": ["dex", "int"],
                     "skills": ["stealth", "acrobatics"],
                     "expertise": ["stealth", "thieves_tools"],
                     "tools": ["thieves_tools"], "languages": ["common"]}),
         json.dumps([
             {"name": "Shortsword", "attack_bonus": 6, "damage": "1d6+4",
              "damage_type": "piercing"},
             {"name": "Void Lash", "attack_bonus": 9, "damage": "6d6+4",
              "damage_type": "necrotic"},  # no SRD weapon — must survive untouched
         ]),
         json.dumps([])),
    )
    store.conn.execute(
        "INSERT INTO resources (character_id, hp, hit_dice_remaining, spell_slots)"
        " VALUES (1, 9, 1, '{}')",
    )
    store.conn.commit()
    yield store
    store.close()


def test_normalizer_fixes_old_rows(old_campaign, rules_path):
    changes = normalize_characters(old_campaign, RulesDB(rules_path))
    assert changes  # something was fixed
    char = old_campaign.get_character("Algarve")
    profs = char["proficiencies"]
    assert profs["saves"] == ["dex", "int"]
    assert "saving_throws" not in profs
    assert profs["tools"] == ["thieves-tools"]           # slug normalized
    assert profs["expertise"] == ["stealth", "thieves-tools"]
    by_name = {a["name"]: a for a in char["attacks"]}
    sword = by_name["Shortsword"]
    assert (sword["ability"], sword["damage"], sword["source"]) == (
        "dex", "1d6", "srd:shortsword")                  # re-derived from SRD
    assert by_name["Void Lash"] == {                     # untouched, refuses on use
        "name": "Void Lash", "attack_bonus": 9, "damage": "6d6+4",
        "damage_type": "necrotic"}


def test_normalizer_is_idempotent(old_campaign, rules_path):
    rules = RulesDB(rules_path)
    normalize_characters(old_campaign, rules)
    assert normalize_characters(old_campaign, rules) == []


def test_normalizer_noop_on_valid_rows(tmp_path, rules_path):
    store = CampaignStore.create(
        tmp_path / "c", slug="new", name="N", death_mode="narrative",
        rng_seed=7, skeleton={"premise": "t"},
    )
    assert normalize_characters(store, RulesDB(rules_path)) == []
    store.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/state/test_migrate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dm_engine.state.migrate'`

- [ ] **Step 3: Implement**

```python
# src/dm_engine/state/migrate.py
"""One-time normalizer for pre-validation character rows (2026-07 format
bug: monster-style attack specs, `saving_throws` key, underscore slugs).

Idempotent and cheap, so it simply runs on every campaign open. Rows it
cannot confidently fix are left untouched — the attack resolver refuses
them cleanly on use. Deletable once no pre-fix campaigns exist.
"""

from __future__ import annotations

from pydantic import ValidationError

from dm_engine.content.lookup import RulesDB
from dm_engine.models.character import AttackSpec, Proficiencies, normalize_slug
from dm_engine.rules.character_build import derive_attack, derive_saves
from dm_engine.state.store import CampaignStore

_PROF_KEYS = ("saves", "skills", "expertise", "tools", "languages")


def _normalize_proficiencies(char: dict, rules: RulesDB) -> dict | None:
    profs = dict(char["proficiencies"])
    if "saving_throws" in profs:
        profs.setdefault("saves", profs.pop("saving_throws"))
    if not profs.get("saves"):
        record = rules.get_class(char["class_slug"])
        if record:
            profs["saves"] = derive_saves(record)
    try:
        normalized = Proficiencies(
            **{k: profs.get(k, []) for k in _PROF_KEYS}
        ).model_dump()
    except ValidationError:
        return None  # unfixable — leave for on-use refusals
    return normalized if normalized != char["proficiencies"] else None


def _normalize_attacks(char: dict, rules: RulesDB) -> list[dict] | None:
    out: list[dict] = []
    changed = False
    for spec in char["attacks"]:
        try:
            valid = AttackSpec(**spec).model_dump()
            out.append(valid)
            changed = changed or valid != spec
            continue
        except ValidationError:
            pass
        record = rules.get_equipment(normalize_slug(spec.get("name", "")))
        class_record = rules.get_class(char["class_slug"]) or {}
        if record and "damage" in record:
            out.extend(
                s.model_dump()
                for s in derive_attack(record, char["abilities"], class_record)
            )
            changed = True
        else:
            out.append(spec)  # unfixable — refuses on use
    return out if changed else None


def normalize_characters(store: CampaignStore, rules: RulesDB) -> list[str]:
    """Returns one human-readable note per fixed character (empty = no-op)."""
    changes: list[str] = []
    ids = [r[0] for r in store.conn.execute("SELECT id FROM characters")]
    with store.transaction():
        for cid in ids:
            char = store.get_character_by_id(cid)
            fields: dict = {}
            new_profs = _normalize_proficiencies(char, rules)
            if new_profs is not None:
                fields["proficiencies"] = new_profs
            new_attacks = _normalize_attacks(char, rules)
            if new_attacks is not None:
                fields["attacks"] = new_attacks
            if fields:
                store.update_character(cid, **fields)
                changes.append(
                    f"{char['name']}: normalized {', '.join(sorted(fields))}"
                )
    return changes
```

And in `src/dm_engine/commands/registry.py`, in `open_campaign_context` (line 113), after `store = CampaignStore.open(...)` and before reading meta:

```python
    rules = RulesDB(rules_db_path)
    normalize_characters(store, rules)
```

with imports `from dm_engine.state.migrate import normalize_characters` — and change the final line to reuse the `rules` instance:

```python
    return CommandContext(store=store, roller=roller, rules=rules)
```

(Import note: `migrate.py` imports from `state.store`; `registry.py` already imports both — no cycle, since `store.py` imports nothing from `commands/`.)

- [ ] **Step 4: Run state suite + full command suite**

Run: `uv run pytest tests/state tests/commands -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/dm_engine/state/migrate.py src/dm_engine/commands/registry.py tests/state/test_migrate.py
git commit -m "feat: normalize legacy character rows on open"
```

---

### Task 9: Integration sweep + e2e coverage

**Files:**
- Modify: `tests/integration/test_e2e_campaign_lifecycle.py`, `test_e2e_combat_goblin_ambush.py`, `test_e2e_death_modes.py`, `test_e2e_resume_rehydration.py` (and any other integration file passing old-format `create_character` kwargs — find them with `rg -l '"saves"' tests/integration`)
- Test: `tests/integration/test_e2e_character_mechanics.py` (new)

**Interfaces:**
- Consumes: the Task 3 input contract; Task 5 `tool_check`; Task 7 sheet sections.

- [ ] **Step 1: Sweep old-format creations**

In each integration file, update every `create_character` call:
- drop `"saves": [...]` from `proficiencies` (fighter/cleric saves derive identically, so no behavioral assertions change);
- replace inline attack dicts with `{"weapon": "<slug>", "name": "<old lowercase name>"}` (e.g. `attacks=[{"weapon": "longsword", "name": "longsword"}]`) so `attack_name=` references keep resolving.

Run after each file: `uv run pytest tests/integration/<file> -v` — expected PASS. Seed-dependent damage assertions must NOT change (ability/proficiency/dice are identical before and after); if one does change, the derivation is wrong — stop and fix the engine, not the test.

- [ ] **Step 2: Write the new e2e test**

```python
# tests/integration/test_e2e_character_mechanics.py
"""E2E: weapon-derived attacks resolve in combat, expertise applies in and
out of combat, and the materialized sheet shows real-5e-sheet sections.
Exercises the engine exactly as the LLM does: registry.execute only."""

from dm_engine.commands import registry


def _run(ctx, name, **kwargs):
    result = registry.execute(name, ctx, **kwargs)
    assert result.ok, f"{name} refused: {result.refusal}"
    return result


def test_derived_rogue_fights_sneaks_and_picks_locks(ctx, tmp_path):
    _run(ctx, "create_character", name="Sable", role="pc",
         class_slug="rogue", race_slug="wood-elf",
         abilities={"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10},
         ac=15, speed=35,
         proficiencies={"skills": ["stealth", "perception"], "tools": ["thieves_tools"],
                        "expertise": ["stealth", "thieves_tools"]},
         attacks=[{"weapon": "shortbow"}, {"weapon": "dagger"}])

    # Sheet materialized with derived numbers
    sheet = (ctx.store.root / "sheets" / "sable.md").read_text()
    assert "Shortbow: +6 to hit, 1d6+4 piercing (80/320)" in sheet
    assert "◉ DEX +6" in sheet and "◉ INT +2" in sheet
    assert "◉◉ Stealth +8 (expertise)" in sheet

    # Expertise out of combat: stealth at +8, lockpicking at +8
    check = _run(ctx, "skill_check", character="Sable", skill="stealth",
                 dc=15, player_value=10)
    assert check.data["total"] == 18
    lock = _run(ctx, "tool_check", character="Sable", tool="thieves_tools",
                ability="dex", dc=15, player_value=11)
    assert lock.data["total"] == 19 and lock.data["success"]

    # Derived shortbow resolves in combat from `near` (engine-rolled, fixed seed)
    _run(ctx, "start_combat",
         monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
         pc_initiative=20)
    result = _run(ctx, "attack", attacker="Sable", target="goblin-1",
                  attack_name="Shortbow")
    assert result.data["attack"]["modifier"] == 6   # derived to-hit reached combat
```

(Verify the `attack` result's data key layout against `tests/commands/test_attacks.py` before finalizing the last assertion — mirror how those tests read the roll payload. Same for `start_combat` kwargs — copy the shape used in `test_e2e_combat_goblin_ambush.py`.)

- [ ] **Step 3: Run the whole integration suite**

Run: `uv run pytest tests/integration -v`
Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add tests/integration
git commit -m "test: e2e derived mechanics coverage"
```

---

### Task 10: dm-session skill doc + full-suite gate

**Files:**
- Modify: `.claude/skills/dm-session/SKILL.md` (campaign-creation section)

**Interfaces:**
- Consumes: the Task 3 input contract (documents it for future DM sessions).

- [ ] **Step 1: Update the skill doc**

In `.claude/skills/dm-session/SKILL.md`, in the "Campaign creation" section after the `create_character` mention, add:

```markdown
- `create_character` mechanics are engine-derived: pass attacks as
  `{"weapon": "<srd-slug>"}` (add `"custom": {...}` only when no SRD weapon
  fits) and NEVER supply `saves`/`saving_throws` — save proficiencies come
  from the class. Declare only choices: `skills`, `expertise`, `tools`,
  `languages`. Use `tool_check` (explicit ability) for lock picking and
  other tool work.
```

- [ ] **Step 2: Run the FULL suite**

Run: `uv run pytest`
Expected: everything passes (previously 347 tests; now more). If `data/build/rules.sqlite` is missing, run `uv run dm seed` first.

- [ ] **Step 3: Verify against the live campaign (real migration)**

```bash
uv run python -c "
from pathlib import Path
from dm_engine.commands.registry import open_campaign_context
ctx = open_campaign_context(Path('campaigns'), 'the-fading-of-liraeth', Path('data/build/rules.sqlite'))
char = ctx.store.get_character('Algarve')
print('saves:', char['proficiencies']['saves'])
print('tools:', char['proficiencies']['tools'])
print([ (a['name'], a['source']) for a in char['attacks'] ])
"
```

Expected: `saves: ['dex', 'int']`, `tools: ['thieves-tools']`, attacks listing with `srd:`/`custom` sources and no crash. Then print the sheet and eyeball it: `uv run dm sheet Algarve --campaign the-fading-of-liraeth` (or `cat campaigns/the-fading-of-liraeth/sheets/algarve.md`) — Saving Throws/Skills/Tools sections present, Stealth +8.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/dm-session/SKILL.md
git commit -m "docs: dm-session uses derived character inputs"
```

---

## Self-review notes (already applied)

- Spec coverage: models (§1→Task 1), derivation + shared math (§2→Task 2), create_character API (§3→Task 3), expertise skill_check (§3→Task 4), tool_check (§3→Task 5), attack resolver refactor + on-use refusal (§3, §5→Task 6), full sheet (§4→Task 7), migration (§5→Task 8), error handling (§6→Tasks 3/6/8), testing incl. old-fixture cleanup (§7→Tasks 1–9), skill doc (§8→Task 10).
- Migration lives in `open_campaign_context` (not `CampaignStore.open`) because normalization needs the rules DB for re-derivation; both MCP and CLI open campaigns through this one function.
- Type consistency: `attack_to_hit(spec: dict, abilities: dict, level: int)` and `skill_modifier(skill, proficiencies, abilities, level)` are used with identical signatures in Tasks 2, 4, 6, 7.
- Known judgment calls an implementer may hit: exact refusal wording (match the tests, they are the contract); `tests/commands/test_attacks.py` helper names in Task 6 Step 1 and result-payload keys in Task 9 (read those files first, adapt the marked lines only).
