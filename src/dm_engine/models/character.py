"""Validated shapes for character mechanics.

These models are the single valid shape for the `characters.attacks` and
`characters.proficiencies` JSON columns. `create_character` (and the
open-time normalizer in state/migrate.py) guarantee every stored row
conforms, so downstream readers (attack resolver, sheet renderer) may rely
on every field being present and well-formed.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, field_validator, model_validator

Ability = Literal["str", "dex", "con", "int", "wis", "cha"]

# The canonical 18 skills (hyphenated slugs) and their governing ability.
# Moved here from commands/checks.py so models and rules can share it
# without importing the command layer.
SKILL_ABILITIES: dict[str, str] = {
    "acrobatics": "dex",
    "animal-handling": "wis",
    "arcana": "int",
    "athletics": "str",
    "deception": "cha",
    "history": "int",
    "insight": "wis",
    "intimidation": "cha",
    "investigation": "int",
    "medicine": "wis",
    "nature": "int",
    "perception": "wis",
    "performance": "cha",
    "persuasion": "cha",
    "religion": "int",
    "sleight-of-hand": "dex",
    "stealth": "dex",
    "survival": "wis",
}

_BASE_DICE_RE = re.compile(r"^\d+d\d+$")


def normalize_slug(value: str) -> str:
    """Canonical slug form: lowercase, underscores/spaces → hyphens."""
    return value.strip().lower().replace("_", "-").replace(" ", "-")


class AttackSpec(BaseModel):
    """One resolved attack. Derivation only ever produces str/dex abilities;
    the wider Ability set is for validated custom attacks (e.g. a WIS-based
    shillelagh) — the resolver handles any ability key."""

    name: str
    source: str = "custom"  # "srd:<weapon-slug>" | "custom"
    ability: Ability
    proficient: bool = True
    damage: str  # base dice only, e.g. "1d6" — modifiers computed at use
    damage_type: str
    ranged: bool
    range_ft: int
    long_range_ft: int | None = None
    properties: list[str] = []

    @field_validator("damage")
    @classmethod
    def _damage_is_base_dice(cls, v: str) -> str:
        if not _BASE_DICE_RE.match(v):
            raise ValueError(
                f"damage must be base dice only, e.g. '1d6' (got {v!r}); "
                "ability modifiers are computed at use"
            )
        return v


class Proficiencies(BaseModel):
    """`saves` is derived from class (callers may not supply it — the
    command layer enforces that); the rest are declared player choices."""

    saves: list[Ability]
    skills: list[str] = []
    expertise: list[str] = []
    tools: list[str] = []
    languages: list[str] = []

    @field_validator("skills", "expertise", "tools", mode="before")
    @classmethod
    def _normalize(cls, v: object) -> list[str]:
        if not isinstance(v, list) or not all(isinstance(s, str) for s in v):
            raise ValueError("must be a list of strings")
        return [normalize_slug(s) for s in v]

    @field_validator("skills")
    @classmethod
    def _known_skills(cls, v: list[str]) -> list[str]:
        unknown = [s for s in v if s not in SKILL_ABILITIES]
        if unknown:
            raise ValueError(f"unknown skills: {', '.join(unknown)}")
        return v

    @model_validator(mode="after")
    def _expertise_covered(self) -> "Proficiencies":
        pool = set(self.skills) | set(self.tools)
        bad = [e for e in self.expertise if e not in pool]
        if bad:
            raise ValueError(
                "expertise not covered by skills/tools: " + ", ".join(bad)
            )
        return self
