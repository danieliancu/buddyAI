"""Server administration commands.

    python -m app.cli create-operator <username>      # asks for the password
    python -m app.cli reset-operator-password <username>
"""

from __future__ import annotations

import argparse
import getpass
import sys

from app.db.repositories import AdminRepo
from app.db.session import run_migrations, session_scope
from app.security import hash_password


def _password() -> str:
    pw = getpass.getpass("Password (min 12 characters): ")
    if len(pw) < 12:
        sys.exit("Password too short.")
    if getpass.getpass("Repeat password: ") != pw:
        sys.exit("Passwords do not match.")
    return pw


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create-operator").add_argument("username")
    sub.add_parser("reset-operator-password").add_argument("username")
    args = parser.parse_args()
    run_migrations()
    with session_scope() as db:
        repo = AdminRepo(db)
        user = repo.get(args.username)
        if args.cmd == "create-operator":
            if user:
                sys.exit(f"Operator {args.username!r} already exists.")
            repo.create(args.username, hash_password(_password()))
            print(f"Operator {args.username!r} created. Sign in at https://<app domain>/admin/login")
        else:
            if not user:
                sys.exit(f"No operator {args.username!r}.")
            user.password_hash = hash_password(_password())
            db.add(user)
            db.commit()
            print("Password updated.")


if __name__ == "__main__":
    main()
