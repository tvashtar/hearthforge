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
