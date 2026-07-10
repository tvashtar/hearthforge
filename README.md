# llm-dungeon-master

An engine-first AI Dungeon Master for solo D&D 5e (2014 rules) campaigns.
The `dm-engine` is the complete mechanical game — rules lookup, audited
dice, and persistent campaign state — exposed as an MCP server. Claude is
the narrative brain on top: it never computes or invents mechanical facts,
only issues commands and narrates the results. Play happens inside
[Claude Code](https://claude.com/claude-code), driven by the `dm-session`
skill.

**Local-first, solo by design.** Apart from Claude Code itself, everything
runs and stays on your machine: campaigns, characters, dice audit logs, and
the rules database are SQLite files in this directory, and nothing is sent
to any external API. The MCP server is a local subprocess Claude Code talks
to over stdio — it never reaches out to an external service. (The only
other network use is optional: `scripts/sync_srd.py`, if you choose to
re-fetch the vendored SRD data from GitHub.)

## Requirements

- [uv](https://docs.astral.sh/uv/) (Python 3.12+ is fetched automatically)
- Claude Code

## Setup

```
git clone <this repo>
cd llm-dungeon-master
uv sync
uv run dm seed          # builds the SRD rules database
uv run pytest           # optional: verify everything works
```

The SRD 5.1 data is vendored under `data/srd/` and already committed;
`uv run python scripts/sync_srd.py` re-fetches it from upstream, which you
only need if you want to refresh it.

## Play

Open the repo in Claude Code — `.mcp.json` wires up the `dm-engine` MCP
server and the committed project settings preapprove the gameplay tools, so
sessions run without permission prompts. Say something like "start a new
campaign" and the `dm-session` skill takes it from there: it interviews you
for tone, character concept, companions, and death mode, then generates and
persists a starting region and campaign skeleton. Next time, "continue my
campaign" resumes from the recap.

You roll your own character's dice at the table and report the raw totals;
the engine rolls everything else and records every die in an audit log.
Keep your character sheet open in an editor while you play —
`campaigns/<slug>/sheets/<you>.md` regenerates after every command, so it
live-updates as the session progresses.

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
  `spell` subcommands).
- `dm new` — create a new campaign with a minimal skeleton.
- `dm resume` — open a campaign (snapshotting it) and print the session
  brief.

## Attribution

Rules content is derived from the [SRD 5.1](data/srd/ATTRIBUTION.md),
licensed under CC-BY-4.0.
