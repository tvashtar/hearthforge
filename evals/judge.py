"""Layer-2 grading: a fixed, blind judge scores each transcript on the rubric."""

from __future__ import annotations

import json
import re

import anthropic
from pydantic import BaseModel, Field

from evals import llm
from evals.cells import ABILITY_ORDER

JUDGE_MODEL = "claude-opus-4-8"


class DimensionScore(BaseModel):
    score: int = Field(ge=1, le=5)
    justification: str


class JudgeScores(BaseModel):
    narrative_quality: DimensionScore
    mechanical_fidelity: DimensionScore
    ruling_quality: DimensionScore
    player_experience: DimensionScore
    overall_comments: str


def anonymize(text: str, model_ids: list[str]) -> str:
    for mid in model_ids:
        if mid:
            text = text.replace(mid, "[MODEL]")
    for alias in ABILITY_ORDER:
        text = re.sub(rf"\b{alias}\b", "[MODEL]", text, flags=re.IGNORECASE)
    return text


_RUBRIC = """You are grading an AI Dungeon Master's play session transcript. The DM was
required to follow the skill document below exactly. Score each dimension 1-5
(5 = excellent) and cite specific transcript moments in each justification.

1. narrative_quality: prose, pacing, NPC voice, continuity with established facts.
2. mechanical_fidelity: never states a mechanical number without a preceding tool
   result; treats refusals as steering (narrates around them, never invents
   outcomes); keeps gm_only material hidden.
3. ruling_quality: dm_ruling rationales are sensible; Tier-2 spells get a
   follow-up ruling; improvised NPCs/locations/quests are persisted via commands.
4. player_experience: responsive to player intent; prompts the player for their
   own dice per the etiquette; keeps stakes and options clear.

You do not know which model produced this transcript. Grade only what is here."""


_JSON_ONLY = (
    "\n\nReply with ONLY a JSON object matching this schema, no prose or markdown "
    "fences: {\"narrative_quality\": {\"score\": <1-5 int>, \"justification\": str}, "
    "\"mechanical_fidelity\": {...}, \"ruling_quality\": {...}, "
    "\"player_experience\": {...}, \"overall_comments\": str}"
)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text.strip())
    return text.strip()


def judge_transcript(
    client: anthropic.Anthropic | None,
    transcript_text: str,
    scenario_yaml: str,
    skill_text: str,
) -> JudgeScores | None:
    user = (
        f"## The skill the DM must follow\n{skill_text}\n\n"
        f"## The scenario being played\n{scenario_yaml}\n\n"
        f"## Transcript\n{transcript_text}"
    )
    use_api = client is not None and llm.have_api_creds()
    for _ in range(2):  # one retry on malformed output
        try:
            if use_api:
                response = client.messages.parse(
                    model=JUDGE_MODEL,
                    max_tokens=4000,
                    thinking={"type": "adaptive"},
                    output_config={"effort": "high"},
                    system=_RUBRIC,
                    messages=[{"role": "user", "content": user}],
                    output_format=JudgeScores,
                )
                if response.parsed_output is not None:  # refusal/truncation -> retry
                    return response.parsed_output
            else:
                text = llm.complete(JUDGE_MODEL, _RUBRIC + _JSON_ONLY, user, 4000)
                return JudgeScores.model_validate(json.loads(_strip_fences(text)))
        except Exception:
            continue
    return None
