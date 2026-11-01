"""Conversation RAG on real PostgreSQL + pgvector: search, indexing, locks and references (each scenario runs in
tests/pg/rag_scenarios.py, in its own process). Needs BUDDYAI_PG_TEST_URL on an image with pgvector
(docs/CONVERSATION_RAG.md, "Running the PostgreSQL tests")."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from app.config import SERVER_DIR

PG_URL = os.environ.get("BUDDYAI_PG_TEST_URL", "")
pytestmark = pytest.mark.skipif(not PG_URL, reason="BUDDYAI_PG_TEST_URL not set")

SCENARIOS = [
    "search_is_semantic_and_isolated",
    "vectors_of_another_model_are_ignored",
    "statement_timeout_is_local_to_the_search",
    "a_deletion_waits_for_a_running_store_and_removes_its_vectors",
    "a_switch_off_waits_for_a_running_store_and_removes_its_vectors",
    "embedded_during_a_deletion_or_switch_off_stores_nothing",
    "only_one_process_leases_a_watch",
    "references_follow_their_turns",
    "retention_waits_for_a_reply_being_stored_and_removes_it",
]


def _run(name: str) -> None:
    env = {**os.environ, "BUDDYAI_DATABASE_URL": PG_URL, "BUDDYAI_MOCK_PROVIDERS": "true",
           "BUDDYAI_CONVERSATION_RAG_ENABLED": "true", "BUDDYAI_CONVERSATION_RAG_INDEXING_ENABLED": "true",
           "BUDDYAI_CONVERSATION_RAG_ACCOUNTS": ""}
    r = subprocess.run([sys.executable, "-m", "tests.pg.rag_scenarios", name], cwd=SERVER_DIR, env=env,
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stdout[-2000:] + r.stderr[-3000:]


@pytest.fixture(scope="module", autouse=True)
def schema():
    if PG_URL:
        _run("setup")


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name):
    _run(name)
