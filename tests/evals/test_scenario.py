import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

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
    level, spells_known = db.execute(
        "SELECT level, spells_known FROM characters WHERE name = 'Brother Aldric'"
    ).fetchone()
    assert level == 3  # TVA-34/TVA-43: no longer stuck at level 1 with no spells
    assert {"bless", "healing-word", "cure-wounds", "sacred-flame", "guiding-bolt"} <= (
        set(json.loads(spells_known))
    )


def test_build_campaign_raises_on_create_character_refusal(tmp_path, rules_path):
    sc = load_scenario(SCENARIO)
    bad_member = {**sc.party[1], "spells_known": ["not-a-real-spell"]}
    bad_sc = replace(sc, party=[sc.party[0], bad_member])
    with pytest.raises(RuntimeError, match="not-a-real-spell"):
        build_campaign(bad_sc, tmp_path, rules_path, slug="eval-bad", seed=1234)
