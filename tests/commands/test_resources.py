import pytest

from dm_engine.commands import registry

pytestmark = pytest.mark.usefixtures("party")


def test_long_rest_restores_everything_and_advances_clock(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute(
        "UPDATE resources SET hp = 2, exhaustion = 2 WHERE character_id = ?",
        (kira["id"],))
    ctx.store.conn.commit()
    before = ctx.store.world_clock()
    result = registry.execute("rest", ctx, kind="long")
    assert result.ok
    res = ctx.store.get_resources(kira["id"])
    assert res["hp"] == kira["max_hp"] and res["exhaustion"] == 1
    after = ctx.store.world_clock()
    assert (after["day"], after["minutes"]) != (before["day"], before["minutes"])


def test_short_rest_spends_hit_dice_with_player_values(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 4 WHERE character_id = ?",
                           (kira["id"],))
    ctx.store.conn.commit()
    result = registry.execute("rest", ctx, kind="short", hit_dice={"Kira": 1},
                              player_hit_die_values=[8])
    assert result.ok
    res = ctx.store.get_resources(kira["id"])
    assert res["hp"] == 4 + 8 + 2  # roll 8 + CON 2
    assert res["hit_dice_remaining"] == 0


def test_use_item_requires_holding_it(ctx):
    result = registry.execute("use_item", ctx, character="Kira", item="healing potion")
    assert result.ok is False
    registry.execute("add_item", ctx, character="Kira", item="healing potion",
                     quantity=2)
    result = registry.execute("use_item", ctx, character="Kira",
                              item="healing potion", heal="2d4+2")
    assert result.ok
    assert ctx.store.items_for(ctx.store.get_character("Kira")["id"])[0]["quantity"] == 1


# --- own tests ----------------------------------------------------------


def test_rest_during_combat_is_refused(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    short = registry.execute("rest", ctx, kind="short", hit_dice={"Kira": 1})
    assert short.ok is False and "combat" in short.refusal.lower()
    long = registry.execute("rest", ctx, kind="long")
    assert long.ok is False and "combat" in long.refusal.lower()


def test_rest_unknown_kind_refused(ctx):
    result = registry.execute("rest", ctx, kind="nap")
    assert result.ok is False


def test_short_rest_overspend_refused_before_rolling(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 4 WHERE character_id = ?",
                           (kira["id"],))
    ctx.store.conn.commit()
    # Kira has 1 hit die at level 1; asking for 2 must refuse and heal nothing.
    result = registry.execute("rest", ctx, kind="short", hit_dice={"Kira": 2})
    assert result.ok is False
    assert ctx.store.get_resources(kira["id"])["hp"] == 4


def test_use_item_without_heal_needs_ruling(ctx):
    registry.execute("add_item", ctx, character="Kira", item="mystery vial")
    result = registry.execute("use_item", ctx, character="Kira", item="mystery vial")
    assert result.ok
    assert result.data["needs_ruling"] is True
    assert ctx.store.items_for(ctx.store.get_character("Kira")["id"]) == []


def test_remove_item_refuses_when_not_enough(ctx):
    result = registry.execute("remove_item", ctx, character="Kira", item="rope")
    assert result.ok is False


def test_add_then_remove_item_mutates_inventory(ctx):
    kira_id = ctx.store.get_character("Kira")["id"]
    before = ctx.store.event_count()
    registry.execute("add_item", ctx, character="Kira", item="torch", quantity=3)
    assert ctx.store.items_for(kira_id)[0]["quantity"] == 3
    result = registry.execute("remove_item", ctx, character="Kira", item="torch",
                              quantity=2)
    assert result.ok
    assert ctx.store.items_for(kira_id)[0]["quantity"] == 1
    assert ctx.store.event_count() == before + 2  # both commands logged events
