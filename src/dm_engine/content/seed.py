"""Build rules.sqlite from the vendored SRD sources.

Static reference data: rebuilt by `dm seed`, never written during play.
Each table keeps queryable columns plus the full upstream record as JSON
in `data`. Slugs are the 5e-bits `index` values.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dm_engine.content.lookup import DEFAULT_DB
from dm_engine.content.markdown_sections import parse_sections
from dm_engine.models.srd import MonsterRecord, SpellRecord

SCHEMA = """
CREATE TABLE monsters (
    slug TEXT PRIMARY KEY, name TEXT NOT NULL, size TEXT, type TEXT, alignment TEXT,
    armor_class INTEGER, hit_points INTEGER, hit_dice TEXT,
    challenge_rating REAL, xp INTEGER,
    str INTEGER, dex INTEGER, con INTEGER, "int" INTEGER, wis INTEGER, cha INTEGER,
    data TEXT NOT NULL
);
CREATE TABLE spells (
    slug TEXT PRIMARY KEY, name TEXT NOT NULL, level INTEGER NOT NULL, school TEXT,
    concentration INTEGER NOT NULL, ritual INTEGER NOT NULL,
    casting_time TEXT, range TEXT, duration TEXT,
    data TEXT NOT NULL
);
CREATE TABLE classes (slug TEXT PRIMARY KEY, name TEXT NOT NULL, hit_die INTEGER, data TEXT NOT NULL);
CREATE TABLE races (slug TEXT PRIMARY KEY, name TEXT NOT NULL, speed INTEGER, data TEXT NOT NULL);
CREATE TABLE equipment (slug TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT, data TEXT NOT NULL);
CREATE TABLE magic_items (slug TEXT PRIMARY KEY, name TEXT NOT NULL, rarity TEXT, data TEXT NOT NULL);
CREATE TABLE conditions (slug TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE features (
    slug TEXT PRIMARY KEY, name TEXT NOT NULL, class_slug TEXT, level INTEGER,
    description TEXT, data TEXT NOT NULL
);
CREATE TABLE class_levels (
    class_slug TEXT, level INTEGER, prof_bonus INTEGER,
    spellcasting TEXT, features TEXT, data TEXT,
    PRIMARY KEY (class_slug, level)
);
CREATE VIRTUAL TABLE srd_text USING fts5(source, heading_path, heading, body);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

EDITION_META = [("edition", "2014"), ("srd_version", "5.1")]


def _records(structured_dir: Path, filename: str) -> list[dict]:
    return json.loads((structured_dir / filename).read_text())


def ensure_rules_db(dest: Path = DEFAULT_DB) -> Path:
    """Build the rules DB from the vendored SRD sources iff `dest` is missing.

    A present file is trusted and left untouched — `dm seed` is the explicit
    rebuild path.
    """
    dest = Path(dest)
    if not dest.exists():
        repo_root = Path(__file__).resolve().parents[3]
        srd = repo_root / "data" / "srd" / "2014"
        build_rules_db(
            structured_dir=srd / "structured", text_dir=srd / "text", dest=dest
        )
    return dest


def build_rules_db(structured_dir: Path, text_dir: Path, dest: Path) -> dict[str, int]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    conn = sqlite3.connect(dest)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO meta VALUES (?,?)", EDITION_META)

    for raw in _records(structured_dir, "5e-SRD-Monsters.json"):
        m = MonsterRecord.model_validate(raw)
        conn.execute(
            "INSERT INTO monsters VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                m.slug, m.name, m.size, m.type, m.alignment,
                m.ac, m.hit_points, m.hit_dice, m.challenge_rating, m.xp,
                m.strength, m.dexterity, m.constitution,
                m.intelligence, m.wisdom, m.charisma,
                json.dumps(raw),
            ),
        )

    for raw in _records(structured_dir, "5e-SRD-Spells.json"):
        s = SpellRecord.model_validate(raw)
        conn.execute(
            "INSERT INTO spells VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                s.slug, s.name, s.level, s.school_name,
                int(s.concentration), int(s.ritual),
                s.casting_time, s.range, s.duration,
                json.dumps(raw),
            ),
        )

    simple_tables = [
        ("5e-SRD-Classes.json", "classes", lambda r: (r.get("hit_die"),)),
        ("5e-SRD-Races.json", "races", lambda r: (r.get("speed"),)),
        (
            "5e-SRD-Equipment.json",
            "equipment",
            lambda r: (r.get("equipment_category", {}).get("name"),),
        ),
        ("5e-SRD-Magic-Items.json", "magic_items", lambda r: (r.get("rarity", {}).get("name"),)),
        ("5e-SRD-Conditions.json", "conditions", lambda r: ()),
        (
            "5e-SRD-Features.json",
            "features",
            lambda r: (
                r.get("class", {}).get("index"),
                r.get("level"),
                "\n\n".join(r.get("desc", [])),
            ),
        ),
    ]
    for filename, table, extra_cols in simple_tables:
        for raw in _records(structured_dir, filename):
            cols = (raw["index"], raw["name"], *extra_cols(raw), json.dumps(raw))
            placeholders = ",".join("?" * len(cols))
            conn.execute(f"INSERT INTO {table} VALUES ({placeholders})", cols)

    for raw in _records(structured_dir, "5e-SRD-Levels.json"):
        if "subclass" in raw:
            continue
        conn.execute(
            "INSERT INTO class_levels VALUES (?,?,?,?,?,?)",
            (
                raw["class"]["index"],
                raw["level"],
                raw["prof_bonus"],
                json.dumps(raw["spellcasting"]) if "spellcasting" in raw else None,
                json.dumps(raw.get("features", [])),
                json.dumps(raw),
            ),
        )

    for md_file in sorted(text_dir.glob("*.md")):
        for sec in parse_sections(md_file.read_text(), source=md_file.name):
            conn.execute(
                "INSERT INTO srd_text VALUES (?,?,?,?)",
                (sec.source, sec.heading_path, sec.heading, sec.body),
            )

    conn.commit()
    counts = {}
    for table in [
        "monsters", "spells", "classes", "races",
        "equipment", "magic_items", "conditions", "features",
        "class_levels", "srd_text",
    ]:
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return counts
