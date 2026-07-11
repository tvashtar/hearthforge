from dm_engine.rules.active_effects import (
    clock_expired,
    effective_ac,
    remaining_minutes,
    validate_mechanics,
)


# -- effective_ac fold ------------------------------------------------------


def test_effective_ac_no_effects_is_base():
    assert effective_ac(12, []) == 12


def test_ac_override_raises_base_but_never_lowers_it():
    # mage armor on an unarmored wizard: 12 -> 15
    assert effective_ac(12, [{"ac_override": 15}]) == 15
    # mage armor on plate: the override never lowers real armor
    assert effective_ac(18, [{"ac_override": 15}]) == 18


def test_ac_bonus_stacks_on_top_of_best_override():
    # mage armor + shield: max(12, 15) + 5 = 20
    assert effective_ac(12, [{"ac_override": 15}, {"ac_bonus": 5}]) == 20
    # two bonuses stack (shield + cover)
    assert effective_ac(14, [{"ac_bonus": 5}, {"ac_bonus": 2}]) == 21


def test_unknown_and_non_ac_mechanics_are_ignored_by_the_fold():
    assert effective_ac(12, [{"note": "outlined in faerie fire"}]) == 12


# -- expiry ------------------------------------------------------------------


def test_clock_expiry_boundary():
    eff = {"expires_day": 1, "expires_minutes": 960}
    assert clock_expired(eff, 1, 959) is False
    assert clock_expired(eff, 1, 960) is True   # expires exactly on the tick
    assert clock_expired(eff, 2, 0) is True
    assert clock_expired({"expires_day": None, "expires_minutes": None}, 9, 0) is False


def test_remaining_minutes_spans_days():
    eff = {"expires_day": 2, "expires_minutes": 120}
    assert remaining_minutes(eff, 1, 480) == 1080
    assert remaining_minutes({"expires_day": None, "expires_minutes": None}, 1, 0) is None


# -- mechanics validation ----------------------------------------------------


def test_validate_mechanics_accepts_known_shape():
    assert validate_mechanics({}) is None
    assert validate_mechanics({"ac_override": 15, "note": "mage armor"}) is None
    assert validate_mechanics({"ac_bonus": 5}) is None


def test_validate_mechanics_refuses_bad_input():
    assert validate_mechanics(["ac_override", 15]) is not None
    assert validate_mechanics({"ac_override": "15"}) is not None
    assert validate_mechanics({"ac_bonus": True}) is not None  # bool is not an int here
    assert validate_mechanics({"note": "  "}) is not None
    assert "unknown mechanic" in validate_mechanics({"advantage": True})
