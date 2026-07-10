import json

import pytest

from dm_engine.commands import registry
from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.commands.registry import (
    CommandContext,
    RecordingRoller,
    execute,
    open_campaign_context,
)
from dm_engine.content.lookup import RulesDB
from dm_engine.state.store import CampaignStore


def _echo(ctx, **kwargs) -> CommandResult:
    return CommandResult(ok=True, command="_test_echo", digest="echoed", data=kwargs)


def _refuse(ctx, **kwargs) -> CommandResult:
    return refuse("_test_refuse", "not allowed")


def _boom(ctx, **kwargs) -> CommandResult:
    ctx.store.upsert_location("half", "Half", "should roll back", region=None)
    raise RuntimeError("engine bug")


def _boom_after_roll(ctx, **kwargs) -> CommandResult:
    ctx.roller.roll("1d20")
    raise KeyError("damage_type")


def _roll(ctx, **kwargs) -> CommandResult:
    r = ctx.roller.roll("2d6", player_value=kwargs.get("player_value"))
    return CommandResult(ok=True, command="_test_roll", digest=f"rolled {r.total}",
                         data={"total": r.total})


def _roll_many_d6(ctx, **kwargs) -> CommandResult:
    # A large batch of non-d20 dice: with high probability at least one draw
    # hits rejection-sampling, so this consumes a different number of
    # underlying RNG words than a d20 fast-forward of the same draw count.
    r = ctx.roller.roll("20d6")
    return CommandResult(ok=True, command="_test_roll_many_d6",
                         digest=f"rolled {r.total}", data={"total": r.total})


@pytest.fixture(autouse=True)
def _register_test_commands():
    """Register the module's `_test_*` handlers for the duration of each test
    and remove them afterward. Registering at import time would leave them in
    the global registry for the whole session, breaking other suites (e.g. the
    MCP smoke test) that assert the registry's exact command set."""
    handlers = {
        "_test_echo": _echo,
        "_test_refuse": _refuse,
        "_test_boom": _boom,
        "_test_boom_after_roll": _boom_after_roll,
        "_test_roll": _roll,
        "_test_roll_many_d6": _roll_many_d6,
    }
    for name, fn in handlers.items():
        registry._COMMANDS[name] = fn
    yield
    for name in handlers:
        registry._COMMANDS.pop(name, None)


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


def test_handler_exception_rolls_back_state_but_logs_crash_event(ctx):
    with pytest.raises(RuntimeError):
        execute("_test_boom", ctx, target="Goblin 2")
    assert ctx.store.get_location("half") is None   # state rolled back

    # ...but the audit log has no hole: a crash event was committed separately.
    assert ctx.store.event_count() == 1
    row = ctx.store.conn.execute(
        "SELECT command, inputs, result, is_ruling FROM event_log WHERE id = 1"
    ).fetchone()
    assert row["command"] == "_test_boom"
    assert row["is_ruling"] == 0
    assert json.loads(row["inputs"]) == {"target": "Goblin 2"}
    result = json.loads(row["result"])
    assert result["ok"] is False
    assert result["data"]["crash"] is True
    assert result["data"]["exception_type"] == "RuntimeError"
    assert result["data"]["exception_message"] == "engine bug"
    assert "RuntimeError: engine bug" in result["data"]["traceback"]


def test_crash_event_does_not_persist_rng_or_rolls(ctx):
    """The state-rollback guarantee is untouched: dice drawn before the crash
    are rolled back (re-drawn on replay), so the crash event records none and
    the persisted RNG position stays put."""
    with pytest.raises(KeyError):
        execute("_test_boom_after_roll", ctx)
    meta = ctx.store.campaign_meta()
    assert meta["rng_draws"] == 0 and meta["rng_state"] is None
    row = ctx.store.conn.execute("SELECT rolls FROM event_log WHERE id = 1").fetchone()
    assert json.loads(row["rolls"]) == []


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


def test_rng_state_roundtrip_resumes_mixed_dice():
    a = RecordingRoller(11)
    [a.roll("3d6") for _ in range(3)]  # mixed-size dice consume variable RNG words
    state = a.getstate()
    expected = [a.roll("1d20").total for _ in range(5)]
    b = RecordingRoller(11)
    b.setstate(state)
    assert [b.roll("1d20").total for _ in range(5)] == expected


def test_execute_persists_rng_state(ctx):
    execute("_test_roll", ctx)
    assert ctx.store.campaign_meta()["rng_state"] is not None


def test_reopen_resumes_exact_rng_state_for_mixed_dice(tmp_path, rules_path):
    """d6 draws consume a different number of RNG words than d20 draws, so
    the old fast-forward-by-d20 mechanism resumes to the wrong RNG position.
    Persisting/restoring the exact state must resume correctly instead."""
    campaigns_dir = tmp_path / "campaigns"
    seed = 4242

    store = CampaignStore.create(
        campaigns_dir, slug="resume", name="Resume", death_mode="narrative",
        rng_seed=seed, skeleton={"premise": "test"},
    )
    ctx1 = CommandContext(
        store=store, roller=RecordingRoller(seed), rules=RulesDB(rules_path)
    )
    execute("_test_roll_many_d6", ctx1)  # 20d6: non-d20-shaped RNG word usage
    store.close()

    ctx2 = open_campaign_context(campaigns_dir, "resume", rules_path)
    actual_next = ctx2.roller.roll("1d20").total
    ctx2.store.close()

    # Expectation: replay the same d6 rolls on a fresh roller with the same
    # seed, then take the next roll — this is what a never-closed roller
    # would have produced.
    expected_roller = RecordingRoller(seed)
    expected_roller.roll("20d6")
    expected_next = expected_roller.roll("1d20").total

    assert actual_next == expected_next


def test_open_campaign_context_refreshes_stale_sheets_after_migration(
    tmp_path, rules_path
):
    """A pre-fix row's sheet on disk is stale (or, pre-Fix-1, crash-inducing).
    Reopening the campaign must re-render it with normalized/tolerant data."""
    campaigns_dir = tmp_path / "campaigns"
    store = CampaignStore.create(
        campaigns_dir, slug="legacy", name="Legacy", death_mode="narrative",
        rng_seed=1, skeleton={"premise": "t"},
    )
    store.conn.execute(
        "INSERT INTO characters (name, role, class_slug, race_slug, level,"
        " abilities, max_hp, ac, speed, proficiencies, attacks, spells_known)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("Algarve", "pc", "rogue", "wood-elf", 1,
         json.dumps({"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10}),
         9, 15, 35,
         json.dumps({"saving_throws": ["dex", "int"], "skills": ["stealth"],
                     "expertise": [], "tools": [], "languages": []}),
         json.dumps([
             {"name": "Void Lash", "attack_bonus": 9, "damage": "6d6+4",
              "damage_type": "necrotic"},
         ]),
         json.dumps([])),
    )
    store.conn.execute(
        "INSERT INTO resources (character_id, hp, hit_dice_remaining, spell_slots)"
        " VALUES (1, 9, 1, '{}')",
    )
    store.conn.commit()
    # No sheet file yet — this row predates the sheets feature entirely.
    assert not (store.root / "sheets" / "algarve.md").exists()
    store.close()

    ctx = open_campaign_context(campaigns_dir, "legacy", rules_path)
    sheet = (ctx.store.root / "sheets" / "algarve.md").read_text()
    assert "◉ DEX" in sheet  # saves normalized and rendered
    assert "Void Lash: (invalid legacy spec — refuses on use)" in sheet
    ctx.store.close()
