from pathlib import Path

import pytest

from dm_engine.commands import registry
from dm_engine.commands.registry import CommandContext, RecordingRoller
from dm_engine.content.lookup import RulesDB
from dm_engine.content.seed import build_rules_db
from dm_engine.state.store import CampaignStore

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="session")
def rules_db(tmp_path_factory) -> Path:
    dest = tmp_path_factory.mktemp("rules") / "rules.sqlite"
    build_rules_db(
        structured_dir=REPO_ROOT / "data" / "srd" / "2014" / "structured",
        text_dir=REPO_ROOT / "data" / "srd" / "2014" / "text",
        dest=dest,
    )
    return dest


@pytest.fixture(scope="session")
def rules_path(rules_db):
    # reuse the session-scoped seeded rules.sqlite
    return rules_db


@pytest.fixture()
def ctx(tmp_path, rules_path):
    store = CampaignStore.create(
        tmp_path / "campaigns", slug="t", name="T", death_mode="narrative",
        rng_seed=99, skeleton={"premise": "test"},
    )
    context = CommandContext(
        store=store, roller=RecordingRoller(99), rules=RulesDB(rules_path)
    )
    yield context
    store.close()


@pytest.fixture()
def party(ctx):
    """Kira (PC fighter) + Brother Aldric (companion cleric), for command
    tests that need a populated party. Reused by later task tests."""
    registry.execute(
        "create_character", ctx, name="Kira", role="pc",
        class_slug="fighter", race_slug="human",
        abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        ac=16, proficiencies={"skills": ["athletics", "intimidation"], "saves": ["str", "con"]},
        attacks=[{"name": "longsword", "ranged": False, "range_ft": 5, "long_range_ft": None,
                  "damage": "1d8", "damage_type": "slashing", "ability": "str",
                  "proficient": True}],
    )
    registry.execute(
        "create_character", ctx, name="Brother Aldric", role="companion",
        class_slug="cleric", race_slug="hill-dwarf",
        abilities={"str": 14, "dex": 8, "con": 15, "int": 10, "wis": 15, "cha": 12},
        ac=18, proficiencies={"skills": ["medicine", "religion"], "saves": ["wis", "cha"]},
        attacks=[{"name": "mace", "ranged": False, "range_ft": 5, "long_range_ft": None,
                  "damage": "1d6", "damage_type": "bludgeoning", "ability": "str",
                  "proficient": True}],
        spells_known=["cure-wounds", "bless", "guiding-bolt", "sacred-flame",
                      "burning-hands", "hold-person"],
    )
    # Brother Aldric must be a level-3 cleric so he has 2nd-level slots
    # (needed for hold-person). Deterministic direct store writes — fixtures
    # aren't gameplay, so we bypass the XP-splitting award_xp path (which would
    # also level Kira). Level-3 cleric: HP 24 (d8 + CON 2, +2 levels of 7),
    # hit dice 3, slots {1: 4, 2: 2}.
    aldric = ctx.store.get_character("Brother Aldric")
    ctx.store.update_character(aldric["id"], level=3, xp=900, max_hp=24)
    ctx.store.update_resources(
        aldric["id"], hp=24, hit_dice_remaining=3,
        spell_slots={"1": {"max": 4, "remaining": 4}, "2": {"max": 2, "remaining": 2}},
    )
    ctx.store.conn.commit()
    return ctx
