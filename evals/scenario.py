"""Load the scenario spec and build an identical starting campaign state."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from dm_engine.commands import registry
from dm_engine.commands.campaign import bootstrap_campaign


@dataclass(frozen=True)
class Beat:
    id: str
    goal: str
    done_when: dict
    notes: str | None = None
    max_player_messages: int = 4


@dataclass(frozen=True)
class Scenario:
    name: str
    premise: str
    player_persona: str
    pc_name: str
    party: list[dict]
    starting_region: dict
    quest: dict
    scene: dict
    beats: list[Beat] = field(default_factory=list)


def load_scenario(path: Path) -> Scenario:
    raw = yaml.safe_load(path.read_text())
    beats = [Beat(**b) for b in raw.pop("beats")]
    return Scenario(beats=beats, **raw)


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def build_campaign(
    scenario: Scenario, campaigns_dir: Path, rules_db_path: Path, *, slug: str, seed: int
) -> None:
    """Create the seeded scratch campaign with the full starting state.

    Everything the DM under test should find on open_campaign is created here,
    through the engine's own bootstrap + registry commands (audited, legal).
    """
    ctx = bootstrap_campaign(
        campaigns_dir,
        rules_db_path,
        slug=slug,
        name=scenario.name,
        skeleton={"premise": scenario.premise},
        starting_region=scenario.starting_region,
        seed=seed,
    )
    try:
        for member in scenario.party:
            result = registry.execute("create_character", ctx, **member)
            if not result.ok:
                raise RuntimeError(
                    f"create_character failed for {member.get('name')!r}: "
                    f"{result.refusal}"
                )
        # update_quest takes slug/title/status/notes, not the scenario's
        # name/description keys — map them rather than changing the handler.
        registry.execute(
            "update_quest",
            ctx,
            slug=scenario.quest.get("slug") or _slugify(scenario.quest["name"]),
            title=scenario.quest["name"],
            notes=scenario.quest.get("description", ""),
            status="active",
        )
        registry.execute("set_scene", ctx, **scenario.scene)
    finally:
        ctx.store.close()
