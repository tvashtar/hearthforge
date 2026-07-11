# SQLite storage schema

The engine persists to two kinds of SQLite database (FC-5):

| Database | Path | Written by | Lifetime |
|---|---|---|---|
| **Campaign store** | `campaigns/<slug>/campaign.sqlite` | every command, one transaction each | one per campaign, lives forever |
| **Rules DB** | `data/build/rules.sqlite` | `dm seed` only | static, rebuilt from vendored SRD sources, gitignored |

Alongside each campaign store: `sheets/` (rendered markdown character
sheets, re-materialized after every successful command) and `snapshots/`
(full copies of `campaign.sqlite` taken automatically every time the
campaign is opened, named `<ISO-timestamp>.sqlite`).

Schema sources of truth: `src/dm_engine/state/store.py` (campaign) and
`src/dm_engine/content/seed.py` (rules). This document describes; those
define.

---

## Campaign store (`campaign.sqlite`)

Conventions:

- **JSON columns** — SQLite has no JSON type; columns noted as *JSON* hold
  serialized JSON text. The store (`state/store.py`) encodes/decodes them in
  its typed accessors; nothing else touches the connection during play.
- **Singleton tables** — `campaign`, `world_clock`, and `combat_state` hold
  exactly one row, enforced by `CHECK (id = 1)`.
- **One transaction per command** (FC-6) — `registry.execute` wraps the
  handler, the event-log append, the RNG position update, and sheet
  re-rendering in a single transaction. Refusals (`ok=false`) commit their
  event row; handler exceptions roll everything back.

### `campaign` — identity and RNG (singleton)

| Column | Notes |
|---|---|
| `slug`, `name` | campaign identity; slug matches the directory name |
| `edition` | ruleset the campaign was created under (`2014`) |
| `death_mode` | `narrative` or `hardcore` (FC-7) |
| `rng_seed` | seed of the campaign's one RNG (FC-2) |
| `rng_draws` | count of engine dice drawn so far (player-supplied rolls don't count) |
| `rng_state` | *JSON* — exact `random.Random` state, saved after every command so reopening resumes the RNG mid-stream rather than fast-forwarding |
| `skeleton` | *JSON* — the campaign plot skeleton: `premise`, `acts`, `factions` (each with a `secret`), `endgame` |

### `characters` — party members (PC and companions)

One row per character, `role IN ('pc','companion')`,
`status IN ('active','defeated','dead','departed')`. NPCs are *not*
characters — they live in `npcs` and have no mechanics.

*JSON* columns: `abilities` (`{"str": 8, ..., "cha": 10}`),
`proficiencies` (`{"saves": [...], "skills": [...], "expertise": [...],
"tools": [...], "languages": [...]}` — saves are class-derived, never
supplied), `attacks` (list of resolved attack records: `name`, `source`
e.g. `srd:shortsword`, `ability`, `proficient`, `damage`, `damage_type`,
`ranged`, `range_ft`, `long_range_ft`, `properties`), `spells_known`
(list of spell slugs).

### `resources` — expendable state, 1:1 with `characters`

Keyed by `character_id`. Current `hp`, `temp_hp`, `hit_dice_remaining`,
`exhaustion`, and *JSON*: `spell_slots`
(`{"1": {"max": 2, "remaining": 1}}`), `conditions` (list of condition
slugs), `death_saves`
(`{"successes", "failures", "stable", "dead"}`), `concentration`
(`{"spell", "day", "minutes", "duration"}` or null).

The `characters` row is who you are; the `resources` row is how you're
doing.

### `inventory`

One row per item stack per character: `name`, `quantity`, `equipped`,
`attuned`, free-text `notes`. Items are freeform — the engine doesn't
resolve them against the rules DB.

### `npcs` / `locations` / `quests` — the DM's world memory

Narrative state persisted by the world-write commands (`create_npc`,
`create_location`, `update_quest`). `npcs.notes` is a *JSON* object with
DM-chosen keys (including `gm_secret`-style entries — nothing here is
player-visible by construction; the dm-session skill handles secrecy).
`npcs.name` is UNIQUE and `create_npc` upserts on it. `locations.slug` and
`quests.slug` are primary keys; `quests.status` is one of
`open/active/completed/failed/abandoned`.

### `world_clock` — in-game time and scene (singleton)

`day` (starts at 1), `minutes` since midnight (starts at 480 = 8:00),
current `location_slug`, and the free-text `scene` description set by
`set_scene`. Real-world time lives on event rows, not here.

### `combat_state` — the active encounter (singleton)

`active` flag, `round`, `turn_index`, `encounter_xp`, and `combatants`
(*JSON* list, initiative order). Each combatant entry carries `key`
(`"Algarve"`, `"goblin-2"`), `kind` (`character`/`monster`),
`character_id` or `monster_slug`, `initiative`, `band` (FC-4:
`engaged/near/far/distant`), `engaged_with` (list of keys), `hp`/`ac`
(monsters carry their own; characters mirror `resources`), `budget`
(remaining movement/action/bonus/reaction for the current turn),
`reaction_used`, and `defeated`. The exact shape is owned by
`commands/combat.py`; treat it as opaque outside the command layer.

### `session_recaps`

Append-only narrative memory: `kind IN ('session_end','checkpoint')` and
`content`. `end_session` writes the former; the ~20-event crash-insurance
`checkpoint` command writes the latter. `get_campaign_brief` surfaces the
newest recap on reopen.

### `event_log` — the audit trail (FC-6, append-only)

One row per command execution, written in the same transaction as the
state it changed:

| Column | Notes |
|---|---|
| `created_at` | real-world UTC time (in-game time is `world_clock`'s job) |
| `command` | registry name (`attack`, `roll_dice`, …) |
| `inputs` | *JSON* — kwargs exactly as received |
| `result` | *JSON* — the full FC-1 `CommandResult` envelope (including `ok: false` refusals) |
| `rolls` | *JSON* — every `Roll` the command drew: notation, individual dice, modifier, total, `player_supplied`, `gm_only` |
| `is_ruling` | 1 for successful `dm_ruling` commands |
| `rationale` | required non-empty when `is_ruling = 1` |

`dm audit` lists only ruling rows; everything else is queryable with
plain SQL. Replay guarantee: `rng_seed` + the ordered `rolls` column
reproduce every engine-rolled total; `player_supplied` rolls are
reproduced from their recorded values.

---

## Rules DB (`data/build/rules.sqlite`)

Static SRD 5.1 (2014) content, seeded by `dm seed` from
`data/srd/2014/`, never written during play. Common shape: a few indexed
scalar columns for lookup plus a `data` column holding the full source
JSON record.

| Table | Key columns besides `slug`/`name` |
|---|---|
| `monsters` | size, type, alignment, AC, HP, hit dice, CR, XP, six ability scores |
| `spells` | level, school, `concentration`, `ritual`, casting time, range, duration |
| `classes` | `hit_die` |
| `races` | `speed` |
| `equipment` | `category` |
| `magic_items` | `rarity` |
| `conditions` | — |
| `features` | `class_slug`, `level`, `description` (full text; subclass features carry a `subclass` key inside `data`) |
| `class_levels` | PK (`class_slug`, `level`): `prof_bonus`, spellcasting table, feature list |
| `srd_text` | FTS5 full-text index over the SRD prose (`source`, `heading_path`, `heading`, `body`) — backs `lookup_rule` |
| `meta` | at least `edition = 2014`, `srd_version = 5.1` |

---

## Inspecting a campaign

```bash
# rulings with rationales
uv run dm audit --campaign the-fading-of-liraeth

# everything else: plain SQL
sqlite3 campaigns/the-fading-of-liraeth/campaign.sqlite \
  "SELECT id, command, json_extract(result,'$.digest') FROM event_log ORDER BY id"

# all dice a command rolled
sqlite3 campaigns/the-fading-of-liraeth/campaign.sqlite \
  "SELECT rolls FROM event_log WHERE command = 'roll_dice'"
```

Snapshots in `campaigns/<slug>/snapshots/` are ordinary SQLite files —
point-in-time copies of everything above, one per session open.
