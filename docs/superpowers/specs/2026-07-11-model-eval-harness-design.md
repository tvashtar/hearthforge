# Model Evaluation Harness — Design (TVA-30)

**Date:** 2026-07-11
**Ticket:** TVA-30 — Create a timed and graded model evaluation test
**Status:** Approved design, pre-implementation

## Overview

A standalone eval harness that plays the same short campaign — narrative
beats followed by a moderately complex combat — across a matrix of
(model × thinking effort) DM configurations, times each run, archives
transcripts, and grades them on two layers: objective metrics computed
from the engine's event log, and a blind LLM judge scoring against a
rubric. Output is a comparison report answering "which model/effort
should the DM run on, and what does each config trade in time vs
quality." Token cost is explicitly not an optimization axis.

## Goals

- Comparable, repeatable runs: identical starting state, seeded dice,
  fixed player behavior, fixed judge.
- Grade what the repo actually cares about: tool-calling discipline
  against the engine, narrative quality, ruling quality, and the
  dm-session skill's iron rules.
- Rerunnable from the command line (`uv run dm-eval`), no live Claude
  Code session required.

## Non-goals

- Not a general-purpose agent benchmark; scenario and rubric are
  specific to this engine and skill.
- Not a cost benchmark: tokens are reported but not scored.
- No pairwise/tournament judging (absolute per-transcript scores only).
- No CI integration of full eval runs (LLM-free metric unit tests only).

## Scenario: "The Missing Caravan"

One fixed scenario spec at `evals/scenarios/caravan_ambush.yaml`.

**Setup (harness-built, not DM-built).** The harness constructs the
starting state directly through `registry.execute` so every run begins
identically: scratch campaign with a fixed RNG seed, a level-3 martial
PC + a caster companion with spell slots (same shape as the test
`party` fixture — exercises both player-rolled and engine-rolled dice),
two NPCs, a quest hook, and an opening scene. The DM under test starts
cold with `open_campaign`, exactly like a real session.

**Player beats.** An ordered list; each beat is designed to force a
graded capability to come up:

| # | Beat | Exercises |
|---|------|-----------|
| 1 | Question the innkeeper about the missing caravan | NPC recall, social skill check |
| 2 | Buy supplies | inventory ops |
| 3 | Travel to the ambush site | `travel`, world clock |
| 4 | Investigate the wreckage | checks; improvised facts must be persisted |
| 5 | Ambush: 4 bandits + leader, mixed melee/ranged | `start_combat`, range bands, `engage`, initiative |
| 6 | Cast a Tier-1 spell (heal wounded companion) | auto-resolved `cast_spell` |
| 7 | Cast a Tier-2 spell (non-automatable) | `needs_ruling` → `dm_ruling` follow-through |
| 8 | Attempt an illegal action (melee at `distant`) | refusal handling: narrate around, don't fight the engine |
| 9 | Finish combat, loot, end session | `end_combat`, XP, checkpoint/`end_session` discipline |

Each beat carries:

- `goal`: what the player is trying to do (prompt material for the
  player agent).
- `done_when`: a machine-checkable predicate against the event log
  (e.g. beat 6: a `cast_spell` row with `ok=1` that healed the
  companion). Checked by the harness after every DM turn.
- `max_player_messages`: budget; on exhaustion the beat is recorded
  **failed** and the player moves to the next beat.
- Optional scripted `player_value` dice reports ("I rolled a 17") on a
  few beats, to exercise the player-supplied path deterministically.

## Architecture

```
evals/
  __init__.py
  run.py            # CLI entry (dm-eval console script)
  runner.py         # one cell: DM session loop via claude-agent-sdk
  player.py         # beat-driven player agent (Anthropic API)
  scenario.py       # load/validate scenario yaml, build starting state
  metrics.py        # layer-1 mechanical metrics from event log + transcript
  judge.py          # layer-2 blind judge (Anthropic API)
  report.py         # comparison report assembly
  scenarios/
    caravan_ambush.yaml
  runs/             # gitignored output bundles
```

Top-level `evals/` package, outside `src/dm_engine`, never shipped.
Dependencies (`anthropic`, `claude-agent-sdk`) live in a dedicated
`eval` dependency group. `evals/runs/` is gitignored.

### DM runner (one matrix cell)

- `claude-agent-sdk` session, `cwd` = repo root, project settings
  loaded — the DM gets the real `dm-session` skill and dm-engine MCP
  server, the same context a production session sees.
- Model set per cell. Thinking effort set per cell — **the mechanism
  (settings flag vs env) is verified by an early spike; if effort is
  not controllable per spawned session, the matrix degrades to
  model × (thinking on/off) and the report says so.**
- Permission prompts bypassed; tool allowlist pinned to
  `mcp__dm-engine__*` only (no Bash, no file tools) so a model cannot
  score well by working around the engine.
- The harness records, per DM turn: wall-clock start/stop, token usage,
  and every tool call + result from the stream.

### Player agent

One fixed cheap config (Haiku, no extended thinking) held constant
across all cells, called via the plain Anthropic API. Input: a persona,
the current beat's `goal`, and the DM narration so far. Output: the
next player message. It never sees the rubric or which model it is
playing against.

### Run loop

```
player message → DM turn (timed, captured) → beat predicates checked
against event log → advance beat or decrement budget → next player
message … until all beats done or a guardrail fires
```

Guardrails: per-turn timeout, max total DM turns, total run timeout.
A run that dies is graded on what exists; **incomplete** is a headline
metric, not a discarded run.

### Orchestration

- `--parallel N` (default 3) runs cells concurrently, each with its own
  scratch campaign slug and MCP server process; `--serial` for the
  cleanest latency numbers.
- Per cell, the bundle `transcript.jsonl`, `timing.json`, and the
  campaign sqlite are copied to `evals/runs/<timestamp>/<model>-<effort>/`,
  then the scratch campaign under `campaigns/` is deleted (live-data
  rule: scratch slugs never linger).

## Grading layer 1 — mechanical metrics

Pure Python over each run's event log + transcript; largely codifies
dm-retro's existing queries.

- **Completion:** beats completed/total, combat ended cleanly, session
  closed with a checkpoint, run complete vs guardrail-killed.
- **Tool discipline:** refusal count; refusal-retry loops (same command
  + near-identical input at consecutive event ids); engine crashes
  (`ENGINE CRASH` digests); schema-rejected tool calls (transcript
  `is_error` that never produced an event row); orphaned Tier-2 casts
  (`needs_ruling` with no subsequent `dm_ruling`); `player_supplied`
  rolls on non-PC actors.
- **Efficiency:** total wall clock; per-DM-turn latency (median, p90);
  tool calls per player message; total tokens (input/output); redundant
  polling (repeated `get_scene_state`/sheet reads between actions).

## Grading layer 2 — blind judge

- Fixed judge config: latest Opus, high effort — constant across every
  cell in a report so scores are comparable. The resolved judge model
  id is recorded in the report; re-grading old bundles after the alias
  has moved to a newer Opus is what `--judge-only` is for.
- Judge input: anonymized transcript (model identifiers stripped), the
  scenario spec, the event-log digest, and the `dm-session` skill text
  as the standard being graded against.
- Absolute 1–5 scores with cited justification (transcript offsets /
  event ids) on four dimensions:
  1. **Narrative quality** — prose, pacing, NPC voice, continuity with
     established facts.
  2. **Mechanical fidelity** — never narrates a number without a
     command result; respects refusals; keeps `gm_only` behind the
     screen.
  3. **Ruling quality** — sensible `dm_ruling` rationales, Tier-2
     follow-through, invented facts persisted via upserts.
  4. **Player experience** — responsiveness to player intent, dice
     etiquette (prompting for physical rolls), clarity of stakes.

## Report

`evals/runs/<ts>/report.md`:

1. One-line recommendation up top.
2. Comparison table: cell × (time, tokens, mechanical metrics, judge
   scores).
3. Per-cell notes: judge quotes, crashes, refusal loops, failed beats.

Raw bundles sit alongside for dm-retro-style drill-down.

## CLI

```
uv run dm-eval                                  # default matrix
uv run dm-eval --cells opus:high,sonnet:low
uv run dm-eval --reps 3                         # reps per cell, fresh seed per rep
uv run dm-eval --smoke                          # one Haiku cell, 2 beats: wiring check
uv run dm-eval --serial                         # no parallelism, cleanest timings
uv run dm-eval --judge-only <run-dir>           # re-grade existing bundles
```

Default matrix (focused): `haiku`, `sonnet`, `opus`, `fable` — all at
medium effort. Cells use family aliases, not pinned version ids: each
run resolves to the latest model in that family, and the resolved model
id is recorded in the run bundle and report for provenance.
Configurable via `--cells`.

**Run order is always ascending model ability** (Haiku → Sonnet → Opus
→ Fable), regardless of the order given to `--cells`, so as many cells
as possible complete before account/usage limits bite. With
`--parallel`, cells are *launched* in ascending order too; the queue
never starts a stronger model while a weaker one is still waiting.

`--reps N` reruns each cell with a different campaign seed per rep and
reports mean/spread. Default 1.

## Error handling

- Cell-level failure (SDK crash, MCP server death) marks the cell
  errored in the report and does not abort other cells.
- Guardrail-terminated runs are graded on partial evidence and flagged
  incomplete.
- Judge output is schema-validated (scores 1–5 + justification per
  dimension); a malformed judge response is retried once, then the cell
  is reported with mechanical metrics only and a judge-failed flag.
- Scratch campaign cleanup runs in a `finally`: bundles are copied
  before deletion; on copy failure the campaign is left in place and
  the path is reported rather than deleted.

## Testing

- Unit tests for `metrics.py` against a fixture event log (crafted
  sqlite with known refusals, retry loops, an orphaned Tier-2 cast) —
  each metric must catch its planted defect.
- Unit tests for beat `done_when` predicate evaluation.
- No LLM calls in CI. `--smoke` is the manual end-to-end wiring check.

## Risks / open items

- **Thinking-effort control per SDK session** is the one unverified
  mechanism; spike it first (10 min). Fallback: model × (thinking
  on/off) matrix, stated plainly in the report.
- Parallel runs share account rate limits, which can skew latency;
  `--serial` exists for timing-sensitive comparisons.
- Judge scores are absolute, not pairwise — slightly weaker at
  separating close cells; `--reps` is the intended remedy for close
  calls.

## Settled decisions (do not reopen without user)

- Player side: beat-scripted LLM player (fixed cheap model), not a
  verbatim script and not a free-form player.
- Grading: mechanical metrics + blind judge, both layers required.
- Default matrix: haiku / sonnet / opus / fable family aliases (latest
  version of each, never pinned) at medium effort; full sweeps are
  opt-in. Resolved model ids recorded for provenance.
- Cells always run in ascending model ability so limits hit last.
- 1 rep default; `--reps` for confidence.
- DM tool allowlist is dm-engine-only during evals.
- Judge grades transcripts independently (absolute scores).
