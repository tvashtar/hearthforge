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


def test_get_feature_returns_full_record(rules_db):
    with RulesDB(rules_db) as db:
        feat = db.get_feature("cunning-action")
        assert db.get_feature("nonexistent") is None
    assert feat is not None
    assert feat["name"] == "Cunning Action"
    assert any("Dash, Disengage, or Hide" in p for p in feat["desc"])


def test_class_features_accumulate_by_level(rules_db):
    with RulesDB(rules_db) as db:
        l1 = {f.slug for f in db.class_features("rogue", 1)}
        l2 = {f.slug for f in db.class_features("rogue", 2)}
    assert {"sneak-attack", "thieves-cant", "rogue-expertise-1"} <= l1
    assert "cunning-action" not in l1
    assert "cunning-action" in l2
    assert l1 <= l2  # leveling only adds features


def test_class_features_carry_name_level_description(rules_db):
    with RulesDB(rules_db) as db:
        feats = {f.slug: f for f in db.class_features("rogue", 2)}
    cunning = feats["cunning-action"]
    assert cunning.name == "Cunning Action"
    assert cunning.level == 2
    assert "bonus action" in cunning.description.lower()


def test_class_features_exclude_subclass_and_option_records(rules_db):
    with RulesDB(rules_db) as db:
        slugs = {f.slug for f in db.class_features("fighter", 3)}
    assert {"second-wind", "action-surge-1-use", "fighter-fighting-style"} <= slugs
    # Choice sub-options (parented records) and subclass features are not
    # class-wide grants — they must not appear.
    assert "fighter-fighting-style-archery" not in slugs
    assert "improved-critical" not in slugs  # Champion (subclass), level 3


def test_lookup_rule_returns_grappling_section(rules_db):
    with RulesDB(rules_db) as db:
        hits = db.lookup_rule("grappling")
    assert hits, "expected at least one FTS hit"
    assert any("Grappl" in h.heading_path for h in hits)


def test_lookup_rule_survives_fts_special_chars(rules_db):
    with RulesDB(rules_db) as db:
        hits = db.lookup_rule('opportunity "attack" -weird*')
    assert isinstance(hits, list)  # must not raise on FTS syntax characters


def test_spell_slots_for_cleric(rules_db):
    with RulesDB(rules_db) as db:
        assert db.spell_slots_for("cleric", 1) == {1: 2}
        assert db.spell_slots_for("cleric", 5) == {1: 4, 2: 3, 3: 2}
        assert db.spell_slots_for("fighter", 5) == {}
        assert db.spell_slots_for("cleric", 99) == {}


def test_get_class_level(rules_db):
    with RulesDB(rules_db) as db:
        record = db.get_class_level("wizard", 3)
        assert record is not None and record["prof_bonus"] == 2
        assert db.get_class_level("wizard", 21) is None
