# M2 — Rules Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pure, deterministic 5e (2014 RAW) rules functions in `dm_engine/rules/` — dice, checks, attacks, damage, conditions, action economy, initiative, range bands, death saves, concentration, rests, progression, encounter math — everything M3's command layer needs to resolve play.

**Architecture:** `dm_engine.rules` is a package of pure modules: stdlib + pydantic only, no I/O, no SQLite, no globals. All randomness enters through the injected `DiceRoller` protocol (FC-2); a `SeededDiceRoller` gives one reproducible RNG per campaign. Functions return small pydantic result models that M3 serializes into the event log; illegal inputs to *pure math* raise `ValueError` (M3's command layer converts game-rule illegality into structured refusals before calling in).

**Tech Stack:** Python ≥3.12, pydantic v2, pytest, hypothesis (property tests), pytest-cov (gate), ruff.

## Global Constraints

- Branch: all M2 work on `feat/m2-rules-engine`; never commit to `main`; never push.
- Purity is frozen: nothing under `src/dm_engine/rules/` may import `sqlite3`, `pathlib`, open files, read `rules.sqlite`, or touch `random` except `dice.py`'s `SeededDiceRoller`. Dice are always injected as a `DiceRoller`.
- FC-2 (verbatim, frozen): `Roll(notation, rolls, modifier, total, player_supplied=False, gm_only=False)`; `DiceRoller.roll(notation, *, player_value=None, gm_only=False) -> Roll`. `player_value` bypasses RNG (the raw die total the player reported, before modifiers) and sets `player_supplied=True`.
- FC-4 (verbatim, frozen): `Band = Literal["engaged", "near", "far", "distant"]` with thresholds 5/30/60/120 ft. Leaving `engaged` without Disengage provokes an opportunity attack. AoE spells hit up to `max_targets` creatures within one band.
- Every public function/model in this plan is a frozen interface for M3 — exact names and signatures.
- Ruleset: 2014 RAW (SRD 5.1). Advantage never stacks; any advantage + any disadvantage = normal. Crits double dice, never modifiers. Damage order: flat reduction → immunity → resistance (halve, round down) → vulnerability (double).
- M2 gate: full suite green, `uv run pytest --cov=dm_engine.rules --cov-report=term-missing` shows **100%** coverage for `dm_engine/rules/`, ruff clean.
- Conventional commits, first line under 50 chars. Verify `uv run pytest` and `uv run ruff check .` before every commit.
- Rules tests live in `tests/rules/` (new directory; the existing session-scoped `rules_db` fixture in `tests/conftest.py` is for content tests and is not used here).

---

### Task 1: Dice (FC-2) + property tests

**Files:**
- Create: `src/dm_engine/rules/__init__.py`, `src/dm_engine/rules/dice.py`, `tests/rules/__init__.py`
- Modify: `pyproject.toml` (add `hypothesis`, `pytest-cov` to dev group; coverage config)
- Test: `tests/rules/test_dice.py`

**Interfaces:**
- Produces (frozen, FC-2): `Roll` (pydantic), `DiceRoller` (Protocol), `SeededDiceRoller(seed: int)`, `parse_notation(notation: str) -> tuple[int, int, int]` (count, sides, modifier). Every later task consumes `DiceRoller`/`Roll` from here.

- [ ] **Step 1: Create branch**

```bash
git checkout -b feat/m2-rules-engine
```

- [ ] **Step 2: Add dev dependencies and coverage config**

In `pyproject.toml`, change the dev group to:

```toml
[dependency-groups]
dev = [
    "pytest>=8",
    "ruff>=0.4",
    "hypothesis>=6.100",
    "pytest-cov>=5",
]
```

and append at the end of the file:

```toml
[tool.coverage.report]
exclude_also = [
    "^\\s*\\.\\.\\.$",
]
```

Then run `uv sync`.

- [ ] **Step 3: Write the failing tests**

`tests/rules/__init__.py`: empty file.

`tests/rules/test_dice.py`:
```python
import pytest
from hypothesis import given
from hypothesis import strategies as st

from dm_engine.rules.dice import Roll, SeededDiceRoller, parse_notation


def test_parse_notation_forms():
    assert parse_notation("1d20+5") == (1, 20, 5)
    assert parse_notation("8d6") == (8, 6, 0)
    assert parse_notation("d20") == (1, 20, 0)
    assert parse_notation("2d8-1") == (2, 8, -1)
    assert parse_notation(" 1D12 + 3 ") == (1, 12, 3)


@pytest.mark.parametrize("bad", ["", "d", "0d6", "1d1", "20", "1d20+", "fireball", "1d20+5+3"])
def test_parse_notation_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_notation(bad)


def test_same_seed_reproduces_sequence():
    a = SeededDiceRoller(42)
    b = SeededDiceRoller(42)
    assert [a.roll("1d20").total for _ in range(20)] == [
        b.roll("1d20").total for _ in range(20)
    ]


def test_different_seeds_diverge():
    a = [SeededDiceRoller(1).roll("1d20").total for _ in range(10)]
    b = [SeededDiceRoller(2).roll("1d20").total for _ in range(10)]
    assert a != b


def test_player_value_bypasses_rng():
    roll = SeededDiceRoller(1).roll("1d20+5", player_value=17)
    assert roll == Roll(
        notation="1d20+5", rolls=[17], modifier=5, total=22, player_supplied=True
    )


def test_gm_only_flag_carries():
    assert SeededDiceRoller(1).roll("1d20", gm_only=True).gm_only is True


@given(
    count=st.integers(min_value=1, max_value=20),
    sides=st.sampled_from([4, 6, 8, 10, 12, 20, 100]),
    modifier=st.integers(min_value=-10, max_value=10),
    seed=st.integers(min_value=0, max_value=2**32),
)
def test_roll_bounds_and_arithmetic(count, sides, modifier, seed):
    sign = "+" if modifier >= 0 else "-"
    notation = f"{count}d{sides}{sign}{abs(modifier)}"
    roll = SeededDiceRoller(seed).roll(notation)
    assert len(roll.rolls) == count
    assert all(1 <= r <= sides for r in roll.rolls)
    assert roll.total == sum(roll.rolls) + modifier
    assert count + modifier <= roll.total <= count * sides + modifier
    assert roll.player_supplied is False
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_dice.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dm_engine.rules'`

- [ ] **Step 5: Write the dice module**

`src/dm_engine/rules/__init__.py`: empty file.

`src/dm_engine/rules/dice.py`:
```python
"""Dice notation parsing and seeded rolling (FC-2).

The engine's only randomness source. One SeededDiceRoller per campaign,
seeded at creation; the M3 command layer records every Roll in the event
log. `player_value` bypasses the RNG with the raw die total a player
reported (before modifiers) and flags the Roll `player_supplied`.
"""

from __future__ import annotations

import random
import re
from typing import Protocol

from pydantic import BaseModel

_NOTATION = re.compile(r"^\s*(\d*)[dD](\d+)\s*(?:([+-])\s*(\d+))?\s*$")


class Roll(BaseModel):
    notation: str
    rolls: list[int]
    modifier: int
    total: int
    player_supplied: bool = False
    gm_only: bool = False


class DiceRoller(Protocol):
    def roll(
        self, notation: str, *, player_value: int | None = None, gm_only: bool = False
    ) -> Roll: ...


def parse_notation(notation: str) -> tuple[int, int, int]:
    """Parse 'NdS+K' into (count, sides, modifier). 'd20' means one die."""
    m = _NOTATION.match(notation)
    if not m:
        raise ValueError(f"invalid dice notation: {notation!r}")
    count = int(m.group(1)) if m.group(1) else 1
    sides = int(m.group(2))
    modifier = int(f"{m.group(3)}{m.group(4)}") if m.group(3) else 0
    if count < 1 or sides < 2:
        raise ValueError(f"invalid dice notation: {notation!r}")
    return count, sides, modifier


class SeededDiceRoller:
    """DiceRoller backed by one seeded RNG; same seed, same roll sequence."""

    def __init__(self, seed: int):
        self._rng = random.Random(seed)

    def roll(
        self, notation: str, *, player_value: int | None = None, gm_only: bool = False
    ) -> Roll:
        count, sides, modifier = parse_notation(notation)
        if player_value is not None:
            return Roll(
                notation=notation,
                rolls=[player_value],
                modifier=modifier,
                total=player_value + modifier,
                player_supplied=True,
                gm_only=gm_only,
            )
        rolls = [self._rng.randint(1, sides) for _ in range(count)]
        return Roll(
            notation=notation,
            rolls=rolls,
            modifier=modifier,
            total=sum(rolls) + modifier,
            gm_only=gm_only,
        )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_dice.py -v`
Expected: PASS (7 tests, hypothesis runs the property test many times)

- [ ] **Step 7: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff check .`

```bash
git add pyproject.toml uv.lock src/dm_engine/rules tests/rules
git commit -m "feat: add seeded dice roller (FC-2)"
```

---

### Task 2: d20 core — modifiers, advantage, checks & saves

**Files:**
- Create: `src/dm_engine/rules/checks.py`
- Test: `tests/rules/test_checks.py`

**Interfaces:**
- Consumes: `DiceRoller`, `Roll` from `dm_engine.rules.dice`.
- Produces (frozen): `AdvantageMode = Literal["normal","advantage","disadvantage"]`; `ability_modifier(score: int) -> int`; `proficiency_bonus(level: int) -> int`; `combine_advantage(advantage: bool, disadvantage: bool) -> AdvantageMode`; `D20Result(rolls: list[Roll], mode, natural: int, modifier: int, total: int)`; `roll_d20(roller, modifier, mode="normal", *, player_value=None, gm_only=False) -> D20Result`; `CheckResult(d20, dc, success, margin)`; `resolve_check(roller, modifier, dc, mode="normal", *, player_value=None, gm_only=False) -> CheckResult`. Saving throws use `resolve_check` too — same math; M3 names the command.

- [ ] **Step 1: Write the failing tests**

`tests/rules/test_checks.py`:
```python
import pytest

from dm_engine.rules.checks import (
    ability_modifier,
    combine_advantage,
    proficiency_bonus,
    resolve_check,
    roll_d20,
)
from dm_engine.rules.dice import SeededDiceRoller


def test_ability_modifier_raw_table():
    assert ability_modifier(1) == -5
    assert ability_modifier(8) == -1
    assert ability_modifier(10) == 0
    assert ability_modifier(11) == 0
    assert ability_modifier(15) == 2
    assert ability_modifier(20) == 5
    assert ability_modifier(30) == 10


@pytest.mark.parametrize("bad", [0, 31, -3])
def test_ability_modifier_range(bad):
    with pytest.raises(ValueError):
        ability_modifier(bad)


def test_proficiency_bonus_progression():
    levels = (1, 4, 5, 8, 9, 12, 13, 16, 17, 20)
    assert [proficiency_bonus(x) for x in levels] == [2, 2, 3, 3, 4, 4, 5, 5, 6, 6]
    with pytest.raises(ValueError):
        proficiency_bonus(21)


def test_advantage_stacking_rules():
    # RAW: sources never stack; any adv + any dis cancel to normal.
    assert combine_advantage(False, False) == "normal"
    assert combine_advantage(True, False) == "advantage"
    assert combine_advantage(False, True) == "disadvantage"
    assert combine_advantage(True, True) == "normal"


def test_normal_roll_uses_one_die():
    result = roll_d20(SeededDiceRoller(3), modifier=4)
    assert len(result.rolls) == 1
    assert result.natural == result.rolls[0].rolls[0]
    assert result.total == result.natural + 4


def test_advantage_picks_higher_die():
    result = roll_d20(SeededDiceRoller(7), modifier=3, mode="advantage")
    naturals = [r.rolls[0] for r in result.rolls]
    assert len(naturals) == 2
    assert result.natural == max(naturals)
    assert result.total == result.natural + 3


def test_disadvantage_picks_lower_die():
    result = roll_d20(SeededDiceRoller(7), modifier=0, mode="disadvantage")
    naturals = [r.rolls[0] for r in result.rolls]
    assert result.natural == min(naturals)


def test_player_value_skips_engine_dice():
    result = roll_d20(SeededDiceRoller(1), modifier=2, player_value=15)
    assert len(result.rolls) == 1
    assert result.rolls[0].player_supplied is True
    assert result.natural == 15
    assert result.total == 17


def test_gm_only_propagates_to_rolls():
    result = roll_d20(SeededDiceRoller(1), modifier=0, mode="advantage", gm_only=True)
    assert all(r.gm_only for r in result.rolls)


def test_check_meets_dc_succeeds():
    # "Meets it, beats it": total == DC is a success.
    result = resolve_check(SeededDiceRoller(1), modifier=2, dc=15, player_value=13)
    assert result.success is True
    assert result.margin == 0


def test_check_below_dc_fails():
    result = resolve_check(SeededDiceRoller(1), modifier=0, dc=15, player_value=9)
    assert result.success is False
    assert result.margin == -6
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_checks.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `dm_engine.rules.checks`)

- [ ] **Step 3: Write the checks module**

`src/dm_engine/rules/checks.py`:
```python
"""Core d20 resolution: modifiers, proficiency, advantage, checks and saves.

Saving throws are mechanically identical to ability checks against a DC, so
`resolve_check` serves both; the M3 command layer names them separately.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from dm_engine.rules.dice import DiceRoller, Roll

AdvantageMode = Literal["normal", "advantage", "disadvantage"]


def ability_modifier(score: int) -> int:
    """RAW: (score - 10) // 2, rounded down (8 -> -1, 1 -> -5)."""
    if not 1 <= score <= 30:
        raise ValueError(f"ability score out of range: {score}")
    return (score - 10) // 2


def proficiency_bonus(level: int) -> int:
    """+2 at level 1, +1 every 4 levels (max +6 at 17-20)."""
    if not 1 <= level <= 20:
        raise ValueError(f"level out of range: {level}")
    return 2 + (level - 1) // 4


def combine_advantage(advantage: bool, disadvantage: bool) -> AdvantageMode:
    """RAW stacking: sources never stack; any advantage plus any
    disadvantage cancels to normal, regardless of source counts."""
    if advantage and disadvantage:
        return "normal"
    if advantage:
        return "advantage"
    if disadvantage:
        return "disadvantage"
    return "normal"


class D20Result(BaseModel):
    rolls: list[Roll]  # one die normally, two under advantage/disadvantage
    mode: AdvantageMode
    natural: int  # the die face that counts
    modifier: int
    total: int


def roll_d20(
    roller: DiceRoller,
    modifier: int,
    mode: AdvantageMode = "normal",
    *,
    player_value: int | None = None,
    gm_only: bool = False,
) -> D20Result:
    """Roll 1d20, or 2d20 pick high/low under advantage/disadvantage.

    `player_value` is the final natural the player reports (they applied
    their own advantage state at the table); the engine rolls no dice.
    """
    if player_value is not None:
        roll = roller.roll("1d20", player_value=player_value, gm_only=gm_only)
        return D20Result(
            rolls=[roll],
            mode=mode,
            natural=player_value,
            modifier=modifier,
            total=player_value + modifier,
        )
    first = roller.roll("1d20", gm_only=gm_only)
    rolls = [first]
    natural = first.rolls[0]
    if mode != "normal":
        second = roller.roll("1d20", gm_only=gm_only)
        rolls.append(second)
        pick = max if mode == "advantage" else min
        natural = pick(natural, second.rolls[0])
    return D20Result(
        rolls=rolls, mode=mode, natural=natural, modifier=modifier, total=natural + modifier
    )


class CheckResult(BaseModel):
    d20: D20Result
    dc: int
    success: bool
    margin: int  # total - dc


def resolve_check(
    roller: DiceRoller,
    modifier: int,
    dc: int,
    mode: AdvantageMode = "normal",
    *,
    player_value: int | None = None,
    gm_only: bool = False,
) -> CheckResult:
    """Ability check or saving throw vs a DC ("meets it, beats it")."""
    d20 = roll_d20(roller, modifier, mode, player_value=player_value, gm_only=gm_only)
    return CheckResult(d20=d20, dc=dc, success=d20.total >= dc, margin=d20.total - dc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_checks.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff check .`

```bash
git add src/dm_engine/rules/checks.py tests/rules/test_checks.py
git commit -m "feat: add d20 checks with advantage rules"
```

---

### Task 3: Damage mitigation

**Files:**
- Create: `src/dm_engine/rules/damage.py`
- Test: `tests/rules/test_damage.py`

**Interfaces:**
- Produces (frozen): `DAMAGE_TYPES: frozenset[str]` (the 13 RAW types); `MitigatedDamage(raw, damage_type, after_reduction, final, applied: list[str])`; `apply_mitigation(raw: int, damage_type: str, *, resistances=(), vulnerabilities=(), immunities=(), reduction: int = 0) -> MitigatedDamage`.

- [ ] **Step 1: Write the failing tests**

`tests/rules/test_damage.py`:
```python
import pytest

from dm_engine.rules.damage import DAMAGE_TYPES, apply_mitigation


def test_thirteen_raw_damage_types():
    assert DAMAGE_TYPES == {
        "acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic",
        "piercing", "poison", "psychic", "radiant", "slashing", "thunder",
    }


def test_plain_damage_passes_through():
    result = apply_mitigation(11, "slashing")
    assert result.final == 11
    assert result.applied == []


def test_resistance_halves_rounding_down():
    assert apply_mitigation(11, "fire", resistances={"fire"}).final == 5


def test_vulnerability_doubles():
    assert apply_mitigation(7, "cold", vulnerabilities={"cold"}).final == 14


def test_immunity_zeroes():
    result = apply_mitigation(50, "poison", immunities={"poison"})
    assert result.final == 0
    assert result.applied == ["immunity"]


def test_reduction_applies_before_resistance():
    # RAW order golden: (11 - 3) // 2 == 4, never (11 // 2) - 3 == 2.
    result = apply_mitigation(
        11, "slashing", resistances={"slashing"}, reduction=3
    )
    assert result.after_reduction == 8
    assert result.final == 4
    assert result.applied == ["reduction:3", "resistance"]


def test_resistance_then_vulnerability_ordering():
    # Halve (floor) first, then double: 11 -> 5 -> 10, not back to 11.
    result = apply_mitigation(
        11, "fire", resistances={"fire"}, vulnerabilities={"fire"}
    )
    assert result.final == 10
    assert result.applied == ["resistance", "vulnerability"]


def test_reduction_cannot_go_negative():
    assert apply_mitigation(2, "piercing", reduction=5).final == 0


def test_unrelated_defenses_do_not_apply():
    result = apply_mitigation(
        9, "fire", resistances={"cold"}, vulnerabilities={"acid"}, immunities={"poison"}
    )
    assert result.final == 9


def test_unknown_damage_type_raises():
    with pytest.raises(ValueError):
        apply_mitigation(5, "emotional")


def test_negative_damage_raises():
    with pytest.raises(ValueError):
        apply_mitigation(-1, "fire")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_damage.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `dm_engine.rules.damage`)

- [ ] **Step 3: Write the damage module**

`src/dm_engine/rules/damage.py`:
```python
"""Damage mitigation in RAW order.

Flat reductions apply first, then immunity, then resistance (halve, round
down), then vulnerability (double). Multiple instances of resistance or
vulnerability to one type count only once (sets make that structural).
"""

from __future__ import annotations

from collections.abc import Collection

from pydantic import BaseModel

DAMAGE_TYPES = frozenset({
    "acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic",
    "piercing", "poison", "psychic", "radiant", "slashing", "thunder",
})


class MitigatedDamage(BaseModel):
    raw: int
    damage_type: str
    after_reduction: int
    final: int
    applied: list[str]  # audit trail, e.g. ["reduction:3", "resistance"]


def apply_mitigation(
    raw: int,
    damage_type: str,
    *,
    resistances: Collection[str] = (),
    vulnerabilities: Collection[str] = (),
    immunities: Collection[str] = (),
    reduction: int = 0,
) -> MitigatedDamage:
    if damage_type not in DAMAGE_TYPES:
        raise ValueError(f"unknown damage type: {damage_type!r}")
    if raw < 0:
        raise ValueError("damage cannot be negative")
    if reduction < 0:
        raise ValueError("reduction cannot be negative")
    applied: list[str] = []
    after_reduction = raw
    if reduction:
        after_reduction = max(0, raw - reduction)
        applied.append(f"reduction:{reduction}")
    final = after_reduction
    if damage_type in immunities:
        applied.append("immunity")
        final = 0
    else:
        if damage_type in resistances:
            final //= 2
            applied.append("resistance")
        if damage_type in vulnerabilities:
            final *= 2
            applied.append("vulnerability")
    return MitigatedDamage(
        raw=raw,
        damage_type=damage_type,
        after_reduction=after_reduction,
        final=final,
        applied=applied,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_damage.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff check .`

```bash
git add src/dm_engine/rules/damage.py tests/rules/test_damage.py
git commit -m "feat: add damage mitigation ordering"
```

---

### Task 4: Attack rolls and damage dice

**Files:**
- Create: `src/dm_engine/rules/attacks.py`
- Test: `tests/rules/test_attacks.py`

**Interfaces:**
- Consumes: `roll_d20`, `D20Result`, `AdvantageMode` from `checks`; `DiceRoller`, `Roll`, `parse_notation` from `dice`.
- Produces (frozen): `AttackRollResult(d20: D20Result, target_ac: int, hit: bool, critical_hit: bool, critical_miss: bool)`; `resolve_attack_roll(roller, attack_bonus, target_ac, mode="normal", *, player_value=None, gm_only=False) -> AttackRollResult`; `DamageRollResult(rolls: list[Roll], critical: bool, total: int)`; `roll_damage(roller, notation, *, critical=False, player_value=None, gm_only=False) -> DamageRollResult`.

- [ ] **Step 1: Write the failing tests**

`tests/rules/test_attacks.py`:
```python
from dm_engine.rules.attacks import resolve_attack_roll, roll_damage
from dm_engine.rules.dice import SeededDiceRoller


def test_hit_when_total_meets_ac():
    result = resolve_attack_roll(SeededDiceRoller(1), attack_bonus=5, target_ac=15, player_value=10)
    assert result.hit is True
    assert result.critical_hit is False


def test_miss_when_total_below_ac():
    result = resolve_attack_roll(SeededDiceRoller(1), attack_bonus=2, target_ac=15, player_value=12)
    assert result.hit is False


def test_natural_twenty_always_hits_and_crits():
    result = resolve_attack_roll(SeededDiceRoller(1), attack_bonus=0, target_ac=30, player_value=20)
    assert result.hit is True
    assert result.critical_hit is True


def test_natural_one_always_misses():
    result = resolve_attack_roll(SeededDiceRoller(1), attack_bonus=19, target_ac=5, player_value=1)
    assert result.hit is False
    assert result.critical_miss is True


def test_damage_roll_normal():
    result = roll_damage(SeededDiceRoller(5), "2d6+3")
    assert result.critical is False
    assert len(result.rolls) == 1
    assert result.total == result.rolls[0].total
    assert 5 <= result.total <= 15


def test_crit_doubles_dice_not_modifier():
    result = roll_damage(SeededDiceRoller(5), "2d6+3", critical=True)
    assert result.critical is True
    assert len(result.rolls) == 2
    assert result.rolls[0].notation == "2d6+3"
    assert result.rolls[1].notation == "2d6"  # extra dice carry no modifier
    assert result.rolls[1].modifier == 0
    assert result.total == result.rolls[0].total + result.rolls[1].total
    assert 7 <= result.total <= 27  # 4d6 + 3


def test_damage_never_negative():
    # 1d4-3 can roll below zero; damage floors at 0.
    totals = [roll_damage(SeededDiceRoller(seed), "1d4-3").total for seed in range(30)]
    assert all(t >= 0 for t in totals)
    assert 0 in totals  # some rolls actually hit the floor


def test_player_supplied_damage_adds_modifier_once():
    # Player reports raw dice total (crit dice included); engine adds +3 once.
    result = roll_damage(SeededDiceRoller(1), "2d6+3", critical=True, player_value=14)
    assert len(result.rolls) == 1
    assert result.rolls[0].player_supplied is True
    assert result.total == 17
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_attacks.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `dm_engine.rules.attacks`)

- [ ] **Step 3: Write the attacks module**

`src/dm_engine/rules/attacks.py`:
```python
"""Attack rolls and damage dice.

Natural 20 always hits and crits; natural 1 always misses. A critical hit
rolls all damage dice twice; modifiers apply once. Player-supplied damage
values are the raw dice total (crit dice already included by the player);
the engine adds the notation's modifier once.
"""

from __future__ import annotations

from pydantic import BaseModel

from dm_engine.rules.checks import AdvantageMode, D20Result, roll_d20
from dm_engine.rules.dice import DiceRoller, Roll, parse_notation


class AttackRollResult(BaseModel):
    d20: D20Result
    target_ac: int
    hit: bool
    critical_hit: bool
    critical_miss: bool


def resolve_attack_roll(
    roller: DiceRoller,
    attack_bonus: int,
    target_ac: int,
    mode: AdvantageMode = "normal",
    *,
    player_value: int | None = None,
    gm_only: bool = False,
) -> AttackRollResult:
    d20 = roll_d20(roller, attack_bonus, mode, player_value=player_value, gm_only=gm_only)
    critical_hit = d20.natural == 20
    critical_miss = d20.natural == 1
    hit = critical_hit or (not critical_miss and d20.total >= target_ac)
    return AttackRollResult(
        d20=d20,
        target_ac=target_ac,
        hit=hit,
        critical_hit=critical_hit,
        critical_miss=critical_miss,
    )


class DamageRollResult(BaseModel):
    rolls: list[Roll]
    critical: bool
    total: int


def roll_damage(
    roller: DiceRoller,
    notation: str,
    *,
    critical: bool = False,
    player_value: int | None = None,
    gm_only: bool = False,
) -> DamageRollResult:
    count, sides, _modifier = parse_notation(notation)
    if player_value is not None:
        roll = roller.roll(notation, player_value=player_value, gm_only=gm_only)
        return DamageRollResult(
            rolls=[roll], critical=critical, total=max(0, roll.total)
        )
    rolls = [roller.roll(notation, gm_only=gm_only)]
    if critical:
        rolls.append(roller.roll(f"{count}d{sides}", gm_only=gm_only))
    return DamageRollResult(
        rolls=rolls, critical=critical, total=max(0, sum(r.total for r in rolls))
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_attacks.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff check .`

```bash
git add src/dm_engine/rules/attacks.py tests/rules/test_attacks.py
git commit -m "feat: add attack rolls and crit damage"
```

---

### Task 5: Conditions

**Files:**
- Create: `src/dm_engine/rules/conditions.py`
- Test: `tests/rules/test_conditions.py`

**Interfaces:**
- Consumes: `AdvantageMode`, `combine_advantage` from `checks`.
- Produces (frozen): `CONDITIONS: frozenset[str]` (the 15 RAW names); `ConditionEffects` (pydantic, per-creature aggregate flags — see code); `effects_for(conditions: Iterable[str], exhaustion_level: int = 0) -> ConditionEffects`; `AttackInteraction(mode: AdvantageMode, auto_crit_on_hit: bool)`; `attack_interaction(attacker: ConditionEffects, target: ConditionEffects, *, engaged: bool) -> AttackInteraction`. In the band system (FC-4) `engaged` means within 5 ft.

- [ ] **Step 1: Write the failing tests**

`tests/rules/test_conditions.py`:
```python
import pytest

from dm_engine.rules.conditions import (
    CONDITIONS,
    attack_interaction,
    effects_for,
)


def test_all_fifteen_raw_conditions():
    assert CONDITIONS == {
        "blinded", "charmed", "deafened", "exhaustion", "frightened", "grappled",
        "incapacitated", "invisible", "paralyzed", "petrified", "poisoned",
        "prone", "restrained", "stunned", "unconscious",
    }


def test_unknown_condition_raises():
    with pytest.raises(ValueError):
        effects_for({"sleepy"})


def test_no_conditions_is_neutral():
    e = effects_for([])
    assert e.can_take_actions and e.can_move and e.can_speak
    assert e.speed_multiplier == 1.0
    assert not e.attacked_with_advantage and not e.attacks_have_disadvantage


def test_paralyzed_aggregate():
    e = effects_for({"paralyzed"})
    assert e.can_take_actions is False
    assert e.can_take_reactions is False
    assert e.can_move is False
    assert e.can_speak is False
    assert e.auto_fail_str_dex_saves is True
    assert e.attacked_with_advantage is True
    assert e.melee_hits_are_critical is True


def test_unconscious_includes_prone():
    e = effects_for({"unconscious"})
    assert e.prone is True
    assert e.melee_hits_are_critical is True
    assert e.auto_fail_str_dex_saves is True


def test_restrained_flags():
    e = effects_for({"restrained"})
    assert e.can_move is False
    assert e.attacked_with_advantage is True
    assert e.attacks_have_disadvantage is True
    assert e.dex_saves_have_disadvantage is True


def test_petrified_resists_all_damage():
    assert effects_for({"petrified"}).resist_all_damage is True


def test_grappled_only_stops_movement():
    e = effects_for({"grappled"})
    assert e.can_move is False
    assert e.can_take_actions is True


def test_blinded_deafened_charmed_frightened_flags():
    assert effects_for({"blinded"}).auto_fail_sight_checks is True
    assert effects_for({"deafened"}).auto_fail_hearing_checks is True
    assert effects_for({"charmed"}).cannot_attack_charmer is True
    fear = effects_for({"frightened"})
    assert fear.cannot_approach_fear_source is True
    assert fear.checks_have_disadvantage is True
    assert fear.attacks_have_disadvantage is True


def test_exhaustion_ladder_is_cumulative():
    assert effects_for([], exhaustion_level=1).checks_have_disadvantage is True
    assert effects_for([], exhaustion_level=2).speed_multiplier == 0.5
    e3 = effects_for([], exhaustion_level=3)
    assert e3.attacks_have_disadvantage and e3.saves_have_disadvantage
    assert e3.checks_have_disadvantage  # level 1 effect persists
    assert effects_for([], exhaustion_level=4).hp_max_halved is True
    assert effects_for([], exhaustion_level=5).can_move is False
    assert effects_for([], exhaustion_level=6).dead is True
    with pytest.raises(ValueError):
        effects_for([], exhaustion_level=7)


def test_exhaustion_name_implies_level_one():
    assert effects_for({"exhaustion"}).checks_have_disadvantage is True


def test_attack_interaction_poisoned_vs_blinded_cancels():
    # Poisoned attacker (dis) vs blinded target (adv against it) -> normal.
    attacker = effects_for({"poisoned"})
    target = effects_for({"blinded"})
    assert attack_interaction(attacker, target, engaged=True).mode == "normal"


def test_attack_interaction_prone_target_depends_on_range():
    neutral = effects_for([])
    prone = effects_for({"prone"})
    assert attack_interaction(neutral, prone, engaged=True).mode == "advantage"
    assert attack_interaction(neutral, prone, engaged=False).mode == "disadvantage"


def test_attack_interaction_invisible_attacker_has_advantage():
    invisible = effects_for({"invisible"})
    neutral = effects_for([])
    assert attack_interaction(invisible, neutral, engaged=False).mode == "advantage"
    # ...and attacks against an invisible creature have disadvantage.
    assert attack_interaction(neutral, invisible, engaged=False).mode == "disadvantage"


def test_paralyzed_auto_crit_only_within_reach():
    neutral = effects_for([])
    paralyzed = effects_for({"paralyzed"})
    assert attack_interaction(neutral, paralyzed, engaged=True).auto_crit_on_hit is True
    assert attack_interaction(neutral, paralyzed, engaged=False).auto_crit_on_hit is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_conditions.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `dm_engine.rules.conditions`)

- [ ] **Step 3: Write the conditions module**

`src/dm_engine/rules/conditions.py`:
```python
"""The 15 SRD conditions plus exhaustion levels: aggregated mechanical flags.

`effects_for` folds a creature's active conditions into one flag set.
Interactions that depend on range (prone, paralyzed auto-crit) live in
`attack_interaction`, which takes an `engaged` flag — in the band system
(FC-4), engaged means within 5 ft.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from dm_engine.rules.checks import AdvantageMode, combine_advantage

CONDITIONS = frozenset({
    "blinded", "charmed", "deafened", "exhaustion", "frightened", "grappled",
    "incapacitated", "invisible", "paralyzed", "petrified", "poisoned",
    "prone", "restrained", "stunned", "unconscious",
})

_INCAPACITATING = frozenset({"incapacitated", "paralyzed", "petrified", "stunned", "unconscious"})
_NO_MOVE = frozenset({"grappled", "paralyzed", "petrified", "restrained", "stunned", "unconscious"})
_NO_SPEECH = frozenset({"paralyzed", "petrified", "unconscious"})
_ATTACKED_WITH_ADVANTAGE = frozenset(
    {"blinded", "paralyzed", "petrified", "restrained", "stunned", "unconscious"}
)
_ATTACKS_WITH_DISADVANTAGE = frozenset({"blinded", "frightened", "poisoned", "prone", "restrained"})
_AUTO_FAIL_STR_DEX = frozenset({"paralyzed", "petrified", "stunned", "unconscious"})
_MELEE_AUTO_CRIT = frozenset({"paralyzed", "unconscious"})


class ConditionEffects(BaseModel):
    can_take_actions: bool = True
    can_take_reactions: bool = True
    can_move: bool = True
    can_speak: bool = True
    speed_multiplier: float = 1.0
    attacks_have_advantage: bool = False  # invisible attacker
    attacks_have_disadvantage: bool = False
    attacked_with_advantage: bool = False  # attackers roll with advantage
    attacked_with_disadvantage: bool = False  # invisible target
    checks_have_disadvantage: bool = False
    saves_have_disadvantage: bool = False  # exhaustion >= 3
    dex_saves_have_disadvantage: bool = False  # restrained
    auto_fail_str_dex_saves: bool = False
    melee_hits_are_critical: bool = False  # paralyzed/unconscious, hit from within 5 ft
    prone: bool = False
    resist_all_damage: bool = False  # petrified
    auto_fail_sight_checks: bool = False  # blinded
    auto_fail_hearing_checks: bool = False  # deafened
    cannot_attack_charmer: bool = False  # charmed
    cannot_approach_fear_source: bool = False  # frightened
    hp_max_halved: bool = False  # exhaustion >= 4
    dead: bool = False  # exhaustion 6


def effects_for(conditions: Iterable[str], exhaustion_level: int = 0) -> ConditionEffects:
    names = set(conditions)
    unknown = names - CONDITIONS
    if unknown:
        raise ValueError(f"unknown conditions: {sorted(unknown)}")
    if not 0 <= exhaustion_level <= 6:
        raise ValueError(f"exhaustion level out of range: {exhaustion_level}")
    if "exhaustion" in names and exhaustion_level == 0:
        exhaustion_level = 1

    e = ConditionEffects()
    if names & _INCAPACITATING:
        e.can_take_actions = False
        e.can_take_reactions = False
    if names & _NO_MOVE:
        e.can_move = False
    if names & _NO_SPEECH:
        e.can_speak = False
    if names & _ATTACKED_WITH_ADVANTAGE:
        e.attacked_with_advantage = True
    if names & _ATTACKS_WITH_DISADVANTAGE:
        e.attacks_have_disadvantage = True
    if names & _AUTO_FAIL_STR_DEX:
        e.auto_fail_str_dex_saves = True
    if names & _MELEE_AUTO_CRIT:
        e.melee_hits_are_critical = True
    if names & {"prone", "unconscious"}:
        e.prone = True
    if "invisible" in names:
        e.attacks_have_advantage = True
        e.attacked_with_disadvantage = True
    if names & {"poisoned", "frightened"}:
        e.checks_have_disadvantage = True
    if "restrained" in names:
        e.dex_saves_have_disadvantage = True
    if "petrified" in names:
        e.resist_all_damage = True
    if "blinded" in names:
        e.auto_fail_sight_checks = True
    if "deafened" in names:
        e.auto_fail_hearing_checks = True
    if "charmed" in names:
        e.cannot_attack_charmer = True
    if "frightened" in names:
        e.cannot_approach_fear_source = True

    if exhaustion_level >= 1:
        e.checks_have_disadvantage = True
    if exhaustion_level >= 2:
        e.speed_multiplier = 0.5
    if exhaustion_level >= 3:
        e.attacks_have_disadvantage = True
        e.saves_have_disadvantage = True
    if exhaustion_level >= 4:
        e.hp_max_halved = True
    if exhaustion_level >= 5:
        e.can_move = False
    if exhaustion_level >= 6:
        e.dead = True
    return e


class AttackInteraction(BaseModel):
    mode: AdvantageMode
    auto_crit_on_hit: bool


def attack_interaction(
    attacker: ConditionEffects, target: ConditionEffects, *, engaged: bool
) -> AttackInteraction:
    """Advantage state of one attack, from both creatures' conditions.

    Prone targets grant advantage to engaged attackers and impose
    disadvantage on everyone else. Paralyzed/unconscious targets turn
    engaged hits into critical hits.
    """
    advantage = (
        attacker.attacks_have_advantage
        or target.attacked_with_advantage
        or (target.prone and engaged)
    )
    disadvantage = (
        attacker.attacks_have_disadvantage
        or target.attacked_with_disadvantage
        or (target.prone and not engaged)
    )
    return AttackInteraction(
        mode=combine_advantage(advantage, disadvantage),
        auto_crit_on_hit=target.melee_hits_are_critical and engaged,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_conditions.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff check .`

```bash
git add src/dm_engine/rules/conditions.py tests/rules/test_conditions.py
git commit -m "feat: add all 15 conditions and exhaustion"
```

---

### Task 6: Action economy and initiative

**Files:**
- Create: `src/dm_engine/rules/action_economy.py`, `src/dm_engine/rules/initiative.py`
- Test: `tests/rules/test_action_economy.py`, `tests/rules/test_initiative.py`

**Interfaces:**
- Consumes: `DiceRoller`, `Roll` from `dice`.
- Produces (frozen):
  - `ActionKind = Literal["action", "bonus_action", "reaction"]`; `TurnBudget(speed, movement_remaining, action_available=True, bonus_action_available=True, reaction_available=True)`; `new_turn(speed: int) -> TurnBudget`; `SpendResult(ok: bool, reason: str | None, budget: TurnBudget)`; `spend(budget, kind) -> SpendResult`; `spend_movement(budget, feet: int) -> SpendResult`; `dash(budget) -> SpendResult`.
  - `InitiativeEntry(combatant_id: str, roll: Roll, dex_modifier: int, total: int)`; `roll_initiative(roller, combatants: Sequence[tuple[str, int]], *, player_values: Mapping[str, int] | None = None) -> list[InitiativeEntry]` — sorted into turn order; ties break by higher DEX modifier, then input order (documented determinism).

- [ ] **Step 1: Write the failing tests**

`tests/rules/test_action_economy.py`:
```python
import pytest

from dm_engine.rules.action_economy import dash, new_turn, spend, spend_movement


def test_new_turn_grants_full_budget():
    budget = new_turn(30)
    assert budget.movement_remaining == 30
    assert budget.action_available
    assert budget.bonus_action_available
    assert budget.reaction_available


def test_spend_action_once_only():
    budget = new_turn(30)
    first = spend(budget, "action")
    assert first.ok is True
    assert first.budget.action_available is False
    second = spend(first.budget, "action")
    assert second.ok is False
    assert "action" in second.reason
    # refusal returns the budget unchanged
    assert second.budget == first.budget


def test_bonus_action_and_reaction_are_separate_pools():
    budget = spend(new_turn(30), "action").budget
    assert spend(budget, "bonus_action").ok is True
    assert spend(budget, "reaction").ok is True


def test_spend_movement_within_speed():
    result = spend_movement(new_turn(30), 25)
    assert result.ok is True
    assert result.budget.movement_remaining == 5


def test_spend_movement_beyond_remaining_refused():
    result = spend_movement(new_turn(30), 35)
    assert result.ok is False
    assert "30" in result.reason


def test_negative_movement_raises():
    with pytest.raises(ValueError):
        spend_movement(new_turn(30), -5)


def test_dash_consumes_action_and_adds_speed():
    result = dash(new_turn(30))
    assert result.ok is True
    assert result.budget.action_available is False
    assert result.budget.movement_remaining == 60


def test_dash_without_action_refused():
    spent = spend(new_turn(30), "action").budget
    assert dash(spent).ok is False
```

`tests/rules/test_initiative.py`:
```python
from dm_engine.rules.dice import SeededDiceRoller
from dm_engine.rules.initiative import roll_initiative


def test_orders_by_total_descending():
    entries = roll_initiative(
        SeededDiceRoller(11), [("kira", 3), ("goblin-1", 2), ("goblin-2", 2), ("brother-aldric", 0)]
    )
    totals = [e.total for e in entries]
    assert totals == sorted(totals, reverse=True)
    assert {e.combatant_id for e in entries} == {"kira", "goblin-1", "goblin-2", "brother-aldric"}
    for e in entries:
        assert e.total == e.roll.total + e.dex_modifier


def test_ties_break_by_dex_then_input_order():
    class FixedRoller:
        def roll(self, notation, *, player_value=None, gm_only=False):
            from dm_engine.rules.dice import Roll

            value = player_value if player_value is not None else 10
            return Roll(
                notation=notation, rolls=[value], modifier=0, total=value,
                player_supplied=player_value is not None,
            )

    entries = roll_initiative(FixedRoller(), [("slow", 1), ("late", 2), ("early", 2)])
    assert [e.combatant_id for e in entries] == ["late", "early", "slow"]


def test_player_value_flags_player_roll():
    entries = roll_initiative(
        SeededDiceRoller(1), [("kira", 3), ("goblin-1", 2)], player_values={"kira": 18}
    )
    by_id = {e.combatant_id: e for e in entries}
    assert by_id["kira"].roll.player_supplied is True
    assert by_id["kira"].total == 21
    assert by_id["goblin-1"].roll.player_supplied is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_action_economy.py tests/rules/test_initiative.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write both modules**

`src/dm_engine/rules/action_economy.py`:
```python
"""Per-turn action economy: one action, one bonus action, one reaction,
speed-limited movement. Budgets are immutable; spending returns a new one.
Illegal spends return refusals (ok=False), not exceptions — M3 forwards
the reason to the player."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ActionKind = Literal["action", "bonus_action", "reaction"]


class TurnBudget(BaseModel):
    speed: int
    movement_remaining: int
    action_available: bool = True
    bonus_action_available: bool = True
    reaction_available: bool = True


def new_turn(speed: int) -> TurnBudget:
    if speed < 0:
        raise ValueError("speed cannot be negative")
    return TurnBudget(speed=speed, movement_remaining=speed)


class SpendResult(BaseModel):
    ok: bool
    reason: str | None = None
    budget: TurnBudget


def spend(budget: TurnBudget, kind: ActionKind) -> SpendResult:
    field = f"{kind}_available"
    if not getattr(budget, field):
        return SpendResult(
            ok=False,
            reason=f"no {kind.replace('_', ' ')} remaining this turn",
            budget=budget,
        )
    return SpendResult(ok=True, budget=budget.model_copy(update={field: False}))


def spend_movement(budget: TurnBudget, feet: int) -> SpendResult:
    if feet < 0:
        raise ValueError("movement cannot be negative")
    if feet > budget.movement_remaining:
        return SpendResult(
            ok=False,
            reason=f"only {budget.movement_remaining} ft of movement remaining",
            budget=budget,
        )
    return SpendResult(
        ok=True,
        budget=budget.model_copy(
            update={"movement_remaining": budget.movement_remaining - feet}
        ),
    )


def dash(budget: TurnBudget) -> SpendResult:
    """Dash: spend the action, gain speed's worth of extra movement."""
    result = spend(budget, "action")
    if not result.ok:
        return result
    b = result.budget
    return SpendResult(
        ok=True,
        budget=b.model_copy(update={"movement_remaining": b.movement_remaining + b.speed}),
    )
```

`src/dm_engine/rules/initiative.py`:
```python
"""Initiative: d20 + DEX modifier. Ties break by higher DEX modifier, then
by input order — deterministic so a resumed combat reproduces the order."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from dm_engine.rules.dice import DiceRoller, Roll


class InitiativeEntry(BaseModel):
    combatant_id: str
    roll: Roll
    dex_modifier: int
    total: int


def roll_initiative(
    roller: DiceRoller,
    combatants: Sequence[tuple[str, int]],
    *,
    player_values: Mapping[str, int] | None = None,
) -> list[InitiativeEntry]:
    """Roll for every (combatant_id, dex_modifier) and return turn order.

    `player_values` maps a combatant id to a player-reported natural d20.
    """
    if not combatants:
        raise ValueError("no combatants")
    player_values = player_values or {}
    indexed: list[tuple[int, InitiativeEntry]] = []
    for index, (combatant_id, dex_modifier) in enumerate(combatants):
        roll = roller.roll("1d20", player_value=player_values.get(combatant_id))
        entry = InitiativeEntry(
            combatant_id=combatant_id,
            roll=roll,
            dex_modifier=dex_modifier,
            total=roll.total + dex_modifier,
        )
        indexed.append((index, entry))
    indexed.sort(key=lambda pair: (-pair[1].total, -pair[1].dex_modifier, pair[0]))
    return [entry for _, entry in indexed]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_action_economy.py tests/rules/test_initiative.py -v`
Expected: PASS (11 tests)

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff check .`

```bash
git add src/dm_engine/rules/action_economy.py src/dm_engine/rules/initiative.py tests/rules/test_action_economy.py tests/rules/test_initiative.py
git commit -m "feat: add action economy and initiative"
```

---

### Task 7: Range bands (FC-4)

**Files:**
- Create: `src/dm_engine/rules/bands.py`
- Test: `tests/rules/test_bands.py`

**Interfaces:**
- Produces (frozen, FC-4): `Band = Literal["engaged", "near", "far", "distant"]`; `BAND_ORDER: tuple[Band, ...]`; `BAND_RANGE_FT: dict[Band, int]` (5/30/60/120); `band_index(band) -> int`; `distance_band(a: Band, b: Band, *, mutually_engaged: bool = False) -> Band`; `movement_cost_ft(from_band, to_band) -> int`; `provokes_opportunity_attacks(from_band, engaged_with: Set[str], *, disengaged: bool) -> frozenset[str]`; `RangeLegality = Literal["normal", "disadvantage", "out_of_range"]`; `weapon_range_legality(distance: Band, range_ft: int, long_range_ft: int | None = None, *, ranged: bool, attacker_engaged: bool = False) -> RangeLegality`; `aoe_targets(positions: Mapping[str, Band], target_band: Band, max_targets: int) -> list[str]`.
- Position semantics (M3 consumes as written): each combatant holds a band relative to the scene plus an `engaged_with` set. Two creatures' separation is the *wider* of their two scene bands; mutually engaged creatures are at `engaged` range. Moving between bands costs the difference of the bands' distances in feet (engaged→near 25 ft, near→far 30 ft, far→distant 60 ft).

- [ ] **Step 1: Write the failing tests**

`tests/rules/test_bands.py`:
```python
import pytest

from dm_engine.rules.bands import (
    BAND_ORDER,
    BAND_RANGE_FT,
    aoe_targets,
    band_index,
    distance_band,
    movement_cost_ft,
    provokes_opportunity_attacks,
    weapon_range_legality,
)


def test_fc4_bands_and_thresholds():
    assert BAND_ORDER == ("engaged", "near", "far", "distant")
    assert BAND_RANGE_FT == {"engaged": 5, "near": 30, "far": 60, "distant": 120}


def test_band_index_rejects_unknown():
    assert band_index("near") == 1
    with pytest.raises(ValueError):
        band_index("adjacent")


def test_distance_is_wider_band():
    assert distance_band("engaged", "near") == "near"
    assert distance_band("near", "distant") == "distant"
    assert distance_band("far", "far") == "far"
    assert distance_band("engaged", "engaged") == "engaged"


def test_mutually_engaged_overrides_bands():
    assert distance_band("near", "near", mutually_engaged=True) == "engaged"


def test_movement_costs():
    assert movement_cost_ft("engaged", "near") == 25
    assert movement_cost_ft("near", "far") == 30
    assert movement_cost_ft("far", "distant") == 60
    assert movement_cost_ft("engaged", "far") == 55
    assert movement_cost_ft("near", "engaged") == 25
    assert movement_cost_ft("near", "near") == 0


def test_leaving_engaged_without_disengage_provokes():
    result = provokes_opportunity_attacks(
        "engaged", {"goblin-1", "goblin-2"}, disengaged=False
    )
    assert result == frozenset({"goblin-1", "goblin-2"})


def test_disengage_prevents_opportunity_attacks():
    assert provokes_opportunity_attacks("engaged", {"goblin-1"}, disengaged=True) == frozenset()


def test_leaving_other_bands_never_provokes():
    assert provokes_opportunity_attacks("near", {"goblin-1"}, disengaged=False) == frozenset()


def test_melee_weapon_only_reaches_engaged():
    assert weapon_range_legality("engaged", 5, ranged=False) == "normal"
    assert weapon_range_legality("near", 5, ranged=False) == "out_of_range"


def test_bow_bands():
    # Shortbow 80/320: normal out to far (60 ft), long range at distant (120 ft).
    assert weapon_range_legality("near", 80, 320, ranged=True) == "normal"
    assert weapon_range_legality("far", 80, 320, ranged=True) == "normal"
    assert weapon_range_legality("distant", 80, 320, ranged=True) == "disadvantage"
    # Dagger thrown 20/60: near (30 ft) already exceeds the 20 ft normal
    # range, so it is a long-range throw; far is long range too; distant is out.
    assert weapon_range_legality("near", 20, 60, ranged=True) == "disadvantage"
    assert weapon_range_legality("far", 20, 60, ranged=True) == "disadvantage"
    assert weapon_range_legality("distant", 20, 60, ranged=True) == "out_of_range"


def test_ranged_attack_while_engaged_has_disadvantage():
    assert (
        weapon_range_legality("engaged", 80, 320, ranged=True, attacker_engaged=True)
        == "disadvantage"
    )
    # Melee is unaffected by being engaged (that's where it wants to be).
    assert (
        weapon_range_legality("engaged", 5, ranged=False, attacker_engaged=True) == "normal"
    )


def test_aoe_clusters_within_one_band():
    positions = {
        "goblin-1": "near", "goblin-2": "near", "goblin-3": "near",
        "wolf": "far", "kira": "engaged",
    }
    assert aoe_targets(positions, "near", max_targets=2) == ["goblin-1", "goblin-2"]
    assert aoe_targets(positions, "far", max_targets=6) == ["wolf"]
    assert aoe_targets(positions, "distant", max_targets=3) == []
    with pytest.raises(ValueError):
        aoe_targets(positions, "near", max_targets=0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_bands.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `dm_engine.rules.bands`)

- [ ] **Step 3: Write the bands module**

`src/dm_engine/rules/bands.py`:
```python
"""Range bands (FC-4): engaged/near/far/distant at 5/30/60/120 ft.

Positions are bands relative to the scene plus an `engaged_with` set (M3
state). Two creatures' separation is the wider of their two scene bands —
except mutually engaged creatures, which are at engaged range. Moving
between bands costs the difference of the bands' distances in feet.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Literal

Band = Literal["engaged", "near", "far", "distant"]

BAND_ORDER: tuple[Band, ...] = ("engaged", "near", "far", "distant")
BAND_RANGE_FT: dict[Band, int] = {"engaged": 5, "near": 30, "far": 60, "distant": 120}

RangeLegality = Literal["normal", "disadvantage", "out_of_range"]


def band_index(band: Band) -> int:
    try:
        return BAND_ORDER.index(band)
    except ValueError:
        raise ValueError(f"unknown band: {band!r}") from None


def distance_band(a: Band, b: Band, *, mutually_engaged: bool = False) -> Band:
    if mutually_engaged:
        return "engaged"
    return BAND_ORDER[max(band_index(a), band_index(b))]


def movement_cost_ft(from_band: Band, to_band: Band) -> int:
    return abs(BAND_RANGE_FT[from_band] - BAND_RANGE_FT[to_band])


def provokes_opportunity_attacks(
    from_band: Band, engaged_with: Set[str], *, disengaged: bool
) -> frozenset[str]:
    """Leaving engaged without Disengage provokes from everyone you were
    engaged with (FC-4). Movement in wider bands never provokes."""
    if band_index(from_band) != 0 or disengaged:
        return frozenset()
    return frozenset(engaged_with)


def weapon_range_legality(
    distance: Band,
    range_ft: int,
    long_range_ft: int | None = None,
    *,
    ranged: bool,
    attacker_engaged: bool = False,
) -> RangeLegality:
    """A weapon reaches a band when the band's distance fits its range.

    Long range imposes disadvantage, as does making a ranged attack while
    a hostile is within 5 ft (attacker_engaged).
    """
    d = BAND_RANGE_FT[distance]
    if d <= range_ft:
        if ranged and attacker_engaged:
            return "disadvantage"
        return "normal"
    if long_range_ft is not None and d <= long_range_ft:
        return "disadvantage"
    return "out_of_range"


def aoe_targets(
    positions: Mapping[str, Band], target_band: Band, max_targets: int
) -> list[str]:
    """AoE clustering (FC-4): up to max_targets creatures within the one
    targeted band, in deterministic insertion order."""
    if max_targets < 1:
        raise ValueError("max_targets must be >= 1")
    band_index(target_band)  # validate
    return [cid for cid, band in positions.items() if band == target_band][:max_targets]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_bands.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff check .`

```bash
git add src/dm_engine/rules/bands.py tests/rules/test_bands.py
git commit -m "feat: add range bands (FC-4)"
```

---

### Task 8: Death saves and concentration

**Files:**
- Create: `src/dm_engine/rules/death.py`, `src/dm_engine/rules/concentration.py`
- Test: `tests/rules/test_death.py`, `tests/rules/test_concentration.py`

**Interfaces:**
- Consumes: `ConditionEffects` from `conditions`.
- Produces (frozen):
  - `DeathSaveState(successes=0, failures=0, stable=False, dead=False)` — `dead=True` means the third failure landed; M3 maps it to `defeated` (narrative) or `dead` (hardcore) per campaign death mode, mechanics identical (FC-7).
  - `DeathEvent = Literal["success", "failure", "critical_failure", "stabilized", "regained_hp", "died"]`; `DeathSaveOutcome(state, event, regained_hp=False)`.
  - `apply_death_save(state, natural: int) -> DeathSaveOutcome`; `apply_damage_while_dying(state, damage: int, max_hp: int, *, critical: bool) -> DeathSaveOutcome`.
  - `concentration_save_dc(damage: int) -> int`; `concentration_broken_by_conditions(effects: ConditionEffects) -> bool`.
- Healing any amount of HP ends dying and resets the counters: M3 simply replaces the state with a fresh `DeathSaveState()` (documented in the module docstring; no dedicated function).

- [ ] **Step 1: Write the failing tests**

`tests/rules/test_death.py`:
```python
import pytest

from dm_engine.rules.death import DeathSaveState, apply_damage_while_dying, apply_death_save


def test_save_of_ten_or_more_succeeds():
    outcome = apply_death_save(DeathSaveState(), 10)
    assert outcome.event == "success"
    assert outcome.state.successes == 1


def test_save_below_ten_fails():
    outcome = apply_death_save(DeathSaveState(), 9)
    assert outcome.event == "failure"
    assert outcome.state.failures == 1


def test_three_successes_stabilize():
    state = DeathSaveState(successes=2)
    outcome = apply_death_save(state, 15)
    assert outcome.event == "stabilized"
    assert outcome.state.stable is True
    assert outcome.state.dead is False


def test_three_failures_die():
    state = DeathSaveState(failures=2)
    outcome = apply_death_save(state, 4)
    assert outcome.event == "failure"
    assert outcome.state.dead is True


def test_natural_one_counts_two_failures():
    outcome = apply_death_save(DeathSaveState(failures=1), 1)
    assert outcome.event == "critical_failure"
    assert outcome.state.failures == 3
    assert outcome.state.dead is True


def test_natural_twenty_regains_one_hp():
    outcome = apply_death_save(DeathSaveState(successes=1, failures=2), 20)
    assert outcome.event == "regained_hp"
    assert outcome.regained_hp is True
    # back on your feet: dying state fully resets
    assert outcome.state == DeathSaveState()


def test_full_sequence_success_fail_success_success():
    state = DeathSaveState()
    for natural, expected in ((12, "success"), (7, "failure"), (14, "success"), (18, "stabilized")):
        outcome = apply_death_save(state, natural)
        assert outcome.event == expected
        state = outcome.state
    assert state.stable and not state.dead


def test_cannot_save_when_not_dying():
    with pytest.raises(ValueError):
        apply_death_save(DeathSaveState(stable=True), 10)
    with pytest.raises(ValueError):
        apply_death_save(DeathSaveState(dead=True), 10)
    with pytest.raises(ValueError):
        apply_death_save(DeathSaveState(), 21)


def test_damage_while_dying_is_a_failure():
    outcome = apply_damage_while_dying(DeathSaveState(), 6, max_hp=20, critical=False)
    assert outcome.event == "failure"
    assert outcome.state.failures == 1


def test_critical_damage_while_dying_is_two_failures():
    outcome = apply_damage_while_dying(DeathSaveState(failures=1), 6, max_hp=20, critical=True)
    assert outcome.event == "critical_failure"
    assert outcome.state.failures == 3
    assert outcome.state.dead is True


def test_massive_damage_is_instant_death():
    outcome = apply_damage_while_dying(DeathSaveState(), 25, max_hp=20, critical=False)
    assert outcome.event == "died"
    assert outcome.state.dead is True


def test_damage_breaks_stability():
    outcome = apply_damage_while_dying(DeathSaveState(stable=True), 3, max_hp=20, critical=False)
    assert outcome.state.stable is False
    assert outcome.state.failures == 1
```

`tests/rules/test_concentration.py`:
```python
import pytest

from dm_engine.rules.concentration import (
    concentration_broken_by_conditions,
    concentration_save_dc,
)
from dm_engine.rules.conditions import effects_for


def test_dc_is_half_damage_minimum_ten():
    assert concentration_save_dc(7) == 10
    assert concentration_save_dc(20) == 10
    assert concentration_save_dc(22) == 11
    assert concentration_save_dc(26) == 13
    assert concentration_save_dc(45) == 22


def test_negative_damage_raises():
    with pytest.raises(ValueError):
        concentration_save_dc(-1)


def test_incapacitating_conditions_break_concentration():
    assert concentration_broken_by_conditions(effects_for({"stunned"})) is True
    assert concentration_broken_by_conditions(effects_for({"unconscious"})) is True
    assert concentration_broken_by_conditions(effects_for([], exhaustion_level=6)) is True


def test_ordinary_conditions_do_not_break_concentration():
    assert concentration_broken_by_conditions(effects_for({"prone"})) is False
    assert concentration_broken_by_conditions(effects_for({"poisoned"})) is False
    assert concentration_broken_by_conditions(effects_for([])) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_death.py tests/rules/test_concentration.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write both modules**

`src/dm_engine/rules/death.py`:
```python
"""Death saving throws — identical mechanics in both campaign death modes;
M3 maps the third failure to 'defeated' (narrative) or 'dead' (hardcore).

Healing any amount of HP ends dying: M3 replaces the state with a fresh
DeathSaveState(). A natural 20 does the same and restores 1 HP.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

DeathEvent = Literal[
    "success", "failure", "critical_failure", "stabilized", "regained_hp", "died"
]


class DeathSaveState(BaseModel):
    successes: int = 0
    failures: int = 0
    stable: bool = False
    dead: bool = False


class DeathSaveOutcome(BaseModel):
    state: DeathSaveState
    event: DeathEvent
    regained_hp: bool = False


def _with_failures(state: DeathSaveState, added: int) -> DeathSaveState:
    failures = state.failures + added
    return state.model_copy(
        update={"failures": failures, "stable": False, "dead": failures >= 3}
    )


def apply_death_save(state: DeathSaveState, natural: int) -> DeathSaveOutcome:
    """One death save: DC 10; natural 1 counts two failures; natural 20
    restores 1 HP and ends dying."""
    if state.dead or state.stable:
        raise ValueError("creature is not dying")
    if not 1 <= natural <= 20:
        raise ValueError(f"d20 natural out of range: {natural}")
    if natural == 20:
        return DeathSaveOutcome(state=DeathSaveState(), event="regained_hp", regained_hp=True)
    if natural == 1:
        return DeathSaveOutcome(state=_with_failures(state, 2), event="critical_failure")
    if natural >= 10:
        successes = state.successes + 1
        if successes >= 3:
            return DeathSaveOutcome(
                state=state.model_copy(update={"successes": successes, "stable": True}),
                event="stabilized",
            )
        return DeathSaveOutcome(
            state=state.model_copy(update={"successes": successes}), event="success"
        )
    return DeathSaveOutcome(state=_with_failures(state, 1), event="failure")


def apply_damage_while_dying(
    state: DeathSaveState, damage: int, max_hp: int, *, critical: bool
) -> DeathSaveOutcome:
    """Damage at 0 HP: one death-save failure (two on a crit); damage that
    meets the HP maximum kills outright. Damage also breaks stability."""
    if state.dead:
        raise ValueError("creature is already dead")
    if damage < 0 or max_hp < 1:
        raise ValueError("damage must be >= 0 and max_hp >= 1")
    if damage >= max_hp:
        return DeathSaveOutcome(
            state=state.model_copy(update={"stable": False, "dead": True}), event="died"
        )
    added = 2 if critical else 1
    event: DeathEvent = "critical_failure" if critical else "failure"
    return DeathSaveOutcome(state=_with_failures(state, added), event=event)
```

`src/dm_engine/rules/concentration.py`:
```python
"""Concentration: the save DC when damaged and the conditions that end it.
Single-spell exclusivity (one concentration effect at a time) is state,
enforced by M3 when a second concentration spell is cast."""

from __future__ import annotations

from dm_engine.rules.conditions import ConditionEffects


def concentration_save_dc(damage: int) -> int:
    """CON save DC when damaged while concentrating: half the damage, min 10."""
    if damage < 0:
        raise ValueError("damage cannot be negative")
    return max(10, damage // 2)


def concentration_broken_by_conditions(effects: ConditionEffects) -> bool:
    """Concentration ends when incapacitated (any incapacitating condition)
    or dead."""
    return not effects.can_take_actions or effects.dead
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_death.py tests/rules/test_concentration.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff check .`

```bash
git add src/dm_engine/rules/death.py src/dm_engine/rules/concentration.py tests/rules/test_death.py tests/rules/test_concentration.py
git commit -m "feat: add death saves and concentration"
```

---

### Task 9: Rests and progression

**Files:**
- Create: `src/dm_engine/rules/rests.py`, `src/dm_engine/rules/progression.py`
- Test: `tests/rules/test_rests.py`, `tests/rules/test_progression.py`

**Interfaces:**
- Consumes: `DiceRoller`, `Roll` from `dice`.
- Produces (frozen):
  - `HitDicePool(die: int, total: int, remaining: int)`; `ShortRestResult(healed: int, rolls: list[Roll], pool: HitDicePool)`; `spend_hit_dice(roller, pool, count, con_modifier, *, player_values: list[int] | None = None) -> ShortRestResult`; `LongRestResult(hit_dice_regained: int, pool: HitDicePool, exhaustion_level: int)`; `long_rest(pool, exhaustion_level=0) -> LongRestResult`. HP-to-max and spell-slot restoration on a long rest are state writes M3 applies; these functions compute the hit-dice and exhaustion deltas.
  - `XP_THRESHOLDS: tuple[int, ...]` (20 entries, cumulative XP to *reach* each level; index = level - 1); `level_for_xp(xp: int) -> int`; `xp_to_next_level(xp: int) -> int | None` (None at 20); `level_up_hp_gain(hit_die: int, con_modifier: int) -> int` (fixed-average rule: die//2 + 1 + CON, min 1); `max_hp_for_level(hit_die: int, con_modifier: int, level: int) -> int` (level 1 = max die + CON, min 1; each later level adds the fixed average).

- [ ] **Step 1: Write the failing tests**

`tests/rules/test_rests.py`:
```python
import pytest

from dm_engine.rules.dice import SeededDiceRoller
from dm_engine.rules.rests import HitDicePool, long_rest, spend_hit_dice


def test_spend_hit_dice_heals_and_depletes_pool():
    pool = HitDicePool(die=10, total=3, remaining=3)
    result = spend_hit_dice(SeededDiceRoller(9), pool, count=2, con_modifier=2)
    assert result.pool.remaining == 1
    assert len(result.rolls) == 2
    assert result.healed == sum(r.total + 2 for r in result.rolls)
    assert 6 <= result.healed <= 24  # 2 * (1..10 + 2)


def test_negative_con_cannot_reduce_healing_below_zero_per_die():
    # Each die heals max(0, roll + con); a -3 CON die can heal 0 but not negative.
    pool = HitDicePool(die=6, total=5, remaining=5)
    result = spend_hit_dice(SeededDiceRoller(2), pool, count=5, con_modifier=-3)
    assert result.healed >= 0
    assert result.healed == sum(max(0, r.total - 3) for r in result.rolls)


def test_player_supplied_hit_die_values():
    pool = HitDicePool(die=10, total=2, remaining=2)
    result = spend_hit_dice(
        SeededDiceRoller(1), pool, count=2, con_modifier=1, player_values=[7, 4]
    )
    assert result.healed == (7 + 1) + (4 + 1)
    assert all(r.player_supplied for r in result.rolls)


def test_cannot_overspend_hit_dice():
    pool = HitDicePool(die=8, total=3, remaining=1)
    with pytest.raises(ValueError):
        spend_hit_dice(SeededDiceRoller(1), pool, count=2, con_modifier=0)
    with pytest.raises(ValueError):
        spend_hit_dice(SeededDiceRoller(1), pool, count=0, con_modifier=0)
    with pytest.raises(ValueError):
        spend_hit_dice(SeededDiceRoller(1), pool, count=1, con_modifier=0, player_values=[5, 5])


def test_long_rest_regains_half_total_hit_dice_min_one():
    pool = HitDicePool(die=10, total=5, remaining=0)
    result = long_rest(pool)
    assert result.hit_dice_regained == 2  # 5 // 2
    assert result.pool.remaining == 2

    level1 = HitDicePool(die=10, total=1, remaining=0)
    assert long_rest(level1).hit_dice_regained == 1  # minimum 1


def test_long_rest_caps_at_total():
    pool = HitDicePool(die=10, total=4, remaining=3)
    result = long_rest(pool)
    assert result.pool.remaining == 4
    assert result.hit_dice_regained == 1


def test_long_rest_reduces_exhaustion_by_one():
    pool = HitDicePool(die=8, total=2, remaining=2)
    assert long_rest(pool, exhaustion_level=3).exhaustion_level == 2
    assert long_rest(pool, exhaustion_level=0).exhaustion_level == 0
```

`tests/rules/test_progression.py`:
```python
import pytest

from dm_engine.rules.progression import (
    XP_THRESHOLDS,
    level_for_xp,
    level_up_hp_gain,
    max_hp_for_level,
    xp_to_next_level,
)


def test_raw_xp_thresholds():
    assert len(XP_THRESHOLDS) == 20
    assert XP_THRESHOLDS[0] == 0
    assert XP_THRESHOLDS[1] == 300
    assert XP_THRESHOLDS[4] == 6500
    assert XP_THRESHOLDS[19] == 355000
    assert list(XP_THRESHOLDS) == sorted(XP_THRESHOLDS)


def test_level_for_xp_boundaries():
    assert level_for_xp(0) == 1
    assert level_for_xp(299) == 1
    assert level_for_xp(300) == 2
    assert level_for_xp(899) == 2
    assert level_for_xp(900) == 3
    assert level_for_xp(2700) == 4
    assert level_for_xp(6500) == 5
    assert level_for_xp(355000) == 20
    assert level_for_xp(9_999_999) == 20
    with pytest.raises(ValueError):
        level_for_xp(-1)


def test_xp_to_next_level():
    assert xp_to_next_level(0) == 300
    assert xp_to_next_level(250) == 50
    assert xp_to_next_level(300) == 600  # 900 - 300
    assert xp_to_next_level(355000) is None


def test_level_up_hp_gain_fixed_average():
    assert level_up_hp_gain(10, 2) == 8   # fighter, +2 CON
    assert level_up_hp_gain(6, 1) == 5    # wizard, +1 CON
    assert level_up_hp_gain(8, 2) == 7    # cleric, +2 CON
    assert level_up_hp_gain(6, -5) == 1   # never below 1 per level


def test_max_hp_hand_verified_levels_one_to_five():
    # Fighter d10 +2 CON: 12, 20, 28, 36, 44.
    assert [max_hp_for_level(10, 2, lvl) for lvl in range(1, 6)] == [12, 20, 28, 36, 44]
    # Wizard d6 +1 CON: 7, 12, 17, 22, 27.
    assert [max_hp_for_level(6, 1, lvl) for lvl in range(1, 6)] == [7, 12, 17, 22, 27]
    # Cleric d8 +2 CON: 10, 17, 24, 31, 38.
    assert [max_hp_for_level(8, 2, lvl) for lvl in range(1, 6)] == [10, 17, 24, 31, 38]


def test_max_hp_supports_one_to_twenty():
    assert max_hp_for_level(10, 2, 20) == 12 + 19 * 8  # 164
    with pytest.raises(ValueError):
        max_hp_for_level(10, 2, 21)
    with pytest.raises(ValueError):
        max_hp_for_level(10, 2, 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_rests.py tests/rules/test_progression.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write both modules**

`src/dm_engine/rules/rests.py`:
```python
"""Short and long rests (RAW).

Short rest: spend hit dice, each healing die + CON modifier (min 0 per
die). Long rest: regain half of total hit dice (min 1) and shed one level
of exhaustion. Restoring HP to max and refreshing spell slots are state
writes the M3 layer applies alongside these deltas.
"""

from __future__ import annotations

from pydantic import BaseModel

from dm_engine.rules.dice import DiceRoller, Roll


class HitDicePool(BaseModel):
    die: int  # faces, e.g. 10 for d10
    total: int
    remaining: int


class ShortRestResult(BaseModel):
    healed: int
    rolls: list[Roll]
    pool: HitDicePool


def spend_hit_dice(
    roller: DiceRoller,
    pool: HitDicePool,
    count: int,
    con_modifier: int,
    *,
    player_values: list[int] | None = None,
) -> ShortRestResult:
    if count < 1:
        raise ValueError("must spend at least one hit die")
    if count > pool.remaining:
        raise ValueError(f"only {pool.remaining} hit dice remaining")
    values: list[int | None] = list(player_values) if player_values else [None] * count
    if len(values) != count:
        raise ValueError(f"expected {count} player values, got {len(values)}")
    rolls: list[Roll] = []
    healed = 0
    for value in values:
        roll = roller.roll(f"1d{pool.die}", player_value=value)
        rolls.append(roll)
        healed += max(0, roll.total + con_modifier)
    return ShortRestResult(
        healed=healed,
        rolls=rolls,
        pool=pool.model_copy(update={"remaining": pool.remaining - count}),
    )


class LongRestResult(BaseModel):
    hit_dice_regained: int
    pool: HitDicePool
    exhaustion_level: int


def long_rest(pool: HitDicePool, exhaustion_level: int = 0) -> LongRestResult:
    regained = max(1, pool.total // 2)
    remaining = min(pool.total, pool.remaining + regained)
    return LongRestResult(
        hit_dice_regained=remaining - pool.remaining,
        pool=pool.model_copy(update={"remaining": remaining}),
        exhaustion_level=max(0, exhaustion_level - 1),
    )
```

`src/dm_engine/rules/progression.py`:
```python
"""XP thresholds and level-up math for levels 1-20 (hand-verified 1-5).

HP uses the fixed-average rule: level 1 is max die + CON; each later level
adds die//2 + 1 + CON (minimum 1 per level).
"""

from __future__ import annotations

# Cumulative XP required to reach each level (index = level - 1). RAW table.
XP_THRESHOLDS: tuple[int, ...] = (
    0, 300, 900, 2700, 6500, 14000, 23000, 34000, 48000, 64000,
    85000, 100000, 120000, 140000, 165000, 195000, 225000, 265000, 305000, 355000,
)


def level_for_xp(xp: int) -> int:
    if xp < 0:
        raise ValueError("xp cannot be negative")
    level = 1
    for index, threshold in enumerate(XP_THRESHOLDS):
        if xp >= threshold:
            level = index + 1
    return level


def xp_to_next_level(xp: int) -> int | None:
    """XP still needed for the next level; None at level 20."""
    level = level_for_xp(xp)
    if level >= 20:
        return None
    return XP_THRESHOLDS[level] - xp


def level_up_hp_gain(hit_die: int, con_modifier: int) -> int:
    return max(1, hit_die // 2 + 1 + con_modifier)


def max_hp_for_level(hit_die: int, con_modifier: int, level: int) -> int:
    if not 1 <= level <= 20:
        raise ValueError(f"level out of range: {level}")
    hp = max(1, hit_die + con_modifier)
    for _ in range(level - 1):
        hp += level_up_hp_gain(hit_die, con_modifier)
    return hp
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_rests.py tests/rules/test_progression.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff check .`

```bash
git add src/dm_engine/rules/rests.py src/dm_engine/rules/progression.py tests/rules/test_rests.py tests/rules/test_progression.py
git commit -m "feat: add rests and level progression"
```

---

### Task 10: Encounter difficulty math

**Files:**
- Create: `src/dm_engine/rules/encounters.py`
- Test: `tests/rules/test_encounters.py`

**Interfaces:**
- Produces (frozen): `XP_THRESHOLDS_BY_LEVEL: dict[int, tuple[int, int, int, int]]` (level → easy/medium/hard/deadly); `Difficulty = Literal["trivial", "easy", "medium", "hard", "deadly"]`; `encounter_multiplier(monster_count: int, party_size: int) -> float`; `party_thresholds(levels: Sequence[int]) -> tuple[int, int, int, int]`; `EncounterAssessment(total_monster_xp, multiplier, adjusted_xp, party_thresholds, difficulty)`; `assess_encounter(monster_xps: Sequence[int], party_levels: Sequence[int]) -> EncounterAssessment`. Budget is advisory (FC-7): M3/M4 report it and log deviations; nothing here refuses a fight.

- [ ] **Step 1: Write the failing tests**

`tests/rules/test_encounters.py`:
```python
import pytest

from dm_engine.rules.encounters import (
    XP_THRESHOLDS_BY_LEVEL,
    assess_encounter,
    encounter_multiplier,
    party_thresholds,
)


def test_dmg_threshold_table_shape_and_goldens():
    assert set(XP_THRESHOLDS_BY_LEVEL) == set(range(1, 21))
    assert XP_THRESHOLDS_BY_LEVEL[1] == (25, 50, 75, 100)
    assert XP_THRESHOLDS_BY_LEVEL[3] == (75, 150, 225, 400)
    assert XP_THRESHOLDS_BY_LEVEL[5] == (250, 500, 750, 1100)
    assert XP_THRESHOLDS_BY_LEVEL[20] == (2800, 5700, 8500, 12700)
    for level in range(1, 21):
        easy, medium, hard, deadly = XP_THRESHOLDS_BY_LEVEL[level]
        assert easy < medium < hard < deadly


def test_multiplier_by_monster_count():
    # Standard party of 3-5 uses the DMG base ladder.
    assert encounter_multiplier(1, 4) == 1.0
    assert encounter_multiplier(2, 4) == 1.5
    assert encounter_multiplier(3, 4) == 2.0
    assert encounter_multiplier(6, 4) == 2.0
    assert encounter_multiplier(7, 4) == 2.5
    assert encounter_multiplier(11, 4) == 3.0
    assert encounter_multiplier(15, 4) == 4.0


def test_small_party_shifts_multiplier_up():
    # Parties smaller than 3 treat the count one row higher (DMG).
    assert encounter_multiplier(1, 2) == 1.5
    assert encounter_multiplier(2, 2) == 2.0
    assert encounter_multiplier(15, 1) == 5.0


def test_large_party_shifts_multiplier_down():
    assert encounter_multiplier(1, 6) == 0.5
    assert encounter_multiplier(3, 7) == 1.5


def test_multiplier_input_validation():
    with pytest.raises(ValueError):
        encounter_multiplier(0, 4)
    with pytest.raises(ValueError):
        encounter_multiplier(1, 0)


def test_party_thresholds_sum_members():
    assert party_thresholds([1, 1]) == (50, 100, 150, 200)
    assert party_thresholds([3, 3, 2]) == (200, 400, 600, 1000)
    with pytest.raises(ValueError):
        party_thresholds([])
    with pytest.raises(ValueError):
        party_thresholds([21])


def test_goblin_ambush_golden():
    # Two goblins (50 XP each) vs a level-1 pair: 100 XP * 2.0 = 200 = deadly.
    result = assess_encounter([50, 50], [1, 1])
    assert result.total_monster_xp == 100
    assert result.multiplier == 2.0
    assert result.adjusted_xp == 200
    assert result.party_thresholds == (50, 100, 150, 200)
    assert result.difficulty == "deadly"


def test_difficulty_ladder():
    # Party of three level 2s: thresholds (150, 300, 450, 600).
    assert assess_encounter([100], [2, 2, 2]).difficulty == "trivial"
    assert assess_encounter([200], [2, 2, 2]).difficulty == "easy"
    assert assess_encounter([300], [2, 2, 2]).difficulty == "medium"
    assert assess_encounter([450], [2, 2, 2]).difficulty == "hard"
    assert assess_encounter([700], [2, 2, 2]).difficulty == "deadly"
    with pytest.raises(ValueError):
        assess_encounter([], [1])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/rules/test_encounters.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `dm_engine.rules.encounters`)

- [ ] **Step 3: Write the encounters module**

`src/dm_engine/rules/encounters.py`:
```python
"""Encounter difficulty math (DMG): per-character XP thresholds by level,
monster-count multipliers (party-size adjusted), and an advisory rating.

The budget is advisory (FC-7): the DM computes and reports it, may
deliberately deviate, and the deviation is logged. Nothing here refuses.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel

# level: (easy, medium, hard, deadly) per character — DMG table.
XP_THRESHOLDS_BY_LEVEL: dict[int, tuple[int, int, int, int]] = {
    1: (25, 50, 75, 100),
    2: (50, 100, 150, 200),
    3: (75, 150, 225, 400),
    4: (125, 250, 375, 500),
    5: (250, 500, 750, 1100),
    6: (300, 600, 900, 1400),
    7: (350, 750, 1100, 1700),
    8: (450, 900, 1400, 2100),
    9: (550, 1100, 1600, 2400),
    10: (600, 1200, 1900, 2800),
    11: (800, 1600, 2400, 3600),
    12: (1000, 2000, 3000, 4500),
    13: (1100, 2200, 3400, 5100),
    14: (1250, 2500, 3800, 5700),
    15: (1400, 2800, 4300, 6400),
    16: (1600, 3200, 4800, 7200),
    17: (2000, 3900, 5900, 8800),
    18: (2100, 4200, 6300, 9500),
    19: (2400, 4900, 7300, 10900),
    20: (2800, 5700, 8500, 12700),
}

_MULTIPLIER_LADDER = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0)

Difficulty = Literal["trivial", "easy", "medium", "hard", "deadly"]


def encounter_multiplier(monster_count: int, party_size: int) -> float:
    """DMG multiplier by monster count. Parties smaller than 3 shift one
    step up the ladder; parties of 6+ shift one step down."""
    if monster_count < 1:
        raise ValueError("encounter needs at least one monster")
    if party_size < 1:
        raise ValueError("party cannot be empty")
    if monster_count == 1:
        rung = 1
    elif monster_count == 2:
        rung = 2
    elif monster_count <= 6:
        rung = 3
    elif monster_count <= 10:
        rung = 4
    elif monster_count <= 14:
        rung = 5
    else:
        rung = 6
    if party_size < 3:
        rung += 1
    elif party_size >= 6:
        rung -= 1
    return _MULTIPLIER_LADDER[rung]


def party_thresholds(levels: Sequence[int]) -> tuple[int, int, int, int]:
    """Sum each member's per-level thresholds (easy, medium, hard, deadly)."""
    if not levels:
        raise ValueError("party cannot be empty")
    if any(level not in XP_THRESHOLDS_BY_LEVEL for level in levels):
        raise ValueError(f"levels must be 1-20: {list(levels)}")
    rows = [XP_THRESHOLDS_BY_LEVEL[level] for level in levels]
    easy, medium, hard, deadly = (sum(row[i] for row in rows) for i in range(4))
    return (easy, medium, hard, deadly)


class EncounterAssessment(BaseModel):
    total_monster_xp: int
    multiplier: float
    adjusted_xp: int
    party_thresholds: tuple[int, int, int, int]
    difficulty: Difficulty


def assess_encounter(
    monster_xps: Sequence[int], party_levels: Sequence[int]
) -> EncounterAssessment:
    if not monster_xps:
        raise ValueError("encounter needs at least one monster")
    total = sum(monster_xps)
    multiplier = encounter_multiplier(len(monster_xps), len(party_levels))
    adjusted = int(total * multiplier)
    thresholds = party_thresholds(party_levels)
    easy, medium, hard, deadly = thresholds
    difficulty: Difficulty
    if adjusted >= deadly:
        difficulty = "deadly"
    elif adjusted >= hard:
        difficulty = "hard"
    elif adjusted >= medium:
        difficulty = "medium"
    elif adjusted >= easy:
        difficulty = "easy"
    else:
        difficulty = "trivial"
    return EncounterAssessment(
        total_monster_xp=total,
        multiplier=multiplier,
        adjusted_xp=adjusted,
        party_thresholds=thresholds,
        difficulty=difficulty,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/rules/test_encounters.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Full suite, lint, commit**

Run: `uv run pytest && uv run ruff check .`

```bash
git add src/dm_engine/rules/encounters.py tests/rules/test_encounters.py
git commit -m "feat: add encounter difficulty math"
```

---

### Task 11: Milestone gate

**Files:** none new — verification only.

- [ ] **Step 1: Full verification**

```bash
uv run pytest -v
uv run pytest --cov=dm_engine.rules --cov-report=term-missing
uv run ruff check .
```

Expected: entire suite green (M1 tests + all `tests/rules/`); the coverage table shows **100%** for every file under `src/dm_engine/rules/`; ruff clean. If any rules file is below 100%, the uncovered lines are listed — add a meaningful test for each miss (an uncovered line means an untested rule branch; do not add `pragma: no cover`).

- [ ] **Step 2: Merge**

Merge `feat/m2-rules-engine` into `main` (no push). Then the orchestrator writes the M3 plan per the roadmap.
