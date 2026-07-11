"""Materialized markdown character sheets (Task 5).

`render_character_sheet` produces a stable, player-visible markdown sheet
from store state plus static reference data from the rules DB (class
features by class/level, per-spell metadata); the registry's post-command
hook calls `write_party_sheets` to materialize one file per party member
after every successful mutation. There is no `gm_only` material in these
tables, so everything stored is rendered.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from dm_engine.content.lookup import RulesDB
from dm_engine.models.character import SKILL_ABILITIES, AttackSpec
from dm_engine.rules.character_build import (
    attack_damage_mod,
    attack_to_hit,
    skill_modifier,
    tool_bonus,
)
from dm_engine.rules.checks import ability_modifier, proficiency_bonus
from dm_engine.rules.progression import xp_to_next_level
from dm_engine.state.store import CampaignStore

_ABILITY_ORDER = ("str", "dex", "con", "int", "wis", "cha")


def _sheet_filename(name: str) -> str:
    return f"{name.lower().replace(' ', '-')}.md"


def _fmt_mod(value: int) -> str:
    return f"+{value}" if value >= 0 else str(value)


def _one_line_description(description: str) -> str:
    """First paragraph of a feature description — the reminder line on the
    sheet; full text stays a `lookup_feature` / `dm lookup feature` away."""
    return description.split("\n", 1)[0].strip()


def _feature_dice_annotation(feature_slug: str, class_specific: dict) -> str:
    """Level-scaled dice for features the SRD tracks per level in the class
    table (e.g. Sneak Attack 1d6, Martial Arts 1d4)."""
    spec = class_specific.get(feature_slug.replace("-", "_"))
    if isinstance(spec, dict) and "dice_count" in spec and "dice_value" in spec:
        return f" ({spec['dice_count']}d{spec['dice_value']})"
    return ""


def _spell_line(rules: RulesDB, slug: str) -> str:
    """`Name — level, components[, ritual][, concentration]`; unknown slugs
    (homebrew spells the rules DB can't resolve) render bare."""
    record = rules.get_spell(slug)
    if record is None:
        return slug
    parts = ["cantrip" if record.level == 0 else f"L{record.level}"]
    components = getattr(record, "components", None)
    if components:
        parts.append("/".join(components))
    if record.ritual:
        parts.append("ritual")
    if record.concentration:
        parts.append("concentration")
    return f"{record.name} — {', '.join(parts)}"


def render_character_sheet(
    store: CampaignStore, character_id: int, rules: RulesDB
) -> str:
    char = store.get_character_by_id(character_id)
    if char is None:
        raise KeyError(f"no character with id {character_id}")
    res = store.get_resources(character_id)
    inventory = store.items_for(character_id)

    level = char["level"]
    prof = proficiency_bonus(level)
    abilities = char["abilities"]
    lines: list[str] = []

    lines.append(f"# {char['name']}")
    lines.append("")
    lines.append(
        f"*{char['role']} — {char['race_slug']} {char['class_slug']}, level {level}*"
    )
    lines.append("")

    # Experience
    to_next = xp_to_next_level(char["xp"])
    to_next_str = "max level" if to_next is None else f"{to_next} to next level"
    lines.append("## Experience")
    lines.append(f"- XP: {char['xp']} ({to_next_str})")
    lines.append(f"- Status: {char['status']}")
    lines.append("")

    # Abilities
    lines.append("## Abilities")
    for ability in _ABILITY_ORDER:
        score = abilities[ability]
        mod = ability_modifier(score)
        lines.append(f"- {ability.upper()}: {score} ({_fmt_mod(mod)})")
    lines.append("")

    # Defense / vitals
    lines.append("## Defense")
    lines.append(f"- AC: {char['ac']}")
    lines.append(f"- Speed: {char['speed']} ft")
    hp_line = f"- HP: {res['hp']} / {char['max_hp']}"
    if res["temp_hp"]:
        hp_line += f" (+{res['temp_hp']} temp)"
    lines.append(hp_line)
    lines.append(f"- Hit Dice: {res['hit_dice_remaining']} / {level}")
    lines.append("")

    # Spell slots
    slots = res["spell_slots"]
    if slots:
        lines.append("## Spell Slots")
        for slot_level in sorted(slots, key=int):
            entry = slots[slot_level]
            lines.append(
                f"- Level {slot_level}: {entry['remaining']} / {entry['max']}"
            )
        lines.append("")

    # Conditions & exhaustion
    conditions = res["conditions"]
    lines.append("## Conditions")
    lines.append(f"- Conditions: {', '.join(conditions) if conditions else 'none'}")
    lines.append(f"- Exhaustion: {res['exhaustion']}")
    concentration = res.get("concentration")
    if concentration:
        if isinstance(concentration, dict):
            spell = concentration.get("spell", "unknown")
            duration = concentration.get("duration")
            conc_line = f"- Concentrating on: {spell}"
            if duration:
                conc_line += f" ({duration})"
            lines.append(conc_line)
        else:
            lines.append(f"- Concentrating on: {concentration}")
    lines.append("")

    # Death saves — only rendered while dying / with recorded saves
    death = res["death_saves"]
    dying = res["hp"] <= 0 and not death.get("dead") and not death.get("stable")
    if dying or death.get("successes") or death.get("failures"):
        succ = "●" * death.get("successes", 0) + "○" * (3 - death.get("successes", 0))
        fail = "●" * death.get("failures", 0) + "○" * (3 - death.get("failures", 0))
        lines.append("## Death Saves")
        lines.append(f"- Successes: {succ}")
        lines.append(f"- Failures: {fail}")
        lines.append("")

    profs = char["proficiencies"]

    # Saving throws — all six, proficient first
    save_profs = profs.get("saves", [])
    lines.append("## Saving Throws")

    def _save_entry(ability: str) -> str:
        marker = "◉" if ability in save_profs else "○"
        mod = ability_modifier(abilities[ability]) + (
            prof if ability in save_profs else 0
        )
        return f"{marker} {ability.upper()} {_fmt_mod(mod)}"

    proficient = [a for a in _ABILITY_ORDER if a in save_profs]
    plain = [a for a in _ABILITY_ORDER if a not in save_profs]
    if proficient:
        lines.append("- " + "   ".join(_save_entry(a) for a in proficient))
    lines.append("- " + "   ".join(_save_entry(a) for a in plain))
    lines.append("")

    # Skills — all 18: expertise, then proficient, then the rest
    skill_list = profs.get("skills", [])
    expertise = profs.get("expertise", [])
    lines.append("## Skills")

    def _skill_rank(s: str) -> tuple:
        return (s not in expertise, s not in skill_list, s)

    for skill in sorted(SKILL_ABILITIES, key=_skill_rank):
        mod = skill_modifier(skill, profs, abilities, level)
        label = skill.replace("-", " ").title()
        if skill in expertise:
            lines.append(f"- ◉◉ {label} {_fmt_mod(mod)} (expertise)")
        elif skill in skill_list:
            lines.append(f"- ◉ {label} {_fmt_mod(mod)}")
        else:
            lines.append(f"- ○ {label} {_fmt_mod(mod)}")
    passive = 10 + skill_modifier("perception", profs, abilities, level)
    lines.append(f"- Passive Perception: {passive}")
    lines.append("")

    # Tools — proficiency component only (ability chosen per check)
    tools = profs.get("tools", [])
    if tools:
        lines.append("## Tools")
        for tool in tools:
            bonus = tool_bonus(tool, profs, level)
            marker = "◉◉" if tool in expertise else "◉"
            lines.append(f"- {marker} {tool} (prof {_fmt_mod(bonus)})")
        lines.append("")

    # Attacks — computed exactly as the resolver computes them
    lines.append("## Attacks")
    if char["attacks"]:
        for atk in char["attacks"]:
            try:
                AttackSpec(**atk)
            except ValidationError:
                # Legacy spec the migration deliberately left untouched
                # (see state/migrate.py) — refuses on use, degraded here.
                name = atk.get("name", "unknown attack")
                lines.append(f"- {name}: (invalid legacy spec — refuses on use)")
                continue
            to_hit = attack_to_hit(atk, abilities, level)
            dmg_mod = attack_damage_mod(atk, abilities)
            damage = f"{atk['damage']}{_fmt_mod(dmg_mod)}"
            if atk.get("ranged") and atk.get("long_range_ft"):
                annot = f" ({atk['range_ft']}/{atk['long_range_ft']})"
            elif "finesse" in atk.get("properties", []):
                annot = " (finesse)"
            else:
                annot = ""
            lines.append(
                f"- {atk['name']}: {_fmt_mod(to_hit)} to hit, "
                f"{damage} {atk['damage_type']}{annot}"
            )
    else:
        lines.append("- none")
    lines.append("")

    # Class features — derived from class + level, so a level-up re-render
    # picks up new features with no per-character bookkeeping.
    features = rules.class_features(char["class_slug"], level)
    if features:
        class_level = rules.get_class_level(char["class_slug"], level) or {}
        class_specific = class_level.get("class_specific", {})
        lines.append("## Features")
        for feat in features:
            annot = _feature_dice_annotation(feat.slug, class_specific)
            entry = f"- {feat.name}{annot}"
            one_liner = _one_line_description(feat.description)
            if one_liner:
                entry += f" — {one_liner}"
            lines.append(entry)
        lines.append("")

    # Spells known — annotated from the rules DB (level, V/S/M components,
    # ritual/concentration); full text stays a `lookup_spell` away.
    spells = char["spells_known"]
    if spells:
        lines.append("## Spells Known")
        for spell in spells:
            lines.append(f"- {_spell_line(rules, spell)}")
        lines.append("")

    # Inventory
    lines.append("## Inventory")
    if inventory:
        for item in inventory:
            qty = item["quantity"]
            suffix = f" x{qty}" if qty != 1 else ""
            marks = []
            if item["equipped"]:
                marks.append("equipped")
            if item["attuned"]:
                marks.append("attuned")
            mark_str = f" ({', '.join(marks)})" if marks else ""
            lines.append(f"- {item['name']}{suffix}{mark_str}")
    else:
        lines.append("- (empty)")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_party_sheets(store: CampaignStore, rules: RulesDB) -> list[Path]:
    sheets_dir = store.root / "sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for char in store.party():
        path = sheets_dir / _sheet_filename(char["name"])
        path.write_text(render_character_sheet(store, char["id"], rules))
        written.append(path)
    return written
