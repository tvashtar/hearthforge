from dm_engine.commands import registry
from dm_engine.state.sheets import render_character_sheet


def test_sheet_renders_core_fields(ctx):
    registry.execute(
        "create_character", ctx, name="Kira", role="pc", class_slug="fighter",
        race_slug="human",
        abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        ac=16, proficiencies={"skills": ["athletics"], "saves": ["str", "con"]},
        attacks=[{"name": "longsword", "ranged": False, "range_ft": 5,
                  "long_range_ft": None, "damage": "1d8", "damage_type": "slashing",
                  "ability": "str", "proficient": True}],
    )
    md = render_character_sheet(ctx.store, ctx.store.get_character("Kira")["id"])
    for expected in ("# Kira", "fighter", "12 / 12", "AC", "16", "longsword", "+5"):
        assert expected in md  # +5 = STR +3 and proficiency +2
    files = list((ctx.store.root / "sheets").glob("*.md"))
    assert len(files) == 1  # registry hook already materialized it
