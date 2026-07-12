"""Goal-gate e2e: the scripted goblin ambush.

Every gameplay mutation goes through ``registry.execute`` (wrapped in a
call-counting ``Driver`` so we can prove event-log completeness). Turn order,
bands and budgets are forced with direct ``update_combat`` writes — allowed as
documented *test setup* only, never as gameplay — so each rules assertion is
exercised in isolation against the fixed seed.

This is the milestone gate; ``test_combat_headless.py`` covers a similar arc
but these are the assertions that must hold.
"""

from __future__ import annotations

from dm_engine.commands import registry
from dm_engine.commands.campaign import bootstrap_campaign
from dm_engine.commands.registry import open_campaign_context

SEED = 1234

_FULL_BUDGET = {
    "speed": 30, "movement_remaining": 30, "action_available": True,
    "bonus_action_available": True, "reaction_available": True,
}


class Driver:
    """Every gameplay call routes through here so the test can assert that
    each ``registry.execute`` — success or refusal — appends exactly one
    event row (``bootstrap`` appended one before any command ran)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.calls = 0

    def __call__(self, command, /, **kwargs):
        self.calls += 1
        return registry.execute(command, self.ctx, **kwargs)


def _combatant(ctx, key: str) -> dict:
    return next(c for c in ctx.store.combat()["combatants"] if c["key"] == key)


def _kira_resources(ctx) -> dict:
    return ctx.store.get_resources(ctx.store.get_character("Kira")["id"])


def _last_event(ctx) -> dict:
    return ctx.store.conn.execute(
        "SELECT command, result, rolls FROM event_log ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _force(ctx, key, *, band=None, engaged_with=None, budget=False,
           reaction_used=None, turn=False) -> None:
    """Documented TEST SETUP ONLY: pin a combatant's band / engagement /
    budget / whose-turn-it-is so a single rule can be exercised in isolation.
    Not a gameplay path — all real actions go through the Driver."""
    combatants = ctx.store.combat()["combatants"]
    for c in combatants:
        if c["key"] == key:
            if band is not None:
                c["band"] = band
            if engaged_with is not None:
                c["engaged_with"] = list(engaged_with)
            if budget:
                c["budget"] = dict(_FULL_BUDGET)
            if reaction_used is not None:
                c["reaction_used"] = reaction_used
    fields = {"combatants": combatants}
    if turn:
        fields["turn_index"] = next(
            i for i, c in enumerate(combatants) if c["key"] == key
        )
    ctx.store.update_combat(**fields)
    ctx.store.conn.commit()


def _mutual_engage(ctx, a: str, b: str) -> None:
    _force(ctx, a, band="engaged", engaged_with=[b])
    _force(ctx, b, band="engaged", engaged_with=[a])


def test_goblin_ambush_gate(tmp_path, rules_path):
    campaigns_dir = tmp_path / "campaigns"
    slug = "ambush-gate"

    ctx = bootstrap_campaign(
        campaigns_dir, rules_path, slug=slug, name="Goblin Ambush",
        death_mode="narrative", skeleton={"premise": "waylaid on the road"},
        seed=SEED,
    )
    run = Driver(ctx)
    try:
        assert ctx.store.event_count() == 1  # bootstrap's create_campaign event

        assert run(
            "create_character", name="Kira", role="pc",
            class_slug="fighter", race_slug="human",
            abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12,
                       "cha": 8},
            ac=16,
            proficiencies={"skills": ["athletics"]},
            attacks=[
                {"weapon": "longsword", "name": "longsword"},
                {"weapon": "dagger", "name": "dagger"},
            ],
        ).ok

        assert run(
            "create_character", name="Brother Aldric", role="companion",
            class_slug="cleric", race_slug="hill-dwarf",
            abilities={"str": 14, "dex": 8, "con": 15, "int": 10, "wis": 15,
                       "cha": 12},
            ac=18,
            proficiencies={"skills": ["medicine"]},
            attacks=[
                {"weapon": "mace", "name": "mace"},
                {"weapon": "shortbow", "name": "shortbow"},
            ],
            spells_known=["cure-wounds", "guiding-bolt", "sacred-flame", "bless"],
        ).ok

        # --- start_combat: initiative descending, advisory difficulty --------
        started = run(
            "start_combat",
            monsters=[{"slug": "goblin", "count": 2, "band": "near"}],
            pc_initiative=15,
        )
        assert started.ok, started.refusal
        assert {o["key"] for o in started.data["order"]} == {
            "Kira", "Brother Aldric", "goblin-1", "goblin-2"
        }
        inits = [o["initiative"] for o in started.data["order"]]
        assert inits == sorted(inits, reverse=True)  # totals descending
        assert started.data["encounter"]["difficulty"] in (
            "trivial", "easy", "medium", "hard", "deadly"
        )
        assert started.data["encounter"]["adjusted_xp"] > 0

        kira = ctx.store.get_character("Kira")
        kira_max_hp = kira["max_hp"]

        # --- band legality: PC melee cannot reach a near-band target ---------
        # spend="none" skips the economy so we land on the *range* refusal.
        for weapon in ("longsword", "dagger"):
            reach = run("attack", attacker="Kira", target="goblin-1",
                        attack_name=weapon, spend="none")
            assert reach.ok is False and "reach" in reach.refusal.lower()
            refused_event = _last_event(ctx)
            assert refused_event["command"] == "attack"
            assert '"ok": false' in refused_event["result"]  # refusal still logged

        # --- damage/HP arithmetic vs the known seed --------------------------
        # Kira is at full HP; the first landed engine-rolled goblin swing must
        # leave her at exactly max_hp - final (arithmetic, not a magic number).
        assert _kira_resources(ctx)["hp"] == kira_max_hp
        _mutual_engage(ctx, "goblin-2", "Kira")
        for _ in range(20):
            swing = run("attack", attacker="goblin-2", target="Kira",
                        attack_name="Scimitar", spend="none")
            assert swing.ok, swing.refusal
            if swing.data["hit"]:
                break
        else:  # pragma: no cover - seed guarantees a hit inside 20 swings
            raise AssertionError("goblin never landed a hit under the seed")
        final = swing.data["damage"]["final"]
        assert final > 0
        assert _kira_resources(ctx)["hp"] == kira_max_hp - final

        # --- hidden enemy stealth: gm_only check, hidden in the log ----------
        stealth = run("skill_check", character="goblin-2", skill="stealth",
                      dc=11, gm_only=True)
        assert stealth.ok, stealth.refusal
        assert stealth.gm_only is True
        assert stealth.data["modifier"] == 6  # goblin Stealth +6 from the SRD
        assert '"gm_only": true' in _last_event(ctx)["rolls"]

        # --- player-supplied attack roll -------------------------------------
        _mutual_engage(ctx, "Kira", "goblin-1")
        goblin1_hp_before = _combatant(ctx, "goblin-1")["hp"]
        hit = run("attack", attacker="Kira", target="goblin-1",
                  attack_name="longsword", spend="none",
                  player_attack_value=18, player_damage_value=1)
        assert hit.ok, hit.refusal
        assert hit.data["hit"] is True          # 18 + STR/prof 5 = 23 vs AC 15
        assert hit.data["damage"]["final"] == 4  # 1 + STR 3
        assert '"player_supplied": true' in _last_event(ctx)["rolls"]
        assert _combatant(ctx, "goblin-1")["hp"] == goblin1_hp_before - 4

        # --- band legality: companion ranged options ARE legal from near -----
        bow = run("attack", attacker="Brother Aldric", target="goblin-1",
                  attack_name="shortbow", spend="none")
        assert bow.ok, bow.refusal  # 80-ft shortbow reaches a near (30 ft) foe
        if not _combatant(ctx, "goblin-1")["defeated"]:
            bolt = run("cast_spell", caster="Brother Aldric",
                       spell_slug="guiding-bolt", targets=["goblin-1"],
                       spend="none")
            assert bolt.ok, bolt.refusal  # 120-ft spell attack reaches `near`

        # --- opportunity attack: reaction consumed, second refused -----------
        _mutual_engage(ctx, "goblin-2", "Kira")
        _force(ctx, "goblin-2", budget=True, turn=True)  # goblin-2's turn
        _force(ctx, "Kira", reaction_used=False)
        goblin2_hp_before = _combatant(ctx, "goblin-2")["hp"]
        oa = run("attack", attacker="Kira", target="goblin-2",
                 attack_name="longsword", spend="reaction",
                 player_attack_value=18, player_damage_value=0)
        assert oa.ok, oa.refusal
        assert oa.data["opportunity"] is True         # off-turn reaction swing
        assert oa.data["hit"] is True and oa.data["damage"]["final"] == 3  # STR 3
        assert _combatant(ctx, "goblin-2")["hp"] == goblin2_hp_before - 3
        assert _combatant(ctx, "Kira")["reaction_used"] is True
        # a second reaction this same round is refused (economy consumed)
        again = run("attack", attacker="Kira", target="goblin-2",
                    attack_name="longsword", spend="reaction",
                    player_attack_value=18, player_damage_value=0)
        assert again.ok is False and "reaction" in again.refusal.lower()
        # goblin-2 breaks away from engaged range without disengaging: the move
        # names Kira (whom it was engaged with) as a provoker.
        flee = run("move", combatant="goblin-2", to_band="near")
        assert flee.ok, flee.refusal
        assert flee.data["opportunity_attacks_from"] == ["Kira"]

        # --- forced dying sequence -> death save -> cure-wounds revival -------
        drop = run(
            "dm_ruling",
            description="A goblin's lucky crit lays Kira out cold.",
            rationale="test scripting",
            effects=[
                {"op": "adjust_hp", "target": "Kira", "delta": -1000},
                {"op": "set_condition", "target": "Kira", "condition": "unconscious"},
            ],
        )
        assert drop.ok, drop.refusal
        assert _kira_resources(ctx)["hp"] == 0
        assert "unconscious" in _kira_resources(ctx)["conditions"]

        fail1 = run("death_save", character="Kira", player_value=4)  # 4 < DC 10
        assert fail1.ok and fail1.data["event"] == "failure"
        assert fail1.data["failures"] == 1
        assert _kira_resources(ctx)["death_saves"]["failures"] == 1

        cure = run("cast_spell", caster="Brother Aldric", spell_slug="cure-wounds",
                   targets=["Kira"], spend="none")
        assert cure.ok, cure.refusal
        revived = _kira_resources(ctx)
        assert revived["hp"] > 0
        assert "unconscious" not in revived["conditions"]
        assert revived["death_saves"]["failures"] == 0  # reset on revival

        # --- finish both goblins (player_value 20 crits), end combat, XP -----
        for goblin in ("goblin-1", "goblin-2"):
            if _combatant(ctx, goblin)["defeated"]:
                continue  # guiding-bolt may already have felled goblin-1
            _mutual_engage(ctx, "Kira", goblin)
            kill = run("attack", attacker="Kira", target=goblin,
                       attack_name="longsword", spend="none",
                       player_attack_value=20, player_damage_value=10)
            assert kill.ok, kill.refusal
            assert kill.data["critical"] is True
            assert _combatant(ctx, goblin)["defeated"] is True
        assert _combatant(ctx, "goblin-1")["defeated"] is True
        assert _combatant(ctx, "goblin-2")["defeated"] is True

        end = run("end_combat")
        assert end.ok, end.refusal
        assert end.data["xp_awarded"] == 100      # 2 goblins x 50 XP
        assert end.data["per_member"] == 50       # split across Kira + Aldric
        assert {r["name"] for r in end.data["recipients"]} == {
            "Kira", "Brother Aldric"
        }
        assert ctx.store.combat()["active"] == 0

        # --- event-log completeness ------------------------------------------
        # bootstrap wrote 1 event; every Driver call (success or refusal) wrote
        # exactly one more; TVA-41 auto-checkpoints (not issued by the Driver,
        # so not in run.calls) may add a few more once ~20 events accumulate.
        # Forced update_combat setup writes never log events.
        auto_checkpoints = sum(
            1 for e in ctx.store.events_tail(1000) if e["command"] == "checkpoint"
        )
        assert ctx.store.event_count() == 1 + run.calls + auto_checkpoints
    finally:
        ctx.store.close()

    # reopening fresh reflects the persisted, post-combat world.
    ctx2 = open_campaign_context(campaigns_dir, slug, rules_path)
    try:
        brief = registry.execute("get_campaign_brief", ctx2)
        assert brief.ok, brief.refusal
        assert brief.data["combat_active"] is False
        by_name = {p["name"]: p for p in brief.data["party"]}
        assert by_name["Kira"]["xp"] == 50
        assert by_name["Brother Aldric"]["xp"] == 50
    finally:
        ctx2.store.close()
