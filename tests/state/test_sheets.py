from dm_engine.commands import registry
from dm_engine.state.sheets import render_character_sheet


def test_sheet_renders_full_saves_skills_tools_attacks(party):
    ctx = party
    # party() already has Kira; create a rich rogue as a companion for
    # rendering (companions don't need the PC slot, and the sheet renders
    # the same regardless of role).
    registry.execute(
        "create_character", ctx, name="Sable", role="companion",
        class_slug="rogue", race_slug="wood-elf",
        abilities={"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10},
        ac=15, speed=35,
        proficiencies={"skills": ["stealth", "acrobatics", "perception"],
                       "tools": ["thieves_tools"],
                       "expertise": ["stealth", "thieves_tools"]},
        attacks=[{"weapon": "shortsword"}, {"weapon": "shortbow"}],
    )
    md = render_character_sheet(ctx.store, ctx.store.get_character("Sable")["id"], ctx.rules)

    # Saving throws: all six, proficient first with filled markers
    assert "## Saving Throws" in md
    assert "◉ DEX +6" in md and "◉ INT +2" in md
    assert "○ STR -1" in md and "○ CON +1" in md and "○ WIS +1" in md and "○ CHA +0" in md

    # Skills: all 18, expertise/proficient/plain tiers, passive perception
    assert "## Skills" in md
    assert "◉◉ Stealth +8 (expertise)" in md
    assert "◉ Acrobatics +6" in md
    assert "○ Athletics -1" in md
    assert md.count("◉") >= 6 and "Animal Handling" in md   # full 18 present
    assert "Passive Perception: 13" in md                   # 10 + (1 wis + 2 prof)

    # Tools
    assert "## Tools" in md
    assert "◉◉ thieves-tools (prof +4)" in md

    # Attacks: computed to-hit, annotations
    assert "Shortsword: +6 to hit, 1d6+4 piercing (finesse)" in md
    assert "Shortbow: +6 to hit, 1d6+4 piercing (80/320)" in md


def test_sheet_saves_section_replaces_old_proficiencies_block(party):
    ctx = party
    md = render_character_sheet(ctx.store, ctx.store.get_character("Kira")["id"], ctx.rules)
    assert "## Proficiencies" not in md
    assert "◉ STR" in md and "◉ CON" in md      # fighter's derived saves


def test_sheet_renders_core_fields(ctx):
    registry.execute(
        "create_character", ctx, name="Kira", role="pc", class_slug="fighter",
        race_slug="human",
        abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        ac=16, proficiencies={"skills": ["athletics"]},
        attacks=[{"weapon": "longsword", "name": "longsword"}],
    )
    md = render_character_sheet(ctx.store, ctx.store.get_character("Kira")["id"], ctx.rules)
    for expected in ("# Kira", "fighter", "12 / 12", "AC", "16", "longsword", "+5"):
        assert expected in md  # +5 = STR +3 and proficiency +2
    files = list((ctx.store.root / "sheets").glob("*.md"))
    assert len(files) == 1  # registry hook already materialized it


def test_sheet_renders_signed_zero_damage_mod(ctx):
    # STR 10 -> mod 0; the resolver notates "+0" and the sheet must match.
    registry.execute(
        "create_character", ctx, name="Kira", role="pc", class_slug="fighter",
        race_slug="human",
        abilities={"str": 10, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        ac=16, proficiencies={"skills": ["athletics"]},
        attacks=[{"custom": {
            "name": "Plain Fist", "ability": "str", "damage": "1d6",
            "damage_type": "bludgeoning", "ranged": False, "range_ft": 5,
        }}],
    )
    md = render_character_sheet(ctx.store, ctx.store.get_character("Kira")["id"], ctx.rules)
    assert "1d6+0 bludgeoning" in md


def test_sheet_renders_degraded_line_for_unfixable_legacy_attack(ctx):
    registry.execute(
        "create_character", ctx, name="Kira", role="pc", class_slug="fighter",
        race_slug="human",
        abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        ac=16, proficiencies={"skills": ["athletics"]},
        attacks=[{"weapon": "longsword", "name": "longsword"}],
    )
    kira = ctx.store.get_character("Kira")
    legacy_attacks = kira["attacks"] + [{
        "name": "Void Lash", "attack_bonus": 9, "damage": "6d6+4",
        "damage_type": "necrotic",
    }]
    ctx.store.update_character(kira["id"], attacks=legacy_attacks)
    ctx.store.conn.commit()

    md = render_character_sheet(ctx.store, kira["id"], ctx.rules)  # must not raise

    assert "Void Lash: (invalid legacy spec — refuses on use)" in md
    assert "longsword" in md  # the valid attack still renders normally


def _create_rogue(ctx, name="Sable", role="companion"):
    registry.execute(
        "create_character", ctx, name=name, role=role,
        class_slug="rogue", race_slug="wood-elf",
        abilities={"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10},
        ac=15, proficiencies={"skills": ["stealth"]},
        attacks=[{"weapon": "shortsword"}],
    )
    return ctx.store.get_character(name)


def test_sheet_lists_class_features_for_current_level(party):
    ctx = party
    sable = _create_rogue(ctx)
    md = render_character_sheet(ctx.store, sable["id"], ctx.rules)

    assert "## Features" in md
    assert "Sneak Attack (1d6)" in md   # dice annotated from class_specific
    assert "Thieves' Cant" in md
    assert "Cunning Action" not in md   # level 2 feature, rogue is level 1
    # one-line description (first paragraph), not the full multi-paragraph text
    sneak_line = next(li for li in md.splitlines() if "Sneak Attack" in li)
    assert "Once per turn" in sneak_line
    assert "You don't need advantage" not in sneak_line  # later paragraphs cut


def test_sheet_features_follow_level_up(party):
    ctx = party
    sable = _create_rogue(ctx)
    ctx.store.update_character(sable["id"], level=2, xp=300)
    ctx.store.conn.commit()

    md = render_character_sheet(ctx.store, sable["id"], ctx.rules)
    assert "Cunning Action" in md  # derived from class + level, no extra bookkeeping


def test_level_up_through_registry_rewrites_sheet_with_new_features(ctx):
    _create_rogue(ctx, name="Vex", role="pc")
    sheet_path = ctx.store.root / "sheets" / "vex.md"
    assert "Cunning Action" not in sheet_path.read_text()

    result = registry.execute("award_xp", ctx, amount=300, reason="heist")
    assert result.ok

    md = sheet_path.read_text()
    assert "Cunning Action" in md  # materialized sheet reflects the new level


def test_sheet_annotates_known_spells_with_metadata(party):
    ctx = party
    aldric = ctx.store.get_character("Brother Aldric")
    # add a ritual spell so every marker is exercised
    ctx.store.update_character(
        aldric["id"], spells_known=aldric["spells_known"] + ["detect-magic"]
    )
    ctx.store.conn.commit()

    md = render_character_sheet(ctx.store, aldric["id"], ctx.rules)
    assert "Sacred Flame — cantrip, V/S" in md
    assert "Cure Wounds — L1, V/S" in md
    assert "Bless — L1, V/S/M, concentration" in md
    assert "Hold Person — L2, V/S/M, concentration" in md
    assert "Detect Magic — L1, V/S, ritual, concentration" in md


def test_sheet_renders_unknown_spell_slug_bare(party):
    ctx = party
    aldric = ctx.store.get_character("Brother Aldric")
    ctx.store.update_character(
        aldric["id"], spells_known=aldric["spells_known"] + ["homebrew-hex"]
    )
    ctx.store.conn.commit()

    md = render_character_sheet(ctx.store, aldric["id"], ctx.rules)  # must not raise
    assert "- homebrew-hex" in md


def test_sheet_renders_concentration_spell_name_not_dict_repr(ctx):
    registry.execute(
        "create_character", ctx, name="Kira", role="pc", class_slug="fighter",
        race_slug="human",
        abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        ac=16, proficiencies={"skills": ["athletics"]},
        attacks=[{"weapon": "longsword", "name": "longsword"}],
    )
    kira = ctx.store.get_character("Kira")
    ctx.store.update_resources(
        kira["id"],
        concentration={"spell": "bless", "day": 1, "minutes": 480,
                        "duration": "Concentration, up to 1 minute"},
    )
    ctx.store.conn.commit()
    md = render_character_sheet(ctx.store, kira["id"], ctx.rules)
    assert "bless" in md
    assert "Concentration, up to 1 minute" in md
    assert "{'" not in md
