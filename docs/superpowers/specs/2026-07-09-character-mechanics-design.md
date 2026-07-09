# Character Mechanics: Attacks, Proficiency, Expertise, Saving Throws

**Date:** 2026-07-09
**Status:** Approved design, pending implementation plan

## Problem

`create_character` stores `attacks` and `proficiencies` as unvalidated JSON
blobs. In the first real campaign this produced a character whose attacks were
monster-style specs (`attack_bonus`, damage with the modifier baked in) — the
sheet rendered a DEX-18 rogue at −1 to hit, and the first `attack` command
would have crashed on `KeyError: 'ability'`. Separately: the engine has no
concept of expertise (a rogue's defining mechanic), no tool checks (lock
picking has no resolution path), save proficiencies are caller-declared free
text (`saving_throws` vs the engine's `saves` key silently produced "no save
proficiencies"), and the character sheet shows neither computed modifiers nor
the standard save/skill lists.

## Goals

1. A character's mechanical facts (weapon math, save proficiencies) cannot be
   wrong, regardless of what the LLM caller passes.
2. Player choices (skill picks, expertise picks) remain declared, but are
   validated for shape and internal consistency.
3. Expertise works RAW for skills and tool checks.
4. The rendered sheet reads like a real 5e character sheet: all six saves,
   all 18 skills with computed modifiers, passive Perception, correct attack
   lines.
5. The existing campaign (`the-fading-of-liraeth`) keeps working via a tiny
   idempotent migration.

## Decisions (settled during brainstorming)

- **Hybrid derivation**: rules-fixed facts are derived from the seeded SRD
  data (saves from class; weapon attacks from the `equipment` table); player
  choices are declared and validated; a validated `custom` attack format is
  the escape hatch for anything the SRD doesn't cover.
- **Approach A — resolve at creation**: derivation happens once in
  `create_character`; the character row stores the fully resolved spec. No
  runtime dependency on the rules DB during combat; the campaign sqlite stays
  self-contained ("the DB is the truth").
- **Expertise scope**: skills (`skill_check`) plus a new `tool_check` command.
- **Sheet**: full-sheet layout (all saves, all skills, markers, passive
  Perception).
- **Migration**: idempotent normalizer on `CampaignStore.open`, deletable
  once no pre-fix campaigns exist.

## Design

### 1. Data model (`models/character.py`)

```python
class AttackSpec(BaseModel):
    name: str
    source: str            # "srd:<weapon-slug>" | "custom"
    ability: Literal["str", "dex", "con", "int", "wis", "cha"]
    # Derivation only ever produces str/dex; the wider set is for validated
    # custom attacks (e.g. a WIS-based shillelagh). The resolver already
    # handles any ability key.
    proficient: bool = True
    damage: str            # base dice only, e.g. "1d6" — mods computed at use
    damage_type: str
    ranged: bool
    range_ft: int
    long_range_ft: int | None = None
    properties: list[str] = []   # "finesse", "light", ... display/audit only

class Proficiencies(BaseModel):
    saves: list[str]           # DERIVED from class; callers may not supply
    skills: list[str]          # validated against the canonical 18
    expertise: list[str] = []  # must be ⊆ skills ∪ tools
    tools: list[str] = []
    languages: list[str] = []
```

These models are the single validated shape for character mechanics. Stored
JSON in the `characters` table conforms to them from creation (or migration)
onward.

### 2. Derivation (`rules/character_build.py`)

Pure functions, records in / specs out, no I/O — consistent with how `rules/`
is kept dumb today.

- `derive_attack(weapon_record, abilities, class_record) -> list[AttackSpec]`
  - Damage dice, damage type, and range come from the equipment record.
  - **Finesse** → DEX if DEX mod ≥ STR mod, else STR. Otherwise melee → STR,
    ranged → DEX.
  - **Thrown** melee weapons emit a second spec (`"<Name> (thrown)"`, ranged,
    thrown range from the record) — the resolver's spec shape is single-mode,
    so two specs model RAW usage without touching combat code.
  - **Proficient** derived from the class proficiency list: category match
    (`simple-weapons` / `martial-weapons` vs the record's `weapon_category` +
    `weapon_range`) or specific match (pluralized-slug entries like
    `shortswords`). An explicit `proficient` boolean in the input overrides.
- `derive_saves(class_record) -> list[str]` — straight from the SRD class
  record's `saving_throws`.
- `build_proficiencies(input, class_record) -> Proficiencies` — refuses
  caller-supplied `saves`; validates skill names against the canonical 18;
  validates `expertise ⊆ skills ∪ tools`. Skill picks are deliberately NOT
  restricted to the class choice list (backgrounds/races grant off-list
  skills and are not modeled; this is the LLM-flex zone, visible in data).
- `attack_to_hit(spec, abilities, level) -> int` — the one shared to-hit /
  damage-mod computation, called by both the attack resolver and the sheet
  renderer so the two can never diverge again.

### 3. Command API

- **`create_character`**: each `attacks` entry is either
  `{"weapon": "<slug>"}` (SRD-derived; optional `"name"` and `"proficient"`
  overrides) or `{"custom": {...full AttackSpec fields...}}` (validated
  field-by-field, `source: "custom"`). Unknown weapon slug → structured
  refusal naming it. Malformed custom spec → refusal with the pydantic error
  summary. `proficiencies.saves` supplied by the caller → refusal.
- **`skill_check`**: proficiency bonus doubles when the skill is in
  `expertise`.
- **`tool_check`** (new command): `(character, tool, ability, dc, advantage,
  disadvantage, player_value, gm_only)`. Tools have no fixed ability in RAW,
  so the ability is an explicit argument. Proficiency from
  `proficiencies.tools`; expertise doubles. Registers via `@command`, so MCP
  and CLI pick it up 1:1 (FC-3).
- **`saving_throw`**: no logic change — it already reads
  `proficiencies["saves"]`; upstream derivation now guarantees that data.
- **`attack`**: no resolution change — it reads the same stored spec shape,
  now guaranteed complete at write time.

### 4. Sheet rendering (`state/sheets.py`)

All numbers computed from stored data at render time; nothing stored
redundantly.

- **Saving Throws**: all six, proficient first — `◉ DEX +6` / `○ STR -1`.
- **Skills**: all 18 in canonical order — `◉◉ Stealth +8` (expertise),
  `◉ Acrobatics +6` (proficient), `○ Athletics -1` — closing with
  `Passive Perception: <10 + Perception mod>`.
- **Tools**: proficiency/expertise markers showing the proficiency component
  only (tools have no fixed ability), e.g. `◉◉ thieves_tools (prof +4)` for
  a level-1 expert — the ability modifier is added per-check by `tool_check`.
- **Attacks**: `Shortsword: +6 to hit, 1d6+4 piercing (finesse)`; ranged
  entries annotated `(80/320)`. Uses `attack_to_hit` — the same helper the
  resolver uses.
- The silent `atk.get("ability", "str")` default is removed; the renderer
  reads validated fields only.

### 5. Migration

On `CampaignStore.open`, an idempotent normalizer (~30 lines, sibling of the
store) runs over `characters` rows in one transaction:

- `proficiencies`: `saving_throws` key renamed to `saves`; missing `saves`
  re-derived from the class record.
- `attacks`: each spec run through `AttackSpec` validation. Already-valid
  specs gain defaults (`source: "custom"`, `properties: []`). Old
  monster-style specs (`attack_bonus`, no `ability`) are re-derived by
  treating the lowercased name as an SRD weapon slug. If that lookup fails,
  the row is left untouched and open proceeds; the first *use* of that attack
  refuses with a message naming the character and attack (no mid-combat
  crash).
- Rows are rewritten only when changed. No schema version stamp — the pass is
  cheap and idempotent, so it runs on every open. Marked deletable once no
  pre-fix campaigns exist.

### 6. Error handling

Within the frozen contracts: bad *input* → structured `ok=False` refusal
(unknown weapon slug, bad skill name, expertise not a subset, caller-supplied
saves, malformed custom spec). Engine-side invariant violations (a stored
spec failing validation after migration) raise — those are bugs (FC-3).
Stored specs the migration deliberately leaves unfixed (§5) are a distinct,
expected case, not an invariant violation: they are refused cleanly on use
and rendered as a degraded line on sheets (§4); "a stored spec failing
validation raises" applies only to specs that were engine-derived/validated
at creation and have since been corrupted.

### 7. Testing

- **Unit — `rules/character_build`**: finesse picks DEX for a rogue and STR
  for a STR-brute; thrown dagger emits both specs with correct ranges;
  proficiency matching covers category, specific (`shortswords` for a wizard
  → false), and override paths; `derive_saves` for two classes;
  `build_proficiencies` refusals (bad skill name, expertise ⊄ skills ∪ tools,
  caller-supplied saves).
- **Unit — checks**: expertise doubling in `skill_check`; `tool_check`
  none/proficient/expertise tiers.
- **Integration**: `create_character` with `{"weapon": "shortbow"}` →
  `attack` from `near` resolves with derived numbers against a fixed seed;
  sheet markdown asserts full-sheet sections (6 saves, 18 skills, passive
  Perception, computed to-hit); migration test opens a fixture campaign with
  the original malformed rows and asserts normalization, plus a garbage row
  degrading to a refusal on use.
- **Cleanup**: existing tests constructing characters with raw attack dicts
  are updated to the new input format in the same PR; no dead-format
  fixtures remain.

### 8. Documentation

The `dm-session` skill's campaign-creation section gains explicit
instructions: pass attacks as `{"weapon": <slug>}` (custom only when the SRD
has no record) and never declare `saves` — closing the loop on how the
original bug happened.

## Out of scope

- Racial ability bonuses / traits derivation (abilities passed to
  `create_character` remain final post-racial scores; `race_slug` remains
  unvalidated flavor).
- Backgrounds as a modeled concept.
- Magic weapon bonuses (future: edit the stored spec).
- Multi-ability tool-check presets, weapon slug pluralization edge cases
  beyond the SRD list, and the deferred items already tracked from Phase 1.
