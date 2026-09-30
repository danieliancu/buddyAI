"""Give an existing customer a complimentary ola Care pilot (no payment, no Stripe objects).

Safe to run more than once: an unexpired grant is left as it is (use --extend to add days).
It refuses to touch an account that pays for Care through Stripe, and checks the account really is
the one you mean (status and a linked watch by name) before changing anything.

    python tools/grant_pilot.py --account-id 2 --expect-device Marineasca --dry-run
    python tools/grant_pilot.py --account-id 2 --expect-device Marineasca
    python tools/grant_pilot.py --enable-enforcement      # turn allowance checks on (audited)

Every change is written to the audit log (actor "operator:cli").
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import select  # noqa: E402

from app import accounts, billing, entitlements, plan as plan_mod  # noqa: E402
from app.db.models import Account, Device  # noqa: E402
from app.db.session import run_migrations, session_scope  # noqa: E402

ACTOR = "operator:cli"


def mask(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:3]}***@{domain}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account-id", type=int)
    ap.add_argument("--expect-device", help="name of a watch that must be linked to this account")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--allowance-pence", type=int, default=None, help="default: the plan allowance")
    ap.add_argument("--note", default="Complimentary ola Care pilot - no payment")
    ap.add_argument("--extend", action="store_true", help="add --days to an unexpired grant")
    ap.add_argument("--enable-enforcement", action="store_true", help="check subscriptions/allowances (audited)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    run_migrations()
    with session_scope() as db:
        if args.enable_enforcement:
            p = plan_mod.get_plan(db)
            print(f"enforcement: currently {'on' if p.enforce else 'off'}")
            if not p.enforce and not args.dry_run:
                plan_mod.update(db, {"enforce": True}, ACTOR)
                accounts.audit(db, ACTOR, "billing.settings", detail="{'enforce': 'False -> True'}")
                print("enforcement: turned ON")
        if args.account_id is None:
            return 0

        acc = db.get(Account, args.account_id)
        if acc is None:
            print(f"ERROR: account {args.account_id} not found")
            return 2
        devices = db.exec(select(Device).where(Device.account_id == acc.id)).all()
        print(f"account {acc.id}: {mask(acc.email)}, status={acc.status}, internal={acc.internal}, "
              f"watches={[d.name for d in devices]}")
        if acc.status != "active":
            print("ERROR: account is not active")
            return 2
        if acc.internal:
            print("ERROR: internal account (already unlimited)")
            return 2
        if args.expect_device and args.expect_device not in [d.name for d in devices]:
            print(f"ERROR: no watch named {args.expect_device!r} is linked to this account")
            return 2

        current = billing.active_subscription(db, acc.id)
        if current is not None:
            print(f"current plan: source={current.source}, status={current.status}, "
                  f"entitled={billing.is_entitled(current)}, until={current.current_period_end}")
        if current is not None and current.source == "stripe" and billing.is_entitled(current):
            print("Account already has a Stripe subscription: left unchanged.")
            return 0
        if args.dry_run:
            print(f"dry run: would grant {args.days} days (allowance "
                  f"{'plan default' if args.allowance_pence is None else str(args.allowance_pence) + 'p'})")
            return 0
        sub, created = billing.grant_complimentary(
            db, acc, args.days, ACTOR, args.note, args.allowance_pence, args.extend
        )
        print(f"{'granted' if created else 'already granted (unchanged)'}: complimentary until "
              f"{sub.current_period_end:%Y-%m-%d %H:%M} UTC, subscription id {sub.id}")
        account_id = acc.id

    decision = asyncio.run(entitlements.check(account_id))
    with session_scope() as db:
        a = entitlements.allowance(db, db.get(Account, account_id))
        enforced = plan_mod.get_plan(db).enforced
    print(f"entitlement check: allowed={decision.allowed} code={decision.code} (enforcement {'on' if enforced else 'off'})")
    print(f"allowance: {a.used_pct}% used, period {a.period.start:%Y-%m-%d} -> {a.period.end:%Y-%m-%d} ({a.period.kind})")
    return 0 if decision.allowed else 1


if __name__ == "__main__":
    sys.exit(main())
