import pytest

from dm_engine.commands import registry

pytestmark = pytest.mark.usefixtures("party")


def test_ruling_requires_rationale(ctx):
    result = registry.execute("dm_ruling", ctx, description="Kira swings on the rope",
                              rationale="   ")
    assert result.ok is False
    row = ctx.store.conn.execute(
        "SELECT is_ruling FROM event_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["is_ruling"] == 0  # refusal is logged but is not a ruling


def test_ruling_applies_effects_atomically(ctx):
    kira = ctx.store.get_character("Kira")
    good = registry.execute(
        "dm_ruling", ctx, description="Falling rocks", rationale="trap sprung, RAW silent",
        effects=[{"op": "adjust_hp", "target": "Kira", "delta": -4},
                 {"op": "set_condition", "target": "Kira", "condition": "prone"}])
    assert good.ok
    assert ctx.store.get_resources(kira["id"])["hp"] == kira["max_hp"] - 4
    bad = registry.execute(
        "dm_ruling", ctx, description="Bad op batch", rationale="testing",
        effects=[{"op": "adjust_hp", "target": "Kira", "delta": -1},
                 {"op": "set_condition", "target": "Kira", "condition": "sleepy"}])
    assert bad.ok is False
    assert ctx.store.get_resources(kira["id"])["hp"] == kira["max_hp"] - 4  # unchanged


def test_rulings_listed_for_audit(ctx):
    registry.execute("dm_ruling", ctx, description="X", rationale="because RAW gap",
                     effects=[])
    rulings = ctx.store.rulings()
    assert len(rulings) == 1 and rulings[0]["rationale"] == "because RAW gap"


# -- own tests -------------------------------------------------------------


def test_ruling_requires_description(ctx):
    result = registry.execute("dm_ruling", ctx, description="   ", rationale="reason")
    assert result.ok is False


def test_ruling_note_op_lands_in_data_without_mutation(ctx):
    kira = ctx.store.get_character("Kira")
    before = ctx.store.get_resources(kira["id"])["hp"]
    result = registry.execute(
        "dm_ruling", ctx, description="Just a note", rationale="documenting",
        effects=[{"op": "note", "text": "the rope was frayed"}],
    )
    assert result.ok
    assert result.data["applied"] == [{"op": "note", "text": "the rope was frayed"}]
    assert ctx.store.get_resources(kira["id"])["hp"] == before


def test_ruling_adjust_hp_clamps_to_character_max(ctx):
    kira = ctx.store.get_character("Kira")
    result = registry.execute(
        "dm_ruling", ctx, description="Overheal", rationale="blessing",
        effects=[{"op": "adjust_hp", "target": "Kira", "delta": 999}],
    )
    assert result.ok
    assert ctx.store.get_resources(kira["id"])["hp"] == kira["max_hp"]


def test_ruling_adjust_hp_defeats_monster_at_zero(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    key = next(c["key"] for c in ctx.store.combat()["combatants"] if c["kind"] == "monster")
    result = registry.execute(
        "dm_ruling", ctx, description="Rockfall crushes the goblin",
        rationale="environmental hazard",
        effects=[{"op": "adjust_hp", "target": key, "delta": -999}],
    )
    assert result.ok
    combatant = next(c for c in ctx.store.combat()["combatants"] if c["key"] == key)
    assert combatant["hp"] == 0 and combatant["defeated"] is True


def test_ruling_set_exhaustion_refuses_for_monster_target(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    key = next(c["key"] for c in ctx.store.combat()["combatants"] if c["kind"] == "monster")
    result = registry.execute(
        "dm_ruling", ctx, description="Weary goblin", rationale="testing",
        effects=[{"op": "set_exhaustion", "target": key, "level": 2}],
    )
    assert result.ok is False


def test_ruling_adjust_slot_and_adjust_xp(ctx):
    aldric = ctx.store.get_character("Brother Aldric")
    result = registry.execute(
        "dm_ruling", ctx, description="Spent a slot studying, gained insight",
        rationale="downtime activity",
        effects=[{"op": "adjust_slot", "character": "Brother Aldric",
                  "slot_level": 1, "delta": -1},
                 {"op": "adjust_xp", "character": "Brother Aldric", "delta": 50}],
    )
    assert result.ok
    res = ctx.store.get_resources(aldric["id"])
    assert res["spell_slots"]["1"]["remaining"] == 3
    assert ctx.store.get_character("Brother Aldric")["xp"] == 950


def test_ruling_rejects_unknown_op(ctx):
    result = registry.execute(
        "dm_ruling", ctx, description="Odd effect", rationale="testing",
        effects=[{"op": "teleport", "target": "Kira"}],
    )
    assert result.ok is False


# -- roll_dice ---------------------------------------------------------------


def _last_event_rolls(ctx):
    import json
    row = ctx.store.conn.execute(
        "SELECT rolls FROM event_log ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row["rolls"])


def test_roll_dice_engine_path_rolls_and_logs(ctx):
    result = registry.execute("roll_dice", ctx, count=5, sides=8,
                              reason="sleep HP pool")
    assert result.ok, result.refusal
    data = result.data
    assert data["count"] == 5 and data["sides"] == 8
    assert len(data["rolls"]) == 5
    assert all(1 <= r <= 8 for r in data["rolls"])
    assert data["total"] == sum(data["rolls"])
    assert data["reason"] == "sleep HP pool"
    assert data["player_supplied"] is False
    assert "5d8" in result.digest and "sleep HP pool" in result.digest
    logged = _last_event_rolls(ctx)
    assert len(logged) == 1
    assert logged[0]["rolls"] == data["rolls"]
    assert logged[0]["player_supplied"] is False


def test_roll_dice_is_seed_deterministic(ctx, tmp_path_factory, rules_path):
    from dm_engine.commands.registry import CommandContext, RecordingRoller
    from dm_engine.content.lookup import RulesDB
    from dm_engine.state.store import CampaignStore

    def fresh_roll():
        store = CampaignStore.create(
            tmp_path_factory.mktemp("dice") / "c", slug="d", name="D",
            death_mode="narrative", rng_seed=7, skeleton={"premise": "t"},
        )
        c = CommandContext(store=store, roller=RecordingRoller(7),
                           rules=RulesDB(rules_path))
        result = registry.execute("roll_dice", c, count=6, sides=20,
                                  reason="determinism probe")
        store.close()
        return result.data["rolls"]

    assert fresh_roll() == fresh_roll()


def test_roll_dice_gm_only_stays_behind_the_screen(ctx):
    result = registry.execute("roll_dice", ctx, count=2, sides=6,
                              reason="hidden morale roll", gm_only=True)
    assert result.ok, result.refusal
    assert result.gm_only is True
    logged = _last_event_rolls(ctx)
    assert all(r["gm_only"] for r in logged)


def test_roll_dice_player_values_are_flagged_and_echoed(ctx):
    result = registry.execute("roll_dice", ctx, count=4, sides=6,
                              reason="rolled stats at the table",
                              player_values=[6, 1, 4, 3])
    assert result.ok, result.refusal
    assert result.data["rolls"] == [6, 1, 4, 3]
    assert result.data["total"] == 14
    assert result.data["player_supplied"] is True
    assert result.digest.startswith("Player rolls")
    logged = _last_event_rolls(ctx)
    assert len(logged) == 4  # one 1d6 Roll per supplied die (FC-2 protocol)
    assert [r["rolls"][0] for r in logged] == [6, 1, 4, 3]
    assert all(r["player_supplied"] for r in logged)


def test_roll_dice_refusals_leave_no_rolls(ctx):
    cases = [
        {"count": 5, "sides": 8, "reason": "   "},          # blank reason
        {"count": 0, "sides": 8, "reason": "r"},            # count too low
        {"count": 101, "sides": 8, "reason": "r"},          # count too high
        {"count": 1, "sides": 1, "reason": "r"},            # sides too low
        {"count": 1, "sides": 1001, "reason": "r"},         # sides too high
        {"count": 3, "sides": 6, "reason": "r",
         "player_values": [1, 2]},                          # length mismatch
        {"count": 2, "sides": 6, "reason": "r",
         "player_values": [3, 7]},                          # die out of range
    ]
    for kwargs in cases:
        result = registry.execute("roll_dice", ctx, **kwargs)
        assert result.ok is False, kwargs
        assert _last_event_rolls(ctx) == []
