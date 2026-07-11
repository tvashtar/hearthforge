import pytest
from pydantic import ValidationError

from evals.judge import JudgeScores, anonymize


def test_anonymize_strips_model_ids_and_aliases():
    text = 'model "claude-opus-4-8" (alias opus) rolled via claude-haiku-4-5'
    out = anonymize(text, ["claude-opus-4-8", "claude-haiku-4-5"])
    assert "claude-opus-4-8" not in out and "claude-haiku-4-5" not in out
    assert "opus" not in out and "haiku" not in out
    assert "[MODEL]" in out


def test_judge_scores_bounds_enforced():
    dim = {"score": 5, "justification": "ok"}
    JudgeScores(narrative_quality=dim, mechanical_fidelity=dim,
                ruling_quality=dim, player_experience=dim, overall_comments="x")
    with pytest.raises(ValidationError):
        JudgeScores(narrative_quality={"score": 6, "justification": "no"},
                    mechanical_fidelity=dim, ruling_quality=dim,
                    player_experience=dim, overall_comments="x")
