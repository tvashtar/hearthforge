from pathlib import Path

import pytest

from dm_engine.commands.registry import CommandContext, RecordingRoller
from dm_engine.content.lookup import RulesDB
from dm_engine.state.store import CampaignStore

REPO_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(scope="session")
def rules_path(rules_db):
    # reuse the session-scoped seeded rules.sqlite from tests/conftest.py
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
