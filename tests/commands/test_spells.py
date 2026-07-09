import pytest

from dm_engine.commands import registry

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


def test_unknown_spell_and_not_known_refuse(ctx):
    missing = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                               spell_slug="wish")
    assert missing.ok is False
    not_known = registry.execute("cast_spell", ctx, caster="Kira",
                                 spell_slug="cure-wounds", targets=["Kira"])
    assert not_known.ok is False
