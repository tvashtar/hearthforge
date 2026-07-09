# AI Dungeon Master — Design Spec

**Date:** 2026-07-08
**Status:** Approved (refined via grill-me session 2026-07-08)
**Goal:** A full-featured, AI-driven dungeon master for a solo D&D 5e campaign. Text-based first, UI later.

## Decisions (settled during brainstorming)

| Question | Decision |
|---|---|
| Rules rigor | Full RAW enforcement — code-enforced mechanics, LLM never touches numbers |
| Campaign source | LLM-generated original campaign with persisted plot skeleton |
| Party | 1 player character + 1–3 AI-run companions (standard encounter balance) |
| DM runtime | Engine-first hybrid: Claude Code as DM brain (Phase 1), API-driven loop (Phase 2), UI (Phase 3) |
| SRD strategy | Vendor both: `dnd-5e-srd` fork markdown (rules prose) + `5e-bits/5e-database` JSON (structured data) |
| Ruleset edition | 2014 rules (SRD 5.1) now — the only edition with complete structured data (5e-bits' 2024 set lacks spells and most monsters as of 2026-07). Layout and DB are edition-tagged so migrating to 2024/SRD 5.2 is a re-vendor + re-seed + verify, not a redesign |
| Session model | Resumable anytime; autosave every command; DB is always the truth |
| Level range | Levels 1–5 hand-verified at launch; schema and engine designed for 1–20 |
| LLM↔engine boundary | Command-resolution engine (Option A): LLM issues commands, engine validates/rolls/persists/returns |
| Combat space | Range bands/zones: engaged / near / far / distant (≈5/30/60/120 ft); AoE via clustering rules |
| Dice | Player rolls all their PC's dice (delegable per-roll via `/roll`); engine rolls companions/monsters; hidden DM rolls where RAW implies a screen |
| Spell automation | Tiered: damage/heal/condition spells fully engine-executed; rest get slot/concentration/duration enforcement + `dm_ruling` resolution |
| Ability scores | Player's choice: rolled (4d6-drop-lowest, player-rolled), standard array, or point buy; companions use standard array |
| Companions | DM-generated to complement the PC, recruited in-fiction, full sheets, can permanently die; autonomous with in-fiction suggestions |
| PC death | Campaign setting: `narrative` (default, defeated-not-dead) or `hardcore` (real death, new PC joins persistent world) |
| Progression | Engine-awarded XP at RAW thresholds; non-combat resolutions earn the encounter's XP |
| World generation | Plot skeleton + starting region up front; everything else lazily generated and persisted when approached |
| Secrecy | Honor system in Phase 1; `gm_only` flag on all payloads from day one for Phase 2/UI enforcement |
| Sessions | Explicit `end_session` recap + silent mini-recap checkpoints every ~20 events |
| `dm_ruling` | Full power, mandatory rationale, prominent event-log marking, `dm audit` CLI review |
| Encounter budget | Advisory: DM must compute and report difficulty, may deliberately deviate (logged) |

## 1. System overview

A Python package (`dm_engine`) is the complete mechanical game — rules, dice, state. An LLM is the narrative brain on top. The LLM never computes or records mechanical facts directly: it issues **commands** (`attack`, `skill_check`, `cast_spell`), the engine validates → rolls → mutates SQLite → returns structured results, and the LLM narrates them. The LLM cannot fudge a roll or forget a spell slot because the numbers never pass through it.

Three phases, one engine:

- **Phase 1 (this spec):** Claude Code is the DM. It calls engine commands through an MCP server, guided by a `dm-session` skill. Play happens by conversing in a Claude Code session.
- **Phase 2:** A standalone Python agent loop (Anthropic SDK, tool use) replaces Claude Code as the brain. Context is assembled from the DB every turn — no dependence on conversation-window memory.
- **Phase 3:** A UI on top of the Phase 2 loop.

Phases 2 and 3 are out of scope for this spec beyond the requirement that the command registry be the single integration surface (nothing in Phase 1 may depend on Claude Code specifics except the MCP adapter and the skill).

## 2. Repo layout & SRD ingestion

```
llm-dungeon-master/
├── pyproject.toml            # uv-managed Python project
├── src/dm_engine/
│   ├── models/               # Pydantic: Character, Monster, Spell, Encounter…
│   ├── rules/                # pure functions: dice, checks, combat math, leveling
│   ├── commands/             # command registry + handlers (the LLM's API)
│   ├── state/                # SQLite persistence, event log, campaign store
│   ├── content/              # SRD loaders, seed scripts, rules lookup/search
│   ├── mcp/                  # MCP server exposing commands (Phase 1)
│   └── cli/                  # typer CLI: dm new, dm resume, dm cmd …
├── data/srd/
│   ├── 2014/text/            # vendored markdown from the dnd-5e-srd fork (SRD 5.1)
│   ├── 2014/structured/      # vendored 5e-bits/5e-database JSON (SRD 5.1)
│   └── ATTRIBUTION.md        # CC-BY attribution for both sources
│                             # (a future data/srd/2024/ slots in beside it)
├── campaigns/                # per-campaign SQLite DBs + snapshots (gitignored)
├── scripts/sync_srd.py       # re-vendor both SRD sources from upstream
├── .claude/skills/dm-session/ # DM persona skill (Phase 1)
└── tests/
```

**SRD ingestion strategy.** The `../dnd-5e-srd` fork is *vendored, not moved*: `scripts/sync_srd.py` copies its `markdown/` directory into `data/srd/2014/text/` and fetches the 5e-bits JSON into `data/srd/2014/structured/`. Vendored files are committed so the repo is self-contained; upstream fixes are one script run away. Rationale: the fork's json/yaml are prose-in-a-heading-tree (stat blocks are markdown strings), so it serves as the DM's rules-reference library; 5e-bits provides the same SRD content as typed, queryable fields for the mechanics engine. Same CC-BY license for both.

## 3. Data layer

Two SQLite databases with different lifecycles.

**`rules.sqlite`** — static reference, rebuilt from `data/srd/<edition>/` by a seed script, shared across campaigns, never written during play. A `meta` table records the edition it was built from; campaigns record which edition they were created under.

- Typed tables: `monsters`, `spells`, `classes`, `class_features`, `races`, `equipment`, `magic_items`, `conditions`. Key mechanical fields as real columns (CR, AC, HP, spell level, school, cost…), full record as a JSON column.
- **FTS5 full-text index** over the fork's markdown sections, powering `lookup_rule("grappling")` so the DM quotes real rules text instead of hallucinating.

**`<campaign>.sqlite`** — one per campaign, written on every command.

- `campaign` — metadata plus the generated plot skeleton (premise, arcs, factions, secrets, endgame).
- `characters` — the PC and companions: full sheets (abilities, proficiencies, class features, known spells).
- `resources` — current HP, spell slots, hit dice, item charges, exhaustion; anything spendable.
- `inventory` — items, quantities, attunement, equipped state.
- `npcs` — persistent NPCs with disposition and knowledge memory.
- `locations`, `quests` / `plot_threads` — the persistent world graph the DM writes into as it improvises.
- `combat_state` — initiative order, round number, whose turn, remaining action economy.
- `session_recaps` — end-of-session narrative summaries (the "previously on…").
- `world_clock` — in-game date/time for rests, travel, durations.
- `event_log` — **append-only**: every command, its inputs, dice results with RNG seed, and state deltas. Tables are the fast current-state view; the event log is the authoritative audit trail.

## 4. Rules engine

Pure, deterministic functions in `rules/` — dice injected as a seeded, logged RNG so every resolution is replayable.

Coverage (v1): ability checks and saving throws with advantage/disadvantage; attack rolls and crits; damage with resistance/vulnerability/immunity; all 15 conditions and their mechanical effects; action economy (action, bonus action, reaction, movement per turn); initiative; short/long rests; death saves; concentration; XP and leveling for levels 1–20; encounter difficulty budgeting (CR/XP-threshold math) so the DM builds fair fights.

**Combat space — range bands.** Each combatant holds an abstract position: `engaged(with X)`, `near`, `far`, `distant` (thresholds ≈ 5/30/60/120 ft). The engine enforces weapon/spell range legality, movement between bands per speed, and opportunity attacks when leaving `engaged`. AoE spells hit N targets via clustering rules (targets in the same band, capped per spell record) — RAW-adjacent by design; exact templates arrive with the Phase 3 UI if ever.

**Dice ownership.** All of the player PC's dice (d20s, damage, hit dice, death saves) are player-rolled and reported to the DM; any single roll is delegable to the engine via `/roll`. Player-supplied values enter commands as an optional input and are flagged `player_supplied` in the event log. Companions and monsters are always engine-rolled. The engine performs **hidden rolls** where RAW implies a DM screen (enemy stealth, contested checks against the player); hidden results carry `gm_only` and the DM narrates around them.

**Spell automation — tiered.** Tier 1 (fully engine-executed from structured effect records): spells reducible to attack/save + damage/heal + condition/duration — the large majority of combat-relevant spells at levels 1–3. Tier 2 (everything else): the engine still enforces slot consumption, concentration, and duration; the *effect* resolves via `dm_ruling` with the SRD spell text retrieved. Effect records are added over time, shrinking tier 2.

The engine is data-driven from `rules.sqlite`: it does not hardcode "Fireball," it executes the spell record. Levels 1–5 receive hand-verified test coverage at launch; the schema and code paths carry 1–20 so later tiers are a verification effort, not a redesign.

**Progression.** Engine-awarded XP at RAW thresholds via `award_xp`; the engine announces level-ups. Non-combat resolutions of an encounter earn its full XP value. **Encounter budgeting is advisory:** the DM must compute and report the difficulty rating when building a fight, but may deliberately deviate when the fiction demands — the deviation is logged.

## 5. Command interface

A single command registry exposed three ways: Python API (Phase 2), **MCP server tools (Phase 1)**, and a typer CLI (debugging/manual play). Every command follows the same lifecycle:

1. **Validate** — is it this creature's turn? Is the spell slot available? Is the target in range? Illegal actions return *structured refusals* ("Kira has no 2nd-level slots remaining"), never exceptions that crash play.
2. **Resolve** — pure rules-engine math, dice rolled by the engine.
3. **Persist** — one SQLite transaction: state tables updated, event appended to `event_log`.
4. **Return** — structured result plus a one-line human-readable digest for narration.

Command families (~25 commands in v1):

- **Campaign:** `create_campaign`, `get_campaign_brief` (the resume-rehydration payload: plot skeleton, scene, party state, open threads, last recap).
- **Characters:** guided creation, leveling, sheet queries.
- **Scene & travel:** set/describe scene, travel with world-clock advancement.
- **Checks:** `skill_check`, `saving_throw` (engine computes DC outcomes).
- **Combat:** `start_combat`, `attack`, `cast_spell`, `next_turn`, `apply_condition`, `end_combat`.
- **Resources:** `use_item`, `rest` (short/long), inventory operations.
- **World writes:** `create_npc`, `create_location`, `update_quest` — how the DM's improvisations become persistent facts.
- **Queries:** `get_character_sheet`, `get_scene_state`, `lookup_rule`, `lookup_monster`, `lookup_spell`.
- **Sessions:** `end_session` (DM writes a persisted recap + open-thread updates); the DM also silently checkpoints a mini-recap every ~20 events so an abrupt exit (crash, compaction, closed laptop) still resumes with fresh narrative memory.
- **Escape hatch:** `dm_ruling` — for corner cases the engine doesn't model; full read/write power, requires a written rationale, prominently marked in `event_log`, reviewable via a `dm audit` CLI listing all rulings. RAW coverage gaps must never block play.

**Result visibility.** Every command result and payload carries a `gm_only` flag from day one. Phase 1 runs on the honor system (tool outputs are collapsed in Claude Code; the player doesn't read them); Phase 2's loop and Phase 3's UI enforce hiding for real. No schema migration later.

## 6. DM brain (Phase 1)

A `.claude/skills/dm-session` skill defines persona and procedure:

- **On session start:** call `get_campaign_brief`; never trust conversation memory over the DB.
- **Narration rule:** every mechanical claim (hit, damage, save, resource) must come from a command result.
- **Dice etiquette:** prompt the player to roll their own dice and pass the values through; accept `/roll` as delegation; never reveal `gm_only` results in narration.
- **Companions:** DM-generated to complement the player's build (preferences gathered in the campaign interview), recruited in-fiction as real NPCs with full sheets. They act **autonomously** on personality + tactical doctrine set at recruitment; the player can suggest in-fiction and they usually comply. They can permanently die; replacements emerge through play. Keep the spotlight on the player's PC.
- **World persistence rule:** improvised facts (a new NPC, a rumor, a location) must be written back via world-write commands, or they didn't happen.
- **Campaign creation:** an interview with the player (tone, themes, hard limits, character concept, companion preferences, death mode: `narrative` default / `hardcore` opt-in) → generate the plot skeleton (premise, 3-act arc outline, 3–5 factions with goals and secrets, endgame condition) **plus a fleshed-out starting region** (town, 5–10 NPCs, 3–5 hooks, first dungeon) → persist via `create_campaign`. Everything beyond the starting region is generated lazily when approached and persisted via world-write commands.
- **Character creation:** guided interview; ability scores by player's choice of rolled 4d6-drop-lowest (player rolls), standard array, or point buy. Companions use standard array.
- **PC defeat:** in `narrative` mode a failed final death save means *defeated, not dead* — the DM invents real consequences (capture, loss, rescue at cost). In `hardcore` mode death is final; the player makes a new character who joins the persistent world (or promotes a companion). Death-save mechanics run identically in both.

## 7. Error handling & integrity

- Every command is a single transaction — no half-applied attacks.
- Structured refusals steer the LLM instead of crashing the session.
- RNG seeded per campaign and logged per roll — replayable and auditable.
- `dm_ruling` requires a rationale string, appended to the event log.
- Campaign DB snapshot (file copy) at session start as a rollback safety net.
- Mid-session engine bugs degrade gracefully: `dm_ruling` can override anything, logged, so play continues and the bug is fixed later.

## 8. Testing

- **Unit (rules/):** golden cases from SRD examples — crit math, advantage/disadvantage stacking, resistance/vulnerability ordering, condition interactions, death-save sequences, spell-slot consumption, level-up deltas for levels 1–5 across SRD classes.
- **Integration (commands/):** command → temp DB → assert state mutation + event-log entry; one scripted end-to-end combat (goblin ambush: initiative, several rounds, a death save, healing, victory, XP award, short rest).
- **Content:** seed-script validation — every monster has AC/HP/CR, spell count matches the SRD, no unparseable records.
- **Not tested:** LLM narration quality. Phase 1 play is the E2E test.

## Out of scope (this spec)

- Phase 2 API loop and Phase 3 UI (constrained only by the command-registry boundary).
- Multiclassing, feats beyond SRD's Grappler, legendary/lair actions (schema supports; verification deferred with levels 6–20).
- Published-module ingestion, multiplayer, voice, images/maps.
