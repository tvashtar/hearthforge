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
