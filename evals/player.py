"""Beat-scripted player agent: one fixed cheap model, constant across cells."""

from __future__ import annotations

import anthropic

from evals import llm
from evals.scenario import Beat, Scenario

PLAYER_MODEL = "claude-haiku-4-5"
MAX_NARRATION_CHARS = 6000

_SYSTEM_TEMPLATE = """{persona}

You are the PLAYER in a dungeons-and-dragons game; the other party is the DM.
Reply with your character's next message only: 1-3 sentences, first person,
no narration of outcomes, no out-of-character commentary. Always push toward
your current goal. If the DM asks a question, answer it and keep pushing."""

_USER_TEMPLATE = """Recent DM narration (newest last):
{narration}

Your current goal: {goal}
{notes}
Write your next message to the DM."""


def build_player_prompt(
    scenario: Scenario, beat: Beat, narration: list[str]
) -> tuple[str, str]:
    tail: list[str] = []
    total = 0
    for chunk in reversed(narration):
        total += len(chunk)
        if total > MAX_NARRATION_CHARS and tail:  # always keep the newest chunk
            break
        tail.append(chunk)
    text = "\n\n".join(reversed(tail)) or "(the session is just beginning)"
    notes = f"Dice notes: {beat.notes}\n" if beat.notes else ""
    system = _SYSTEM_TEMPLATE.format(persona=scenario.player_persona.strip())
    user = _USER_TEMPLATE.format(narration=text, goal=beat.goal.strip(), notes=notes)
    return system, user


def next_player_message(
    client: anthropic.Anthropic | None, scenario: Scenario, beat: Beat, narration: list[str]
) -> str:
    del client  # kept for signature stability; evals.llm resolves credentials itself
    system, user = build_player_prompt(scenario, beat, narration)
    return llm.complete(PLAYER_MODEL, system, user, 300)
