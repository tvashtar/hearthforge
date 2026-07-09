"""Creation-time derivation of character mechanics from SRD records, plus
the shared modifier math used by BOTH the attack/check resolvers and the
sheet renderer (so displayed and rolled numbers can never diverge).

Pure functions: records/dicts in, values out. No I/O, no store access — the
command layer fetches records and converts ValueError/ValidationError into
structured refusals.
"""

from __future__ import annotations

from dm_engine.models.character import SKILL_ABILITIES, AttackSpec, Proficiencies
from dm_engine.rules.checks import ability_modifier, proficiency_bonus


def derive_saves(class_record: dict) -> list[str]:
    """Save proficiencies are a rules fact: straight from the SRD class."""
    return [s["index"] for s in class_record.get("saving_throws", [])]


def _weapon_proficient(weapon_record: dict, class_record: dict) -> bool:
    """Category match (simple-weapons/martial-weapons) or specific match
    (SRD proficiency indexes are pluralized weapon slugs, e.g. 'daggers')."""
    profs = {p["index"] for p in class_record.get("proficiencies", [])}
    category = weapon_record.get("weapon_category", "").lower()  # simple|martial
    if f"{category}-weapons" in profs:
        return True
    return f"{weapon_record['index']}s" in profs


def derive_attack(
    weapon_record: dict,
    abilities: dict,
    class_record: dict,
    *,
    name: str | None = None,
    proficient: bool | None = None,
) -> list[AttackSpec]:
    props = [p["index"] for p in weapon_record.get("properties", [])]
    is_ranged = weapon_record.get("weapon_range") == "Ranged"
    if "finesse" in props:
        ability = (
            "dex"
            if ability_modifier(abilities["dex"]) >= ability_modifier(abilities["str"])
            else "str"
        )
    else:
        ability = "dex" if is_ranged else "str"
    dmg = weapon_record["damage"]
    rng = weapon_record.get("range", {})
    base = AttackSpec(
        name=name or weapon_record["name"],
        source=f"srd:{weapon_record['index']}",
        ability=ability,
        proficient=(
            _weapon_proficient(weapon_record, class_record)
            if proficient is None
            else proficient
        ),
        damage=dmg["damage_dice"],
        damage_type=dmg["damage_type"]["index"],
        ranged=is_ranged,
        range_ft=rng.get("normal", 5),
        long_range_ft=rng.get("long"),
        properties=props,
    )
    specs = [base]
    throw = weapon_record.get("throw_range")
    if throw and not is_ranged:
        # The resolver's spec shape is single-mode, so a thrown melee weapon
        # is two specs; the thrown profile keeps the melee ability (RAW).
        specs.append(base.model_copy(update={
            "name": f"{base.name} (thrown)",
            "ranged": True,
            "range_ft": throw["normal"],
            "long_range_ft": throw["long"],
        }))
    return specs


def build_proficiencies(declared: dict, class_record: dict) -> Proficiencies:
    if "saves" in declared or "saving_throws" in declared:
        raise ValueError(
            "save proficiencies are derived from class; do not supply them"
        )
    return Proficiencies(
        saves=derive_saves(class_record),
        skills=declared.get("skills", []),
        expertise=declared.get("expertise", []),
        tools=declared.get("tools", []),
        languages=declared.get("languages", []),
    )


# -- shared modifier math (resolvers + sheet renderer) ---------------------


def attack_to_hit(spec: dict, abilities: dict, level: int) -> int:
    mod = ability_modifier(abilities[spec["ability"]])
    return mod + (proficiency_bonus(level) if spec.get("proficient") else 0)


def attack_damage_mod(spec: dict, abilities: dict) -> int:
    return ability_modifier(abilities[spec["ability"]])


def skill_modifier(
    skill: str, proficiencies: dict, abilities: dict, level: int
) -> int:
    modifier = ability_modifier(abilities[SKILL_ABILITIES[skill]])
    if skill in proficiencies.get("skills", []):
        bonus = proficiency_bonus(level)
        if skill in proficiencies.get("expertise", []):
            bonus *= 2
        modifier += bonus
    return modifier


def tool_bonus(tool: str, proficiencies: dict, level: int) -> int:
    """Proficiency component only — the per-check ability is chosen by the
    tool_check command, so the ability modifier is added there."""
    if tool not in proficiencies.get("tools", []):
        return 0
    bonus = proficiency_bonus(level)
    if tool in proficiencies.get("expertise", []):
        bonus *= 2
    return bonus
