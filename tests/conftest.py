from pathlib import Path

import pytest

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
