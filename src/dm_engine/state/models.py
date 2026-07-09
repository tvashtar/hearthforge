"""State models shared by the store and command handlers."""

from __future__ import annotations

from pydantic import BaseModel

from dm_engine.rules.bands import Band


class Combatant(BaseModel):
    """One entry in combat_state.combatants (JSON).

    Characters keep hp/conditions in their own tables (source of truth);
    monster instances carry theirs here. `key` addresses the combatant in
    every combat command: the character's name, or '<slug>-<n>' for monsters.
    """

    key: str
    kind: str  # "character" | "monster"
    name: str
    character_id: int | None = None
    monster_slug: str | None = None
    initiative: int
    dex_modifier: int
    ac: int
    hp: int | None = None       # monsters only
    max_hp: int | None = None   # monsters only
    xp: int = 0                 # monsters only: XP value for the award
    band: Band = "near"
    engaged_with: list[str] = []
    conditions: list[str] = []  # monsters only
    defeated: bool = False
    # v1 surprise: cleared after round 1; suppresses the round-1 budget.
    surprised: bool = False
    # reaction spent since the top of this round (opportunity attacks etc.);
    # reset by next_turn each round. Consumed by Task 8's reaction commands.
    reaction_used: bool = False
    # current-turn action economy, reset by next_turn (JSON of rules TurnBudget)
    budget: dict | None = None
