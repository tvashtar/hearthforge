import pytest

from dm_engine.rules.bands import (
    BAND_ORDER,
    BAND_RANGE_FT,
    aoe_targets,
    band_index,
    distance_band,
    movement_cost_ft,
    provokes_opportunity_attacks,
    weapon_range_legality,
)


def test_fc4_bands_and_thresholds():
    assert BAND_ORDER == ("engaged", "near", "far", "distant")
    assert BAND_RANGE_FT == {"engaged": 5, "near": 30, "far": 60, "distant": 120}


def test_band_index_rejects_unknown():
    assert band_index("near") == 1
    with pytest.raises(ValueError):
        band_index("adjacent")


def test_distance_is_wider_band():
    assert distance_band("engaged", "near") == "near"
    assert distance_band("near", "distant") == "distant"
    assert distance_band("far", "far") == "far"
    assert distance_band("engaged", "engaged") == "engaged"


def test_mutually_engaged_overrides_bands():
    assert distance_band("near", "near", mutually_engaged=True) == "engaged"


def test_movement_costs():
    assert movement_cost_ft("engaged", "near") == 25
    assert movement_cost_ft("near", "far") == 30
    assert movement_cost_ft("far", "distant") == 60
    assert movement_cost_ft("engaged", "far") == 55
    assert movement_cost_ft("near", "engaged") == 25
    assert movement_cost_ft("near", "near") == 0


def test_leaving_engaged_without_disengage_provokes():
    result = provokes_opportunity_attacks(
        "engaged", {"goblin-1", "goblin-2"}, disengaged=False
    )
    assert result == frozenset({"goblin-1", "goblin-2"})


def test_disengage_prevents_opportunity_attacks():
    assert provokes_opportunity_attacks("engaged", {"goblin-1"}, disengaged=True) == frozenset()


def test_leaving_other_bands_never_provokes():
    assert provokes_opportunity_attacks("near", {"goblin-1"}, disengaged=False) == frozenset()


def test_melee_weapon_only_reaches_engaged():
    assert weapon_range_legality("engaged", 5, ranged=False) == "normal"
    assert weapon_range_legality("near", 5, ranged=False) == "out_of_range"


def test_bow_bands():
    # Shortbow 80/320: normal out to far (60 ft), long range at distant (120 ft).
    assert weapon_range_legality("near", 80, 320, ranged=True) == "normal"
    assert weapon_range_legality("far", 80, 320, ranged=True) == "normal"
    assert weapon_range_legality("distant", 80, 320, ranged=True) == "disadvantage"
    # Dagger thrown 20/60: near (30 ft) already exceeds the 20 ft normal
    # range, so it is a long-range throw; far is long range too; distant is out.
    assert weapon_range_legality("near", 20, 60, ranged=True) == "disadvantage"
    assert weapon_range_legality("far", 20, 60, ranged=True) == "disadvantage"
    assert weapon_range_legality("distant", 20, 60, ranged=True) == "out_of_range"


def test_ranged_attack_while_engaged_has_disadvantage():
    assert (
        weapon_range_legality("engaged", 80, 320, ranged=True, attacker_engaged=True)
        == "disadvantage"
    )
    # Melee is unaffected by being engaged (that's where it wants to be).
    assert (
        weapon_range_legality("engaged", 5, ranged=False, attacker_engaged=True) == "normal"
    )


def test_aoe_clusters_within_one_band():
    positions = {
        "goblin-1": "near", "goblin-2": "near", "goblin-3": "near",
        "wolf": "far", "kira": "engaged",
    }
    assert aoe_targets(positions, "near", max_targets=2) == ["goblin-1", "goblin-2"]
    assert aoe_targets(positions, "far", max_targets=6) == ["wolf"]
    assert aoe_targets(positions, "distant", max_targets=3) == []
    with pytest.raises(ValueError):
        aoe_targets(positions, "near", max_targets=0)
