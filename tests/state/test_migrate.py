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


def test_note_only_unknown_class_row_never_logs_events(tmp_path, rules_path):
    """A row whose only issue is an unknown class (saves underivable) has its
    note regenerated on EVERY open. That must not append a migrate_normalize
    event or report changes each time — the event audits actual rewrites; the
    informational note only rides along with one."""
    from dm_engine.models.character import AttackSpec

    store = CampaignStore.create(
        tmp_path / "c", slug="hb", name="HB", death_mode="narrative",
        rng_seed=7, skeleton={"premise": "t"},
    )
    valid_attack = AttackSpec(
        name="Claw", ability="str", damage="1d6", damage_type="slashing",
        ranged=False, range_ft=5,
    ).model_dump()
    store.conn.execute(
        "INSERT INTO characters (name, role, class_slug, race_slug, level,"
        " abilities, max_hp, ac, speed, proficiencies, attacks, spells_known)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("Grix", "pc", "homebrew-warden", "human", 1,
         json.dumps({"str": 14, "dex": 10, "con": 12, "int": 10, "wis": 10, "cha": 10}),
         10, 14, 30,
         json.dumps({"saves": [], "skills": [], "expertise": [],
                     "tools": [], "languages": []}),
         json.dumps([valid_attack]),
         json.dumps([])),
    )
    store.conn.execute(
        "INSERT INTO resources (character_id, hp, hit_dice_remaining, spell_slots)"
        " VALUES (1, 10, 1, '{}')",
    )
    store.conn.commit()

    rules = RulesDB(rules_path)
    assert normalize_characters(store, rules) == []
    assert normalize_characters(store, rules) == []
    assert store.event_count() == 0
    store.close()


def test_unknown_class_note_rides_along_with_a_real_fix(old_campaign, rules_path):
    """When a row actually gets rewritten, an accompanying unknown-class note
    is included in the audit event rather than dropped."""
    # Unknown class AND no saves anywhere -> the note fires; the remaining
    # normalizable material (underscore slugs, monster-style attacks) still
    # produces a real rewrite for the note to ride along with.
    old_campaign.conn.execute(
        "UPDATE characters SET class_slug = 'homebrew-warden',"
        " proficiencies = ? WHERE name = 'Algarve'",
        (json.dumps({"skills": ["stealth"], "expertise": [],
                     "tools": [], "languages": []}),),
    )
    old_campaign.conn.commit()

    changes = normalize_characters(old_campaign, RulesDB(rules_path))
    assert changes  # saving_throws rename + attack re-derivation still fix rows
    assert any("unknown class 'homebrew-warden'" in c for c in changes)
    row = old_campaign.conn.execute(
        "SELECT result FROM event_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert json.loads(row["result"])["data"]["notes"] == changes


def test_normalizer_second_run_adds_no_event(old_campaign, rules_path):
    rules = RulesDB(rules_path)
    normalize_characters(old_campaign, rules)
    before = old_campaign.event_count()
    changes = normalize_characters(old_campaign, rules)
    assert changes == []
    assert old_campaign.event_count() == before


def test_normalizer_noop_on_valid_rows(tmp_path, rules_path):
    from dm_engine.commands import registry
    from dm_engine.commands.registry import CommandContext, RecordingRoller

    store = CampaignStore.create(
        tmp_path / "c", slug="new", name="N", death_mode="narrative",
        rng_seed=7, skeleton={"premise": "t"},
    )
    ctx = CommandContext(
        store=store, roller=RecordingRoller(7), rules=RulesDB(rules_path)
    )
    result = registry.execute(
        "create_character", ctx, name="Kira", role="pc", class_slug="fighter",
        race_slug="human",
        abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        ac=16, proficiencies={"skills": ["athletics"]},
        attacks=[{"weapon": "longsword", "name": "longsword"}],
    )
    assert result.ok, result.refusal  # sanity: row is genuinely valid post-fix

    assert normalize_characters(store, RulesDB(rules_path)) == []
    store.close()
