"""Per-turn action economy: one action, one bonus action, one reaction,
speed-limited movement. Budgets are immutable; spending returns a new one.
Illegal spends return refusals (ok=False), not exceptions — M3 forwards
the reason to the player."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ActionKind = Literal["action", "bonus_action", "reaction"]


class TurnBudget(BaseModel):
    speed: int
    movement_remaining: int
    action_available: bool = True
    bonus_action_available: bool = True
    reaction_available: bool = True


def new_turn(speed: int) -> TurnBudget:
    if speed < 0:
        raise ValueError("speed cannot be negative")
    return TurnBudget(speed=speed, movement_remaining=speed)


class SpendResult(BaseModel):
    ok: bool
    reason: str | None = None
    budget: TurnBudget


def spend(budget: TurnBudget, kind: ActionKind) -> SpendResult:
    field = f"{kind}_available"
    if not getattr(budget, field):
        return SpendResult(
            ok=False,
            reason=f"no {kind.replace('_', ' ')} remaining this turn",
            budget=budget,
        )
    return SpendResult(ok=True, budget=budget.model_copy(update={field: False}))


def spend_movement(budget: TurnBudget, feet: int) -> SpendResult:
    if feet < 0:
        raise ValueError("movement cannot be negative")
    if feet > budget.movement_remaining:
        return SpendResult(
            ok=False,
            reason=f"only {budget.movement_remaining} ft of movement remaining",
            budget=budget,
        )
    return SpendResult(
        ok=True,
        budget=budget.model_copy(
            update={"movement_remaining": budget.movement_remaining - feet}
        ),
    )


def dash(budget: TurnBudget) -> SpendResult:
    """Dash: spend the action, gain speed's worth of extra movement."""
    result = spend(budget, "action")
    if not result.ok:
        return result
    b = result.budget
    return SpendResult(
        ok=True,
        budget=b.model_copy(update={"movement_remaining": b.movement_remaining + b.speed}),
    )
