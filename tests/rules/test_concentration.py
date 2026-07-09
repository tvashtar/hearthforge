import pytest

from dm_engine.rules.concentration import (
    concentration_broken_by_conditions,
    concentration_save_dc,
)
from dm_engine.rules.conditions import effects_for


def test_dc_is_half_damage_minimum_ten():
    assert concentration_save_dc(7) == 10
    assert concentration_save_dc(20) == 10
    assert concentration_save_dc(22) == 11
    assert concentration_save_dc(26) == 13
    assert concentration_save_dc(45) == 22


def test_negative_damage_raises():
    with pytest.raises(ValueError):
        concentration_save_dc(-1)


def test_incapacitating_conditions_break_concentration():
    assert concentration_broken_by_conditions(effects_for({"stunned"})) is True
    assert concentration_broken_by_conditions(effects_for({"unconscious"})) is True
    assert concentration_broken_by_conditions(effects_for([], exhaustion_level=6)) is True


def test_ordinary_conditions_do_not_break_concentration():
    assert concentration_broken_by_conditions(effects_for({"prone"})) is False
    assert concentration_broken_by_conditions(effects_for({"poisoned"})) is False
    assert concentration_broken_by_conditions(effects_for([])) is False
