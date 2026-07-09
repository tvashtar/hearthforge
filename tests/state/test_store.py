import json
from pathlib import Path

import pytest

from dm_engine.state.store import CampaignStore


@pytest.fixture()
def campaigns_dir(tmp_path) -> Path:
    return tmp_path / "campaigns"


def _create(campaigns_dir) -> CampaignStore:
    return CampaignStore.create(
        campaigns_dir, slug="test-camp", name="Test Campaign",
        death_mode="narrative", rng_seed=42,
        skeleton={"premise": "save the valley", "acts": [], "factions": []},
    )


def test_create_builds_layout_and_meta(campaigns_dir):
    store = _create(campaigns_dir)
    try:
        assert (campaigns_dir / "test-camp" / "campaign.sqlite").exists()
        meta = store.campaign_meta()
        assert meta["slug"] == "test-camp"
        assert meta["edition"] == "2014"
        assert meta["death_mode"] == "narrative"
        assert meta["rng_seed"] == 42
        assert meta["skeleton"]["premise"] == "save the valley"
        clock = store.world_clock()
        assert clock["day"] == 1
    finally:
        store.close()


def test_create_refuses_existing_slug(campaigns_dir):
    _create(campaigns_dir).close()
    with pytest.raises(FileExistsError):
        _create(campaigns_dir)


def test_open_snapshots_and_reads(campaigns_dir):
    _create(campaigns_dir).close()
    store = CampaignStore.open(campaigns_dir, "test-camp")
    try:
        snaps = list((campaigns_dir / "test-camp" / "snapshots").glob("*.sqlite"))
        assert len(snaps) == 1
        assert store.campaign_meta()["name"] == "Test Campaign"
    finally:
        store.close()


def test_transaction_commits_and_rolls_back(campaigns_dir):
    store = _create(campaigns_dir)
    try:
        with store.transaction():
            store.upsert_location("greenhollow", "Greenhollow", "A sleepy town", region="valley")
        assert store.get_location("greenhollow")["name"] == "Greenhollow"

        with pytest.raises(RuntimeError):
            with store.transaction():
                store.upsert_location("doomed", "Doomed", "never lands", region=None)
                raise RuntimeError("engine bug")
        assert store.get_location("doomed") is None
    finally:
        store.close()


def test_event_log_is_append_only_fc6(campaigns_dir):
    store = _create(campaigns_dir)
    try:
        with store.transaction():
            event_id = store.append_event(
                command="skill_check", inputs={"character": "Kira"},
                result={"ok": True}, rolls=[{"notation": "1d20", "total": 14}],
            )
        assert event_id == 1
        row = store.conn.execute(
            "SELECT command, inputs, rolls, is_ruling, rationale FROM event_log"
        ).fetchone()
        assert row[0] == "skill_check"
        assert json.loads(row[1]) == {"character": "Kira"}
        assert row[3] == 0 and row[4] is None

        with store.transaction():
            rid = store.append_event(
                command="dm_ruling", inputs={}, result={"ok": True}, rolls=[],
                is_ruling=True, rationale="rope trick edge case",
            )
        assert rid == 2
        # rationale required when is_ruling
        with pytest.raises(ValueError):
            with store.transaction():
                store.append_event(command="dm_ruling", inputs={}, result={},
                                   rolls=[], is_ruling=True, rationale="")
    finally:
        store.close()


def test_rng_draws_persist(campaigns_dir):
    store = _create(campaigns_dir)
    try:
        with store.transaction():
            store.set_rng_draws(17)
        assert store.campaign_meta()["rng_draws"] == 17
    finally:
        store.close()


def test_character_and_resources_roundtrip(campaigns_dir):
    store = _create(campaigns_dir)
    try:
        with store.transaction():
            cid = store.insert_character(
                name="Kira", role="pc", class_slug="fighter", race_slug="human",
                level=1, abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
                max_hp=12, ac=16, speed=30,
                proficiencies={"skills": ["athletics"], "saves": ["str", "con"]},
                attacks=[{"name": "longsword", "ranged": False, "range_ft": 5,
                          "long_range_ft": None, "damage": "1d8", "damage_type": "slashing",
                          "ability": "str", "proficient": True}],
                spells_known=[], spell_slots={},
            )
        char = store.get_character("Kira")
        assert char["id"] == cid and char["level"] == 1 and char["max_hp"] == 12
        res = store.get_resources(cid)
        assert res["hp"] == 12 and res["hit_dice_remaining"] == 1
        with store.transaction():
            store.update_resources(cid, hp=5, conditions=["prone"])
        assert store.get_resources(cid)["hp"] == 5
        assert store.get_resources(cid)["conditions"] == ["prone"]
        assert store.get_character("nobody") is None
        assert [c["name"] for c in store.party()] == ["Kira"]
    finally:
        store.close()
