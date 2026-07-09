from dm_engine.rules.attacks import resolve_attack_roll, roll_damage
from dm_engine.rules.dice import SeededDiceRoller


def test_hit_when_total_meets_ac():
    result = resolve_attack_roll(SeededDiceRoller(1), attack_bonus=5, target_ac=15, player_value=10)
    assert result.hit is True
    assert result.critical_hit is False


def test_miss_when_total_below_ac():
    result = resolve_attack_roll(SeededDiceRoller(1), attack_bonus=2, target_ac=15, player_value=12)
    assert result.hit is False


def test_natural_twenty_always_hits_and_crits():
    result = resolve_attack_roll(SeededDiceRoller(1), attack_bonus=0, target_ac=30, player_value=20)
    assert result.hit is True
    assert result.critical_hit is True


def test_natural_one_always_misses():
    result = resolve_attack_roll(SeededDiceRoller(1), attack_bonus=19, target_ac=5, player_value=1)
    assert result.hit is False
    assert result.critical_miss is True


def test_damage_roll_normal():
    result = roll_damage(SeededDiceRoller(5), "2d6+3")
    assert result.critical is False
    assert len(result.rolls) == 1
    assert result.total == result.rolls[0].total
    assert 5 <= result.total <= 15


def test_crit_doubles_dice_not_modifier():
    result = roll_damage(SeededDiceRoller(5), "2d6+3", critical=True)
    assert result.critical is True
    assert len(result.rolls) == 2
    assert result.rolls[0].notation == "2d6+3"
    assert result.rolls[1].notation == "2d6"  # extra dice carry no modifier
    assert result.rolls[1].modifier == 0
    assert result.total == result.rolls[0].total + result.rolls[1].total
    assert 7 <= result.total <= 27  # 4d6 + 3


def test_damage_never_negative():
    # 1d4-3 can roll below zero; damage floors at 0.
    totals = [roll_damage(SeededDiceRoller(seed), "1d4-3").total for seed in range(30)]
    assert all(t >= 0 for t in totals)
    assert 0 in totals  # some rolls actually hit the floor


def test_player_supplied_damage_adds_modifier_once():
    # Player reports raw dice total (crit dice included); engine adds +3 once.
    result = roll_damage(SeededDiceRoller(1), "2d6+3", critical=True, player_value=14)
    assert len(result.rolls) == 1
    assert result.rolls[0].player_supplied is True
    assert result.total == 17
