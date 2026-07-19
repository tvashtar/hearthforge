"""add_scene_prop / remove_scene_prop, and their lifecycle hooks in
set_scene (clears) and get_scene_state (reports)."""

from dm_engine.commands import registry


def test_add_scene_prop_upserts_on_name(ctx):
    result = registry.execute("add_scene_prop", ctx, name="overturned wagon", band="near")
    assert result.ok, result.refusal
    assert result.digest == "Prop: overturned wagon (near)"
    assert result.data["props"] == [
        {"id": 1, "name": "overturned wagon", "band": "near", "note": None}
    ]

    # Same name again = move it, not duplicate it.
    result = registry.execute(
        "add_scene_prop", ctx, name="overturned wagon", band="far", note="burning"
    )
    assert result.ok
    assert result.data["props"] == [
        {"id": 1, "name": "overturned wagon", "band": "far", "note": "burning"}
    ]


def test_add_scene_prop_ambient_band_is_none(ctx):
    result = registry.execute("add_scene_prop", ctx, name="thick fog")
    assert result.ok
    assert result.digest == "Prop: thick fog (ambient)"
    assert result.data["props"][0]["band"] is None


def test_add_scene_prop_refuses_invalid_band(ctx):
    result = registry.execute("add_scene_prop", ctx, name="ghost", band="very-far")
    assert not result.ok
    assert "very-far" in result.refusal
    assert "engaged, near, far, distant" in result.refusal


def test_remove_scene_prop_refuses_unknown_listing_known(ctx):
    registry.execute("add_scene_prop", ctx, name="bonfire", band="near")
    result = registry.execute("remove_scene_prop", ctx, name="wagon")
    assert not result.ok
    assert "wagon" in result.refusal
    assert "bonfire" in result.refusal  # steers with the known props

    result = registry.execute("remove_scene_prop", ctx, name="bonfire")
    assert result.ok
    assert result.data["props"] == []


def test_remove_scene_prop_refusal_with_no_props(ctx):
    result = registry.execute("remove_scene_prop", ctx, name="wagon")
    assert not result.ok
    assert "none" in result.refusal


def test_set_scene_clears_props_and_reports_count(ctx):
    registry.execute("add_scene_prop", ctx, name="bonfire", band="near")
    registry.execute("add_scene_prop", ctx, name="thick fog")

    result = registry.execute("set_scene", ctx, description="The road at dawn")
    assert result.ok
    assert result.data["props_cleared"] == 2
    assert "(2 props cleared)" in result.digest
    assert ctx.store.scene_props() == []

    # No props → no clutter in the digest.
    result = registry.execute("set_scene", ctx, description="A quiet mill")
    assert result.data["props_cleared"] == 0
    assert "cleared" not in result.digest


def test_get_scene_state_includes_props(ctx):
    registry.execute("add_scene_prop", ctx, name="bonfire", band="near")
    result = registry.execute("get_scene_state", ctx)
    assert result.ok
    assert result.data["props"] == [
        {"id": 1, "name": "bonfire", "band": "near", "note": None}
    ]
