"""Check commands: skill checks, saving throws, and death saves.

Saving throws and skill checks only ever *log* an event; they never
mutate character state. `death_save` is the one mutator here: it writes
`resources.death_saves`, and on the extremes (nat 20 / third failure) also
hp, conditions, character status, and (if a combat is active) combat_state.
"""

from __future__ import annotations

from dm_engine.commands.combatants import (
    ambiguous_combatant_refusal,
    defeated_status,
    find_combatant,
    set_combatant_defeated,
)
from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.commands.registry import CommandContext, command
from dm_engine.models.character import SKILL_ABILITIES, normalize_slug
from dm_engine.rules.character_build import skill_modifier, tool_bonus
from dm_engine.rules.checks import (
    ability_modifier,
    combine_advantage,
    proficiency_bonus,
    resolve_check,
)
from dm_engine.rules.conditions import effects_for
from dm_engine.rules.death import DeathSaveState, apply_death_save

_ABILITIES = ("str", "dex", "con", "int", "wis", "cha")

_ABILITY_FULL_NAMES: dict[str, str] = {
    "str": "strength",
    "dex": "dexterity",
    "con": "constitution",
    "int": "intelligence",
    "wis": "wisdom",
    "cha": "charisma",
}

# TVA-24: refusals for enum-ish inputs echo the full vocabulary so recovery
# is single-shot, and obvious input variants normalize to the canonical form.
_VALID_SKILLS = ", ".join(sorted(SKILL_ABILITIES))
_VALID_ABILITIES = ", ".join(_ABILITIES)

_ABILITY_ALIASES: dict[str, str] = {full: abbr for abbr, full in _ABILITY_FULL_NAMES.items()}


def _normalize_ability(ability: str) -> str:
    """Canonical ability key: lowercased/stripped; full names collapse to it."""
    key = ability.strip().lower()
    return _ABILITY_ALIASES.get(key, key)


def _find_monster_combatant(ctx: CommandContext, character: str) -> dict | None:
    combat = ctx.store.combat()
    if not combat["active"]:
        return None
    for combatant in combat["combatants"]:
        if combatant.get("key") == character and combatant.get("kind") == "monster":
            return combatant
    return None


def _monster_skill_modifier(record, skill: str) -> int:
    """Modifier for `skill`: the SRD proficiency `value` if listed (it's the
    TOTAL modifier, not an add-on), else the raw ability modifier."""
    ability = SKILL_ABILITIES[skill]
    proficiencies = (record.model_extra or {}).get("proficiencies", [])
    for entry in proficiencies:
        index = entry.get("proficiency", {}).get("index")
        if index == f"skill-{skill}":
            return entry["value"]
    full_name = _ABILITY_FULL_NAMES[ability]
    return ability_modifier(getattr(record, full_name))


def _monster_skill_check(
    ctx: CommandContext,
    combatant: dict,
    skill: str,
    dc: int,
    advantage: bool,
    disadvantage: bool,
    player_value: int | None,
    gm_only: bool,
) -> CommandResult:
    character = combatant["key"]
    if player_value is not None:
        return refuse(
            "skill_check",
            f"{character} is a monster; only PCs report a player_value "
            "(monsters are engine-rolled)",
        )
    record = ctx.rules.get_monster(combatant["monster_slug"])
    if record is None:
        return refuse("skill_check", f"no character named {character!r}")

    modifier = _monster_skill_modifier(record, skill)
    mode = combine_advantage(advantage, disadvantage)

    check = resolve_check(ctx.roller, modifier, dc, mode, player_value=None, gm_only=gm_only)
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
    skill = normalize_slug(skill)
    char = ctx.store.get_character(character)
    if char is None:
        combatant = _find_monster_combatant(ctx, character)
        if combatant is None:
            return refuse("skill_check", f"no character named {character!r}")
        if skill not in SKILL_ABILITIES:
            return refuse(
                "skill_check", f"unknown skill {skill!r} (valid skills: {_VALID_SKILLS})"
            )
        if dc < 1:
            return refuse("skill_check", f"dc must be >= 1 (got {dc})")
        return _monster_skill_check(
            ctx, combatant, skill, dc, advantage, disadvantage, player_value, gm_only
        )
    if skill not in SKILL_ABILITIES:
        return refuse(
            "skill_check", f"unknown skill {skill!r} (valid skills: {_VALID_SKILLS})"
        )
    if dc < 1:
        return refuse("skill_check", f"dc must be >= 1 (got {dc})")
    reason = _validate_player_value(char, player_value)
    if reason:
        return refuse("skill_check", reason)

    modifier = skill_modifier(skill, char["proficiencies"], char["abilities"], char["level"])
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


@command("tool_check")
def tool_check(
    ctx: CommandContext,
    character: str,
    tool: str,
    ability: str,
    dc: int,
    advantage: bool = False,
    disadvantage: bool = False,
    player_value: int | None = None,
    gm_only: bool = False,
    **kwargs,
) -> CommandResult:
    """Tool proficiency check. Tools have no fixed ability in RAW (thieves'
    tools + DEX to pick a lock, + INT to recall trap designs), so the
    ability is an explicit argument."""
    ability = _normalize_ability(ability)
    char = ctx.store.get_character(character)
    if char is None:
        return refuse("tool_check", f"no character named {character!r}")
    if ability not in _ABILITIES:
        return refuse(
            "tool_check", f"unknown ability {ability!r} (valid abilities: {_VALID_ABILITIES})"
        )
    if dc < 1:
        return refuse("tool_check", f"dc must be >= 1 (got {dc})")
    reason = _validate_player_value(char, player_value)
    if reason:
        return refuse("tool_check", reason)

    tool_slug = normalize_slug(tool)
    modifier = ability_modifier(char["abilities"][ability]) + tool_bonus(
        tool_slug, char["proficiencies"], char["level"]
    )
    mode = combine_advantage(advantage, disadvantage)
    check = resolve_check(
        ctx.roller, modifier, dc, mode, player_value=player_value, gm_only=gm_only
    )
    data = {
        "tool": tool_slug,
        "ability": ability,
        "modifier": modifier,
        "dc": dc,
        "natural": check.d20.natural,
        "total": check.d20.total,
        "success": check.success,
        "margin": check.margin,
    }
    outcome = "success" if check.success else "failure"
    digest = (
        f"{character} {_label(tool_slug)} ({ability.upper()}) check: "
        f"{check.d20.total} vs DC {dc} — {outcome}"
    )
    return CommandResult(
        ok=True, command="tool_check", digest=digest, data=data, gm_only=gm_only
    )


def _save_result(
    character: str, ability: str, dc: int, modifier: int, *, auto_fail: bool, check, gm_only: bool
) -> CommandResult:
    """Shared data/digest/envelope shape for both the character and monster
    saving-throw paths, given either an auto-fail (no roll) or a resolved
    check."""
    if auto_fail:
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
    else:
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


def _monster_save_modifier(record, ability: str) -> int:
    """SRD save-proficiency total if listed (it's the TOTAL modifier, not an
    add-on), else the bare ability modifier."""
    proficiencies = (record.model_extra or {}).get("proficiencies", [])
    for prof in proficiencies:
        if prof.get("proficiency", {}).get("index") == f"saving-throw-{ability}":
            return int(prof["value"])
    return ability_modifier(record.ability_scores[ability])


def _monster_saving_throw(
    ctx: CommandContext,
    combatant: dict,
    ability: str,
    dc: int,
    *,
    advantage: bool,
    disadvantage: bool,
    player_value: int | None,
    gm_only: bool,
) -> CommandResult:
    character = combatant["key"]
    if ability not in _ABILITIES:
        return refuse(
            "saving_throw", f"unknown ability {ability!r} (valid abilities: {_VALID_ABILITIES})"
        )
    if dc < 1:
        return refuse("saving_throw", f"dc must be >= 1 (got {dc})")
    if player_value is not None:
        return refuse(
            "saving_throw",
            "player_value is only for the PC's own dice — "
            "monster saves always roll in the engine",
        )
    record = ctx.rules.get_monster(combatant["monster_slug"])
    if record is None:
        return refuse("saving_throw", f"no character named {character!r}")

    modifier = _monster_save_modifier(record, ability)
    effects = effects_for(combatant.get("conditions", []), 0)

    if effects.auto_fail_str_dex_saves and ability in ("str", "dex"):
        return _save_result(character, ability, dc, modifier, auto_fail=True, check=None,
                             gm_only=gm_only)

    effective_disadvantage = disadvantage or effects.saves_have_disadvantage or (
        ability == "dex" and effects.dex_saves_have_disadvantage
    )
    mode = combine_advantage(advantage, effective_disadvantage)
    check = resolve_check(ctx.roller, modifier, dc, mode, player_value=None, gm_only=gm_only)
    return _save_result(character, ability, dc, modifier, auto_fail=False, check=check,
                         gm_only=gm_only)


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
    ability = _normalize_ability(ability)
    char = ctx.store.get_character(character)
    if char is None:
        combat = ctx.store.combat()
        if combat["active"]:
            combatant, ambiguous = find_combatant(combat["combatants"], character)
            if ambiguous:
                return refuse(
                    "saving_throw", ambiguous_combatant_refusal(character, ambiguous)
                )
            if combatant is not None and combatant["kind"] == "monster":
                return _monster_saving_throw(
                    ctx, combatant, ability, dc,
                    advantage=advantage, disadvantage=disadvantage,
                    player_value=player_value, gm_only=gm_only,
                )
        return refuse("saving_throw", f"no character named {character!r}")
    if ability not in _ABILITIES:
        return refuse(
            "saving_throw", f"unknown ability {ability!r} (valid abilities: {_VALID_ABILITIES})"
        )
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
        return _save_result(character, ability, dc, modifier, auto_fail=True, check=None,
                             gm_only=gm_only)

    effective_disadvantage = disadvantage or effects.saves_have_disadvantage or (
        ability == "dex" and effects.dex_saves_have_disadvantage
    )
    mode = combine_advantage(advantage, effective_disadvantage)

    check = resolve_check(
        ctx.roller, modifier, dc, mode, player_value=player_value, gm_only=gm_only
    )
    return _save_result(character, ability, dc, modifier, auto_fail=False, check=check,
                         gm_only=gm_only)


def _mark_combatant_defeated(ctx: CommandContext, character: str) -> None:
    set_combatant_defeated(ctx, character, True)


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
        or char["status"] != "active"
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
        new_status = defeated_status(ctx)
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


@command("stabilize")
def stabilize(
    ctx: CommandContext,
    character: str,
    medicine_by: str | None = None,
    player_value: int | None = None,
    gm_only: bool = False,
    **kwargs,
) -> CommandResult:
    """Stabilize a dying character: optional Medicine check (DC 10) by
    `medicine_by`; without a checker it is DM fiat (Spare the Dying etc.)."""
    char = ctx.store.get_character(character)
    if char is None:
        return refuse("stabilize", f"no character named {character!r}")
    resources = ctx.store.get_resources(char["id"])
    ds = resources["death_saves"]
    if resources["hp"] > 0 or ds["stable"] or ds["dead"] or char["status"] != "active":
        return refuse(
            "stabilize", f"{character} is not dying (0 hp, not yet stable or dead)"
        )
    check_data = None
    if medicine_by is not None:
        result = skill_check(
            ctx, character=medicine_by, skill="medicine", dc=10,
            player_value=player_value, gm_only=gm_only,
        )
        if not result.ok:
            return refuse("stabilize", result.refusal)
        check_data = result.data
        if not check_data["success"]:
            digest = (
                f"{medicine_by} fails to stabilize {character} "
                f"(Medicine {check_data['total']} vs DC 10)"
            )
            return CommandResult(
                ok=True, command="stabilize", digest=digest,
                data={"stabilized": False, "check": check_data}, gm_only=gm_only,
            )
    ctx.store.update_resources(
        char["id"], death_saves=DeathSaveState(stable=True).model_dump()
    )
    by = f" by {medicine_by}" if medicine_by else ""
    digest = f"{character} is stabilized{by} — 0 HP, unconscious, no longer dying"
    return CommandResult(
        ok=True, command="stabilize", digest=digest,
        data={"stabilized": True, "check": check_data}, gm_only=gm_only,
    )
