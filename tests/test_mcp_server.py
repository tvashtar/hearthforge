"""In-process tests for the MCP call_tool handler: lifecycle-tool context
hygiene (TVA-13) and crash surfacing/auditing (TVA-12).

These drive the registered CallToolRequest handler directly (no stdio
subprocess) so the tests can observe the server-side CommandContext objects.
"""

import json
import sqlite3

import pytest
from mcp import types

import dm_engine.mcp.server as server_mod
from dm_engine.commands import registry
from dm_engine.mcp.server import build_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _call(server, name: str, arguments: dict) -> types.CallToolResult:
    handler = server.request_handlers[types.CallToolRequest]
    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    return (await handler(req)).root


def _campaign_args(slug: str) -> dict:
    return {"slug": slug, "name": slug.title(), "death_mode": "narrative",
            "skeleton": {"premise": "test"}, "seed": 7}


@pytest.mark.anyio
async def test_create_campaign_response_surfaces_start_clock(tmp_path, rules_path):
    """The create_campaign envelope must carry the logged digest (start
    day/time) and the resulting clock, not a rebuilt summary (TVA-48)."""
    server = build_server(tmp_path / "campaigns", rules_path)
    args = _campaign_args("dusk") | {"start_day": 3, "start_time": "18:30"}

    result = await _call(server, "create_campaign", args)
    assert result.isError is False
    envelope = json.loads(result.content[0].text)
    assert envelope["digest"] == "Campaign 'Dusk' created on day 3, 18:30"
    assert (envelope["data"]["clock"]["day"], envelope["data"]["clock"]["minutes"]) == (
        3, 18 * 60 + 30,
    )


@pytest.mark.anyio
async def test_lifecycle_tools_close_the_prior_context(
    tmp_path, rules_path, monkeypatch
):
    """create_campaign/open_campaign must close the outgoing context's SQLite
    connection instead of leaking it when they swap in the new one."""
    server = build_server(tmp_path / "campaigns", rules_path)

    contexts = []
    real_bootstrap = server_mod.bootstrap_campaign
    real_open = server_mod.open_campaign_context

    def spy_bootstrap(*args, **kwargs):
        ctx = real_bootstrap(*args, **kwargs)
        contexts.append(ctx)
        return ctx

    def spy_open(*args, **kwargs):
        ctx = real_open(*args, **kwargs)
        contexts.append(ctx)
        return ctx

    monkeypatch.setattr(server_mod, "bootstrap_campaign", spy_bootstrap)
    monkeypatch.setattr(server_mod, "open_campaign_context", spy_open)

    first = await _call(server, "create_campaign", _campaign_args("one"))
    assert first.isError is False
    second = await _call(server, "create_campaign", _campaign_args("two"))
    assert second.isError is False

    # First context's connection is closed, the replacement's is live.
    with pytest.raises(sqlite3.ProgrammingError):
        contexts[0].store.conn.execute("SELECT 1")
    contexts[1].store.conn.execute("SELECT 1")

    third = await _call(server, "open_campaign", {"slug": "one"})
    assert third.isError is False
    with pytest.raises(sqlite3.ProgrammingError):
        contexts[1].store.conn.execute("SELECT 1")
    contexts[2].store.conn.execute("SELECT 1")


def _mcp_boom(ctx, **kwargs):
    raise KeyError("damage_type")


@pytest.mark.anyio
async def test_crash_surfaces_command_and_exception_class(tmp_path, rules_path):
    """A handler crash must reach the MCP client naming the command and the
    exception class (not just the bare exception text), leave a crash event in
    the audit log, and keep the session usable."""
    registry._COMMANDS["_test_mcp_boom"] = _mcp_boom
    try:
        campaigns_dir = tmp_path / "campaigns"
        server = build_server(campaigns_dir, rules_path)
        created = await _call(server, "create_campaign", _campaign_args("smoke"))
        assert created.isError is False

        crash = await _call(server, "_test_mcp_boom", {"spell": "sleep"})
        assert crash.isError is True
        message = crash.content[0].text
        assert "_test_mcp_boom" in message
        assert "KeyError" in message and "damage_type" in message

        # The audit log has no hole: the crash landed as a committed event.
        conn = sqlite3.connect(campaigns_dir / "smoke" / "campaign.sqlite")
        try:
            row = conn.execute(
                "SELECT command, result FROM event_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        assert row[0] == "_test_mcp_boom"
        assert json.loads(row[1])["data"]["exception_type"] == "KeyError"

        # The session survived: the rebuilt context serves the next command.
        after = await _call(server, "lookup_rule", {"query": "grappling"})
        assert after.isError is False
        assert json.loads(after.content[0].text)["ok"] is True
    finally:
        registry._COMMANDS.pop("_test_mcp_boom", None)
