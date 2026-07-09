"""Death saving throws — identical mechanics in both campaign death modes;
M3 maps the third failure to 'defeated' (narrative) or 'dead' (hardcore).

Healing any amount of HP ends dying: M3 replaces the state with a fresh
DeathSaveState(). A natural 20 does the same and restores 1 HP.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

DeathEvent = Literal[
    "success", "failure", "critical_failure", "stabilized", "regained_hp", "died"
]


class DeathSaveState(BaseModel):
    successes: int = 0
    failures: int = 0
    stable: bool = False
    dead: bool = False


class DeathSaveOutcome(BaseModel):
    state: DeathSaveState
    event: DeathEvent
    regained_hp: bool = False


def _with_failures(state: DeathSaveState, added: int) -> DeathSaveState:
    failures = state.failures + added
    return state.model_copy(
        update={"failures": failures, "stable": False, "dead": failures >= 3}
    )


def apply_death_save(state: DeathSaveState, natural: int) -> DeathSaveOutcome:
    """One death save: DC 10; natural 1 counts two failures; natural 20
    restores 1 HP and ends dying."""
    if state.dead or state.stable:
        raise ValueError("creature is not dying")
    if not 1 <= natural <= 20:
        raise ValueError(f"d20 natural out of range: {natural}")
    if natural == 20:
        return DeathSaveOutcome(state=DeathSaveState(), event="regained_hp", regained_hp=True)
    if natural == 1:
        return DeathSaveOutcome(state=_with_failures(state, 2), event="critical_failure")
    if natural >= 10:
        successes = state.successes + 1
        if successes >= 3:
            return DeathSaveOutcome(
                state=state.model_copy(update={"successes": successes, "stable": True}),
                event="stabilized",
            )
        return DeathSaveOutcome(
            state=state.model_copy(update={"successes": successes}), event="success"
        )
    return DeathSaveOutcome(state=_with_failures(state, 1), event="failure")


def apply_damage_while_dying(
    state: DeathSaveState, damage: int, max_hp: int, *, critical: bool
) -> DeathSaveOutcome:
    """Damage at 0 HP: one death-save failure (two on a crit); damage that
    meets the HP maximum kills outright. Damage also breaks stability."""
    if state.dead:
        raise ValueError("creature is already dead")
    if damage < 0 or max_hp < 1:
        raise ValueError("damage must be >= 0 and max_hp >= 1")
    if damage >= max_hp:
        return DeathSaveOutcome(
            state=state.model_copy(update={"stable": False, "dead": True}), event="died"
        )
    added = 2 if critical else 1
    event: DeathEvent = "critical_failure" if critical else "failure"
    return DeathSaveOutcome(state=_with_failures(state, added), event=event)
