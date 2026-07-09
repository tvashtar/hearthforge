"""E2E: weapon-derived attacks resolve in combat, expertise applies in and
out of combat, and the materialized sheet shows real-5e-sheet sections.
Exercises the engine exactly as the LLM does: registry.execute only."""

from dm_engine.commands import registry


def _run(ctx, command_name, **kwargs):
    result = registry.execute(command_name, ctx, **kwargs)
    assert result.ok, f"{command_name} refused: {result.refusal}"
    return result


def test_derived_rogue_fights_sneaks_and_picks_locks(ctx, tmp_path):
    _run(ctx, "create_character", name="Sable", role="pc",
         class_slug="rogue", race_slug="wood-elf",
         abilities={"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10},
         ac=15, speed=35,
         proficiencies={"skills": ["stealth", "perception"], "tools": ["thieves_tools"],
                        "expertise": ["stealth", "thieves_tools"]},
         attacks=[{"weapon": "shortbow"}, {"weapon": "dagger"}])

    # Sheet materialized with derived numbers
    sheet = (ctx.store.root / "sheets" / "sable.md").read_text()
    assert "Shortbow: +6 to hit, 1d6+4 piercing (80/320)" in sheet
    assert "◉ DEX +6" in sheet and "◉ INT +2" in sheet
    assert "◉◉ Stealth +8 (expertise)" in sheet

    # Expertise out of combat: stealth at +8, lockpicking at +8
    check = _run(ctx, "skill_check", character="Sable", skill="stealth",
                 dc=15, player_value=10)
    assert check.data["total"] == 18
    lock = _run(ctx, "tool_check", character="Sable", tool="thieves_tools",
                ability="dex", dc=15, player_value=11)
    assert lock.data["total"] == 19 and lock.data["success"]

    # Derived shortbow resolves in combat from `near` (engine-rolled, fixed seed)
    _run(ctx, "start_combat",
         monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
         pc_initiative=20)
    result = _run(ctx, "attack", attacker="Sable", target="goblin-1",
                  attack_name="Shortbow")
    # derived to-hit (+6) reached combat: total - natural recovers the modifier
    roll = result.data["attack_roll"]
    assert roll["total"] - roll["natural"] == 6
