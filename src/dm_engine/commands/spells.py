"""Spell casting: tiered resolution.

Tier 1 spells carry mechanical data (`damage` with a `damage_type`, or
`heal_at_slot_level`) the engine resolves fully — a heal, a spell attack vs
AC, or a saving-throw effect. Tier 2 spells have no such data; the slot and
concentration are spent and the effect is handed to the DM via `dm_ruling`.
A `damage` block without a `damage_type` (sleep's HP pool, prismatic spray's
per-ray types) is not resolvable mechanics — those spells are Tier 2.

Ritual casting (`ritual=True`): 2014 Ritual Casting classes (bard, cleric,
druid, wizard) cast a spell with the ritual tag at base level without
spending a slot, at +10 minutes on the world clock. Impossible while combat
is active.

AoE cluster cap: `max_targets = max(1, aoe_size_ft // 5)`, capped at 8
(burning hands 15-ft cone -> 3; fireball 20-ft sphere -> 4). This is the FC-4
`data.max_targets` rule for v1: an auto-clustered area spell (empty `targets`,
a record with `area_of_effect`) hits up to that many hostile combatants in the
targeted band, in initiative order.

Damage application and concentration follow-ups reuse `attacks`' shared sink
(`apply_damage_to_target`) and helpers so spells and weapon attacks land damage
identically.

Dart spells (magic missile): the SRD record folds all darts into one notation
("3d4 + 3"), but RAW each dart is a separate 1d4+1 auto-hit (no attack roll,
no save) that can be aimed independently, and each hit triggers its own
concentration check on the receiver. `_DART_SPELLS` overrides Tier-1
resolution for these slugs: one `roll_damage` per dart through `ctx.roller`,
one `apply_damage_to_target` per dart, one `per_target` entry per dart. The
record's `damage_at_slot_level` keys still gate which slot levels are valid.
"""

from __future__ import annotations

from dm_engine.commands.attacks import (
    _effects_for_combatant,
    _monster_defense_sets,
    apply_damage_to_target,
)
from dm_engine.commands.combatants import (
    ambiguous_combatant_refusal,
    find_combatant,
    set_combatant_defeated,
    unknown_combatant_refusal,
)
from dm_engine.commands.effects import (
    clear_concentration_effects,
    effective_ac_for_combatant,
    expire_clock_effects,
)
from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.commands.registry import CommandContext, command
from dm_engine.rules.action_economy import TurnBudget
from dm_engine.rules.action_economy import spend as spend_budget
from dm_engine.rules.attacks import resolve_attack_roll, roll_damage
from dm_engine.rules.checks import (
    ability_modifier,
    combine_advantage,
    proficiency_bonus,
    resolve_check,
)
from dm_engine.rules.conditions import attack_interaction, effects_for
from dm_engine.rules.damage import apply_mitigation
from dm_engine.rules.death import DeathSaveState

SPELLCASTING_ABILITY = {
    "bard": "cha", "cleric": "wis", "druid": "wis", "paladin": "cha",
    "ranger": "wis", "sorcerer": "cha", "warlock": "cha", "wizard": "int",
}

_SPENDS = ("action", "bonus_action", "reaction", "none")
# Classes with the 2014 Ritual Casting feature (among the castable classes).
_RITUAL_CASTERS = {"bard", "cleric", "druid", "wizard"}
_ORDINALS = {
    1: "1st", 2: "2nd", 3: "3rd", 4: "4th", 5: "5th",
    6: "6th", 7: "7th", 8: "8th", 9: "9th",
}


# Auto-hit dart spells: `darts` at the base level, +`per_upcast` per slot
# level above it; each dart rolls `dart_damage` independently. No attack
# roll, no save. `player_damage_value` is ignored for these — one reported
# value cannot stand in for N independent dart rolls, so every dart is an
# engine roll through ctx.roller.
_DART_SPELLS = {
    "magic-missile": {"darts": 3, "per_upcast": 1, "dart_damage": "1d4+1"},
}


def _ordinal(n: int) -> str:
    return _ORDINALS.get(n, f"{n}th")


def _dart_count(spec: dict, base_level: int, slot_level: int | None) -> int:
    return spec["darts"] + spec["per_upcast"] * ((slot_level or base_level) - base_level)


def _find_combat_target(combatants, identifier):
    """Resolve one spell target against the live combatants — key OR display
    name, case-insensitively (TVA-38, the shared `attack` resolution) — or
    return an error string to refuse with: ambiguity lists the candidates,
    an unknown identifier lists the live roster."""
    tc, ambiguous = find_combatant(combatants, identifier)
    if ambiguous:
        return ambiguous_combatant_refusal(identifier, ambiguous)
    if tc is None:
        return unknown_combatant_refusal("target", identifier, combatants)
    return tc


def _assign_darts(combatants, targets, count, spell_name):
    """Resolve one combatant dict per dart, or an error string to refuse with.

    A single target takes every dart; otherwise the list is per-dart (one
    entry each, repeats allowed) and must match the dart count exactly.
    """
    if not targets:
        return f"{spell_name} needs a target for its darts"
    if len(targets) == 1:
        keys = [targets[0]] * count
    elif len(targets) == count:
        keys = list(targets)
    else:
        return (
            f"{spell_name} fires {count} darts — give one target or exactly "
            f"{count} (one per dart), not {len(targets)}"
        )
    chosen = []
    for key in keys:
        tc = _find_combat_target(combatants, key)
        if isinstance(tc, str):
            return tc
        chosen.append(tc)
    return chosen


def _heal_target_cid(ctx: CommandContext, key: str) -> int | None:
    """The character id a heal on `key` would land on, or None if no such
    character exists. Prefers a matching character combatant (by key), else a
    party character by name; mirrors `_apply_healing`'s resolution so callers
    can validate a heal target before consuming any state."""
    combat = ctx.store.combat()
    if combat["active"]:
        characters = [c for c in combat["combatants"] if c["kind"] == "character"]
        combatant, ambiguous = find_combatant(characters, key)
        if combatant is not None and ambiguous is None:
            return combatant["character_id"]
    char = ctx.store.get_character(key)
    return char["id"] if char is not None else None


def _heal_notation(entry: str, mod: int) -> str:
    """Substitute the ability MOD into a heal notation ("1d8 + MOD").

    Post-processes the "+-" that a negative modifier would otherwise produce
    (e.g. "1d8+-2") into a clean "1d8-2".
    """
    return entry.replace(" ", "").replace("MOD", str(mod)).replace("+-", "-")


def _apply_healing(ctx: CommandContext, key: str, amount: int) -> dict | None:
    """Heal `amount` to a character (by combatant key or name), capped at max.

    Healing a character at 0 HP revives it: fresh death saves, `unconscious`
    dropped, HP set to the healed amount (capped). If the kill already
    resolved (TVA-51's dying path stamps `characters.status` "defeated"),
    revival also clears that status back to "active" and un-defeats the
    combatant tracker entry so the character can act again this combat
    (TVA-52). A hardcore "dead" status is not touched here — callers must
    refuse a dead target before calling this (see `cast_spell` step 4).
    Returns a per-target fragment (`healed` is the rolled amount, `hp` the
    resulting total) or None if no such character exists.
    """
    cid = _heal_target_cid(ctx, key)
    if cid is None:
        return None

    char_row = ctx.store.get_character_by_id(cid)
    res = ctx.store.get_resources(cid)
    max_hp = char_row["max_hp"]
    hp_before = res["hp"]

    if hp_before == 0:
        conditions = [c for c in res["conditions"] if c != "unconscious"]
        new_hp = min(max_hp, amount)
        ctx.store.update_resources(
            cid, hp=new_hp, conditions=conditions,
            death_saves=DeathSaveState().model_dump(),
        )
        if char_row["status"] == "defeated":
            ctx.store.update_character(cid, status="active")
        set_combatant_defeated(ctx, char_row["name"], False)
    else:
        new_hp = min(max_hp, hp_before + amount)
        ctx.store.update_resources(cid, hp=new_hp)
    # Echo the canonical character name: `key` may have been a differently-
    # cased alias (TVA-38's forgiving resolution).
    return {"key": char_row["name"], "healed": amount, "hp": new_hp}


def _save_modifier(ctx: CommandContext, combatant: dict, ability: str) -> int:
    """The saving-throw modifier for a combatant against `ability`.

    Monsters use the stat-block save bonus when they are proficient in that
    save (the upstream `value` already folds in proficiency), else the raw
    ability modifier. Characters add their proficiency bonus when the save is
    in their proficiency list.
    """
    if combatant["kind"] == "monster":
        record = ctx.rules.get_monster(combatant["monster_slug"])
        index = f"saving-throw-{ability}"
        for entry in (record.model_extra or {}).get("proficiencies", []):
            if entry.get("proficiency", {}).get("index") == index:
                return int(entry["value"])
        return ability_modifier(record.ability_scores[ability])
    char = ctx.store.get_character_by_id(combatant["character_id"])
    mod = ability_modifier(char["abilities"][ability])
    if ability in char["proficiencies"].get("saves", []):
        mod += proficiency_bonus(char["level"])
    return mod


def _mitigate(ctx: CommandContext, combatant: dict, raw: int, damage_type: str) -> int:
    """Final damage to one combatant after its resistances/immunities."""
    if combatant["kind"] == "monster":
        record = ctx.rules.get_monster(combatant["monster_slug"])
        resistances, vulnerabilities, immunities = _monster_defense_sets(
            record, damage_type
        )
    else:
        res = ctx.store.get_resources(combatant["character_id"])
        petrified = effects_for(
            res["conditions"], res.get("exhaustion", 0)
        ).resist_all_damage
        resistances = {damage_type} if petrified else set()
        vulnerabilities = set()
        immunities = set()
    return apply_mitigation(
        raw, damage_type, resistances=resistances,
        vulnerabilities=vulnerabilities, immunities=immunities,
    ).final


@command("cast_spell")
def cast_spell(
    ctx: CommandContext,
    caster: str,
    spell_slug: str,
    slot_level: int | None = None,
    targets: list[str] = [],  # noqa: B006 (frozen contract; never mutated)
    band: str | None = None,
    spend: str = "action",
    ritual: bool = False,
    player_attack_value: int | None = None,
    player_damage_value: int | None = None,
    player_save_values: dict[str, int] | None = None,
    **kwargs,
) -> CommandResult:
    # Step 1: existence and knowledge.
    char = ctx.store.get_character(caster)
    if char is None:
        return refuse("cast_spell", f"no character named {caster!r}")
    record = ctx.rules.get_spell(spell_slug)
    if record is None:
        return refuse("cast_spell", f"unknown spell {spell_slug!r}")
    if spell_slug not in char["spells_known"]:
        known = ", ".join(sorted(char["spells_known"])) if char["spells_known"] else "none"
        return refuse(
            "cast_spell",
            f"{caster} does not know {record.name} (knows: {known}) — add "
            "spells at character creation, or adjudicate the effect via dm_ruling",
        )

    is_pc = char["role"] == "pc"
    cid = char["id"]
    ability = SPELLCASTING_ABILITY.get(char["class_slug"])
    if ability is None:
        return refuse("cast_spell", f"{char['class_slug']} is not a spellcasting class")
    abil_mod = ability_modifier(char["abilities"][ability])
    prof = proficiency_bonus(char["level"])
    is_cantrip = record.level == 0

    # Step 2: ritual validation (2014 Ritual Casting: no slot, +10 minutes,
    # base level only) — or slot validation (cantrips ignore slots).
    if ritual:
        if not record.ritual:
            return refuse("cast_spell", f"{record.name} is not a ritual")
        if char["class_slug"] not in _RITUAL_CASTERS:
            return refuse(
                "cast_spell",
                f"{char['class_slug']} has no Ritual Casting feature",
            )
        if ctx.store.combat()["active"]:
            return refuse(
                "cast_spell",
                "a ritual takes 10 extra minutes to cast — impossible in combat",
            )
    res = ctx.store.get_resources(cid)
    slots = res["spell_slots"]
    if is_cantrip or ritual:
        slot_level = None
    else:
        slot_level = slot_level or record.level
        if slot_level < record.level:
            return refuse(
                "cast_spell",
                f"{record.name} needs at least a {_ordinal(record.level)}-level slot",
            )
        key = str(slot_level)
        slot = slots.get(key)
        if slot is None or slot["remaining"] <= 0:
            return refuse(
                "cast_spell",
                f"{caster} has no {_ordinal(slot_level)}-level slots remaining",
            )

    # Step 3: action economy (only while combat is active).
    combat = ctx.store.combat()
    caster_combatant = None
    committed_budget = None
    use_reaction_flag = False
    if combat["active"]:
        combatants = combat["combatants"]
        caster_combatant = next((c for c in combatants if c["key"] == caster), None)
        if spend not in _SPENDS:
            return refuse("cast_spell", f"invalid spend {spend!r}")
        if caster_combatant is not None and spend != "none":
            idx = combat["turn_index"]
            is_turn = (
                0 <= idx < len(combatants) and combatants[idx]["key"] == caster
            )
            budget = (
                TurnBudget(**caster_combatant["budget"])
                if caster_combatant["budget"] else None
            )
            if spend in ("action", "bonus_action"):
                if not is_turn:
                    return refuse(
                        "cast_spell",
                        f"it is not {caster}'s turn ({spend} requires your turn)",
                    )
                result = spend_budget(budget, spend) if budget else None
                if result is None or not result.ok:
                    return refuse(
                        "cast_spell", f"{caster} has no {spend} remaining this turn"
                    )
                committed_budget = result.budget
            elif spend == "reaction":
                if is_turn:
                    result = spend_budget(budget, "reaction") if budget else None
                    if result is None or not result.ok:
                        return refuse(
                            "cast_spell", f"{caster} has no reaction remaining this turn"
                        )
                    committed_budget = result.budget
                elif caster_combatant["reaction_used"]:
                    return refuse(
                        "cast_spell", f"{caster} has already used its reaction this round"
                    )
                else:
                    use_reaction_flag = True

    # Step 4: validate the full effect resolution BEFORE consuming any state.
    # The registry commits refusals (only exceptions roll back), so every
    # refusal-producing check must run before the slot/concentration/economy
    # writes below — otherwise a refusal here would commit a lost slot and
    # stale concentration. Tier 2 has nothing further to validate.
    extra = record.model_extra or {}
    combatants = combat["combatants"] if combat["active"] else []
    if "heal_at_slot_level" in extra:
        if not targets:
            return refuse("cast_spell", f"{record.name} needs a target to heal")
        if extra["heal_at_slot_level"].get(str(slot_level)) is None:
            return refuse("cast_spell", f"{record.name} has no healing at that slot")
        heal_cid = _heal_target_cid(ctx, targets[0])
        if heal_cid is None:
            return refuse("cast_spell", f"no character named {targets[0]!r} to heal")
        target_row = ctx.store.get_character_by_id(heal_cid)
        if target_row["status"] == "dead":
            return refuse("cast_spell", f"{target_row['name']} is dead")
    elif _is_tier1_damage(extra):
        if _damage_notation(extra, slot_level, char["level"]) is None:
            return refuse("cast_spell", f"{record.name} has no damage at that level")
        dart_spec = _DART_SPELLS.get(spell_slug)
        if dart_spec is not None:
            count = _dart_count(dart_spec, record.level, slot_level)
            resolved = _assign_darts(combatants, targets, count, record.name)
            if isinstance(resolved, str):
                return refuse("cast_spell", resolved)
        elif extra.get("attack_type"):
            if not targets:
                return refuse("cast_spell", f"{record.name} needs a target")
            resolved = _find_combat_target(combatants, targets[0])
            if isinstance(resolved, str):
                return refuse("cast_spell", resolved)
        else:
            resolved = _select_save_targets(combatants, targets, band, extra)
            if isinstance(resolved, str):
                return refuse("cast_spell", resolved)

    # Step 5: consume the slot (validation passed; nothing below refuses).
    # A ritual spends time instead: +10 minutes on the world clock, advanced
    # before concentration stamps its start time.
    if ritual:
        clock = ctx.store.world_clock()
        day_overflow, minutes = divmod(clock["minutes"] + 10, 1440)
        ctx.store.update_world_clock(
            day=clock["day"] + day_overflow, minutes=minutes
        )
        expire_clock_effects(ctx)
    elif not is_cantrip:
        slots[str(slot_level)]["remaining"] -= 1
        ctx.store.update_resources(cid, spell_slots=slots)

    # Step 6: concentration (replaces any current one).
    concentration_replaced = None
    if record.concentration:
        existing = ctx.store.get_resources(cid)["concentration"]
        if existing is not None:
            concentration_replaced = existing.get("spell")
            # Effects the old spell was sustaining end with it.
            clear_concentration_effects(ctx, cid)
        clock = ctx.store.world_clock()
        ctx.store.update_resources(cid, concentration={
            "spell": spell_slug, "day": clock["day"], "minutes": clock["minutes"],
            "duration": record.duration,
        })

    # Commit the economy spend now that the cast is going ahead.
    if caster_combatant is not None and (committed_budget or use_reaction_flag):
        combatants = ctx.store.combat()["combatants"]
        for c in combatants:
            if c["key"] == caster:
                if committed_budget is not None:
                    c["budget"] = committed_budget.model_dump()
                if use_reaction_flag:
                    c["reaction_used"] = True
        ctx.store.update_combat(combatants=combatants)

    base_data = {"slot_used": slot_level}
    if ritual:
        base_data["ritual"] = True
    if concentration_replaced is not None:
        base_data["concentration_replaced"] = concentration_replaced

    # Step 7: Tier 1 — heal (roll + apply; targets already validated).
    if "heal_at_slot_level" in extra:
        return _resolve_heal(
            ctx, caster, record, extra, slot_level, targets, abil_mod, is_pc,
            player_damage_value, base_data,
        )

    # Step 7: Tier 1 — damage (spell attack or saving throw).
    if _is_tier1_damage(extra):
        return _resolve_damage(
            ctx, caster, record, extra, slot_level, targets, band, ability,
            abil_mod, prof, char["level"], is_pc, is_cantrip,
            player_attack_value, player_damage_value, player_save_values, base_data,
        )

    # Step 8: Tier 2 — no mechanical effect; hand to the DM.
    slot_note = (
        f" ({_ordinal(slot_level)}-level slot)" if slot_level
        else (" (ritual, no slot)" if ritual else " (cantrip)")
    )
    data = {
        **base_data, "tier": 2, "needs_ruling": True,
        "spell_text": record.desc, "duration": record.duration,
    }
    digest = f"{record.name} cast{slot_note} — resolve effect via dm_ruling"
    return CommandResult(ok=True, command="cast_spell", digest=digest, data=data)


def _resolve_heal(
    ctx, caster, record, extra, slot_level, targets, abil_mod, is_pc,
    player_damage_value, base_data,
) -> CommandResult:
    if not targets:
        return refuse("cast_spell", f"{record.name} needs a target to heal")
    entry = extra["heal_at_slot_level"].get(str(slot_level))
    if entry is None:
        return refuse("cast_spell", f"{record.name} has no healing at that slot")
    notation = _heal_notation(entry, abil_mod)
    pv = player_damage_value if is_pc else None
    roll = roll_damage(ctx.roller, notation, player_value=pv)
    frag = _apply_healing(ctx, targets[0], roll.total)
    if frag is None:
        return refuse("cast_spell", f"no character named {targets[0]!r} to heal")
    data = {**base_data, "tier": 1, "effect": "heal", "per_target": [frag]}
    digest = (
        f"{caster} casts {record.name} — {targets[0]} healed for "
        f"{frag['healed']} (hp {frag['hp']})"
    )
    return CommandResult(ok=True, command="cast_spell", digest=digest, data=data)


def _is_tier1_damage(extra: dict) -> bool:
    """Whether a spell's damage block is engine-resolvable (Tier 1).

    Requires a `damage_type`: sleep (HP pool) and prismatic spray (per-ray
    types) carry `damage` without one and must fall through to Tier 2.
    """
    return "damage" in extra and "damage_type" in extra["damage"]


def _damage_notation(
    extra: dict, slot_level: int | None, caster_level: int
) -> str | None:
    damage = extra["damage"]
    if "damage_at_slot_level" in damage:
        return damage["damage_at_slot_level"].get(str(slot_level))
    if "damage_at_character_level" in damage:
        tiers = damage["damage_at_character_level"]
        applicable = [int(k) for k in tiers if int(k) <= caster_level]
        if not applicable:
            return None
        return tiers[str(max(applicable))]
    return None


def _resolve_damage(
    ctx, caster, record, extra, slot_level, targets, band, ability, abil_mod,
    prof, caster_level, is_pc, is_cantrip, player_attack_value,
    player_damage_value, player_save_values, base_data,
) -> CommandResult:
    notation = _damage_notation(extra, slot_level, caster_level)
    if notation is None:
        return refuse("cast_spell", f"{record.name} has no damage at that level")
    damage_type = extra["damage"]["damage_type"]["index"]
    combat = ctx.store.combat()
    combatants = combat["combatants"] if combat["active"] else []

    # -- auto-hit darts (magic missile) --------------------------------
    # No attack roll and no save: every dart lands. One roll, one damage
    # application, and one per_target entry per dart, so the shared damage
    # sink raises a concentration check per dart on the receiving side.
    spec = _DART_SPELLS.get(record.slug)
    if spec is not None:
        count = _dart_count(spec, record.level, slot_level)
        resolved = _assign_darts(combatants, targets, count, record.name)
        if isinstance(resolved, str):
            # Unreachable when called via cast_spell (step 4 validated this
            # before the slot was spent); kept as a defensive refusal.
            return refuse("cast_spell", resolved)
        per_target = []
        total = 0
        for i, tc in enumerate(resolved, start=1):
            dmg = roll_damage(ctx.roller, spec["dart_damage"])
            final = _mitigate(ctx, tc, dmg.total, damage_type)
            frag = apply_damage_to_target(
                ctx, tc["key"], final, damage_type, critical=False
            )
            entry = {
                "key": tc["key"], "dart": i, "hit": True,
                "damage_rolled": dmg.total, "damage": final, **frag["target"],
            }
            _copy_concentration_flags(frag, entry)
            per_target.append(entry)
            total += final
        data = {
            **base_data, "tier": 1, "effect": "damage", "darts": count,
            "per_target": per_target,
        }
        hit_keys = ", ".join(dict.fromkeys(t["key"] for t in resolved))
        digest = (
            f"{caster} casts {record.name} — {count} darts hit {hit_keys} "
            f"for {total} {damage_type} total"
        )
        return CommandResult(ok=True, command="cast_spell", digest=digest, data=data)

    # -- spell attack --------------------------------------------------
    if extra.get("attack_type"):
        if not targets:
            return refuse("cast_spell", f"{record.name} needs a target")
        tgt = _find_combat_target(combatants, targets[0])
        if isinstance(tgt, str):
            return refuse("cast_spell", tgt)
        # Canonical key: `targets[0]` may have been a display name.
        target_key = tgt["key"]
        pv = player_attack_value if is_pc else None
        caster_c = next((c for c in combatants if c["key"] == caster), None)
        if caster_c is not None:
            interaction = attack_interaction(
                _effects_for_combatant(ctx, caster_c),
                _effects_for_combatant(ctx, tgt),
                engaged=target_key in caster_c["engaged_with"],
            )
            mode = combine_advantage(
                interaction.mode == "advantage", interaction.mode == "disadvantage"
            )
        else:
            mode = "normal"
        target_ac = effective_ac_for_combatant(ctx, tgt)
        roll = resolve_attack_roll(
            ctx.roller, prof + abil_mod, target_ac, mode, player_value=pv
        )
        data = {
            **base_data, "tier": 1, "effect": "damage",
            "attack_roll": {
                "natural": roll.d20.natural, "total": roll.d20.total,
                "mode": roll.d20.mode, "target_ac": target_ac,
            },
        }
        if not roll.hit:
            data["per_target"] = [{"key": target_key, "hit": False, "damage": 0}]
            digest = (
                f"{caster} casts {record.name} but misses {target_key} "
                f"({roll.d20.total} vs AC {target_ac})"
            )
            return CommandResult(ok=True, command="cast_spell", digest=digest, data=data)
        pdv = player_damage_value if is_pc else None
        dmg = roll_damage(
            ctx.roller, notation, critical=roll.critical_hit, player_value=pdv
        )
        final = _mitigate(ctx, tgt, dmg.total, damage_type)
        frag = apply_damage_to_target(
            ctx, target_key, final, damage_type, critical=roll.critical_hit
        )
        entry = {
            "key": target_key, "hit": True, "critical": roll.critical_hit,
            "damage_rolled": dmg.total, "damage": final, **frag["target"],
        }
        data["per_target"] = [entry]
        _copy_concentration_flags(frag, data)
        verb = "crits" if roll.critical_hit else "hits"
        digest = (
            f"{caster} casts {record.name} and {verb} {target_key} for "
            f"{final} {damage_type}"
        )
        return CommandResult(ok=True, command="cast_spell", digest=digest, data=data)

    # -- saving-throw damage ------------------------------------------
    dc_info = extra.get("dc", {})
    save_ability = dc_info.get("dc_type", {}).get("index", "dex")
    dc_success = dc_info.get("dc_success", "none")
    dc = 8 + prof + abil_mod

    resolved = _select_save_targets(combatants, targets, band, extra)
    if isinstance(resolved, str):
        return refuse("cast_spell", resolved)

    player_save_values = player_save_values or {}
    dmg = roll_damage(ctx.roller, notation)
    rolled = dmg.total
    per_target: list[dict] = []
    for tc in resolved:
        if tc["kind"] == "character":
            char = ctx.store.get_character_by_id(tc["character_id"])
            pv = player_save_values.get(char["name"]) if char["role"] == "pc" else None
        else:
            pv = None
        check = resolve_check(
            ctx.roller, _save_modifier(ctx, tc, save_ability), dc, player_value=pv
        )
        if check.success:
            base = rolled // 2 if dc_success == "half" else 0
        else:
            base = rolled
        final = _mitigate(ctx, tc, base, damage_type)
        frag = apply_damage_to_target(
            ctx, tc["key"], final, damage_type, critical=False
        )
        entry = {
            "key": tc["key"],
            "save": {"dc": dc, "total": check.d20.total, "success": check.success},
            "damage_rolled": rolled, "damage": final, **frag["target"],
        }
        _copy_concentration_flags(frag, entry)
        per_target.append(entry)

    data = {**base_data, "tier": 1, "effect": "damage", "per_target": per_target}
    digest = (
        f"{caster} casts {record.name} — {len(per_target)} caught in the "
        f"{damage_type} (DC {dc})"
    )
    return CommandResult(ok=True, command="cast_spell", digest=digest, data=data)


def _select_save_targets(combatants, targets, band, extra):
    """Resolve the combatant dicts a save-damage spell affects.

    Explicit `targets` must all be combatants in the stated `band` and fit
    under the AoE cap. Empty `targets` on an area spell auto-clusters the
    hostile (monster) combatants standing in `band`. Returns a list of
    combatant dicts, or an error string for the caller to refuse with.
    """
    aoe = extra.get("area_of_effect")
    max_targets = min(8, max(1, (aoe["size"] // 5))) if aoe else 1

    if targets:
        chosen = []
        for key in targets:
            tc = _find_combat_target(combatants, key)
            if isinstance(tc, str):
                return tc
            if band is not None and tc["band"] != band:
                return f"{tc['key']} is not in band {band!r}"
            chosen.append(tc)
        if len(chosen) > max_targets:
            return f"too many targets ({len(chosen)} > {max_targets})"
        return chosen

    if aoe is None:
        return "no targets given and this spell has no area of effect"
    if band is None:
        return "an area spell needs a band to target"
    # Deliberately narrower than bands.aoe_targets: an auto-clustered area
    # spell hits only hostile (monster) combatants in the band, never the
    # party or downed foes. An empty cluster is a refusal, not a no-op cast.
    hostiles = [
        c for c in combatants
        if c["kind"] == "monster" and not c["defeated"] and c["band"] == band
    ]
    chosen = hostiles[:max_targets]
    if not chosen:
        return f"no valid targets at {band}"
    return chosen


def _copy_concentration_flags(frag: dict, target_dict: dict) -> None:
    if "concentration_check" in frag:
        target_dict["concentration_check"] = frag["concentration_check"]
    if frag.get("concentration_broken"):
        target_dict["concentration_broken"] = True
