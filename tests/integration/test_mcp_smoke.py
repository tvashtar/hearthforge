"""MCP smoke test: server starts on stdio, exposes the registry 1:1, and
round-trips commands with verbatim FC-1 envelopes."""

import json
import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from dm_engine.commands.registry import registered_commands


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_mcp_smoke(tmp_path, rules_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dm_engine.mcp", "--campaigns-dir", str(tmp_path / "campaigns"),
              "--db", str(rules_path)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tool_names = [t.name for t in (await session.list_tools()).tools]
            tools = set(tool_names)
            expected = set(registered_commands()) | {"create_campaign", "open_campaign"}
            assert tools == expected  # 1:1, nothing missing, nothing extra
            assert len(tool_names) == len(tools)  # no duplicates

            # refusal before a campaign is open — still an FC-1 envelope
            early = await session.call_tool("lookup_rule", {"query": "grappling"})
            envelope = json.loads(early.content[0].text)
            assert envelope["ok"] is False and "no campaign open" in envelope["refusal"]

            result = await session.call_tool("create_campaign", {
                "slug": "smoke", "name": "Smoke Test", "death_mode": "narrative",
                "skeleton": {"premise": "smoke"}, "seed": 7,
            })
            envelope = json.loads(result.content[0].text)
            assert envelope["ok"] is True
            assert envelope["event_ids"]  # bootstrap's create_campaign audit row

            # TVA-26: reopening logs an `open_campaign` event under its own
            # name and echoes the event id — session starts are queryable.
            result = await session.call_tool("open_campaign", {"slug": "smoke"})
            envelope = json.loads(result.content[0].text)
            assert envelope["ok"] is True and envelope["command"] == "open_campaign"
            assert envelope["event_ids"]

            result = await session.call_tool("create_character", {
                "name": "Kira", "role": "pc", "class_slug": "fighter",
                "race_slug": "human",
                "abilities": {"str": 16, "dex": 14, "con": 14, "int": 10,
                              "wis": 12, "cha": 8},
                "ac": 16,
                "proficiencies": {"skills": ["athletics"]},
                "attacks": [],
            })
            assert json.loads(result.content[0].text)["ok"] is True

            check = await session.call_tool("skill_check", {
                "character": "Kira", "skill": "athletics", "dc": 10,
                "player_value": 15,
            })
            envelope = json.loads(check.content[0].text)
            # verbatim FC-1 envelope
            assert set(envelope) == {"ok", "command", "refusal", "digest", "data",
                                     "gm_only", "event_ids"}
            assert envelope["ok"] is True and envelope["command"] == "skill_check"
            assert envelope["data"]["total"] == 20  # 15 + STR 3 + prof 2

            rule = await session.call_tool("lookup_rule", {"query": "grappling"})
            envelope = json.loads(rule.content[0].text)
            assert envelope["ok"] is True and envelope["data"]["hits"]
