"""Creation-time derivation: SRD records in, validated specs out."""

import pytest

from dm_engine.content.lookup import RulesDB
from dm_engine.rules.character_build import (
    attack_damage_mod,
    attack_to_hit,
    build_proficiencies,
    derive_attack,
    derive_saves,
    skill_modifier,
    tool_bonus,
)

ROGUE_ABILITIES = {"str": 8, "dex": 18, "con": 12, "int": 11, "wis": 12, "cha": 10}
BRUTE_ABILITIES = {"str": 18, "dex": 10, "con": 16, "int": 8, "wis": 10, "cha": 8}


@pytest.fixture(scope="module")
def rules(rules_path):
    return RulesDB(rules_path)


def test_derive_saves_from_class(rules):
    assert derive_saves(rules.get_class("rogue")) == ["dex", "int"]
    assert derive_saves(rules.get_class("cleric")) == ["wis", "cha"]


def test_finesse_picks_higher_of_str_dex(rules):
    dagger = rules.get_equipment("dagger")
    rogue_cls = rules.get_class("rogue")
    assert derive_attack(dagger, ROGUE_ABILITIES, rogue_cls)[0].ability == "dex"
    assert derive_attack(dagger, BRUTE_ABILITIES, rogue_cls)[0].ability == "str"


def test_melee_uses_str_ranged_uses_dex(rules):
    fighter = rules.get_class("fighter")
    sword = derive_attack(rules.get_equipment("longsword"), BRUTE_ABILITIES, fighter)[0]
    bow = derive_attack(rules.get_equipment("shortbow"), ROGUE_ABILITIES, fighter)[0]
    assert (sword.ability, sword.ranged, sword.range_ft) == ("str", False, 5)
    assert (bow.ability, bow.ranged, bow.range_ft, bow.long_range_ft) == ("dex", True, 80, 320)
    assert sword.damage == "1d8" and bow.damage == "1d6"


def test_thrown_melee_weapon_emits_second_spec(rules):
    specs = derive_attack(rules.get_equipment("dagger"), ROGUE_ABILITIES, rules.get_class("rogue"))
    assert [s.name for s in specs] == ["Dagger", "Dagger (thrown)"]
    thrown = specs[1]
    assert (thrown.ranged, thrown.range_ft, thrown.long_range_ft) == (True, 20, 60)
    assert thrown.ability == specs[0].ability  # thrown finesse keeps the melee ability


def test_proficiency_matching_category_specific_and_override(rules):
    fighter, wizard = rules.get_class("fighter"), rules.get_class("wizard")
    sword = rules.get_equipment("longsword")
    dagger = rules.get_equipment("dagger")
    assert derive_attack(sword, BRUTE_ABILITIES, fighter)[0].proficient is True    # martial-weapons
    assert derive_attack(sword, BRUTE_ABILITIES, wizard)[0].proficient is False    # no match
    assert derive_attack(dagger, ROGUE_ABILITIES, wizard)[0].proficient is True    # specific: daggers
    forced = derive_attack(sword, BRUTE_ABILITIES, wizard, proficient=True)[0]
    assert forced.proficient is True                                               # override


def test_derive_attack_name_override_and_source(rules):
    spec = derive_attack(
        rules.get_equipment("longsword"), BRUTE_ABILITIES, rules.get_class("fighter"),
        name="Heirloom Blade",
    )[0]
    assert spec.name == "Heirloom Blade"
    assert spec.source == "srd:longsword"


def test_build_proficiencies_refuses_caller_saves(rules):
    with pytest.raises(ValueError, match="derived from class"):
        build_proficiencies({"skills": ["stealth"], "saves": ["cha"]}, rules.get_class("rogue"))
    with pytest.raises(ValueError, match="derived from class"):
        build_proficiencies({"saving_throws": ["cha"]}, rules.get_class("rogue"))


def test_build_proficiencies_derives_saves_and_validates_choices(rules):
    p = build_proficiencies(
        {"skills": ["stealth", "Sleight_of_Hand"], "tools": ["thieves_tools"],
         "expertise": ["stealth", "thieves_tools"], "languages": ["common"]},
        rules.get_class("rogue"),
    )
    assert p.saves == ["dex", "int"]
    assert p.skills == ["stealth", "sleight-of-hand"]
    assert p.expertise == ["stealth", "thieves-tools"]


def test_attack_to_hit_and_damage_mod():
    spec = {"ability": "dex", "proficient": True}
    assert attack_to_hit(spec, ROGUE_ABILITIES, level=1) == 6   # +4 dex, +2 prof
    assert attack_to_hit({**spec, "proficient": False}, ROGUE_ABILITIES, 1) == 4
    assert attack_damage_mod(spec, ROGUE_ABILITIES) == 4


def test_skill_modifier_tiers():
    profs = {"skills": ["stealth", "acrobatics"], "expertise": ["stealth"]}
    assert skill_modifier("stealth", profs, ROGUE_ABILITIES, 1) == 8      # 4 + 2*2
    assert skill_modifier("acrobatics", profs, ROGUE_ABILITIES, 1) == 6   # 4 + 2
    assert skill_modifier("athletics", profs, ROGUE_ABILITIES, 1) == -1   # -1, no prof


def test_tool_bonus_tiers():
    profs = {"tools": ["thieves-tools", "poisoners-kit"], "expertise": ["thieves-tools"]}
    assert tool_bonus("thieves-tools", profs, 1) == 4
    assert tool_bonus("poisoners-kit", profs, 1) == 2
    assert tool_bonus("herbalism-kit", profs, 1) == 0
