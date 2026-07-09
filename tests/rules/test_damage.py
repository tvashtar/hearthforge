import pytest

from dm_engine.rules.damage import DAMAGE_TYPES, apply_mitigation


def test_thirteen_raw_damage_types():
    assert DAMAGE_TYPES == {
        "acid", "bludgeoning", "cold", "fire", "force", "lightning", "necrotic",
        "piercing", "poison", "psychic", "radiant", "slashing", "thunder",
    }


def test_plain_damage_passes_through():
    result = apply_mitigation(11, "slashing")
    assert result.final == 11
    assert result.applied == []


def test_resistance_halves_rounding_down():
    assert apply_mitigation(11, "fire", resistances={"fire"}).final == 5


def test_vulnerability_doubles():
    assert apply_mitigation(7, "cold", vulnerabilities={"cold"}).final == 14


def test_immunity_zeroes():
    result = apply_mitigation(50, "poison", immunities={"poison"})
    assert result.final == 0
    assert result.applied == ["immunity"]


def test_reduction_applies_before_resistance():
    # RAW order golden: (11 - 3) // 2 == 4, never (11 // 2) - 3 == 2.
    result = apply_mitigation(
        11, "slashing", resistances={"slashing"}, reduction=3
    )
    assert result.after_reduction == 8
    assert result.final == 4
    assert result.applied == ["reduction:3", "resistance"]


def test_resistance_then_vulnerability_ordering():
    # Halve (floor) first, then double: 11 -> 5 -> 10, not back to 11.
    result = apply_mitigation(
        11, "fire", resistances={"fire"}, vulnerabilities={"fire"}
    )
    assert result.final == 10
    assert result.applied == ["resistance", "vulnerability"]


def test_reduction_cannot_go_negative():
    assert apply_mitigation(2, "piercing", reduction=5).final == 0


def test_unrelated_defenses_do_not_apply():
    result = apply_mitigation(
        9, "fire", resistances={"cold"}, vulnerabilities={"acid"}, immunities={"poison"}
    )
    assert result.final == 9


def test_unknown_damage_type_raises():
    with pytest.raises(ValueError):
        apply_mitigation(5, "emotional")


def test_negative_damage_raises():
    with pytest.raises(ValueError):
        apply_mitigation(-1, "fire")


def test_negative_reduction_raises():
    with pytest.raises(ValueError):
        apply_mitigation(5, "fire", reduction=-1)
