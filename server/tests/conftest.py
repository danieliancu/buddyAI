import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.mkdtemp(prefix="buddyai-test-")
os.environ.setdefault("BUDDYAI_DATA_DIR", _tmp)
os.environ.setdefault("BUDDYAI_MOCK_PROVIDERS", "true")
os.environ.setdefault("BUDDYAI_MDNS_ENABLED", "false")
# Never use the developer's real Stripe settings from server/.env (tests fake Stripe explicitly).
for _name in ("SECRET_KEY", "WEBHOOK_SECRET", "PRICE_CARE_GBP", "PRICE_CARE_EUR", "PRICE_WATCH_GBP", "PRICE_WATCH_EUR"):
    os.environ[f"BUDDYAI_STRIPE_{_name}"] = ""

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _migrated_database():
    """Tests that use the DB without starting the app still need the schema."""
    from app.db.session import run_migrations

    run_migrations()
    yield
