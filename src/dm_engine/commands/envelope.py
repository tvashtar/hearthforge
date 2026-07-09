"""FC-1: the command result envelope. MCP and CLI serialize it verbatim."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CommandResult(BaseModel):
    ok: bool
    command: str
    refusal: str | None = None
    digest: str
    data: dict[str, Any]
    gm_only: bool = False
    event_ids: list[int] = []


def refuse(command: str, reason: str) -> CommandResult:
    """Structured refusal helper — the ONLY way handlers report illegal actions."""
    return CommandResult(ok=False, command=command, refusal=reason,
                         digest=f"Refused: {reason}", data={})
