"""Goal Gate e2e: the three spell tiers resolved through ``registry.execute``.

Tier 1 spells the engine resolves fully (a heal; a saving-throw AoE). Tier 2
spells spend the slot + concentration and hand the effect to the DM via
``dm_ruling``. Slot exhaustion produces an ordinal refusal. The party is a
fighter PC plus a level-3 cleric (2nd-level slots for hold-person), mirroring
the unit fixtures.
"""

from __future__ import annotations

from dm_engine.commands import registry
from dm_engine.commands.campaign import bootstrap_campaign

SEED = 314

KIRA_KWARGS = dict(
    name="Kira", role="pc", class_slug="fighter", race_slug="human",
    abilities={"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8},
    ac=16, proficiencies={"skills": ["athletics"], "saves": ["str", "con"]},
    attacks=[{"name": "longsword", "ranged": False, "range_ft": 5,
              "long_range_ft": None, "damage": "1d8", "damage_type": "slashing",
              "ability": "str", "proficient": True}],
)


def _last_event(ctx) -> dict:
    return ctx.store.conn.execute(
        "SELECT command, is_ruling, rationale FROM event_log ORDER BY id DESC LIMIT 1"
    ).fetchone()


def _spell_party(tmp_path, rules_path):
    """Bootstrap a campaign with Kira (fighter PC) and a level-3 cleric.

    The level-3 cleric is set up with direct store writes (level/HP/slots),
    the same approach as the root conftest ``party`` fixture: this is test
    scaffolding, not gameplay, so it bypasses the XP-splitting award_xp path
    (which would also level Kira) to land the exact 4/2 slot layout.
    """
    ctx = bootstrap_campaign(
        tmp_path / "campaigns", rules_path, slug="spells", name="Spells",
        death_mode="narrative", skeleton={"premise": "a spell duel"}, seed=SEED,
    )
    assert registry.execute("create_character", ctx, **KIRA_KWARGS).ok
    assert registry.execute(
        "create_character", ctx, name="Brother Aldric", role="companion",
        class_slug="cleric", race_slug="hill-dwarf",
        abilities={"str": 12, "dex": 13, "con": 14, "int": 10, "wis": 15, "cha": 8},
        ac=18, proficiencies={"skills": ["medicine"], "saves": ["wis", "cha"]},
        attacks=[{"name": "mace", "ranged": False, "range_ft": 5,
                  "long_range_ft": None, "damage": "1d6", "damage_type": "bludgeoning",
                  "ability": "str", "proficient": True}],
        spells_known=["cure-wounds", "burning-hands", "hold-person"],
    ).ok
    aldric = ctx.store.get_character("Brother Aldric")
    ctx.store.update_character(aldric["id"], level=3, xp=900, max_hp=24)
    ctx.store.update_resources(
        aldric["id"], hp=24, hit_dice_remaining=3,
        spell_slots={"1": {"max": 4, "remaining": 4}, "2": {"max": 2, "remaining": 2}},
    )
    ctx.store.conn.commit()
    return ctx


def _aldric_slots(ctx) -> dict:
    aldric = ctx.store.get_character("Brother Aldric")
    return ctx.store.get_resources(aldric["id"])["spell_slots"]


def test_tier1_heal_consumes_first_level_slot(tmp_path, rules_path):
    ctx = _spell_party(tmp_path, rules_path)
    try:
        kira = ctx.store.get_character("Kira")  # fighter L1, max_hp 12
        # Script the fighter to 3 HP (delta from full).
        registry.execute("dm_ruling", ctx, description="Kira bleeds out to 3 HP.",
                          rationale="test scripting",
                          effects=[{"op": "adjust_hp", "target": "Kira", "delta": -9}])
        assert ctx.store.get_resources(kira["id"])["hp"] == 3

        before = _aldric_slots(ctx)["1"]["remaining"]
        result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                                  spell_slug="cure-wounds", targets=["Kira"])
        assert result.ok, result.refusal
        assert result.data["tier"] == 1 and result.data["effect"] == "heal"
        healed = result.data["per_target"][0]["healed"]
        assert healed >= 1
        # Healed via the effect record; HP rose by the rolled amount (capped).
        assert ctx.store.get_resources(kira["id"])["hp"] == min(12, 3 + healed)
        assert _aldric_slots(ctx)["1"]["remaining"] == before - 1  # 1st slot spent
    finally:
        ctx.store.close()


def test_tier1_aoe_burning_hands_clusters_and_saves_halve(tmp_path, rules_path):
    ctx = _spell_party(tmp_path, rules_path)
    try:
        registry.execute("start_combat", ctx,
                         monsters=[{"slug": "goblin", "count": 3, "band": "near"}],
                         pc_initiative=15)
        result = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                                  spell_slug="burning-hands", band="near", spend="none")
        assert result.ok, result.refusal
        per = result.data["per_target"]
        assert len(per) == 3  # 15-ft cone cap = 3, exactly the three goblins

        combatants = {c["key"]: c for c in ctx.store.combat()["combatants"]}
        for entry in per:
            assert entry["save"]["dc"] == 12  # 8 + prof 2 + WIS 2
            if entry["save"]["success"]:
                assert entry["damage"] == entry["damage_rolled"] // 2  # half, floored
            # goblins start at 7 HP; combat state reflects the damage dealt.
            assert combatants[entry["key"]]["hp"] == max(0, 7 - entry["damage"])
    finally:
        ctx.store.close()


def test_tier2_hold_person_needs_ruling_and_both_events_audit(tmp_path, rules_path):
    ctx = _spell_party(tmp_path, rules_path)
    try:
        registry.execute("start_combat", ctx,
                         monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                         pc_initiative=15)
        before = _aldric_slots(ctx)["2"]["remaining"]

        cast = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                                spell_slug="hold-person", spend="none")
        assert cast.ok, cast.refusal
        assert cast.data["tier"] == 2 and cast.data["needs_ruling"] is True
        assert cast.data["spell_text"]  # the DM gets the spell text to adjudicate
        # slot consumed + concentration recorded (spell + duration)
        assert _aldric_slots(ctx)["2"]["remaining"] == before - 1
        conc = ctx.store.get_resources(
            ctx.store.get_character("Brother Aldric")["id"])["concentration"]
        assert conc["spell"] == "hold-person" and conc["duration"]
        # the cast is a normal command, NOT a ruling
        cast_event = _last_event(ctx)
        assert cast_event["command"] == "cast_spell" and cast_event["is_ruling"] == 0

        goblin = next(c["key"] for c in ctx.store.combat()["combatants"]
                      if c["kind"] == "monster")
        ruling = registry.execute(
            "dm_ruling", ctx, description="The goblin fails its WIS save and locks up.",
            rationale="hold-person paralyzes on a failed save",
            effects=[{"op": "set_condition", "target": goblin, "condition": "paralyzed"}],
        )
        assert ruling.ok, ruling.refusal
        combatant = next(c for c in ctx.store.combat()["combatants"] if c["key"] == goblin)
        assert "paralyzed" in combatant["conditions"]
        # the ruling IS audited as a ruling, with its rationale
        ruling_event = _last_event(ctx)
        assert ruling_event["command"] == "dm_ruling"
        assert ruling_event["is_ruling"] == 1
        assert ruling_event["rationale"] == "hold-person paralyzes on a failed save"
    finally:
        ctx.store.close()


def test_casting_with_no_slots_left_gives_ordinal_refusal(tmp_path, rules_path):
    ctx = _spell_party(tmp_path, rules_path)
    try:
        # Spend one 2nd-level slot on a real hold-person cast...
        first = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                                 spell_slug="hold-person")
        assert first.ok, first.refusal
        # ...then drain the last one via a ruling (sanctioned scaffolding).
        registry.execute("dm_ruling", ctx, description="The last slot fizzles.",
                          rationale="test scripting",
                          effects=[{"op": "adjust_slot", "character": "Brother Aldric",
                                    "slot_level": 2, "delta": -1}])
        assert _aldric_slots(ctx)["2"]["remaining"] == 0

        refused = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                                   spell_slug="hold-person")
        assert refused.ok is False
        assert "2nd-level slots remaining" in refused.refusal  # ordinal refusal text
    finally:
        ctx.store.close()
