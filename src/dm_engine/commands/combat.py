"""Combat core: initiative, turn order, movement/engagement, and the
end-of-combat XP award.

Combat state lives in the single `combat_state` row (FC-6). Each combatant is
a `Combatant` dump in the JSON `combatants` list; characters keep hp/conditions
in their own tables (source of truth), monster instances carry theirs inline.
The per-turn action budget is a serialized rules `TurnBudget`; `next_turn`
resets it for the active combatant.
"""

from __future__ import annotations

import re

from dm_engine.commands.characters import award_party_xp
from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.commands.registry import CommandContext, RecordingRoller, command
from dm_engine.rules.action_economy import (
    TurnBudget,
    dash as dash_action,
    new_turn,
    spend,
    spend_movement,
)
from dm_engine.rules.bands import (
    BAND_ORDER,
    Band,
    movement_cost_ft,
    provokes_opportunity_attacks,
)
from dm_engine.rules.checks import ability_modifier
from dm_engine.rules.conditions import effects_for
from dm_engine.rules.encounters import assess_encounter
from dm_engine.rules.initiative import roll_initiative
from dm_engine.state.models import Combatant

_SPEED_RE = re.compile(r"(\d+)")


def _monster_speed(record) -> int:
    """Parse walk speed (e.g. '30 ft.') from a monster record; default 30."""
    walk = (record.model_extra or {}).get("speed", {}).get("walk")
    if isinstance(walk, str):
        match = _SPEED_RE.search(walk)
        if match:
            return int(match.group(1))
    return 30


class _ForcedGMOnlyRoller:
    """Adapter around the recording roller so `roll_initiative` emits
    `gm_only` rolls for monsters. `roll_initiative` rolls combatants in the
    order given; we align a queue of gm_only flags to that order and apply
    each flag to the corresponding roll — one capture per roll, correctly
    hidden or visible."""

    def __init__(self, inner: RecordingRoller, gm_flags: list[bool]):
        self._inner = inner
        self._flags = gm_flags
        self._i = 0

    def roll(self, notation, *, player_value=None, gm_only=False):
        flag = self._flags[self._i]
        self._i += 1
        return self._inner.roll(notation, player_value=player_value, gm_only=flag)


def _condition_effects(ctx: CommandContext, combatant: dict):
    """Fold the active conditions of one combatant into a flag set."""
    if combatant["kind"] == "character":
        res = ctx.store.get_resources(combatant["character_id"])
        return effects_for(res["conditions"], res["exhaustion"])
    return effects_for(combatant["conditions"])


def _budget_for(ctx: CommandContext, combatant: dict, base_speed: int) -> dict | None:
    """The reset action budget for a combatant starting its turn: speed scaled
    by any condition speed multiplier, movement zeroed when it cannot move
    (the action is still granted)."""
    effects = _condition_effects(ctx, combatant)
    speed = int(base_speed * effects.speed_multiplier)
    budget = new_turn(speed)
    if not effects.can_move:
        budget = budget.model_copy(update={"movement_remaining": 0})
    return budget.model_dump()


def _base_speed(ctx: CommandContext, combatant: dict) -> int:
    if combatant["kind"] == "character":
        return ctx.store.get_character_by_id(combatant["character_id"])["speed"]
    record = ctx.rules.get_monster(combatant["monster_slug"])
    return _monster_speed(record)


@command("start_combat")
def start_combat(
    ctx: CommandContext,
    monsters: list[dict],
    pc_initiative: int | None = None,
    surprise: list[str] | None = None,
    **kwargs,
) -> CommandResult:
    surprise = surprise or []
    if ctx.store.combat()["active"]:
        return refuse("start_combat", "combat is already active")
    if not monsters:
        return refuse("start_combat", "no monsters to fight")
    if pc_initiative is not None and not (1 <= pc_initiative <= 20):
        return refuse("start_combat", f"pc_initiative must be 1-20: {pc_initiative!r}")

    # Resolve monster records first so an unknown slug refuses before any work.
    resolved: list[tuple[str, Band, object]] = []
    for entry in monsters:
        slug = entry["slug"]
        record = ctx.rules.get_monster(slug)
        if record is None:
            return refuse("start_combat", f"unknown monster {slug!r}")
        count = entry.get("count", 1)
        band = entry.get("band", "near")
        for _ in range(count):
            resolved.append((slug, band, record))

    active_party = [c for c in ctx.store.party() if c["status"] == "active"]
    if not active_party:
        return refuse("start_combat", "no active party members to fight")

    # Build combatants (unordered); characters first, then monster instances.
    combatants: list[Combatant] = []
    pc_key: str | None = None
    for char in active_party:
        res = ctx.store.get_resources(char["id"])
        if char["role"] == "pc":
            pc_key = char["name"]
        combatants.append(Combatant(
            key=char["name"], kind="character", name=char["name"],
            character_id=char["id"], initiative=0,
            dex_modifier=ability_modifier(char["abilities"]["dex"]),
            ac=char["ac"], band="near",
            surprised=char["name"] in surprise,
        ))
        _ = res  # hp/conditions stay in the tables; not duplicated here

    counters: dict[str, int] = {}
    for slug, band, record in resolved:
        counters[slug] = counters.get(slug, 0) + 1
        key = f"{slug}-{counters[slug]}"
        combatants.append(Combatant(
            key=key, kind="monster", name=record.name, monster_slug=slug,
            initiative=0, dex_modifier=ability_modifier(record.dexterity),
            ac=record.ac, hp=record.hit_points, max_hp=record.hit_points,
            xp=record.xp, band=band, surprised=key in surprise,
        ))

    # Initiative: PC uses its reported natural; monsters roll hidden (gm_only).
    seq = [(c.key, c.dex_modifier) for c in combatants]
    gm_flags = [c.kind == "monster" for c in combatants]
    player_values = {pc_key: pc_initiative} if pc_key and pc_initiative else {}
    roller = _ForcedGMOnlyRoller(ctx.roller, gm_flags)
    order = roll_initiative(roller, seq, player_values=player_values)

    by_key = {c.key: c for c in combatants}
    for entry in order:
        by_key[entry.combatant_id].initiative = entry.total
    ordered = [by_key[entry.combatant_id] for entry in order]

    # First combatant gets a budget now (unless surprised in round 1).
    dumps = [c.model_dump() for c in ordered]
    first = dumps[0]
    if not first["surprised"]:
        first["budget"] = _budget_for(ctx, first, _base_speed(ctx, first))

    assessment = assess_encounter(
        [c["xp"] for c in dumps if c["kind"] == "monster"],
        [c["level"] for c in active_party],
    )

    ctx.store.update_combat(
        active=1, round=1, turn_index=0, combatants=dumps, encounter_xp=0
    )

    order_summary = [
        {"key": c["key"], "initiative": c["initiative"], "kind": c["kind"]}
        for c in dumps
    ]
    monster_count = sum(1 for c in dumps if c["kind"] == "monster")
    inits = ", ".join(f"{c['key']} {c['initiative']}" for c in dumps)
    digest = (
        f"Combat! {monster_count} enemies face the party — initiative: {inits} "
        f"({assessment.difficulty}, {assessment.adjusted_xp} adj. XP)"
    )
    return CommandResult(
        ok=True, command="start_combat", digest=digest,
        data={
            "order": order_summary,
            "round": 1,
            "active": dumps[0]["key"],
            "surprise": list(surprise),
            "encounter": assessment.model_dump(),
        },
    )


@command("next_turn")
def next_turn(ctx: CommandContext, **kwargs) -> CommandResult:
    combat = ctx.store.combat()
    if not combat["active"]:
        return refuse("next_turn", "no combat is active")

    combatants = combat["combatants"]
    n = len(combatants)
    idx = combat["turn_index"]
    rnd = combat["round"]

    # Advance to the next non-defeated combatant, wrapping into a new round.
    for _ in range(n):
        idx += 1
        if idx >= n:
            idx = 0
            rnd += 1
            for c in combatants:
                c["reaction_used"] = False
                if rnd >= 2:
                    c["surprised"] = False
        if not combatants[idx]["defeated"]:
            break

    actor = combatants[idx]
    if actor["surprised"] and rnd == 1:
        actor["budget"] = None
    else:
        actor["budget"] = _budget_for(ctx, actor, _base_speed(ctx, actor))

    ctx.store.update_combat(combatants=combatants, turn_index=idx, round=rnd)
    digest = f"Round {rnd} — {actor['key']}'s turn"
    return CommandResult(
        ok=True, command="next_turn", digest=digest,
        data={"round": rnd, "active": actor["key"], "kind": actor["kind"],
              "budget": actor["budget"]},
    )


def _active_combatant(combat: dict, key: str) -> dict | None:
    combatants = combat["combatants"]
    idx = combat["turn_index"]
    if 0 <= idx < len(combatants) and combatants[idx]["key"] == key:
        return combatants[idx]
    return None


@command("move")
def move(
    ctx: CommandContext, combatant: str, to_band: str,
    disengage: bool = False, dash: bool = False, **kwargs,
) -> CommandResult:
    combat = ctx.store.combat()
    if not combat["active"]:
        return refuse("move", "no combat is active")
    actor = _active_combatant(combat, combatant)
    if actor is None:
        return refuse("move", f"it is not {combatant}'s turn")
    if to_band not in BAND_ORDER:
        return refuse("move", f"unknown band {to_band!r}")
    if actor["budget"] is None:
        return refuse("move", f"{combatant} has no movement this turn")
    if not _condition_effects(ctx, actor).can_move:
        return refuse("move", f"{combatant} cannot move (condition)")

    budget = TurnBudget(**actor["budget"])
    if dash:
        result = dash_action(budget)
        if not result.ok:
            return refuse("move", result.reason)
        budget = result.budget
    if disengage:
        result = spend(budget, "action")
        if not result.ok:
            return refuse("move", "disengage requires an available action")
        budget = result.budget

    from_band = actor["band"]
    cost = movement_cost_ft(from_band, to_band)
    result = spend_movement(budget, cost)
    if not result.ok:
        return refuse("move", result.reason)
    budget = result.budget

    engaged_with = list(actor["engaged_with"])
    provokers = provokes_opportunity_attacks(
        from_band, set(engaged_with), disengaged=disengage
    )
    opportunity = [k for k in engaged_with if k in provokers]

    combatants = combat["combatants"]
    # Departing engaged range dissolves the engagement, both directions.
    if from_band == "engaged" and to_band != "engaged":
        for other in combatants:
            if other["key"] in engaged_with and combatant in other["engaged_with"]:
                other["engaged_with"] = [
                    k for k in other["engaged_with"] if k != combatant
                ]
        actor["engaged_with"] = []

    actor["band"] = to_band
    actor["budget"] = budget.model_dump()
    ctx.store.update_combat(combatants=combatants)

    digest = f"{combatant} moves to {to_band}"
    if opportunity:
        digest += f" (provokes: {', '.join(opportunity)})"
    return CommandResult(
        ok=True, command="move", digest=digest,
        data={"band": to_band, "opportunity_attacks_from": opportunity,
              "budget": actor["budget"]},
    )


@command("engage")
def engage(
    ctx: CommandContext, combatant: str, target: str, **kwargs
) -> CommandResult:
    combat = ctx.store.combat()
    if not combat["active"]:
        return refuse("engage", "no combat is active")
    actor = _active_combatant(combat, combatant)
    if actor is None:
        return refuse("engage", f"it is not {combatant}'s turn")
    combatants = combat["combatants"]
    target_c = next((c for c in combatants if c["key"] == target), None)
    if target_c is None:
        return refuse("engage", f"unknown combatant {target!r}")
    if actor["budget"] is None:
        return refuse("engage", f"{combatant} has no movement this turn")
    if not _condition_effects(ctx, actor).can_move:
        return refuse("engage", f"{combatant} cannot move (condition)")

    budget = TurnBudget(**actor["budget"])
    cost = movement_cost_ft(actor["band"], target_c["band"])
    result = spend_movement(budget, cost)
    if not result.ok:
        return refuse("engage", result.reason)
    budget = result.budget

    actor["band"] = target_c["band"]
    if target not in actor["engaged_with"]:
        actor["engaged_with"].append(target)
    if combatant not in target_c["engaged_with"]:
        target_c["engaged_with"].append(combatant)
    actor["budget"] = budget.model_dump()
    ctx.store.update_combat(combatants=combatants)

    return CommandResult(
        ok=True, command="engage",
        digest=f"{combatant} closes to melee with {target}",
        data={"band": actor["band"], "engaged_with": actor["engaged_with"],
              "budget": actor["budget"]},
    )


@command("end_combat")
def end_combat(ctx: CommandContext, **kwargs) -> CommandResult:
    combat = ctx.store.combat()
    if not combat["active"]:
        return refuse("end_combat", "no combat is active")

    combatants = combat["combatants"]
    defeated = [c["key"] for c in combatants if c["defeated"]]
    total = sum(
        c["xp"] for c in combatants if c["kind"] == "monster" and c["defeated"]
    )
    total += combat["encounter_xp"]

    per_member = 0
    recipients: list[dict] = []
    active_party = any(c["status"] == "active" for c in ctx.store.party())
    if total > 0 and active_party:
        award = award_party_xp(ctx, total, "combat")
        per_member = award["per_member"]
        recipients = award["recipients"]

    ctx.store.update_combat(
        active=0, round=0, turn_index=0, combatants=[], encounter_xp=0
    )

    digest = f"Combat ends — {total} XP awarded ({per_member} each)"
    return CommandResult(
        ok=True, command="end_combat", digest=digest,
        data={"xp_awarded": total, "per_member": per_member,
              "recipients": recipients, "defeated": defeated},
    )


@command("get_scene_state")
def get_scene_state(ctx: CommandContext, **kwargs) -> CommandResult:
    clock = ctx.store.world_clock()
    location = None
    if clock.get("location_slug"):
        location = ctx.store.get_location(clock["location_slug"])

    combat = ctx.store.combat()
    combat_payload = None
    if combat["active"]:
        order = []
        budgets = {}
        for c in combat["combatants"]:
            dump = dict(c)
            if c["kind"] == "character":
                res = ctx.store.get_resources(c["character_id"])
                dump["hp"] = res["hp"]
                dump["max_hp"] = ctx.store.get_character_by_id(c["character_id"])["max_hp"]
                dump["conditions"] = res["conditions"]
            order.append(dump)
            budgets[c["key"]] = c["budget"]
        combat_payload = {
            "round": combat["round"],
            "turn_index": combat["turn_index"],
            "active": combat["combatants"][combat["turn_index"]]["key"],
            "order": order,
            "budgets": budgets,
        }

    return CommandResult(
        ok=True, command="get_scene_state",
        digest="Scene state", gm_only=True,
        data={
            "clock": clock,
            "location": location,
            "scene": clock.get("scene"),
            "combat": combat_payload,
        },
    )
