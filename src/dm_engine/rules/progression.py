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


def xp_for_level(level: int) -> int:
    """Minimum cumulative XP for `level` — the inverse of level_for_xp."""
    if not 1 <= level <= 20:
        raise ValueError(f"level out of range: {level}")
    return XP_THRESHOLDS[level - 1]


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
