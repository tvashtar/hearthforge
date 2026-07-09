"""`dm_ruling`: the escape hatch for anything the frozen command set doesn't
model — combat trap damage, a magic item's one-off effect, a house-ruled
outcome — logged with a mandatory rationale (FC-7) so the audit trail always
explains *why* the engine's normal rules were set aside.

The ruling carries an optional batch of effect ops (adjust_hp,
set_condition, clear_condition, adjust_slot, set_exhaustion, adjust_xp,
note). Every op is validated against current state before any of them
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
from dm_engine.rules.conditions import CONDITIONS

_OPS = (
    "adjust_hp", "set_condition", "clear_condition", "adjust_slot",
    "set_exhaustion", "adjust_xp", "note",
)


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
        return f"unknown op {kind!r}"

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
            return f"unknown condition {condition!r}"
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
