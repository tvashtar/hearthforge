"""scene.html is materialized by the registry hook after every successful
command, switches modes with combat, and is untouched by refusals."""

from dm_engine.commands import registry


def _scene_path(ctx):
    return ctx.store.root / "scene.html"


def test_success_materializes_and_combat_flips_modes(party):
    ctx = party
    result = registry.execute("set_scene", ctx, description="A misty ford")
    assert result.ok
    path = _scene_path(ctx)
    assert path.exists()
    html = path.read_text()
    assert "A misty ford" in html
    assert "<svg" not in html  # scene mode

    result = registry.execute(
        "start_combat", ctx,
        monsters=[{"slug": "goblin", "band": "near"}], pc_initiative=15,
    )
    assert result.ok, result.refusal
    html = path.read_text()
    assert "<svg" in html          # combat mode
    assert "Round 1" in html
    assert "Kira" in html

    result = registry.execute("end_combat", ctx)
    assert result.ok
    assert "<svg" not in path.read_text()  # back to scene mode


def test_refusal_does_not_rewrite_the_file(party):
    ctx = party
    registry.execute("set_scene", ctx, description="Before")
    path = _scene_path(ctx)
    before = path.read_text()

    result = registry.execute("travel", ctx, destination_slug="nowhere", hours=1)
    assert not result.ok  # unknown location -> refusal
    assert path.read_text() == before


def test_footer_tracks_the_committing_event(party):
    ctx = party
    result = registry.execute("set_scene", ctx, description="Now")
    assert f"as of event #{result.event_ids[0]}" in _scene_path(ctx).read_text()


def test_prop_commands_show_up_immediately(party):
    ctx = party
    registry.execute("add_scene_prop", ctx, name="grain hoist", band="near")
    assert "grain hoist" in _scene_path(ctx).read_text()
    registry.execute("remove_scene_prop", ctx, name="grain hoist")
    assert "grain hoist" not in _scene_path(ctx).read_text()
