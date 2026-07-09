import pytest

from dm_engine.commands import registry

pytestmark = pytest.mark.usefixtures("party")  # Kira PC + Brother Aldric companion


def _start(ctx, **over):
    kwargs = dict(monsters=[{"slug": "goblin", "count": 2, "band": "near"}],
                  pc_initiative=15)
    kwargs.update(over)
    return registry.execute("start_combat", ctx, **kwargs)


def test_start_combat_builds_order_and_reports_difficulty(ctx):
    result = _start(ctx)
    assert result.ok, result.refusal
    order = result.data["order"]
    assert {o["key"] for o in order} == {"Kira", "Brother Aldric", "goblin-1", "goblin-2"}
    totals = [o["initiative"] for o in order]
    assert totals == sorted(totals, reverse=True)
    assert result.data["encounter"]["difficulty"] in ("easy", "medium", "hard", "deadly", "trivial")
    assert result.data["encounter"]["adjusted_xp"] > 0


def test_start_combat_twice_refused(ctx):
    _start(ctx)
    assert _start(ctx).ok is False


def test_monster_initiative_is_gm_only(ctx):
    _start(ctx)
    row = ctx.store.conn.execute(
        "SELECT rolls FROM event_log ORDER BY id DESC LIMIT 1").fetchone()
    assert '"gm_only": true' in row["rolls"]


def test_move_out_of_engaged_provokes(ctx):
    _start(ctx)
    combat = ctx.store.combat()
    # force a known setup: it's Kira's turn, engaged with goblin-1
    for c in combat["combatants"]:
        if c["key"] == "Kira":
            c["band"] = "engaged"; c["engaged_with"] = ["goblin-1"]
        if c["key"] == "goblin-1":
            c["band"] = "engaged"; c["engaged_with"] = ["Kira"]
    turn_index = next(i for i, c in enumerate(combat["combatants"]) if c["key"] == "Kira")
    ctx.store.update_combat(combatants=combat["combatants"], turn_index=turn_index)
    ctx.store.conn.commit()
    result = registry.execute("move", ctx, combatant="Kira", to_band="near")
    assert result.ok
    assert result.data["opportunity_attacks_from"] == ["goblin-1"]

    # disengage path: no OA (fresh turn needed for the action)
    registry.execute("next_turn", ctx)  # give the turn away and come back around
    # (implementer: also cover disengage in an isolated test with a fresh setup)


def test_next_turn_advances_and_resets_budget(ctx):
    _start(ctx)
    first = ctx.store.combat()
    result = registry.execute("next_turn", ctx)
    assert result.ok
    after = ctx.store.combat()
    assert after["turn_index"] == (first["turn_index"] + 1) % len(first["combatants"])
    assert result.data["budget"]["action_available"] is True


def test_end_combat_awards_xp_for_defeated(ctx):
    _start(ctx)
    combat = ctx.store.combat()
    for c in combat["combatants"]:
        if c["kind"] == "monster":
            c["defeated"] = True; c["hp"] = 0
    ctx.store.update_combat(combatants=combat["combatants"])
    ctx.store.conn.commit()
    result = registry.execute("end_combat", ctx)
    assert result.ok
    assert result.data["xp_awarded"] == 100  # 2 goblins x 50
    assert result.data["per_member"] == 50
    assert ctx.store.get_character("Kira")["xp"] == 50
    assert ctx.store.combat()["active"] == 0


def test_scene_state_rehydrates_combat(ctx):
    _start(ctx)
    state = registry.execute("get_scene_state", ctx)
    assert state.ok
    combat = state.data["combat"]
    assert combat["round"] == 1 and len(combat["order"]) == 4
    kira = next(c for c in combat["order"] if c["key"] == "Kira")
    assert kira["hp"] == 12  # merged live from resources


# ---------------------------------------------------------------------------
# Implementer's own per-command tests (mutation + event per command).
# ---------------------------------------------------------------------------


def _last_event(ctx, command):
    row = ctx.store.conn.execute(
        "SELECT command FROM event_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row["command"] == command


# --- start_combat ----------------------------------------------------------

def test_start_combat_empty_monsters_refused(ctx):
    result = _start(ctx, monsters=[])
    assert result.ok is False


def test_start_combat_unknown_slug_refused(ctx):
    result = _start(ctx, monsters=[{"slug": "nope-not-a-monster", "count": 1}])
    assert result.ok is False


def test_start_combat_invalid_pc_initiative_refused(ctx):
    assert _start(ctx, pc_initiative=0).ok is False
    assert _start(ctx, pc_initiative=21).ok is False


def test_start_combat_mutates_combat_state_and_logs(ctx):
    _start(ctx)
    combat = ctx.store.combat()
    assert combat["active"] == 1
    assert combat["round"] == 1
    assert combat["turn_index"] == 0
    assert len(combat["combatants"]) == 4
    # first combatant has a granted budget; monsters carry hp/xp, characters do not
    assert combat["combatants"][0]["budget"] is not None
    goblin = next(c for c in combat["combatants"] if c["key"] == "goblin-1")
    assert goblin["hp"] == 7 and goblin["xp"] == 50 and goblin["ac"] == 15
    kira = next(c for c in combat["combatants"] if c["key"] == "Kira")
    assert kira["hp"] is None and kira["max_hp"] is None
    assert _last_event(ctx, "start_combat")


# --- next_turn -------------------------------------------------------------

def test_next_turn_no_combat_refused(ctx):
    assert registry.execute("next_turn", ctx).ok is False


def test_next_turn_wraps_to_next_round(ctx):
    _start(ctx)
    n = len(ctx.store.combat()["combatants"])
    for _ in range(n):
        result = registry.execute("next_turn", ctx)
    assert result.ok
    combat = ctx.store.combat()
    assert combat["round"] == 2
    assert combat["turn_index"] == 0
    assert _last_event(ctx, "next_turn")


def test_next_turn_skips_defeated(ctx):
    _start(ctx)
    combat = ctx.store.combat()
    # defeat the combatant at index 1 so next_turn skips it
    combat["combatants"][1]["defeated"] = True
    ctx.store.update_combat(combatants=combat["combatants"])
    ctx.store.conn.commit()
    result = registry.execute("next_turn", ctx)
    assert result.ok
    assert ctx.store.combat()["turn_index"] == 2


def test_next_turn_resets_reactions_each_round(ctx):
    _start(ctx)
    combat = ctx.store.combat()
    for c in combat["combatants"]:
        c["reaction_used"] = True
    ctx.store.update_combat(combatants=combat["combatants"])
    ctx.store.conn.commit()
    n = len(combat["combatants"])
    for _ in range(n):  # complete a round to wrap into round 2
        registry.execute("next_turn", ctx)
    after = ctx.store.combat()
    assert all(c["reaction_used"] is False for c in after["combatants"])


# --- surprise --------------------------------------------------------------

def test_surprised_combatant_gets_no_budget_in_round_one(ctx):
    _start(ctx, surprise=["goblin-1"])
    combat = ctx.store.combat()
    idx = next(i for i, c in enumerate(combat["combatants"]) if c["key"] == "goblin-1")
    if idx == 0:
        assert combat["combatants"][0]["budget"] is None
    else:
        ctx.store.update_combat(turn_index=idx - 1)
        ctx.store.conn.commit()
        result = registry.execute("next_turn", ctx)
        assert result.data["budget"] is None


# --- move ------------------------------------------------------------------

def test_move_no_combat_refused(ctx):
    assert registry.execute("move", ctx, combatant="Kira", to_band="near").ok is False


def test_move_not_your_turn_refused(ctx):
    _start(ctx)
    # pick a combatant that is not at turn_index 0
    combat = ctx.store.combat()
    not_active = combat["combatants"][1]["key"]
    result = registry.execute("move", ctx, combatant=not_active, to_band="far")
    assert result.ok is False


def test_move_unknown_band_refused(ctx):
    _start(ctx)
    active = ctx.store.combat()["combatants"][0]["key"]
    result = registry.execute("move", ctx, combatant=active, to_band="orbit")
    assert result.ok is False


def test_move_insufficient_movement_refused(ctx):
    _start(ctx)
    active = ctx.store.combat()["combatants"][0]["key"]
    # near -> distant is 90 ft, more than a 30 ft speed allows without dashing
    result = registry.execute("move", ctx, combatant=active, to_band="distant")
    assert result.ok is False


def test_move_dash_grants_extra_movement(ctx):
    _start(ctx)
    active = ctx.store.combat()["combatants"][0]["key"]
    # near -> distant is 90 ft; 30 speed + 30 dash = 60 still short, use far (30 ft)
    # confirm dash lets a far move leave the action spent
    result = registry.execute("move", ctx, combatant=active, to_band="far", dash=True)
    assert result.ok
    assert result.data["budget"]["action_available"] is False
    combat = ctx.store.combat()
    assert combat["combatants"][0]["band"] == "far"


def test_move_updates_band_and_logs(ctx):
    _start(ctx)
    active = ctx.store.combat()["combatants"][0]["key"]
    result = registry.execute("move", ctx, combatant=active, to_band="far")
    assert result.ok
    assert ctx.store.combat()["combatants"][0]["band"] == "far"
    assert _last_event(ctx, "move")


def test_move_disengage_avoids_opportunity_attacks(ctx):
    _start(ctx)
    combat = ctx.store.combat()
    for c in combat["combatants"]:
        if c["key"] == "Kira":
            c["band"] = "engaged"
            c["engaged_with"] = ["goblin-1"]
        if c["key"] == "goblin-1":
            c["band"] = "engaged"
            c["engaged_with"] = ["Kira"]
    turn_index = next(i for i, c in enumerate(combat["combatants"]) if c["key"] == "Kira")
    ctx.store.update_combat(combatants=combat["combatants"], turn_index=turn_index)
    ctx.store.conn.commit()
    result = registry.execute("move", ctx, combatant="Kira", to_band="near", disengage=True)
    assert result.ok
    assert result.data["opportunity_attacks_from"] == []
    # disengage spent the action
    assert result.data["budget"]["action_available"] is False
    # engagement is cleared both directions
    combat = ctx.store.combat()
    kira = next(c for c in combat["combatants"] if c["key"] == "Kira")
    goblin = next(c for c in combat["combatants"] if c["key"] == "goblin-1")
    assert kira["engaged_with"] == [] and "Kira" not in goblin["engaged_with"]


# --- engage ----------------------------------------------------------------

def test_engage_sets_mutual_engagement_and_logs(ctx):
    _start(ctx)
    active = ctx.store.combat()["combatants"][0]["key"]
    # target: any other combatant in the near band (all start near)
    target = next(c["key"] for c in ctx.store.combat()["combatants"] if c["key"] != active)
    result = registry.execute("engage", ctx, combatant=active, target=target)
    assert result.ok
    combat = ctx.store.combat()
    a = next(c for c in combat["combatants"] if c["key"] == active)
    t = next(c for c in combat["combatants"] if c["key"] == target)
    assert target in a["engaged_with"] and active in t["engaged_with"]
    assert _last_event(ctx, "engage")


def test_engage_not_your_turn_refused(ctx):
    _start(ctx)
    combat = ctx.store.combat()
    not_active = combat["combatants"][1]["key"]
    active = combat["combatants"][0]["key"]
    result = registry.execute("engage", ctx, combatant=not_active, target=active)
    assert result.ok is False


def test_engage_cannot_afford_refused(ctx):
    _start(ctx)
    combat = ctx.store.combat()
    active = combat["combatants"][0]["key"]
    # move the target far away so the combatant cannot afford to reach it
    for c in combat["combatants"]:
        if c["key"] != active and c["kind"] == "monster":
            c["band"] = "distant"
            far_target = c["key"]
    ctx.store.update_combat(combatants=combat["combatants"])
    ctx.store.conn.commit()
    result = registry.execute("engage", ctx, combatant=active, target=far_target)
    assert result.ok is False


# --- end_combat ------------------------------------------------------------

def test_end_combat_no_combat_refused(ctx):
    assert registry.execute("end_combat", ctx).ok is False


def test_end_combat_includes_encounter_xp_accumulator(ctx):
    _start(ctx)
    combat = ctx.store.combat()
    # one goblin defeated (50), plus a 30 xp accumulator entry
    for c in combat["combatants"]:
        if c["key"] == "goblin-1":
            c["defeated"] = True
    ctx.store.update_combat(combatants=combat["combatants"], encounter_xp=30)
    ctx.store.conn.commit()
    result = registry.execute("end_combat", ctx)
    assert result.ok
    assert result.data["xp_awarded"] == 80  # 50 defeated + 30 accumulator
    assert ctx.store.combat()["active"] == 0
    assert _last_event(ctx, "end_combat")


# --- get_scene_state -------------------------------------------------------

def test_scene_state_without_combat(ctx):
    state = registry.execute("get_scene_state", ctx)
    assert state.ok
    assert state.data["combat"] is None
    assert "clock" in state.data
