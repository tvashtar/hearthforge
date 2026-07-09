"""Check commands: skill checks, saving throws, and death saves.

Saving throws and skill checks only ever *log* an event; they never
mutate character state. `death_save` is the one mutator here: it writes
`resources.death_saves`, and on the extremes (nat 20 / third failure) also
hp, conditions, character status, and (if a combat is active) combat_state.
"""

from __future__ import annotations

from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.commands.registry import CommandContext, command
from dm_engine.rules.checks import (
    ability_modifier,
    combine_advantage,
    proficiency_bonus,
    resolve_check,
)
from dm_engine.rules.conditions import effects_for
from dm_engine.rules.death import DeathSaveState, apply_death_save

SKILL_ABILITIES: dict[str, str] = {
    "acrobatics": "dex",
    "animal-handling": "wis",
    "arcana": "int",
    "athletics": "str",
    "deception": "cha",
    "history": "int",
    "insight": "wis",
    "intimidation": "cha",
    "investigation": "int",
    "medicine": "wis",
    "nature": "int",
    "perception": "wis",
    "performance": "cha",
    "persuasion": "cha",
    "religion": "int",
    "sleight-of-hand": "dex",
    "stealth": "dex",
    "survival": "wis",
}

_ABILITIES = ("str", "dex", "con", "int", "wis", "cha")


def _label(slug: str) -> str:
    return slug.replace("-", " ").title()


def _validate_player_value(character: dict, player_value: int | None) -> str | None:
    """Returns a refusal reason, or None if player_value is acceptable."""
    if player_value is None:
        return None
    if not 1 <= player_value <= 20:
        return f"player_value must be between 1 and 20 (got {player_value})"
    if character["role"] != "pc":
        return (
            f"{character['name']} is not a PC; only PCs report a player_value "
            "(companions are engine-rolled)"
        )
    return None


@command("skill_check")
def skill_check(
    ctx: CommandContext,
    character: str,
    skill: str,
    dc: int,
    advantage: bool = False,
    disadvantage: bool = False,
    player_value: int | None = None,
    gm_only: bool = False,
    **kwargs,
) -> CommandResult:
    char = ctx.store.get_character(character)
    if char is None:
        return refuse("skill_check", f"no character named {character!r}")
    if skill not in SKILL_ABILITIES:
        return refuse("skill_check", f"unknown skill {skill!r}")
    if dc < 1:
        return refuse("skill_check", f"dc must be >= 1 (got {dc})")
    reason = _validate_player_value(char, player_value)
    if reason:
        return refuse("skill_check", reason)

    ability = SKILL_ABILITIES[skill]
    modifier = ability_modifier(char["abilities"][ability])
    if skill in char["proficiencies"].get("skills", []):
        modifier += proficiency_bonus(char["level"])
    mode = combine_advantage(advantage, disadvantage)

    check = resolve_check(
        ctx.roller, modifier, dc, mode, player_value=player_value, gm_only=gm_only
    )
    data = {
        "skill": skill,
        "modifier": modifier,
        "dc": dc,
        "natural": check.d20.natural,
        "total": check.d20.total,
        "success": check.success,
        "margin": check.margin,
    }
    outcome = "success" if check.success else "failure"
    digest = f"{character} {_label(skill)} check: {check.d20.total} vs DC {dc} — {outcome}"
    return CommandResult(
        ok=True, command="skill_check", digest=digest, data=data, gm_only=gm_only
    )


@command("saving_throw")
def saving_throw(
    ctx: CommandContext,
    character: str,
    ability: str,
    dc: int,
    advantage: bool = False,
    disadvantage: bool = False,
    player_value: int | None = None,
    gm_only: bool = False,
    **kwargs,
) -> CommandResult:
    char = ctx.store.get_character(character)
    if char is None:
        return refuse("saving_throw", f"no character named {character!r}")
    if ability not in _ABILITIES:
        return refuse("saving_throw", f"unknown ability {ability!r}")
    if dc < 1:
        return refuse("saving_throw", f"dc must be >= 1 (got {dc})")
    reason = _validate_player_value(char, player_value)
    if reason:
        return refuse("saving_throw", reason)

    modifier = ability_modifier(char["abilities"][ability])
    if ability in char["proficiencies"].get("saves", []):
        modifier += proficiency_bonus(char["level"])

    resources = ctx.store.get_resources(char["id"])
    effects = effects_for(resources["conditions"], resources.get("exhaustion", 0))

    if effects.auto_fail_str_dex_saves and ability in ("str", "dex"):
        data = {
            "ability": ability,
            "modifier": modifier,
            "dc": dc,
            "natural": None,
            "total": None,
            "success": False,
            "margin": None,
            "auto_fail": True,
        }
        digest = f"{character} {ability.upper()} save: automatic failure (condition)"
        return CommandResult(
            ok=True, command="saving_throw", digest=digest, data=data, gm_only=gm_only
        )

    effective_disadvantage = disadvantage or effects.saves_have_disadvantage or (
        ability == "dex" and effects.dex_saves_have_disadvantage
    )
    mode = combine_advantage(advantage, effective_disadvantage)

    check = resolve_check(
        ctx.roller, modifier, dc, mode, player_value=player_value, gm_only=gm_only
    )
    data = {
        "ability": ability,
        "modifier": modifier,
        "dc": dc,
        "natural": check.d20.natural,
        "total": check.d20.total,
        "success": check.success,
        "margin": check.margin,
        "auto_fail": False,
    }
    outcome = "success" if check.success else "failure"
    digest = f"{character} {ability.upper()} save: {check.d20.total} vs DC {dc} — {outcome}"
    return CommandResult(
        ok=True, command="saving_throw", digest=digest, data=data, gm_only=gm_only
    )


def _mark_combatant_defeated(ctx: CommandContext, character: str) -> None:
    combat = ctx.store.combat()
    if not combat["active"]:
        return
    combatants = combat["combatants"]
    changed = False
    for combatant in combatants:
        if combatant.get("name") == character:
            combatant["defeated"] = True
            changed = True
    if changed:
        ctx.store.update_combat(combatants=combatants)


@command("death_save")
def death_save(
    ctx: CommandContext, character: str, player_value: int | None = None, **kwargs,
) -> CommandResult:
    char = ctx.store.get_character(character)
    if char is None:
        return refuse("death_save", f"no character named {character!r}")
    resources = ctx.store.get_resources(char["id"])
    death_saves = resources["death_saves"]
    if (
        resources["hp"] > 0
        or death_saves["stable"]
        or death_saves["dead"]
        or char["status"] in ("defeated", "dead")
    ):
        return refuse(
            "death_save", f"{character} is not dying (0 hp, not yet stable or dead)"
        )
    reason = _validate_player_value(char, player_value)
    if reason:
        return refuse("death_save", reason)

    if player_value is not None:
        natural = player_value
    else:
        roll = ctx.roller.roll("1d20")
        natural = roll.rolls[0]

    state = DeathSaveState(**death_saves)
    outcome = apply_death_save(state, natural)
    ctx.store.update_resources(char["id"], death_saves=outcome.state.model_dump())

    status_note = ""
    if outcome.regained_hp:
        conditions = [c for c in resources["conditions"] if c != "unconscious"]
        ctx.store.update_resources(char["id"], hp=1, conditions=conditions)
    elif outcome.state.dead:
        death_mode = ctx.store.campaign_meta()["death_mode"]
        new_status = "dead" if death_mode == "hardcore" else "defeated"
        ctx.store.update_character(char["id"], status=new_status)
        _mark_combatant_defeated(ctx, character)
        status_note = f" — {character} is {new_status}"

    data = {
        **outcome.state.model_dump(),
        "event": outcome.event,
        "natural": natural,
        "regained_hp": outcome.regained_hp,
    }
    digest = f"{character} death save: natural {natural} ({outcome.event}){status_note}"
    return CommandResult(ok=True, command="death_save", digest=digest, data=data)
