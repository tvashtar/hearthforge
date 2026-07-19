"""render_scene_html: deterministic, self-contained, and structurally
faithful — engaged pairs render in their shared band track, tokens don't
move unless band/engagement changes, and nothing numeric about monsters
survives into the page."""

import re

from dm_engine.state.scene import (
    CombatView,
    InitiativeEntry,
    PartyRow,
    PropView,
    SceneView,
    TokenView,
    render_scene_html,
)


def _melee_links(html: str) -> list[str]:
    """Every <line>/<path class="melee" .../> element in the rendered SVG."""
    return re.findall(r'<(?:line|path)[^>]*class="melee"[^>]*/>', html)


def _line_xs(link: str) -> tuple[float, float]:
    """The (from, to) x-coordinates of a straight melee <line> link."""
    x1 = float(re.search(r'x1="([\d.]+)"', link).group(1))
    x2 = float(re.search(r'x2="([\d.]+)"', link).group(1))
    return x1, x2


def _token_rect_xs(html: str) -> list[float]:
    """Left x of every token rect (the ones carrying rx=8 rounding)."""
    return [float(x) for x in re.findall(r'<rect x="([\d.]+)" y="[\d.]+" rx', html)]


def _token_positions(html: str) -> dict[str, tuple[float, float]]:
    """Token name -> its rect's top-left (x, y)."""
    pat = re.compile(
        r'<rect x="([\d.]+)" y="([\d.]+)" rx="8"[^/]*/>'
        r'<text [^>]*class="name">([^<]+)</text>'
    )
    return {name: (float(x), float(y)) for x, y, name in pat.findall(html)}


def _link_sample_points(link: str, n: int = 64) -> list[tuple[float, float]]:
    """Dense point samples along a melee link (line, polyline path, or
    quadratic path) for rect-intersection checks."""
    ts = [i / n for i in range(n + 1)]
    if link.startswith("<line"):
        x1, x2 = _line_xs(link)
        y1 = float(re.search(r'y1="([\d.-]+)"', link).group(1))
        y2 = float(re.search(r'y2="([\d.-]+)"', link).group(1))
        return [(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t) for t in ts]
    d = re.search(r'd="([^"]+)"', link).group(1)
    pts: list[tuple[float, float]] = []
    cur: tuple[float, float] | None = None
    for cmd, coords in re.findall(r"([MLQ])((?:\s+-?[\d.]+)+)", d):
        vals = [float(v) for v in coords.split()]
        if cmd == "M":
            cur = (vals[0], vals[1])
            pts.append(cur)
        elif cmd == "L":
            nxt = (vals[0], vals[1])
            pts += [
                (cur[0] + (nxt[0] - cur[0]) * t, cur[1] + (nxt[1] - cur[1]) * t)
                for t in ts
            ]
            cur = nxt
        else:  # Q cx cy x y
            cx, cy, x, y = vals
            pts += [
                (
                    (1 - t) ** 2 * cur[0] + 2 * t * (1 - t) * cx + t**2 * x,
                    (1 - t) ** 2 * cur[1] + 2 * t * (1 - t) * cy + t**2 * y,
                )
                for t in ts
            ]
            cur = (x, y)
    return pts


def _inside_any_rect(pt, rects, w=150.0, h=64.0):
    px, py = pt
    return any(rx < px < rx + w and ry < py < ry + h for rx, ry in rects)


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


def test_mutual_pair_link_is_double_headed():
    view = _view([
        _token("Kira", kind="character", band="engaged", hp=11, max_hp=12,
               engaged=["goblin-1"], active=True),
        _token("goblin-1", band="engaged", engaged=["Kira"]),
    ])
    html = render_scene_html(view)
    assert html.count('class="melee"') == 1
    link = _melee_links(html)[0]
    assert 'marker-start="url(#ah-start)"' in link
    assert 'marker-end="url(#ah-end)"' in link


def test_asymmetric_pair_is_single_headed_running_engager_to_target():
    # a lists b, but b does not list a -> one-way link a -> b.
    a = TokenView(
        key="Kira", name="Kira", kind="character", band="engaged",
        engaged_with=["goblin-1"], conditions=[], defeated=False, active=True,
        hp=11, max_hp=12,
    )
    b = TokenView(
        key="goblin-1", name="goblin-1", kind="monster", band="engaged",
        engaged_with=[], conditions=[], defeated=False, active=False,
        condition_word="fresh",
    )
    html = render_scene_html(_view([a, b]))
    link = _melee_links(html)[0]
    assert 'marker-end="url(#ah-end)"' in link
    assert "marker-start" not in link
    # Path runs engager (Kira, left slot) -> target (goblin-1, right slot):
    # the "from" x precedes the "to" x.
    x1, x2 = _line_xs(link)
    assert x1 < x2


def test_adjacent_link_endpoints_sit_outside_both_rects():
    view = _view([
        _token("Kira", kind="character", band="engaged", hp=11, max_hp=12,
               engaged=["goblin-1"], active=True),
        _token("goblin-1", band="engaged", engaged=["Kira"]),
    ])
    html = render_scene_html(view)
    rects = _token_rect_xs(html)
    left_x, right_x = sorted(rects)
    left_right_edge = left_x + 150   # _TOKEN_W
    right_left_edge = right_x
    for x in _line_xs(_melee_links(html)[0]):
        assert left_right_edge < x < right_left_edge


def test_combat_output_drops_swords_glyph():
    view = _view([
        _token("Kira", kind="character", band="engaged", hp=11, max_hp=12,
               engaged=["goblin-1"], active=True),
        _token("goblin-1", band="engaged", engaged=["Kira"]),
    ])
    html = render_scene_html(view)
    assert "⚔" not in html
    assert 'class="swords"' not in html


def test_cluster_of_three_arcs_over_the_row():
    # Kira engaged with both goblins: goblin-1 sits adjacent (straight
    # link), goblin-2 one slot further (arc). The arc must stay above the
    # row's rect tops for its whole length — never through a rect.
    view = _view([
        _token("Kira", kind="character", band="near", hp=10, max_hp=10,
               engaged=["goblin-1", "goblin-2"], active=True),
        _token("goblin-1", band="near", engaged=["Kira"]),
        _token("goblin-2", band="near", engaged=["Kira"]),
    ])
    html = render_scene_html(view)
    links = _melee_links(html)
    assert len(links) == 2
    arcs = [ln for ln in links if ln.startswith("<path")]
    assert len(arcs) == 1
    d = re.search(r'd="([^"]+)"', arcs[0]).group(1)
    m = re.fullmatch(
        r"M ([\d.-]+) ([\d.-]+) Q ([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+)", d)
    _x1, y1, _cx, cy, _x2, y2 = (float(v) for v in m.groups())
    rects = _token_positions(html).values()
    row_top = min(y for _, y in rects)
    assert y1 == y2 == row_top - 3   # endpoints just above the rects
    assert cy < row_top              # control above too => whole curve above
    assert not any(_inside_any_rect(p, rects)
                   for p in _link_sample_points(arcs[0]))


def test_cluster_wraps_to_next_row_instead_of_straddling():
    # Four singletons fill row-0 columns 0..3; a mutual pair no longer
    # fits on that row, so it word-wraps to row 1 columns 0 and 1 together
    # and keeps the straight adjacent link.
    singles = [_token(f"g{i}", band="near") for i in range(1, 5)]
    pair = [
        _token("Kira", kind="character", band="near", hp=9, max_hp=9,
               engaged=["wolf-1"], active=True),
        _token("wolf-1", band="near", engaged=["Kira"]),
    ]
    html = render_scene_html(_view(singles + pair))
    pos = _token_positions(html)
    assert pos["Kira"][1] == pos["wolf-1"][1]        # same row
    assert pos["Kira"][1] > pos["g1"][1]             # ...the next one down
    assert (pos["Kira"][0], pos["wolf-1"][0]) == (90.0, 268.0)  # cols 0, 1
    link = _melee_links(html)[0]
    assert link.startswith("<line")


def test_giant_cluster_cross_row_links_avoid_all_rects():
    # One token mutually engaged with five others: the cluster spans two
    # rows, so one link must cross rows. Every emitted link — straight,
    # arc, or gutter-routed — must avoid every token rect.
    others = [f"goblin-{i}" for i in range(1, 6)]
    view = _view([
        _token("Kira", kind="character", band="near", hp=9, max_hp=9,
               engaged=list(others), active=True),
        *[_token(k, band="near", engaged=["Kira"]) for k in others],
    ])
    html = render_scene_html(view)
    links = _melee_links(html)
    assert len(links) == 5
    pos = _token_positions(html)
    assert len({y for _, y in pos.values()}) == 2    # cluster spans two rows
    rects = list(pos.values())
    for link in links:
        assert not any(_inside_any_rect(p, rects)
                       for p in _link_sample_points(link)), link


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
