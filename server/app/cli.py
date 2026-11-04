"""Server administration commands.

    python -m app.cli create-operator <username> [--role admin|support|principal]   # asks for the password
    python -m app.cli reset-operator-password <username>
    python -m app.cli set-operator-role <username> <admin|support|principal>
    python -m app.cli reset-operator-mfa <username>      # turns two-factor authentication off (lost phone)
    python -m app.cli disable-operator <username>
    python -m app.cli enable-operator <username>
    python -m app.cli reconcile-deletions <ledger file or folder>... [--dry-run]   # after restoring a backup
    python -m app.cli reconcile-deletions --no-ledger   # no ledger exists: records that decision in the audit log
    python -m app.cli price-change-notice --old-pence 799 --new-pence 899 --effective 2027-03-01 [--send]
        # lists how many customers would be told; with --send, queues the emails for the running server to send.
        # Changes nothing in Stripe: the new price is a separate step, in Stripe.

Every change except create-operator signs the operator out everywhere (session_version) and is audited.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from datetime import datetime
from pathlib import Path

from sqlmodel import Session

from app import accounts, deletion_log, email_outbox
from app.db.models import AdminUser
from app.db.repositories import AdminRepo
from app.db.session import run_migrations, session_scope
from app.security import ROLES, hash_password


class OperatorError(Exception):
    pass


def _password() -> str:
    pw = getpass.getpass("Password (min 12 characters): ")
    if len(pw) < 12:
        sys.exit("Password too short.")
    if getpass.getpass("Repeat password: ") != pw:
        sys.exit("Passwords do not match.")
    return pw


def _get(db: Session, username: str) -> AdminUser:
    user = AdminRepo(db).get(username)
    if not user:
        raise OperatorError(f"No operator {username!r}.")
    return user


def _save(db: Session, user: AdminUser, action: str, detail: str = "") -> None:
    user.session_version += 1  # sign out everywhere
    db.add(user)
    db.commit()
    db.refresh(user)
    accounts.audit(db, "system", action, detail=f"operator {user.username!r}" + (f": {detail}" if detail else ""))


def create_operator(db: Session, username: str, password: str, role: str = "admin") -> AdminUser:
    if role not in ROLES:
        raise OperatorError(f"Unknown role {role!r} (use one of: {', '.join(ROLES)}).")
    repo = AdminRepo(db)
    if repo.get(username):
        raise OperatorError(f"Operator {username!r} already exists.")
    user = repo.create(username, hash_password(password))
    user.role = role
    db.add(user)
    db.commit()
    db.refresh(user)
    accounts.audit(db, "system", "operator.create", detail=f"operator {username!r}, role {role}")
    return user


def set_password(db: Session, username: str, password: str) -> None:
    user = _get(db, username)
    user.password_hash = hash_password(password)
    _save(db, user, "operator.password_reset")


def set_role(db: Session, username: str, role: str) -> None:
    if role not in ROLES:
        raise OperatorError(f"Unknown role {role!r} (use one of: {', '.join(ROLES)}).")
    user = _get(db, username)
    old = user.role
    user.role = role
    _save(db, user, "operator.role_change", f"{old} -> {role}")


def reset_mfa(db: Session, username: str) -> None:
    user = _get(db, username)
    user.mfa_enabled = False
    user.totp_secret_enc = None
    user.recovery_codes = None
    _save(db, user, "operator.mfa_disabled", "reset from the server console")


def set_disabled(db: Session, username: str, disabled: bool) -> None:
    user = _get(db, username)
    user.disabled = disabled
    _save(db, user, "operator.disabled" if disabled else "operator.enabled")


def _reconcile_deletions(db: Session, ledger: list[Path], dry_run: bool, no_ledger: bool) -> int:
    if no_ledger == bool(ledger):
        print("Give the decrypted ledger files or folders, or --no-ledger if none exists.")
        return 2
    result = deletion_log.reconcile(db, deletion_log.load_ledger(ledger), dry_run=dry_run)
    print(json.dumps(result, indent=2))
    if dry_run:
        return 0
    if result["failed"]:
        print("Some deletions could not be applied: the server stays blocked. Fix them and run this again.")
        return 1
    deletion_log.clear_required(db)
    accounts.audit(db, "system", "deletions.reconciled",
                   detail="no ledger available" if no_ledger else f"{result['applied']} applied")
    print("Done. The server can start on this database.")
    return 0


def _price_change_notice(db: Session, old_pence: int, new_pence: int, effective: str, send: bool) -> None:
    try:
        day = datetime.strptime(effective, "%Y-%m-%d")
    except ValueError:
        raise OperatorError("--effective must be a date like 2027-03-01") from None
    n = len(email_outbox.price_notice_recipients(db))
    if not send:
        print(f"Dry run: {n} customers would be told {old_pence}p -> {new_pence}p from {effective}. Add --send to queue the emails.")
        return
    try:
        queued = email_outbox.queue_price_notice(db, old_pence, new_pence, day, "cli")
    except ValueError as exc:
        raise OperatorError(str(exc)) from None
    print(f"{queued} emails queued ({n} customers). The running server sends them, up to 50 a minute. Stripe was not changed.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-operator")
    p.add_argument("username")
    p.add_argument("--role", choices=ROLES, default="admin")
    sub.add_parser("reset-operator-password").add_argument("username")
    p = sub.add_parser("set-operator-role")
    p.add_argument("username")
    p.add_argument("role", choices=ROLES)
    for name in ("reset-operator-mfa", "disable-operator", "enable-operator"):
        sub.add_parser(name).add_argument("username")
    p = sub.add_parser("reconcile-deletions")
    p.add_argument("ledger", nargs="*", type=Path)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-ledger", action="store_true")
    p = sub.add_parser("price-change-notice")
    p.add_argument("--old-pence", type=int, required=True)
    p.add_argument("--new-pence", type=int, required=True)
    p.add_argument("--effective", required=True, help="YYYY-MM-DD, at least 30 days from now")
    p.add_argument("--send", action="store_true", help="queue the emails (without it: only count the customers)")
    args = parser.parse_args()
    run_migrations()
    with session_scope() as db:
        try:
            if args.cmd == "reconcile-deletions":
                sys.exit(_reconcile_deletions(db, args.ledger, args.dry_run, args.no_ledger))
            if args.cmd == "price-change-notice":
                _price_change_notice(db, args.old_pence, args.new_pence, args.effective, args.send)
            elif args.cmd == "create-operator":
                if AdminRepo(db).get(args.username):
                    raise OperatorError(f"Operator {args.username!r} already exists.")
                create_operator(db, args.username, _password(), args.role)
                print(f"Operator {args.username!r} ({args.role}) created. Sign in at https://<app domain>/admin/login")
            elif args.cmd == "reset-operator-password":
                _get(db, args.username)
                set_password(db, args.username, _password())
                print("Password updated. The operator was signed out everywhere.")
            elif args.cmd == "set-operator-role":
                set_role(db, args.username, args.role)
                print(f"Role set to {args.role}. The operator was signed out everywhere.")
            elif args.cmd == "reset-operator-mfa":
                reset_mfa(db, args.username)
                print("Two-factor authentication turned off. The operator was signed out everywhere.")
            else:
                set_disabled(db, args.username, args.cmd == "disable-operator")
                print("Operator disabled and signed out everywhere." if args.cmd == "disable-operator"
                      else "Operator enabled.")
        except OperatorError as exc:
            sys.exit(str(exc))


if __name__ == "__main__":
    main()
