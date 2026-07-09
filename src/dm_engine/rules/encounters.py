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
