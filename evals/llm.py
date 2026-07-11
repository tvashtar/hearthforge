"""One-shot LLM completion with headless fallback.

Uses the plain anthropic SDK when API credentials exist in the environment;
otherwise falls back to a claude-agent-sdk one-shot query(), which resolves
Claude Code's own OAuth session.
"""

from __future__ import annotations

import asyncio
import os

import anthropic

# Full model ids -> claude CLI aliases; family aliases pass through untouched.
_CLI_ALIASES = {
    "claude-haiku-4-5": "haiku",
    "claude-opus-4-8": "opus",
}


def have_api_creds() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


async def _query_once(model: str, system: str, user: str) -> str:
    from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query

    options = ClaudeAgentOptions(
        model=_CLI_ALIASES.get(model, model),
        system_prompt=system,
        tools=[],
        setting_sources=[],
        permission_mode="bypassPermissions",
        max_turns=1,
    )
    parts: list[str] = []
    async for message in query(prompt=user, options=options):
        if isinstance(message, AssistantMessage):
            parts.extend(b.text for b in message.content if isinstance(b, TextBlock))
    return "\n".join(parts).strip()


def complete(model: str, system: str, user: str, max_tokens: int = 1024) -> str:
    """Return the text completion for a single system+user exchange."""
    if have_api_creds():
        response = anthropic.Anthropic().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return next(b.text for b in response.content if b.type == "text").strip()
    return asyncio.run(_query_once(model, system, user))
