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
def ctx_hardcore(tmp_path, rules_path):
    """Hardcore-mode twin of `ctx`: same seed/skeleton, death_mode='hardcore'
    so death-mode-mapping tests (narrative 'defeated' vs hardcore 'dead')
    can run the identical script against both."""
    store = CampaignStore.create(
        tmp_path / "campaigns", slug="t-hc", name="T-HC", death_mode="hardcore",
        rng_seed=99, skeleton={"premise": "test"},
    )
    context = CommandContext(
        store=store, roller=RecordingRoller(99), rules=RulesDB(rules_path)
    )
    yield context
    store.close()


@pytest.fixture()
def party_hardcore(ctx_hardcore):
    """Hardcore-mode twin of `party`: identical Kira + Brother Aldric build."""
    registry.execute(
        "create_character", ctx_hardcore, name="Kira", role="pc",
        class_slug="fighter", race_slug="human",
        abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        ac=16, proficiencies={"skills": ["athletics", "intimidation"]},
        attacks=[{"weapon": "longsword", "name": "longsword"}],
    )
    registry.execute(
        "create_character", ctx_hardcore, name="Brother Aldric", role="companion",
        class_slug="cleric", race_slug="hill-dwarf", level=3,
        abilities={"str": 14, "dex": 8, "con": 15, "int": 10, "wis": 15, "cha": 12},
        ac=18, proficiencies={"skills": ["medicine", "religion"]},
        attacks=[{"weapon": "mace", "name": "mace"}],
        spells_known=["cure-wounds", "bless", "guiding-bolt", "sacred-flame",
                      "hold-person"],
    )
    aldric = ctx_hardcore.store.get_character("Brother Aldric")
    ctx_hardcore.store.update_character(
        aldric["id"], spells_known=aldric["spells_known"] + ["burning-hands"]
    )
    ctx_hardcore.store.conn.commit()
    return ctx_hardcore


@pytest.fixture()
def party(ctx):
    """Kira (PC fighter) + Brother Aldric (companion cleric), for command
    tests that need a populated party. Reused by later task tests."""
    registry.execute(
        "create_character", ctx, name="Kira", role="pc",
        class_slug="fighter", race_slug="human",
        abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        ac=16, proficiencies={"skills": ["athletics", "intimidation"]},
        attacks=[{"weapon": "longsword", "name": "longsword"}],
    )
    registry.execute(
        "create_character", ctx, name="Brother Aldric", role="companion",
        class_slug="cleric", race_slug="hill-dwarf", level=3,
        abilities={"str": 14, "dex": 8, "con": 15, "int": 10, "wis": 15, "cha": 12},
        ac=18, proficiencies={"skills": ["medicine", "religion"]},
        attacks=[{"weapon": "mace", "name": "mace"}],
        # hold-person is on the cleric list (2nd level -> needs his 2nd-level
        # slot); burning-hands is not, so it's added below via a direct store
        # write, bypassing create_character's class-castability check.
        spells_known=["cure-wounds", "bless", "guiding-bolt", "sacred-flame",
                      "hold-person"],
    )
    # Some spell-mechanics tests exercise the AoE/save-halves resolver path
    # via burning-hands, which is wizard/sorcerer-only in the SRD. Aldric
    # doesn't actually know it in-fiction; this is a direct store write for
    # test convenience (same pattern test_fire_bolt uses for fire-bolt),
    # not something create_character would ever accept.
    aldric = ctx.store.get_character("Brother Aldric")
    ctx.store.update_character(
        aldric["id"], spells_known=aldric["spells_known"] + ["burning-hands"]
    )
    ctx.store.conn.commit()
    return ctx
