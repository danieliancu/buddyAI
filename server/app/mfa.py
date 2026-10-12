"""Operator two-factor authentication (TOTP, RFC 6238) and recovery codes.

The TOTP secret is stored encrypted (Fernet). The encryption key is derived with HKDF-SHA256 from the server's
existing secret key (data/secret.key, also the cookie-signing key), info=b"buddyai-totp": a copy of the
database alone does not reveal the secrets. Rotating data/secret.key makes stored secrets unreadable, so every
operator then has to enrol again (`python -m app.cli reset-operator-mfa <name>`).

Recovery codes: 10 single-use codes, shown once; only bcrypt hashes are stored (JSON list). A used code is
removed. Codes are never logged.
"""

from __future__ import annotations

import base64
import json
import secrets
from functools import lru_cache

import pyotp
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import get_settings
from app.db.models import AdminUser
from app.security import hash_password, verify_password

ISSUER = "olá operator"
RECOVERY_CODES = 10
_B32 = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: easy to copy by hand


@lru_cache
def _fernet_for(secret: str) -> Fernet:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"buddyai-totp").derive(secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def _fernet() -> Fernet:
    return _fernet_for(get_settings().secret_key)


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("ascii")).decode("ascii")


def decrypt_secret(token: str) -> str | None:
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("ascii")
    except (InvalidToken, ValueError):
        return None


def new_secret() -> str:
    return pyotp.random_base32()


def otpauth_uri(secret: str, username: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=ISSUER)


def _normalise(code: str) -> str:
    return "".join(ch for ch in (code or "") if ch.isalnum()).upper()


def verify_totp(user: AdminUser, code: str) -> bool:
    if not user.totp_secret_enc:
        return False
    secret = decrypt_secret(user.totp_secret_enc)
    code = _normalise(code)
    if not secret or len(code) != 6 or not code.isdigit():
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def new_recovery_codes() -> tuple[list[str], str]:
    """(plaintext codes to show once, JSON of their bcrypt hashes to store)."""
    codes = []
    for _ in range(RECOVERY_CODES):
        raw = "".join(secrets.choice(_B32) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes, json.dumps([hash_password(_normalise(c)) for c in codes])


def recovery_codes_left(user: AdminUser) -> int:
    try:
        return len(json.loads(user.recovery_codes or "[]"))
    except ValueError:
        return 0


def use_recovery_code(user: AdminUser, code: str) -> bool:
    """True and the code removed from `user` (caller commits) if it is an unused recovery code."""
    code = _normalise(code)
    if len(code) != 10:
        return False
    try:
        hashes_ = json.loads(user.recovery_codes or "[]")
    except ValueError:
        return False
    for i, h in enumerate(hashes_):
        if verify_password(code, h):
            user.recovery_codes = json.dumps(hashes_[:i] + hashes_[i + 1:])
            return True
    return False


def verify_second_factor(user: AdminUser, code: str) -> bool:
    """A TOTP code or an unused recovery code (consumed; the caller commits)."""
    return verify_totp(user, code) or use_recovery_code(user, code)
