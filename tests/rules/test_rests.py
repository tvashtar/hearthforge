import pytest

from dm_engine.rules.dice import SeededDiceRoller
from dm_engine.rules.rests import HitDicePool, long_rest, spend_hit_dice


def test_spend_hit_dice_heals_and_depletes_pool():
    pool = HitDicePool(die=10, total=3, remaining=3)
    result = spend_hit_dice(SeededDiceRoller(9), pool, count=2, con_modifier=2)
    assert result.pool.remaining == 1
    assert len(result.rolls) == 2
    assert result.healed == sum(r.total + 2 for r in result.rolls)
    assert 6 <= result.healed <= 24  # 2 * (1..10 + 2)


def test_negative_con_cannot_reduce_healing_below_zero_per_die():
    # Each die heals max(0, roll + con); a -3 CON die can heal 0 but not negative.
    pool = HitDicePool(die=6, total=5, remaining=5)
    result = spend_hit_dice(SeededDiceRoller(2), pool, count=5, con_modifier=-3)
    assert result.healed >= 0
    assert result.healed == sum(max(0, r.total - 3) for r in result.rolls)


def test_player_supplied_hit_die_values():
    pool = HitDicePool(die=10, total=2, remaining=2)
    result = spend_hit_dice(
        SeededDiceRoller(1), pool, count=2, con_modifier=1, player_values=[7, 4]
    )
    assert result.healed == (7 + 1) + (4 + 1)
    assert all(r.player_supplied for r in result.rolls)


def test_cannot_overspend_hit_dice():
    pool = HitDicePool(die=8, total=3, remaining=1)
    with pytest.raises(ValueError):
        spend_hit_dice(SeededDiceRoller(1), pool, count=2, con_modifier=0)
    with pytest.raises(ValueError):
        spend_hit_dice(SeededDiceRoller(1), pool, count=0, con_modifier=0)
    with pytest.raises(ValueError):
        spend_hit_dice(SeededDiceRoller(1), pool, count=1, con_modifier=0, player_values=[5, 5])


def test_long_rest_regains_half_total_hit_dice_min_one():
    pool = HitDicePool(die=10, total=5, remaining=0)
    result = long_rest(pool)
    assert result.hit_dice_regained == 2  # 5 // 2
    assert result.pool.remaining == 2

    level1 = HitDicePool(die=10, total=1, remaining=0)
    assert long_rest(level1).hit_dice_regained == 1  # minimum 1


def test_long_rest_caps_at_total():
    pool = HitDicePool(die=10, total=4, remaining=3)
    result = long_rest(pool)
    assert result.pool.remaining == 4
    assert result.hit_dice_regained == 1


def test_long_rest_reduces_exhaustion_by_one():
    pool = HitDicePool(die=8, total=2, remaining=2)
    assert long_rest(pool, exhaustion_level=3).exhaustion_level == 2
    assert long_rest(pool, exhaustion_level=0).exhaustion_level == 0
