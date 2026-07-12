import json

import pytest

from dm_engine.commands import registry
from dm_engine.models.character import SKILL_ABILITIES
from dm_engine.rules.checks import ability_modifier

pytestmark = pytest.mark.usefixtures("party")  # implementer adds a fixture creating Kira (PC) + Brother Aldric (companion) via registry


def test_skill_check_applies_proficiency(ctx):
    result = registry.execute("skill_check", ctx, character="Kira",
                              skill="athletics", dc=10, player_value=12)
    assert result.ok
    assert result.data["modifier"] == 5  # STR +3, prof +2
    assert result.data["total"] == 17 and result.data["success"] is True


def test_player_value_refused_for_companion(ctx):
    result = registry.execute("skill_check", ctx, character="Brother Aldric",
                              skill="medicine", dc=10, player_value=12)
    assert result.ok is False and "player" in result.refusal.lower()


def test_player_value_out_of_range_refused(ctx):
    result = registry.execute("skill_check", ctx, character="Kira",
                              skill="athletics", dc=10, player_value=21)
    assert result.ok is False


def test_gm_only_stealth_check_flags_everything(ctx):
    result = registry.execute("skill_check", ctx, character="Brother Aldric",
                              skill="stealth", dc=12, gm_only=True)
    assert result.ok and result.gm_only is True
    row = ctx.store.conn.execute(
        "SELECT rolls FROM event_log ORDER BY id DESC LIMIT 1").fetchone()
    assert '"gm_only": true' in row["rolls"]


def test_death_save_sequence_narrative_defeat(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 0 WHERE character_id = ?",
                           (kira["id"],))
    ctx.store.conn.commit()
    registry.execute("death_save", ctx, character="Kira", player_value=9)   # fail 1
    registry.execute("death_save", ctx, character="Kira", player_value=8)   # fail 2
    result = registry.execute("death_save", ctx, character="Kira", player_value=2)
    assert result.ok
    assert ctx.store.get_character("Kira")["status"] == "defeated"  # narrative mode


def test_death_save_nat20_recovers(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 0 WHERE character_id = ?",
                           (kira["id"],))
    ctx.store.conn.commit()
    result = registry.execute("death_save", ctx, character="Kira", player_value=20)
    assert result.ok
    assert ctx.store.get_resources(kira["id"])["hp"] == 1


# -- own tests: skill_check refusals ----------------------------------------

def test_skill_check_unknown_character_refused(ctx):
    result = registry.execute("skill_check", ctx, character="Nobody", skill="athletics", dc=10)
    assert result.ok is False and "nobody" in result.refusal.lower()


def test_skill_check_unknown_skill_refused(ctx):
    result = registry.execute("skill_check", ctx, character="Kira", skill="juggling", dc=10)
    assert result.ok is False and "skill" in result.refusal.lower()


def test_skill_check_normalizes_skill_input(ctx):
    # TVA-24: underscores, spaces, case, and padding all reach the canonical slug.
    for raw in ("sleight_of_hand", "Sleight of Hand", " SLEIGHT-OF-HAND "):
        result = registry.execute("skill_check", ctx, character="Kira",
                                  skill=raw, dc=10, player_value=10)
        assert result.ok, result.refusal
        assert result.data["skill"] == "sleight-of-hand"


def test_skill_check_unknown_skill_refusal_lists_all_slugs(ctx):
    result = registry.execute("skill_check", ctx, character="Kira",
                              skill="basketweaving", dc=10)
    assert result.ok is False
    for slug in SKILL_ABILITIES:  # all 18 canonical slugs, single-shot recovery
        assert slug in result.refusal


def test_skill_check_dc_below_one_refused(ctx):
    result = registry.execute("skill_check", ctx, character="Kira", skill="athletics", dc=0)
    assert result.ok is False


def test_skill_check_no_proficiency_no_bonus(ctx):
    # Kira is not proficient in stealth (DEX +2, no prof).
    result = registry.execute("skill_check", ctx, character="Kira", skill="stealth",
                              dc=10, player_value=10)
    assert result.ok
    assert result.data["modifier"] == 2
    assert result.data["total"] == 12


def test_skill_check_companion_engine_rolled_logs_event(ctx):
    before = ctx.store.event_count()
    result = registry.execute("skill_check", ctx, character="Brother Aldric",
                              skill="medicine", dc=10)
    assert result.ok
    assert ctx.store.event_count() == before + 1  # event append (no mutation)
    row = ctx.store.conn.execute(
        "SELECT result FROM event_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    payload = json.loads(row["result"])
    assert payload["data"]["skill"] == "medicine"
    assert set(payload["data"]) == {"skill", "modifier", "dc", "natural", "total",
                                     "success", "margin"}


# -- own tests: saving_throw -------------------------------------------------

def test_saving_throw_applies_proficiency_and_logs(ctx):
    before = ctx.store.event_count()
    result = registry.execute("saving_throw", ctx, character="Kira", ability="str",
                              dc=10, player_value=15)
    assert result.ok
    assert result.data["modifier"] == 5  # STR +3, prof +2 (Kira proficient in str saves)
    assert result.data["total"] == 20 and result.data["success"] is True
    assert ctx.store.event_count() == before + 1
    row = ctx.store.conn.execute(
        "SELECT result FROM event_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    payload = json.loads(row["result"])
    assert payload["data"]["ability"] == "str"
    assert payload["data"]["success"] is True


def test_saving_throw_no_proficiency_no_bonus(ctx):
    # Kira is not proficient in wis saves.
    result = registry.execute("saving_throw", ctx, character="Kira", ability="wis",
                              dc=10, player_value=10)
    assert result.ok
    assert result.data["modifier"] == 1  # WIS 12 -> +1, no prof


def test_saving_throw_unknown_ability_refused(ctx):
    result = registry.execute("saving_throw", ctx, character="Kira", ability="luck", dc=10)
    assert result.ok is False


def test_saving_throw_normalizes_ability_input(ctx):
    # TVA-24: case/padding and full ability names collapse to the 3-letter key.
    for raw in ("STR", " Strength "):
        result = registry.execute("saving_throw", ctx, character="Kira",
                                  ability=raw, dc=10, player_value=10)
        assert result.ok, result.refusal
        assert result.data["ability"] == "str"


def test_saving_throw_unknown_ability_refusal_lists_vocabulary(ctx):
    result = registry.execute("saving_throw", ctx, character="Kira", ability="luck", dc=10)
    assert result.ok is False
    for ability in ("str", "dex", "con", "int", "wis", "cha"):
        assert ability in result.refusal


def test_tool_check_normalizes_ability_input(ctx):
    result = registry.execute("tool_check", ctx, character="Kira",
                              tool="thieves' tools", ability=" DEX", dc=10,
                              player_value=10)
    assert result.ok, result.refusal
    assert result.data["ability"] == "dex"


def test_saving_throw_auto_fails_str_dex_when_paralyzed(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.update_resources(kira["id"], conditions=["paralyzed"])
    before = ctx.store.event_count()
    result = registry.execute("saving_throw", ctx, character="Kira", ability="dex", dc=10)
    assert result.ok
    assert result.data["auto_fail"] is True
    assert result.data["success"] is False
    assert result.data["natural"] is None
    assert ctx.store.event_count() == before + 1  # still logs, no mutation of resources
    res = ctx.store.get_resources(kira["id"])
    assert res["conditions"] == ["paralyzed"]  # unchanged: saving_throw never mutates


def test_saving_throw_disadvantage_from_exhaustion(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.update_resources(kira["id"], conditions=["exhaustion"], exhaustion=3)
    # con save (proficient), disadvantage forced by exhaustion >= 3.
    result = registry.execute("saving_throw", ctx, character="Kira", ability="con",
                              dc=10, player_value=1)
    assert result.ok
    # player_value bypasses dice, but mode is still recorded via the roll itself
    # (no crash / refusal proves the disadvantage merge path executed cleanly).
    assert result.data["natural"] == 1


def test_saving_throw_gm_only_flags_event(ctx):
    result = registry.execute("saving_throw", ctx, character="Kira", ability="str",
                              dc=10, gm_only=True)
    assert result.ok and result.gm_only is True
    row = ctx.store.conn.execute(
        "SELECT rolls FROM event_log ORDER BY id DESC LIMIT 1").fetchone()
    assert '"gm_only": true' in row["rolls"]


# -- own tests: monster saving_throw (TVA-56) --------------------------------

def test_monster_saving_throw_in_combat(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "bandit", "count": 1, "band": "near"}],
                     pc_initiative=15)
    before = ctx.store.event_count()
    result = registry.execute("saving_throw", ctx, character="bandit-1",
                              ability="wis", dc=12, gm_only=True)
    assert result.ok, result.refusal
    # bandit WIS 10 -> +0, no save proficiency in the SRD record
    assert result.data["modifier"] == ability_modifier(10) == 0
    assert {"natural", "total", "success", "margin"} <= set(result.data)
    assert ctx.store.event_count() == before + 1  # the die went through the engine


def test_monster_save_uses_srd_save_proficiency(ctx):
    # flying-sword (CR 1/4) carries an explicit SRD saving-throw-dex
    # proficiency of +4, which is NOT the same as its bare DEX modifier
    # (DEX 15 -> +2) — proves the proficiency branch, not just the fallback.
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "flying-sword", "count": 1, "band": "near"}],
                     pc_initiative=15)
    result = registry.execute("saving_throw", ctx, character="flying-sword-1",
                              ability="dex", dc=12, gm_only=True)
    assert result.ok, result.refusal
    assert result.data["modifier"] == 4
    assert result.data["modifier"] != ability_modifier(15)


def test_monster_save_refuses_player_value(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "bandit", "count": 1, "band": "near"}],
                     pc_initiative=15)
    result = registry.execute("saving_throw", ctx, character="bandit-1",
                              ability="wis", dc=12, player_value=15)
    assert result.ok is False
    assert "player_value" in result.refusal
    assert result.data == {}  # refused before any engine roll — no natural/total leaked


def test_saving_throw_still_refuses_unknown_name_with_active_combat(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "bandit", "count": 1, "band": "near"}],
                     pc_initiative=15)
    result = registry.execute("saving_throw", ctx, character="Nobody",
                              ability="wis", dc=12)
    assert result.ok is False and "nobody" in result.refusal.lower()


def test_saving_throw_ambiguous_display_name_refused(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 2, "band": "near"}],
                     pc_initiative=15)
    result = registry.execute("saving_throw", ctx, character="Goblin",
                              ability="wis", dc=12)
    assert result.ok is False
    assert "goblin-1" in result.refusal and "goblin-2" in result.refusal
    assert "multiple" in result.refusal.lower()


# -- own tests: death_save ----------------------------------------------------

def test_death_save_refused_when_not_dying(ctx):
    result = registry.execute("death_save", ctx, character="Kira", player_value=10)
    assert result.ok is False


def test_death_save_unknown_character_refused(ctx):
    result = registry.execute("death_save", ctx, character="Nobody")
    assert result.ok is False


def test_death_save_success_mutates_death_saves_state(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 0 WHERE character_id = ?", (kira["id"],))
    ctx.store.conn.commit()
    before = ctx.store.event_count()
    result = registry.execute("death_save", ctx, character="Kira", player_value=15)
    assert result.ok
    assert ctx.store.event_count() == before + 1  # event append
    res = ctx.store.get_resources(kira["id"])
    assert res["death_saves"]["successes"] == 1  # state mutation
    assert result.data["event"] == "success"


def test_death_save_stabilizes_on_third_success(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 0 WHERE character_id = ?", (kira["id"],))
    ctx.store.conn.commit()
    registry.execute("death_save", ctx, character="Kira", player_value=15)
    registry.execute("death_save", ctx, character="Kira", player_value=16)
    result = registry.execute("death_save", ctx, character="Kira", player_value=17)
    assert result.ok
    assert result.data["event"] == "stabilized"
    res = ctx.store.get_resources(kira["id"])
    assert res["death_saves"]["stable"] is True
    # further death saves are refused once stable
    refused = registry.execute("death_save", ctx, character="Kira", player_value=10)
    assert refused.ok is False


def test_death_save_hardcore_mode_kills(tmp_path, rules_path):
    from dm_engine.commands.registry import CommandContext, RecordingRoller
    from dm_engine.content.lookup import RulesDB
    from dm_engine.state.store import CampaignStore

    store = CampaignStore.create(
        tmp_path / "campaigns", slug="hc", name="HC", death_mode="hardcore",
        rng_seed=1, skeleton={"premise": "test"},
    )
    hc_ctx = CommandContext(store=store, roller=RecordingRoller(1), rules=RulesDB(rules_path))
    registry.execute(
        "create_character", hc_ctx, name="Kira", role="pc",
        class_slug="fighter", race_slug="human",
        abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        ac=16, proficiencies={"skills": ["athletics"]},
        attacks=[{"weapon": "longsword", "name": "longsword"}],
    )
    kira = hc_ctx.store.get_character("Kira")
    hc_ctx.store.conn.execute("UPDATE resources SET hp = 0 WHERE character_id = ?", (kira["id"],))
    hc_ctx.store.conn.commit()
    registry.execute("death_save", hc_ctx, character="Kira", player_value=9)
    registry.execute("death_save", hc_ctx, character="Kira", player_value=8)
    result = registry.execute("death_save", hc_ctx, character="Kira", player_value=2)
    assert result.ok
    assert hc_ctx.store.get_character("Kira")["status"] == "dead"
    store.close()


def test_death_save_marks_combatant_defeated_in_active_combat(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 0 WHERE character_id = ?", (kira["id"],))
    ctx.store.conn.commit()
    ctx.store.update_combat(
        active=1,
        combatants=[
            {"key": "Kira", "name": "Kira", "defeated": False},
            {"key": "goblin-1", "name": "goblin-1"},
        ],
    )
    registry.execute("death_save", ctx, character="Kira", player_value=9)
    registry.execute("death_save", ctx, character="Kira", player_value=8)
    registry.execute("death_save", ctx, character="Kira", player_value=2)
    combatants = ctx.store.combat()["combatants"]
    kira_entry = next(c for c in combatants if c["name"] == "Kira")
    assert kira_entry["defeated"] is True
    goblin_entry = next(c for c in combatants if c["name"] == "goblin-1")
    assert "defeated" not in goblin_entry


def test_death_save_critical_failure_counts_two(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 0 WHERE character_id = ?", (kira["id"],))
    ctx.store.conn.commit()
    result = registry.execute("death_save", ctx, character="Kira", player_value=1)
    assert result.ok
    res = ctx.store.get_resources(kira["id"])
    assert res["death_saves"]["failures"] == 2  # nat 1 counts as two failures
    assert result.data["event"] == "critical_failure"


def test_death_save_companion_engine_rolled(ctx):
    aldric = ctx.store.get_character("Brother Aldric")
    ctx.store.conn.execute("UPDATE resources SET hp = 0 WHERE character_id = ?",
                           (aldric["id"],))
    ctx.store.conn.commit()
    result = registry.execute("death_save", ctx, character="Brother Aldric")
    assert result.ok  # no player_value: engine rolls via ctx.roller


def test_death_save_player_value_refused_for_companion(ctx):
    aldric = ctx.store.get_character("Brother Aldric")
    ctx.store.conn.execute("UPDATE resources SET hp = 0 WHERE character_id = ?",
                           (aldric["id"],))
    ctx.store.conn.commit()
    result = registry.execute("death_save", ctx, character="Brother Aldric", player_value=10)
    assert result.ok is False and "player" in result.refusal.lower()


# -- own tests: stabilize -----------------------------------------------------


def test_stabilize_refuses_when_not_dying(ctx, party):
    # Kira at full hp -> refused; requirement is named in the refusal.
    result = registry.execute("stabilize", ctx, character="Kira",
                              medicine_by="Brother Aldric")
    assert result.ok is False
    assert "dying" in result.refusal.lower()


def test_stabilize_unknown_character_refused(ctx, party):
    result = registry.execute("stabilize", ctx, character="Nobody")
    assert result.ok is False and "nobody" in result.refusal.lower()


def test_stabilize_with_medicine_check_success(ctx, party):
    # Aldric (companion, Medicine +4) rolls a natural 13 on the seeded
    # roller's first d20 -> total 17, beats DC 10.
    kira = ctx.store.get_character("Kira")
    ctx.store.update_resources(kira["id"], hp=0, conditions=["unconscious"])
    ctx.store.conn.commit()
    result = registry.execute("stabilize", ctx, character="Kira",
                              medicine_by="Brother Aldric", player_value=None)
    assert result.ok, result.refusal
    assert result.data["stabilized"] is True
    assert result.data["check"]["success"] is True
    res = ctx.store.get_resources(kira["id"])
    assert res["death_saves"]["stable"] is True
    assert res["hp"] == 0
    assert "unconscious" in res["conditions"]


def test_stabilize_medicine_check_failure(ctx, party):
    # Advance the seeded roller past its first 7 draws (13,13,7,20,6,8,8) so
    # the 8th natural is 5: 5 + 4 (Aldric's Medicine modifier) = 9 < DC 10.
    kira = ctx.store.get_character("Kira")
    ctx.store.update_resources(kira["id"], hp=0, conditions=["unconscious"])
    ctx.store.conn.commit()
    for _ in range(7):
        ctx.roller.roll("1d20")
    result = registry.execute("stabilize", ctx, character="Kira",
                              medicine_by="Brother Aldric")
    assert result.ok, result.refusal  # a failed check is not a refusal
    assert result.data["stabilized"] is False
    assert result.data["check"]["success"] is False
    res = ctx.store.get_resources(kira["id"])
    assert res["death_saves"]["stable"] is False
    assert res["hp"] == 0


def test_stabilize_without_checker_is_dm_fiat(ctx, party):
    kira = ctx.store.get_character("Kira")
    ctx.store.update_resources(kira["id"], hp=0, conditions=["unconscious"])
    ctx.store.conn.commit()
    result = registry.execute("stabilize", ctx, character="Kira")
    assert result.ok, result.refusal
    assert result.data["stabilized"] is True
    assert result.data["check"] is None
    res = ctx.store.get_resources(kira["id"])
    assert res["death_saves"]["stable"] is True
    assert res["hp"] == 0


def test_stabilize_medicine_by_player_value_refused_for_companion(ctx, party):
    # medicine_by is Brother Aldric, a companion: player_value must be
    # refused per _validate_player_value, and the refusal propagates.
    kira = ctx.store.get_character("Kira")
    ctx.store.update_resources(kira["id"], hp=0, conditions=["unconscious"])
    ctx.store.conn.commit()
    result = registry.execute("stabilize", ctx, character="Kira",
                              medicine_by="Brother Aldric", player_value=12)
    assert result.ok is False and "player" in result.refusal.lower()
    res = ctx.store.get_resources(kira["id"])
    assert res["death_saves"]["stable"] is False  # refused before any mutation


# -- own tests / binding tests: monster skill_check --------------------------

def test_monster_stealth_check_gm_only(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    result = registry.execute("skill_check", ctx, character="goblin-1",
                              skill="stealth", dc=12, gm_only=True)
    assert result.ok, result.refusal
    assert result.gm_only is True
    assert result.data["modifier"] == 6  # goblin Stealth +6 from the SRD record
    row = ctx.store.conn.execute(
        "SELECT rolls FROM event_log ORDER BY id DESC LIMIT 1").fetchone()
    assert '"gm_only": true' in row["rolls"]


def test_monster_check_refuses_player_value(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    result = registry.execute("skill_check", ctx, character="goblin-1",
                              skill="stealth", dc=12, player_value=15)
    assert result.ok is False


def test_monster_check_no_matching_proficiency_uses_ability_modifier(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    result = registry.execute("skill_check", ctx, character="goblin-1",
                              skill="athletics", dc=10, gm_only=True)
    assert result.ok, result.refusal
    assert result.data["modifier"] == ability_modifier(8)  # goblin STR 8 -> -1


def test_skill_check_still_refuses_unknown_name_with_active_combat(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    result = registry.execute("skill_check", ctx, character="Nobody",
                              skill="stealth", dc=12)
    assert result.ok is False and "nobody" in result.refusal.lower()


def test_skill_check_expertise_doubles_proficiency(ctx):
    # Mark Kira as dead to allow creating Sable as the PC (party() only includes active/defeated)
    kira = ctx.store.get_character("Kira")
    ctx.store.update_character(kira["id"], status="dead")

    registry.execute(
        "create_character", ctx, name="Sable", role="pc",
        class_slug="rogue", race_slug="wood-elf",
        abilities={"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10},
        ac=15, speed=35,
        proficiencies={"skills": ["stealth", "acrobatics"], "expertise": ["stealth"]},
        attacks=[{"weapon": "shortsword"}],
    )
    # player_value pins the d20 so the assertion is pure modifier math
    expert = registry.execute("skill_check", ctx, character="Sable",
                              skill="stealth", dc=10, player_value=10)
    assert expert.data["modifier"] == 8          # +4 dex +2 prof ×2
    assert expert.data["total"] == 18
    merely_proficient = registry.execute("skill_check", ctx, character="Sable",
                                         skill="acrobatics", dc=10, player_value=10)
    assert merely_proficient.data["modifier"] == 6


# -- own tests: tool_check ----------------------------------------------------

def _make_rogue(ctx, expertise=("thieves_tools",)):
    kira = ctx.store.get_character("Kira")
    ctx.store.update_character(kira["id"], status="dead")
    registry.execute(
        "create_character", ctx, name="Sable", role="pc",
        class_slug="rogue", race_slug="wood-elf",
        abilities={"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10},
        ac=15, speed=35,
        proficiencies={"skills": ["stealth"], "tools": ["thieves_tools"],
                       "expertise": list(expertise)},
        attacks=[{"weapon": "shortsword"}],
    )


def test_tool_check_expertise_and_explicit_ability(ctx):
    _make_rogue(ctx)
    result = registry.execute("tool_check", ctx, character="Sable",
                              tool="thieves_tools", ability="dex", dc=15,
                              player_value=10)
    assert result.ok
    assert result.data["modifier"] == 8            # +4 dex, +2 prof ×2
    assert result.data["total"] == 18
    assert result.data["success"] is True
    # same tool, different ability: recalling trap designs with INT
    brainy = registry.execute("tool_check", ctx, character="Sable",
                              tool="thieves_tools", ability="int", dc=10,
                              player_value=10)
    assert brainy.data["modifier"] == 4            # +0 int, +2 prof ×2


def test_tool_check_unproficient_gets_bare_ability(ctx):
    _make_rogue(ctx, expertise=())
    result = registry.execute("tool_check", ctx, character="Sable",
                              tool="herbalism_kit", ability="wis", dc=10,
                              player_value=10)
    assert result.data["modifier"] == 1            # bare WIS


def test_tool_check_refuses_bad_inputs(ctx):
    _make_rogue(ctx)
    assert not registry.execute("tool_check", ctx, character="Nobody",
                                tool="thieves_tools", ability="dex", dc=10).ok
    assert not registry.execute("tool_check", ctx, character="Sable",
                                tool="thieves_tools", ability="luck", dc=10).ok
    assert not registry.execute("tool_check", ctx, character="Sable",
                                tool="thieves_tools", ability="dex", dc=0).ok
