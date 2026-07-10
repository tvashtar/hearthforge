import json
import sqlite3
from contextlib import closing

from dm_engine.content.seed import ensure_rules_db


def test_ensure_rules_db_builds_when_missing_and_noops_when_present(tmp_path):
    dest = tmp_path / "build" / "rules.sqlite"
    assert not dest.exists()

    built = ensure_rules_db(dest)
    assert built == dest and dest.exists()
    with closing(sqlite3.connect(dest)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM monsters").fetchone()[0] > 300

    stamp = dest.stat().st_mtime_ns
    again = ensure_rules_db(dest)
    assert again == dest
    assert dest.stat().st_mtime_ns == stamp  # present → untouched, no rebuild


def test_seed_row_counts(rules_db):
    with closing(sqlite3.connect(rules_db)) as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in [
                "monsters", "spells", "classes", "races",
                "equipment", "magic_items", "conditions", "features",
            ]
        }
    assert counts["monsters"] > 300
    assert counts["spells"] > 300
    assert counts["classes"] == 12
    assert counts["races"] == 9
    assert counts["conditions"] == 15
    assert counts["equipment"] > 200
    assert counts["magic_items"] > 200
    assert counts["features"] > 300


def test_monster_typed_columns(rules_db):
    with closing(sqlite3.connect(rules_db)) as conn:
        row = conn.execute(
            "SELECT name, armor_class, hit_points, challenge_rating, xp"
            " FROM monsters WHERE slug='aboleth'"
        ).fetchone()
    assert row == ("Aboleth", 17, 135, 10.0, 5900)


def test_full_record_survives_in_data_column(rules_db):
    with closing(sqlite3.connect(rules_db)) as conn:
        (data,) = conn.execute("SELECT data FROM monsters WHERE slug='aboleth'").fetchone()
    record = json.loads(data)
    assert record["speed"]["swim"] == "40 ft."
    assert any(a["name"] == "Multiattack" for a in record["actions"])


def test_cr_range_query(rules_db):
    with closing(sqlite3.connect(rules_db)) as conn:
        (n,) = conn.execute(
            "SELECT COUNT(*) FROM monsters WHERE challenge_rating <= 0.25 AND type='humanoid'"
        ).fetchone()
    assert n >= 5  # goblins, kobolds, bandits, cultists, ...


def test_meta_records_edition(rules_db):
    with closing(sqlite3.connect(rules_db)) as conn:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    assert meta["edition"] == "2014"
    assert meta["srd_version"] == "5.1"


def test_fts_index_finds_rules_text(rules_db):
    with closing(sqlite3.connect(rules_db)) as conn:
        rows = conn.execute(
            "SELECT heading_path FROM srd_text WHERE srd_text MATCH ? ORDER BY rank LIMIT 5",
            ('"opportunity attack"',),
        ).fetchall()
    assert any("Opportunity Attacks" in p for (p,) in rows)


def test_class_levels_table(rules_db):
    import contextlib

    with contextlib.closing(sqlite3.connect(rules_db)) as conn:
        (n,) = conn.execute("SELECT COUNT(*) FROM class_levels").fetchone()
        assert n == 240  # 12 classes x 20 levels, subclass rows excluded
        row = conn.execute(
            "SELECT prof_bonus, spellcasting FROM class_levels"
            " WHERE class_slug='cleric' AND level=1"
        ).fetchone()
        assert row[0] == 2
        assert json.loads(row[1])["spell_slots_level_1"] == 2
