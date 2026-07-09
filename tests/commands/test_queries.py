from dm_engine.commands import registry


def test_lookup_rule_returns_hits_and_never_refuses(ctx):
    result = registry.execute("lookup_rule", ctx, query="grappling")
    assert result.ok
    assert isinstance(result.data["hits"], list)
    assert len(result.data["hits"]) > 0
    hit = result.data["hits"][0]
    assert {"source", "heading_path", "heading", "snippet"} <= hit.keys()


def test_lookup_rule_empty_hits_is_still_ok(ctx):
    result = registry.execute("lookup_rule", ctx, query="zzznomatchzzz")
    assert result.ok
    assert result.data["hits"] == []


def test_lookup_monster_round_trips_full_record(ctx):
    result = registry.execute("lookup_monster", ctx, slug="goblin")
    assert result.ok
    assert result.gm_only is True
    assert result.data["name"].lower() == "goblin"
    assert "hit_points" in result.data


def test_lookup_monster_refuses_unknown_slug(ctx):
    result = registry.execute("lookup_monster", ctx, slug="nonexistent")
    assert result.ok is False


def test_lookup_spell_round_trips_full_record(ctx):
    result = registry.execute("lookup_spell", ctx, slug="magic-missile")
    assert result.ok
    assert result.data["name"].lower() == "magic missile"
    assert "level" in result.data


def test_lookup_spell_refuses_unknown_slug(ctx):
    result = registry.execute("lookup_spell", ctx, slug="nonexistent")
    assert result.ok is False


def test_lookup_commands_log_events(ctx):
    before = ctx.store.event_count()
    registry.execute("lookup_rule", ctx, query="grappling")
    assert ctx.store.event_count() == before + 1
