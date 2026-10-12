"""Server administration commands.

    python -m app.cli create-operator <username> [--role admin|support]   # asks for the password
    python -m app.cli reset-operator-password <username>
    python -m app.cli set-operator-role <username> <admin|support>
    python -m app.cli reset-operator-mfa <username>      # turns two-factor authentication off (lost phone)
    python -m app.cli disable-operator <username>
    python -m app.cli enable-operator <username>

Every change except create-operator signs the operator out everywhere (session_version) and is audited.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlmodel import Session

from app import accounts
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
    args = parser.parse_args()
    run_migrations()
    with session_scope() as db:
        try:
            if args.cmd == "create-operator":
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
