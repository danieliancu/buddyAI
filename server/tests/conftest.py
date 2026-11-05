import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.mkdtemp(prefix="buddyai-test-")
os.environ.setdefault("BUDDYAI_DATA_DIR", _tmp)
os.environ.setdefault("BUDDYAI_MOCK_PROVIDERS", "true")
os.environ.setdefault("BUDDYAI_MDNS_ENABLED", "false")
# Test clients left open keep the server's background loops alive; a memory job loop waking up during a memory test takes
# its job. No test relies on the loop's timing: they call the job functions themselves.
os.environ.setdefault("BUDDYAI_MEMORY_JOB_INTERVAL_S", "3600")
# Never use the developer's real Stripe settings from server/.env (tests fake Stripe explicitly).
for _name in ("SECRET_KEY", "WEBHOOK_SECRET", "PRICE_CARE_GBP", "PRICE_CARE_EUR", "PRICE_WATCH_GBP", "PRICE_WATCH_EUR"):
    os.environ[f"BUDDYAI_STRIPE_{_name}"] = ""
# The local test switches (no Stripe Tax / no ToS URL) stay at their production defaults in tests.
os.environ["BUDDYAI_STRIPE_AUTOMATIC_TAX"] = "true"
os.environ["BUDDYAI_STRIPE_REQUIRE_TOS"] = "true"

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _migrated_database():
    """Tests that use the DB without starting the app still need the schema."""
    from app import care_activation, email_outbox
    from app.db.session import run_migrations

    # The same as the memory job loop above: a loop of a client left open must not act on another test's rows.
    # These tests call deliver_due and recover_sync themselves.
    care_activation.RECOVER_FIRST_DELAY_S = care_activation.RECOVER_INTERVAL_S = 3600
    email_outbox.INTERVAL_S = 3600
    run_migrations()
    yield
