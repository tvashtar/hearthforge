"""Eval matrix cells: (model family alias, thinking effort)."""

from __future__ import annotations

from dataclasses import dataclass

ABILITY_ORDER = ["haiku", "sonnet", "opus", "fable"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
DEFAULT_EFFORT = "medium"


@dataclass(frozen=True)
class Cell:
    model: str  # family alias, resolved to latest by the CLI at run time
    effort: str

    @property
    def slug(self) -> str:
        return f"{self.model}-{self.effort}"


def parse_cells(spec: str | None) -> list[Cell]:
    """Parse 'model[:effort],...' into cells, always sorted ascending ability."""
    if not spec:
        cells = [Cell(m, DEFAULT_EFFORT) for m in ABILITY_ORDER]
    else:
        cells = []
        for part in spec.split(","):
            model, _, effort = part.strip().partition(":")
            effort = effort or DEFAULT_EFFORT
            if model not in ABILITY_ORDER:
                raise ValueError(f"unknown model {model!r}; choose from {ABILITY_ORDER}")
            if effort not in EFFORTS:
                raise ValueError(f"unknown effort {effort!r}; choose from {EFFORTS}")
            cells.append(Cell(model, effort))
    return sorted(cells, key=lambda c: ABILITY_ORDER.index(c.model))
