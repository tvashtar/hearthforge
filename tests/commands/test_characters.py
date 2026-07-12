from dm_engine.commands import registry

FIGHTER_KWARGS = dict(
    name="Kira", role="pc", class_slug="fighter", race_slug="human",
    abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
    ac=16, proficiencies={"skills": ["athletics", "intimidation"]},
    attacks=[{"weapon": "longsword", "name": "longsword"}],
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
        ac=18, proficiencies={"skills": ["medicine", "religion"]},
        attacks=[{"weapon": "mace", "name": "mace"}],
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
        ac=18, proficiencies={"skills": ["medicine"]},
        attacks=[{"weapon": "mace", "name": "mace"}],
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


# --- character mechanics: derived attacks/saves (append) -------------------

ROGUE_KWARGS = dict(
    name="Sable", role="pc", class_slug="rogue", race_slug="wood-elf",
    abilities={"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10},
    ac=15, speed=35,
    proficiencies={"skills": ["stealth", "acrobatics"], "tools": ["thieves_tools"],
                   "expertise": ["stealth", "thieves_tools"]},
    attacks=[{"weapon": "shortsword"}, {"weapon": "dagger"}],
)


def test_create_character_derives_weapon_attacks(ctx):
    result = registry.execute("create_character", ctx, **ROGUE_KWARGS)
    assert result.ok
    char = ctx.store.get_character("Sable")
    by_name = {a["name"]: a for a in char["attacks"]}
    # dagger is thrown → two specs
    assert set(by_name) == {"Shortsword", "Dagger", "Dagger (thrown)"}
    sword = by_name["Shortsword"]
    assert (sword["ability"], sword["proficient"], sword["damage"]) == ("dex", True, "1d6")
    assert sword["source"] == "srd:shortsword"
    assert by_name["Dagger (thrown)"]["range_ft"] == 20


def test_create_character_derives_saves_from_class(ctx):
    registry.execute("create_character", ctx, **ROGUE_KWARGS)
    profs = ctx.store.get_character("Sable")["proficiencies"]
    assert profs["saves"] == ["dex", "int"]
    assert profs["expertise"] == ["stealth", "thieves-tools"]  # normalized


def test_create_character_refuses_caller_saves(ctx):
    kwargs = {**ROGUE_KWARGS,
              "proficiencies": {"skills": ["stealth"], "saves": ["cha"]}}
    result = registry.execute("create_character", ctx, **kwargs)
    assert not result.ok
    assert "derived from class" in result.refusal


def test_create_character_refuses_unknown_weapon(ctx):
    kwargs = {**ROGUE_KWARGS, "attacks": [{"weapon": "vorpal-zweihander"}]}
    result = registry.execute("create_character", ctx, **kwargs)
    assert not result.ok
    assert "vorpal-zweihander" in result.refusal


def test_create_character_refuses_unknown_skill(ctx):
    kwargs = {**ROGUE_KWARGS, "proficiencies": {"skills": ["lockpicking"]}}
    result = registry.execute("create_character", ctx, **kwargs)
    assert not result.ok
    assert "lockpicking" in result.refusal


def test_create_character_accepts_valid_custom_attack(ctx):
    kwargs = {**ROGUE_KWARGS, "attacks": [{"custom": {
        "name": "Cursed Fang", "ability": "dex", "damage": "1d6",
        "damage_type": "necrotic", "ranged": False, "range_ft": 5}}]}
    result = registry.execute("create_character", ctx, **kwargs)
    assert result.ok
    atk = ctx.store.get_character("Sable")["attacks"][0]
    assert atk["source"] == "custom"


def test_create_character_refuses_malformed_custom_attack(ctx):
    kwargs = {**ROGUE_KWARGS, "attacks": [{"custom": {
        "name": "Bad", "ability": "dex", "damage": "1d6+4",
        "damage_type": "piercing", "ranged": False, "range_ft": 5}}]}
    result = registry.execute("create_character", ctx, **kwargs)
    assert not result.ok
    assert "base dice only" in result.refusal


def test_create_character_refuses_attack_entry_without_weapon_or_custom(ctx):
    kwargs = {**ROGUE_KWARGS, "attacks": [{"name": "Shortsword", "attack_bonus": 6}]}
    result = registry.execute("create_character", ctx, **kwargs)
    assert not result.ok
    assert "'weapon' or 'custom'" in result.refusal


def test_create_character_refuses_non_list_skills(ctx):
    kwargs = {**ROGUE_KWARGS, "proficiencies": {"skills": 42}}
    result = registry.execute("create_character", ctx, **kwargs)
    assert result.ok is False


def test_create_character_refuses_non_string_skill_entries(ctx):
    kwargs = {**ROGUE_KWARGS, "proficiencies": {"skills": [42]}}
    result = registry.execute("create_character", ctx, **kwargs)
    assert result.ok is False


def test_create_character_refuses_non_string_weapon(ctx):
    kwargs = {**ROGUE_KWARGS, "attacks": [{"weapon": 42}]}
    result = registry.execute("create_character", ctx, **kwargs)
    assert result.ok is False


def test_create_character_refuses_non_dict_custom(ctx):
    kwargs = {**ROGUE_KWARGS, "attacks": [{"custom": "x"}]}
    result = registry.execute("create_character", ctx, **kwargs)
    assert result.ok is False


def test_create_character_refuses_non_string_weapon_name(ctx):
    kwargs = {**ROGUE_KWARGS, "attacks": [{"weapon": "longsword", "name": 42}]}
    result = registry.execute("create_character", ctx, **kwargs)
    assert result.ok is False


def test_create_character_refuses_non_bool_weapon_proficient(ctx):
    kwargs = {**ROGUE_KWARGS, "attacks": [{"weapon": "longsword", "proficient": 42}]}
    result = registry.execute("create_character", ctx, **kwargs)
    assert result.ok is False


# --- level param (TVA-34) ---------------------------------------------------


def test_create_character_level_derives_hp_and_slots(ctx):
    result = registry.execute(
        "create_character", ctx, name="Brother Aldric", role="companion",
        class_slug="cleric", race_slug="hill-dwarf", level=3,
        abilities={"str": 14, "dex": 8, "con": 15, "int": 10, "wis": 15, "cha": 12},
        ac=18, proficiencies={"skills": ["medicine", "religion"]},
        attacks=[{"weapon": "mace", "name": "mace"}],
        spells_known=["cure-wounds", "bless", "guiding-bolt", "sacred-flame"],
    )
    assert result.ok, result.refusal
    char = ctx.store.get_character("Brother Aldric")
    assert char["level"] == 3
    assert char["max_hp"] == 24  # d8 + CON 2 at level 1, +2 levels of (4+1+2)=7
    res = ctx.store.get_resources(char["id"])
    assert res["spell_slots"] == {
        "1": {"max": 4, "remaining": 4}, "2": {"max": 2, "remaining": 2},
    }
    assert res["hit_dice_remaining"] == 3


def test_create_character_unknown_level_refused(ctx):
    bad = {**FIGHTER_KWARGS, "level": 99}
    result = registry.execute("create_character", ctx, **bad)
    assert result.ok is False and "level" in result.refusal.lower()
    assert ctx.store.get_character("Kira") is None


def test_create_character_bad_spell_slug_refused(ctx):
    kwargs = {
        **_cleric_kwargs(),
        "spells_known": ["bless", "not-a-real-spell"],
    }
    result = registry.execute("create_character", ctx, **kwargs)
    assert result.ok is False
    assert "not-a-real-spell" in result.refusal
    assert ctx.store.get_character("Aldric") is None


def test_create_character_spell_not_castable_by_class_refused(ctx):
    # fireball is sorcerer/wizard only, not on the cleric spell list
    kwargs = {**_cleric_kwargs(), "spells_known": ["bless", "fireball"]}
    result = registry.execute("create_character", ctx, **kwargs)
    assert result.ok is False
    assert "fireball" in result.refusal
