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
