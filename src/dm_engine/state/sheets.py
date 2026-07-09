"""Materialized markdown character sheets (Task 5).

`render_character_sheet` produces a stable, player-visible markdown sheet
from store state alone (no rules DB); the registry's post-command hook calls
`write_party_sheets` to materialize one file per party member after every
successful mutation. There is no `gm_only` material in these tables, so
everything stored is rendered.
"""

from __future__ import annotations

from pathlib import Path

from dm_engine.rules.checks import ability_modifier, proficiency_bonus
from dm_engine.rules.progression import xp_to_next_level
from dm_engine.state.store import CampaignStore

_ABILITY_ORDER = ("str", "dex", "con", "int", "wis", "cha")


def _sheet_filename(name: str) -> str:
    return f"{name.lower().replace(' ', '-')}.md"


def _fmt_mod(value: int) -> str:
    return f"+{value}" if value >= 0 else str(value)


def render_character_sheet(store: CampaignStore, character_id: int) -> str:
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

    # Proficiencies
    profs = char["proficiencies"]
    lines.append("## Proficiencies")
    skills = profs.get("skills", [])
    saves = profs.get("saves", [])
    lines.append(f"- Skills: {', '.join(skills) if skills else 'none'}")
    lines.append(f"- Saves: {', '.join(saves) if saves else 'none'}")
    lines.append("")

    # Attacks
    lines.append("## Attacks")
    if char["attacks"]:
        for atk in char["attacks"]:
            ability = atk.get("ability", "str")
            ability_mod = ability_modifier(abilities[ability])
            to_hit = ability_mod + (prof if atk.get("proficient") else 0)
            damage = atk.get("damage", "")
            if ability_mod:
                damage = f"{damage}{_fmt_mod(ability_mod)}"
            dtype = atk.get("damage_type", "")
            lines.append(
                f"- {atk['name']}: {_fmt_mod(to_hit)} to hit, {damage} {dtype}".rstrip()
            )
    else:
        lines.append("- none")
    lines.append("")

    # Spells known
    spells = char["spells_known"]
    if spells:
        lines.append("## Spells Known")
        for spell in spells:
            lines.append(f"- {spell}")
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


def write_party_sheets(store: CampaignStore) -> list[Path]:
    sheets_dir = store.root / "sheets"
    sheets_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for char in store.party():
        path = sheets_dir / _sheet_filename(char["name"])
        path.write_text(render_character_sheet(store, char["id"]))
        written.append(path)
    return written
