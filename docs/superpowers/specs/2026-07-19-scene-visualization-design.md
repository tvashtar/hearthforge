# Scene visualization — design

**Date:** 2026-07-19
**Status:** approved design, pre-implementation

## Purpose

Players need a way to *see* the scene — especially combat, where band
positions, engagements, HP, and conditions are easy to lose track of in
prose. The visualization must be:

1. **Faithful** — a pure render of engine state, never LLM-drawn, so it
   cannot contradict the DB.
2. **Consistent turn-to-turn** — deterministic layout; nothing jitters
   between renders.
3. **Usable by the DM skill** — narrative scene furniture (a wagon, a
   cliff) can be pinned into the picture via engine commands.

Chosen approach: an **engine-rendered, self-refreshing HTML/SVG file**
(`campaigns/<slug>/scene.html`), materialized after every successful
command on the same hook as character sheets. No server process, no new
dependencies. The design deliberately enables a future live web view
("approach C") without rework.

Rejected alternatives (recorded so they aren't relitigated): markdown
scene sheet (too cramped to be a real visualization), live server now
(a second process and dependency surface that buys nothing at solo-play
pace), LLM-composed diagrams (no fidelity guarantee), external packages
`svgwrite`/`rich`/`mermaid`/`graphviz`/`jinja2` (each is the wrong
medium or a needless dependency — f-string SVG suffices and keeps the
render path property-testable).

## Architecture

Three stages with strict boundaries; the future live server reuses the
first two untouched:

```
campaign state (store)
      │
      ▼
build_scene_view(store) ──► SceneView          pure builder, pydantic model,
      │                     (JSON-serializable)  future server emits this as JSON/SSE
      ▼
render_scene_html(view) ──► HTML string        pure renderer, inline SVG,
      │                                          same view → byte-identical output
      ▼
materialize_scene(...) ──► campaigns/<slug>/scene.html   thin I/O adapter;
                                                          server swaps in an HTTP response
```

- New module `src/dm_engine/state/scene.py`, sibling to `sheets.py`
  (same layer, same role: state → rendered artifact, no game logic).
- **Hook:** `registry.execute` already re-materializes character sheets
  after every successful command; scene materialization joins that hook,
  inside the same transaction discipline. No per-turn DM tool call; the
  picture cannot drift from the DB; crash/resume re-renders correctly
  because it is a pure function of state.
- **Liveness:** the HTML shell carries
  `<meta http-equiv="refresh" content="2">` — the player opens
  `scene.html` in a browser tab once and it tracks play with ~2 s lag.
  The refresh mechanism lives only in the outer shell template so the
  future server can replace it with SSE without touching builder or
  renderer.
- **`SceneView`** is one model with `mode: "combat" | "scene"`:
  combat mode carries bands/tokens/engagements/initiative; scene mode
  carries the location/time/NPCs card. One file, both modes.

## Scene props

New campaign-store table `scene_props`, created with
`CREATE TABLE IF NOT EXISTS` on store open (in-place migration for
existing campaigns, same pattern as `active_effects`):

| Column | Notes |
|---|---|
| `name` | display text, UNIQUE, upsert key (`"overturned wagon"`) |
| `band` | `engaged/near/far/distant` or NULL (ambient, scene-wide) |
| `note` | optional free text rendered as subtitle/hover |

Two new registry commands (auto-exposed as MCP tools via signature
introspection, like all commands):

- `add_scene_prop(name, band=None, note=None)` — upserts on `name`
  (moving the wagon = call again with the new band). Refuses on an
  invalid band. FC-1 envelope, digest e.g.
  `"Prop: overturned wagon (near)"`.
- `remove_scene_prop(name)` — refuses if unknown, listing known props
  (mirrors the `unknown NPC '…' (known: …)` refusal pattern).

Lifecycle:

- `set_scene` **clears all props** — new scene, new furniture. Its
  digest reports it (`"Scene set: … (3 props cleared)"`).
- `start_combat` / `end_combat` do **not** touch props — combat happens
  *in* the current scene.
- `get_scene_state` gains a `props` list for DM re-orientation after
  resume.
- Props are player-visible by definition. `gm_only` material never
  becomes a prop; the dm-session skill states this explicitly.

## Renderer

### Combat mode

Inline SVG built with f-strings, stdlib only:

- **Four fixed horizontal band tracks** (`ENGAGED / NEAR / FAR /
  DISTANT`), always all four, fixed heights — the frame never moves.
- **Tokens:** rounded rects with display names. Party and monsters get
  distinct hues; defeated tokens gray out and shift to the track edge;
  the active-turn token gets a highlight ring.
- **Engagement links:** the engine guarantees a mutually `engaged_with`
  pair always shares a band (`engage` adopts the target's band; `move`
  breaks links). Links are therefore drawn **in whatever band track the
  pair shares** — the ENGAGED track means "within 5 ft of the scene's
  center of action", *not* "in melee". A companion at `far` engaged
  with a goblin at `far` renders as two adjacent tokens in the FAR
  track joined by a melee link, with nothing implied in the ENGAGED
  track.
- **HP secrecy in engine code, not LLM discipline:** party tokens show
  numeric `hp/max_hp` plus a fill bar. Monster tokens never show
  numbers — the renderer computes the condition word from the
  computable thresholds (full → *fresh*; >half → *wounded*;
  ≤half → *bloodied*; ≤quarter → *staggering*) and shows it as a
  tinted badge. The fifth tier, *near death* ("one hit from
  dropping"), is a DM judgment call with no deterministic formula —
  it stays a narration-level word and is deliberately not rendered.
- **Condition badges** under each token; unknown condition slugs render
  as plain text badges (no allowlist to drift).
- **Props** render as neutral diamond markers on their band track;
  ambient (NULL-band) props in a strip along the top.
- **Initiative strip** across the top: order, active combatant
  highlighted, round number.

Layout determinism (the no-jitter rule):

- Within a band, engaged clusters lay out adjacently (short, legible
  links); clusters and loose tokens are then ordered by initiative,
  which is stable for the whole encounter.
- A token's position changes **only** when its band or its engagement
  set changes — both are real state changes the player should see move.
- No timestamps, no randomness in the SVG: same `SceneView` →
  byte-identical HTML.

### Scene mode (out of combat)

A simple card, no SVG gymnastics: location name, scene description,
day + time-of-day (from `world_clock`, the only time authority), NPCs
present, a party status row (name, `hp/max_hp`, conditions — party
numbers are public), and current props. Header: campaign name. Footer:
`as of event #N` so a stale page is distinguishable from a live one.

## Error handling

Consistent with engine philosophy, no special cases:

- Builder and renderer are pure; an exception in them is an engine bug
  and **propagates**, rolling back the command's transaction exactly as
  a sheet-rendering bug would. Render errors are never swallowed — a
  wrong picture is worse than a loud failure.
- The prop commands follow FC-1: invalid band / unknown prop →
  `ok=False` refusal with a steering message; refusals commit their
  event row like every other command.
- Degenerate states are handled by construction, not guard clauses:
  empty bands render empty; no active combat renders scene mode.

## Testing

- `tests/state/test_scene_view.py` — builder units on the `ctx`/`party`
  fixtures: mode selection, band grouping, engagement pairs (including
  a pair engaged at `far`), monster condition-word thresholds (boundary
  cases: exactly half, exactly quarter), props merged in,
  defeated handling.
- **The secrecy test that matters:** property-style assertion that for
  arbitrary combat states, no monster's numeric HP appears anywhere in
  the rendered HTML. This guarantee must never regress.
- Renderer determinism: same `SceneView` rendered twice → identical
  strings; two states differing only in active turn → all tokens at
  identical coordinates.
- `tests/commands/test_scene_props.py` — add/upsert/remove/refusals,
  `set_scene` clears props, `get_scene_state` includes them.
- `tests/integration/` — through `registry.execute` only: after
  `start_combat`, `scene.html` exists in combat mode; after
  `end_combat`, back to scene mode; after a refused command, the file
  still matches the DB.

## Docs and skill changes (same PR)

- `docs/SCHEMA.md`: document `scene_props`.
- dm-session `SKILL.md`: tell the player once per session that
  `campaigns/<slug>/scene.html` is the live scene view (next to the
  existing "keep your sheet open" line); pin notable terrain/features
  as props *in the same breath* as narrating them (Iron rule 4's
  persistence pattern); never put `gm_only` material in a prop.
- `README.md`: player-facing feature blurb.

## Future (explicitly out of scope now)

- **Approach C, live web view:** `dm view --campaign <slug>` serving
  `SceneView` over SSE with the same renderer. Enabled by the
  builder/renderer/adapter split; requires no changes to this design.
- Location/travel maps, quest dashboards: not modeled, not designed.
