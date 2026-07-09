import pytest

from dm_engine.commands import registry
from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.commands.registry import RecordingRoller, command, execute


@command("_test_echo")
def _echo(ctx, **kwargs) -> CommandResult:
    return CommandResult(ok=True, command="_test_echo", digest="echoed", data=kwargs)


@command("_test_refuse")
def _refuse(ctx, **kwargs) -> CommandResult:
    return refuse("_test_refuse", "not allowed")


@command("_test_boom")
def _boom(ctx, **kwargs) -> CommandResult:
    ctx.store.upsert_location("half", "Half", "should roll back", region=None)
    raise RuntimeError("engine bug")


@command("_test_roll")
def _roll(ctx, **kwargs) -> CommandResult:
    r = ctx.roller.roll("2d6", player_value=kwargs.get("player_value"))
    return CommandResult(ok=True, command="_test_roll", digest=f"rolled {r.total}",
                         data={"total": r.total})


def test_execute_appends_event_and_sets_event_ids(ctx):
    result = execute("_test_echo", ctx, x=1)
    assert result.ok and result.event_ids == [1]
    row = ctx.store.conn.execute(
        "SELECT command, inputs FROM event_log WHERE id = 1"
    ).fetchone()
    assert row["command"] == "_test_echo"
    assert '"x": 1' in row["inputs"]


def test_unknown_command_is_a_refusal_not_an_error(ctx):
    result = execute("no_such_command", ctx)
    assert result.ok is False
    assert "no_such_command" in result.refusal
    assert ctx.store.event_count() == 1  # refusals are logged too


def test_refusals_are_logged(ctx):
    result = execute("_test_refuse", ctx)
    assert result.ok is False
    assert ctx.store.event_count() == 1


def test_handler_exception_rolls_back_everything(ctx):
    with pytest.raises(RuntimeError):
        execute("_test_boom", ctx)
    assert ctx.store.get_location("half") is None   # state rolled back
    assert ctx.store.event_count() == 0             # no event row either


def test_rolls_are_captured_and_draws_persisted(ctx):
    result = execute("_test_roll", ctx)
    assert result.ok
    row = ctx.store.conn.execute("SELECT rolls FROM event_log WHERE id = 1").fetchone()
    assert '"notation": "2d6"' in row["rolls"]
    assert ctx.store.campaign_meta()["rng_draws"] == 2  # two engine dice drawn


def test_player_value_draws_nothing_and_is_flagged(ctx):
    result = execute("_test_roll", ctx, player_value=7)
    assert result.ok and result.data["total"] == 7
    row = ctx.store.conn.execute("SELECT rolls FROM event_log WHERE id = 1").fetchone()
    assert '"player_supplied": true' in row["rolls"]
    assert ctx.store.campaign_meta()["rng_draws"] == 0


def test_recording_roller_fast_forward_is_deterministic():
    a = RecordingRoller(7)
    first = [a.roll("1d20").total for _ in range(5)]
    b = RecordingRoller(7, initial_draws=3)
    assert [b.roll("1d20").total for _ in range(2)] == first[3:]


def test_registered_commands_lists_names():
    assert "_test_echo" in registry.registered_commands()
