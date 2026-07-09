"""Typed views over 5e-bits SRD records.

Records keep all upstream fields (extra="allow"); these models type the
fields the engine queries and leave the rest reachable via model_extra.
The 5e-bits `index` field is the canonical slug everywhere.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MonsterRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str = Field(alias="index")
    name: str
    size: str
    type: str
    alignment: str
    armor_class: list[dict]
    hit_points: int
    hit_dice: str
    challenge_rating: float
    xp: int
    strength: int
    dexterity: int
    constitution: int
    intelligence: int
    wisdom: int
    charisma: int

    @property
    def ac(self) -> int:
        return int(self.armor_class[0]["value"])

    @property
    def ability_scores(self) -> dict[str, int]:
        return {
            "str": self.strength,
            "dex": self.dexterity,
            "con": self.constitution,
            "int": self.intelligence,
            "wis": self.wisdom,
            "cha": self.charisma,
        }


class SpellRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str = Field(alias="index")
    name: str
    level: int
    school: dict
    casting_time: str
    range: str
    duration: str
    concentration: bool
    ritual: bool
    desc: list[str]

    @property
    def school_name(self) -> str:
        return str(self.school["name"])

    @property
    def is_concentration(self) -> bool:
        return self.concentration
