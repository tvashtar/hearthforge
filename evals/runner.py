"""One eval cell: a DM session driven turn-by-turn by the player agent."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

import anthropic
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from evals.cells import Cell
from evals.metrics import beat_done, max_event_id
from evals.player import next_player_message
from evals.scenario import Scenario, build_campaign

SKILL_PATH = Path(".claude/skills/dm-session/SKILL.md")


class TranscriptWriter:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    def write(self, entry: dict) -> None:
        with self.path.open("a") as f:
            f.write(json.dumps(entry) + "\n")


def dm_options(
    *, model: str, effort: str, repo_root: Path, skill_text: str
) -> ClaudeAgentOptions:
    return ClaudeAgentOptions(
        cwd=str(repo_root),
        model=model,
        effort=effort,
        permission_mode="bypassPermissions",
        setting_sources=[],  # no CLAUDE.md / project settings: deterministic context
        tools=[],  # no built-in tools; MCP tools remain available
        mcp_servers={"dm-engine": {"command": "uv", "args": ["run", "dm", "mcp"]}},
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": "You are running a solo D&D session. Follow this skill exactly:\n\n"
            + skill_text,
        },
    )


@dataclass
class RunResult:
    cell: Cell
    resolved_model: str | None = None
    beats_completed: list[str] = field(default_factory=list)
    beats_failed: list[str] = field(default_factory=list)
    complete: bool = False
    error: str | None = None
    wall_clock_s: float = 0.0
    turns: list[dict] = field(default_factory=list)


async def _dm_turn(
    client: ClaudeSDKClient, message: str, transcript: TranscriptWriter, result: RunResult
) -> list[str]:
    """Send one player message, stream the DM's full response, return narration."""
    transcript.write({"type": "player_message", "text": message})
    start = time.monotonic()
    narration: list[str] = []
    await client.query(message)
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            if result.resolved_model is None and getattr(msg, "model", None):
                result.resolved_model = msg.model
            for block in msg.content:
                if isinstance(block, TextBlock):
                    narration.append(block.text)
                    transcript.write({"type": "dm_text", "text": block.text})
                elif isinstance(block, ToolUseBlock):
                    transcript.write(
                        {"type": "tool_call", "name": block.name,
                         "input": block.input, "is_error": False}
                    )
        elif isinstance(msg, UserMessage):
            for block in msg.content if isinstance(msg.content, list) else []:
                if isinstance(block, ToolResultBlock) and block.is_error:
                    transcript.write(
                        {"type": "tool_call", "name": "(result)", "is_error": True,
                         "content": str(block.content)[:500]}
                    )
        elif isinstance(msg, ResultMessage):
            result.turns.append(
                {"wall_s": round(time.monotonic() - start, 2),
                 "duration_api_ms": msg.duration_api_ms, "usage": msg.usage}
            )
    return narration


async def run_cell(
    cell: Cell,
    scenario: Scenario,
    *,
    repo_root: Path,
    campaigns_dir: Path,
    rules_db_path: Path,
    bundle_dir: Path,
    seed: int,
    beats_limit: int | None = None,
    turn_timeout_s: float = 600,
    run_timeout_s: float = 5400,
) -> RunResult:
    slug = f"eval-{cell.slug}-{seed}"
    result = RunResult(cell=cell)
    started = time.monotonic()
    bundle_dir.mkdir(parents=True, exist_ok=True)
    transcript = TranscriptWriter(bundle_dir / "transcript.jsonl")
    db_path = campaigns_dir / slug / "campaign.sqlite"

    try:
        # everything fallible (missing API key, bad YAML, missing skill file)
        # stays inside the try so failures land in result.error and the
        # finally block still writes timing.json and cleans up.
        build_campaign(scenario, campaigns_dir, rules_db_path, slug=slug, seed=seed)
        player = anthropic.Anthropic()
        skill_text = (repo_root / SKILL_PATH).read_text()
        beats = scenario.beats[:beats_limit] if beats_limit is not None else scenario.beats
        options = dm_options(
            model=cell.model, effort=cell.effort, repo_root=repo_root, skill_text=skill_text
        )
        async with ClaudeSDKClient(options=options) as client:
            narration = await asyncio.wait_for(
                _dm_turn(
                    client,
                    f"Open the campaign '{slug}' and start the session. Set the scene "
                    "for me, then wait for my action.",
                    transcript,
                    result,
                ),
                timeout=turn_timeout_s,
            )
            for beat in beats:
                if time.monotonic() - started > run_timeout_s:
                    raise TimeoutError("run timeout")
                marker = max_event_id(db_path)
                done = False
                for _ in range(beat.max_player_messages):
                    msg = await asyncio.to_thread(
                        next_player_message, player, scenario, beat, narration
                    )
                    narration += await asyncio.wait_for(
                        _dm_turn(client, msg, transcript, result), timeout=turn_timeout_s
                    )
                    if beat_done(db_path, beat.done_when, after_id=marker):
                        done = True
                        break
                (result.beats_completed if done else result.beats_failed).append(beat.id)
        result.complete = not result.beats_failed
    except (TimeoutError, asyncio.TimeoutError):
        result.error = "timeout"
    except Exception as exc:  # cell-level failure must not sink other cells
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.wall_clock_s = round(time.monotonic() - started, 1)
        (bundle_dir / "timing.json").write_text(
            json.dumps(
                {"cell": cell.slug, "resolved_model": result.resolved_model,
                 "wall_clock_s": result.wall_clock_s, "turns": result.turns,
                 "beats_completed": result.beats_completed,
                 "beats_failed": result.beats_failed, "error": result.error},
                indent=2,
            )
        )
        if db_path.exists():
            shutil.copy2(db_path, bundle_dir / "campaign.sqlite")
        if (campaigns_dir / slug).exists():
            shutil.rmtree(campaigns_dir / slug)  # live-data rule: no scratch slugs linger
    return result
