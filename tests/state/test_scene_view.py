"""build_scene_view: pure projection of campaign state into the
player-visible SceneView. The secrecy guarantee lives HERE: monster HP
numbers never enter the model (validator-enforced), so no renderer or
future server can leak what the view doesn't carry."""

import pydantic
import pytest

from dm_engine.commands import registry
from dm_engine.state.scene import (
    SceneView,
    TokenView,
    build_scene_view,
    monster_condition_word,
)


def _start_goblin_fight(ctx, bands=("near", "near")):
    monsters = [{"slug": "goblin", "band": b} for b in bands]
    result = registry.execute(
        "start_combat", ctx, monsters=monsters, pc_initiative=15
    )
    assert result.ok, result.refusal
    return result


# -- monster condition word (computable tiers only; "near death" is a DM
# judgment call and deliberately has no formula here) ----------------------

@pytest.mark.parametrize("hp,max_hp,word", [
    (10, 10, "fresh"),
    (9, 10, "wounded"),
    (6, 10, "wounded"),     # just above half
    (5, 10, "bloodied"),    # exactly half
    (3, 10, "bloodied"),    # just above quarter
    (2, 10, "staggering"),  # at/below quarter (2.5 -> 2)
    (1, 10, "staggering"),
])
def test_monster_condition_word_thresholds(hp, max_hp, word):
    assert monster_condition_word(hp, max_hp) == word


# -- the secrecy invariant --------------------------------------------------

def test_monster_tokens_cannot_carry_hp_numbers():
    with pytest.raises(pydantic.ValidationError):
        TokenView(
            key="goblin-1", name="Goblin 1", kind="monster", band="near",
            engaged_with=[], conditions=[], defeated=False, active=False,
            hp=7, max_hp=7, condition_word="fresh",
        )
    with pytest.raises(pydantic.ValidationError):
        TokenView(
            key="goblin-1", name="Goblin 1", kind="monster", band="near",
            engaged_with=[], conditions=[], defeated=False, active=False,
            hp=None, max_hp=None, condition_word=None,  # word is mandatory
        )


def test_built_view_strips_monster_numbers_and_words_them(party):
    ctx = party
    _start_goblin_fight(ctx)
    view = build_scene_view(ctx.store)

    assert view.mode == "combat"
    monsters = [t for t in view.combat.tokens if t.kind == "monster"]
    assert len(monsters) == 2
    for tok in monsters:
        assert tok.hp is None and tok.max_hp is None
        assert tok.condition_word == "fresh"

    # And the whole model serializes without a monster number anywhere:
    # goblin HP is 7 in the SRD; give the party distinctive HP so a bare
    # "7" from a character row can't mask a leak.
    dumped = view.model_dump_json()
    assert '"hp":7' not in dumped and '"hp": 7' not in dumped


def test_character_tokens_carry_live_numbers(party):
    ctx = party
    _start_goblin_fight(ctx)
    kira = ctx.store.get_character("Kira")
    ctx.store.update_resources(kira["id"], hp=5, conditions=["poisoned"])
    ctx.store.conn.commit()

    view = build_scene_view(ctx.store)
    kira_tok = next(t for t in view.combat.tokens if t.name == "Kira")
    assert kira_tok.hp == 5
    assert kira_tok.max_hp == kira["max_hp"]
    assert kira_tok.conditions == ["poisoned"]
    assert kira_tok.condition_word is None


def test_combat_view_marks_active_and_initiative_order(party):
    ctx = party
    result = _start_goblin_fight(ctx)
    order = result.data["order"]  # start_combat reports initiative order
    view = build_scene_view(ctx.store)

    assert [e.key for e in view.combat.initiative] == [c["key"] for c in order]
    active = [e.key for e in view.combat.initiative if e.active]
    token_active = [t.key for t in view.combat.tokens if t.active]
    assert active == token_active
    assert len(active) == 1


def test_engaged_pair_at_far_keeps_far_band(party):
    ctx = party
    _start_goblin_fight(ctx, bands=("far", "near"))
    # Drive whoever's turn it is until Aldric can engage the far goblin.
    view = build_scene_view(ctx.store)
    goblin_far = next(t for t in view.combat.tokens if t.band == "far")

    combat = ctx.store.combat()
    for c in combat["combatants"]:
        if c["kind"] == "character" and c["name"] == "Brother Aldric":
            c["band"] = "far"
            c["engaged_with"] = [goblin_far.key]
        if c["key"] == goblin_far.key:
            c["engaged_with"] = ["Brother Aldric"]
    ctx.store.update_combat(combatants=combat["combatants"])
    ctx.store.conn.commit()

    view = build_scene_view(ctx.store)
    aldric = next(t for t in view.combat.tokens if t.name == "Brother Aldric")
    goblin = next(t for t in view.combat.tokens if t.key == goblin_far.key)
    assert aldric.band == "far" and goblin.band == "far"
    assert goblin.key in aldric.engaged_with
    assert "Brother Aldric" in goblin.engaged_with


def test_scene_mode_out_of_combat(party):
    ctx = party
    registry.execute(
        "create_location", ctx, slug="mill", name="The Old Mill",
        description="A ruined mill by the weir.",
    )
    registry.execute("create_npc", ctx, name="Maro", location_slug="mill")
    registry.execute(
        "set_scene", ctx, description="Rain hammers the roof", location_slug="mill"
    )
    registry.execute("add_scene_prop", ctx, name="grain hoist", band="near")

    view = build_scene_view(ctx.store)
    assert view.mode == "scene"
    assert view.combat is None
    assert view.location_name == "The Old Mill"
    assert view.scene_description == "Rain hammers the roof"
    assert view.npcs_present == ["Maro"]
    assert [p.name for p in view.props] == ["grain hoist"]
    assert [(m.name, m.hp) for m in view.party] == [
        ("Kira", view.party[0].hp), ("Brother Aldric", view.party[1].hp)
    ]
    assert view.event_id == ctx.store.next_event_id() - 1


def test_empty_campaign_builds(ctx):
    # Fresh campaign: no party, no location, no scene — must not crash.
    view = build_scene_view(ctx.store)
    assert view.mode == "scene"
    assert view.party == [] and view.props == [] and view.npcs_present == []
    assert view.location_name is None
    assert isinstance(view, SceneView)
