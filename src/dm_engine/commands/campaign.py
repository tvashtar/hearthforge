"""Campaign lifecycle: bootstrap (pre-store, not a registry command), brief,
and session bookkeeping (end_session / checkpoint)."""

from __future__ import annotations

import random
from pathlib import Path

from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.commands.registry import CommandContext, RecordingRoller, command
from dm_engine.content.lookup import RulesDB
from dm_engine.state.store import CampaignStore


def bootstrap_campaign(
    campaigns_dir: Path,
    rules_db_path: Path,
    *,
    slug: str,
    name: str,
    death_mode: str = "narrative",
    skeleton: dict,
    starting_region: dict | None = None,
    seed: int | None = None,
    start_day: int = 1,
    start_time: str = "08:00",
) -> CommandContext:
    """Create a brand-new campaign store and return a ready CommandContext.

    Not a registry command: there is no open store yet when this runs, so it
    creates the store directly, seeds any starting locations/NPCs, and
    appends a synthetic `create_campaign` event row itself.
    """
    if start_day < 1:
        raise ValueError("start_day must be at least 1")
    if not isinstance(start_time, str) or len(start_time) != 5 or start_time[2] != ":":
        raise ValueError("start_time must use HH:MM format")
    try:
        hour_text, minute_text = start_time.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except ValueError as exc:
        raise ValueError("start_time must use HH:MM format") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("start_time must be a valid 24-hour HH:MM time")
    start_minutes = hour * 60 + minute

    if seed is None:
        seed = random.SystemRandom().randrange(2**31)

    store = CampaignStore.create(
        campaigns_dir,
        slug=slug,
        name=name,
        death_mode=death_mode,
        rng_seed=seed,
        skeleton=skeleton,
    )
    with store.transaction():
        store.update_world_clock(day=start_day, minutes=start_minutes)
        if starting_region:
            for loc in starting_region.get("locations", []):
                store.upsert_location(
                    loc["slug"], loc["name"], loc["description"], loc.get("region")
                )
            for npc in starting_region.get("npcs", []):
                store.upsert_npc(
                    npc["name"],
                    npc.get("disposition", "neutral"),
                    npc.get("location_slug"),
                    npc.get("notes", {}),
                )
        inputs = {
            "slug": slug,
            "name": name,
            "death_mode": death_mode,
            "skeleton": skeleton,
            "starting_region": starting_region,
            "seed": seed,
            "start_day": start_day,
            "start_time": start_time,
        }
        result = CommandResult(
            ok=True,
            command="create_campaign",
            digest=f"Campaign '{name}' created on day {start_day}, {start_time}",
            data={"slug": slug, "clock": store.world_clock()},
        )
        store.append_event(
            command="create_campaign", inputs=inputs, result=result.model_dump(), rolls=[]
        )

    return CommandContext(
        store=store, roller=RecordingRoller(seed), rules=RulesDB(rules_db_path)
    )


@command("get_campaign_brief")
def get_campaign_brief(ctx: CommandContext, **kwargs) -> CommandResult:
    meta = ctx.store.campaign_meta()
    clock = ctx.store.world_clock()

    party = []
    for char in ctx.store.party():
        res = ctx.store.get_resources(char["id"])
        party.append({
            "name": char["name"],
            "role": char["role"],
            "class_slug": char["class_slug"],
            "level": char["level"],
            "xp": char["xp"],
            "hp": res["hp"],
            "max_hp": char["max_hp"],
            "conditions": res["conditions"],
            "status": char["status"],
            "spell_slots": res["spell_slots"],
        })

    recap = ctx.store.latest_recap()
    combat_active = bool(ctx.store.combat()["active"])
    data = {
        "campaign": {
            "name": meta["name"],
            "slug": meta["slug"],
            "edition": meta["edition"],
            "death_mode": meta["death_mode"],
        },
        "skeleton": meta["skeleton"],
        "clock": clock,
        "scene": clock["scene"],
        "party": party,
        "quests": ctx.store.quests(),
        "recap": recap["content"] if recap else None,
        "combat_active": combat_active,
    }
    digest = (
        f"Campaign brief: {len(party)} party members, day {clock['day']}, "
        f"combat {'active' if combat_active else 'inactive'}"
    )
    return CommandResult(ok=True, command="get_campaign_brief", digest=digest, data=data)


@command("open_campaign")
def open_campaign(ctx: CommandContext, slug: str, **kwargs) -> CommandResult:
    """Open an existing campaign (rehydrating its brief) as the active context.

    The MCP server (and `dm resume`) build the campaign context first, then
    run this through the registry so every session start is a first-class
    audit event (TVA-26): logged under its own name, with real event_ids.
    """
    meta = ctx.store.campaign_meta()
    if slug != meta["slug"]:
        return refuse(
            "open_campaign",
            f"slug {slug!r} does not match the open campaign {meta['slug']!r}",
        )
    brief = get_campaign_brief(ctx)
    return CommandResult(
        ok=True, command="open_campaign",
        digest=f"Campaign {slug} opened — {brief.digest}",
        data=brief.data,
    )


@command("end_session")
def end_session(ctx: CommandContext, recap: str, **kwargs) -> CommandResult:
    if not recap.strip():
        return refuse("end_session", "recap must not be empty")
    ctx.store.add_recap("session_end", recap)
    return CommandResult(
        ok=True, command="end_session", digest="Session ended; recap saved",
        data={"recap": recap},
    )


@command("checkpoint")
def checkpoint(ctx: CommandContext, content: str, **kwargs) -> CommandResult:
    if not content.strip():
        return refuse("checkpoint", "content must not be empty")
    ctx.store.add_recap("checkpoint", content)
    return CommandResult(
        ok=True, command="checkpoint", digest="Checkpoint saved", gm_only=True,
        data={"content": content},
    )


@command("list_recaps")
def list_recaps(ctx: CommandContext, **kwargs) -> CommandResult:
    recaps = [
        {"kind": r["kind"], "content": r["content"], "created_at": r["created_at"]}
        for r in ctx.store.recaps()
    ]
    return CommandResult(
        ok=True, command="list_recaps", digest=f"{len(recaps)} recap(s)",
        gm_only=True, data={"recaps": recaps},
    )


_EVENTS_TAIL_MAX = 100


@command("get_events")
def get_events(ctx: CommandContext, limit: int = 20, **kwargs) -> CommandResult:
    if limit < 1:
        return refuse("get_events", "limit must be positive")
    limit = min(limit, _EVENTS_TAIL_MAX)
    events = ctx.store.events_tail(limit)
    return CommandResult(
        ok=True, command="get_events",
        digest=f"Last {len(events)} event(s)", gm_only=True,
        data={"events": events, "limit": limit},
    )
