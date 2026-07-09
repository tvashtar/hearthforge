import json
from pathlib import Path

from dm_engine.models.srd import MonsterRecord, SpellRecord

STRUCTURED = Path(__file__).parent.parent / "data" / "srd" / "2014" / "structured"


def _load(filename: str) -> list[dict]:
    return json.loads((STRUCTURED / filename).read_text())


def test_monster_record_parses_aboleth():
    monsters = {m["index"]: m for m in _load("5e-SRD-Monsters.json")}
    aboleth = MonsterRecord.model_validate(monsters["aboleth"])
    assert aboleth.slug == "aboleth"
    assert aboleth.name == "Aboleth"
    assert aboleth.ac == 17
    assert aboleth.hit_points == 135
    assert aboleth.challenge_rating == 10
    assert aboleth.xp == 5900
    assert aboleth.ability_scores == {
        "str": 21, "dex": 9, "con": 15, "int": 18, "wis": 15, "cha": 18,
    }


def test_every_monster_parses():
    records = [MonsterRecord.model_validate(m) for m in _load("5e-SRD-Monsters.json")]
    assert len(records) > 300
    assert all(r.hit_points > 0 and r.ac > 0 for r in records)


def test_spell_record_parses_magic_missile():
    spells = {s["index"]: s for s in _load("5e-SRD-Spells.json")}
    mm = SpellRecord.model_validate(spells["magic-missile"])
    assert mm.slug == "magic-missile"
    assert mm.level == 1
    assert mm.school_name == "Evocation"
    assert mm.is_concentration is False


def test_every_spell_parses():
    records = [SpellRecord.model_validate(s) for s in _load("5e-SRD-Spells.json")]
    assert len(records) > 300
    assert all(0 <= r.level <= 9 for r in records)
