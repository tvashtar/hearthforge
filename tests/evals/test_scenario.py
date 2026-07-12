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
    assert sc.beats[5].id == "illegal-action"
    # TVA-65: the illegal-action beat now passes on either the engine
    # "cannot reach" refusal or a correct in-narration refusal (abstention).
    any_of = sc.beats[5].done_when["any_of"]
    assert {"command": "attack", "ok": False, "refusal_contains": "cannot reach"} in any_of
    assert any("none_of" in clause for clause in any_of)
    assert all(b.max_player_messages > 0 for b in sc.beats)


def test_tier2_bless_beat_removed():
    # TVA-66: the tier2-spell (Bless) beat depended on unpredictable combat
    # state and penalised a DM that correctly healed a dying PC — removed.
    sc = load_scenario(SCENARIO)
    assert "tier2-spell" not in {b.id for b in sc.beats}


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
    assert db.execute("SELECT day, minutes FROM world_clock").fetchone() == (1, 18 * 60)
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


def test_build_campaign_seeds_party_items(tmp_path, rules_path):
    sc = load_scenario(SCENARIO)
    kira_with_items = {**sc.party[0], "items": [{"item": "gold pieces", "quantity": 60}]}
    sc_with_items = replace(sc, party=[kira_with_items, sc.party[1]])
    build_campaign(sc_with_items, tmp_path, rules_path, slug="eval-items", seed=1234)
    db = sqlite3.connect(tmp_path / "eval-items" / "campaign.sqlite")
    cid = db.execute("SELECT id FROM characters WHERE name = 'Kira'").fetchone()[0]
    row = db.execute(
        "SELECT quantity FROM inventory WHERE character_id = ? AND name = 'gold pieces'",
        (cid,),
    ).fetchone()
    assert row is not None
    assert row[0] == 60
    add_item_events = db.execute(
        "SELECT COUNT(*) FROM event_log WHERE command = 'add_item'"
    ).fetchone()[0]
    assert add_item_events == 1


def test_build_campaign_raises_on_add_item_refusal(tmp_path, rules_path):
    sc = load_scenario(SCENARIO)
    bad_member = {**sc.party[0], "items": [{"item": "gold pieces", "quantity": 0}]}
    bad_sc = replace(sc, party=[bad_member, sc.party[1]])
    with pytest.raises(RuntimeError, match="add_item failed"):
        build_campaign(bad_sc, tmp_path, rules_path, slug="eval-bad-item", seed=1234)
