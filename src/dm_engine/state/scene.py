"""Scene visualization: SceneView builder (this task), HTML/SVG renderer
and materializer (later tasks). Spec:
docs/superpowers/specs/2026-07-19-scene-visualization-design.md.

`build_scene_view` is a pure projection of campaign state into a
JSON-serializable, PLAYER-VISIBLE model: monster HP numbers never enter it
(validator-enforced), so no renderer — or future live-view server — can
leak what the view doesn't carry. The registry's post-command hook
materializes it to campaigns/<slug>/scene.html.
"""

from __future__ import annotations

import html as _html
from pathlib import Path
from typing import Literal, NamedTuple

from pydantic import BaseModel, model_validator

from dm_engine.rules.bands import BAND_ORDER
from dm_engine.state.store import CampaignStore


def monster_condition_word(hp: int, max_hp: int) -> str:
    """The dm-session skill's public monster-status ladder, computable
    tiers only ("near death" is a DM judgment call, deliberately absent).
    full -> fresh; >half -> wounded; <=half -> bloodied; <=quarter ->
    staggering."""
    if hp >= max_hp:
        return "fresh"
    if hp > max_hp / 2:
        return "wounded"
    if hp > max_hp / 4:
        return "bloodied"
    return "staggering"


class PropView(BaseModel):
    name: str
    band: str | None = None
    note: str | None = None


class PartyRow(BaseModel):
    name: str
    hp: int
    max_hp: int
    conditions: list[str]


class TokenView(BaseModel):
    key: str
    name: str
    kind: Literal["character", "monster"]
    band: str
    engaged_with: list[str]
    conditions: list[str]
    defeated: bool
    active: bool
    hp: int | None = None              # characters only
    max_hp: int | None = None          # characters only
    condition_word: str | None = None  # monsters only

    @model_validator(mode="after")
    def _monster_numbers_stay_behind_the_screen(self) -> "TokenView":
        if self.kind == "monster":
            if self.hp is not None or self.max_hp is not None:
                raise ValueError(
                    "monster HP numbers are DM-screen only and never enter SceneView"
                )
            if self.condition_word is None:
                raise ValueError("monster tokens must carry a condition word")
        return self


class InitiativeEntry(BaseModel):
    key: str
    name: str
    active: bool


class CombatView(BaseModel):
    round: int
    tokens: list[TokenView]        # initiative order
    initiative: list[InitiativeEntry]


class SceneView(BaseModel):
    mode: Literal["combat", "scene"]
    campaign_name: str
    event_id: int
    day: int
    minutes: int
    location_name: str | None
    scene_description: str | None
    npcs_present: list[str]
    party: list[PartyRow]
    props: list[PropView]
    combat: CombatView | None = None


def build_scene_view(store: CampaignStore) -> SceneView:
    meta = store.campaign_meta()
    clock = store.world_clock()

    location = None
    npcs_present: list[str] = []
    if clock.get("location_slug"):
        location = store.get_location(clock["location_slug"])
        npcs_present = [n["name"] for n in store.npcs(clock["location_slug"])]

    party = []
    for char in store.party():
        res = store.get_resources(char["id"])
        party.append(PartyRow(
            name=char["name"], hp=res["hp"], max_hp=char["max_hp"],
            conditions=res["conditions"],
        ))

    props = [
        PropView(name=p["name"], band=p["band"], note=p["note"])
        for p in store.scene_props()
    ]

    combat_row = store.combat()
    combat = _build_combat_view(store, combat_row) if combat_row["active"] else None

    return SceneView(
        mode="combat" if combat else "scene",
        campaign_name=meta["name"],
        event_id=store.next_event_id() - 1,
        day=clock["day"],
        minutes=clock["minutes"],
        location_name=location["name"] if location else None,
        scene_description=clock.get("scene"),
        npcs_present=npcs_present,
        party=party,
        props=props,
        combat=combat,
    )


def _build_combat_view(store: CampaignStore, combat: dict) -> CombatView:
    active_key = combat["combatants"][combat["turn_index"]]["key"]
    tokens = []
    for c in combat["combatants"]:
        if c["kind"] == "character":
            res = store.get_resources(c["character_id"])
            char = store.get_character_by_id(c["character_id"])
            hp, max_hp, word = res["hp"], char["max_hp"], None
            conditions = res["conditions"]
        else:
            # The one place monster numbers get worded before they can
            # reach a player-visible surface.
            hp, max_hp = None, None
            word = monster_condition_word(c["hp"], c["max_hp"])
            conditions = c["conditions"]
        tokens.append(TokenView(
            key=c["key"], name=c["name"], kind=c["kind"], band=c["band"],
            engaged_with=list(c["engaged_with"]), conditions=list(conditions),
            defeated=bool(c["defeated"]), active=(c["key"] == active_key),
            hp=hp, max_hp=max_hp, condition_word=word,
        ))
    initiative = [
        InitiativeEntry(key=c["key"], name=c["name"], active=(c["key"] == active_key))
        for c in combat["combatants"]
    ]
    return CombatView(round=combat["round"], tokens=tokens, initiative=initiative)


# -- renderer ---------------------------------------------------------------
# Deterministic by construction: layout is a pure function of the view
# (no timestamps, no randomness, iteration in model/list order only).

_TOKEN_W, _TOKEN_H = 150, 64
_TOKEN_GAP = 28
_PER_ROW = 5
_BAND_H = 170            # fits two token rows + a prop line
_LABEL_W = 90
_SVG_W = _LABEL_W + _PER_ROW * (_TOKEN_W + _TOKEN_GAP) + 20

# Small self-contained arrowheads for engagement links. userSpaceOnUse keeps
# them a fixed size (independent of stroke-width) so geometry stays exact;
# fill matches the link stroke so mutual/one-way arrows read as one mark.
_ARROW_DEFS = (
    "<defs>"
    '<marker id="ah-end" markerWidth="7" markerHeight="7" refX="6" refY="3.5"'
    ' orient="auto" markerUnits="userSpaceOnUse">'
    '<path d="M0,0 L7,3.5 L0,7 Z" fill="#d8d4c8"/></marker>'
    '<marker id="ah-start" markerWidth="7" markerHeight="7" refX="1" refY="3.5"'
    ' orient="auto" markerUnits="userSpaceOnUse">'
    '<path d="M7,0 L0,3.5 L7,7 Z" fill="#d8d4c8"/></marker>'
    "</defs>"
)

_CSS = """
body { background: #16181d; color: #d8d4c8; font: 14px/1.45 -apple-system,
       "Segoe UI", sans-serif; margin: 1.2rem auto; max-width: 1020px; }
header { font-size: 1.05rem; letter-spacing: .04em; margin-bottom: .6rem;
         color: #e8e3d3; }
footer { margin-top: .8rem; color: #6f6a5e; font-size: .8rem; }
.round { font-weight: 700; margin: .4rem 0 .2rem; color: #e0a626; }
.initiative { margin-bottom: .5rem; }
.initiative .init { display: inline-block; padding: .1rem .5rem;
    margin-right: .3rem; border: 1px solid #3a3d45; border-radius: 999px;
    font-size: .8rem; color: #a9a494; }
.initiative .init.active { border-color: #e0a626; color: #e0a626;
    font-weight: 700; }
.ambient { color: #8f8975; font-style: italic; margin-bottom: .4rem; }
svg .track { fill: #1d2027; stroke: #2a2e37; }
svg .bandlabel { fill: #6f7787; font: 700 12px sans-serif;
    letter-spacing: .12em; }
svg .token rect { rx: 8; }
svg .token.character rect { fill: #2e5d8a; }
svg .token.monster rect { fill: #8a3232; }
svg .token.defeated rect { fill: #3a3a3a; }
svg .token.defeated text { fill: #8a8578; }
svg .token.active rect { stroke: #e0a626; stroke-width: 3; }
svg .name { fill: #f0ece0; font: 700 13px sans-serif; }
svg .sub { fill: #d8d4c8; font: 11px sans-serif; }
svg .hpback { fill: #14161a; }
svg .hpfill { fill: #79a56a; }
svg .melee { stroke: #d8d4c8; stroke-width: 1.5; fill: none; }
svg .prop { fill: #b0a274; font: italic 12px sans-serif; }
.card { background: #1d2027; border: 1px solid #2a2e37; border-radius: 10px;
        padding: 1rem 1.2rem; }
.card h1 { margin: 0 0 .4rem; font-size: 1.3rem; color: #e8e3d3; }
.card .scene { color: #c9c4b4; }
.card .npcs, .card .props { color: #a9a494; }
.card table { border-collapse: collapse; margin-top: .6rem; }
.card td, .card th { padding: .2rem .8rem .2rem 0; text-align: left; }
.card th { color: #6f7787; font-size: .8rem; letter-spacing: .08em;
           text-transform: uppercase; }
"""


def render_scene_html(view: SceneView) -> str:
    body = _render_combat(view) if view.mode == "combat" else _render_scene_card(view)
    time = f"day {view.day}, {view.minutes // 60:02d}:{view.minutes % 60:02d}"
    header_bits = [_html.escape(view.campaign_name), time]
    if view.location_name:
        header_bits.append(_html.escape(view.location_name))
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta http-equiv="refresh" content="2">\n'
        f"<title>{_html.escape(view.campaign_name)} — scene</title>\n"
        f"<style>{_CSS}</style>\n</head>\n<body>\n"
        f"<header>{' · '.join(header_bits)}</header>\n"
        f"{body}\n"
        f"<footer>as of event #{view.event_id}</footer>\n"
        "</body>\n</html>\n"
    )


def _render_scene_card(view: SceneView) -> str:
    parts = ['<section class="card">']
    parts.append(f"<h1>{_html.escape(view.location_name or 'Somewhere')}</h1>")
    if view.scene_description:
        parts.append(f'<p class="scene">{_html.escape(view.scene_description)}</p>')
    if view.npcs_present:
        names = ", ".join(_html.escape(n) for n in view.npcs_present)
        parts.append(f'<p class="npcs">Present: {names}</p>')
    if view.props:
        items = []
        for p in view.props:
            where = f" ({p.band})" if p.band else ""
            note = f" — {_html.escape(p.note)}" if p.note else ""
            items.append(f"<li>{_html.escape(p.name)}{where}{note}</li>")
        parts.append('<ul class="props">' + "".join(items) + "</ul>")
    if view.party:
        rows = "".join(
            f"<tr><td>{_html.escape(m.name)}</td><td>{m.hp}/{m.max_hp}</td>"
            f"<td>{_html.escape(', '.join(m.conditions)) or '—'}</td></tr>"
            for m in view.party
        )
        parts.append(
            '<table class="party"><tr><th>Party</th><th>HP</th>'
            f"<th>Conditions</th></tr>{rows}</table>"
        )
    parts.append("</section>")
    return "\n".join(parts)


def _render_combat(view: SceneView) -> str:
    combat = view.combat
    strip = "".join(
        f'<span class="init{" active" if e.active else ""}">'
        f"{_html.escape(e.name)}</span>"
        for e in combat.initiative
    )
    ambient = [p for p in view.props if p.band is None]
    ambient_html = ""
    if ambient:
        names = " · ".join(_html.escape(p.name) for p in ambient)
        ambient_html = f'<div class="ambient">{names}</div>\n'
    svg = _render_band_svg(combat.tokens, [p for p in view.props if p.band])
    return (
        f'<div class="round">Round {combat.round}</div>\n'
        f'<div class="initiative">{strip}</div>\n'
        f"{ambient_html}{svg}"
    )


class _Slot(NamedTuple):
    """A token's laid-out position: pixel origin plus the grid facts the
    link router needs to route around other rects."""
    x: float
    y: float
    band_i: int
    row: int
    col: int
    y0: float  # band track top


def _band_layout(tokens: list[TokenView]) -> list[list[TokenView]]:
    """Engaged clusters adjacent, then initiative order (input order IS
    initiative order). A token's slot changes only when its band or its
    engagement set changes — the no-jitter rule. Returns the clusters so
    the slot assigner can word-wrap each onto a fresh row when it would
    otherwise straddle a row boundary."""
    clusters: list[list[TokenView]] = []
    home_of: dict[str, list[TokenView]] = {}
    for tok in tokens:
        home = None
        for partner in tok.engaged_with:
            if partner in home_of:
                home = home_of[partner]
                break
        if home is None:
            home = []
            clusters.append(home)
        home.append(tok)
        home_of[tok.key] = home
    return clusters


def _token_svg(tok: TokenView, x: float, y: float) -> str:
    cls = tok.kind
    if tok.defeated:
        cls += " defeated"
    if tok.active:
        cls += " active"
    if tok.kind == "character":
        frac = max(0.0, min(1.0, tok.hp / tok.max_hp)) if tok.max_hp else 0.0
        bits = f"{tok.hp}/{tok.max_hp}"
        if tok.conditions:
            bits += " · " + ", ".join(tok.conditions)
        status = (
            f'<rect x="{x + 8}" y="{y + 32}" width="{_TOKEN_W - 16}" height="6"'
            ' class="hpback"/>'
            f'<rect x="{x + 8}" y="{y + 32}" width="{(_TOKEN_W - 16) * frac:.1f}"'
            ' height="6" class="hpfill"/>'
            f'<text x="{x + 8}" y="{y + 54}" class="sub">{_html.escape(bits)}</text>'
        )
    else:
        word = "down" if tok.defeated else tok.condition_word
        bits = ", ".join([word, *tok.conditions])
        status = (
            f'<text x="{x + 8}" y="{y + 44}" class="sub">{_html.escape(bits)}</text>'
        )
    return (
        f'<g class="token {cls}">'
        f'<rect x="{x}" y="{y}" rx="8" width="{_TOKEN_W}" height="{_TOKEN_H}"/>'
        f'<text x="{x + 8}" y="{y + 22}" class="name">{_html.escape(tok.name)}</text>'
        f"{status}</g>"
    )


def _render_band_svg(tokens: list[TokenView], band_props: list[PropView]) -> str:
    height = 4 * _BAND_H + 10
    parts = [
        f'<svg width="{_SVG_W}" height="{height}"'
        f' viewBox="0 0 {_SVG_W} {height}" xmlns="http://www.w3.org/2000/svg"'
        ' role="img">',
        _ARROW_DEFS,
    ]
    positions: dict[str, _Slot] = {}
    for i, band in enumerate(BAND_ORDER):
        y0 = 5 + i * _BAND_H
        parts.append(
            f'<rect x="2" y="{y0}" width="{_SVG_W - 4}" height="{_BAND_H - 8}"'
            ' class="track"/>'
        )
        parts.append(
            f'<text x="14" y="{y0 + 22}" class="bandlabel">{band.upper()}</text>'
        )
        clusters = _band_layout([t for t in tokens if t.band == band])
        band_token_svgs: list[str] = []
        slot = 0
        for cluster in clusters:
            # Word-wrap: a cluster that would straddle the row boundary
            # starts at column 0 of the next row instead, so linked pairs
            # can only cross rows inside a cluster wider than one row.
            # Padding is a function of band composition + engagement +
            # order only, so the no-jitter rule still holds.
            remaining = _PER_ROW - slot % _PER_ROW
            if remaining < _PER_ROW and len(cluster) > remaining:
                slot += remaining
            for tok in cluster:
                row, col = divmod(slot, _PER_ROW)
                x = _LABEL_W + col * (_TOKEN_W + _TOKEN_GAP)
                y = y0 + 12 + row * (_TOKEN_H + 8)
                positions[tok.key] = _Slot(x, y, i, row, col, y0)
                band_token_svgs.append(_token_svg(tok, x, y))
                slot += 1
        # Tokens render inside their own band track (between this band's
        # label and the next), so each token's markup sits in the region a
        # reader would look for it. Engagement links are appended last, after
        # all bands, so they paint on top.
        parts.extend(band_token_svgs)
        for j, prop in enumerate(p for p in band_props if p.band == band):
            px = _LABEL_W + j * 180
            py = y0 + _BAND_H - 20
            parts.append(
                f'<text x="{px}" y="{py}" class="prop">'
                f"◆ {_html.escape(prop.name)}</text>"
            )

    # Each unordered engaged pair renders exactly once, in sorted key order
    # (determinism). A pair is MUTUAL when each token lists the other, else
    # ONE-WAY from the engager toward the target; the path always runs
    # engager -> target so the single arrowhead points at the target.
    tok_by_key = {tok.key: tok for tok in tokens}
    pairs = sorted({
        tuple(sorted((tok.key, partner)))
        for tok in tokens
        for partner in tok.engaged_with
        if tok.key in positions and partner in positions
    })
    for ka, kb in pairs:
        a_lists_b = kb in tok_by_key[ka].engaged_with
        b_lists_a = ka in tok_by_key[kb].engaged_with
        mutual = a_lists_b and b_lists_a
        frm, to = (ka, kb) if (mutual or a_lists_b) else (kb, ka)
        parts.append(_link_svg(positions[frm], positions[to], mutual))

    parts.append("</svg>")
    return "".join(parts)


def _link_svg(frm: _Slot, to: _Slot, mutual: bool) -> str:
    """A single engagement link, path running frm -> to. Every route keeps
    the whole stroke OUTSIDE all token rects:

    - same row, adjacent columns: straight horizontal across the gap;
    - same row 0, non-adjacent: quadratic arc over the tops (equal-y
      endpoints keep the whole curve above the row's rects);
    - anything else in the same band (cross-row inside a wrapped cluster,
      or non-adjacent on row >= 1 where an arc would sag into the row
      above): orthogonal route through the rect-free column gutters and,
      when the columns don't share a gutter, the strip above row 0;
    - different bands (unreachable today — engage adopts the target's
      band): the arc, as a harmless fallback."""
    markers = ' marker-end="url(#ah-end)"'
    if mutual:
        markers = ' marker-start="url(#ah-start)" marker-end="url(#ah-end)"'
    same_row = frm.band_i == to.band_i and frm.row == to.row
    if same_row and abs(frm.col - to.col) == 1:
        cy = frm.y + _TOKEN_H / 2
        if frm.x < to.x:
            x1, x2 = frm.x + _TOKEN_W + 3, to.x - 3
        else:
            x1, x2 = frm.x - 3, to.x + _TOKEN_W + 3
        return f'<line x1="{x1}" y1="{cy}" x2="{x2}" y2="{cy}" class="melee"{markers}/>'
    if (same_row and frm.row == 0) or frm.band_i != to.band_i:
        return _arc_svg(frm, to, markers)
    pts = _gutter_route(frm, to)
    d = "M " + " L ".join(f"{x} {y}" for x, y in pts)
    return f'<path d="{d}" class="melee"{markers}/>'


def _arc_svg(frm: _Slot, to: _Slot, markers: str) -> str:
    """Quadratic arc from top-center to top-center. With equal endpoint y
    the curve peaks (3 + 24)/2 = 13.5px above the rect tops, so it clears
    every rect in its own row."""
    ax_t, bx_t = frm.x + _TOKEN_W / 2, to.x + _TOKEN_W / 2
    mid_x = (ax_t + bx_t) / 2
    peak = min(frm.y, to.y) - 24
    d = f"M {ax_t} {frm.y - 3} Q {mid_x} {peak} {bx_t} {to.y - 3}"
    return f'<path d="{d}" class="melee"{markers}/>'


def _gutter_route(frm: _Slot, to: _Slot) -> list[tuple[float, float]]:
    """Orthogonal waypoints frm -> to that never enter a token rect: the
    28px vertical gutters between columns are rect-free in every row, and
    the 6px strip above row 0 (y0+6) is rect-free across the band. The
    only horizontal runs at token height are the 11px stubs between a
    rect's side edge and its own adjacent gutter, which cross nothing."""
    pitch = _TOKEN_W + _TOKEN_GAP

    def gutter_x(g: int) -> float:
        # Gutter g is the channel left of column g (g = _PER_ROW: right
        # of the last column); its center is 14px off the rect edges.
        return _LABEL_W + g * pitch - _TOKEN_GAP / 2

    def pick_gutter(col: int, toward: int) -> int:
        # The gutter adjacent to `col` on the side facing `toward`
        # (right on ties) — always reachable by an 11px stub.
        return col + 1 if toward >= col else col

    def stub_x(slot: _Slot, g: int) -> float:
        # Exit/entry point 3px outside the rect edge facing gutter g.
        if gutter_x(g) > slot.x + _TOKEN_W / 2:
            return slot.x + _TOKEN_W + 3
        return slot.x - 3

    g_frm = pick_gutter(frm.col, to.col)
    g_to = pick_gutter(to.col, frm.col)
    frm_cy = frm.y + _TOKEN_H / 2
    to_cy = to.y + _TOKEN_H / 2
    pts = [(stub_x(frm, g_frm), frm_cy), (gutter_x(g_frm), frm_cy)]
    if g_frm != g_to:
        top = frm.y0 + 6
        pts += [(gutter_x(g_frm), top), (gutter_x(g_to), top)]
    pts += [(gutter_x(g_to), to_cy), (stub_x(to, g_to), to_cy)]
    return pts


def materialize_scene(store: CampaignStore) -> Path:
    """The thin I/O adapter (registry post-command hook). A future live
    web view replaces this file write with an HTTP response and reuses
    build_scene_view/render_scene_html unchanged."""
    path = store.root / "scene.html"
    path.write_text(render_scene_html(build_scene_view(store)), encoding="utf-8")
    return path
