"""FC-3: the command registry — the ONLY mutation path into a campaign.

execute() wraps every handler in one store transaction (FC-6): handler
mutations, the event row, the persisted RNG draw count, and (on success)
sheet re-rendering all land atomically. Refusals (ok=False) are logged as
events; handler exceptions roll everything back and propagate — they are
engine bugs, never gameplay.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from dm_engine.commands.envelope import CommandResult, refuse
from dm_engine.content.lookup import RulesDB
from dm_engine.rules.dice import Roll, SeededDiceRoller
from dm_engine.state import sheets
from dm_engine.state.migrate import normalize_characters
from dm_engine.state.store import CampaignStore

_COMMANDS: dict[str, Callable[..., CommandResult]] = {}


def command(name: str) -> Callable:
    def register(fn: Callable[..., CommandResult]) -> Callable[..., CommandResult]:
        if name in _COMMANDS:
            raise ValueError(f"duplicate command name: {name}")
        _COMMANDS[name] = fn
        return fn
    return register


def registered_commands() -> dict[str, Callable[..., CommandResult]]:
    return dict(_COMMANDS)


class RecordingRoller:
    """FC-2 DiceRoller that counts engine draws and captures Rolls per command."""

    def __init__(self, seed: int, initial_draws: int = 0):
        self._inner = SeededDiceRoller(seed)
        self.draws = 0
        self._captured: list[Roll] = []
        for _ in range(initial_draws):
            self._inner.roll("1d20")
        self.draws = initial_draws

    def roll(self, notation: str, *, player_value: int | None = None,
             gm_only: bool = False) -> Roll:
        result = self._inner.roll(notation, player_value=player_value, gm_only=gm_only)
        if not result.player_supplied:
            self.draws += len(result.rolls)
        self._captured.append(result)
        return result

    def begin_capture(self) -> None:
        self._captured = []

    def captured(self) -> list[Roll]:
        return list(self._captured)

    def getstate(self) -> list:
        """JSON-serializable snapshot of the inner RNG state.

        Reaches into SeededDiceRoller's internal `_rng` (its private
        random.Random) — deliberate engine-internal plumbing so we can
        persist the exact RNG position rather than fast-forwarding it.
        """
        version, internal, gauss = self._inner._rng.getstate()
        return [version, list(internal), gauss]

    def setstate(self, state: list) -> None:
        version, internal, gauss = state
        self._inner._rng.setstate((version, tuple(internal), gauss))


class CommandContext:
    def __init__(self, store: CampaignStore, roller: RecordingRoller, rules: RulesDB):
        self.store = store
        self.roller = roller
        self.rules = rules


def execute(name: str, ctx: CommandContext, /, **kwargs) -> CommandResult:
    handler = _COMMANDS.get(name)
    ctx.roller.begin_capture()
    with ctx.store.transaction():
        if handler is None:
            result = refuse(name, f"unknown command {name!r}")
        else:
            result = handler(ctx, **kwargs)
        rolls = [r.model_dump() for r in ctx.roller.captured()]
        event_id = ctx.store.append_event(
            command=name,
            inputs=kwargs,
            result=result.model_dump(),
            rolls=rolls,
            is_ruling=(name == "dm_ruling" and result.ok),
            rationale=kwargs.get("rationale") if name == "dm_ruling" else None,
        )
        result.event_ids = [event_id]
        ctx.store.set_rng_draws(ctx.roller.draws)
        ctx.store.set_rng_state(json.dumps(ctx.roller.getstate()))
        if result.ok:
            sheets.write_party_sheets(ctx.store)
    return result


def open_campaign_context(
    campaigns_dir: Path, slug: str, rules_db_path: Path
) -> CommandContext:
    store = CampaignStore.open(campaigns_dir, slug)
    rules = RulesDB(rules_db_path)
    normalize_characters(store, rules)
    meta = store.campaign_meta()
    if meta.get("rng_state") is not None:
        roller = RecordingRoller(meta["rng_seed"])
        roller.draws = meta["rng_draws"]
        roller.setstate(json.loads(meta["rng_state"]))
    else:
        # Legacy/fresh campaigns with no persisted RNG state: fall back to
        # fast-forwarding d20 draws (only exact when all prior rolls were d20).
        roller = RecordingRoller(meta["rng_seed"], initial_draws=meta["rng_draws"])
    return CommandContext(store=store, roller=roller, rules=rules)
