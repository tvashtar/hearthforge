---
name: dm-retro
description: Use when asked to analyze a past DM session for improvements — errors, crashes, refusal loops, or inefficiencies in how the DM agent played. Also after any session that felt slow, buggy, or required manual state fixes.
---

# DM Session Retrospective

Mine a finished play session for engine bugs, crashes, and inefficiencies,
and turn them into evidence-backed fixes. Core technique: a three-way join
of **transcript tool call → event_log row → snapshot state delta**. Every
finding must cite its evidence (event ids, transcript offsets); no
vibes-based suggestions.

Everything here is READ-ONLY: never mutate `campaigns/` outside engine
commands, never open a multi-MB `.jsonl` fully — stream with `jq`/`rg`.

## Step 0 — pin the session window

```bash
# session starts are first-class audit events (TVA-26)
sqlite3 campaigns/<slug>/campaign.sqlite \
  "SELECT id, created_at FROM event_log WHERE command='open_campaign'"
# session = rows from one open_campaign to the next (or to end of log)
```

For sessions played before TVA-26 landed, fall back to reconstructing the
window from recaps:

```bash
sqlite3 campaigns/<slug>/campaign.sqlite \
  "SELECT id, created_at, kind FROM session_recaps ORDER BY id"
# session = event_log rows between the prior session_end and this one
sqlite3 campaigns/<slug>/campaign.sqlite \
  "SELECT MIN(id), MAX(id) FROM event_log WHERE created_at > '<prev end>'"
```

## Where to look

| Source | Path / command | Signals |
|---|---|---|
| Event log | `campaigns/<slug>/campaign.sqlite` → `event_log` | crashes, refusals, retry loops, ruling churn, polling (below) |
| Rulings | `uv run dm audit --campaign <slug>` | rulings that re-implement an existing command; boilerplate rationales |
| Snapshots | `campaigns/<slug>/snapshots/*.sqlite` (one per `open_campaign`) | state drift with no event row (severe); dangling conditions/concentration; combat left active; double-snapshots seconds apart = redundant reopen |
| Transcript | `~/.claude/projects/<project-dir>/*.jsonl` — find via `rg -l 'mcp__dm-engine__open_campaign'`, match timestamps to the window | `is_error` tool results, schema rejections that never reached the engine, tool-call thrash (below), narrator stating mechanics with no matching call, checkpoint cadence (~20 events), player corrections/complaints |
| MCP stderr | `~/Library/Caches/claude-cli-nodejs/<project-dir>/mcp-logs-dm-engine/` | server tracebacks, restarts mid-session |
| Permission fossils | `.claude/settings.local.json` | every accumulated `Bash(...)` approval is an action the engine couldn't express — each one is a missing-command candidate (the sqlite3 entries → NPC read tools, TVA-16) |
| Sheets/recaps | `campaigns/<slug>/sheets/*.md`, `session_recaps` | recap contradicts DB state; stale/malformed sheet render |
| Live skill | `.claude/skills/dm-session/SKILL.md` — `git log -p` for the version live DURING the session | guidance violated vs guidance missing |

## Event-log queries

```bash
# crash events (engine bugs, committed since TVA-12; older sessions: look for id gaps instead)
sqlite3 campaigns/<slug>/campaign.sqlite \
  "SELECT id, command, json_extract(result,'$.digest') FROM event_log
   WHERE json_extract(result,'$.digest') LIKE 'ENGINE CRASH%'"
# refusal histogram — repeated identical refusals = the DM fighting the API
sqlite3 campaigns/<slug>/campaign.sqlite \
  "SELECT command, json_extract(result,'$.refusal') r, COUNT(*) FROM event_log
   WHERE json_extract(result,'$.ok')=0 GROUP BY command, r ORDER BY 3 DESC"
```

Also scan for: same command + near-identical inputs at consecutive ids
(retry loop); Tier-2 `cast_spell` with no prompt `dm_ruling` after it
(orphaned slot spend); `player_supplied` rolls on non-PC actors (must be
impossible); heavy `get_scene_state`/sheet polling between actions.

## The inefficiency lens (most important)

In the transcript, measure **tool calls per player message**. Anywhere the
DM took many exploration steps to figure out what to do next — repeated
lookups of the same fact, read-modify-read cycles, trial-and-error against
refusals, dropping to Bash — ask which of these is missing:

1. **Skill guidance** — the situation recurs and `dm-session/SKILL.md`
   could name the exact command/pattern (cheapest fix).
2. **An MCP command** — the DM assembled the answer from several calls or
   raw SQL; one command should exist (e.g. recall tools from TVA-16).
3. **Architectural context** — the DM re-derived how the engine works
   (tiering, bands, economy) mid-session; a markdown doc (CLAUDE.md
   section, SCHEMA.md, or a new doc) should pre-answer it.

If none fits, it may be an engine bug or a legitimately hard judgment call
— say so rather than forcing a fix.

## Deliverable

One Linear ticket per finding (project `hearthforge`), ordered
crash > state integrity > refusal loop > inefficiency, each with: a
**Session evidence** section quoting event ids / transcript evidence, the
proposed fix and its class, and priority. Refusals that correctly blocked
illegal actions are the engine working — list them as non-findings.
