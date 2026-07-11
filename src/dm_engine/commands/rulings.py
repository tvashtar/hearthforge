"""`dm_ruling`: the escape hatch for anything the frozen command set doesn't
model — combat trap damage, a magic item's one-off effect, a house-ruled
outcome — logged with a mandatory rationale (FC-7) so the audit trail always
explains *why* the engine's normal rules were set aside.

The ruling carries an optional batch of effect ops (adjust_hp,
set_condition, clear_condition, adjust_slot, set_exhaustion, adjust_xp,
apply_effect, end_effect, note). Every op is validated against current state before any of them
apply: a single invalid op refuses the whole batch, untouched (the registry
wraps every command in one store transaction, but that only rolls back on
an *exception* — a plain refusal still commits whatever the handler already
wrote, so atomicity for this op language has to be enforced here, with a
validate-then-apply split).
"""

from __future__ import annotations

from typing import Any

from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.commands.registry import CommandContext, command
from dm_engine.rules.active_effects import validate_mechanics
from dm_engine.rules.conditions import CONDITIONS

# The effect-op vocabulary: op name -> its required fields. Single source of
# truth (TVA-25) — _validate_op, the unknown-op refusal, and the MCP tool
# description (dm_ruling.__doc__ below) all derive from this table.
_OP_FIELDS: dict[str, str] = {
    "adjust_hp": "target, delta",
    "set_condition": "target, condition",
    "clear_condition": "target, condition",
    "adjust_slot": "character, slot_level, delta",
    "set_exhaustion": "target, level (0-6)",
    "adjust_xp": "character, delta",
    "apply_effect": "target, name, mechanics"
    " [duration_minutes | expires_on_rest, concentration, concentration_by]",
    "end_effect": "target, name",
    "note": "text",
}
_OPS = tuple(_OP_FIELDS)
_REST_KINDS = ("short", "long")
_MINUTES_PER_DAY = 1440


def ops_cheatsheet() -> str:
    """The op vocabulary as one line, e.g. "adjust_hp(target, delta); ..."."""
    return "; ".join(f"{name}({fields})" for name, fields in _OP_FIELDS.items())


def _effect_name_matches(effect: dict, name: str) -> bool:
    return effect["name"].lower() == name.strip().lower()


def _resolve_target(ctx: CommandContext, target: str):
    """Return ("monster", combatant, None) | ("character", char, combatant|None)
    | ("unknown", None, None). A live combatant key wins over a character
    name (attacks.py's condition-target resolution pattern)."""
    combat = ctx.store.combat()
    if combat["active"]:
        combatant = next((c for c in combat["combatants"] if c["key"] == target), None)
        if combatant is not None:
            if combatant["kind"] == "monster":
                return "monster", combatant, None
            char = ctx.store.get_character_by_id(combatant["character_id"])
            return "character", char, combatant
    char = ctx.store.get_character(target)
    if char is not None:
        return "character", char, None
    return "unknown", None, None


def _validate_op(ctx: CommandContext, op: Any) -> str | None:
    """Return a refusal reason, or None if `op` is legal against current state."""
    if not isinstance(op, dict):
        return f"effect op must be an object, got {op!r}"
    kind = op.get("op")
    if kind not in _OPS:
        return f"unknown op {kind!r} (valid ops: {', '.join(_OPS)})"

    if kind == "adjust_hp":
        target, delta = op.get("target"), op.get("delta")
        if not isinstance(target, str) or not target:
            return "adjust_hp requires a target"
        if not isinstance(delta, int):
            return "adjust_hp requires an integer delta"
        rkind, _, _ = _resolve_target(ctx, target)
        if rkind == "unknown":
            return f"unknown target {target!r}"
        return None

    if kind in ("set_condition", "clear_condition"):
        target, condition = op.get("target"), op.get("condition")
        if not isinstance(target, str) or not target:
            return f"{kind} requires a target"
        if condition not in CONDITIONS:
            return (
                f"unknown condition {condition!r} "
                f"(valid conditions: {', '.join(sorted(CONDITIONS))})"
            )
        rkind, _, _ = _resolve_target(ctx, target)
        if rkind == "unknown":
            return f"unknown target {target!r}"
        return None

    if kind == "adjust_slot":
        character, slot_level, delta = (
            op.get("character"), op.get("slot_level"), op.get("delta")
        )
        if not isinstance(character, str) or not character:
            return "adjust_slot requires a character"
        if not isinstance(slot_level, int):
            return "adjust_slot requires an integer slot_level"
        if not isinstance(delta, int):
            return "adjust_slot requires an integer delta"
        char = ctx.store.get_character(character)
        if char is None:
            return f"no character named {character!r}"
        res = ctx.store.get_resources(char["id"])
        if str(slot_level) not in res["spell_slots"]:
            return f"{character} has no level {slot_level} spell slots"
        return None

    if kind == "set_exhaustion":
        target, level = op.get("target"), op.get("level")
        if not isinstance(target, str) or not target:
            return "set_exhaustion requires a target"
        if not isinstance(level, int) or isinstance(level, bool) or not 0 <= level <= 6:
            return "set_exhaustion requires an integer level between 0 and 6"
        rkind, _, _ = _resolve_target(ctx, target)
        if rkind == "unknown":
            return f"unknown target {target!r}"
        if rkind == "monster":
            return f"{target} is a monster; exhaustion tracks on characters only"
        return None

    if kind == "adjust_xp":
        character, delta = op.get("character"), op.get("delta")
        if not isinstance(character, str) or not character:
            return "adjust_xp requires a character"
        if not isinstance(delta, int):
            return "adjust_xp requires an integer delta"
        if ctx.store.get_character(character) is None:
            return f"no character named {character!r}"
        return None

    if kind == "apply_effect":
        target, name = op.get("target"), op.get("name")
        if not isinstance(target, str) or not target:
            return "apply_effect requires a target"
        if not isinstance(name, str) or not name.strip():
            return "apply_effect requires a non-empty effect name"
        rkind, _, _ = _resolve_target(ctx, target)
        if rkind == "unknown":
            return f"unknown target {target!r}"
        if rkind == "monster":
            return f"{target} is a monster; active effects track on characters only"
        reason = validate_mechanics(op.get("mechanics", {}))
        if reason is not None:
            return f"apply_effect: {reason}"
        duration = op.get("duration_minutes")
        if duration is not None and (
            not isinstance(duration, int) or isinstance(duration, bool) or duration <= 0
        ):
            return "apply_effect duration_minutes must be a positive integer"
        rest = op.get("expires_on_rest")
        if rest is not None and rest not in _REST_KINDS:
            return "apply_effect expires_on_rest must be 'short' or 'long'"
        concentration = op.get("concentration", False)
        if not isinstance(concentration, bool):
            return "apply_effect concentration must be a boolean"
        by = op.get("concentration_by")
        if by is not None:
            if not concentration:
                return "apply_effect concentration_by requires concentration=true"
            if not isinstance(by, str) or ctx.store.get_character(by) is None:
                return f"no character named {by!r} to hold concentration"
        return None

    if kind == "end_effect":
        target, name = op.get("target"), op.get("name")
        if not isinstance(target, str) or not target:
            return "end_effect requires a target"
        if not isinstance(name, str) or not name.strip():
            return "end_effect requires a non-empty effect name"
        rkind, char, _ = _resolve_target(ctx, target)
        if rkind == "unknown":
            return f"unknown target {target!r}"
        if rkind == "monster":
            return f"{target} is a monster; active effects track on characters only"
        effects = ctx.store.active_effects_for(char["id"])
        if not any(_effect_name_matches(e, name) for e in effects):
            active = ", ".join(e["name"] for e in effects) or "none"
            return f"{target} has no active effect named {name!r} (active: {active})"
        return None

    # note
    text = op.get("text")
    if not isinstance(text, str) or not text.strip():
        return "note requires non-empty text"
    return None


def _apply_op(ctx: CommandContext, op: dict) -> dict:
    """Apply one already-validated op; return its echo for `data['applied']`."""
    kind = op["op"]

    if kind == "adjust_hp":
        target, delta = op["target"], op["delta"]
        rkind, char, _ = _resolve_target(ctx, target)
        if rkind == "monster":
            combatants = ctx.store.combat()["combatants"]
            live = next(c for c in combatants if c["key"] == target)
            hp = max(0, live["hp"] + delta)
            live["hp"] = hp
            if hp == 0:
                live["defeated"] = True
                for other in combatants:
                    other["engaged_with"] = [
                        k for k in other["engaged_with"] if k != target
                    ]
            ctx.store.update_combat(combatants=combatants)
            return {"op": "adjust_hp", "target": target, "delta": delta, "hp": hp}
        res = ctx.store.get_resources(char["id"])
        hp = max(0, min(char["max_hp"], res["hp"] + delta))
        ctx.store.update_resources(char["id"], hp=hp)
        return {"op": "adjust_hp", "target": target, "delta": delta, "hp": hp}

    if kind in ("set_condition", "clear_condition"):
        target, condition = op["target"], op["condition"]
        rkind, char, _ = _resolve_target(ctx, target)
        adding = kind == "set_condition"
        if rkind == "monster":
            combatants = ctx.store.combat()["combatants"]
            live = next(c for c in combatants if c["key"] == target)
            conditions = list(live["conditions"])
            if adding and condition not in conditions:
                conditions.append(condition)
            elif not adding and condition in conditions:
                conditions.remove(condition)
            live["conditions"] = conditions
            ctx.store.update_combat(combatants=combatants)
        else:
            res = ctx.store.get_resources(char["id"])
            conditions = list(res["conditions"])
            if adding and condition not in conditions:
                conditions.append(condition)
            elif not adding and condition in conditions:
                conditions.remove(condition)
            ctx.store.update_resources(char["id"], conditions=conditions)
        return {"op": kind, "target": target, "condition": condition}

    if kind == "adjust_slot":
        character, slot_level, delta = op["character"], op["slot_level"], op["delta"]
        char = ctx.store.get_character(character)
        res = ctx.store.get_resources(char["id"])
        slots = res["spell_slots"]
        key = str(slot_level)
        entry = slots[key]
        remaining = max(0, min(entry["max"], entry["remaining"] + delta))
        slots[key] = {"max": entry["max"], "remaining": remaining}
        ctx.store.update_resources(char["id"], spell_slots=slots)
        return {
            "op": "adjust_slot", "character": character, "slot_level": slot_level,
            "delta": delta, "remaining": remaining,
        }

    if kind == "set_exhaustion":
        target, level = op["target"], op["level"]
        _, char, _ = _resolve_target(ctx, target)
        ctx.store.update_resources(char["id"], exhaustion=level)
        return {"op": "set_exhaustion", "target": target, "level": level}

    if kind == "adjust_xp":
        character, delta = op["character"], op["delta"]
        char = ctx.store.get_character(character)
        xp = max(0, char["xp"] + delta)
        ctx.store.update_character(char["id"], xp=xp)
        return {"op": "adjust_xp", "character": character, "delta": delta, "xp": xp}

    if kind == "apply_effect":
        target = op["target"]
        _, char, _ = _resolve_target(ctx, target)
        name = op["name"].strip()
        mechanics = op.get("mechanics") or {}
        expires_day = expires_minutes = None
        duration = op.get("duration_minutes")
        if duration is not None:
            clock = ctx.store.world_clock()
            expires_day, expires_minutes = divmod(
                clock["day"] * _MINUTES_PER_DAY + clock["minutes"] + duration,
                _MINUTES_PER_DAY,
            )
        concentration = bool(op.get("concentration", False))
        caster_id = None
        if concentration:
            by = op.get("concentration_by")
            caster_id = ctx.store.get_character(by)["id"] if by else char["id"]
        effect_id = ctx.store.add_effect(
            char["id"], name=name, mechanics=mechanics,
            # The event row lands later in this same transaction; the log is
            # append-only, so its id is knowable now.
            source_event_id=ctx.store.next_event_id(),
            expires_day=expires_day, expires_minutes=expires_minutes,
            expires_on_rest=op.get("expires_on_rest"),
            concentration=concentration, caster_id=caster_id,
        )
        return {
            "op": "apply_effect", "target": target, "name": name,
            "effect_id": effect_id, "mechanics": mechanics,
            "expires_day": expires_day, "expires_minutes": expires_minutes,
            "expires_on_rest": op.get("expires_on_rest"),
            "concentration": concentration,
        }

    if kind == "end_effect":
        target, name = op["target"], op["name"]
        _, char, _ = _resolve_target(ctx, target)
        matches = [
            e for e in ctx.store.active_effects_for(char["id"])
            if _effect_name_matches(e, name)
        ]
        for effect in matches:
            ctx.store.delete_effect(effect["id"])
        return {"op": "end_effect", "target": target,
                "name": matches[0]["name"], "ended": len(matches)}

    # note: no state change; it lands in the event record via `data["applied"]`.
    return {"op": "note", "text": op["text"]}


@command("dm_ruling")
def dm_ruling(
    ctx: CommandContext,
    description: str,
    rationale: str,
    effects: list[dict] | None = None,
    gm_only: bool = False,
    **kwargs,
) -> CommandResult:
    effects = effects or []
    if not description.strip():
        return refuse("dm_ruling", "description must not be empty")
    if not rationale.strip():
        return refuse("dm_ruling", "rationale must not be empty")

    for op in effects:
        reason = _validate_op(ctx, op)
        if reason is not None:
            return refuse("dm_ruling", f"invalid effect op: {reason}")

    applied = [_apply_op(ctx, op) for op in effects]

    return CommandResult(
        ok=True, command="dm_ruling", digest=description,
        data={"applied": applied}, gm_only=gm_only,
    )


# The MCP tool description is introspected from the first docstring line
# (mcp/server.py `_description`), so the op cheatsheet must live there: one
# line, built from _OP_FIELDS at import time so it can never drift (TVA-25).
dm_ruling.__doc__ = (
    "Log a DM ruling (mandatory rationale) and apply its `effects` ops "
    "atomically — one invalid op refuses the whole batch. "
    f"Effect ops: {ops_cheatsheet()}."
)


@command("roll_dice")
def roll_dice(
    ctx: CommandContext,
    count: int,
    sides: int,
    reason: str,
    gm_only: bool = False,
    player_values: list[int] | None = None,
    **kwargs,
) -> CommandResult:
    """Roll arbitrary audited dice (count x d(sides)) for a ruling.

    The dice come from the campaign's seeded roller so the event log records
    every die and replay stays deterministic — the audited alternative to
    rolling outside the engine. `player_values` reports the PC's physical
    dice (one value per die, flagged player_supplied); FC-2's etiquette on
    whose dice may be player-supplied is the dm-session skill's to enforce.
    """
    if not isinstance(reason, str) or not reason.strip():
        return refuse("roll_dice", "roll_dice requires a non-empty reason")
    if not isinstance(count, int) or not (1 <= count <= 100):
        return refuse("roll_dice", f"count must be 1-100, got {count!r}")
    if not isinstance(sides, int) or not (2 <= sides <= 1000):
        return refuse("roll_dice", f"sides must be 2-1000, got {sides!r}")
    if player_values is not None:
        if len(player_values) != count:
            return refuse(
                "roll_dice",
                f"player_values has {len(player_values)} dice, expected {count}",
            )
        bad = [v for v in player_values
               if not isinstance(v, int) or not (1 <= v <= sides)]
        if bad:
            return refuse(
                "roll_dice", f"player value {bad[0]!r} is not a d{sides} result"
            )
        # FC-2's DiceRoller takes one player_value per roll: decompose into
        # `count` single-die rolls so each reported die is a logged Roll.
        rolls = [
            ctx.roller.roll(f"1d{sides}", player_value=v, gm_only=gm_only).total
            for v in player_values
        ]
        supplied = True
        who = "Player"
    else:
        rolls = ctx.roller.roll(f"{count}d{sides}", gm_only=gm_only).rolls
        supplied = False
        who = "DM"

    total = sum(rolls)
    digest = f"{who} rolls {count}d{sides} → {rolls} = {total} ({reason.strip()})"
    data = {
        "count": count, "sides": sides, "rolls": rolls, "total": total,
        "reason": reason.strip(), "player_supplied": supplied,
    }
    return CommandResult(
        ok=True, command="roll_dice", digest=digest, data=data, gm_only=gm_only,
    )
