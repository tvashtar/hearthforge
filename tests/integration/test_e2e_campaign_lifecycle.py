"""Goal Gate e2e: a whole campaign lifecycle driven only through
``registry.execute`` — create the party, explore, level up, spend and restore
resources, then persist and reopen. Every mutation is a registry command; the
store is read only for assertions. Sheets are checked at three mutation points
(after award_xp, after damage, after rest) to prove the materialized markdown
tracks state.
"""

from __future__ import annotations

from dm_engine.commands import registry
from dm_engine.commands.campaign import bootstrap_campaign
from dm_engine.commands.registry import open_campaign_context

SEED = 42
PREMISE = "The village of Millbrook has lost its miller to the dark."


def _run(ctx, command, /, **kwargs):
    result = registry.execute(command, ctx, **kwargs)
    assert result.ok, f"{command} refused: {result.refusal}"
    return result


def _kira_sheet(ctx) -> str:
    return (ctx.store.root / "sheets" / "kira.md").read_text()


def _aldric_sheet(ctx) -> str:
    return (ctx.store.root / "sheets" / "brother-aldric.md").read_text()


def test_campaign_lifecycle(tmp_path, rules_path):
    campaigns_dir = tmp_path / "campaigns"
    slug = "millbrook"

    ctx = bootstrap_campaign(
        campaigns_dir, rules_path, slug=slug, name="The Missing Miller",
        death_mode="narrative", skeleton={"premise": PREMISE}, seed=SEED,
    )
    try:
        # --- party: a point-buy-legal fighter PC + a standard-array cleric ---
        _run(
            ctx, "create_character", name="Kira", role="pc",
            class_slug="fighter", race_slug="human",
            # point-buy-legal spread (every score in 8..15); CON 14 -> +2.
            abilities={"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8},
            ac=16,
            proficiencies={"skills": ["athletics", "intimidation"], "saves": ["str", "con"]},
            attacks=[{"name": "longsword", "ranged": False, "range_ft": 5,
                      "long_range_ft": None, "damage": "1d8", "damage_type": "slashing",
                      "ability": "str", "proficient": True}],
        )
        _run(
            ctx, "create_character", name="Brother Aldric", role="companion",
            class_slug="cleric", race_slug="hill-dwarf",
            # standard array 15/14/13/12/10/8.
            abilities={"str": 12, "dex": 13, "con": 14, "int": 10, "wis": 15, "cha": 8},
            ac=18,
            proficiencies={"skills": ["medicine", "religion"], "saves": ["wis", "cha"]},
            attacks=[{"name": "mace", "ranged": False, "range_ft": 5,
                      "long_range_ft": None, "damage": "1d6", "damage_type": "bludgeoning",
                      "ability": "str", "proficient": True}],
            spells_known=["cure-wounds", "bless"],
        )

        sheets_dir = ctx.store.root / "sheets"
        assert (sheets_dir / "kira.md").exists()
        assert (sheets_dir / "brother-aldric.md").exists()

        # --- explore: a new location + travel advances the world clock ------
        before = ctx.store.world_clock()
        _run(ctx, "create_location", slug="millbrook-mill", name="The Old Mill",
             description="A waterwheel long since stopped.", region="Millbrook")
        travelled = _run(ctx, "travel", destination_slug="millbrook-mill", days=1)
        after = travelled.data["clock"]
        assert after["day"] == before["day"] + 1  # world clock advanced
        assert after["location_slug"] == "millbrook-mill"
        _run(ctx, "set_scene", description="The mill stands silent in the fog.",
             location_slug="millbrook-mill")
        _run(ctx, "update_quest", slug="missing-miller",
             title="The Missing Miller", status="active",
             notes="Find out what happened to old Toma.")

        # --- award_xp 600 -> both level 1->2; the fighter gains d10+2 = 8 HP -
        award = _run(ctx, "award_xp", amount=600,
                     reason="quest: the missing miller")
        assert award.data["per_member"] == 300  # 600 split across two members
        kira_award = next(r for r in award.data["recipients"] if r["name"] == "Kira")
        assert kira_award["leveled_up"] is True and kira_award["level"] == 2
        kira = ctx.store.get_character("Kira")
        assert kira["level"] == 2 and kira["xp"] == 300
        assert kira["max_hp"] == 20  # 12 at L1, +8 (d10 avg 6 + CON 2) at L2

        # (sheet-reflects-mutation #1) new XP + new HP maximum on the sheet.
        # Leveling raises the maximum (to 20); current HP is unchanged (12).
        sheet = _kira_sheet(ctx)
        assert "XP: 300" in sheet
        assert "HP: 12 / 20" in sheet

        # --- spend a cleric slot: script damage, then cure-wounds the fighter
        aldric = ctx.store.get_character("Brother Aldric")
        slots_max = ctx.store.get_resources(aldric["id"])["spell_slots"]["1"]["max"]
        assert slots_max == 3  # cleric L2 -> three 1st-level slots

        _run(ctx, "dm_ruling", description="A collapsing beam clips Kira.",
             rationale="test scripting",
             effects=[{"op": "adjust_hp", "target": "Kira", "delta": -6}])
        assert ctx.store.get_resources(kira["id"])["hp"] == 6  # 12 current - 6

        # (sheet-reflects-mutation #2) current HP dropped, max unchanged.
        assert "HP: 6 / 20" in _kira_sheet(ctx)

        cure = _run(ctx, "cast_spell", caster="Brother Aldric",
                    spell_slug="cure-wounds", targets=["Kira"])
        healed = cure.data["per_target"][0]["healed"]
        assert healed >= 1
        assert ctx.store.get_resources(kira["id"])["hp"] == min(20, 6 + healed)
        slots_after_cast = ctx.store.get_resources(aldric["id"])["spell_slots"]["1"]
        assert slots_after_cast["remaining"] == slots_max - 1  # slot consumed

        # --- short rest: spend one of Kira's hit dice so the long-rest regain
        # assertion below is exercised against a genuinely depleted pool ------
        kira_hit_dice_full = ctx.store.get_resources(kira["id"])["hit_dice_remaining"]
        assert kira_hit_dice_full == kira["level"]  # 2/2 for a fresh L2 fighter
        short = _run(ctx, "rest", kind="short", hit_dice={"Kira": 1},
                     player_hit_die_values=[6])
        short_healed = short.data["per_character"][0]["healed"]
        assert short_healed >= 0  # exact roll varies; only bound it here
        kira_res_after_short = ctx.store.get_resources(kira["id"])
        assert kira_res_after_short["hit_dice_remaining"] == kira_hit_dice_full - 1
        assert kira_res_after_short["hp"] <= kira["max_hp"]  # bounded, not exact

        # --- long rest: slots and hit dice restored (RAW), HP topped off -----
        rest = _run(ctx, "rest", kind="long")
        assert rest.data["kind"] == "long"
        aldric_res = ctx.store.get_resources(aldric["id"])
        assert aldric_res["spell_slots"]["1"]["remaining"] == slots_max  # restored
        # RAW: regain max(1, total // 2) hit dice, capped at the pool total.
        assert aldric_res["hit_dice_remaining"] == aldric["level"]  # L2 pool full again
        kira_res_after_long = ctx.store.get_resources(kira["id"])
        assert kira_res_after_long["hit_dice_remaining"] == kira_hit_dice_full  # 1 -> 2
        kira_rest_entry = next(
            c for c in rest.data["per_character"] if c["name"] == "Kira"
        )
        assert kira_rest_entry["hit_dice_regained"] >= 1  # RAW regain actually happened
        assert kira_res_after_long["hp"] == 20  # HP restored to max

        # (sheet-reflects-mutation #3) the cleric's sheet shows full slots again.
        assert "Level 1: 3 / 3" in _aldric_sheet(ctx)

        # --- end the session with a recap -----------------------------------
        recap_text = "The party reached the silent mill and learned Toma vanished."
        _run(ctx, "end_session", recap=recap_text)
    finally:
        ctx.store.close()

    # --- reopen and confirm the brief reflects the whole persisted world -----
    ctx2 = open_campaign_context(campaigns_dir, slug, rules_path)
    try:
        brief = registry.execute("get_campaign_brief", ctx2)
        assert brief.ok, brief.refusal
        data = brief.data
        assert data["skeleton"]["premise"] == PREMISE      # skeleton premise
        assert data["clock"]["location_slug"] == "millbrook-mill"  # clock
        assert data["scene"] == "The mill stands silent in the fog."  # scene
        by_name = {p["name"]: p for p in data["party"]}    # party levels/hp
        assert by_name["Kira"]["level"] == 2 and by_name["Kira"]["hp"] == 20
        assert by_name["Brother Aldric"]["level"] == 2
        quest_slugs = {q["slug"] for q in data["quests"]}   # open quests
        assert "missing-miller" in quest_slugs
        assert data["recap"] == recap_text                  # the recap text
    finally:
        ctx2.store.close()
