import pytest

from dm_engine.commands import registry
from dm_engine.commands.spells import _heal_notation

pytestmark = pytest.mark.usefixtures("party")  # Aldric knows cure-wounds, bless, guiding-bolt, sacred-flame, burning-hands (add burning-hands + hold-person to his spells_known in the fixture for these tests)


def test_cure_wounds_heals_and_consumes_slot(ctx):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 3 WHERE character_id = ?",
                           (kira["id"],))
    ctx.store.conn.commit()
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="cure-wounds", targets=["Kira"])
    assert result.ok, result.refusal
    assert result.data["tier"] == 1 and result.data["effect"] == "heal"
    healed = result.data["per_target"][0]["healed"]
    assert 3 <= healed <= 10  # 1d8 + WIS 2
    aldric = ctx.store.get_character("Brother Aldric")
    slots = ctx.store.get_resources(aldric["id"])["spell_slots"]
    assert slots["1"]["remaining"] == slots["1"]["max"] - 1


def test_no_slots_left_is_a_structured_refusal(ctx):
    aldric = ctx.store.get_character("Brother Aldric")
    res = ctx.store.get_resources(aldric["id"])
    slots = res["spell_slots"]; slots["1"]["remaining"] = 0
    ctx.store.update_resources(aldric["id"], spell_slots=slots)
    ctx.store.conn.commit()
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="cure-wounds", targets=["Kira"])
    assert result.ok is False
    assert "1st-level slots remaining" in result.refusal


def test_burning_hands_clusters_and_save_halves(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 3, "band": "near"}],
                     pc_initiative=15)
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="burning-hands", band="near", spend="none")
    assert result.ok, result.refusal
    per = result.data["per_target"]
    assert len(per) == 3  # 15-ft cone -> max 3 targets, all 3 goblins in band
    for entry in per:
        assert entry["save"]["dc"] == 8 + 2 + 2  # prof 2 + WIS 2
        if entry["save"]["success"]:
            assert entry["damage"] == entry["damage_rolled"] // 2


def test_tier2_spell_directs_to_dm_ruling(ctx):
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="hold-person", targets=[])
    assert result.ok
    assert result.data["needs_ruling"] is True and result.data["tier"] == 2
    aldric = ctx.store.get_character("Brother Aldric")
    res = ctx.store.get_resources(aldric["id"])
    assert res["concentration"]["spell"] == "hold-person"
    assert res["spell_slots"]["2"]["remaining"] == res["spell_slots"]["2"]["max"] - 1


# --- own tests ----------------------------------------------------------


def test_upcast_burning_hands_uses_higher_slot(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 3, "band": "near"}],
                     pc_initiative=15)
    aldric = ctx.store.get_character("Brother Aldric")
    before = ctx.store.get_resources(aldric["id"])["spell_slots"]
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="burning-hands", slot_level=2,
                              band="near", spend="none")
    assert result.ok, result.refusal
    assert result.data["slot_used"] == 2
    after = ctx.store.get_resources(aldric["id"])["spell_slots"]
    assert after["2"]["remaining"] == before["2"]["remaining"] - 1
    assert after["1"]["remaining"] == before["1"]["remaining"]  # 1st untouched
    # Upcast burning-hands rolls 4d6 (min 4), vs 3d6 (min 3) at slot 1.
    for entry in result.data["per_target"]:
        assert entry["damage_rolled"] >= 4


def test_fire_bolt_is_a_spell_attack_vs_ac(ctx):
    aldric = ctx.store.get_character("Brother Aldric")
    known = ctx.store.get_character("Brother Aldric")["spells_known"] + ["fire-bolt"]
    ctx.store.update_character(aldric["id"], spells_known=known)
    ctx.store.conn.commit()
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="fire-bolt", targets=["goblin-1"],
                              spend="none")
    assert result.ok, result.refusal
    assert result.data["effect"] == "damage"
    assert result.data["slot_used"] is None  # cantrip: no slot
    entry = result.data["per_target"][0]
    assert entry["key"] == "goblin-1"
    assert "hit" in entry
    assert result.data["attack_roll"]["target_ac"] == 15
    # No slot spent by the cantrip.
    slots = ctx.store.get_resources(aldric["id"])["spell_slots"]
    assert slots["1"]["remaining"] == slots["1"]["max"]


def test_concentration_replaces_prior_spell(ctx):
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="bless")
    assert result.ok, result.refusal
    aldric = ctx.store.get_character("Brother Aldric")
    assert ctx.store.get_resources(aldric["id"])["concentration"]["spell"] == "bless"
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="hold-person")
    assert result.ok, result.refusal
    assert result.data["concentration_replaced"] == "bless"
    res = ctx.store.get_resources(aldric["id"])
    assert res["concentration"]["spell"] == "hold-person"


def test_heal_notation_avoids_double_sign_for_negative_mod():
    # A negative ability modifier must not yield "1d8+-2" (Fix 2).
    assert _heal_notation("1d8 + MOD", -2) == "1d8-2"
    assert _heal_notation("1d8 + MOD", 3) == "1d8+3"
    assert _heal_notation("1d8 + MOD", 0) == "1d8+0"


def _aldric_state(ctx):
    aldric = ctx.store.get_character("Brother Aldric")
    return ctx.store.get_resources(aldric["id"])


def test_heal_without_target_refuses_and_keeps_slot(ctx):
    # (a) validate-before-consume: a refused heal must not spend the slot
    # or touch concentration.
    before = _aldric_state(ctx)
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="cure-wounds", targets=[])
    assert result.ok is False
    assert "needs a target to heal" in result.refusal
    after = _aldric_state(ctx)
    assert after["spell_slots"]["1"]["remaining"] == before["spell_slots"]["1"]["remaining"]
    assert after["concentration"] == before["concentration"]


def test_save_spell_without_band_refuses_and_keeps_slot(ctx):
    # (b) an area save spell with no band refuses before consuming the slot.
    before = _aldric_state(ctx)
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="burning-hands")
    assert result.ok is False
    assert "needs a band to target" in result.refusal
    after = _aldric_state(ctx)
    assert after["spell_slots"]["1"]["remaining"] == before["spell_slots"]["1"]["remaining"]


def test_aoe_into_empty_band_refuses_and_keeps_slot(ctx):
    # (c) auto-cluster over a band with no hostiles refuses; slot untouched.
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 3, "band": "near"}],
                     pc_initiative=15)
    before = _aldric_state(ctx)
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="burning-hands", band="far", spend="none")
    assert result.ok is False
    assert "no valid targets at far" in result.refusal
    after = _aldric_state(ctx)
    assert after["spell_slots"]["1"]["remaining"] == before["spell_slots"]["1"]["remaining"]


def _grant_spells(ctx, name, *slugs):
    char = ctx.store.get_character(name)
    known = char["spells_known"] + [s for s in slugs if s not in char["spells_known"]]
    ctx.store.update_character(char["id"], spells_known=known)
    ctx.store.conn.commit()


def test_sleep_has_no_damage_type_and_resolves_as_tier2(ctx):
    # sleep's SRD record carries a damage block (the 5d8 HP pool) with no
    # damage_type; it must hand off to dm_ruling, not KeyError in Tier 1.
    _grant_spells(ctx, "Brother Aldric", "sleep")
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 2, "band": "near"}],
                     pc_initiative=15)
    before = _aldric_state(ctx)["spell_slots"]["1"]["remaining"]
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="sleep", band="near", spend="none")
    assert result.ok, result.refusal
    assert result.data["tier"] == 2 and result.data["needs_ruling"] is True
    assert "5d8" in result.data["spell_text"] or result.data["spell_text"]
    after = _aldric_state(ctx)["spell_slots"]["1"]["remaining"]
    assert after == before - 1  # Tier 2 still spends the slot


def test_ritual_cast_spends_no_slot_and_advances_clock(ctx):
    _grant_spells(ctx, "Brother Aldric", "detect-poison-and-disease")
    before_slots = _aldric_state(ctx)["spell_slots"]["1"]["remaining"]
    before_clock = ctx.store.world_clock()
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="detect-poison-and-disease", ritual=True)
    assert result.ok, result.refusal
    assert result.data["ritual"] is True
    assert result.data["slot_used"] is None
    assert result.data["tier"] == 2 and result.data["needs_ruling"] is True
    after_slots = _aldric_state(ctx)["spell_slots"]["1"]["remaining"]
    assert after_slots == before_slots  # no slot consumed
    after_clock = ctx.store.world_clock()
    elapsed = (after_clock["day"] - before_clock["day"]) * 1440 + (
        after_clock["minutes"] - before_clock["minutes"])
    assert elapsed == 10  # +10 minutes casting time


def test_ritual_clock_overflow_rolls_the_day(ctx):
    _grant_spells(ctx, "Brother Aldric", "detect-poison-and-disease")
    ctx.store.update_world_clock(day=3, minutes=1435)  # 5 min to midnight
    ctx.store.conn.commit()
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="detect-poison-and-disease", ritual=True)
    assert result.ok, result.refusal
    clock = ctx.store.world_clock()
    assert clock["day"] == 4 and clock["minutes"] == 5


def test_ritual_refuses_non_ritual_spell(ctx):
    before = _aldric_state(ctx)["spell_slots"]["1"]["remaining"]
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="cure-wounds", targets=["Kira"],
                              ritual=True)
    assert result.ok is False
    assert "not a ritual" in result.refusal
    assert _aldric_state(ctx)["spell_slots"]["1"]["remaining"] == before


def test_ritual_refuses_in_combat(ctx):
    _grant_spells(ctx, "Brother Aldric", "detect-poison-and-disease")
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="detect-poison-and-disease", ritual=True)
    assert result.ok is False
    assert "combat" in result.refusal


def test_ritual_refuses_class_without_ritual_casting(ctx):
    # A sorcerer knows detect-magic but has no Ritual Casting feature (2014).
    registry.execute(
        "create_character", ctx, name="Vex", role="companion",
        class_slug="sorcerer", race_slug="human",
        abilities={"str": 8, "dex": 14, "con": 12, "int": 10, "wis": 10, "cha": 16},
        ac=12, proficiencies={"skills": ["arcana"]},
        attacks=[{"weapon": "dagger", "name": "dagger"}],
        spells_known=["detect-magic"],
    )
    result = registry.execute("cast_spell", ctx, caster="Vex",
                              spell_slug="detect-magic", ritual=True)
    assert result.ok is False
    assert "no Ritual Casting feature" in result.refusal


def test_ritual_still_sets_concentration(ctx):
    # detect-magic is both a ritual and a concentration spell.
    _grant_spells(ctx, "Brother Aldric", "detect-magic")
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="detect-magic", ritual=True)
    assert result.ok, result.refusal
    aldric = ctx.store.get_character("Brother Aldric")
    assert ctx.store.get_resources(aldric["id"])["concentration"]["spell"] == "detect-magic"


def _start_goblin_fight(ctx, count):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": count, "band": "near"}],
                     pc_initiative=15)


def test_magic_missile_rolls_three_independent_darts(ctx):
    _grant_spells(ctx, "Brother Aldric", "magic-missile")
    _start_goblin_fight(ctx, 1)
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="magic-missile", targets=["goblin-1"],
                              spend="none")
    assert result.ok, result.refusal
    assert result.data["tier"] == 1 and result.data["darts"] == 3
    per = result.data["per_target"]
    assert len(per) == 3  # one entry per dart, all on the single target
    assert "attack_roll" not in result.data  # auto-hit: no attack roll ...
    for i, entry in enumerate(per, start=1):
        assert entry["key"] == "goblin-1"
        assert entry["dart"] == i and entry["hit"] is True
        assert "save" not in entry  # ... and no save either
        assert 2 <= entry["damage_rolled"] <= 5  # each dart is its own 1d4+1


def test_magic_missile_upcast_adds_a_dart_and_spends_the_slot(ctx):
    _grant_spells(ctx, "Brother Aldric", "magic-missile")
    _start_goblin_fight(ctx, 1)
    before = _aldric_state(ctx)["spell_slots"]
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="magic-missile", slot_level=2,
                              targets=["goblin-1"], spend="none")
    assert result.ok, result.refusal
    assert result.data["darts"] == 4  # +1 dart per slot level above 1st
    assert len(result.data["per_target"]) == 4
    after = _aldric_state(ctx)["spell_slots"]
    assert after["2"]["remaining"] == before["2"]["remaining"] - 1
    assert after["1"]["remaining"] == before["1"]["remaining"]


def test_magic_missile_split_targets_assigns_per_dart(ctx):
    _grant_spells(ctx, "Brother Aldric", "magic-missile")
    _start_goblin_fight(ctx, 2)
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="magic-missile",
                              targets=["goblin-1", "goblin-2", "goblin-1"],
                              spend="none")
    assert result.ok, result.refusal
    keys = [e["key"] for e in result.data["per_target"]]
    assert keys == ["goblin-1", "goblin-2", "goblin-1"]


def test_magic_missile_bad_target_list_refuses_and_keeps_slot(ctx):
    _grant_spells(ctx, "Brother Aldric", "magic-missile")
    _start_goblin_fight(ctx, 2)
    before = _aldric_state(ctx)["spell_slots"]["1"]["remaining"]
    # 2 targets for 3 darts: neither "all on one" nor "one per dart".
    wrong_len = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                                 spell_slug="magic-missile",
                                 targets=["goblin-1", "goblin-2"], spend="none")
    assert wrong_len.ok is False
    assert "3 darts" in wrong_len.refusal
    # A dart aimed at a non-combatant refuses too.
    ghost = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                             spell_slug="magic-missile",
                             targets=["goblin-1", "goblin-9", "goblin-1"],
                             spend="none")
    assert ghost.ok is False
    assert "goblin-9" in ghost.refusal
    no_target = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                                 spell_slug="magic-missile", spend="none")
    assert no_target.ok is False
    # Every refusal happened before the slot was consumed.
    assert _aldric_state(ctx)["spell_slots"]["1"]["remaining"] == before


def test_magic_missile_concentration_check_per_dart(ctx):
    # Each dart that lands on a concentrating creature is its own hit and
    # must raise its own concentration check (DC 10 for < 22 damage).
    _grant_spells(ctx, "Brother Aldric", "magic-missile")
    _start_goblin_fight(ctx, 1)
    bless = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                             spell_slug="bless", spend="none")
    assert bless.ok, bless.refusal
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="magic-missile",
                              targets=["Brother Aldric"], spend="none")
    assert result.ok, result.refusal
    per = result.data["per_target"]
    assert len(per) == 3
    for entry in per:  # Aldric (hp 24) stays conscious through 3-15 damage
        assert entry["concentration_check"] == {"dc": 10}


# --- forgiving combat-target resolution (TVA-38) -------------------------


def _start_labeled_fight(ctx, count=1, label="Fen Scout"):
    registry.execute(
        "start_combat", ctx,
        monsters=[{"slug": "goblin", "count": count, "band": "near", "label": label}],
        pc_initiative=15,
    )


def test_spell_attack_target_resolves_by_display_name(ctx):
    # TVA-38: casting at a monster by its narrated display name, any case.
    _grant_spells(ctx, "Brother Aldric", "fire-bolt")
    _start_labeled_fight(ctx)
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="fire-bolt", targets=["fen scout"],
                              spend="none")
    assert result.ok, result.refusal
    # The payload reports the canonical key, not the alias the caller typed.
    assert result.data["per_target"][0]["key"] == "goblin-1"


def test_spell_attack_unknown_target_lists_roster(ctx):
    _grant_spells(ctx, "Brother Aldric", "fire-bolt")
    _start_labeled_fight(ctx)
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="fire-bolt", targets=["Bandit 3"],
                              spend="none")
    assert result.ok is False
    assert "'Bandit 3'" in result.refusal
    assert 'goblin-1 "Fen Scout"' in result.refusal
    assert "Kira" in result.refusal


def test_spell_attack_ambiguous_target_refused(ctx):
    # Two unlabeled goblins both display as "Goblin" — never guess.
    _grant_spells(ctx, "Brother Aldric", "fire-bolt")
    _start_goblin_fight(ctx, 2)
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="fire-bolt", targets=["Goblin"],
                              spend="none")
    assert result.ok is False
    assert "goblin-1" in result.refusal and "goblin-2" in result.refusal
    assert "multiple" in result.refusal.lower()


def test_magic_missile_darts_resolve_by_display_name(ctx):
    _grant_spells(ctx, "Brother Aldric", "magic-missile")
    _start_labeled_fight(ctx)
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="magic-missile", targets=["FEN SCOUT"],
                              spend="none")
    assert result.ok, result.refusal
    assert [e["key"] for e in result.data["per_target"]] == ["goblin-1"] * 3


def test_magic_missile_ambiguous_dart_target_keeps_slot(ctx):
    _grant_spells(ctx, "Brother Aldric", "magic-missile")
    _start_goblin_fight(ctx, 2)
    before = _aldric_state(ctx)["spell_slots"]["1"]["remaining"]
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="magic-missile", targets=["Goblin"],
                              spend="none")
    assert result.ok is False
    assert "goblin-1" in result.refusal and "goblin-2" in result.refusal
    assert _aldric_state(ctx)["spell_slots"]["1"]["remaining"] == before


def test_save_spell_explicit_target_resolves_by_display_name(ctx):
    _start_labeled_fight(ctx)
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="burning-hands", targets=["fen scout"],
                              band="near", spend="none")
    assert result.ok, result.refusal
    assert result.data["per_target"][0]["key"] == "goblin-1"


def test_save_spell_unknown_target_lists_roster(ctx):
    _start_labeled_fight(ctx)
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="burning-hands", targets=["Bandit 3"],
                              band="near", spend="none")
    assert result.ok is False
    assert "'Bandit 3'" in result.refusal
    assert 'goblin-1 "Fen Scout"' in result.refusal


def test_heal_target_resolves_case_insensitively_in_combat(ctx):
    _start_labeled_fight(ctx)
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 3 WHERE character_id = ?",
                           (kira["id"],))
    ctx.store.conn.commit()
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="cure-wounds", targets=["kira"],
                              spend="none")
    assert result.ok, result.refusal
    assert ctx.store.get_resources(kira["id"])["hp"] > 3


def test_unknown_spell_and_not_known_refuse(ctx):
    missing = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                               spell_slug="wish")
    assert missing.ok is False
    not_known = registry.execute("cast_spell", ctx, caster="Kira",
                                 spell_slug="cure-wounds", targets=["Kira"])
    assert not_known.ok is False


def test_not_known_refusal_lists_known_spells_and_steers(ctx):
    # Kira (fighter) knows no spells: the refusal must say so explicitly
    # and steer to the legal alternatives, not just name the missing spell.
    result = registry.execute("cast_spell", ctx, caster="Kira",
                              spell_slug="cure-wounds", targets=["Kira"])
    assert result.ok is False
    assert result.refusal == (
        "Kira does not know Cure Wounds (knows: none) — add spells at "
        "character creation, or adjudicate the effect via dm_ruling"
    )


def test_not_known_refusal_lists_known_spells_when_some_are_known(ctx):
    # Brother Aldric knows several spells but not a spell he lacks — the
    # refusal should enumerate his actual list, sorted, to steer the model.
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="bane")
    assert result.ok is False
    assert result.refusal == (
        "Brother Aldric does not know Bane (knows: bless, burning-hands, "
        "cure-wounds, guiding-bolt, hold-person, sacred-flame) — add spells "
        "at character creation, or adjudicate the effect via dm_ruling"
    )


def _kill_kira_via_death_saves(ctx):
    """Drive Kira through the real dying path to a kill: 0 hp, unconscious,
    three failed death saves (checks.py's death_save sets `characters.status`
    per death_mode and, when combat is active, marks the combatant tracker's
    `defeated` flag via `_mark_combatant_defeated` — TVA-51's landed contract).
    """
    kira = ctx.store.get_character("Kira")
    ctx.store.update_resources(kira["id"], hp=0, conditions=["unconscious"])
    ctx.store.conn.commit()
    for _ in range(3):
        result = registry.execute("death_save", ctx, character="Kira", player_value=2)
        assert result.ok, result.refusal


def test_healing_revived_pc_rejoins_combat(ctx, party):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    _kill_kira_via_death_saves(ctx)
    kira = ctx.store.get_character("Kira")
    assert kira["status"] == "defeated"  # narrative mode
    kira_c = next(c for c in ctx.store.combat()["combatants"] if c["key"] == "Kira")
    assert kira_c["defeated"] is True

    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="cure-wounds", targets=["Kira"], spend="none")
    assert result.ok, result.refusal

    res = ctx.store.get_resources(kira["id"])
    assert res["hp"] > 0
    assert "unconscious" not in res["conditions"]
    assert res["death_saves"] == {
        "successes": 0, "failures": 0, "stable": False, "dead": False,
    }
    assert ctx.store.get_character("Kira")["status"] == "active"
    kira_c = next(c for c in ctx.store.combat()["combatants"] if c["key"] == "Kira")
    assert kira_c["defeated"] is False


def test_healing_hardcore_dead_pc_is_refused(ctx_hardcore, party_hardcore):
    ctx = ctx_hardcore
    _kill_kira_via_death_saves(ctx)
    assert ctx.store.get_character("Kira")["status"] == "dead"  # hardcore mode

    aldric = ctx.store.get_character("Brother Aldric")
    before = ctx.store.get_resources(aldric["id"])["spell_slots"]["1"]["remaining"]
    result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                              spell_slug="cure-wounds", targets=["Kira"])
    assert result.ok is False
    assert "Kira" in result.refusal and "dead" in result.refusal.lower()
    # The refusal must land before the slot is spent (registry commits
    # refusals) — and Kira must stay exactly as she died.
    after = ctx.store.get_resources(aldric["id"])["spell_slots"]["1"]["remaining"]
    assert after == before
    kira = ctx.store.get_character("Kira")
    assert kira["status"] == "dead"
    assert ctx.store.get_resources(kira["id"])["hp"] == 0
