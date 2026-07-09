"""Pre-validation campaign rows are normalized once on open. Idempotent;
rows that can't be fixed are left for a clean on-use refusal (Task 6)."""

import json

import pytest

from dm_engine.content.lookup import RulesDB
from dm_engine.state.migrate import normalize_characters
from dm_engine.state.store import CampaignStore


@pytest.fixture()
def old_campaign(tmp_path):
    store = CampaignStore.create(
        tmp_path / "campaigns", slug="old", name="Old", death_mode="narrative",
        rng_seed=7, skeleton={"premise": "t"},
    )
    # Insert a row exactly as the pre-fix engine stored it: monster-style
    # attacks, `saving_throws` key, underscore slugs.
    store.conn.execute(
        "INSERT INTO characters (name, role, class_slug, race_slug, level,"
        " abilities, max_hp, ac, speed, proficiencies, attacks, spells_known)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("Algarve", "pc", "rogue", "wood-elf", 1,
         json.dumps({"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10}),
         9, 15, 35,
         json.dumps({"saving_throws": ["dex", "int"],
                     "skills": ["stealth", "acrobatics"],
                     "expertise": ["stealth", "thieves_tools"],
                     "tools": ["thieves_tools"], "languages": ["common"]}),
         json.dumps([
             {"name": "Shortsword", "attack_bonus": 6, "damage": "1d6+4",
              "damage_type": "piercing"},
             {"name": "Void Lash", "attack_bonus": 9, "damage": "6d6+4",
              "damage_type": "necrotic"},  # no SRD weapon — must survive untouched
         ]),
         json.dumps([])),
    )
    store.conn.execute(
        "INSERT INTO resources (character_id, hp, hit_dice_remaining, spell_slots)"
        " VALUES (1, 9, 1, '{}')",
    )
    store.conn.commit()
    yield store
    store.close()


def test_normalizer_fixes_old_rows(old_campaign, rules_path):
    changes = normalize_characters(old_campaign, RulesDB(rules_path))
    assert changes  # something was fixed
    char = old_campaign.get_character("Algarve")
    profs = char["proficiencies"]
    assert profs["saves"] == ["dex", "int"]
    assert "saving_throws" not in profs
    assert profs["tools"] == ["thieves-tools"]           # slug normalized
    assert profs["expertise"] == ["stealth", "thieves-tools"]
    by_name = {a["name"]: a for a in char["attacks"]}
    sword = by_name["Shortsword"]
    assert (sword["ability"], sword["damage"], sword["source"]) == (
        "dex", "1d6", "srd:shortsword")                  # re-derived from SRD
    assert by_name["Void Lash"] == {                     # untouched, refuses on use
        "name": "Void Lash", "attack_bonus": 9, "damage": "6d6+4",
        "damage_type": "necrotic"}


def test_normalizer_is_idempotent(old_campaign, rules_path):
    rules = RulesDB(rules_path)
    normalize_characters(old_campaign, rules)
    assert normalize_characters(old_campaign, rules) == []


def test_normalizer_logs_an_audit_event_when_it_fixes_rows(old_campaign, rules_path):
    rules = RulesDB(rules_path)
    before = old_campaign.event_count()
    changes = normalize_characters(old_campaign, rules)
    assert changes
    assert old_campaign.event_count() == before + 1
    row = old_campaign.conn.execute(
        "SELECT command, result FROM event_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["command"] == "migrate_normalize"
    result = json.loads(row["result"])
    assert result["ok"] is True
    assert result["data"]["notes"] == changes


def test_normalizer_second_run_adds_no_event(old_campaign, rules_path):
    rules = RulesDB(rules_path)
    normalize_characters(old_campaign, rules)
    before = old_campaign.event_count()
    changes = normalize_characters(old_campaign, rules)
    assert changes == []
    assert old_campaign.event_count() == before


def test_normalizer_noop_on_valid_rows(tmp_path, rules_path):
    store = CampaignStore.create(
        tmp_path / "c", slug="new", name="N", death_mode="narrative",
        rng_seed=7, skeleton={"premise": "t"},
    )
    assert normalize_characters(store, RulesDB(rules_path)) == []
    store.close()
