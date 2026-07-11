"""Active-effect helpers shared across command modules (TVA-20).

`active_effects` rows are timed mechanical riders on characters (mage
armor's AC 15 for 8 hours, bless, cover) created by dm_ruling's
`apply_effect` op. This module is the single place that decides which
rows are live and folds them into mechanics:

- consultation (`current_effects_for`, `effective_ac_for_combatant`)
  filters clock-expired rows without deleting them, so a stale row can
  never change an attack outcome even if a cleanup hook was missed;
- housekeeping (`expire_clock_effects`, `expire_rest_effects`,
  `clear_concentration_effects`) deletes rows when the world clock
  advances (travel, long rest, ritual casting), the party rests, or a
  caster's concentration ends.

No @command handlers live here — only helpers called from inside other
handlers, so they run within the caller's registry transaction.
"""

from __future__ import annotations

from dm_engine.commands.registry import CommandContext
from dm_engine.rules.active_effects import clock_expired, effective_ac


def current_effects_for(ctx: CommandContext, character_id: int) -> list[dict]:
    """A character's active effects that are live at the current world clock."""
    clock = ctx.store.world_clock()
    return [
        e for e in ctx.store.active_effects_for(character_id)
        if not clock_expired(e, clock["day"], clock["minutes"])
    ]


def effective_ac_for_combatant(ctx: CommandContext, combatant: dict) -> int:
    """The AC an attack must beat: base AC plus live effect mechanics.

    Monsters carry their AC on the combatant entry and take no tracked
    effects; characters fold live `ac_override`/`ac_bonus` mechanics over
    their stored base AC.
    """
    if combatant["kind"] != "character":
        return combatant["ac"]
    char = ctx.store.get_character_by_id(combatant["character_id"])
    mechanics = [
        e["mechanics"] for e in current_effects_for(ctx, combatant["character_id"])
    ]
    return effective_ac(char["ac"], mechanics)


def _delete(ctx: CommandContext, effects: list[dict]) -> list[dict]:
    for effect in effects:
        ctx.store.delete_effect(effect["id"])
    return effects


def expire_clock_effects(ctx: CommandContext) -> list[dict]:
    """Delete (and return) every effect whose clock expiry has been reached.
    Call after any world-clock advancement."""
    clock = ctx.store.world_clock()
    return _delete(ctx, [
        e for e in ctx.store.all_active_effects()
        if clock_expired(e, clock["day"], clock["minutes"])
    ])


def expire_rest_effects(ctx: CommandContext, kind: str) -> list[dict]:
    """Delete (and return) effects that end on a rest: a short rest clears
    `expires_on_rest='short'`; a long rest clears both kinds."""
    kinds = ("short",) if kind == "short" else ("short", "long")
    return _delete(ctx, [
        e for e in ctx.store.all_active_effects() if e["expires_on_rest"] in kinds
    ])


def clear_concentration_effects(ctx: CommandContext, caster_id: int) -> list[dict]:
    """Delete (and return) every effect sustained by `caster_id`'s
    concentration. Call whenever that concentration breaks or is replaced."""
    return _delete(ctx, [
        e for e in ctx.store.all_active_effects()
        if e["concentration"] and e["caster_id"] == caster_id
    ])
