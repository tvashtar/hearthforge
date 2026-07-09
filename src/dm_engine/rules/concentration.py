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
