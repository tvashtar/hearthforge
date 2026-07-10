# Read-only campaign state tools

**Date:** 2026-07-10
**Status:** Approved

## Problem

The MCP surface can write NPCs (`create_npc`) but nothing can read them back —
the store itself has no NPC read accessor. Locations are readable only as the
current scene's record, recaps only as the latest, and the event log only via
the debug CLI. During live sessions the DM (Claude) had to fall back to
`Bash(sqlite3 …)` against the live campaign DB to recall NPC dispositions,
which required broad Bash allowlist entries. Goal: every read a DM needs
mid-session sits behind an allowlisted, audited dm-engine MCP tool, so a
fresh checkout is instantly playable with no raw-SQL permissions.

## Design

### Store accessors (`src/dm_engine/state/store.py`)

Dumb, safe, no game logic — matching the existing accessor style:

- `get_npc(name) -> dict | None`
- `npcs(location_slug: str | None = None) -> list[dict]`
- `locations() -> list[dict]`
- `recaps() -> list[dict]` (oldest first)
- `events_tail(limit: int) -> list[dict]` — newest first, projecting
  `{id, command, ok, digest, created_at}`; `ok`/`digest` parsed from the
  stored result JSON, not full rows.

### Commands

Registered via `@command` in the existing topical modules, so they
auto-surface as MCP tools. All are pure reads; the registry still logs an
event row per call (consistent with `get_campaign_brief` today).

| Command | Module | Returns | Refusals | gm_only |
|---|---|---|---|---|
| `get_npc(name)` | `world.py` | full record: disposition, location, notes | unknown name → refuse, listing known names | yes |
| `list_npcs(location_slug=None)` | `world.py` | compact name/disposition/location | unknown location slug → refuse (never silently `[]`) | no |
| `list_locations()` | `world.py` | slug, name, region per location | — | no |
| `list_recaps()` | `campaign.py` | kind, content, created_at, oldest first | — | yes |
| `get_events(limit=20)` | `campaign.py` | newest-first compact digests | limit < 1 → refuse; limit > 100 clamped to 100 | yes |

`gm_only=True` marks NPC notes / recaps / event digests as DM-screen
material the narration layer should paraphrase, not read verbatim.

### Scene state fold

`get_scene_state` gains `npcs_present`: compact name + disposition for NPCs
whose `location_slug` matches the current scene location. Empty list when
the scene has no location or no NPCs there.

### Allowlist

Add the five new `mcp__dm-engine__*` names to `.claude/settings.json`
`permissions.allow`. After this ships, `Bash(sqlite3 *)` in
`settings.local.json` is debugging-only and can be pruned at the user's
discretion.

## Testing

- Per-command happy path + refusal tests in `tests/commands/` (existing
  per-module files), through `registry.execute` with the `ctx`/`party`
  fixtures.
- `get_scene_state` fold: NPC at scene location appears; NPC elsewhere
  does not.
- `get_events` sees a crash event row in the tail (integrates with the
  crash-audit behavior added for TVA-12).

## Out of scope

- No schema changes (`docs/SCHEMA.md` unchanged).
- No write-side changes; `create_npc` remains the NPC upsert path.
- No pagination beyond `get_events(limit)` — campaign tables are small at
  solo-play scale (YAGNI).
