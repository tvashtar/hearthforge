"""Read-only reference lookups exposed as registry commands.

These never mutate campaign state; they exist so the MCP/CLI surface can
query the seeded SRD database (`ctx.rules`) through the same envelope as
every other command, with an event row for traceability. `lookup_rule`
never refuses (an empty hit list is still `ok=True`); `lookup_monster` and
`lookup_spell` refuse on an unknown slug.
"""

from __future__ import annotations

from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.commands.registry import CommandContext, command


@command("lookup_rule")
def lookup_rule(ctx: CommandContext, query: str, limit: int = 5, **kwargs) -> CommandResult:
    hits = ctx.rules.lookup_rule(query, limit=limit)
    data = {
        "hits": [
            {
                "source": hit.source,
                "heading_path": hit.heading_path,
                "heading": hit.heading,
                "snippet": hit.snippet,
            }
            for hit in hits
        ]
    }
    digest = f"{len(hits)} rule hit(s) for {query!r}"
    return CommandResult(ok=True, command="lookup_rule", digest=digest, data=data)


@command("lookup_monster")
def lookup_monster(ctx: CommandContext, slug: str, **kwargs) -> CommandResult:
    record = ctx.rules.get_monster(slug)
    if record is None:
        return refuse("lookup_monster", f"no monster with slug {slug!r}")
    data = record.model_dump(by_alias=True)
    return CommandResult(
        ok=True, command="lookup_monster",
        digest=f"Monster: {data.get('name', slug)}", data=data, gm_only=True,
    )


@command("lookup_spell")
def lookup_spell(ctx: CommandContext, slug: str, **kwargs) -> CommandResult:
    record = ctx.rules.get_spell(slug)
    if record is None:
        return refuse("lookup_spell", f"no spell with slug {slug!r}")
    data = record.model_dump(by_alias=True)
    return CommandResult(
        ok=True, command="lookup_spell",
        digest=f"Spell: {data.get('name', slug)}", data=data,
    )
