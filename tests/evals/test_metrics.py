"""Metric tests build events through registry.execute() — production schema,
real FC-1 envelopes, real rolls column — so eval metrics can never drift from
what the engine actually writes (TVA-31)."""

import json

import pytest

from dm_engine.commands import registry
from dm_engine.commands.envelope import CommandResult
from evals.metrics import beat_done, campaign_open, classify_beat_failure, compute_metrics, max_event_id


def _boom(ctx, **kwargs) -> CommandResult:
    raise RuntimeError("planted crash")


def _supplied_roll(ctx, **kwargs) -> CommandResult:
    r = ctx.roller.roll("1d20", player_value=kwargs.get("player_value"))
    return CommandResult(ok=True, command="_eval_supplied_roll",
                         digest=f"rolled {r.total}", data={"total": r.total})


@pytest.fixture(autouse=True)
def _register_test_commands():
    handlers = {"_eval_boom": _boom, "_eval_supplied_roll": _supplied_roll}
    registry._COMMANDS.update(handlers)
    yield
    for name in handlers:
        registry._COMMANDS.pop(name, None)


@pytest.fixture()
def event_db(party):
    """One planted defect per metric, all through registry.execute()."""
    ctx = party  # events 1-2: create_character x2 (party fixture)
    ok = registry.execute("skill_check", ctx, character="Kira",
                          skill="athletics", dc=10)                    # 3
    assert ok.ok
    # refusal retry loop: same command, identical inputs, consecutive
    registry.execute("attack", ctx, attacker="Kira", target="Bandit")  # 4
    registry.execute("attack", ctx, attacker="Kira", target="Bandit")  # 5
    # crash row, committed by _append_crash_event
    with pytest.raises(RuntimeError):
        registry.execute("_eval_boom", ctx)                            # 6
    # orphaned tier-2: needs_ruling with no later dm_ruling
    cast = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                            spell_slug="bless")                        # 7
    assert cast.ok and cast.data["needs_ruling"]
    # player_supplied roll attributed to a non-PC (engine-regression guard)
    registry.execute("_eval_supplied_roll", ctx, actor="Brother Aldric",
                     player_value=15)                                  # 8
    # PC-supplied roll: must NOT count as a violation
    registry.execute("skill_check", ctx, character="Kira",
                     skill="athletics", dc=10, player_value=15)        # 9
    registry.execute("get_scene_state", ctx)                           # 10
    registry.execute("get_scene_state", ctx)                           # 11
    return ctx.store.root / "campaign.sqlite"


@pytest.fixture()
def fixture_transcript(tmp_path):
    """New-shape entries (tool_result carries is_error) plus one legacy
    pre-TVA-32 error entry that must still count."""
    path = tmp_path / "transcript.jsonl"
    lines = [
        {"type": "player_message", "text": "I attack"},
        {"type": "tool_call", "id": "tu_1", "name": "mcp__dm-engine__attack",
         "input": {"attacker": "Kira"}},
        {"type": "tool_result", "tool_use_id": "tu_1", "is_error": True,
         "content": "invalid params"},
        {"type": "tool_call", "id": "tu_2", "name": "mcp__dm-engine__attack",
         "input": {"attacker": "Kira"}},
        {"type": "tool_result", "tool_use_id": "tu_2", "is_error": False,
         "content": [{"type": "text", "text": "{\"ok\": true}"}]},
        {"type": "dm_text", "text": "You swing..."},
        {"type": "player_message", "text": "again"},
        # legacy shape: error results were logged as tool_call entries
        {"type": "tool_call", "name": "(result)", "is_error": True,
         "content": "schema mismatch"},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines))
    return path


def test_max_event_id(event_db):
    assert max_event_id(event_db) == 11


def test_beat_done_respects_after_id_and_ok(event_db):
    assert beat_done(event_db, {"command": "cast_spell", "ok": True}, after_id=0)
    assert not beat_done(event_db, {"command": "cast_spell", "ok": True}, after_id=7)
    assert beat_done(event_db, {"command": "attack", "ok": False}, after_id=0)
    assert not beat_done(event_db, {"command": "end_session", "ok": True}, after_id=0)


def test_metrics_catch_each_planted_defect(event_db, fixture_transcript):
    m = compute_metrics(event_db, fixture_transcript)
    assert m["refusals"] == 3          # 2 attack refusals + 1 crash row (ok=false)
    assert m["refusal_retry_loops"] == 1
    assert m["crashes"] == 1
    assert m["orphaned_tier2"] == 1
    assert m["player_supplied_violations"] == 1  # Aldric only; Kira's is legal
    assert m["polling_reads"] == 2
    assert m["schema_rejections"] == 2  # 1 new-shape + 1 legacy
    assert m["player_messages"] == 2
    assert m["tool_calls"] == 3        # legacy "(result)" rows count as before
    assert m["tool_calls_per_player_message"] == 1.5


# --- TVA-45: opening handshake + beat failure classification ---


def test_campaign_open_false_before_any_open_campaign_event(party):
    db_path = party.store.root / "campaign.sqlite"
    assert not campaign_open(db_path)


def test_campaign_open_true_after_successful_open_campaign_event(party):
    ok = registry.execute("open_campaign", party, slug="t")
    assert ok.ok
    db_path = party.store.root / "campaign.sqlite"
    assert campaign_open(db_path)


def test_campaign_open_false_when_only_refused(party):
    refused = registry.execute("open_campaign", party, slug="not-t")
    assert not refused.ok
    db_path = party.store.root / "campaign.sqlite"
    assert not campaign_open(db_path)


def test_classify_beat_failure_not_attempted(party):
    marker = party.store.conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM event_log"
    ).fetchone()[0]
    # no attack rows logged after the marker at all
    db_path = party.store.root / "campaign.sqlite"
    detail = classify_beat_failure(
        db_path, {"command": "attack", "ok": True}, after_id=marker
    )
    assert detail == {"reason": "not_attempted"}


def test_classify_beat_failure_refused(party):
    marker = party.store.conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM event_log"
    ).fetchone()[0]
    registry.execute("attack", party, attacker="Kira", target="Bandit")
    registry.execute("attack", party, attacker="Kira", target="Bandit")
    db_path = party.store.root / "campaign.sqlite"
    detail = classify_beat_failure(
        db_path, {"command": "attack", "ok": True}, after_id=marker
    )
    assert detail["reason"] == "refused"
    assert detail["refusal"]  # most-recent refusal string is surfaced
