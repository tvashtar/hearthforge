import asyncio
import json
from pathlib import Path

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from dm_engine.commands import registry
from evals.cells import Cell
from evals.runner import (
    MAX_HANDSHAKE_ATTEMPTS,
    RunResult,
    TranscriptWriter,
    _dm_turn,
    _wait_for_mcp_ready,
    dm_options,
    run_cell,
)
from evals.scenario import Scenario


def test_transcript_writer_appends_jsonl(tmp_path):
    path = tmp_path / "transcript.jsonl"
    w = TranscriptWriter(path)
    w.write({"type": "player_message", "text": "hi"})
    w.write({"type": "dm_text", "text": "hello"})
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert [x["type"] for x in lines] == ["player_message", "dm_text"]


def test_dm_options_pins_tool_surface_and_cell(tmp_path):
    skill = "IRON RULES..."
    opts = dm_options(model="opus", effort="medium", repo_root=tmp_path, skill_text=skill)
    assert opts.model == "opus"
    assert opts.effort == "medium"
    assert opts.tools == []                      # no built-in tools
    assert opts.permission_mode == "bypassPermissions"
    assert opts.setting_sources == []            # deterministic context
    assert "dm-engine" in opts.mcp_servers
    assert opts.system_prompt["append"].endswith(skill)


class _StubClient:
    """Replays canned SDK messages; shape-compatible with ClaudeSDKClient."""

    def __init__(self, messages):
        self._messages = messages

    async def query(self, prompt):
        pass

    async def receive_response(self):
        for m in self._messages:
            yield m


def test_dm_turn_records_successful_tool_results(tmp_path):
    envelope = json.dumps({"ok": True, "digest": "Kira hits", "gm_only": False})
    messages = [
        AssistantMessage(
            model="m",
            content=[
                TextBlock(text="Rolling..."),
                ToolUseBlock(id="tu_1", name="mcp__dm-engine__attack",
                             input={"attacker": "Kira", "target": "bandit"}),
            ],
        ),
        UserMessage(content=[
            ToolResultBlock(tool_use_id="tu_1", is_error=None,
                            content=[{"type": "text", "text": envelope}]),
        ]),
        AssistantMessage(model="m", content=[TextBlock(text="You hit!")]),
        ResultMessage(subtype="success", duration_ms=10, duration_api_ms=8,
                      is_error=False, num_turns=1, session_id="s",
                      usage={"output_tokens": 5}),
    ]
    transcript = TranscriptWriter(tmp_path / "t.jsonl")
    result = RunResult(cell=None)
    narration = asyncio.run(
        _dm_turn(_StubClient(messages), "I attack", transcript, result)
    )
    entries = [json.loads(x) for x in (tmp_path / "t.jsonl").read_text().splitlines()]
    kinds = [e["type"] for e in entries]
    assert kinds == ["player_message", "dm_text", "tool_call", "tool_result", "dm_text"]
    call = entries[2]
    assert call == {"type": "tool_call", "id": "tu_1",
                    "name": "mcp__dm-engine__attack",
                    "input": {"attacker": "Kira", "target": "bandit"}}
    res = entries[3]
    assert res["tool_use_id"] == "tu_1"
    assert res["is_error"] is False
    # full structured envelope, untruncated
    assert res["content"] == [{"type": "text", "text": envelope}]
    assert narration == ["Rolling...", "You hit!"]
    assert result.turns[0]["usage"] == {"output_tokens": 5}


# --- MCP readiness gate: the opening prompt must not race tool registration ---
# (Observed in the 20260712-022855 matrix: first API requests went out with no
# dm-engine tools, so the DM narrated pseudo tool calls as text and two cells
# burned all handshake attempts in <8s.)


def _mcp_status(status: str, *, tools: bool = True, error: str | None = None) -> dict:
    server: dict = {"name": "dm-engine", "status": status}
    if tools:
        server["tools"] = [{"name": "mcp__dm-engine__open_campaign"}]
    if error:
        server["error"] = error
    return {"mcpServers": [server]}


class _StatusClient:
    """Yields one canned get_mcp_status response per call (last one repeats)."""

    def __init__(self, statuses: list[dict]):
        self._statuses = statuses
        self.status_calls = 0

    async def get_mcp_status(self):
        self.status_calls += 1
        i = min(self.status_calls - 1, len(self._statuses) - 1)
        return self._statuses[i]


def test_wait_for_mcp_ready_polls_until_tools_are_registered():
    client = _StatusClient(
        [_mcp_status("pending", tools=False), _mcp_status("connected", tools=False),
         _mcp_status("connected")]
    )
    asyncio.run(_wait_for_mcp_ready(client, poll_interval_s=0))
    assert client.status_calls == 3  # pending, connected-but-toolless, ready


def test_wait_for_mcp_ready_raises_on_server_failure():
    client = _StatusClient([_mcp_status("failed", tools=False, error="uv exploded")])
    try:
        asyncio.run(_wait_for_mcp_ready(client, poll_interval_s=0))
    except RuntimeError as exc:
        assert "uv exploded" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for failed MCP server")


def test_wait_for_mcp_ready_raises_on_timeout():
    client = _StatusClient([_mcp_status("pending", tools=False)])
    try:
        asyncio.run(_wait_for_mcp_ready(client, timeout_s=0, poll_interval_s=0))
    except RuntimeError as exc:
        assert "not ready" in str(exc)
    else:
        raise AssertionError("expected RuntimeError on readiness timeout")


# --- TVA-45: opening handshake, driven against a real synthetic campaign DB ---


def _bare_scenario() -> Scenario:
    """No party/beats: only the handshake path is under test here."""
    return Scenario(
        name="Handshake Test", premise="p", player_persona="pp", pc_name="Kira",
        party=[], starting_region={}, quest={"name": "Test Quest"},
        scene={"description": "A quiet room."}, beats=[],
    )


class _FakeSDKClient:
    """Async-context-manager stub standing in for ClaudeSDKClient.

    `on_query(n)` runs as a side effect of the nth `query()` call (1-indexed)
    so tests can simulate "the DM actually called open_campaign" by writing
    that event straight to the synthetic campaign DB through the real
    registry — never a fabricated event-log row.
    """

    def __init__(self, options=None, *, on_query=None):
        self.calls = 0
        self.events: list[str] = []  # interleaving of status polls and queries
        self._on_query = on_query or (lambda n: None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_mcp_status(self):
        self.events.append("status")
        return _mcp_status("connected")

    async def query(self, prompt):
        self.calls += 1
        self.events.append("query")
        self._on_query(self.calls)

    async def receive_response(self):
        return
        yield  # pragma: no cover - makes this an async generator


def _patch_common(monkeypatch, on_query=None):
    monkeypatch.setattr("evals.runner.anthropic.Anthropic", lambda: object())
    client_holder = {}

    def factory(options=None):
        client = _FakeSDKClient(options, on_query=on_query)
        client_holder["client"] = client
        return client

    monkeypatch.setattr("evals.runner.ClaudeSDKClient", factory)
    return client_holder


def test_handshake_failure_aborts_before_any_beats(monkeypatch, tmp_path, rules_path):
    client_holder = _patch_common(monkeypatch)  # open_campaign never called
    result = asyncio.run(
        run_cell(
            Cell("haiku", "medium"), _bare_scenario(),
            repo_root=Path(__file__).parents[2], campaigns_dir=tmp_path / "campaigns",
            rules_db_path=rules_path, bundle_dir=tmp_path / "bundle", seed=1,
        )
    )
    assert result.error == "handshake failed"
    assert result.beats_completed == [] and result.beats_failed == []
    # opening turn + (MAX_HANDSHAKE_ATTEMPTS - 1) nudges, never more
    assert client_holder["client"].calls == MAX_HANDSHAKE_ATTEMPTS
    # the readiness gate must run before the opening prompt is ever sent
    assert client_holder["client"].events[0] == "status"
    timing = json.loads((tmp_path / "bundle" / "timing.json").read_text())
    assert timing["error"] == "handshake failed"


def test_handshake_succeeds_after_a_nudge_and_beats_proceed(monkeypatch, tmp_path, rules_path):
    slug_holder = {}

    def on_query(n):
        if n == 2:  # first nudge: simulate the DM now calling open_campaign
            ctx = registry.open_campaign_context(
                slug_holder["campaigns_dir"], slug_holder["slug"], slug_holder["rules_path"]
            )
            try:
                ok = registry.execute("open_campaign", ctx, slug=slug_holder["slug"])
                assert ok.ok
            finally:
                ctx.store.close()

    client_holder = _patch_common(monkeypatch, on_query=on_query)
    campaigns_dir = tmp_path / "campaigns"
    slug_holder.update(
        campaigns_dir=campaigns_dir, rules_path=rules_path, slug="eval-haiku-medium-1"
    )
    result = asyncio.run(
        run_cell(
            Cell("haiku", "medium"), _bare_scenario(),
            repo_root=Path(__file__).parents[2], campaigns_dir=campaigns_dir,
            rules_db_path=rules_path, bundle_dir=tmp_path / "bundle", seed=1,
        )
    )
    assert result.error is None
    assert result.complete  # no beats in the bare scenario, none failed
    assert client_holder["client"].calls == 2  # opening turn + one nudge, then it proceeded
