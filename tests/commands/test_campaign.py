import pytest

from dm_engine.commands import registry
from dm_engine.commands.campaign import bootstrap_campaign


def test_bootstrap_creates_store_and_logs_event(tmp_path, rules_path):
    ctx = bootstrap_campaign(
        tmp_path / "campaigns", rules_path, slug="valley", name="Valley of Ash",
        death_mode="hardcore", skeleton={"premise": "stop the cult"},
        starting_region={
            "locations": [{"slug": "greenhollow", "name": "Greenhollow",
                           "description": "A sleepy town", "region": "valley"}],
            "npcs": [{"name": "Mara", "disposition": "friendly",
                      "location_slug": "greenhollow", "notes": {"role": "innkeep"}}],
        },
    )
    try:
        assert ctx.store.campaign_meta()["death_mode"] == "hardcore"
        assert ctx.store.get_location("greenhollow") is not None
        row = ctx.store.conn.execute(
            "SELECT command FROM event_log WHERE id = 1").fetchone()
        assert row["command"] == "create_campaign"
    finally:
        ctx.store.close()


def test_brief_reflects_state_and_recap(ctx):
    registry.execute("end_session", ctx, recap="The party reached Greenhollow.")
    brief = registry.execute("get_campaign_brief", ctx)
    assert brief.ok
    assert brief.data["recap"] == "The party reached Greenhollow."
    assert brief.data["campaign"]["edition"] == "2014"
    assert brief.data["combat_active"] is False


def test_end_session_requires_recap(ctx):
    result = registry.execute("end_session", ctx, recap="   ")
    assert result.ok is False


# -- implementer's own per-command mutation+event tests -----------------


def test_bootstrap_seeds_npc_with_notes_and_random_seed_when_omitted(tmp_path, rules_path):
    ctx = bootstrap_campaign(
        tmp_path / "campaigns", rules_path, slug="rand", name="Randomized",
        skeleton={"premise": "test"},
        starting_region={
            "locations": [],
            "npcs": [{"name": "Bram", "disposition": "hostile"}],
        },
    )
    try:
        meta = ctx.store.campaign_meta()
        assert isinstance(meta["rng_seed"], int)
        row = ctx.store.conn.execute(
            "SELECT * FROM npcs WHERE name = 'Bram'"
        ).fetchone()
        assert row["disposition"] == "hostile"
        assert row["location_slug"] is None
    finally:
        ctx.store.close()


def test_end_session_appends_recap_and_event(ctx):
    result = registry.execute("end_session", ctx, recap="They fled the tower.")
    assert result.ok
    assert result.digest == "Session ended; recap saved"
    recap = ctx.store.latest_recap()
    assert recap["kind"] == "session_end" and recap["content"] == "They fled the tower."
    row = ctx.store.conn.execute(
        "SELECT command FROM event_log WHERE id = ?", (result.event_ids[0],)
    ).fetchone()
    assert row["command"] == "end_session"


def test_checkpoint_appends_gm_only_recap_and_event(ctx):
    result = registry.execute("checkpoint", ctx, content="Secret: the duke is a lich.")
    assert result.ok
    assert result.gm_only is True
    recap = ctx.store.latest_recap()
    assert recap["kind"] == "checkpoint"
    assert recap["content"] == "Secret: the duke is a lich."
    assert ctx.store.event_count() == 1


def test_checkpoint_requires_content(ctx):
    result = registry.execute("checkpoint", ctx, content="   ")
    assert result.ok is False
    assert ctx.store.event_count() == 1  # refusal still logged
    assert ctx.store.latest_recap() is None


def test_brief_lists_party_and_open_quests(ctx):
    registry.execute(
        "update_quest", ctx, slug="find-the-key", title="Find the Key",
        status="active",
    )
    brief = registry.execute("get_campaign_brief", ctx)
    assert brief.ok
    slugs = [q["slug"] for q in brief.data["quests"]]
    assert "find-the-key" in slugs
    assert brief.data["party"] == []


# -- read-only history commands ------------------------------------------


def test_list_recaps_returns_all_oldest_first(ctx):
    registry.execute("checkpoint", ctx, content="Secret: the duke is a lich.")
    registry.execute("end_session", ctx, recap="They fled the tower.")
    result = registry.execute("list_recaps", ctx)
    assert result.ok and result.gm_only
    kinds = [r["kind"] for r in result.data["recaps"]]
    assert kinds == ["checkpoint", "session_end"]
    assert result.data["recaps"][1]["content"] == "They fled the tower."
    assert all({"kind", "content", "created_at"} <= set(r)
               for r in result.data["recaps"])


def test_get_events_returns_compact_digests_newest_first(ctx):
    registry.execute("set_scene", ctx, description="A quiet tavern")
    registry.execute("update_quest", ctx, slug="key", title="Find the Key")
    result = registry.execute("get_events", ctx, limit=1)
    assert result.ok and result.gm_only
    events = result.data["events"]
    assert len(events) == 1
    # newest first, and the get_events call itself is not in its own tail
    assert events[0]["command"] == "update_quest"
    assert events[0]["ok"] is True
    assert "Find the Key" in events[0]["digest"]
    assert {"id", "command", "ok", "digest", "created_at"} <= set(events[0])


def test_get_events_nonpositive_limit_refused_and_overlarge_clamped(ctx):
    result = registry.execute("get_events", ctx, limit=0)
    assert result.ok is False

    registry.execute("set_scene", ctx, description="A quiet tavern")
    result = registry.execute("get_events", ctx, limit=5000)
    assert result.ok
    assert result.data["limit"] == 100


def test_get_events_tail_includes_crash_events(ctx):
    def _boom(inner_ctx, **kwargs):
        raise KeyError("damage_type")

    registry._COMMANDS["_test_boom_tail"] = _boom
    try:
        with pytest.raises(KeyError):
            registry.execute("_test_boom_tail", ctx, target="Goblin 2")
    finally:
        del registry._COMMANDS["_test_boom_tail"]

    result = registry.execute("get_events", ctx)
    crash = result.data["events"][0]
    assert crash["command"] == "_test_boom_tail"
    assert crash["ok"] is False
    assert "ENGINE CRASH" in crash["digest"]
