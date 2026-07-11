import asyncio
import json

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from evals.runner import RunResult, TranscriptWriter, _dm_turn, dm_options


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
