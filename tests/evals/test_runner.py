import json

from evals.runner import TranscriptWriter, dm_options


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
