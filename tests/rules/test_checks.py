import pytest

from dm_engine.rules.checks import (
    ability_modifier,
    combine_advantage,
    proficiency_bonus,
    resolve_check,
    roll_d20,
)
from dm_engine.rules.dice import SeededDiceRoller


def test_ability_modifier_raw_table():
    assert ability_modifier(1) == -5
    assert ability_modifier(8) == -1
    assert ability_modifier(10) == 0
    assert ability_modifier(11) == 0
    assert ability_modifier(15) == 2
    assert ability_modifier(20) == 5
    assert ability_modifier(30) == 10


@pytest.mark.parametrize("bad", [0, 31, -3])
def test_ability_modifier_range(bad):
    with pytest.raises(ValueError):
        ability_modifier(bad)


def test_proficiency_bonus_progression():
    levels = (1, 4, 5, 8, 9, 12, 13, 16, 17, 20)
    assert [proficiency_bonus(x) for x in levels] == [2, 2, 3, 3, 4, 4, 5, 5, 6, 6]
    with pytest.raises(ValueError):
        proficiency_bonus(21)


def test_advantage_stacking_rules():
    # RAW: sources never stack; any adv + any dis cancel to normal.
    assert combine_advantage(False, False) == "normal"
    assert combine_advantage(True, False) == "advantage"
    assert combine_advantage(False, True) == "disadvantage"
    assert combine_advantage(True, True) == "normal"


def test_normal_roll_uses_one_die():
    result = roll_d20(SeededDiceRoller(3), modifier=4)
    assert len(result.rolls) == 1
    assert result.natural == result.rolls[0].rolls[0]
    assert result.total == result.natural + 4


def test_advantage_picks_higher_die():
    result = roll_d20(SeededDiceRoller(7), modifier=3, mode="advantage")
    naturals = [r.rolls[0] for r in result.rolls]
    assert len(naturals) == 2
    assert result.natural == max(naturals)
    assert result.total == result.natural + 3


def test_disadvantage_picks_lower_die():
    result = roll_d20(SeededDiceRoller(7), modifier=0, mode="disadvantage")
    naturals = [r.rolls[0] for r in result.rolls]
    assert result.natural == min(naturals)


def test_player_value_skips_engine_dice():
    result = roll_d20(SeededDiceRoller(1), modifier=2, player_value=15)
    assert len(result.rolls) == 1
    assert result.rolls[0].player_supplied is True
    assert result.natural == 15
    assert result.total == 17


def test_gm_only_propagates_to_rolls():
    result = roll_d20(SeededDiceRoller(1), modifier=0, mode="advantage", gm_only=True)
    assert all(r.gm_only for r in result.rolls)


def test_check_meets_dc_succeeds():
    # "Meets it, beats it": total == DC is a success.
    result = resolve_check(SeededDiceRoller(1), modifier=2, dc=15, player_value=13)
    assert result.success is True
    assert result.margin == 0


def test_check_below_dc_fails():
    result = resolve_check(SeededDiceRoller(1), modifier=0, dc=15, player_value=9)
    assert result.success is False
    assert result.margin == -6
