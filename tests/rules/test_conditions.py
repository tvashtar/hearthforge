import pytest

from dm_engine.rules.conditions import (
    CONDITIONS,
    attack_interaction,
    effects_for,
)


def test_all_fifteen_raw_conditions():
    assert CONDITIONS == {
        "blinded", "charmed", "deafened", "exhaustion", "frightened", "grappled",
        "incapacitated", "invisible", "paralyzed", "petrified", "poisoned",
        "prone", "restrained", "stunned", "unconscious",
    }


def test_unknown_condition_raises():
    with pytest.raises(ValueError):
        effects_for({"sleepy"})


def test_no_conditions_is_neutral():
    e = effects_for([])
    assert e.can_take_actions and e.can_move and e.can_speak
    assert e.speed_multiplier == 1.0
    assert not e.attacked_with_advantage and not e.attacks_have_disadvantage


def test_paralyzed_aggregate():
    e = effects_for({"paralyzed"})
    assert e.can_take_actions is False
    assert e.can_take_reactions is False
    assert e.can_move is False
    assert e.can_speak is False
    assert e.auto_fail_str_dex_saves is True
    assert e.attacked_with_advantage is True
    assert e.melee_hits_are_critical is True


def test_unconscious_includes_prone():
    e = effects_for({"unconscious"})
    assert e.prone is True
    assert e.melee_hits_are_critical is True
    assert e.auto_fail_str_dex_saves is True


def test_restrained_flags():
    e = effects_for({"restrained"})
    assert e.can_move is False
    assert e.attacked_with_advantage is True
    assert e.attacks_have_disadvantage is True
    assert e.dex_saves_have_disadvantage is True


def test_petrified_resists_all_damage():
    assert effects_for({"petrified"}).resist_all_damage is True


def test_grappled_only_stops_movement():
    e = effects_for({"grappled"})
    assert e.can_move is False
    assert e.can_take_actions is True


def test_blinded_deafened_charmed_frightened_flags():
    assert effects_for({"blinded"}).auto_fail_sight_checks is True
    assert effects_for({"deafened"}).auto_fail_hearing_checks is True
    assert effects_for({"charmed"}).cannot_attack_charmer is True
    fear = effects_for({"frightened"})
    assert fear.cannot_approach_fear_source is True
    assert fear.checks_have_disadvantage is True
    assert fear.attacks_have_disadvantage is True


def test_exhaustion_ladder_is_cumulative():
    assert effects_for([], exhaustion_level=1).checks_have_disadvantage is True
    assert effects_for([], exhaustion_level=2).speed_multiplier == 0.5
    e3 = effects_for([], exhaustion_level=3)
    assert e3.attacks_have_disadvantage and e3.saves_have_disadvantage
    assert e3.checks_have_disadvantage  # level 1 effect persists
    assert effects_for([], exhaustion_level=4).hp_max_halved is True
    assert effects_for([], exhaustion_level=5).can_move is False
    assert effects_for([], exhaustion_level=6).dead is True
    with pytest.raises(ValueError):
        effects_for([], exhaustion_level=7)


def test_exhaustion_name_implies_level_one():
    assert effects_for({"exhaustion"}).checks_have_disadvantage is True


def test_attack_interaction_poisoned_vs_blinded_cancels():
    # Poisoned attacker (dis) vs blinded target (adv against it) -> normal.
    attacker = effects_for({"poisoned"})
    target = effects_for({"blinded"})
    assert attack_interaction(attacker, target, engaged=True).mode == "normal"


def test_attack_interaction_prone_target_depends_on_range():
    neutral = effects_for([])
    prone = effects_for({"prone"})
    assert attack_interaction(neutral, prone, engaged=True).mode == "advantage"
    assert attack_interaction(neutral, prone, engaged=False).mode == "disadvantage"


def test_attack_interaction_invisible_attacker_has_advantage():
    invisible = effects_for({"invisible"})
    neutral = effects_for([])
    assert attack_interaction(invisible, neutral, engaged=False).mode == "advantage"
    # ...and attacks against an invisible creature have disadvantage.
    assert attack_interaction(neutral, invisible, engaged=False).mode == "disadvantage"


def test_paralyzed_auto_crit_only_within_reach():
    neutral = effects_for([])
    paralyzed = effects_for({"paralyzed"})
    assert attack_interaction(neutral, paralyzed, engaged=True).auto_crit_on_hit is True
    assert attack_interaction(neutral, paralyzed, engaged=False).auto_crit_on_hit is False
