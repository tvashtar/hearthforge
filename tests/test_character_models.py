"""AttackSpec/Proficiencies are the only valid stored character-mechanics
shapes; these tests pin the validation behavior everything else relies on."""

import pytest
from pydantic import ValidationError

from dm_engine.models.character import (
    SKILL_ABILITIES,
    AttackSpec,
    Proficiencies,
    normalize_slug,
)


def test_skill_abilities_has_all_18_canonical_skills():
    assert len(SKILL_ABILITIES) == 18
    assert SKILL_ABILITIES["sleight-of-hand"] == "dex"
    assert SKILL_ABILITIES["athletics"] == "str"


def test_normalize_slug_maps_underscores_and_case():
    assert normalize_slug("Thieves_Tools") == "thieves-tools"
    assert normalize_slug("sleight of hand") == "sleight-of-hand"


def test_attack_spec_accepts_valid_melee_spec():
    spec = AttackSpec(
        name="Shortsword", source="srd:shortsword", ability="dex",
        damage="1d6", damage_type="piercing", ranged=False, range_ft=5,
        properties=["finesse", "light"],
    )
    assert spec.proficient is True          # default
    assert spec.long_range_ft is None       # default


def test_attack_spec_rejects_damage_with_baked_in_modifier():
    with pytest.raises(ValidationError, match="base dice only"):
        AttackSpec(
            name="Shortsword", source="custom", ability="dex",
            damage="1d6+4", damage_type="piercing", ranged=False, range_ft=5,
        )


def test_attack_spec_rejects_missing_ability():
    with pytest.raises(ValidationError):
        AttackSpec(
            name="Shortsword", source="custom",
            damage="1d6", damage_type="piercing", ranged=False, range_ft=5,
        )


def test_proficiencies_normalizes_slugs():
    p = Proficiencies(saves=["dex"], skills=["Stealth"], tools=["thieves_tools"],
                      expertise=["stealth", "thieves_tools"])
    assert p.skills == ["stealth"]
    assert p.tools == ["thieves-tools"]
    assert p.expertise == ["stealth", "thieves-tools"]


def test_proficiencies_rejects_unknown_skill():
    with pytest.raises(ValidationError, match="unknown skills: lockpicking"):
        Proficiencies(saves=[], skills=["lockpicking"])


def test_proficiencies_rejects_expertise_outside_skills_and_tools():
    with pytest.raises(ValidationError, match="expertise not covered"):
        Proficiencies(saves=[], skills=["stealth"], expertise=["athletics"])


def test_proficiencies_rejects_bad_save_ability():
    with pytest.raises(ValidationError):
        Proficiencies(saves=["luck"], skills=[])
