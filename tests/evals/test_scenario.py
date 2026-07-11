import sqlite3
from pathlib import Path

from evals.scenario import build_campaign, load_scenario

SCENARIO = Path(__file__).parents[2] / "evals" / "scenarios" / "caravan_ambush.yaml"


def test_load_scenario_parses_beats_in_order():
    sc = load_scenario(SCENARIO)
    assert sc.pc_name == "Kira"
    assert [b.id for b in sc.beats][:2] == ["question-innkeeper", "buy-supplies"]
    assert sc.beats[7].done_when == {"command": "attack", "ok": False}
    assert all(b.max_player_messages > 0 for b in sc.beats)


def test_build_campaign_creates_identical_starting_state(tmp_path, rules_path):
    sc = load_scenario(SCENARIO)
    build_campaign(sc, tmp_path, rules_path, slug="eval-t", seed=1234)
    db = sqlite3.connect(tmp_path / "eval-t" / "campaign.sqlite")
    npcs = {r[0] for r in db.execute("SELECT name FROM npcs")}
    assert {"Marla Underbough", "Guildmaster Fenn"} <= npcs
    chars = {r[0] for r in db.execute("SELECT name FROM characters")}
    assert {"Kira", "Brother Aldric"} <= chars
    quests = db.execute("SELECT COUNT(*) FROM quests").fetchone()[0]
    assert quests >= 1
