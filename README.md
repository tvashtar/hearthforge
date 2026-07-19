<p align="center">
  <img src="docs/assets/hearthforge_splash.jpg" alt="Hearthforge — a tavern table with a campaign map, dice, and a quill beside a roaring hearth" width="700">
</p>

# Hearthforge

A solo D&D 5e campaign by the fire: Claude tells the story, a local rules
engine rolls the dice. Different every playthrough and you set the scene. 

Hearthforge is engine-first. The `dm-engine` is the complete mechanical
game — rules lookup (2014 rules / SRD 5.1), audited dice, and persistent
campaign state — exposed as an MCP server. Claude is the narrative brain
on top: it never computes or invents mechanical facts, only issues
commands and narrates the results. Play happens inside
[Claude Code](https://claude.com/claude-code), driven by the `dm-session`
skill.

**Local-first, solo by design.** Apart from Claude Code itself, everything
runs and stays on your machine: campaigns, characters, dice audit logs, and
the rules database are SQLite files in this directory, and nothing is sent
to any external API. The MCP server is a local subprocess Claude Code talks
to over stdio — it never reaches out to an external service. (The only
other network use is optional: `scripts/sync_srd.py`, if you choose to
re-fetch the vendored SRD data from GitHub.)

## Quickstart

You need [uv](https://docs.astral.sh/uv/) and
[Claude Code](https://claude.com/claude-code).

```
git clone https://github.com/tvashtar/hearthforge.git
cd hearthforge
uv sync
claude
```

Then just say **"start a campaign"**. (The rules database builds itself
from the vendored SRD data on first launch.) Claude interviews you for tone,
character concept, companions, and death mode, then builds your starting
world and the adventure begins. Next time, "continue my campaign" picks up
from the recap.

## Playing

- You roll your own character's dice and report the raw totals; the engine
  rolls everything else and records every die in an audit log.
- Keep your character sheet open in an editor —
  `campaigns/<slug>/sheets/<you>.md` live-updates as you play, including
  your class features for your current level and per-spell tags (level,
  V/S/M components, ritual, concentration).
- **Live scene view** — the engine renders `campaigns/<slug>/scene.html`
  after every command: a combat band map (positions, engagements, party
  HP, monster condition words — never their numbers) or an
  out-of-combat scene card. Keep it open in a browser tab; it refreshes
  itself. The DM pins scene furniture into it with
  `add_scene_prop`/`remove_scene_prop`.
- No permission prompts: `.mcp.json` wires up the engine and the committed
  project settings preapprove the gameplay tools.

## Playtest feedback

After a session, ask Claude to "run a retro on this session" (the
`dm-retro` skill): it mines the session's audit log and transcript for
engine bugs, crashes, and friction, and produces an evidence-backed
findings report — send that along with your impressions.

## How it works

`ARCHITECTURE.md` describes the layering and the frozen engine contracts;
`docs/SCHEMA.md` documents both databases (campaign store + rules DB).

## Debug surface

The engine ships a CLI (`uv run dm --help`) for inspecting state outside of
a live session:

- `dm cmd` — execute one registry command against a campaign and print its
  result.
- `dm audit` — print every `dm_ruling` event: id, timestamp, rationale, and
  digest.
- `dm sheet <character> --campaign <slug>` — print a character's rendered
  markdown sheet (read-only, no snapshot).
- `dm lookup` — query the seeded SRD rules database (`rule`, `monster`,
  `spell`, `feature` subcommands).
- `dm new` — create a new campaign with a minimal skeleton.
- `dm resume` — open a campaign (snapshotting it) and print the session
  brief.

## Model evals

`uv run dm-eval` benchmarks which Claude model (and thinking effort) makes
the best DM. Each cell of the matrix plays the same seeded scenario — a few
narrative beats, then a bandit ambush — as a real DM session (dm-engine MCP
+ the dm-session skill), driven by a fixed Haiku player agent. Runs are
timed, transcripts archived under `evals/runs/<timestamp>/` (gitignored),
graded mechanically from the event log, and scored by a blind Opus judge.

- Default matrix: `haiku`, `sonnet`, `opus`, `fable` (latest of each family,
  never pinned) at `medium` effort, always run weakest-first so limits bite
  last.
- `--cells opus:high,sonnet:low` — explicit cells; `--reps N` — repeats with
  fresh seeds; `--parallel N` / `--serial` — concurrency; `--smoke` — one
  cheap Haiku cell through two beats to verify wiring; `--judge-only
  <run-dir>` — re-grade existing bundles.
- Credentials: uses `ANTHROPIC_API_KEY` (or an `ant auth login` profile) for
  the player/judge when present, otherwise falls back to headless Claude
  Code sessions using your existing login.
- Scratch campaigns are created under `campaigns/eval-*` and deleted after
  each run's bundle is archived.

## Attribution

Rules content is derived from the [SRD 5.1](data/srd/ATTRIBUTION.md),
licensed under CC-BY-4.0.
