import pytest
from hypothesis import given
from hypothesis import strategies as st

from dm_engine.rules.dice import Roll, SeededDiceRoller, parse_notation


def test_parse_notation_forms():
    assert parse_notation("1d20+5") == (1, 20, 5)
    assert parse_notation("8d6") == (8, 6, 0)
    assert parse_notation("d20") == (1, 20, 0)
    assert parse_notation("2d8-1") == (2, 8, -1)
    assert parse_notation(" 1D12 + 3 ") == (1, 12, 3)


@pytest.mark.parametrize("bad", ["", "d", "0d6", "1d1", "20", "1d20+", "fireball", "1d20+5+3"])
def test_parse_notation_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_notation(bad)


def test_same_seed_reproduces_sequence():
    a = SeededDiceRoller(42)
    b = SeededDiceRoller(42)
    assert [a.roll("1d20").total for _ in range(20)] == [
        b.roll("1d20").total for _ in range(20)
    ]


def test_different_seeds_diverge():
    a = [SeededDiceRoller(1).roll("1d20").total for _ in range(10)]
    b = [SeededDiceRoller(2).roll("1d20").total for _ in range(10)]
    assert a != b


def test_player_value_bypasses_rng():
    roll = SeededDiceRoller(1).roll("1d20+5", player_value=17)
    assert roll == Roll(
        notation="1d20+5", rolls=[17], modifier=5, total=22, player_supplied=True
    )


def test_gm_only_flag_carries():
    assert SeededDiceRoller(1).roll("1d20", gm_only=True).gm_only is True


@given(
    count=st.integers(min_value=1, max_value=20),
    sides=st.sampled_from([4, 6, 8, 10, 12, 20, 100]),
    modifier=st.integers(min_value=-10, max_value=10),
    seed=st.integers(min_value=0, max_value=2**32),
)
def test_roll_bounds_and_arithmetic(count, sides, modifier, seed):
    sign = "+" if modifier >= 0 else "-"
    notation = f"{count}d{sides}{sign}{abs(modifier)}"
    roll = SeededDiceRoller(seed).roll(notation)
    assert len(roll.rolls) == count
    assert all(1 <= r <= sides for r in roll.rolls)
    assert roll.total == sum(roll.rolls) + modifier
    assert count + modifier <= roll.total <= count * sides + modifier
    assert roll.player_supplied is False
