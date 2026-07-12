"""MCP server over the command registry.

Exposes every registered command as an MCP tool 1:1, plus two lifecycle
tools (`create_campaign`, `open_campaign`) that manage the active campaign
context the other tools mutate. Tool schemas are introspected from handler
signatures; results are the verbatim FC-1 envelope serialized as JSON text.

Uses the low-level `mcp.server.Server` (not FastMCP) to keep full control
over the dynamic 1:1 tool surface and the exact schemas.
"""

from __future__ import annotations

import inspect
import types
import typing
from collections.abc import Callable
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

import dm_engine.commands  # noqa: F401 — importing registers every command
from dm_engine.commands.campaign import bootstrap_campaign
from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.commands.registry import (
    CommandContext,
    execute,
    open_campaign_context,
    registered_commands,
)

_SCALARS: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _json_type(annotation: object) -> str:
    """Map a Python annotation to a JSON-schema type name.

    Handles bare `str/int/float/bool/list/dict`, parameterized `list[...]`
    / `dict[...]`, and `X | None` (returns the type of `X`). Falls back to
    ``string`` for anything unrecognized (or an unannotated parameter).
    """
    if annotation is inspect.Parameter.empty:
        return "string"

    origin = typing.get_origin(annotation)

    # X | None (both `Optional[X]` and the `X | None` union syntax): strip
    # NoneType and map the remaining member.
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return _json_type(non_none[0])
        return "string"

    # Parameterized generics: list[...] -> array, dict[...] -> object.
    if origin is list:
        return "array"
    if origin is dict:
        return "object"

    if annotation is list:
        return "array"
    if annotation is dict:
        return "object"

    if isinstance(annotation, type):
        # bool is a subclass of int — check the exact mapping table.
        return _SCALARS.get(annotation, "string")

    return "string"


def input_schema(handler: Callable[..., object]) -> dict:
    """Build a JSON-schema object from a command handler's signature.

    Drops the leading `ctx` parameter and any `**kwargs` catch-all.
    Parameters without a default are ``required``.
    """
    properties: dict[str, dict] = {}
    required: list[str] = []

    # eval_str resolves the string annotations produced by the handlers'
    # `from __future__ import annotations` back into real type objects.
    params = list(inspect.signature(handler, eval_str=True).parameters.values())
    for param in params[1:]:  # drop ctx
        if param.kind in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL):
            continue
        properties[param.name] = {"type": _json_type(param.annotation)}
        if param.default is inspect.Parameter.empty:
            required.append(param.name)

    schema: dict = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required

    # Per-parameter schema overrides (TVA-36): a handler may declare a
    # `__param_schemas__` dict attribute mapping param name -> a full JSON
    # schema for that parameter, replacing the bare introspected type. This
    # is how `dm_ruling.effects` gets a real per-op items schema instead of
    # a shapeless `{"type": "array"}`.
    overrides = getattr(handler, "__param_schemas__", None)
    if overrides:
        for name, override in overrides.items():
            if name in properties:
                properties[name] = override
    return schema


def _description(handler: Callable[..., object], name: str) -> str:
    doc = inspect.getdoc(handler)
    if doc:
        return doc.splitlines()[0].strip()
    return name


_CREATE_CAMPAIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "slug": {"type": "string"},
        "name": {"type": "string"},
        "death_mode": {"type": "string"},
        "skeleton": {"type": "object"},
        "starting_region": {"type": "object"},
        "seed": {"type": "integer"},
        "start_day": {"type": "integer", "minimum": 1},
        "start_time": {"type": "string", "pattern": "^(?:[01]\\d|2[0-3]):[0-5]\\d$"},
    },
    "required": ["slug", "name", "skeleton"],
}

def _text(result: CommandResult) -> list[TextContent]:
    return [TextContent(type="text", text=result.model_dump_json())]


def build_server(campaigns_dir: Path, rules_db_path: Path) -> Server:
    """Build an MCP server exposing the registry over an active campaign context."""
    server: Server = Server("dm-engine")
    commands = registered_commands()

    # Mutable holder for the one active campaign context (lifecycle tools set it).
    active: dict[str, CommandContext | None] = {"ctx": None}

    def activate(ctx: CommandContext) -> None:
        """Make `ctx` the active context, closing the one it replaces.

        Called with the new context already built, so a failed create/open
        leaves the previous campaign open and usable."""
        prev = active["ctx"]
        if prev is not None:
            prev.store.close()
        active["ctx"] = ctx

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        tools = [
            Tool(
                name=name,
                description=_description(handler, name),
                inputSchema=input_schema(handler),
            )
            for name, handler in commands.items()
        ]
        # open_campaign is a registered command (its schema is introspected
        # above); only create_campaign has no handler to introspect, because
        # it runs before any store exists.
        tools.append(Tool(
            name="create_campaign",
            description="Create a new campaign and make it the active context.",
            inputSchema=_CREATE_CAMPAIGN_SCHEMA,
        ))
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "create_campaign":
            ctx = bootstrap_campaign(campaigns_dir, rules_db_path, **arguments)
            activate(ctx)
            # bootstrap logged the create_campaign event itself, with the full
            # digest (start day/time) and clock; echo its id and digest so the
            # envelope matches what was recorded rather than a rebuilt summary
            # (TVA-26, TVA-48).
            logged = ctx.store.events_tail(1)[0]
            result = CommandResult(
                ok=True,
                command="create_campaign",
                digest=logged["digest"],
                data={"slug": arguments["slug"], "clock": ctx.store.world_clock()},
                event_ids=[logged["id"]],
            )
            return _text(result)

        if name == "open_campaign":
            # Build the context first (there is no open store yet), then run
            # the registered command so the session start is logged under its
            # own name with real event_ids (TVA-26).
            ctx = open_campaign_context(campaigns_dir, arguments["slug"], rules_db_path)
            activate(ctx)
            return _text(execute("open_campaign", ctx, **arguments))

        ctx = active["ctx"]
        if ctx is None:
            return _text(refuse(
                name, "no campaign open — call create_campaign or open_campaign first"
            ))

        try:
            result = execute(name, ctx, **arguments)
        except Exception as exc:
            # Handler exceptions are engine bugs: keep them visible (re-raise
            # as an MCP tool error) but rebuild the context so the session
            # stays usable after the rolled-back transaction. The registry
            # has already appended a crash event to the audit log; surface
            # the command name and exception class to the client rather than
            # the bare exception text.
            slug = ctx.store.campaign_meta()["slug"]
            ctx.store.close()
            active["ctx"] = open_campaign_context(campaigns_dir, slug, rules_db_path)
            raise RuntimeError(
                f"engine crash in command {name!r}: {type(exc).__name__}: {exc}"
            ) from exc
        return _text(result)

    return server


async def run_stdio(campaigns_dir: Path, rules_db_path: Path) -> None:
    """Run the MCP server over stdio (the transport Claude Code speaks)."""
    server = build_server(campaigns_dir, rules_db_path)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
