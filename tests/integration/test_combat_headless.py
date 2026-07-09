"""M3 milestone gate: a full multi-round goblin-ambush combat driven ONLY
through ``registry.execute``.

The engine is exercised end to end exactly as an orchestrator would drive it:
every mutation is a registry command. Direct store access appears only for
reading/assertions. The one authorized escape hatch is ``dm_ruling`` (with the
mandatory "test scripting" rationale) to force Kira into the dying state
deterministically instead of fishing for an RNG outcome.

Narrative (turn order is initiative-dependent under seed 1234, so the script
drives turns explicitly via ``next_turn`` rather than assuming an order):

  Round 1
    - Kira swings from `near` with no reach -> refused (band legality).
    - Kira closes to melee with goblin-1 and lands a player-supplied hit
      (attack/damage values flagged in the event log).
    - Brother Aldric fires guiding-bolt at goblin-1 from `near` -> legal.
  Round 2 (opportunity-attack demonstration)
    - Kira steps into the `engaged` band; goblin-2 closes to melee with her.
    - As goblin-2 is adjacent, Kira lands an opportunity swing (reaction).
    - goblin-2 flees the `engaged` band without disengaging -> the move data
      lists Kira as a provoker.
  Dying sequence
    - dm_ruling drops Kira to 0 HP + unconscious ("test scripting").
    - Two failed death saves, then Aldric's cure-wounds revives her mid-sequence.
    - Kira quaffs her healing potion.
  Resolution
    - Both goblins are put down; end_combat splits the XP.
    - The store is reopened fresh and reflects the post-combat world.
"""

from __future__ import annotations

from dm_engine.commands import registry
from dm_engine.commands.campaign import bootstrap_campaign
from dm_engine.commands.registry import open_campaign_context

SEED = 1234


class Driver:
    """Wraps ``registry.execute`` and counts every call so the test can assert
    event-log completeness: every command (success or refusal) appends exactly
    one event, and ``bootstrap_campaign`` appended one before any command ran."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.calls = 0

    def __call__(self, command, /, **kwargs):
        self.calls += 1
        return registry.execute(command, self.ctx, **kwargs)

    def advance_to(self, key: str) -> None:
        """Cycle turns until it is ``key``'s turn (it must be a live combatant)."""
        for _ in range(64):
            combat = self.ctx.store.combat()
            if combat["combatants"][combat["turn_index"]]["key"] == key:
                return
            result = self("next_turn")
            assert result.ok, result.refusal
        raise AssertionError(f"never reached {key}'s turn")

    def fresh_turn(self, key: str) -> None:
        """Guarantee ``key`` gets a brand-new turn with a reset budget, even if
        it is already the active combatant (whose action may be spent)."""
        self("next_turn")
        self.advance_to(key)


def _combatant(ctx, key: str) -> dict:
    return next(c for c in ctx.store.combat()["combatants"] if c["key"] == key)


def _kira_resources(ctx) -> dict:
    return ctx.store.get_resources(ctx.store.get_character("Kira")["id"])


def _last_event(ctx) -> dict:
    return ctx.store.conn.execute(
        "SELECT command, result, rolls FROM event_log ORDER BY id DESC LIMIT 1"
    ).fetchone()


def test_headless_multi_round_combat(tmp_path, rules_path):
    campaigns_dir = tmp_path / "campaigns"
    slug = "ambush"

    # --- Step 1: bootstrap + populate the party (all via commands) ---------
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
            abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
            ac=16,
            proficiencies={"skills": ["athletics"], "saves": ["str", "con"]},
            attacks=[{"name": "longsword", "ranged": False, "range_ft": 5,
                      "long_range_ft": None, "damage": "1d8",
                      "damage_type": "slashing", "ability": "str", "proficient": True}],
        ).ok

        assert run(
            "create_character", name="Brother Aldric", role="companion",
            class_slug="cleric", race_slug="hill-dwarf",
            abilities={"str": 14, "dex": 8, "con": 15, "int": 10, "wis": 15, "cha": 12},
            ac=18,
            proficiencies={"skills": ["medicine"], "saves": ["wis", "cha"]},
            attacks=[{"name": "mace", "ranged": False, "range_ft": 5,
                      "long_range_ft": None, "damage": "1d6",
                      "damage_type": "bludgeoning", "ability": "str", "proficient": True}],
            spells_known=["cure-wounds", "guiding-bolt", "sacred-flame", "bless"],
        ).ok

        assert run("add_item", character="Kira", item="healing potion", quantity=1).ok

        # --- Step 2: start_combat — order + advisory difficulty --------------
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
        assert inits == sorted(inits, reverse=True)  # descending initiative
        assert started.data["encounter"]["difficulty"] in (
            "trivial", "easy", "medium", "hard", "deadly"
        )
        assert started.data["encounter"]["adjusted_xp"] > 0

        # --- Step 3/4: Round 1 — refusal, player-supplied hit, spell band ----
        run.advance_to("Kira")

        # (band legality) longsword cannot reach a near-band target -> refused,
        # and the refusal is still written to the event log.
        reach = run("attack", attacker="Kira", target="goblin-1",
                    attack_name="longsword")
        assert reach.ok is False and "reach" in reach.refusal.lower()
        refused_event = _last_event(ctx)
        assert refused_event["command"] == "attack"
        assert '"ok": false' in refused_event["result"]

        assert run("engage", combatant="Kira", target="goblin-1").ok
        hit = run("attack", attacker="Kira", target="goblin-1",
                  attack_name="longsword",
                  player_attack_value=18, player_damage_value=1)
        assert hit.ok, hit.refusal
        assert hit.data["hit"] is True          # 18 + STR/prof 5 = 23 vs AC 15
        assert hit.data["damage"]["final"] == 4  # 1 + STR 3, goblin 7 -> 3 hp
        # player-supplied flags are captured in the event log's rolls
        assert '"player_supplied": true' in _last_event(ctx)["rolls"]
        assert _combatant(ctx, "goblin-1")["hp"] == 3

        # (band legality) a ranged spell attack is fine from near
        run.advance_to("Brother Aldric")
        bolt = run("cast_spell", caster="Brother Aldric", spell_slug="guiding-bolt",
                   targets=["goblin-1"])
        assert bolt.ok, bolt.refusal  # not refused: 120-ft attack reaches `near`

        # --- Step 4: Round 2 — opportunity attack when a goblin flees ---------
        run.advance_to("Kira")
        # Kira advances into the `engaged` band so an adjacent foe that flees
        # will provoke (the `engaged` band is what OA detection keys on).
        assert run("move", combatant="Kira", to_band="engaged").ok

        run.advance_to("goblin-2")
        assert run("engage", combatant="goblin-2", target="Kira").ok
        assert "goblin-2" in _combatant(ctx, "Kira")["engaged_with"]

        # While goblin-2 is adjacent it is Kira's turn to swing on a reaction
        # (the opportunity attack, resolved as a reaction off her turn).
        oa = run("attack", attacker="Kira", target="goblin-2",
                 attack_name="longsword", spend="reaction",
                 player_attack_value=18, player_damage_value=0)
        assert oa.ok, oa.refusal
        assert oa.data["opportunity"] is True
        assert oa.data["hit"] is True and oa.data["damage"]["final"] == 3  # 0 + STR 3
        assert _combatant(ctx, "goblin-2")["hp"] == 4
        assert _combatant(ctx, "goblin-2")["reaction_used"] is False  # Kira's, not its
        assert _combatant(ctx, "Kira")["reaction_used"] is True

        # On its next turn (fresh movement budget) goblin-2 breaks away from the
        # engaged band without disengaging: the move data names Kira as a provoker.
        run("next_turn")  # end goblin-2's current turn, then cycle back to it
        run.advance_to("goblin-2")
        assert _combatant(ctx, "goblin-2")["band"] == "engaged"  # still adjacent
        flee = run("move", combatant="goblin-2", to_band="near")
        assert flee.ok, flee.refusal
        assert flee.data["opportunity_attacks_from"] == ["Kira"]

        # --- Step 5: forced dying sequence -> death saves -> cure-wounds ------
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

        fail1 = run("death_save", character="Kira", player_value=3)
        assert fail1.ok and fail1.data["failures"] == 1 and fail1.data["event"] == "failure"
        fail2 = run("death_save", character="Kira", player_value=2)
        assert fail2.ok and fail2.data["failures"] == 2

        # cure-wounds mid-sequence brings her back up and clears the dying state
        run.advance_to("Brother Aldric")
        cure = run("cast_spell", caster="Brother Aldric", spell_slug="cure-wounds",
                   targets=["Kira"])
        assert cure.ok, cure.refusal
        revived = _kira_resources(ctx)
        assert revived["hp"] > 0
        assert "unconscious" not in revived["conditions"]
        assert revived["death_saves"]["failures"] == 0  # reset on revival

        # she tops off with the potion added in step 1 (inventory drains)
        potion = run("use_item", character="Kira", item="healing potion", heal="2d4+2")
        assert potion.ok, potion.refusal
        assert potion.data["healed"] > 0
        assert ctx.store.items_for(ctx.store.get_character("Kira")["id"]) == []

        # --- Step 6: finish both goblins, end combat, split XP ---------------
        for goblin in ("goblin-1", "goblin-2"):
            if _combatant(ctx, goblin)["defeated"]:
                continue  # guiding-bolt may already have felled goblin-1
            run.fresh_turn("Kira")
            assert run("engage", combatant="Kira", target=goblin).ok
            kill = run("attack", attacker="Kira", target=goblin,
                       attack_name="longsword",
                       player_attack_value=18, player_damage_value=10)
            assert kill.ok, kill.refusal
            assert _combatant(ctx, goblin)["defeated"] is True

        assert _combatant(ctx, "goblin-1")["defeated"] is True
        assert _combatant(ctx, "goblin-2")["defeated"] is True

        end = run("end_combat")
        assert end.ok, end.refusal
        assert end.data["xp_awarded"] == 100      # 2 goblins x 50 XP
        assert end.data["per_member"] == 50       # split across Kira + Aldric
        assert {r["name"] for r in end.data["recipients"]} == {"Kira", "Brother Aldric"}
        assert ctx.store.combat()["active"] == 0

        # --- event-log completeness -----------------------------------------
        # bootstrap wrote 1 event; every command since wrote exactly 1 more.
        assert ctx.store.event_count() == 1 + run.calls

        kira_xp = ctx.store.get_character("Kira")["xp"]
        kira_hp = _kira_resources(ctx)["hp"]
    finally:
        ctx.store.close()

    # --- Step 7: reopen fresh; the persisted world reflects the aftermath ----
    ctx2 = open_campaign_context(campaigns_dir, slug, rules_path)
    try:
        scene = registry.execute("get_scene_state", ctx2)
        assert scene.ok
        assert scene.data["combat"] is None  # no active combat persisted

        brief = registry.execute("get_campaign_brief", ctx2)
        assert brief.ok
        assert brief.data["combat_active"] is False
        by_name = {p["name"]: p for p in brief.data["party"]}
        assert set(by_name) == {"Kira", "Brother Aldric"}
        assert by_name["Kira"]["xp"] == kira_xp == 50
        assert by_name["Brother Aldric"]["xp"] == 50
        assert by_name["Kira"]["hp"] == kira_hp > 0  # HP left as combat ended
    finally:
        ctx2.store.close()
