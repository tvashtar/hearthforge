"""Attack resolution and condition management.

`attack` runs the frozen 9-step validation/resolution order (see the task
brief): existence and turn/economy checks, spec resolution, range legality,
advantage math, the attack and damage rolls, then damage application. All
dice come from the recording roller so every roll is logged; monster rolls
are public (`gm_only=False`) once they happen. PC attack/damage totals may be
player-supplied.

`apply_damage_to_target` is the reusable damage sink (Task 9 spells reuse it):
it handles monster hp/defeat, character hp/dying/instant-death, and the
concentration follow-up. Conditions live in `apply_condition` /
`remove_condition`; `break_concentration` (and the shared `_break_concentration`
helper) clears a character's concentration.
"""

from __future__ import annotations

import re

from pydantic import ValidationError

from dm_engine.commands.effects import (
    clear_concentration_effects,
    effective_ac_for_combatant,
)
from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.commands.registry import CommandContext, command
from dm_engine.models.character import AttackSpec
from dm_engine.rules.action_economy import TurnBudget
from dm_engine.rules.action_economy import spend as spend_budget
from dm_engine.rules.attacks import resolve_attack_roll, roll_damage
from dm_engine.rules.bands import distance_band, weapon_range_legality
from dm_engine.rules.character_build import attack_damage_mod, attack_to_hit
from dm_engine.rules.checks import combine_advantage
from dm_engine.rules.concentration import concentration_save_dc
from dm_engine.rules.conditions import CONDITIONS, attack_interaction, effects_for
from dm_engine.rules.damage import apply_mitigation, defense_entry_applies
from dm_engine.rules.death import DeathSaveState, apply_damage_while_dying

_SPENDS = ("action", "reaction", "none")
_RANGE_RE = re.compile(r"range (\d+)/(\d+) ft")
_REACH_RE = re.compile(r"reach (\d+) ft")
_HIT_TEXT_RE = re.compile(r"\bHit:\s*", re.IGNORECASE)
_ESCAPE_DC_RE = re.compile(r"escape DC (\d+)", re.IGNORECASE)
_MAX_SWINGS = 10

# TVA-24: unknown-condition refusals echo the vocabulary for single-shot recovery.
_VALID_CONDITIONS = ", ".join(sorted(CONDITIONS))


# -- shared helpers -------------------------------------------------------


def _effects_for_combatant(ctx: CommandContext, combatant: dict):
    """Fold one combatant's active conditions into a flag set."""
    if combatant["kind"] == "character":
        res = ctx.store.get_resources(combatant["character_id"])
        return effects_for(res["conditions"], res.get("exhaustion", 0))
    return effects_for(combatant["conditions"])


def _monster_defense_sets(record, damage_type: str, *, is_magical: bool = False):
    """Resistance/vulnerability/immunity sets for a damage type against a
    monster record. Entries may be compound phrases (e.g. 'bludgeoning,
    piercing, and slashing from nonmagical weapons') — caveat matching,
    including the nonmagical-weapon bypass for magical attacks, lives in
    `rules.damage.defense_entry_applies`."""
    extra = record.model_extra or {}

    def matches(field: str) -> bool:
        return any(
            defense_entry_applies(entry, damage_type, is_magical=is_magical)
            for entry in extra.get(field, [])
        )

    return (
        {damage_type} if matches("damage_resistances") else set(),
        {damage_type} if matches("damage_vulnerabilities") else set(),
        {damage_type} if matches("damage_immunities") else set(),
    )


def _monster_weapons_are_magical(record) -> bool:
    """SRD monsters with magic weapon attacks carry a 'Magic Weapons'
    special ability ("The <monster>'s weapon attacks are magical.")."""
    abilities = (record.model_extra or {}).get("special_abilities") or []
    return any((sa.get("name") or "").lower() == "magic weapons" for sa in abilities)


def _hit_rider(desc: str) -> dict:
    """Structured on-hit rider for an attack action without damage dice
    (TVA-22). Cheap extraction only: the post-'Hit:' text verbatim, any SRD
    condition names it mentions, and a grapple escape DC when present.
    Recurring damage / transfer traits stay DM-adjudicated (`apply_condition`
    / `dm_ruling`)."""
    parts = _HIT_TEXT_RE.split(desc, maxsplit=1)
    text = (parts[1] if len(parts) > 1 else desc).strip()
    lowered = text.lower()
    conditions = sorted(
        c for c in CONDITIONS if re.search(rf"\b{re.escape(c)}\b", lowered)
    )
    rider: dict = {"text": text, "conditions": conditions}
    match = _ESCAPE_DC_RE.search(text)
    if match:
        rider["escape_dc"] = int(match.group(1))
    return rider


def _resolve_attack_spec(ctx: CommandContext, atk: dict, attack_name: str) -> dict | str:
    """Normalize one named attack of a combatant into a spec dict, or return
    a refusal string.

    Monster actions qualify on `attack_bonus` alone: an action whose Hit is
    pure rider text (no damage dice, e.g. the rug's Smother) resolves with
    `damage_notation=None` plus a structured `on_hit` rider (TVA-22).
    """
    if atk["kind"] == "character":
        char = ctx.store.get_character_by_id(atk["character_id"])
        spec = next((s for s in char["attacks"] if s["name"] == attack_name), None)
        if spec is None:
            names = ", ".join(s["name"] for s in char["attacks"]) or "none"
            return (
                f"{atk['key']} has no attack named {attack_name!r} (available: {names})"
            )
        try:
            AttackSpec(**spec)
        except ValidationError:
            return (
                f"{atk['key']}'s attack {attack_name!r} has an invalid stored "
                "spec (pre-validation data?); recreate it or fix via migration"
            )
        dmg_mod = attack_damage_mod(spec, char["abilities"])
        sign = "+" if dmg_mod >= 0 else "-"
        return {
            "attack_bonus": attack_to_hit(spec, char["abilities"], char["level"]),
            "damage_notation": f"{spec['damage']}{sign}{abs(dmg_mod)}",
            "damage_type": spec["damage_type"],
            "ranged": spec["ranged"],
            "range_ft": spec["range_ft"],
            "long_range_ft": spec.get("long_range_ft"),
            "magical": "magical" in (spec.get("properties") or []),
            "on_hit": None,
            "is_pc": char["role"] == "pc",
        }

    record = ctx.rules.get_monster(atk["monster_slug"])
    actions = (record.model_extra or {}).get("actions", [])
    action = next((a for a in actions if a.get("name") == attack_name), None)
    if action is None or "attack_bonus" not in action:
        names = ", ".join(a["name"] for a in actions if "attack_bonus" in a) or "none"
        return f"{atk['name']} has no attack named {attack_name!r} (available: {names})"
    dmg = next(
        (d for d in action.get("damage") or []
         if "damage_dice" in d and d.get("damage_type")),
        None,
    )
    desc = action.get("desc", "")
    range_match = _RANGE_RE.search(desc)
    reach_match = _REACH_RE.search(desc)
    if range_match:
        ranged = True
        range_ft = int(range_match.group(1))
        long_range_ft: int | None = int(range_match.group(2))
    elif reach_match:
        ranged = False
        range_ft = int(reach_match.group(1))
        long_range_ft = None
    else:
        ranged = False
        range_ft = 5
        long_range_ft = None
    return {
        "attack_bonus": action["attack_bonus"],
        "damage_notation": dmg["damage_dice"] if dmg else None,
        "damage_type": dmg["damage_type"]["index"] if dmg else None,
        "ranged": ranged,
        "range_ft": range_ft,
        "long_range_ft": long_range_ft,
        "magical": _monster_weapons_are_magical(record),
        "on_hit": None if dmg else _hit_rider(desc),
        "is_pc": False,
    }


def _break_concentration(ctx: CommandContext, cid: int) -> bool:
    """Clear a character's concentration (and every active effect it was
    sustaining). Returns True if it was concentrating."""
    res = ctx.store.get_resources(cid)
    if res["concentration"] is None:
        return False
    ctx.store.update_resources(cid, concentration=None)
    clear_concentration_effects(ctx, cid)
    return True


def apply_damage_to_target(
    ctx: CommandContext, key: str, amount: int, damage_type: str, *, critical: bool
) -> dict:
    """Apply already-mitigated `amount` of damage to a target and persist it.

    Monsters: hp decremented; at <=0 they are defeated (hp 0, dropped from
    every engagement); end_combat sums defeated XP. Characters: resources.hp
    decremented; dropping to 0 grants unconscious and a fresh dying state,
    while damage whose overflow meets max_hp kills outright; damage taken
    while already at 0 runs `apply_damage_while_dying`. A concentrating
    character that survives conscious gets a `concentration_check` for the DM
    to follow up; one knocked out has its concentration broken here.

    Returns a data fragment merged into the caller's result: at minimum
    `{"target": {"key", "hp", "status"?}}`, plus optional `defeated`,
    `concentration_check`, `concentration_broken`.
    """
    combat = ctx.store.combat()
    combatants = combat["combatants"] if combat["active"] else []
    combatant = next((c for c in combatants if c["key"] == key), None)

    if combatant is not None and combatant["kind"] == "monster":
        hp = combatant["hp"] - amount
        if hp <= 0:
            combatant["hp"] = 0
            combatant["defeated"] = True
            for other in combatants:
                if key in other["engaged_with"]:
                    other["engaged_with"] = [
                        k for k in other["engaged_with"] if k != key
                    ]
            combatant["engaged_with"] = []
            ctx.store.update_combat(combatants=combatants)
            return {"target": {"key": key, "hp": 0, "status": "defeated"},
                    "defeated": True}
        combatant["hp"] = hp
        ctx.store.update_combat(combatants=combatants)
        return {"target": {"key": key, "hp": hp}, "defeated": False}

    # Character target: resolve its id (combatant instance or by name).
    if combatant is not None:
        cid = combatant["character_id"]
    else:
        char = ctx.store.get_character(key)
        if char is None:
            raise ValueError(f"unknown damage target: {key!r}")
        cid = char["id"]

    char_row = ctx.store.get_character_by_id(cid)
    res = ctx.store.get_resources(cid)
    max_hp = char_row["max_hp"]
    hp_before = res["hp"]
    concentrating = res["concentration"] is not None

    frag: dict = {"target": {"key": key}}
    died = False

    if hp_before == 0:
        # Already dying: another failed save (two on a crit).
        outcome = apply_damage_while_dying(
            DeathSaveState(**res["death_saves"]), amount, max_hp, critical=critical
        )
        ctx.store.update_resources(cid, death_saves=outcome.state.model_dump())
        died = outcome.state.dead
        frag["target"]["hp"] = 0
        frag["target"]["status"] = "dead" if died else "dying"
    elif amount >= hp_before and (amount - hp_before) >= max_hp:
        # Massive overflow: instant death.
        death_mode = ctx.store.campaign_meta()["death_mode"]
        status = "dead" if death_mode == "hardcore" else "defeated"
        ctx.store.update_resources(
            cid, hp=0, death_saves=DeathSaveState(dead=True).model_dump()
        )
        ctx.store.update_character(cid, status=status)
        died = True
        frag["target"]["hp"] = 0
        frag["target"]["status"] = status
    elif amount >= hp_before:
        # Dropped to 0: unconscious + fresh dying state.
        conditions = list(res["conditions"])
        if "unconscious" not in conditions:
            conditions.append("unconscious")
        ctx.store.update_resources(
            cid, hp=0, conditions=conditions,
            death_saves=DeathSaveState().model_dump(),
        )
        frag["target"]["hp"] = 0
        frag["target"]["status"] = "unconscious"
    else:
        hp_now = hp_before - amount
        ctx.store.update_resources(cid, hp=hp_now)
        frag["target"]["hp"] = hp_now

    incapacitated = frag["target"].get("status") in (
        "unconscious", "dying", "dead", "defeated"
    )
    if concentrating and amount > 0:
        if incapacitated:
            _break_concentration(ctx, cid)
            frag["concentration_broken"] = True
        else:
            frag["concentration_check"] = {"dc": concentration_save_dc(amount)}

    if died and combatant is not None:
        combatant["defeated"] = True
        ctx.store.update_combat(combatants=combatants)

    return frag


# -- attack ---------------------------------------------------------------


def _resolve_swing(
    ctx: CommandContext,
    atk: dict,
    tgt: dict,
    name: str,
    spec: dict,
    *,
    advantage: bool,
    disadvantage: bool,
    engaged: bool,
    player_attack_value: int | None = None,
    player_damage_value: int | None = None,
) -> dict:
    """Resolve one swing — attack roll, then damage or on-hit rider — and
    apply it. Returns the per-swing data fragment (the single-attack `data`
    shape). Condition interaction is recomputed per swing so mid-volley
    changes (a target dropping unconscious) affect later swings."""
    interaction = attack_interaction(
        _effects_for_combatant(ctx, atk),
        _effects_for_combatant(ctx, tgt),
        engaged=engaged,
    )
    mode = combine_advantage(
        advantage or interaction.mode == "advantage",
        disadvantage or interaction.mode == "disadvantage"
        or spec["range_disadvantage"],
    )
    # Character targets fold live active effects (mage armor, shield of
    # faith) into their AC.
    target_ac = effective_ac_for_combatant(ctx, tgt)
    roll = resolve_attack_roll(
        ctx.roller, spec["attack_bonus"], target_ac, mode,
        player_value=player_attack_value,
    )
    critical = roll.critical_hit or (roll.hit and interaction.auto_crit_on_hit)

    data: dict = {
        "attack_name": name,
        "attack_roll": {
            "natural": roll.d20.natural,
            "total": roll.d20.total,
            "mode": roll.d20.mode,
            "target_ac": target_ac,
        },
        "hit": roll.hit,
        "critical": critical,
        "damage": None,
        "target": {"key": tgt["key"]},
    }
    if not roll.hit:
        return data

    if spec["damage_notation"] is None:
        # No damage dice on this attack (TVA-22): the hit lands its rider —
        # surfaced for the DM to apply via apply_condition / dm_ruling.
        data["on_hit"] = spec["on_hit"]
        return data

    damage = roll_damage(
        ctx.roller, spec["damage_notation"], critical=critical,
        player_value=player_damage_value,
    )
    raw = damage.total
    damage_type = spec["damage_type"]
    if tgt["kind"] == "monster":
        record = ctx.rules.get_monster(tgt["monster_slug"])
        resistances, vulnerabilities, immunities = _monster_defense_sets(
            record, damage_type, is_magical=spec["magical"]
        )
    else:
        res = ctx.store.get_resources(tgt["character_id"])
        petrified = effects_for(res["conditions"], res.get("exhaustion", 0)).resist_all_damage
        resistances = {damage_type} if petrified else set()
        vulnerabilities = set()
        immunities = set()
    mitigated = apply_mitigation(
        raw, damage_type,
        resistances=resistances, vulnerabilities=vulnerabilities, immunities=immunities,
    )
    final = mitigated.final

    fragment = apply_damage_to_target(ctx, tgt["key"], final, damage_type, critical=critical)
    data["damage"] = {
        "raw": raw, "final": final, "type": damage_type, "applied": mitigated.applied,
    }
    data["target"] = fragment["target"]
    if "concentration_check" in fragment:
        data["concentration_check"] = fragment["concentration_check"]
    if fragment.get("concentration_broken"):
        data["concentration_broken"] = True
    if fragment.get("defeated"):
        data["defeated"] = True
    return data


def _drop_tail(target: str, swing: dict) -> str:
    """Digest suffix for a swing that dropped its target, or ''."""
    status = swing["target"].get("status")
    if swing.get("defeated"):
        return " — it drops!"
    if status in ("dead", "defeated"):
        return f" — {target} is {status}!"
    if status == "unconscious":
        return f" — {target} drops unconscious!"
    return ""


def _swing_digest(attacker: str, target: str, swing: dict) -> str:
    ar = swing["attack_roll"]
    if not swing["hit"]:
        outcome = "critically misses" if ar["natural"] == 1 else "misses"
        return f"{attacker} {outcome} {target} ({ar['total']} vs AC {ar['target_ac']})"
    verb = "crits" if swing["critical"] else "hits"
    if swing["damage"] is None:
        rider = swing.get("on_hit") or {}
        effect = (rider.get("text") or "no damage").split(". ")[0]
        return (
            f"{attacker} {verb} {target} with {swing['attack_name']} "
            f"({ar['total']} vs AC {ar['target_ac']}) — {effect}"
        )
    dmg = swing["damage"]
    return (
        f"{attacker} {verb} {target} for {dmg['final']} {dmg['type']} "
        f"({ar['total']} vs AC {ar['target_ac']}){_drop_tail(target, swing)}"
    )


@command("attack")
def attack(
    ctx: CommandContext,
    attacker: str,
    target: str,
    attack_name: str | None = None,
    spend: str = "action",
    player_attack_value: int | None = None,
    player_damage_value: int | None = None,
    advantage: bool = False,
    disadvantage: bool = False,
    attack_names: list[str] | None = None,
    **kwargs,
) -> CommandResult:
    """Resolve one attack, or a full multiattack volley via `attack_names`
    (one action, per-swing results in `data.swings`)."""
    combat = ctx.store.combat()
    if not combat["active"]:
        return refuse("attack", "no combat is active")
    combatants = combat["combatants"]
    atk = next((c for c in combatants if c["key"] == attacker), None)
    tgt = next((c for c in combatants if c["key"] == target), None)
    if atk is None:
        return refuse("attack", f"unknown attacker {attacker!r}")
    if tgt is None:
        return refuse("attack", f"unknown target {target!r}")
    if atk["defeated"]:
        return refuse("attack", f"{attacker} is defeated and cannot act")
    if tgt["defeated"]:
        return refuse("attack", f"{target} is already defeated")

    # Step 2: which swings (TVA-17: attack_names = a multiattack volley).
    multi = attack_names is not None
    if multi:
        if attack_name is not None:
            return refuse("attack", "give either attack_name or attack_names, not both")
        if not attack_names:
            return refuse("attack", "attack_names must be a non-empty list")
        if len(attack_names) > _MAX_SWINGS:
            return refuse(
                "attack", f"too many swings ({len(attack_names)}; max {_MAX_SWINGS})"
            )
        if spend == "reaction":
            return refuse(
                "attack",
                "a reaction is a single attack; multiattack needs spend='action' or 'none'",
            )
        if player_attack_value is not None or player_damage_value is not None:
            return refuse(
                "attack",
                "player values apply to a single attack; multiattack swings are "
                "engine-rolled (or make separate attack calls)",
            )
        names = list(attack_names)
    elif attack_name is None:
        return refuse("attack", "attack_name (or attack_names) is required")
    else:
        names = [attack_name]

    # Step 3: action economy (validated here, committed after all checks).
    if spend not in _SPENDS:
        return refuse("attack", f"invalid spend {spend!r} (expected action/reaction/none)")
    idx = combat["turn_index"]
    is_turn = 0 <= idx < len(combatants) and combatants[idx]["key"] == attacker
    budget = TurnBudget(**atk["budget"]) if atk["budget"] else None
    committed_budget: TurnBudget | None = None
    use_reaction_flag = False
    if spend == "action":
        if not is_turn:
            return refuse("attack", f"it is not {attacker}'s turn (action requires your turn)")
        if budget is None or not budget.action_available:
            return refuse("attack", f"{attacker} has no action remaining this turn")
        committed_budget = spend_budget(budget, "action").budget
    elif spend == "reaction":
        if is_turn:
            if budget is None or not budget.reaction_available:
                return refuse("attack", f"{attacker} has no reaction remaining this turn")
            committed_budget = spend_budget(budget, "reaction").budget
        elif atk["reaction_used"]:
            return refuse("attack", f"{attacker} has already used its reaction this round")
        else:
            use_reaction_flag = True

    # Step 4: attack spec resolution — every named swing, before any spend.
    specs: dict[str, dict] = {}
    for name in names:
        if name in specs:
            continue
        spec = _resolve_attack_spec(ctx, atk, name)
        if isinstance(spec, str):
            return refuse("attack", spec)
        specs[name] = spec
    is_pc = next(iter(specs.values()))["is_pc"]

    # Step 5: player-supplied values (PC attackers only).
    if (player_attack_value is not None or player_damage_value is not None) and not is_pc:
        return refuse(
            "attack", f"{attacker} is engine-rolled; player values are for PCs only"
        )
    if player_attack_value is not None and not (1 <= player_attack_value <= 20):
        return refuse(
            "attack", f"player_attack_value must be between 1 and 20 (got {player_attack_value})"
        )
    if player_damage_value is not None and player_damage_value < 0:
        return refuse(
            "attack", f"player_damage_value must be >= 0 (got {player_damage_value})"
        )

    # Step 6: range legality, per named swing.
    engaged = target in atk["engaged_with"]
    dist = distance_band(atk["band"], tgt["band"], mutually_engaged=engaged)
    for name, spec in specs.items():
        legality = weapon_range_legality(
            dist, spec["range_ft"], spec["long_range_ft"],
            ranged=spec["ranged"], attacker_engaged=bool(atk["engaged_with"]),
        )
        if legality == "out_of_range":
            return refuse(
                "attack",
                f"{name} ({spec['range_ft']} ft) cannot reach a target at {dist}",
            )
        spec["range_disadvantage"] = legality == "disadvantage"

    # Commit the economy spend now that the attack is going ahead.
    if committed_budget is not None:
        atk["budget"] = committed_budget.model_dump()
    if use_reaction_flag:
        atk["reaction_used"] = True
    if committed_budget is not None or use_reaction_flag:
        ctx.store.update_combat(combatants=combatants)

    # Steps 7-8: resolve each swing (advantage math recomputed per swing).
    swings: list[dict] = []
    halted: str | None = None
    for i, name in enumerate(names):
        swing = _resolve_swing(
            ctx, atk, tgt, name, specs[name],
            advantage=advantage, disadvantage=disadvantage, engaged=engaged,
            player_attack_value=player_attack_value,
            player_damage_value=player_damage_value,
        )
        swings.append(swing)
        status = swing["target"].get("status")
        if (swing.get("defeated") or status in ("dead", "defeated")) and i + 1 < len(names):
            halted = f"{target} is down after swing {i + 1}; remaining swings not taken"
            break

    # Step 9: envelope + digest.
    if not multi:
        data = dict(swings[0])
        if spend == "reaction" and not is_turn:
            data["opportunity"] = True
        digest = _swing_digest(attacker, target, swings[0])
        return CommandResult(ok=True, command="attack", digest=digest, data=data)

    hits = sum(1 for s in swings if s["hit"])
    total = sum(s["damage"]["final"] for s in swings if s["damage"])
    data = {
        "swings": swings,
        "attacks": len(names),
        "hits": hits,
        "total_damage": total,
        "target": swings[-1]["target"],
    }
    if halted:
        data["halted"] = halted
    digest = (
        f"{attacker} makes {len(names)} attacks on {target}: "
        f"{hits} hit for {total} damage{_drop_tail(target, swings[-1])}"
    )
    return CommandResult(ok=True, command="attack", digest=digest, data=data)


# -- conditions -----------------------------------------------------------


def _resolve_condition_target(ctx: CommandContext, target: str):
    """Return (kind, combatant | None, character | None) for a condition target.

    A live combatant key wins; otherwise the target is a character name.
    """
    combat = ctx.store.combat()
    if combat["active"]:
        combatant = next(
            (c for c in combat["combatants"] if c["key"] == target), None
        )
        if combatant is not None:
            if combatant["kind"] == "monster":
                return "monster", combatant, None
            char = ctx.store.get_character_by_id(combatant["character_id"])
            return "character", combatant, char
    char = ctx.store.get_character(target)
    if char is not None:
        return "character", None, char
    return "unknown", None, None


@command("apply_condition")
def apply_condition(
    ctx: CommandContext,
    target: str,
    condition: str,
    source: str = "",
    exhaustion_delta: int = 0,
    **kwargs,
) -> CommandResult:
    condition = condition.strip().lower()
    if condition not in CONDITIONS:
        return refuse(
            "apply_condition",
            f"unknown condition {condition!r} (valid conditions: {_VALID_CONDITIONS})",
        )
    kind, combatant, char = _resolve_condition_target(ctx, target)
    if kind == "unknown":
        return refuse("apply_condition", f"unknown target {target!r}")

    # Exhaustion is level-based and tracked only on characters.
    if condition == "exhaustion" or exhaustion_delta:
        if condition != "exhaustion":
            return refuse(
                "apply_condition", "exhaustion_delta only applies to the exhaustion condition"
            )
        if char is None:
            return refuse("apply_condition", "exhaustion tracks on characters only")
        if not -6 <= exhaustion_delta <= 6 or exhaustion_delta == 0:
            return refuse(
                "apply_condition", "exhaustion_delta must be a non-zero value between -6 and 6"
            )
        res = ctx.store.get_resources(char["id"])
        new_level = max(0, min(6, res["exhaustion"] + exhaustion_delta))
        ctx.store.update_resources(char["id"], exhaustion=new_level)
        data = {"target": target, "condition": "exhaustion", "exhaustion": new_level}
        broke = _maybe_break_concentration(ctx, char, res["conditions"], new_level)
        if broke:
            data["concentration_broken"] = True
        return CommandResult(
            ok=True, command="apply_condition",
            digest=f"{target} is now exhaustion level {new_level}", data=data,
        )

    if kind == "monster":
        if condition in combatant["conditions"]:
            return refuse("apply_condition", f"{target} is already {condition}")
        combatant["conditions"] = [*combatant["conditions"], condition]
        combatants = ctx.store.combat()["combatants"]
        for c in combatants:
            if c["key"] == target:
                c["conditions"] = combatant["conditions"]
        ctx.store.update_combat(combatants=combatants)
        return CommandResult(
            ok=True, command="apply_condition",
            digest=f"{target} is now {condition}",
            data={"target": target, "condition": condition},
        )

    res = ctx.store.get_resources(char["id"])
    if condition in res["conditions"]:
        return refuse("apply_condition", f"{target} is already {condition}")
    new_conditions = [*res["conditions"], condition]
    ctx.store.update_resources(char["id"], conditions=new_conditions)
    data = {"target": target, "condition": condition}
    broke = _maybe_break_concentration(
        ctx, char, new_conditions, res.get("exhaustion", 0)
    )
    if broke:
        data["concentration_broken"] = True
    return CommandResult(
        ok=True, command="apply_condition",
        digest=f"{target} is now {condition}", data=data,
    )


def _maybe_break_concentration(
    ctx: CommandContext, char: dict, conditions: list[str], exhaustion: int
) -> bool:
    """Break a concentrating character's concentration when its new condition
    set incapacitates it. Returns True if concentration was broken."""
    if not effects_for(conditions, exhaustion).can_take_actions:
        return _break_concentration(ctx, char["id"])
    return False


@command("remove_condition")
def remove_condition(
    ctx: CommandContext, target: str, condition: str, **kwargs
) -> CommandResult:
    condition = condition.strip().lower()
    if condition not in CONDITIONS:
        return refuse(
            "remove_condition",
            f"unknown condition {condition!r} (valid conditions: {_VALID_CONDITIONS})",
        )
    kind, combatant, char = _resolve_condition_target(ctx, target)
    if kind == "unknown":
        return refuse("remove_condition", f"unknown target {target!r}")

    if condition == "exhaustion":
        if char is None:
            return refuse("remove_condition", "exhaustion tracks on characters only")
        res = ctx.store.get_resources(char["id"])
        if res["exhaustion"] == 0:
            return refuse("remove_condition", f"{target} has no exhaustion")
        ctx.store.update_resources(char["id"], exhaustion=0)
        return CommandResult(
            ok=True, command="remove_condition",
            digest=f"{target} recovers from exhaustion",
            data={"target": target, "condition": "exhaustion", "exhaustion": 0},
        )

    if kind == "monster":
        if condition not in combatant["conditions"]:
            return refuse("remove_condition", f"{target} is not {condition}")
        combatants = ctx.store.combat()["combatants"]
        for c in combatants:
            if c["key"] == target:
                c["conditions"] = [x for x in c["conditions"] if x != condition]
        ctx.store.update_combat(combatants=combatants)
        return CommandResult(
            ok=True, command="remove_condition",
            digest=f"{target} is no longer {condition}",
            data={"target": target, "condition": condition},
        )

    res = ctx.store.get_resources(char["id"])
    if condition not in res["conditions"]:
        return refuse("remove_condition", f"{target} is not {condition}")
    new_conditions = [x for x in res["conditions"] if x != condition]
    ctx.store.update_resources(char["id"], conditions=new_conditions)
    return CommandResult(
        ok=True, command="remove_condition",
        digest=f"{target} is no longer {condition}",
        data={"target": target, "condition": condition},
    )


@command("break_concentration")
def break_concentration(
    ctx: CommandContext, character: str, **kwargs
) -> CommandResult:
    char = ctx.store.get_character(character)
    if char is None:
        return refuse("break_concentration", f"no character named {character!r}")
    if not _break_concentration(ctx, char["id"]):
        return refuse("break_concentration", f"{character} is not concentrating")
    return CommandResult(
        ok=True, command="break_concentration",
        digest=f"{character}'s concentration is broken",
        data={"character": character, "concentration_broken": True},
    )
