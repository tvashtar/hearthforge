"""render_scene_html: deterministic, self-contained, and structurally
faithful — engaged pairs render in their shared band track, tokens don't
move unless band/engagement changes, and nothing numeric about monsters
survives into the page."""

from dm_engine.state.scene import (
    CombatView,
    InitiativeEntry,
    PartyRow,
    PropView,
    SceneView,
    TokenView,
    render_scene_html,
)


def _token(key, kind="monster", band="near", *, name=None, engaged=(),
           active=False, defeated=False, conditions=(), hp=None, max_hp=None,
           word=None):
    if kind == "monster" and word is None:
        word = "fresh"
    return TokenView(
        key=key, name=name or key, kind=kind, band=band,
        engaged_with=list(engaged), conditions=list(conditions),
        defeated=defeated, active=active, hp=hp, max_hp=max_hp,
        condition_word=word,
    )


def _view(tokens, *, props=(), round_=1):
    initiative = [
        InitiativeEntry(key=t.key, name=t.name, active=t.active) for t in tokens
    ]
    return SceneView(
        mode="combat", campaign_name="T", event_id=42, day=1, minutes=480,
        location_name="The Mill", scene_description="Rain", npcs_present=[],
        party=[], props=list(props),
        combat=CombatView(round=round_, tokens=tokens, initiative=initiative),
    )


def test_determinism_same_view_same_bytes():
    view = _view([
        _token("Kira", kind="character", band="engaged", hp=11, max_hp=12,
               active=True, engaged=["goblin-1"]),
        _token("goblin-1", band="engaged", engaged=["Kira"]),
    ])
    assert render_scene_html(view) == render_scene_html(view)


def test_no_jitter_active_turn_change_moves_nothing():
    def tokens(active_key):
        return [
            _token("Kira", kind="character", band="near", hp=11, max_hp=12,
                   active=active_key == "Kira"),
            _token("goblin-1", band="far", active=active_key == "goblin-1"),
        ]

    import re
    coords = re.compile(r'<rect x="[\d.]+" y="[\d.]+" rx')
    a = coords.findall(render_scene_html(_view(tokens("Kira"))))
    b = coords.findall(render_scene_html(_view(tokens("goblin-1"))))
    assert a == b  # token rects at identical coordinates


def test_engaged_pair_at_far_renders_in_far_track_with_link():
    view = _view([
        _token("Aldric", kind="character", band="far", hp=24, max_hp=24,
               engaged=["goblin-1"], active=True),
        _token("goblin-1", band="far", engaged=["Aldric"]),
        _token("goblin-2", band="engaged"),
    ])
    html = render_scene_html(view)
    # Band tracks appear in FC-4 order; both engaged-at-far tokens land
    # between the FAR and DISTANT labels.
    far_track = html.split("FAR")[1].split("DISTANT")[0]
    assert "Aldric" in far_track and "goblin-1" in far_track
    assert 'class="melee"' in html  # exactly the one link
    assert html.count('class="melee"') == 1


def test_monster_shows_word_never_numbers():
    view = _view([
        _token("boss", band="near", word="bloodied", conditions=["poisoned"]),
        _token("Kira", kind="character", band="near", hp=9973, max_hp=9974),
    ])
    html = render_scene_html(view)
    assert "bloodied" in html
    assert "poisoned" in html
    assert "9973/9974" in html  # party numbers are public


def test_defeated_monster_reads_down_not_staggering():
    view = _view([_token("goblin-1", band="near", word="staggering", defeated=True)])
    html = render_scene_html(view)
    assert "down" in html


def test_props_render_on_their_band_and_ambient_strip():
    view = _view(
        [_token("Kira", kind="character", band="engaged", hp=1, max_hp=1)],
        props=[
            PropView(name="overturned wagon", band="near"),
            PropView(name="thick fog", band=None),
        ],
    )
    html = render_scene_html(view)
    near_track = html.split("NEAR")[1].split("FAR")[0]
    assert "overturned wagon" in near_track
    assert "thick fog" in html.split("<svg")[0]  # ambient strip above the map


def test_names_are_escaped():
    view = _view([
        _token('<script>alert("x")</script>', kind="character", band="near",
               hp=1, max_hp=1, name='<script>alert("x")</script>'),
    ])
    html = render_scene_html(view)
    assert "<script>alert" not in html
    assert "&lt;script&gt;" in html


def test_scene_mode_card():
    view = SceneView(
        mode="scene", campaign_name="T", event_id=7, day=2, minutes=1140,
        location_name="The Old Mill", scene_description="Rain hammers the roof",
        npcs_present=["Maro"],
        party=[PartyRow(name="Kira", hp=11, max_hp=12, conditions=["poisoned"])],
        props=[PropView(name="grain hoist", band="near", note="creaking")],
    )
    html = render_scene_html(view)
    for expected in ("The Old Mill", "Rain hammers the roof", "Maro",
                     "11/12", "poisoned", "grain hoist", "creaking",
                     "day 2", "19:00", "as of event #7"):
        assert expected in html
    assert "<svg" not in html  # no band map out of combat


def test_page_is_self_contained_and_self_refreshing():
    view = SceneView(
        mode="scene", campaign_name="T", event_id=1, day=1, minutes=480,
        location_name=None, scene_description=None, npcs_present=[],
        party=[], props=[],
    )
    html = render_scene_html(view)
    assert html.startswith("<!DOCTYPE html>")
    assert '<meta http-equiv="refresh" content="2">' in html
    assert "http://" not in html and "https://" not in html  # no external fetches
