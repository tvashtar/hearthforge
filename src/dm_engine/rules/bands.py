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

    Bands are a coarse abstraction, not exact geometry: any ranged/thrown
    attack is always at normal range against engaged or near targets,
    regardless of the weapon's stated range in feet (a target described as
    "near" may be as close as just past engaged). Far and distant depend on
    the literal range/long-range comparison.

    Long range imposes disadvantage, as does making a ranged attack while
    a hostile is within 5 ft (attacker_engaged).
    """
    d = BAND_RANGE_FT[distance]
    normal = d <= range_ft or (ranged and band_index(distance) <= band_index("near"))
    if normal:
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
