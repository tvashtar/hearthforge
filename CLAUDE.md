# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An engine-first AI Dungeon Master for solo D&D 5e (2014 rules / SRD 5.1).
The `dm-engine` Python package is the complete mechanical game — rules,
dice, persistent campaign state — exposed as an MCP server (`.mcp.json`
wires it up). Claude is the narrative layer on top, driven by the
`dm-session` skill (`.claude/skills/dm-session/`): it issues engine
commands and narrates results, never computing mechanical facts itself.

## Commands

```bash
uv sync                                   # install (uv project, Python >= 3.12)
uv run dm seed                            # build data/build/rules.sqlite — REQUIRED before tests/play
uv run pytest                             # full suite
uv run pytest tests/commands/test_spells.py -k ritual   # single file / test
uv run ruff check src tests              # lint (line length 100)
uv run dm --help                          # debug CLI: cmd, audit, sheet, lookup, new, resume, mcp
uv run dm cmd <command> --campaign <slug> --json '{...}'  # execute one registry command
```

`scripts/sync_srd.py` re-vendors SRD data from upstream; the vendored copy
under `data/srd/` is committed, so this is rarely needed.

## Frozen contracts — read before touching interfaces

`ARCHITECTURE.md` defines FC-1..FC-7 (result envelope, dice interface,
command registry, range bands, storage layout, event log, settled gameplay
decisions). These are settled user decisions — consume them exactly as
written, do not redesign them. FC-7 in particular lists gameplay rulings
(2014 edition, XP progression, death modes, tiered spell automation) that
must not be reopened.

## Architecture

Layered, with one mutation path:

- `src/dm_engine/rules/` — pure rules logic (dice, checks, attacks, action
  economy, conditions, damage mitigation, death saves). No I/O, no store
  access; property-tested with hypothesis.
- `src/dm_engine/commands/` — the command handlers, registered via
  `@command("name")` in `registry.py`. `registry.execute(name, ctx, **kw)`
  is the ONLY way state changes: it wraps each handler in one SQLite
  transaction covering the state mutations, the event-log row, the RNG
  position, and (on success) re-rendered character sheets.
- `src/dm_engine/state/` — `store.py` (typed accessors over
  `campaigns/<slug>/campaign.sqlite`; dumb and safe, no game logic),
  `sheets.py` (markdown sheet materialization).
- `src/dm_engine/content/` — `seed.py` builds the static rules DB from
  vendored SRD JSON; `lookup.py` reads it during play.
- `src/dm_engine/mcp/server.py` and `src/dm_engine/cli/` — thin 1:1
  adapters over the registry. MCP tool schemas are introspected from
  handler signatures, so adding a parameter to a command automatically
  updates the tool surface.

Both database schemas are documented in `docs/SCHEMA.md` (campaign store +
rules DB); the schema constants in `state/store.py` and `content/seed.py`
are the source of truth.

### Engine philosophy (enforced, not aspirational)

- **Refusals, not exceptions.** Illegal/invalid actions return
  `ok=False` with a human-readable `refusal` on the FC-1 envelope. Handler
  exceptions are engine bugs: they roll back the transaction and propagate.
- **Validate before consuming.** The registry commits refusals (only
  exceptions roll back), so every refusal-producing check must run before
  a handler spends slots/HP/economy — see `cast_spell` step 4 and
  `dm_ruling`'s validate-then-apply batch for the pattern.
- **Every die is audited.** One seeded RNG per campaign; all rolls flow
  through `ctx.roller` and land in the event row. The PC's dice accept
  `player_value` (reported physical rolls, flagged `player_supplied`);
  companion/monster dice never do. For arbitrary ruling dice use the
  `roll_dice` command — never out-of-engine RNG.
- **Tiered spells.** Records with resolvable mechanics (`heal_at_slot_level`
  or `damage` *with* a `damage_type`) resolve fully (Tier 1); everything
  else consumes the slot and returns `needs_ruling` for `dm_ruling` to
  apply (Tier 2). Rituals (`ritual=True`) spend +10 world-clock minutes
  instead of a slot.
- **Combat position** is a band (`engaged/near/far/distant`) relative to
  the scene plus a mutual `engaged_with` set — `engage` adopts the
  target's band and links the pair; melee legality checks the set, not
  the band.

## Conventions

- New commands: handler in the topical module under `commands/`, signature
  `fn(ctx, ..., **kwargs) -> CommandResult`, registered with `@command`;
  the module must be imported by `commands/__init__.py` to register.
- Tests live in `tests/commands/` (per-module), `tests/rules/` (pure
  logic), `tests/integration/` (end-to-end through `registry.execute`
  only). Fixtures in `tests/conftest.py`: `ctx` (fresh campaign store +
  recorded roller, seed 99) and `party` (Kira the PC fighter + Brother
  Aldric, a level-3 companion cleric with slots).
- Specs go in `docs/superpowers/specs/`, plans in
  `docs/superpowers/plans/`.
- When Claude merges a PR, it first checks README.md against the PR's
  changes and updates it in the same PR if anything is stale — player-
  facing behavior, setup steps, and the CLI/debug surface must never lag
  a merge.

## Live data warning

`campaigns/` contains real ongoing campaigns (gitignored). Never mutate
them outside engine commands: use `dm cmd` (audited) for state surgery,
scratch slugs (`dm new`) for experiments, and delete scratch campaigns
afterwards. Reading with `sqlite3` is fine — that's the DM screen.
