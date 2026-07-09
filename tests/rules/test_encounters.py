import pytest

from dm_engine.rules.encounters import (
    XP_THRESHOLDS_BY_LEVEL,
    assess_encounter,
    encounter_multiplier,
    party_thresholds,
)


def test_dmg_threshold_table_shape_and_goldens():
    assert set(XP_THRESHOLDS_BY_LEVEL) == set(range(1, 21))
    assert XP_THRESHOLDS_BY_LEVEL[1] == (25, 50, 75, 100)
    assert XP_THRESHOLDS_BY_LEVEL[3] == (75, 150, 225, 400)
    assert XP_THRESHOLDS_BY_LEVEL[5] == (250, 500, 750, 1100)
    assert XP_THRESHOLDS_BY_LEVEL[20] == (2800, 5700, 8500, 12700)
    for level in range(1, 21):
        easy, medium, hard, deadly = XP_THRESHOLDS_BY_LEVEL[level]
        assert easy < medium < hard < deadly


def test_multiplier_by_monster_count():
    # Standard party of 3-5 uses the DMG base ladder.
    assert encounter_multiplier(1, 4) == 1.0
    assert encounter_multiplier(2, 4) == 1.5
    assert encounter_multiplier(3, 4) == 2.0
    assert encounter_multiplier(6, 4) == 2.0
    assert encounter_multiplier(7, 4) == 2.5
    assert encounter_multiplier(11, 4) == 3.0
    assert encounter_multiplier(15, 4) == 4.0


def test_small_party_shifts_multiplier_up():
    # Parties smaller than 3 treat the count one row higher (DMG).
    assert encounter_multiplier(1, 2) == 1.5
    assert encounter_multiplier(2, 2) == 2.0
    assert encounter_multiplier(15, 1) == 5.0


def test_large_party_shifts_multiplier_down():
    assert encounter_multiplier(1, 6) == 0.5
    assert encounter_multiplier(3, 7) == 1.5


def test_multiplier_input_validation():
    with pytest.raises(ValueError):
        encounter_multiplier(0, 4)
    with pytest.raises(ValueError):
        encounter_multiplier(1, 0)


def test_party_thresholds_sum_members():
    assert party_thresholds([1, 1]) == (50, 100, 150, 200)
    assert party_thresholds([3, 3, 2]) == (200, 400, 600, 1000)
    with pytest.raises(ValueError):
        party_thresholds([])
    with pytest.raises(ValueError):
        party_thresholds([21])


def test_goblin_ambush_golden():
    # Two goblins (50 XP each) vs a level-1 pair: 100 XP * 2.0 = 200 = deadly.
    result = assess_encounter([50, 50], [1, 1])
    assert result.total_monster_xp == 100
    assert result.multiplier == 2.0
    assert result.adjusted_xp == 200
    assert result.party_thresholds == (50, 100, 150, 200)
    assert result.difficulty == "deadly"


def test_difficulty_ladder():
    # Party of three level 2s: thresholds (150, 300, 450, 600).
    assert assess_encounter([100], [2, 2, 2]).difficulty == "trivial"
    assert assess_encounter([200], [2, 2, 2]).difficulty == "easy"
    assert assess_encounter([300], [2, 2, 2]).difficulty == "medium"
    assert assess_encounter([450], [2, 2, 2]).difficulty == "hard"
    assert assess_encounter([700], [2, 2, 2]).difficulty == "deadly"
    with pytest.raises(ValueError):
        assess_encounter([], [1])
