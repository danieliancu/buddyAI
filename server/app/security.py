"""Passwords, device tokens, pairing codes, admin session dependency."""

from __future__ import annotations

import hashlib
import hmac
import secrets

import bcrypt
from fastapi import HTTPException, Request, WebSocket, status


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


def require_admin(request: Request) -> str:
    """FastAPI dependency: returns the admin username or 401."""
    user = request.session.get("admin")
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    return user


def websocket_admin(ws: WebSocket) -> str | None:
    return ws.session.get("admin") if "session" in ws.scope else None
