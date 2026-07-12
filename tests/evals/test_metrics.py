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


# --- TVA-60: done_when matchers (inputs, result paths, refusal_contains) ---


def test_beat_done_inputs_matcher(party):
    ctx = party
    registry.execute("cast_spell", ctx, caster="Brother Aldric",
                     spell_slug="cure-wounds", targets=["Brother Aldric"])
    registry.execute("cast_spell", ctx, caster="Brother Aldric", spell_slug="bless")
    db_path = ctx.store.root / "campaign.sqlite"
    done_when = {"command": "cast_spell", "ok": True, "inputs": {"spell_slug": "bless"}}
    assert beat_done(db_path, done_when, after_id=0)
    missed = {"command": "cast_spell", "ok": True,
              "inputs": {"spell_slug": "hold-person"}}
    assert not beat_done(db_path, missed, after_id=0)


def test_beat_done_refusal_contains(party):
    ctx = party
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "far"}],
                     pc_initiative=15)
    combatants = ctx.store.combat()["combatants"]
    idx = next(i for i, c in enumerate(combatants) if c["key"] == "goblin-1")
    ctx.store.update_combat(turn_index=idx)
    ctx.store.conn.commit()
    # not Kira's turn: turn_index points at goblin-1
    not_turn = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                                attack_name="longsword")
    assert not not_turn.ok and "not Kira's turn" in not_turn.refusal
    # give Kira the turn back but leave her out of longsword range ("far")
    for c in combatants:
        if c["key"] == "Kira":
            c["budget"] = {"speed": 30, "movement_remaining": 30,
                           "action_available": True, "bonus_action_available": True,
                           "reaction_available": True}
    kira_idx = next(i for i, c in enumerate(combatants) if c["key"] == "Kira")
    ctx.store.update_combat(combatants=combatants, turn_index=kira_idx)
    ctx.store.conn.commit()
    out_of_range = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                                    attack_name="longsword")
    assert not out_of_range.ok and "cannot reach" in out_of_range.refusal

    db_path = ctx.store.root / "campaign.sqlite"
    reach = {"command": "attack", "ok": False, "refusal_contains": "cannot reach"}
    assert beat_done(db_path, reach, after_id=0)
    missed = {"command": "attack", "ok": False, "refusal_contains": "no such text"}
    assert not beat_done(db_path, missed, after_id=0)


def test_beat_done_result_path_matcher(party):
    ctx = party
    cast = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                            spell_slug="bless")
    assert cast.ok and cast.data["needs_ruling"]
    db_path = ctx.store.root / "campaign.sqlite"
    done_when = {"command": "cast_spell", "ok": True,
                 "result": {"data.needs_ruling": 1}}
    assert beat_done(db_path, done_when, after_id=0)
    missed = {"command": "cast_spell", "ok": True,
              "result": {"data.needs_ruling": 0}}
    assert not beat_done(db_path, missed, after_id=0)


def test_classify_uses_same_matchers(party):
    ctx = party
    marker = ctx.store.conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM event_log"
    ).fetchone()[0]
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "far"}],
                     pc_initiative=15)
    combatants = ctx.store.combat()["combatants"]
    idx = next(i for i, c in enumerate(combatants) if c["key"] == "goblin-1")
    ctx.store.update_combat(turn_index=idx)
    ctx.store.conn.commit()
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="longsword")
    assert not result.ok and "not Kira's turn" in result.refusal

    db_path = ctx.store.root / "campaign.sqlite"
    done_when = {"command": "attack", "ok": False, "refusal_contains": "cannot reach"}
    assert not beat_done(db_path, done_when, after_id=marker)
    detail = classify_beat_failure(db_path, done_when, after_id=marker)
    assert detail["reason"] == "refused"
    assert "not Kira's turn" in detail["refusal"]


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


# --- TVA-65: dual-path illegal-action scoring (any_of + none_of abstention) ---

# Mirrors the illegal-action beat's done_when in caravan_ambush.yaml.
ILLEGAL_DONE_WHEN = {
    "any_of": [
        {"command": "attack", "ok": False, "refusal_contains": "cannot reach"},
        {"none_of": [
            {"command": "attack", "ok": True, "inputs": {"attacker": "Kira"}},
            {"command": "engage", "inputs": {"combatant": "Kira"}},
            {"command": "move", "inputs": {"combatant": "Kira"}},
        ]},
    ]
}


def _max_id(ctx) -> int:
    return ctx.store.conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM event_log"
    ).fetchone()[0]


def _give_kira_full_turn(ctx) -> list[dict]:
    """Point the initiative at Kira with a fresh budget; return combatants."""
    combatants = ctx.store.combat()["combatants"]
    for c in combatants:
        if c["key"] == "Kira":
            c["budget"] = {"speed": 30, "movement_remaining": 30,
                           "action_available": True, "bonus_action_available": True,
                           "reaction_available": True}
    kira_idx = next(i for i, c in enumerate(combatants) if c["key"] == "Kira")
    ctx.store.update_combat(combatants=combatants, turn_index=kira_idx)
    ctx.store.conn.commit()
    return combatants


def test_illegal_action_passes_on_engine_refusal(party):
    # Path 1: the DM submits the swing and the engine refuses "cannot reach".
    ctx = party
    marker = _max_id(ctx)
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "far"}],
                     pc_initiative=15)
    _give_kira_full_turn(ctx)
    out = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                           attack_name="longsword")
    assert not out.ok and "cannot reach" in out.refusal
    db_path = ctx.store.root / "campaign.sqlite"
    assert beat_done(db_path, ILLEGAL_DONE_WHEN, after_id=marker)


def test_illegal_action_passes_on_narration_refusal(party):
    # Path 2: the DM correctly declines in narration — Kira never attacks,
    # engages, or moves — so there is no tool call, and abstention holds.
    ctx = party
    marker = _max_id(ctx)
    db_path = ctx.store.root / "campaign.sqlite"
    assert beat_done(db_path, ILLEGAL_DONE_WHEN, after_id=marker)


def test_illegal_action_fails_on_workaround(party):
    # A workaround — Kira engages to close the distance — trips none_of, and
    # with no "cannot reach" refusal the beat fails.
    ctx = party
    marker = _max_id(ctx)
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    _give_kira_full_turn(ctx)
    eng = registry.execute("engage", ctx, combatant="Kira", target="goblin-1")
    assert eng.ok, eng.refusal
    db_path = ctx.store.root / "campaign.sqlite"
    assert not beat_done(db_path, ILLEGAL_DONE_WHEN, after_id=marker)
    detail = classify_beat_failure(db_path, ILLEGAL_DONE_WHEN, after_id=marker)
    assert isinstance(detail["reason"], str)  # triages without crashing on any_of


def test_illegal_action_fails_on_fabricated_hit(party):
    # A successful swing resolved for Kira (a fabricated hit on the far
    # target) also trips none_of via the attack clause.
    ctx = party
    marker = _max_id(ctx)
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    combatants = _give_kira_full_turn(ctx)
    # Put the goblin in melee reach so the swing resolves without an engage
    # command (the attack itself is the illegal-action-window event).
    for c in combatants:
        if c["key"] == "goblin-1":
            c["band"] = "engaged"
            c["engaged_with"] = ["Kira"]
        if c["key"] == "Kira":
            c["engaged_with"] = ["goblin-1"]
    ctx.store.update_combat(combatants=combatants)
    ctx.store.conn.commit()
    hit = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                           attack_name="longsword", player_attack_value=20)
    assert hit.ok, hit.refusal
    db_path = ctx.store.root / "campaign.sqlite"
    assert not beat_done(db_path, ILLEGAL_DONE_WHEN, after_id=marker)
