"""Usage admission across independent processes (each its own interpreter, engine and pool).

PostgreSQL cases need BUDDYAI_PG_TEST_URL (a throw-away database: its schema is dropped and recreated), e.g.
  docker run --rm -d --name buddyai-pg-test -e POSTGRES_USER=buddyai -e POSTGRES_PASSWORD=test \\
      -e POSTGRES_DB=buddyai_test -p 55432:5432 postgres:16-alpine
  BUDDYAI_PG_TEST_URL=postgresql+psycopg://buddyai:test@localhost:55432/buddyai_test pytest tests/pg
The SQLite case runs everywhere.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import queue
import tempfile
import time
from pathlib import Path

import pytest

from tests.pg import workers

PG_URL = os.environ.get("BUDDYAI_PG_TEST_URL", "")
pg = pytest.mark.skipif(not PG_URL, reason="BUDDYAI_PG_TEST_URL not set")
CTX = mp.get_context("spawn")


class Cluster:
    """Spawns worker processes against one database."""

    def __init__(self, url: str, **env: str) -> None:
        self.env = {"BUDDYAI_DATABASE_URL": url, "BUDDYAI_USAGE_LEASE_S": "3", "BUDDYAI_USAGE_LOCK_TIMEOUT_MS": "10000",
                    **env}
        self.out = CTX.Queue()

    def start(self, name: str, *args, barrier=None) -> mp.Process:
        p = CTX.Process(target=workers.entry, args=(name, self.env, args, self.out, barrier), daemon=True)
        p.start()
        return p

    def results(self, n: int, timeout: float = 90) -> list:
        got = []
        deadline = time.monotonic() + timeout
        while len(got) < n:
            try:
                name, status, value = self.out.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                raise AssertionError(f"only {len(got)}/{n} worker results") from None
            assert status == "ok", value
            got.append(value)
        return got

    def run(self, name: str, *args, timeout: float = 90):
        p = self.start(name, *args)
        (value,) = self.results(1, timeout)
        p.join(10)
        return value

    def race(self, name: str, arg_list: list[tuple], timeout: float = 90) -> list:
        barrier = CTX.Barrier(len(arg_list))
        procs = [self.start(name, *a, barrier=barrier) for a in arg_list]
        values = self.results(len(arg_list), timeout)
        for p in procs:
            p.join(10)
        return values


@pytest.fixture(scope="module")
def pgc():
    if not PG_URL:
        pytest.skip("BUDDYAI_PG_TEST_URL not set")
    c = Cluster(PG_URL)
    assert c.run("reset_schema", timeout=180) == "ok"
    return c


# --- the last budget, shared by watches on different processes --------------------------------------------


@pg
def test_last_budget_race_admits_exactly_one(pgc):
    acc = pgc.run("make_account", 2.46)  # £2.50 allowance: room for one 3p reservation
    res = pgc.race("admit", [(acc, None, f"watch-{i}") for i in range(6)])
    assert len({r["pid"] for r in res}) == 6  # six independent processes
    assert sum(r["allowed"] for r in res) == 1
    assert {r["code"] for r in res if not r["allowed"]} == {"busy_concurrent"}


@pg
def test_budget_room_for_several_is_shared_not_multiplied(pgc):
    acc = pgc.run("make_account", 2.38)  # room for four 3p reservations (12p)
    res = pgc.race("admit", [(acc, None, f"w{i}") for i in range(8)])
    assert sum(r["allowed"] for r in res) == 4


@pg
def test_accounts_do_not_block_each_other(pgc):
    a1, a2 = pgc.run("make_account", 2.46), pgc.run("make_account", 2.46)
    res = pgc.race("admit", [(a1, None, "x1"), (a2, None, "x2")])
    assert all(r["allowed"] for r in res)


@pg
def test_same_request_from_several_processes_is_one_operation(pgc):
    acc = pgc.run("make_account")
    res = pgc.race("admit", [(acc, "one-request", f"p{i}") for i in range(5)])
    assert sum(r["allowed"] for r in res) == 1
    assert {r["code"] for r in res if not r["allowed"]} <= {"duplicate", "request_conflict"}
    assert pgc.run("count_ops", acc) == 1


@pg
def test_admission_waits_for_the_account_lock(pgc):
    """Proof that the decision is serialised by the database: while one process holds the account row
    inside admit, another one's admit waits on a row lock (pg_stat_activity) and decides after it."""
    from sqlalchemy import create_engine, text

    acc = pgc.run("make_account", 2.46)
    flag = Path(tempfile.mkdtemp()) / "locked"
    holder = pgc.start("admit_holding_lock", acc, 2.0, str(flag))
    waiter = pgc.start("admit_after_flag", acc, str(flag))
    engine = create_engine(PG_URL.replace("postgresql+psycopg", "postgresql+psycopg"))
    seen_wait = False
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not seen_wait:
        with engine.connect() as c:
            rows = c.execute(text("SELECT wait_event_type FROM pg_stat_activity WHERE application_name LIKE 'buddyai:%' "
                                  "AND wait_event_type = 'Lock'")).all()
        seen_wait = bool(rows)
        time.sleep(0.05)
    a, b = pgc.results(2)
    holder.join(10)
    waiter.join(10)
    engine.dispose()
    first, second = (a, b) if "t_start" not in a else (b, a)
    assert seen_wait, "the second admission never waited on a lock"
    assert first["allowed"] and not second["allowed"] and second["code"] == "busy_concurrent"
    assert second["t_done"] >= first["t_done"] - 0.05  # decided only after the holder committed


# --- crashes, leases, recovery -----------------------------------------------------------------------------


@pg
def test_killed_process_operation_is_recovered_by_another(pgc):
    acc = pgc.run("make_account")
    flag = Path(tempfile.mkdtemp()) / "running"
    doomed = pgc.start("run_then_hang", acc, str(flag))
    deadline = time.monotonic() + 60
    while not (flag.exists() and flag.read_text()) and time.monotonic() < deadline:
        time.sleep(0.05)
    op_id = int(flag.read_text().split(":")[0])
    doomed.kill()  # like kill -9: no cleanup, no settle
    doomed.join(10)
    assert pgc.run("op_state", op_id)["state"] == "running"
    time.sleep(3.5)  # the 3 s lease runs out
    assert pgc.run("recover") >= 1  # another process (or a restarted server) recovers it
    st = pgc.run("op_state", op_id)
    assert st["state"] == "expired" and st["billable"] is False and st["certainty"] == "uncertain"
    assert st["records"] == 1 and st["billable_records"] == 0  # the cost is kept, not billed
    assert pgc.run("recover") == 0  # never twice


@pg
def test_recovery_and_settle_race_end_in_one_consistent_state(pgc):
    acc = pgc.run("make_account")
    for _ in range(6):
        a = pgc.run("admit", acc, None, "racer")
        pgc.run("expire", a["op_id"])
        barrier = CTX.Barrier(2)
        p1 = pgc.start("settle", a["op_id"], a["token"], 1.0, barrier=barrier)
        p2 = pgc.start("recover_race", barrier=barrier)
        pgc.results(2)
        p1.join(10)
        p2.join(10)
        st = pgc.run("op_state", a["op_id"])
        assert st["state"] in ("settled", "expired")
        assert st["records"] == 1  # the cost exactly once either way
        if st["state"] == "settled":
            assert st["billable"] is True and st["billable_records"] == 1
        else:
            assert st["billable"] is False and st["billable_records"] == 0


@pg
def test_concurrent_cost_reports_are_written_once(pgc):
    acc = pgc.run("make_account")
    a = pgc.run("admit", acc, None, "dup")
    res = pgc.race("record", [(a["op_id"], 5) for _ in range(4)])
    assert sum(res) == 5  # five distinct calls, each recorded by exactly one process
    assert pgc.run("op_state", a["op_id"])["records"] == 5


@pg
def test_refund_and_admission_concurrently_match_a_serial_order(pgc):
    acc = pgc.run("make_account", 2.50)  # used up; a paid top-up gives room
    pgc.run("add_paid_topup", acc)
    barrier = CTX.Barrier(2)
    p1 = pgc.start("refund_topup", acc, barrier=barrier)
    p2 = pgc.start("admit", acc, None, "w-refund", barrier=barrier)
    values = pgc.results(2)
    p1.join(10)
    p2.join(10)
    adm = next(v for v in values if isinstance(v, dict))
    # either admitted before the refund (allowed) or after it (limit) - never a half-applied state
    assert adm["allowed"] or adm["code"] == "limit_reached"
    after = pgc.run("admit", acc, None, "w-after")
    assert after["code"] in ("limit_reached", "busy_concurrent")


@pg
def test_database_unreachable_refuses():
    bad = Cluster("postgresql+psycopg://buddyai:wrong@localhost:1/none", BUDDYAI_USAGE_ADMIT_RETRIES="1")
    res = bad.run("admit_cold", 1, timeout=120)
    assert not res["allowed"] and res["code"] == "service_unavailable"


# --- SQLite: several processes on one file (development) ------------------------------------------------------


def test_sqlite_processes_share_the_budget():
    path = Path(tempfile.mkdtemp()) / "mp.db"
    c = Cluster(f"sqlite:///{path.as_posix()}", BUDDYAI_DATA_DIR=str(path.parent))
    assert c.run("reset_schema", timeout=180) == "ok"
    acc = c.run("make_account", 2.46)
    res = c.race("admit", [(acc, None, f"s{i}") for i in range(4)])
    assert len({r["pid"] for r in res}) == 4
    assert sum(r["allowed"] for r in res) == 1
