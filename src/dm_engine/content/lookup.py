"""Read API over rules.sqlite — the only way the engine reads reference data."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from dm_engine.models.srd import MonsterRecord, SpellRecord

DEFAULT_DB = Path("data/build/rules.sqlite")


@dataclass
class RuleHit:
    source: str
    heading_path: str
    heading: str
    snippet: str


@dataclass
class MonsterSummary:
    slug: str
    name: str
    challenge_rating: float
    xp: int


@dataclass
class SpellSummary:
    slug: str
    name: str
    level: int
    school: str


@dataclass
class FeatureSummary:
    slug: str
    name: str
    level: int
    description: str


class RulesDB:
    def __init__(self, path: Path = DEFAULT_DB):
        if not Path(path).exists():
            raise FileNotFoundError(f"{path} not found — run `dm seed` first")
        self._conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    def __enter__(self) -> "RulesDB":
        return self

    def __exit__(self, *exc) -> None:
        self._conn.close()

    def lookup_rule(self, query: str, limit: int = 5) -> list[RuleHit]:
        # Quote every term: user text must never hit FTS5 query syntax.
        terms = [t for t in query.replace('"', " ").split() if t]
        if not terms:
            return []
        fts_query = " ".join(f'"{t}"' for t in terms)
        rows = self._conn.execute(
            "SELECT source, heading_path, heading,"
            " snippet(srd_text, 3, '[', ']', ' … ', 24)"
            " FROM srd_text WHERE srd_text MATCH ? ORDER BY rank LIMIT ?",
            (fts_query, limit),
        ).fetchall()
        return [RuleHit(*row) for row in rows]

    def get_monster(self, slug: str) -> MonsterRecord | None:
        row = self._conn.execute(
            "SELECT data FROM monsters WHERE slug=?", (slug,)
        ).fetchone()
        return MonsterRecord.model_validate(json.loads(row[0])) if row else None

    def search_monsters(
        self,
        max_cr: float | None = None,
        type: str | None = None,
        limit: int = 20,
    ) -> list[MonsterSummary]:
        clauses, params = [], []
        if max_cr is not None:
            clauses.append("challenge_rating <= ?")
            params.append(max_cr)
        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT slug, name, challenge_rating, xp FROM monsters {where}"
            " ORDER BY challenge_rating, name LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [MonsterSummary(*row) for row in rows]

    def get_spell(self, slug: str) -> SpellRecord | None:
        row = self._conn.execute(
            "SELECT data FROM spells WHERE slug=?", (slug,)
        ).fetchone()
        return SpellRecord.model_validate(json.loads(row[0])) if row else None

    def search_spells(self, level: int | None = None, limit: int = 20) -> list[SpellSummary]:
        clauses, params = [], []
        if level is not None:
            clauses.append("level = ?")
            params.append(level)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT slug, name, level, school FROM spells {where}"
            " ORDER BY level, name LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [SpellSummary(*row) for row in rows]

    def get_feature(self, slug: str) -> dict | None:
        row = self._conn.execute(
            "SELECT data FROM features WHERE slug=?", (slug,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def class_features(self, class_slug: str, level: int) -> list[FeatureSummary]:
        """Base class features gained at or below `level`, in level order.

        Subclass features and choice sub-options (records with a `parent`,
        e.g. the individual Fighting Style picks) are excluded — those are
        character-specific selections, not class-wide grants.
        """
        rows = self._conn.execute(
            "SELECT slug, name, level, description FROM features"
            " WHERE class_slug=? AND level<=?"
            " AND json_extract(data, '$.subclass') IS NULL"
            " AND json_extract(data, '$.parent') IS NULL"
            " ORDER BY level, name",
            (class_slug, level),
        ).fetchall()
        return [FeatureSummary(*row) for row in rows]

    def get_class(self, class_slug: str) -> dict | None:
        """Return a class record incl. its `hit_die` (from the dedicated
        column) merged into the parsed `data` payload, or None if unknown."""
        row = self._conn.execute(
            "SELECT hit_die, data FROM classes WHERE slug=?", (class_slug,)
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row[1])
        data["hit_die"] = row[0]
        return data

    def get_class_level(self, class_slug: str, level: int) -> dict | None:
        row = self._conn.execute(
            "SELECT data FROM class_levels WHERE class_slug=? AND level=?",
            (class_slug, level),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def get_equipment(self, slug: str) -> dict | None:
        row = self._conn.execute(
            "SELECT data FROM equipment WHERE slug=?", (slug,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def spell_slots_for(self, class_slug: str, level: int) -> dict[int, int]:
        """Return {slot_level: count} for slot levels with count > 0.

        Reads the `spellcasting` column only. Warlock pact-magic slots are
        stored under `class_specific` in the upstream data, not
        `spellcasting`, so this returns `{}` for warlock at most levels.
        Fine for v1; a future task can add class_specific-aware handling.
        """
        row = self._conn.execute(
            "SELECT spellcasting FROM class_levels WHERE class_slug=? AND level=?",
            (class_slug, level),
        ).fetchone()
        if row is None or row[0] is None:
            return {}
        spellcasting = json.loads(row[0])
        return {
            int(k.rsplit("_", 1)[1]): v
            for k, v in spellcasting.items()
            if k.startswith("spell_slots_level_") and v > 0
        }
