# llm-dungeon-master

An engine-first AI Dungeon Master for solo D&D 5e (2014 rules) campaigns. The
`dm-engine` is the complete mechanical game — rules lookup, dice, and
persistent campaign state — exposed as an MCP server. Claude is the
narrative brain on top: it never computes or invents mechanical facts, only
issues commands and narrates the results. Phase 1 targets play inside Claude
Code, driven by the `dm-session` skill.

## Setup

```
git clone <this repo>
cd llm-dungeon-master
uv sync
uv run dm seed
```

`uv run python scripts/sync_srd.py` re-vendors the SRD 5.1 data from the
upstream `5e-database` source — the vendored data under `data/srd/` is
already committed, so you only need this if you want to refresh it.
`uv run dm seed` builds `rules.sqlite` from that vendored data.

## Verify

```
uv run pytest
```

## Play

Open the repo in Claude Code — `.mcp.json` wires up the `dm-engine` MCP
server automatically, no extra configuration needed. Say something like
"start a new campaign" and the `dm-session` skill takes it from there: it
interviews you for tone, character concept, companions, and death mode,
then generates and persists a starting region and campaign skeleton.

Keep your character sheet open in an editor while you play —
`campaigns/<slug>/sheets/<you>.md` is regenerated after every command, so it
live-updates as the session progresses.

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
