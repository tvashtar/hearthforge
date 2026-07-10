"""Damage mitigation in RAW order.

Flat reductions apply first, then immunity, then resistance (halve, round
down), then vulnerability (double). Multiple instances of resistance or
vulnerability to one type count only once (sets make that structural).

`defense_entry_applies` decides whether one SRD resistance / immunity /
vulnerability entry covers a given hit — including the compound
"…from nonmagical weapons" phrases that magic weapons bypass (2014 RAW).
"""

from __future__ import annotations

import re
from collections.abc import Collection

from pydantic import BaseModel

DAMAGE_TYPES = frozenset({
    "acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic",
    "piercing", "poison", "psychic", "radiant", "slashing", "thunder",
})

_TYPE_WORDS = {t: re.compile(rf"\b{t}\b") for t in DAMAGE_TYPES}


def defense_entry_applies(
    entry: str, damage_type: str, *, is_magical: bool = False
) -> bool:
    """Whether one SRD defense entry applies to a hit of `damage_type`.

    Entries in the seeded SRD 5.1 data are a small closed set: either a bare
    type ("fire") or a compound phrase with a caveat —

    - "bludgeoning, piercing, and slashing from nonmagical weapons"
    - "bludgeoning, piercing, and slashing from nonmagical attacks (from stoneskin)"
    - "bludgeoning, piercing, and slashing from nonmagical weapons that aren't adamantine"
    - "bludgeoning, piercing, and slashing from nonmagical weapons that aren't silvered"
    - "piercing and slashing from nonmagical weapons that aren't adamantine"
    - "piercing from magic weapons wielded by good creatures"
    - "damage from spells"

    A "nonmagical" caveat is bypassed by magical attacks (2014 RAW); the
    rakshasa's "from magic weapons" vulnerability requires a magical attack.
    Silvered/adamantine weapon materials and wielder alignment are not
    modeled, so those clauses conservatively fall back to the magic check.
    "damage from spells" names no damage type, so it never matches a typed
    weapon hit here.
    """
    if damage_type not in DAMAGE_TYPES:
        raise ValueError(f"unknown damage type: {damage_type!r}")
    text = entry.lower()
    if not _TYPE_WORDS[damage_type].search(text):
        return False
    if "nonmagical" in text:
        return not is_magical
    if "magic weapons" in text:
        return is_magical
    return True


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
