"""Rest and item commands.

`rest` applies short/long rests over the whole party (RAW deltas from
`rules.rests`), refusing while combat is active. `use_item` / `add_item` /
`remove_item` are thin, refusal-guarded wrappers over the inventory store; a
healing item with a `heal` notation rolls engine dice and heals via the same
sink cure-wounds uses.
"""

from __future__ import annotations

from dm_engine.commands.effects import (
    clear_concentration_effects,
    expire_clock_effects,
    expire_rest_effects,
)
from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.commands.registry import CommandContext, command
from dm_engine.commands.spells import _apply_healing
from dm_engine.rules.attacks import roll_damage
from dm_engine.rules.checks import ability_modifier
from dm_engine.rules.death import DeathSaveState
from dm_engine.rules.rests import HitDicePool, long_rest, spend_hit_dice

_KINDS = ("short", "long")
_LONG_REST_MINUTES = 8 * 60


@command("rest")
def rest(
    ctx: CommandContext,
    kind: str,
    hit_dice: dict[str, int] | None = None,
    player_hit_die_values: list[int] | None = None,
    wake_time: str | None = None,
    **kwargs,
) -> CommandResult:
    if kind not in _KINDS:
        return refuse("rest", f"unknown rest kind {kind!r} (expected short/long)")
    if ctx.store.combat()["active"]:
        return refuse("rest", "cannot rest while combat is active")
    if kind == "short":
        if wake_time is not None:
            return refuse("rest", "wake_time is only valid for a long rest")
        return _short_rest(ctx, hit_dice or {}, player_hit_die_values)
    return _long_rest(ctx, wake_time)


def _short_rest(
    ctx: CommandContext, hit_dice: dict[str, int], player_values: list[int] | None
) -> CommandResult:
    # Validate every spend up front so an over-spend refuses before any roll.
    plan: list[tuple[dict, int, list[int] | None]] = []
    for name, count in hit_dice.items():
        char = ctx.store.get_character(name)
        if char is None or char["status"] != "active":
            return refuse("rest", f"no active character named {name!r}")
        if count < 1:
            return refuse("rest", f"{name} must spend at least one hit die")
        remaining = ctx.store.get_resources(char["id"])["hit_dice_remaining"]
        if count > remaining:
            return refuse(
                "rest", f"{name} has only {remaining} hit dice remaining (asked {count})"
            )
        values = None
        if char["role"] == "pc" and player_values is not None:
            if len(player_values) != count:
                return refuse(
                    "rest",
                    f"{name}: expected {count} hit-die values, got {len(player_values)}",
                )
            values = player_values
        plan.append((char, count, values))

    per_character: list[dict] = []
    for char, count, values in plan:
        cid = char["id"]
        res = ctx.store.get_resources(cid)
        hit_die = ctx.rules.get_class(char["class_slug"])["hit_die"]
        con_mod = ability_modifier(char["abilities"]["con"])
        pool = HitDicePool(
            die=hit_die, total=char["level"], remaining=res["hit_dice_remaining"]
        )
        outcome = spend_hit_dice(
            ctx.roller, pool, count, con_mod, player_values=values
        )
        new_hp = min(char["max_hp"], res["hp"] + outcome.healed)
        ctx.store.update_resources(
            cid, hp=new_hp, hit_dice_remaining=outcome.pool.remaining
        )
        per_character.append({
            "name": char["name"], "healed": outcome.healed, "hp": new_hp,
            "hit_dice_remaining": outcome.pool.remaining,
        })

    expired = expire_rest_effects(ctx, "short")

    total = sum(c["healed"] for c in per_character)
    digest = f"Short rest — the party recovers {total} HP"
    return CommandResult(
        ok=True, command="rest", digest=digest,
        data={"kind": "short", "per_character": per_character,
              "effects_expired": [e["name"] for e in expired]},
    )


def _long_rest(ctx: CommandContext, wake_time: str | None = None) -> CommandResult:
    rest_minutes = _LONG_REST_MINUTES
    if wake_time is not None:
        if not isinstance(wake_time, str) or len(wake_time) != 5 or wake_time[2] != ":":
            return refuse("rest", "wake_time must use HH:MM format")
        try:
            hour_text, minute_text = wake_time.split(":", 1)
            hour, minute = int(hour_text), int(minute_text)
        except ValueError:
            return refuse("rest", "wake_time must use HH:MM format")
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return refuse("rest", "wake_time must be a valid 24-hour HH:MM time")
        clock = ctx.store.world_clock()
        target = hour * 60 + minute
        rest_minutes = (target - clock["minutes"]) % 1440
        if rest_minutes < _LONG_REST_MINUTES:
            return refuse(
                "rest",
                f"waking at {wake_time} would allow only {rest_minutes} minutes; "
                "a long rest requires at least 480 minutes",
            )

    per_character: list[dict] = []
    rested_ids: list[int] = []
    for char in ctx.store.party():
        if char["status"] != "active":
            continue
        cid = char["id"]
        rested_ids.append(cid)
        res = ctx.store.get_resources(cid)
        hit_die = ctx.rules.get_class(char["class_slug"])["hit_die"]
        pool = HitDicePool(
            die=hit_die, total=char["level"], remaining=res["hit_dice_remaining"]
        )
        outcome = long_rest(pool, res["exhaustion"])
        slots = res["spell_slots"]
        for slot in slots.values():
            slot["remaining"] = slot["max"]
        ctx.store.update_resources(
            cid,
            hp=char["max_hp"],
            hit_dice_remaining=outcome.pool.remaining,
            spell_slots=slots,
            exhaustion=outcome.exhaustion_level,
            conditions=[c for c in res["conditions"] if c != "unconscious"],
            concentration=None,
            death_saves=DeathSaveState().model_dump(),
        )
        per_character.append({
            "name": char["name"],
            "healed": char["max_hp"] - res["hp"],
            "hit_dice_regained": outcome.hit_dice_regained,
            "exhaustion": outcome.exhaustion_level,
        })

    # Effects end with the rest: rest-scoped ones, everything the sleepers
    # were concentrating on (concentration was cleared above), and anything
    # whose clock runs out during the 8 hours.
    expired = expire_rest_effects(ctx, "long")
    for cid in rested_ids:
        expired += clear_concentration_effects(ctx, cid)

    clock = ctx.store.world_clock()
    day_overflow, minutes = divmod(clock["minutes"] + rest_minutes, 1440)
    new_day = clock["day"] + day_overflow
    ctx.store.update_world_clock(day=new_day, minutes=minutes)
    expired += expire_clock_effects(ctx)

    digest = (f"Long rest ({rest_minutes} minutes) — the party wakes on day "
              f"{new_day}, {minutes // 60:02d}:{minutes % 60:02d} fully restored")
    return CommandResult(
        ok=True, command="rest", digest=digest,
        data={"kind": "long", "per_character": per_character, "day": new_day,
              "clock": ctx.store.world_clock(), "elapsed_minutes": rest_minutes,
              "effects_expired": [e["name"] for e in expired]},
    )


@command("use_item")
def use_item(
    ctx: CommandContext, character: str, item: str, heal: str | None = None, **kwargs
) -> CommandResult:
    char = ctx.store.get_character(character)
    if char is None:
        return refuse("use_item", f"no character named {character!r}")
    # The healing path self-targets: a hardcore-dead character must refuse
    # here, before the item charge is consumed (the registry commits
    # refusals) — same guard as cast_spell's heal-target validation, or a
    # dead PC could end up status="dead" with hp > 0.
    if heal is not None and char["status"] == "dead":
        return refuse("use_item", f"{char['name']} is dead")
    if not ctx.store.remove_item(char["id"], item, 1):
        return refuse("use_item", f"{character} is not holding {item!r}")

    if heal is not None:
        roll = roll_damage(ctx.roller, heal)
        frag = _apply_healing(ctx, character, roll.total)
        digest = (
            f"{character} uses {item} — healed for {frag['healed']} (hp {frag['hp']})"
        )
        return CommandResult(
            ok=True, command="use_item", digest=digest,
            data={"item": item, "healed": frag["healed"], "hp": frag["hp"]},
        )

    digest = f"{character} uses {item} — resolve its effect via dm_ruling"
    return CommandResult(
        ok=True, command="use_item", digest=digest,
        data={"item": item, "needs_ruling": True},
    )


@command("add_item")
def add_item(
    ctx: CommandContext, character: str, item: str, quantity: int = 1,
    notes: str | None = None, **kwargs,
) -> CommandResult:
    char = ctx.store.get_character(character)
    if char is None:
        return refuse("add_item", f"no character named {character!r}")
    if quantity < 1:
        return refuse("add_item", "quantity must be at least 1")
    ctx.store.add_item(char["id"], item, quantity, notes)
    return CommandResult(
        ok=True, command="add_item",
        digest=f"{character} gains {quantity}x {item}",
        data={"item": item, "quantity": quantity},
    )


@command("remove_item")
def remove_item(
    ctx: CommandContext, character: str, item: str, quantity: int = 1, **kwargs
) -> CommandResult:
    char = ctx.store.get_character(character)
    if char is None:
        return refuse("remove_item", f"no character named {character!r}")
    if quantity < 1:
        return refuse("remove_item", "quantity must be at least 1")
    if not ctx.store.remove_item(char["id"], item, quantity):
        return refuse("remove_item", f"{character} does not have {quantity}x {item}")
    return CommandResult(
        ok=True, command="remove_item",
        digest=f"{character} loses {quantity}x {item}",
        data={"item": item, "quantity": quantity},
    )
