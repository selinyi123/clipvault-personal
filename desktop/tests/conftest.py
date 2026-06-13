from pathlib import Path

import pytest

from clipvault.store import db

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(scope="session")
def vectors_dir() -> Path:
    return REPO_ROOT / "contracts" / "vectors"


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    return Path(__file__).parent / "golden"


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.migrate(c)
    yield c
    c.close()
