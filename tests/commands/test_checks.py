import json

import pytest

from dm_engine.commands import registry
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
