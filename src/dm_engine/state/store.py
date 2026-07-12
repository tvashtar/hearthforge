"""Campaign persistence: one sqlite file per campaign (FC-5, FC-6).

The store is dumb and safe: typed accessors over the schema, one
`transaction()` context manager, snapshot-on-open. All game logic lives in
the command handlers; nothing here rolls dice or interprets rules.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# IF NOT EXISTS so it can double as the in-place migration for campaigns
# created before TVA-20: every CampaignStore constructor runs it.
ACTIVE_EFFECTS_TABLE = """
CREATE TABLE IF NOT EXISTS active_effects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL REFERENCES characters(id),
    name TEXT NOT NULL,
    source_event_id INTEGER,
    mechanics TEXT NOT NULL DEFAULT '{}',
    expires_day INTEGER,
    expires_minutes INTEGER,
    expires_on_rest TEXT CHECK (expires_on_rest IN ('short','long')),
    concentration INTEGER NOT NULL DEFAULT 0,
    caster_id INTEGER REFERENCES characters(id)
);
"""

SCHEMA = """
CREATE TABLE campaign (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    edition TEXT NOT NULL,
    death_mode TEXT NOT NULL CHECK (death_mode IN ('narrative','hardcore')),
    rng_seed INTEGER NOT NULL,
    rng_draws INTEGER NOT NULL DEFAULT 0,
    rng_state TEXT,
    skeleton TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL CHECK (role IN ('pc','companion')),
    class_slug TEXT NOT NULL,
    race_slug TEXT NOT NULL,
    level INTEGER NOT NULL,
    xp INTEGER NOT NULL DEFAULT 0,
    abilities TEXT NOT NULL,
    max_hp INTEGER NOT NULL,
    ac INTEGER NOT NULL,
    speed INTEGER NOT NULL,
    proficiencies TEXT NOT NULL,
    attacks TEXT NOT NULL,
    spells_known TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active','defeated','dead','departed'))
);
CREATE TABLE resources (
    character_id INTEGER PRIMARY KEY REFERENCES characters(id),
    hp INTEGER NOT NULL,
    temp_hp INTEGER NOT NULL DEFAULT 0,
    hit_dice_remaining INTEGER NOT NULL,
    spell_slots TEXT NOT NULL DEFAULT '{}',
    conditions TEXT NOT NULL DEFAULT '[]',
    exhaustion INTEGER NOT NULL DEFAULT 0,
    death_saves TEXT NOT NULL
        DEFAULT '{"successes": 0, "failures": 0, "stable": false, "dead": false}',
    concentration TEXT
);
CREATE TABLE inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id INTEGER NOT NULL REFERENCES characters(id),
    name TEXT NOT NULL,
    quantity INTEGER NOT NULL DEFAULT 1,
    equipped INTEGER NOT NULL DEFAULT 0,
    attuned INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);
CREATE TABLE npcs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    disposition TEXT NOT NULL DEFAULT 'neutral',
    location_slug TEXT,
    notes TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE locations (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    region TEXT,
    discovered INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE quests (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','active','completed','failed','abandoned')),
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE combat_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    active INTEGER NOT NULL DEFAULT 0,
    round INTEGER NOT NULL DEFAULT 0,
    turn_index INTEGER NOT NULL DEFAULT 0,
    combatants TEXT NOT NULL DEFAULT '[]',
    encounter_xp INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE session_recaps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    kind TEXT NOT NULL CHECK (kind IN ('session_end','checkpoint')),
    content TEXT NOT NULL
);
CREATE TABLE world_clock (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    day INTEGER NOT NULL DEFAULT 1,
    minutes INTEGER NOT NULL DEFAULT 480,
    location_slug TEXT,
    scene TEXT
);
CREATE TABLE event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    command TEXT NOT NULL,
    inputs TEXT NOT NULL,
    result TEXT NOT NULL,
    rolls TEXT NOT NULL,
    is_ruling INTEGER NOT NULL DEFAULT 0,
    rationale TEXT
);
""" + ACTIVE_EFFECTS_TABLE

_JSON_CHARACTER_FIELDS = {"abilities", "proficiencies", "attacks", "spells_known"}
_JSON_RESOURCE_FIELDS = {"spell_slots", "conditions", "death_saves", "concentration"}


class CampaignStore:
    def __init__(self, conn: sqlite3.Connection, root: Path):
        self.conn = conn
        self.root = root  # campaigns/<slug>/
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        # In-place migration for campaigns created before TVA-20 (no-op
        # otherwise); executescript commits, so it runs before any command
        # transaction is open.
        conn.executescript(ACTIVE_EFFECTS_TABLE)

    # -- lifecycle -----------------------------------------------------

    @classmethod
    def create(
        cls,
        campaigns_dir: Path,
        *,
        slug: str,
        name: str,
        death_mode: str,
        rng_seed: int,
        skeleton: dict,
        edition: str = "2014",
    ) -> "CampaignStore":
        root = Path(campaigns_dir) / slug
        db_path = root / "campaign.sqlite"
        if db_path.exists():
            raise FileExistsError(f"campaign {slug!r} already exists at {db_path}")
        root.mkdir(parents=True, exist_ok=True)
        (root / "sheets").mkdir(exist_ok=True)
        (root / "snapshots").mkdir(exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT INTO campaign (id, slug, name, edition, death_mode, rng_seed, skeleton)"
            " VALUES (1, ?, ?, ?, ?, ?, ?)",
            (slug, name, edition, death_mode, rng_seed, json.dumps(skeleton)),
        )
        conn.execute("INSERT INTO world_clock (id) VALUES (1)")
        conn.execute("INSERT INTO combat_state (id) VALUES (1)")
        conn.commit()
        return cls(conn, root)

    @classmethod
    def open(cls, campaigns_dir: Path, slug: str) -> "CampaignStore":
        """Open an existing campaign; copies a session-start snapshot (FC-5)."""
        root = Path(campaigns_dir) / slug
        db_path = root / "campaign.sqlite"
        if not db_path.exists():
            raise FileNotFoundError(f"no campaign at {db_path}")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        (root / "snapshots").mkdir(exist_ok=True)
        shutil.copy2(db_path, root / "snapshots" / f"{stamp}.sqlite")
        return cls(sqlite3.connect(db_path), root)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self):
        """One transaction per command (FC-6). Commits on success, rolls
        back and re-raises on any exception."""
        try:
            yield
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    # -- campaign / clock ----------------------------------------------

    def campaign_meta(self) -> dict:
        row = self.conn.execute("SELECT * FROM campaign WHERE id = 1").fetchone()
        meta = dict(row)
        meta["skeleton"] = json.loads(meta["skeleton"])
        return meta

    def set_rng_draws(self, draws: int) -> None:
        self.conn.execute("UPDATE campaign SET rng_draws = ? WHERE id = 1", (draws,))

    def set_rng_state(self, state_json: str) -> None:
        self.conn.execute("UPDATE campaign SET rng_state = ? WHERE id = 1", (state_json,))

    def world_clock(self) -> dict:
        return dict(self.conn.execute("SELECT * FROM world_clock WHERE id = 1").fetchone())

    def update_world_clock(self, **fields: Any) -> None:
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.conn.execute(f"UPDATE world_clock SET {cols} WHERE id = 1", tuple(fields.values()))

    # -- event log (FC-6) ------------------------------------------------

    def append_event(
        self,
        command: str,
        inputs: dict,
        result: dict,
        rolls: list[dict],
        *,
        is_ruling: bool = False,
        rationale: str | None = None,
    ) -> int:
        if is_ruling and not (rationale or "").strip():
            raise ValueError("dm_ruling events require a non-empty rationale")
        cur = self.conn.execute(
            "INSERT INTO event_log (command, inputs, result, rolls, is_ruling, rationale)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (command, json.dumps(inputs), json.dumps(result), json.dumps(rolls),
             int(is_ruling), rationale),
        )
        return int(cur.lastrowid)

    def event_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0]

    def next_event_id(self) -> int:
        """The id the next `append_event` will be assigned. Exact because the
        log is append-only AUTOINCREMENT (never deleted, ids never reused);
        handlers use it to stamp rows with their own not-yet-written event."""
        row = self.conn.execute(
            "SELECT seq FROM sqlite_sequence WHERE name = 'event_log'"
        ).fetchone()
        return (int(row[0]) if row else 0) + 1

    def rulings(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, created_at, command, rationale, result FROM event_log"
            " WHERE is_ruling = 1 ORDER BY id"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- characters & resources ------------------------------------------

    def insert_character(
        self,
        *,
        name: str,
        role: str,
        class_slug: str,
        race_slug: str,
        level: int,
        abilities: dict,
        max_hp: int,
        ac: int,
        speed: int,
        proficiencies: dict,
        attacks: list[dict],
        spells_known: list[str],
        spell_slots: dict,
        xp: int = 0,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO characters (name, role, class_slug, race_slug, level, xp,"
            " abilities, max_hp, ac, speed, proficiencies, attacks, spells_known)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, role, class_slug, race_slug, level, xp, json.dumps(abilities),
             max_hp, ac, speed, json.dumps(proficiencies), json.dumps(attacks),
             json.dumps(spells_known)),
        )
        cid = int(cur.lastrowid)
        slots = {str(k): {"max": v, "remaining": v} for k, v in spell_slots.items()}
        self.conn.execute(
            "INSERT INTO resources (character_id, hp, hit_dice_remaining, spell_slots)"
            " VALUES (?, ?, ?, ?)",
            (cid, max_hp, level, json.dumps(slots)),
        )
        return cid

    def get_character(self, name: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM characters WHERE name = ?", (name,)
        ).fetchone()
        return self._parse_character(row) if row else None

    def get_character_by_id(self, cid: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM characters WHERE id = ?", (cid,)).fetchone()
        return self._parse_character(row) if row else None

    def _parse_character(self, row: sqlite3.Row) -> dict:
        char = dict(row)
        for field in _JSON_CHARACTER_FIELDS:
            char[field] = json.loads(char[field])
        return char

    def party(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM characters WHERE status IN ('active','defeated') ORDER BY id"
        ).fetchall()
        return [self._parse_character(r) for r in rows]

    def update_character(self, cid: int, **fields: Any) -> None:
        payload = {
            k: json.dumps(v) if k in _JSON_CHARACTER_FIELDS else v
            for k, v in fields.items()
        }
        cols = ", ".join(f"{k} = ?" for k in payload)
        self.conn.execute(
            f"UPDATE characters SET {cols} WHERE id = ?", (*payload.values(), cid)
        )

    def get_resources(self, cid: int) -> dict:
        row = self.conn.execute(
            "SELECT * FROM resources WHERE character_id = ?", (cid,)
        ).fetchone()
        res = dict(row)
        for field in _JSON_RESOURCE_FIELDS:
            if res[field] is not None:
                res[field] = json.loads(res[field])
        return res

    def update_resources(self, cid: int, **fields: Any) -> None:
        payload = {
            k: (json.dumps(v) if k in _JSON_RESOURCE_FIELDS and v is not None else v)
            for k, v in fields.items()
        }
        cols = ", ".join(f"{k} = ?" for k in payload)
        self.conn.execute(
            f"UPDATE resources SET {cols} WHERE character_id = ?",
            (*payload.values(), cid),
        )

    # -- active effects ----------------------------------------------------

    def add_effect(
        self,
        character_id: int,
        *,
        name: str,
        mechanics: dict,
        source_event_id: int | None = None,
        expires_day: int | None = None,
        expires_minutes: int | None = None,
        expires_on_rest: str | None = None,
        concentration: bool = False,
        caster_id: int | None = None,
    ) -> int:
        cur = self.conn.execute(
            "INSERT INTO active_effects (character_id, name, source_event_id,"
            " mechanics, expires_day, expires_minutes, expires_on_rest,"
            " concentration, caster_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (character_id, name, source_event_id, json.dumps(mechanics),
             expires_day, expires_minutes, expires_on_rest,
             int(concentration), caster_id),
        )
        return int(cur.lastrowid)

    def _parse_effect(self, row: sqlite3.Row) -> dict:
        effect = dict(row)
        effect["mechanics"] = json.loads(effect["mechanics"])
        effect["concentration"] = bool(effect["concentration"])
        return effect

    def active_effects_for(self, character_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM active_effects WHERE character_id = ? ORDER BY id",
            (character_id,),
        ).fetchall()
        return [self._parse_effect(r) for r in rows]

    def all_active_effects(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM active_effects ORDER BY id"
        ).fetchall()
        return [self._parse_effect(r) for r in rows]

    def delete_effect(self, effect_id: int) -> None:
        self.conn.execute("DELETE FROM active_effects WHERE id = ?", (effect_id,))

    # -- inventory --------------------------------------------------------

    def add_item(self, cid: int, name: str, quantity: int, notes: str | None = None) -> int:
        row = self.conn.execute(
            "SELECT id, quantity FROM inventory WHERE character_id = ? AND name = ?",
            (cid, name),
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE inventory SET quantity = quantity + ? WHERE id = ?",
                (quantity, row["id"]),
            )
            return int(row["id"])
        cur = self.conn.execute(
            "INSERT INTO inventory (character_id, name, quantity, notes) VALUES (?, ?, ?, ?)",
            (cid, name, quantity, notes),
        )
        return int(cur.lastrowid)

    def remove_item(self, cid: int, name: str, quantity: int) -> bool:
        """Decrement (deleting at zero). Returns False if not enough held."""
        row = self.conn.execute(
            "SELECT id, quantity FROM inventory WHERE character_id = ? AND name = ?",
            (cid, name),
        ).fetchone()
        if row is None or row["quantity"] < quantity:
            return False
        if row["quantity"] == quantity:
            self.conn.execute("DELETE FROM inventory WHERE id = ?", (row["id"],))
        else:
            self.conn.execute(
                "UPDATE inventory SET quantity = quantity - ? WHERE id = ?",
                (quantity, row["id"]),
            )
        return True

    def items_for(self, cid: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM inventory WHERE character_id = ? ORDER BY name", (cid,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- world -----------------------------------------------------------

    def upsert_npc(self, name: str, disposition: str, location_slug: str | None,
                   notes: dict) -> int:
        self.conn.execute(
            "INSERT INTO npcs (name, disposition, location_slug, notes) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET disposition = excluded.disposition,"
            " location_slug = excluded.location_slug, notes = excluded.notes",
            (name, disposition, location_slug, json.dumps(notes)),
        )
        row = self.conn.execute("SELECT id FROM npcs WHERE name = ?", (name,)).fetchone()
        return int(row["id"])

    def get_npc(self, name: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM npcs WHERE name = ?", (name,)).fetchone()
        if row is None:
            return None
        npc = dict(row)
        npc["notes"] = json.loads(npc["notes"])
        return npc

    def npcs(self, location_slug: str | None = None) -> list[dict]:
        if location_slug is None:
            rows = self.conn.execute("SELECT * FROM npcs ORDER BY name").fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM npcs WHERE location_slug = ? ORDER BY name",
                (location_slug,),
            ).fetchall()
        out = []
        for r in rows:
            npc = dict(r)
            npc["notes"] = json.loads(npc["notes"])
            out.append(npc)
        return out

    def upsert_location(self, slug: str, name: str, description: str,
                        region: str | None) -> None:
        self.conn.execute(
            "INSERT INTO locations (slug, name, description, region) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(slug) DO UPDATE SET name = excluded.name,"
            " description = excluded.description, region = excluded.region",
            (slug, name, description, region),
        )

    def get_location(self, slug: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM locations WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def locations(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM locations ORDER BY slug").fetchall()
        return [dict(r) for r in rows]

    def upsert_quest(self, slug: str, title: str, status: str, notes: str) -> None:
        self.conn.execute(
            "INSERT INTO quests (slug, title, status, notes) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(slug) DO UPDATE SET title = excluded.title,"
            " status = excluded.status, notes = excluded.notes",
            (slug, title, status, notes),
        )

    def get_quest(self, slug: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM quests WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def quests(self, statuses: tuple[str, ...] = ("open", "active")) -> list[dict]:
        marks = ",".join("?" * len(statuses))
        rows = self.conn.execute(
            f"SELECT * FROM quests WHERE status IN ({marks}) ORDER BY slug", statuses
        ).fetchall()
        return [dict(r) for r in rows]

    # -- combat ------------------------------------------------------------

    def combat(self) -> dict:
        row = dict(self.conn.execute("SELECT * FROM combat_state WHERE id = 1").fetchone())
        row["combatants"] = json.loads(row["combatants"])
        return row

    def update_combat(self, **fields: Any) -> None:
        payload = {
            k: (json.dumps(v) if k == "combatants" else v) for k, v in fields.items()
        }
        cols = ", ".join(f"{k} = ?" for k in payload)
        self.conn.execute(f"UPDATE combat_state SET {cols} WHERE id = 1",
                          tuple(payload.values()))

    # -- recaps --------------------------------------------------------------

    def add_recap(self, kind: str, content: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO session_recaps (kind, content) VALUES (?, ?)", (kind, content)
        )
        return int(cur.lastrowid)

    def latest_recap(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM session_recaps ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def recaps(self) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM session_recaps ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def events_tail(self, limit: int) -> list[dict]:
        """Newest-first compact projection of the event log; `ok`/`digest`
        come out of the stored result envelope, not the full row."""
        rows = self.conn.execute(
            "SELECT id, command, result, created_at FROM event_log"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            result = json.loads(r["result"])
            out.append({
                "id": r["id"],
                "command": r["command"],
                "ok": result.get("ok"),
                "digest": result.get("digest"),
                "created_at": r["created_at"],
            })
        return out
