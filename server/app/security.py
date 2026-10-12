"""Passwords, device tokens, pairing codes, operator session dependencies and roles.

Operator sessions (signed cookie, Starlette SessionMiddleware): session["admin"] = username and
session["admin_v"] = AdminUser.session_version. Every operator request re-reads the operator row: an unknown or
disabled operator, or a version mismatch (password/role/MFA change, "sign out everywhere"), gets 401 and the
operator part of the session is cleared. With BUDDYAI_ADMIN_MFA_REQUIRED=true an operator without two-factor
authentication can only use /api/auth/me, /api/auth/logout and /api/auth/mfa/* (everything else: 403).

Role matrix (enforced here and in the routers; the web UI only hides what a role cannot use):

    area                                            admin   support
    accounts: list, detail, create, status, audit    yes     yes
    devices: pair, assign, rename, revoke, settings  yes     yes
    conversation history: view, delete (audited)     yes     yes
    orders: view, change status                      yes     yes
    usage, diagnostics, incidents/issues: read       yes     yes
    finance: read                                    yes     yes
    pricing: read                                    yes     yes
    ola Care activation retry (no money changes)     yes     yes
    live events (websocket)                          yes     yes
    billing settings: read                           yes     yes
    firmware releases: list                          yes     yes
    system info (provider keys masked)               yes     yes
    incidents / issues: clear (DELETE)               yes     no
    pricing rule / currency changes                  yes     no
    billing settings: change                         yes     no
    complimentary grant/revoke, allowance changes    yes     no
    provider keys: change; provider tests (cost)     yes     no
    firmware: upload, OTA push                       yes     no
    global audit log (/api/audit)                    yes     no
    (operator accounts are managed with python -m app.cli; there is no web endpoint)
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

import bcrypt
from fastapi import Depends, HTTPException, Request, WebSocket, status
from sqlmodel import Session, select

from app.config import get_settings
from app.db.models import AdminUser
from app.db.session import get_session, session_scope

ROLES = ("admin", "support")
MFA_FIRST = "Set up two-factor authentication first."


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


def new_device_token() -> str:
    """32 random bytes, URL-safe. Only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_device_token(token: str) -> str:
    # Tokens are high-entropy random values, so a fast hash is sufficient (no brute-force risk).
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def is_valid_pairing_code(code: str | None) -> bool:
    return bool(code) and len(code) == 6 and code.isdigit()


def operator_login(request: Request, user: AdminUser) -> None:
    request.session["admin"] = user.username
    request.session["admin_v"] = user.session_version


def operator_logout(request: Request) -> None:
    # Only the operator signs out; a customer session in the same browser stays.
    request.session.pop("admin", None)
    request.session.pop("admin_v", None)


def _session_operator(session: dict, db: Session) -> AdminUser | None:
    name = session.get("admin")
    if not name:
        return None
    user = db.exec(select(AdminUser).where(AdminUser.username == name)).first()
    if user is None or user.disabled or session.get("admin_v") != user.session_version:
        return None
    return user


def mfa_missing(user: AdminUser) -> bool:
    return get_settings().admin_mfa_required and not user.mfa_enabled


def current_operator(request: Request, db: Session = Depends(get_session)) -> AdminUser:
    """The signed-in operator, without the two-factor requirement (/me, /logout, /mfa/*), or 401."""
    user = _session_operator(request.session, db)
    if user is None:
        operator_logout(request)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    request.state.operator = user
    return user


def require_admin(request: Request, user: AdminUser = Depends(current_operator)) -> str:
    """FastAPI dependency for every operator route: the operator's username, 401 or 403 (MFA required)."""
    if mfa_missing(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, MFA_FIRST)
    return user.username


def require_role(*roles: str):
    """Dependency factory: the operator must have one of `roles` (after require_admin's checks)."""

    def dependency(request: Request, _: str = Depends(require_admin)) -> str:
        user: AdminUser = request.state.operator
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "not allowed for your operator role")
        return user.username

    return dependency


require_admin_role = require_role("admin")


def websocket_admin(ws: WebSocket) -> str | None:
    """The operator's username for a websocket (same checks as require_admin), else None."""
    if "session" not in ws.scope:
        return None
    with session_scope() as db:
        user = _session_operator(ws.session, db)
        if user is None or mfa_missing(user):
            return None
        return user.username
