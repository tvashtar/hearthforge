"""One-time normalizer for pre-validation character rows (2026-07 format
bug: monster-style attack specs, `saving_throws` key, underscore slugs).

Idempotent and cheap, so it simply runs on every campaign open. Rows it
cannot confidently fix are left untouched — the attack resolver refuses
them cleanly on use. Deletable once no pre-fix campaigns exist.
"""

from __future__ import annotations

from pydantic import ValidationError

from dm_engine.content.lookup import RulesDB
from dm_engine.models.character import AttackSpec, Proficiencies, normalize_slug
from dm_engine.rules.character_build import derive_attack, derive_saves
from dm_engine.state.store import CampaignStore

_PROF_KEYS = ("saves", "skills", "expertise", "tools", "languages")


def _normalize_proficiencies(
    char: dict, rules: RulesDB
) -> tuple[dict | None, str | None]:
    """Returns (normalized-or-None, note-or-None). The note flags a
    character whose saves are missing and whose class can't be looked up —
    partially-fixed and worth surfacing even when nothing else changed."""
    profs = dict(char["proficiencies"])
    if "saving_throws" in profs:
        profs.setdefault("saves", profs.pop("saving_throws"))
    note = None
    if not profs.get("saves"):
        record = rules.get_class(char["class_slug"])
        if record:
            profs["saves"] = derive_saves(record)
        else:
            note = (
                f"{char['name']}: could not derive saves — "
                f"unknown class {char['class_slug']!r}"
            )
    try:
        normalized = Proficiencies(
            **{k: profs.get(k, []) for k in _PROF_KEYS}
        ).model_dump()
    except ValidationError:
        return None, note  # unfixable — leave for on-use refusals
    return (normalized if normalized != char["proficiencies"] else None), note


def _normalize_attacks(char: dict, rules: RulesDB) -> list[dict] | None:
    out: list[dict] = []
    changed = False
    for spec in char["attacks"]:
        try:
            valid = AttackSpec(**spec).model_dump()
            out.append(valid)
            changed = changed or valid != spec
            continue
        except ValidationError:
            pass
        record = rules.get_equipment(normalize_slug(spec.get("name", "")))
        class_record = rules.get_class(char["class_slug"]) or {}
        if record and "damage" in record:
            out.extend(
                s.model_dump()
                for s in derive_attack(record, char["abilities"], class_record)
            )
            changed = True
        else:
            out.append(spec)  # unfixable — refuses on use
    return out if changed else None


def normalize_characters(store: CampaignStore, rules: RulesDB) -> list[str]:
    """Returns one human-readable note per fixed character (empty = no-op).

    Informational notes (e.g. saves underivable for an unknown class) are
    regenerated on every open, so on their own they are NOT changes: they
    only ride along — in the audit event and the return value — when at
    least one row was actually rewritten. Otherwise every open would log a
    fresh event and re-trigger sheet rewrites, breaking idempotency."""
    changes: list[str] = []
    info_notes: list[str] = []
    ids = [r[0] for r in store.conn.execute("SELECT id FROM characters")]
    with store.transaction():
        for cid in ids:
            char = store.get_character_by_id(cid)
            fields: dict = {}
            new_profs, note = _normalize_proficiencies(char, rules)
            if new_profs is not None:
                fields["proficiencies"] = new_profs
            new_attacks = _normalize_attacks(char, rules)
            if new_attacks is not None:
                fields["attacks"] = new_attacks
            if fields:
                store.update_character(cid, **fields)
                changes.append(
                    f"{char['name']}: normalized {', '.join(sorted(fields))}"
                )
            if note:
                info_notes.append(note)
        if changes:
            changes = changes + info_notes
            store.append_event(
                command="migrate_normalize",
                inputs={},
                result={
                    "ok": True,
                    "command": "migrate_normalize",
                    "refusal": None,
                    "digest": "; ".join(changes),
                    "data": {"notes": changes},
                    "gm_only": False,
                    "event_ids": [],
                },
                rolls=[],
            )
    return changes
