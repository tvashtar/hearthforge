import json
import sqlite3
from pathlib import Path

import pytest

from evals.metrics import beat_done, compute_metrics, max_event_id


@pytest.fixture()
def fixture_db(tmp_path) -> Path:
    """Hand-crafted event_log with one planted defect per metric."""
    db_path = tmp_path / "campaign.sqlite"
    db = sqlite3.connect(db_path)
    db.execute(
        "CREATE TABLE event_log (id INTEGER PRIMARY KEY, command TEXT,"
        " inputs TEXT, result TEXT, created_at TEXT DEFAULT '')"
    )
    rows = [
        ("open_campaign", "{}", '{"ok": true}'),
        ("skill_check", '{"actor": "Kira"}', '{"ok": true}'),
        # refusal retry loop: same command, identical inputs, consecutive
        ("attack", '{"actor": "Kira", "target": "archer"}',
         '{"ok": false, "refusal": "target is distant"}'),
        ("attack", '{"actor": "Kira", "target": "archer"}',
         '{"ok": false, "refusal": "target is distant"}'),
        # crash
        ("cast_spell", "{}", '{"ok": false, "digest": "ENGINE CRASH: KeyError"}'),
        # orphaned tier-2: needs_ruling with no later dm_ruling
        ("cast_spell", '{"caster": "Brother Aldric"}',
         '{"ok": true, "needs_ruling": true}'),
        # player_supplied roll on a non-PC actor (must never happen)
        ("attack", '{"actor": "Brother Aldric", "player_attack_value": 15}',
         '{"ok": true, "rolls": [{"player_supplied": true}]}'),
        # polling reads
        ("get_scene_state", "{}", '{"ok": true}'),
        ("get_scene_state", "{}", '{"ok": true}'),
    ]
    db.executemany(
        "INSERT INTO event_log (command, inputs, result) VALUES (?, ?, ?)", rows
    )
    db.commit()
    db.close()
    return db_path


@pytest.fixture()
def fixture_transcript(tmp_path) -> Path:
    path = tmp_path / "transcript.jsonl"
    lines = [
        {"type": "player_message", "text": "I attack"},
        {"type": "tool_call", "name": "mcp__dm-engine__attack", "is_error": False},
        {"type": "tool_call", "name": "mcp__dm-engine__attack", "is_error": True},
        {"type": "dm_text", "text": "You swing..."},
        {"type": "player_message", "text": "again"},
        {"type": "tool_call", "name": "mcp__dm-engine__attack", "is_error": False},
    ]
    path.write_text("\n".join(json.dumps(x) for x in lines))
    return path


def test_beat_done_respects_after_id_and_ok(fixture_db):
    assert beat_done(fixture_db, {"command": "skill_check", "ok": True}, after_id=0)
    assert not beat_done(fixture_db, {"command": "skill_check", "ok": True}, after_id=2)
    assert beat_done(fixture_db, {"command": "attack", "ok": False}, after_id=0)
    assert not beat_done(fixture_db, {"command": "end_session", "ok": True}, after_id=0)


def test_max_event_id(fixture_db):
    assert max_event_id(fixture_db) == 9


def test_metrics_catch_each_planted_defect(fixture_db, fixture_transcript):
    m = compute_metrics(fixture_db, fixture_transcript)
    assert m["refusals"] == 3          # 2 attack refusals + 1 crash row (ok=false)
    assert m["refusal_retry_loops"] == 1
    assert m["crashes"] == 1
    assert m["orphaned_tier2"] == 1
    assert m["player_supplied_violations"] == 1
    assert m["schema_rejections"] == 1
    assert m["polling_reads"] == 2
    assert m["player_messages"] == 2
    assert m["tool_calls"] == 3
    assert m["tool_calls_per_player_message"] == 1.5
