"""Initiative: d20 + DEX modifier. Ties break by higher DEX modifier, then
by input order — deterministic so a resumed combat reproduces the order."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel

from dm_engine.rules.dice import DiceRoller, Roll


class InitiativeEntry(BaseModel):
    combatant_id: str
    roll: Roll
    dex_modifier: int
    total: int


def roll_initiative(
    roller: DiceRoller,
    combatants: Sequence[tuple[str, int]],
    *,
    player_values: Mapping[str, int] | None = None,
) -> list[InitiativeEntry]:
    """Roll for every (combatant_id, dex_modifier) and return turn order.

    `player_values` maps a combatant id to a player-reported natural d20.
    """
    if not combatants:
        raise ValueError("no combatants")
    player_values = player_values or {}
    indexed: list[tuple[int, InitiativeEntry]] = []
    for index, (combatant_id, dex_modifier) in enumerate(combatants):
        roll = roller.roll("1d20", player_value=player_values.get(combatant_id))
        entry = InitiativeEntry(
            combatant_id=combatant_id,
            roll=roll,
            dex_modifier=dex_modifier,
            total=roll.total + dex_modifier,
        )
        indexed.append((index, entry))
    indexed.sort(key=lambda pair: (-pair[1].total, -pair[1].dex_modifier, pair[0]))
    return [entry for _, entry in indexed]
