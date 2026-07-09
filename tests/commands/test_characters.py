from dm_engine.commands import registry

FIGHTER_KWARGS = dict(
    name="Kira", role="pc", class_slug="fighter", race_slug="human",
    abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
    ac=16, proficiencies={"skills": ["athletics", "intimidation"], "saves": ["str", "con"]},
    attacks=[{"name": "longsword", "ranged": False, "range_ft": 5, "long_range_ft": None,
              "damage": "1d8", "damage_type": "slashing", "ability": "str",
              "proficient": True}],
)


def test_create_character_derives_hp_and_slots(ctx):
    result = registry.execute("create_character", ctx, **FIGHTER_KWARGS)
    assert result.ok, result.refusal
    char = ctx.store.get_character("Kira")
    assert char["max_hp"] == 12  # d10 fighter, +2 CON
    result2 = registry.execute(
        "create_character", ctx, name="Brother Aldric", role="companion",
        class_slug="cleric", race_slug="hill-dwarf",
        abilities={"str": 14, "dex": 8, "con": 15, "int": 10, "wis": 15, "cha": 12},
        ac=18, proficiencies={"skills": ["medicine", "religion"], "saves": ["wis", "cha"]},
        attacks=[{"name": "mace", "ranged": False, "range_ft": 5, "long_range_ft": None,
                  "damage": "1d6", "damage_type": "bludgeoning", "ability": "str",
                  "proficient": True}],
        spells_known=["cure-wounds", "bless", "guiding-bolt", "sacred-flame"],
    )
    assert result2.ok, result2.refusal
    cleric = ctx.store.get_character("Brother Aldric")
    res = ctx.store.get_resources(cleric["id"])
    assert res["spell_slots"] == {"1": {"max": 2, "remaining": 2}}


def test_second_pc_refused(ctx):
    registry.execute("create_character", ctx, **FIGHTER_KWARGS)
    dupe = {**FIGHTER_KWARGS, "name": "Zed"}
    result = registry.execute("create_character", ctx, **dupe)
    assert result.ok is False and "pc" in result.refusal.lower()


def test_award_xp_levels_up_and_updates_sheet(ctx):
    registry.execute("create_character", ctx, **FIGHTER_KWARGS)
    result = registry.execute("award_xp", ctx, amount=300, reason="quest: rats")
    assert result.ok
    char = ctx.store.get_character("Kira")
    assert char["level"] == 2 and char["xp"] == 300
    assert char["max_hp"] == 20
    assert "level 2" in result.digest.lower() or "reaches level 2" in result.digest
    sheet = (ctx.store.root / "sheets" / "kira.md").read_text()
    assert "20" in sheet and "300" in sheet


def _cleric_kwargs(name="Aldric", role="companion"):
    return dict(
        name=name, role=role, class_slug="cleric", race_slug="hill-dwarf",
        abilities={"str": 14, "dex": 8, "con": 15, "int": 10, "wis": 15, "cha": 12},
        ac=18, proficiencies={"skills": ["medicine"], "saves": ["wis", "cha"]},
        attacks=[{"name": "mace", "ranged": False, "range_ft": 5, "long_range_ft": None,
                  "damage": "1d6", "damage_type": "bludgeoning", "ability": "str",
                  "proficient": True}],
        spells_known=["bless"],
    )


def test_create_character_mutates_and_logs(ctx):
    before = ctx.store.event_count()
    result = registry.execute("create_character", ctx, **FIGHTER_KWARGS)
    assert result.ok
    assert ctx.store.get_character("Kira") is not None  # state mutation
    assert ctx.store.event_count() == before + 1  # event append
    assert result.event_ids  # envelope carries the event id


def test_create_character_data_is_full_sheet(ctx):
    result = registry.execute("create_character", ctx, **FIGHTER_KWARGS)
    assert set(result.data) == {"character", "resources", "inventory", "markdown"}
    assert result.data["character"]["name"] == "Kira"
    assert "# Kira" in result.data["markdown"]


def test_create_character_unknown_class_refused(ctx):
    bad = {**FIGHTER_KWARGS, "class_slug": "wizardosaurus"}
    result = registry.execute("create_character", ctx, **bad)
    assert result.ok is False and "class" in result.refusal.lower()


def test_create_character_duplicate_name_refused(ctx):
    registry.execute("create_character", ctx, **FIGHTER_KWARGS)
    result = registry.execute("create_character", ctx, **FIGHTER_KWARGS)
    assert result.ok is False and "name" in result.refusal.lower()


def test_create_character_invalid_role_refused(ctx):
    bad = {**FIGHTER_KWARGS, "role": "villain"}
    result = registry.execute("create_character", ctx, **bad)
    assert result.ok is False and "role" in result.refusal.lower()


def test_create_character_bad_abilities_refused(ctx):
    missing = {**FIGHTER_KWARGS}
    missing["abilities"] = {"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12}
    result = registry.execute("create_character", ctx, **missing)
    assert result.ok is False
    out_of_range = {**FIGHTER_KWARGS, "name": "Ogre"}
    out_of_range["abilities"] = {**FIGHTER_KWARGS["abilities"], "str": 99}
    result2 = registry.execute("create_character", ctx, **out_of_range)
    assert result2.ok is False


def test_get_character_sheet_unknown_refused(ctx):
    result = registry.execute("get_character_sheet", ctx, name="Nobody")
    assert result.ok is False and "nobody" in result.refusal.lower()


def test_get_character_sheet_reads_and_logs(ctx):
    registry.execute("create_character", ctx, **FIGHTER_KWARGS)
    before = ctx.store.event_count()
    result = registry.execute("get_character_sheet", ctx, name="Kira")
    assert result.ok
    assert ctx.store.event_count() == before + 1  # event append (read-only command)
    assert set(result.data) == {"character", "resources", "inventory", "markdown"}
    assert "# Kira" in result.data["markdown"]


def test_award_xp_refused_no_party(ctx):
    result = registry.execute("award_xp", ctx, amount=100, reason="x")
    assert result.ok is False


def test_award_xp_refused_nonpositive(ctx):
    registry.execute("create_character", ctx, **FIGHTER_KWARGS)
    result = registry.execute("award_xp", ctx, amount=0, reason="x")
    assert result.ok is False


def test_award_xp_splits_floor_across_active(ctx):
    registry.execute("create_character", ctx, **FIGHTER_KWARGS)
    registry.execute("create_character", ctx, **_cleric_kwargs())
    before = ctx.store.event_count()
    result = registry.execute("award_xp", ctx, amount=301, reason="loot")
    assert result.ok
    assert result.data["per_member"] == 150  # 301 // 2
    assert ctx.store.get_character("Kira")["xp"] == 150  # state mutation
    assert ctx.store.event_count() == before + 1  # event append


def test_award_xp_tops_up_spell_slot_maxima(ctx):
    registry.execute("create_character", ctx, **_cleric_kwargs(name="Solo", role="pc"))
    cid = ctx.store.get_character("Solo")["id"]
    # spend a slot before leveling to prove remaining is preserved and topped up
    slots = ctx.store.get_resources(cid)["spell_slots"]
    slots["1"]["remaining"] = 1
    ctx.store.update_resources(cid, spell_slots=slots)
    result = registry.execute("award_xp", ctx, amount=900, reason="big")  # -> level 3
    assert result.ok
    res = ctx.store.get_resources(cid)
    # cleric L3: 4/2/x slots. Level 1 max goes 2->4 (delta 2); remaining 1 + 2 = 3.
    assert res["spell_slots"]["1"]["max"] == 4
    assert res["spell_slots"]["1"]["remaining"] == 3
    assert res["spell_slots"]["2"]["max"] == 2
    char = ctx.store.get_character("Solo")
    assert char["level"] == 3
    assert res["hit_dice_remaining"] == 3  # pool grew with level
