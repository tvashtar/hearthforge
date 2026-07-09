import pytest

from dm_engine.rules.action_economy import dash, new_turn, spend, spend_movement


def test_new_turn_grants_full_budget():
    budget = new_turn(30)
    assert budget.movement_remaining == 30
    assert budget.action_available
    assert budget.bonus_action_available
    assert budget.reaction_available


def test_spend_action_once_only():
    budget = new_turn(30)
    first = spend(budget, "action")
    assert first.ok is True
    assert first.budget.action_available is False
    second = spend(first.budget, "action")
    assert second.ok is False
    assert "action" in second.reason
    # refusal returns the budget unchanged
    assert second.budget == first.budget


def test_bonus_action_and_reaction_are_separate_pools():
    budget = spend(new_turn(30), "action").budget
    assert spend(budget, "bonus_action").ok is True
    assert spend(budget, "reaction").ok is True


def test_spend_movement_within_speed():
    result = spend_movement(new_turn(30), 25)
    assert result.ok is True
    assert result.budget.movement_remaining == 5


def test_spend_movement_beyond_remaining_refused():
    result = spend_movement(new_turn(30), 35)
    assert result.ok is False
    assert "30" in result.reason


def test_negative_movement_raises():
    with pytest.raises(ValueError):
        spend_movement(new_turn(30), -5)


def test_dash_consumes_action_and_adds_speed():
    result = dash(new_turn(30))
    assert result.ok is True
    assert result.budget.action_available is False
    assert result.budget.movement_remaining == 60


def test_dash_without_action_refused():
    spent = spend(new_turn(30), "action").budget
    assert dash(spent).ok is False
