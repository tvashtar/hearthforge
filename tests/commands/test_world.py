from dm_engine.commands import registry


def test_travel_advances_clock_and_moves_party(ctx):
    registry.execute("create_location", ctx, slug="mill", name="Old Mill",
                     description="Creaky", region="valley")
    result = registry.execute("travel", ctx, destination_slug="mill", hours=30)
    assert result.ok
    clock = ctx.store.world_clock()
    assert clock["location_slug"] == "mill"
    assert clock["day"] == 2 and clock["minutes"] == 480 + 30 * 60 - 1440


def test_travel_to_unknown_location_refused(ctx):
    result = registry.execute("travel", ctx, destination_slug="atlantis", hours=1)
    assert result.ok is False and "atlantis" in result.refusal


# -- implementer's own per-command mutation+event tests -----------------


def test_set_scene_updates_description_and_logs_event(ctx):
    result = registry.execute("set_scene", ctx, description="A quiet tavern")
    assert result.ok
    clock = ctx.store.world_clock()
    assert clock["scene"] == "A quiet tavern"
    row = ctx.store.conn.execute(
        "SELECT command FROM event_log WHERE id = ?", (result.event_ids[0],)
    ).fetchone()
    assert row["command"] == "set_scene"


def test_set_scene_with_unknown_location_refused(ctx):
    result = registry.execute(
        "set_scene", ctx, description="A ruin", location_slug="nowhere"
    )
    assert result.ok is False
    assert "nowhere" in result.refusal
    clock = ctx.store.world_clock()
    assert clock["scene"] is None  # unchanged


def test_travel_clears_prior_scene(ctx):
    registry.execute("create_location", ctx, slug="mill", name="Old Mill",
                     description="Creaky", region="valley")
    registry.execute("set_scene", ctx, description="Ambush!")
    registry.execute("travel", ctx, destination_slug="mill", hours=2)
    assert ctx.store.world_clock()["scene"] is None


def test_travel_requires_positive_duration(ctx):
    registry.execute("create_location", ctx, slug="mill", name="Old Mill",
                     description="Creaky", region="valley")
    result = registry.execute("travel", ctx, destination_slug="mill", hours=0, days=0)
    assert result.ok is False
    assert ctx.store.event_count() == 2  # create_location + refusal, clock untouched
    assert ctx.store.world_clock()["location_slug"] is None


def test_create_npc_upserts_and_logs_event(ctx):
    result = registry.execute(
        "create_npc", ctx, name="Mara", disposition="friendly",
        location_slug=None, notes={"role": "innkeep"},
    )
    assert result.ok
    assert result.digest == "NPC Mara recorded (friendly)"
    row = ctx.store.conn.execute("SELECT * FROM npcs WHERE name = 'Mara'").fetchone()
    assert row["disposition"] == "friendly"
    assert ctx.store.event_count() == 1


def test_create_npc_with_location_digest_includes_place(ctx):
    registry.execute("create_location", ctx, slug="greenhollow", name="Greenhollow",
                     description="A sleepy town", region="valley")
    result = registry.execute(
        "create_npc", ctx, name="Mara", disposition="friendly",
        location_slug="greenhollow",
    )
    assert result.digest == "NPC Mara recorded (friendly, at greenhollow)"


def test_create_location_upserts_and_logs_event(ctx):
    result = registry.execute(
        "create_location", ctx, slug="mill", name="Old Mill",
        description="Creaky", region="valley",
    )
    assert result.ok
    loc = ctx.store.get_location("mill")
    assert loc == {
        "slug": "mill", "name": "Old Mill", "description": "Creaky",
        "region": "valley", "discovered": 1,
    }
    assert ctx.store.event_count() == 1


def test_update_quest_upserts_and_logs_event(ctx):
    result = registry.execute(
        "update_quest", ctx, slug="find-the-key", title="Find the Key",
        status="active", notes="It's in the mill.",
    )
    assert result.ok
    quests = ctx.store.quests()
    assert quests == [{
        "slug": "find-the-key", "title": "Find the Key", "status": "active",
        "notes": "It's in the mill.",
    }]
    assert ctx.store.event_count() == 1


def test_update_quest_invalid_status_refused(ctx):
    result = registry.execute(
        "update_quest", ctx, slug="find-the-key", title="Find the Key",
        status="bogus",
    )
    assert result.ok is False
    assert "bogus" in result.refusal
    assert ctx.store.quests() == []


# -- read-only recall commands ------------------------------------------


def _seed_world(ctx):
    registry.execute("create_location", ctx, slug="greenhollow", name="Greenhollow",
                     description="A sleepy town", region="valley")
    registry.execute("create_location", ctx, slug="mill", name="Old Mill",
                     description="Creaky", region="valley")
    registry.execute("create_npc", ctx, name="Elowen", disposition="friendly",
                     location_slug="greenhollow", notes={"secret": "knows the sigil"})
    registry.execute("create_npc", ctx, name="Bram", disposition="hostile",
                     location_slug="mill")


def test_get_npc_returns_full_record(ctx):
    _seed_world(ctx)
    result = registry.execute("get_npc", ctx, name="Elowen")
    assert result.ok and result.gm_only
    assert result.data["npc"]["disposition"] == "friendly"
    assert result.data["npc"]["location_slug"] == "greenhollow"
    assert result.data["npc"]["notes"] == {"secret": "knows the sigil"}


def test_get_npc_unknown_name_refused_listing_known(ctx):
    _seed_world(ctx)
    result = registry.execute("get_npc", ctx, name="Elowyn")
    assert result.ok is False
    assert "Elowyn" in result.refusal
    assert "Elowen" in result.refusal and "Bram" in result.refusal


def test_list_npcs_all_and_filtered_by_location(ctx):
    _seed_world(ctx)
    everyone = registry.execute("list_npcs", ctx)
    assert everyone.ok
    assert [n["name"] for n in everyone.data["npcs"]] == ["Bram", "Elowen"]
    assert all(set(n) == {"name", "disposition", "location_slug"}
               for n in everyone.data["npcs"])

    local = registry.execute("list_npcs", ctx, location_slug="mill")
    assert [n["name"] for n in local.data["npcs"]] == ["Bram"]


def test_list_npcs_unknown_location_refused(ctx):
    _seed_world(ctx)
    result = registry.execute("list_npcs", ctx, location_slug="atlantis")
    assert result.ok is False
    assert "atlantis" in result.refusal


def test_list_locations_returns_compact_records(ctx):
    _seed_world(ctx)
    result = registry.execute("list_locations", ctx)
    assert result.ok
    assert [loc["slug"] for loc in result.data["locations"]] == ["greenhollow", "mill"]
    assert all(set(loc) == {"slug", "name", "region"}
               for loc in result.data["locations"])
