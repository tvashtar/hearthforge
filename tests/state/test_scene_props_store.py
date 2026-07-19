"""scene_props store accessors: upsert-on-name, remove, clear, ordering,
and in-place migration for campaigns created before the table existed."""

import sqlite3

from dm_engine.state.store import SCENE_PROPS_TABLE, CampaignStore


def _store(tmp_path, slug="p"):
    return CampaignStore.create(
        tmp_path / "campaigns", slug=slug, name="P", death_mode="narrative",
        rng_seed=7, skeleton={"premise": "t"},
    )


def test_upsert_insert_and_update_on_name(tmp_path):
    store = _store(tmp_path)
    store.upsert_scene_prop("overturned wagon", "near", None)
    store.upsert_scene_prop("cliff edge", None, "sheer drop")
    props = store.scene_props()
    assert [(p["name"], p["band"], p["note"]) for p in props] == [
        ("overturned wagon", "near", None),
        ("cliff edge", None, "sheer drop"),
    ]

    # Upsert on the same name moves the prop; ordering (by id) is preserved.
    store.upsert_scene_prop("overturned wagon", "far", "now burning")
    props = store.scene_props()
    assert [(p["name"], p["band"], p["note"]) for p in props] == [
        ("overturned wagon", "far", "now burning"),
        ("cliff edge", None, "sheer drop"),
    ]


def test_remove_returns_false_for_unknown(tmp_path):
    store = _store(tmp_path)
    store.upsert_scene_prop("bonfire", "near", None)
    assert store.remove_scene_prop("bonfire") is True
    assert store.scene_props() == []
    assert store.remove_scene_prop("bonfire") is False


def test_clear_returns_deleted_count(tmp_path):
    store = _store(tmp_path)
    assert store.clear_scene_props() == 0
    store.upsert_scene_prop("a", None, None)
    store.upsert_scene_prop("b", "distant", None)
    assert store.clear_scene_props() == 2
    assert store.scene_props() == []


def test_invalid_band_rejected_by_check_constraint(tmp_path):
    # The store is dumb-and-safe: the CHECK constraint is the last line of
    # defense; command-level validation (Task 2) is the friendly one.
    import pytest

    store = _store(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_scene_prop("ghost", "very-far", None)


def test_migration_adds_table_to_existing_campaign(tmp_path):
    # Simulate a pre-feature campaign: create, drop the table, reopen.
    store = _store(tmp_path)
    store.conn.execute("DROP TABLE scene_props")
    store.conn.commit()
    store.close()
    reopened = CampaignStore.open(tmp_path / "campaigns", "p")
    assert reopened.scene_props() == []  # table recreated by constructor
    reopened.close()


def test_table_constant_is_if_not_exists():
    assert "IF NOT EXISTS" in SCENE_PROPS_TABLE
