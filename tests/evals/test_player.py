from evals import player
from evals.player import build_player_prompt
from evals.scenario import Beat, Scenario


def _scenario() -> Scenario:
    return Scenario(
        name="T", premise="p", player_persona="You are Kira. Be blunt.",
        pc_name="Kira", party=[], starting_region={}, quest={}, scene={}, beats=[],
    )


def _beat() -> Beat:
    return Beat(id="b1", goal="Ask the innkeeper about the caravan.",
                done_when={"command": "skill_check"}, notes="Report 17 on a d20.")


def test_prompt_contains_persona_goal_and_notes():
    system, user = build_player_prompt(_scenario(), _beat(), ["The inn is warm."])
    assert "You are Kira" in system
    assert "Ask the innkeeper" in user
    assert "Report 17" in user
    assert "The inn is warm." in user


def test_narration_is_truncated_to_recent_tail():
    narration = [f"chunk {i} " + "x" * 500 for i in range(50)]
    _, user = build_player_prompt(_scenario(), _beat(), narration)
    assert len(user) < 9000
    assert "chunk 49" in user      # newest kept
    assert "chunk 0 " not in user  # oldest dropped


def test_player_message_never_starts_with_slash(monkeypatch):
    """A leading "/" is intercepted by Claude Code slash-command parsing
    before the DM model ever sees the message (TVA-33)."""
    monkeypatch.setattr(player.llm, "complete", lambda *a, **k: "/roll")
    msg = player.next_player_message(None, _scenario(), _beat(), [])
    assert not msg.startswith("/")
    assert "roll" in msg
