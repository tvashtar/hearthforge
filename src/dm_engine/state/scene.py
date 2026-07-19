"""Scene visualization: SceneView builder (this task), HTML/SVG renderer
and materializer (later tasks). Spec:
docs/superpowers/specs/2026-07-19-scene-visualization-design.md.

`build_scene_view` is a pure projection of campaign state into a
JSON-serializable, PLAYER-VISIBLE model: monster HP numbers never enter it
(validator-enforced), so no renderer — or future live-view server — can
leak what the view doesn't carry. The registry's post-command hook
materializes it to campaigns/<slug>/scene.html.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator

from dm_engine.state.store import CampaignStore


def monster_condition_word(hp: int, max_hp: int) -> str:
    """The dm-session skill's public monster-status ladder, computable
    tiers only ("near death" is a DM judgment call, deliberately absent).
    full -> fresh; >half -> wounded; <=half -> bloodied; <=quarter ->
    staggering."""
    if hp >= max_hp:
        return "fresh"
    if hp > max_hp / 2:
        return "wounded"
    if hp > max_hp / 4:
        return "bloodied"
    return "staggering"


class PropView(BaseModel):
    name: str
    band: str | None = None
    note: str | None = None


class PartyRow(BaseModel):
    name: str
    hp: int
    max_hp: int
    conditions: list[str]


class TokenView(BaseModel):
    key: str
    name: str
    kind: Literal["character", "monster"]
    band: str
    engaged_with: list[str]
    conditions: list[str]
    defeated: bool
    active: bool
    hp: int | None = None              # characters only
    max_hp: int | None = None          # characters only
    condition_word: str | None = None  # monsters only

    @model_validator(mode="after")
    def _monster_numbers_stay_behind_the_screen(self) -> "TokenView":
        if self.kind == "monster":
            if self.hp is not None or self.max_hp is not None:
                raise ValueError(
                    "monster HP numbers are DM-screen only and never enter SceneView"
                )
            if self.condition_word is None:
                raise ValueError("monster tokens must carry a condition word")
        return self


class InitiativeEntry(BaseModel):
    key: str
    name: str
    active: bool


class CombatView(BaseModel):
    round: int
    tokens: list[TokenView]        # initiative order
    initiative: list[InitiativeEntry]


class SceneView(BaseModel):
    mode: Literal["combat", "scene"]
    campaign_name: str
    event_id: int
    day: int
    minutes: int
    location_name: str | None
    scene_description: str | None
    npcs_present: list[str]
    party: list[PartyRow]
    props: list[PropView]
    combat: CombatView | None = None


def build_scene_view(store: CampaignStore) -> SceneView:
    meta = store.campaign_meta()
    clock = store.world_clock()

    location = None
    npcs_present: list[str] = []
    if clock.get("location_slug"):
        location = store.get_location(clock["location_slug"])
        npcs_present = [n["name"] for n in store.npcs(clock["location_slug"])]

    party = []
    for char in store.party():
        res = store.get_resources(char["id"])
        party.append(PartyRow(
            name=char["name"], hp=res["hp"], max_hp=char["max_hp"],
            conditions=res["conditions"],
        ))

    props = [
        PropView(name=p["name"], band=p["band"], note=p["note"])
        for p in store.scene_props()
    ]

    combat_row = store.combat()
    combat = _build_combat_view(store, combat_row) if combat_row["active"] else None

    return SceneView(
        mode="combat" if combat else "scene",
        campaign_name=meta["name"],
        event_id=store.next_event_id() - 1,
        day=clock["day"],
        minutes=clock["minutes"],
        location_name=location["name"] if location else None,
        scene_description=clock.get("scene"),
        npcs_present=npcs_present,
        party=party,
        props=props,
        combat=combat,
    )


def _build_combat_view(store: CampaignStore, combat: dict) -> CombatView:
    active_key = combat["combatants"][combat["turn_index"]]["key"]
    tokens = []
    for c in combat["combatants"]:
        if c["kind"] == "character":
            res = store.get_resources(c["character_id"])
            char = store.get_character_by_id(c["character_id"])
            hp, max_hp, word = res["hp"], char["max_hp"], None
            conditions = res["conditions"]
        else:
            # The one place monster numbers get worded before they can
            # reach a player-visible surface.
            hp, max_hp = None, None
            word = monster_condition_word(c["hp"], c["max_hp"])
            conditions = c["conditions"]
        tokens.append(TokenView(
            key=c["key"], name=c["name"], kind=c["kind"], band=c["band"],
            engaged_with=list(c["engaged_with"]), conditions=list(conditions),
            defeated=bool(c["defeated"]), active=(c["key"] == active_key),
            hp=hp, max_hp=max_hp, condition_word=word,
        ))
    initiative = [
        InitiativeEntry(key=c["key"], name=c["name"], active=(c["key"] == active_key))
        for c in combat["combatants"]
    ]
    return CombatView(round=combat["round"], tokens=tokens, initiative=initiative)
