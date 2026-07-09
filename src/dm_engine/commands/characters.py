"""Character commands: creation, sheet retrieval, and XP / leveling.

`award_party_xp` is factored out so Task 7's `end_combat` can reuse the exact
leveling math; the `award_xp` command is a thin wrapper over it.
"""

from __future__ import annotations

from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.commands.registry import CommandContext, command
from dm_engine.rules.checks import ability_modifier
from dm_engine.rules.progression import level_for_xp, level_up_hp_gain, max_hp_for_level
from dm_engine.state.sheets import render_character_sheet

_ROLES = ("pc", "companion")
_ABILITY_KEYS = ("str", "dex", "con", "int", "wis", "cha")


def _sheet_payload(ctx: CommandContext, character_id: int) -> dict:
    """The read-only sheet payload shared by create_character and
    get_character_sheet: typed character fields, resources, inventory, and
    the rendered markdown."""
    char = ctx.store.get_character_by_id(character_id)
    return {
        "character": char,
        "resources": ctx.store.get_resources(character_id),
        "inventory": ctx.store.items_for(character_id),
        "markdown": render_character_sheet(ctx.store, character_id),
    }


@command("create_character")
def create_character(
    ctx: CommandContext,
    name: str,
    role: str,
    class_slug: str,
    race_slug: str,
    abilities: dict,
    ac: int,
    proficiencies: dict,
    attacks: list[dict],
    speed: int = 30,
    spells_known: list[str] | None = None,
    **kwargs,
) -> CommandResult:
    spells_known = spells_known or []

    # Validations, in the frozen order.
    if ctx.store.get_character(name) is not None:
        return refuse("create_character", f"a character named {name!r} already exists")
    if role not in _ROLES:
        return refuse("create_character", f"invalid role {role!r} (expected pc/companion)")
    class_record = ctx.rules.get_class(class_slug)
    if class_record is None or ctx.rules.get_class_level(class_slug, 1) is None:
        return refuse("create_character", f"unknown class {class_slug!r}")
    missing = [k for k in _ABILITY_KEYS if k not in abilities]
    if missing:
        return refuse(
            "create_character", f"abilities missing keys: {', '.join(missing)}"
        )
    for key in _ABILITY_KEYS:
        score = abilities[key]
        if not (isinstance(score, int) and 1 <= score <= 30):
            return refuse(
                "create_character", f"ability {key} out of range (1-30): {score!r}"
            )
    if role == "pc" and any(c["role"] == "pc" for c in ctx.store.party()):
        return refuse(
            "create_character", "the party already has a living pc (only one allowed)"
        )

    # Derive level-1 stats.
    hit_die = class_record["hit_die"]
    con_mod = ability_modifier(abilities["con"])
    max_hp = max_hp_for_level(hit_die, con_mod, 1)
    spell_slots = ctx.rules.spell_slots_for(class_slug, 1)

    cid = ctx.store.insert_character(
        name=name, role=role, class_slug=class_slug, race_slug=race_slug, level=1,
        abilities=abilities, max_hp=max_hp, ac=ac, speed=speed,
        proficiencies=proficiencies, attacks=attacks, spells_known=spells_known,
        spell_slots=spell_slots,
    )

    return CommandResult(
        ok=True, command="create_character",
        digest=f"{name} the {class_slug} joins the party (HP {max_hp}, AC {ac})",
        data=_sheet_payload(ctx, cid),
    )


@command("get_character_sheet")
def get_character_sheet(ctx: CommandContext, name: str, **kwargs) -> CommandResult:
    char = ctx.store.get_character(name)
    if char is None:
        return refuse("get_character_sheet", f"no character named {name!r}")
    data = _sheet_payload(ctx, char["id"])
    return CommandResult(
        ok=True, command="get_character_sheet",
        digest=f"Character sheet for {name}", data=data,
    )


def award_party_xp(ctx: CommandContext, total: int, reason: str) -> dict:
    """Split `total` XP evenly (floor) across active party members, applying
    any level-ups immediately. Returns the `award_xp` data payload. Reused by
    end_combat (Task 7). Callers guarantee total > 0 and a non-empty party."""
    recipients_chars = [c for c in ctx.store.party() if c["status"] == "active"]
    per_member = total // len(recipients_chars)
    recipients: list[dict] = []

    for char in recipients_chars:
        cid = char["id"]
        old_level = char["level"]
        new_xp = char["xp"] + per_member
        new_level = level_for_xp(new_xp)
        leveled_up = new_level > old_level

        if leveled_up:
            hit_die = ctx.rules.get_class(char["class_slug"])["hit_die"]
            con_mod = ability_modifier(char["abilities"]["con"])
            new_max_hp = char["max_hp"]
            for _ in range(new_level - old_level):
                new_max_hp += level_up_hp_gain(hit_die, con_mod)

            res = ctx.store.get_resources(cid)
            slots = res["spell_slots"]
            for slot_level, new_max in ctx.rules.spell_slots_for(
                char["class_slug"], new_level
            ).items():
                key = str(slot_level)
                current = slots.get(key, {"max": 0, "remaining": 0})
                delta = new_max - current["max"]
                slots[key] = {
                    "max": new_max,
                    "remaining": current["remaining"] + max(0, delta),
                }
            ctx.store.update_resources(
                cid,
                spell_slots=slots,
                hit_dice_remaining=res["hit_dice_remaining"] + (new_level - old_level),
            )
            ctx.store.update_character(cid, level=new_level, xp=new_xp, max_hp=new_max_hp)
        else:
            new_max_hp = char["max_hp"]
            ctx.store.update_character(cid, xp=new_xp)

        recipients.append({
            "name": char["name"],
            "xp": new_xp,
            "level": new_level,
            "leveled_up": leveled_up,
            "new_max_hp": new_max_hp,
        })

    return {"per_member": per_member, "recipients": recipients}


@command("award_xp")
def award_xp(ctx: CommandContext, amount: int, reason: str, **kwargs) -> CommandResult:
    if amount <= 0:
        return refuse("award_xp", "xp amount must be positive")
    if not any(c["status"] == "active" for c in ctx.store.party()):
        return refuse("award_xp", "no active party members to award xp to")

    data = award_party_xp(ctx, amount, reason)
    per_member = data["per_member"]
    levelers = [r["name"] for r in data["recipients"] if r["leveled_up"]]
    digest = f"Awarded {amount} XP ({per_member} each)"
    if levelers:
        reaches = ", ".join(
            f"{r['name']} reaches level {r['level']}"
            for r in data["recipients"] if r["leveled_up"]
        )
        digest += f" — {reaches}!"
    return CommandResult(ok=True, command="award_xp", digest=digest, data=data)
