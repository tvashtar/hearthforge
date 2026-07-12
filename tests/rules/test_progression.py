import pytest

from dm_engine.rules.progression import (
    XP_THRESHOLDS,
    level_for_xp,
    level_up_hp_gain,
    max_hp_for_level,
    xp_for_level,
    xp_to_next_level,
)


def test_raw_xp_thresholds():
    assert len(XP_THRESHOLDS) == 20
    assert XP_THRESHOLDS[0] == 0
    assert XP_THRESHOLDS[1] == 300
    assert XP_THRESHOLDS[4] == 6500
    assert XP_THRESHOLDS[19] == 355000
    assert list(XP_THRESHOLDS) == sorted(XP_THRESHOLDS)


def test_level_for_xp_boundaries():
    assert level_for_xp(0) == 1
    assert level_for_xp(299) == 1
    assert level_for_xp(300) == 2
    assert level_for_xp(899) == 2
    assert level_for_xp(900) == 3
    assert level_for_xp(2700) == 4
    assert level_for_xp(6500) == 5
    assert level_for_xp(355000) == 20
    assert level_for_xp(9_999_999) == 20
    with pytest.raises(ValueError):
        level_for_xp(-1)


def test_xp_for_level_is_the_inverse_of_level_for_xp():
    assert xp_for_level(1) == 0
    assert xp_for_level(2) == 300
    assert xp_for_level(3) == 900
    assert xp_for_level(20) == 355000
    for level in range(1, 21):
        assert level_for_xp(xp_for_level(level)) == level
    with pytest.raises(ValueError):
        xp_for_level(0)
    with pytest.raises(ValueError):
        xp_for_level(21)


def test_xp_to_next_level():
    assert xp_to_next_level(0) == 300
    assert xp_to_next_level(250) == 50
    assert xp_to_next_level(300) == 600  # 900 - 300
    assert xp_to_next_level(355000) is None


def test_level_up_hp_gain_fixed_average():
    assert level_up_hp_gain(10, 2) == 8   # fighter, +2 CON
    assert level_up_hp_gain(6, 1) == 5    # wizard, +1 CON
    assert level_up_hp_gain(8, 2) == 7    # cleric, +2 CON
    assert level_up_hp_gain(6, -5) == 1   # never below 1 per level


def test_max_hp_hand_verified_levels_one_to_five():
    # Fighter d10 +2 CON: 12, 20, 28, 36, 44.
    assert [max_hp_for_level(10, 2, lvl) for lvl in range(1, 6)] == [12, 20, 28, 36, 44]
    # Wizard d6 +1 CON: 7, 12, 17, 22, 27.
    assert [max_hp_for_level(6, 1, lvl) for lvl in range(1, 6)] == [7, 12, 17, 22, 27]
    # Cleric d8 +2 CON: 10, 17, 24, 31, 38.
    assert [max_hp_for_level(8, 2, lvl) for lvl in range(1, 6)] == [10, 17, 24, 31, 38]


def test_max_hp_supports_one_to_twenty():
    assert max_hp_for_level(10, 2, 20) == 12 + 19 * 8  # 164
    with pytest.raises(ValueError):
        max_hp_for_level(10, 2, 21)
    with pytest.raises(ValueError):
        max_hp_for_level(10, 2, 0)
