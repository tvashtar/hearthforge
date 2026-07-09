from dm_engine.content.lookup import RulesDB


def test_get_monster_roundtrips_typed_record(rules_db):
    with RulesDB(rules_db) as db:
        aboleth = db.get_monster("aboleth")
    assert aboleth is not None
    assert aboleth.hit_points == 135
    assert aboleth.ability_scores["str"] == 21


def test_get_monster_missing_returns_none(rules_db):
    with RulesDB(rules_db) as db:
        assert db.get_monster("tarrasque-jr") is None


def test_search_monsters_filters_by_cr_and_type(rules_db):
    with RulesDB(rules_db) as db:
        results = db.search_monsters(max_cr=0.25, type="humanoid")
    assert any(r.slug == "goblin" for r in results)
    assert all(r.challenge_rating <= 0.25 for r in results)


def test_get_spell(rules_db):
    with RulesDB(rules_db) as db:
        mm = db.get_spell("magic-missile")
    assert mm is not None and mm.level == 1


def test_search_spells_by_level(rules_db):
    with RulesDB(rules_db) as db:
        cantrips = db.search_spells(level=0)
    assert any(s.slug == "fire-bolt" for s in cantrips)
    assert all(s.level == 0 for s in cantrips)


def test_lookup_rule_returns_grappling_section(rules_db):
    with RulesDB(rules_db) as db:
        hits = db.lookup_rule("grappling")
    assert hits, "expected at least one FTS hit"
    assert any("Grappl" in h.heading_path for h in hits)


def test_lookup_rule_survives_fts_special_chars(rules_db):
    with RulesDB(rules_db) as db:
        hits = db.lookup_rule('opportunity "attack" -weird*')
    assert isinstance(hits, list)  # must not raise on FTS syntax characters
