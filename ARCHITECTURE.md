# Architecture

The engine-first design in one paragraph: `dm-engine` is the complete
mechanical game — rules lookup, audited dice, persistent campaign state —
exposed as an MCP server whose tools map 1:1 onto a command registry.
The LLM (Claude, driven by the `dm-session` skill) is a narrative layer
that issues commands and narrates results; it never computes or records
mechanical facts itself. `registry.execute` is the only mutation path:
each command runs in one SQLite transaction covering the state changes,
the event-log row, and the RNG position. Illegal actions come back as
structured refusals (`ok=False` + a human-readable reason), never
exceptions; handler exceptions are engine bugs — they roll back and a
crash event is committed separately so the audit trail keeps the attempt.

Layering (each layer only calls downward):

| Layer | Path | Role |
|---|---|---|
| Adapters | `src/dm_engine/mcp/`, `src/dm_engine/cli/` | thin 1:1 surfaces over the registry; MCP tool schemas are introspected from handler signatures |
| Commands | `src/dm_engine/commands/` | handlers registered via `@command`; all game-state orchestration |
| Rules | `src/dm_engine/rules/` | pure logic (dice, checks, attacks, economy, conditions, mitigation); no I/O, property-tested |
| State | `src/dm_engine/state/` | typed accessors over the campaign SQLite + sheet materialization; dumb and safe |
| Content | `src/dm_engine/content/` | seeds and reads the static SRD rules DB |

Both database schemas are documented in `docs/SCHEMA.md`.

## Frozen Contracts

These are settled user decisions and cross-milestone interfaces — consume
them exactly as written; do NOT redesign them. If an implementation
reality genuinely conflicts with one, STOP and ask the user. (Extracted
verbatim on 2026-07-10 from `docs/superpowers/plans/2026-07-08-roadmap.md`,
the Phase 1 planning record; this file is the authoritative copy.)

### FC-1: Command result envelope
Every command returns this shape (pydantic model in `dm_engine/commands/envelope.py`; the MCP and CLI layers serialize it verbatim):

```python
class CommandResult(BaseModel):
    ok: bool                       # False = structured refusal (never an exception for illegal actions)
    command: str                   # registry name, e.g. "attack"
    refusal: str | None = None     # human-readable reason when ok=False, e.g. "Kira has no 2nd-level slots remaining"
    digest: str                    # one-line narration hook, e.g. "Goblin 2 hits Kira for 5 slashing (17 vs AC 15)"
    data: dict[str, Any]           # command-specific structured payload
    gm_only: bool = False          # True → hide from player (Phase 2/3 enforce; Phase 1 honor system)
    event_ids: list[int] = []      # event_log rows appended by this command
```

### FC-2: Dice interface
```python
class Roll(BaseModel):
    notation: str                  # "1d20+5", "8d6"
    rolls: list[int]               # individual die results
    modifier: int
    total: int
    player_supplied: bool = False  # True when the player rolled physically and reported the value
    gm_only: bool = False          # hidden DM roll

class DiceRoller(Protocol):
    def roll(self, notation: str, *, player_value: int | None = None, gm_only: bool = False) -> Roll: ...
```
- One RNG per campaign, seeded at creation, seed stored in the `campaign` table; every `Roll` is recorded in the event log entry that caused it.
- `player_value` bypasses RNG (the raw die total the player reported, before modifiers) and sets `player_supplied=True`.
- All of the player PC's dice accept `player_value`; companion/monster dice never do. Any PC roll may be delegated to the engine (the `/roll` path = simply omitting `player_value`).

### FC-3: Command registry
- Commands are functions registered by name: `@command("attack")`, signature `fn(ctx: CommandContext, **kwargs) -> CommandResult`. `CommandContext` carries the open campaign store, the seeded `DiceRoller`, and the rules DB connection.
- `registry.execute(name, ctx, **kwargs)` is the ONLY mutation path. MCP tools and CLI `dm cmd` are thin 1:1 adapters over it.
- Illegal/invalid actions return `ok=False` refusals; engine bugs raise (and are fixed), they are never swallowed.

### FC-4: Range bands
`Band = Literal["engaged", "near", "far", "distant"]` with thresholds 5/30/60/120 ft. Position state = band relative to the scene plus an `engaged_with` set. Leaving `engaged` without Disengage provokes an opportunity attack. AoE spells hit up to `data.max_targets` creatures within one band per the spell's effect record.

### FC-5: Storage layout
- Rules DB (static, rebuilt by `dm seed`, never written during play): `data/build/rules.sqlite` (gitignored). Contains a `meta` table with at least `('edition', '2014')` and `('srd_version', '5.1')`.
- Campaigns: `campaigns/<slug>/campaign.sqlite`; snapshots copied to `campaigns/<slug>/snapshots/<ISO-timestamp>.sqlite` at session start. `campaigns/` is gitignored. The `campaign` table records the edition it was created under.
- Vendored SRD sources are edition-tagged: `data/srd/2014/text/` (fork markdown, SRD 5.1), `data/srd/2014/structured/` (5e-bits JSON), `data/srd/ATTRIBUTION.md`. Committed. A future 2024/SRD-5.2 migration adds `data/srd/2024/` and re-seeds — it must not require engine redesign.

### FC-6: Event log (authoritative audit trail, append-only)
```sql
CREATE TABLE event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),   -- real time; in-game time lives in world_clock
    command TEXT NOT NULL,
    inputs TEXT NOT NULL,        -- JSON kwargs as received
    result TEXT NOT NULL,        -- JSON CommandResult
    rolls TEXT NOT NULL,         -- JSON list[Roll], player_supplied/gm_only flags included
    is_ruling INTEGER NOT NULL DEFAULT 0,
    rationale TEXT               -- REQUIRED (non-null, non-empty) when is_ruling=1
);
```
Every command execution = exactly one SQLite transaction covering state-table updates + its event row.

*Amendment (2026-07-10, TVA-12):* on a handler exception that transaction rolls back in full, and a crash event row (`digest` prefixed `ENGINE CRASH:`, empty `rolls`, RNG position not persisted) is then committed in its own best-effort transaction, so the audit trail records the attempt without weakening the state-rollback or replay guarantees.

### FC-7: Settled gameplay decisions (do not reopen)
Ruleset edition: 2014 rules / SRD 5.1 (5e-bits' 2024 dataset is incomplete as of 2026-07 — no spells, ~a dozen monsters; user chose 2014-now-migrate-later, so edition-tag data paths and DB meta but do NOT build dual-edition engine logic); full-RAW enforcement; LLM-generated campaign (skeleton + starting region up front, lazy beyond); 1 PC + 1–3 DM-generated, in-fiction-recruited, mortal, autonomous companions; XP progression, engine-awarded, non-combat resolutions earn encounter XP; death mode per campaign (`narrative` default / `hardcore`), death saves identical in both; ability scores by player's choice of rolled/array/point-buy, companions use array; tiered spell automation; advisory encounter budget (computed & reported, deviation allowed and logged); `dm_ruling` full power + mandatory rationale + `dm audit`; explicit `end_session` recap + auto mini-recap checkpoint every ~20 events; `gm_only` on all payloads from day one; levels 1–5 verified on 1–20 schema.
