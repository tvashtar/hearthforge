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
