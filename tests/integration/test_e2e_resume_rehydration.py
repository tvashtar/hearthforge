"""Goal-gate e2e: kill the process mid-combat, reopen from disk, and prove the
scene rehydrates exactly.

A session is driven to a non-trivial mid-combat state (a checkpoint recap, a
started encounter, a PC who has spent her action and engaged, a wounded goblin,
conditions on both a character and a monster), the live state is recorded, the
store is closed to simulate a killed process, and a *fresh* context is opened.
The reopened brief and scene state must match the recorded snapshot field for
field — nothing is re-derived, everything is reconstructed from disk.
"""

from __future__ import annotations

from dm_engine.commands import registry
from dm_engine.commands.campaign import bootstrap_campaign
from dm_engine.commands.registry import open_campaign_context

SEED = 777
RECAP = "The party crested the ridge and walked straight into the ambush."

_BUDGET_FLAGS = (
    "action_available", "bonus_action_available", "reaction_available",
    "movement_remaining", "speed",
)


def _run(ctx, command, /, **kwargs):
    result = registry.execute(command, ctx, **kwargs)
    assert result.ok, f"{command} refused: {result.refusal}"
    return result


def _combatant(ctx, key: str) -> dict:
    return next(c for c in ctx.store.combat()["combatants"] if c["key"] == key)


def _advance_to(ctx, key: str) -> None:
    for _ in range(64):
        combat = ctx.store.combat()
        if combat["combatants"][combat["turn_index"]]["key"] == key:
            return
        _run(ctx, "next_turn")
    raise AssertionError(f"never reached {key}'s turn")


def test_resume_rehydration_gate(tmp_path, rules_path):
    campaigns_dir = tmp_path / "campaigns"
    slug = "resume-gate"

    ctx = bootstrap_campaign(
        campaigns_dir, rules_path, slug=slug, name="Ambush Resume",
        death_mode="narrative", skeleton={"premise": "ambushed on the ridge"},
        seed=SEED,
    )
    try:
        _run(ctx, "create_character", name="Kira", role="pc",
             class_slug="fighter", race_slug="human",
             abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12,
                        "cha": 8},
             ac=16,
             proficiencies={"skills": ["athletics"]},
             attacks=[{"weapon": "longsword", "name": "longsword"}])
        _run(ctx, "create_character", name="Brother Aldric", role="companion",
             class_slug="cleric", race_slug="hill-dwarf",
             abilities={"str": 14, "dex": 8, "con": 15, "int": 10, "wis": 15,
                        "cha": 12},
             ac=18,
             proficiencies={"skills": ["medicine"]},
             attacks=[{"weapon": "mace", "name": "mace"}],
             spells_known=["cure-wounds", "bless"])

        # a checkpoint recap: it must resurface as the latest recap on reopen.
        _run(ctx, "checkpoint", content=RECAP)

        _run(ctx, "start_combat",
             monsters=[{"slug": "goblin", "count": 2, "band": "near"}],
             pc_initiative=15)

        # Two turns of scripted actions: Kira engages + attacks (spending her
        # action and part of her movement), then the turn advances; she gets a
        # fresh turn next round and attacks again. Combat is left MID-round,
        # deliberately not on Kira's turn, so her spent budget is frozen on
        # disk rather than reset.
        for _ in range(2):
            _advance_to(ctx, "Kira")
            if "goblin-1" not in _combatant(ctx, "Kira")["engaged_with"]:
                _run(ctx, "engage", combatant="Kira", target="goblin-1")
            _run(ctx, "attack", attacker="Kira", target="goblin-1",
                 attack_name="longsword", player_attack_value=12,
                 player_damage_value=1)
            _run(ctx, "next_turn")

        # Conditions on both a monster and a character must also rehydrate.
        _run(ctx, "apply_condition", target="goblin-1", condition="prone")
        _run(ctx, "apply_condition", target="Kira", condition="poisoned")

        # --- record the live mid-combat state --------------------------------
        combat = ctx.store.combat()
        rec_round = combat["round"]
        rec_turn_index = combat["turn_index"]
        rec_active = combat["combatants"][rec_turn_index]["key"]
        rec_order = [c["key"] for c in combat["combatants"]]
        rec_kira_hp = ctx.store.get_resources(ctx.store.get_character("Kira")["id"])["hp"]
        rec_kira_conditions = ctx.store.get_resources(
            ctx.store.get_character("Kira")["id"])["conditions"]
        rec_goblin_hp = _combatant(ctx, "goblin-1")["hp"]
        rec_goblin_conditions = _combatant(ctx, "goblin-1")["conditions"]
        rec_kira_budget = dict(_combatant(ctx, "Kira")["budget"])

        # Guards proving the recorded state is genuinely depleted, so the
        # reopen assertions below are not vacuous "restored what was never used"
        # checks: Kira spent her action, the goblin took real damage, and the
        # process is being killed while it is NOT Kira's turn.
        assert rec_active != "Kira"
        assert rec_kira_budget["action_available"] is False  # action was spent
        goblin_full_hp = ctx.rules.get_monster("goblin").hit_points
        assert rec_goblin_hp < goblin_full_hp                # goblin was wounded
        assert rec_goblin_conditions == ["prone"]
        assert "poisoned" in rec_kira_conditions
    finally:
        ctx.store.close()  # simulate a killed process

    # --- reopen fresh from disk ---------------------------------------------
    ctx2 = open_campaign_context(campaigns_dir, slug, rules_path)
    try:
        brief = registry.execute("get_campaign_brief", ctx2)
        assert brief.ok, brief.refusal
        assert brief.data["combat_active"] is True         # combat still live
        assert brief.data["recap"] == RECAP                # checkpoint is latest

        scene = registry.execute("get_scene_state", ctx2)
        assert scene.ok, scene.refusal
        combat = scene.data["combat"]
        assert combat is not None

        # initiative order: identical keys in identical order
        assert [c["key"] for c in combat["order"]] == rec_order
        assert combat["active"] == rec_active              # whose turn
        assert combat["round"] == rec_round
        assert combat["turn_index"] == rec_turn_index

        # remaining action economy: every budget flag survives the reopen
        reopened_budget = combat["budgets"]["Kira"]
        for flag in _BUDGET_FLAGS:
            assert reopened_budget[flag] == rec_kira_budget[flag]

        # HP and conditions rebuilt for both the character and the monster
        entries = {c["key"]: c for c in combat["order"]}
        assert entries["Kira"]["hp"] == rec_kira_hp
        assert entries["Kira"]["conditions"] == rec_kira_conditions
        assert entries["goblin-1"]["hp"] == rec_goblin_hp
        assert entries["goblin-1"]["conditions"] == rec_goblin_conditions
    finally:
        ctx2.store.close()
