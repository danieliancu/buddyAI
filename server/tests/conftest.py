import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.mkdtemp(prefix="buddyai-test-")
os.environ.setdefault("BUDDYAI_DATA_DIR", _tmp)
os.environ.setdefault("BUDDYAI_MOCK_PROVIDERS", "true")
os.environ.setdefault("BUDDYAI_MDNS_ENABLED", "false")

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _migrated_database():
    """Tests that use the DB without starting the app still need the schema."""
    from app.db.session import run_migrations

    run_migrations()
    yield
