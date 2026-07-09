from pathlib import Path

import pytest

from dm_engine.content.seed import build_rules_db

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
