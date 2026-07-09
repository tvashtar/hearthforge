import pytest

from dm_engine.rules.death import DeathSaveState, apply_damage_while_dying, apply_death_save


def test_save_of_ten_or_more_succeeds():
    outcome = apply_death_save(DeathSaveState(), 10)
    assert outcome.event == "success"
    assert outcome.state.successes == 1


def test_save_below_ten_fails():
    outcome = apply_death_save(DeathSaveState(), 9)
    assert outcome.event == "failure"
    assert outcome.state.failures == 1


def test_three_successes_stabilize():
    state = DeathSaveState(successes=2)
    outcome = apply_death_save(state, 15)
    assert outcome.event == "stabilized"
    assert outcome.state.stable is True
    assert outcome.state.dead is False


def test_three_failures_die():
    state = DeathSaveState(failures=2)
    outcome = apply_death_save(state, 4)
    assert outcome.event == "failure"
    assert outcome.state.dead is True


def test_natural_one_counts_two_failures():
    outcome = apply_death_save(DeathSaveState(failures=1), 1)
    assert outcome.event == "critical_failure"
    assert outcome.state.failures == 3
    assert outcome.state.dead is True


def test_natural_twenty_regains_one_hp():
    outcome = apply_death_save(DeathSaveState(successes=1, failures=2), 20)
    assert outcome.event == "regained_hp"
    assert outcome.regained_hp is True
    # back on your feet: dying state fully resets
    assert outcome.state == DeathSaveState()


def test_full_sequence_success_fail_success_success():
    state = DeathSaveState()
    for natural, expected in ((12, "success"), (7, "failure"), (14, "success"), (18, "stabilized")):
        outcome = apply_death_save(state, natural)
        assert outcome.event == expected
        state = outcome.state
    assert state.stable and not state.dead


def test_cannot_save_when_not_dying():
    with pytest.raises(ValueError):
        apply_death_save(DeathSaveState(stable=True), 10)
    with pytest.raises(ValueError):
        apply_death_save(DeathSaveState(dead=True), 10)
    with pytest.raises(ValueError):
        apply_death_save(DeathSaveState(), 21)


def test_damage_while_dying_is_a_failure():
    outcome = apply_damage_while_dying(DeathSaveState(), 6, max_hp=20, critical=False)
    assert outcome.event == "failure"
    assert outcome.state.failures == 1


def test_critical_damage_while_dying_is_two_failures():
    outcome = apply_damage_while_dying(DeathSaveState(failures=1), 6, max_hp=20, critical=True)
    assert outcome.event == "critical_failure"
    assert outcome.state.failures == 3
    assert outcome.state.dead is True


def test_massive_damage_is_instant_death():
    outcome = apply_damage_while_dying(DeathSaveState(), 25, max_hp=20, critical=False)
    assert outcome.event == "died"
    assert outcome.state.dead is True


def test_damage_breaks_stability():
    outcome = apply_damage_while_dying(DeathSaveState(stable=True), 3, max_hp=20, critical=False)
    assert outcome.state.stable is False
    assert outcome.state.failures == 1


def test_damage_while_dying_on_already_dead_raises():
    with pytest.raises(ValueError):
        apply_damage_while_dying(DeathSaveState(dead=True), 6, max_hp=20, critical=False)


def test_damage_while_dying_rejects_negative_damage_and_bad_max_hp():
    with pytest.raises(ValueError):
        apply_damage_while_dying(DeathSaveState(), -1, max_hp=20, critical=False)
    with pytest.raises(ValueError):
        apply_damage_while_dying(DeathSaveState(), 6, max_hp=0, critical=False)
