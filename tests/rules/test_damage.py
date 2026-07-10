import pytest
from hypothesis import given
from hypothesis import strategies as st

from dm_engine.rules.damage import DAMAGE_TYPES, apply_mitigation, defense_entry_applies


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


# --- defense_entry_applies: SRD caveat phrases ----------------------------

# The closed set of compound "nonmagical" phrases in the seeded SRD data.
NONMAGICAL_ENTRIES = [
    "bludgeoning, piercing, and slashing from nonmagical weapons",
    "bludgeoning, piercing, and slashing from nonmagical attacks (from stoneskin)",
    "bludgeoning, piercing, and slashing from nonmagical weapons that aren't adamantine",
    "bludgeoning, piercing, and slashing from nonmagical weapons that aren't silvered",
]


def test_plain_entry_applies_regardless_of_magic():
    assert defense_entry_applies("slashing", "slashing", is_magical=False)
    assert defense_entry_applies("slashing", "slashing", is_magical=True)


def test_plain_entry_wrong_type_never_applies():
    assert not defense_entry_applies("fire", "slashing")
    assert not defense_entry_applies("fire", "slashing", is_magical=True)


@pytest.mark.parametrize("entry", NONMAGICAL_ENTRIES)
@pytest.mark.parametrize("dtype", ["bludgeoning", "piercing", "slashing"])
def test_nonmagical_entry_applies_to_nonmagical_attack(entry, dtype):
    assert defense_entry_applies(entry, dtype, is_magical=False)


@pytest.mark.parametrize("entry", NONMAGICAL_ENTRIES)
@pytest.mark.parametrize("dtype", ["bludgeoning", "piercing", "slashing"])
def test_magical_attack_bypasses_nonmagical_entry(entry, dtype):
    assert not defense_entry_applies(entry, dtype, is_magical=True)


def test_nonmagical_entry_narrower_type_list():
    entry = "piercing and slashing from nonmagical weapons that aren't adamantine"
    assert defense_entry_applies(entry, "piercing")
    assert not defense_entry_applies(entry, "bludgeoning")


def test_magic_weapons_vulnerability_requires_magical_attack():
    entry = "piercing from magic weapons wielded by good creatures"
    assert defense_entry_applies(entry, "piercing", is_magical=True)
    assert not defense_entry_applies(entry, "piercing", is_magical=False)


def test_damage_from_spells_never_matches_a_weapon_type():
    for dtype in DAMAGE_TYPES:
        assert not defense_entry_applies("damage from spells", dtype)
        assert not defense_entry_applies("damage from spells", dtype, is_magical=True)


def test_defense_entry_unknown_damage_type_raises():
    with pytest.raises(ValueError):
        defense_entry_applies("slashing", "emotional")


@given(
    dtype=st.sampled_from(sorted(DAMAGE_TYPES)),
    entry=st.sampled_from(NONMAGICAL_ENTRIES),
)
def test_no_type_triggers_a_nonmagical_entry_magically(dtype, entry):
    # Property: a magical attack never suffers a nonmagical-only defense,
    # whatever the damage type.
    assert not defense_entry_applies(entry, dtype, is_magical=True)


@given(dtype=st.sampled_from(sorted(DAMAGE_TYPES)), is_magical=st.booleans())
def test_bare_type_entry_matches_exactly_itself(dtype, is_magical):
    # Property: a bare-type entry applies iff the types are equal, and the
    # magic flag is irrelevant without a caveat.
    for entry in DAMAGE_TYPES:
        assert defense_entry_applies(entry, dtype, is_magical=is_magical) == (
            entry == dtype
        )
