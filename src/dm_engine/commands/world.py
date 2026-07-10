"""World-state commands: scene/clock, travel, NPCs, locations, quests."""

from __future__ import annotations

from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.commands.registry import CommandContext, command

_QUEST_STATUSES = ("open", "active", "completed", "failed", "abandoned")


@command("set_scene")
def set_scene(
    ctx: CommandContext, description: str, location_slug: str | None = None, **kwargs
) -> CommandResult:
    if location_slug is not None and ctx.store.get_location(location_slug) is None:
        return refuse("set_scene", f"unknown location {location_slug!r}")

    fields: dict = {"scene": description}
    if location_slug is not None:
        fields["location_slug"] = location_slug
    ctx.store.update_world_clock(**fields)
    return CommandResult(
        ok=True, command="set_scene", digest=f"Scene set: {description}",
        data={"scene": description, "location_slug": location_slug},
    )


@command("travel")
def travel(
    ctx: CommandContext, destination_slug: str, hours: int = 0, days: int = 0, **kwargs
) -> CommandResult:
    if ctx.store.get_location(destination_slug) is None:
        return refuse("travel", f"unknown location {destination_slug!r}")
    if hours + days <= 0:
        return refuse("travel", "travel time (hours + days) must be positive")

    clock = ctx.store.world_clock()
    day_overflow, minutes = divmod(clock["minutes"] + hours * 60, 1440)
    new_day = clock["day"] + days + day_overflow
    ctx.store.update_world_clock(
        day=new_day, minutes=minutes, location_slug=destination_slug, scene=None
    )
    new_clock = ctx.store.world_clock()
    return CommandResult(
        ok=True, command="travel", digest=f"Traveled to {destination_slug}",
        data={"clock": new_clock},
    )


@command("create_npc")
def create_npc(
    ctx: CommandContext,
    name: str,
    disposition: str = "neutral",
    location_slug: str | None = None,
    notes: dict | None = None,
    **kwargs,
) -> CommandResult:
    ctx.store.upsert_npc(name, disposition, location_slug, notes or {})
    where = f", at {location_slug}" if location_slug else ""
    return CommandResult(
        ok=True, command="create_npc",
        digest=f"NPC {name} recorded ({disposition}{where})",
        data={"name": name, "disposition": disposition, "location_slug": location_slug},
    )


@command("get_npc")
def get_npc(ctx: CommandContext, name: str, **kwargs) -> CommandResult:
    npc = ctx.store.get_npc(name)
    if npc is None:
        known = ", ".join(n["name"] for n in ctx.store.npcs()) or "none recorded"
        return refuse("get_npc", f"unknown NPC {name!r} (known: {known})")
    where = f" at {npc['location_slug']}" if npc["location_slug"] else ""
    return CommandResult(
        ok=True, command="get_npc", gm_only=True,
        digest=f"NPC {npc['name']}: {npc['disposition']}{where}",
        data={"npc": {"name": npc["name"], "disposition": npc["disposition"],
                      "location_slug": npc["location_slug"], "notes": npc["notes"]}},
    )


@command("list_npcs")
def list_npcs(
    ctx: CommandContext, location_slug: str | None = None, **kwargs
) -> CommandResult:
    if location_slug is not None and ctx.store.get_location(location_slug) is None:
        return refuse("list_npcs", f"unknown location {location_slug!r}")
    compact = [
        {"name": n["name"], "disposition": n["disposition"],
         "location_slug": n["location_slug"]}
        for n in ctx.store.npcs(location_slug)
    ]
    where = f" at {location_slug}" if location_slug else ""
    return CommandResult(
        ok=True, command="list_npcs",
        digest=f"{len(compact)} NPC(s) known{where}",
        data={"npcs": compact, "location_slug": location_slug},
    )


@command("list_locations")
def list_locations(ctx: CommandContext, **kwargs) -> CommandResult:
    compact = [
        {"slug": loc["slug"], "name": loc["name"], "region": loc["region"]}
        for loc in ctx.store.locations()
    ]
    return CommandResult(
        ok=True, command="list_locations",
        digest=f"{len(compact)} location(s) known",
        data={"locations": compact},
    )


@command("create_location")
def create_location(
    ctx: CommandContext, slug: str, name: str, description: str,
    region: str | None = None, **kwargs,
) -> CommandResult:
    ctx.store.upsert_location(slug, name, description, region)
    return CommandResult(
        ok=True, command="create_location", digest=f"Location {name} recorded",
        data={"slug": slug, "name": name, "region": region},
    )


@command("update_quest")
def update_quest(
    ctx: CommandContext, slug: str, title: str, status: str = "open",
    notes: str = "", **kwargs,
) -> CommandResult:
    if status not in _QUEST_STATUSES:
        return refuse("update_quest", f"invalid quest status {status!r}")
    ctx.store.upsert_quest(slug, title, status, notes)
    return CommandResult(
        ok=True, command="update_quest", digest=f"Quest {title} updated ({status})",
        data={"slug": slug, "title": title, "status": status},
    )
