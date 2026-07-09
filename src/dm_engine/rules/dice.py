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
