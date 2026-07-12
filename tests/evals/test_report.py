from evals.judge import JudgeScores
from evals.report import render_report

DIM = {"score": 4, "justification": "solid"}


def _result(cell="haiku-medium", judge=True):
    return {
        "cell": cell,
        "resolved_model": "claude-haiku-4-5",
        "wall_clock_s": 812.0,
        "median_turn_s": 24.5,
        "output_tokens": 20000,
        "beats_completed": ["a", "b"],
        "beats_failed": [],
        "error": None,
        "metrics": {"refusals": 2, "crashes": 0, "tool_calls_per_player_message": 3.1,
                    "refusal_retry_loops": 0, "orphaned_tier2": 0,
                    "schema_rejections": 0, "player_supplied_violations": 0,
                    "polling_reads": 1, "player_messages": 10, "tool_calls": 31},
        "judge": JudgeScores(
            narrative_quality=DIM, mechanical_fidelity=DIM, ruling_quality=DIM,
            player_experience=DIM, overall_comments="fine",
        ) if judge else None,
    }


def test_report_has_table_row_per_cell_and_provenance():
    md = render_report([_result(), _result(cell="opus-medium")], judge_model="claude-opus-4-8")
    assert md.count("haiku-medium") >= 1 and md.count("opus-medium") >= 1
    assert "claude-haiku-4-5" in md          # resolved id recorded
    assert "Judge: claude-opus-4-8" in md
    assert "24.5" in md and "20000" in md    # median turn latency + output tokens


def test_judge_failure_is_flagged_not_hidden():
    md = render_report([_result(judge=False)], judge_model="claude-opus-4-8")
    assert "judge-failed" in md


def test_incomplete_run_is_marked_in_beats_column():
    result = _result()
    result["error"] = "timeout"
    md = render_report([result], judge_model="claude-opus-4-8")
    assert "(INCOMPLETE: timeout)" in md


def test_beat_failures_are_listed_with_reason_and_refusal():
    result = _result()
    result["beat_failures"] = [
        {"id": "buy-supplies", "reason": "not_attempted"},
        {"id": "question-innkeeper", "reason": "refused", "refusal": "no such target"},
    ]
    md = render_report([result], judge_model="claude-opus-4-8")
    assert "`buy-supplies` (not_attempted)" in md
    assert "`question-innkeeper` (refused) — no such target" in md
