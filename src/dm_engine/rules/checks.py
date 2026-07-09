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
