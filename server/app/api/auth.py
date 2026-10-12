"""Operator auth: first-run setup, login (password + optional TOTP), logout, sign out everywhere, current
operator, two-factor enrolment. Session cookie (signed); revocable through AdminUser.session_version
(app/security.py). Passwords and codes are never logged or written to the audit log."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session

from app import accounts, mfa
from app.config import get_settings
from app.db.models import AdminUser
from app.db.repositories import AdminRepo
from app.db.session import get_session
from app.ratelimit import LOGIN_PER_ACCOUNT, LOGIN_PER_IP, client_ip
from app.security import (
    _session_operator,
    current_operator,
    hash_password,
    mfa_missing,
    operator_login,
    operator_logout,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

MFA_NEEDED = {"mfa_required": True, "message": "Enter the code from your authenticator app or a recovery code."}
MFA_WRONG = {"mfa_required": True, "message": "That code is not valid. Try again."}


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class LoginBody(Credentials):
    code: str | None = Field(None, max_length=32)  # TOTP (6 digits) or a recovery code


@router.get("/status")
def status(request: Request, db: Session = Depends(get_session)) -> dict:
    user = _session_operator(request.session, db)
    return {
        "needs_setup": AdminRepo(db).count() == 0,
        "web_setup_allowed": get_settings().allow_web_setup,
        "user": user.username if user else None,
    }


@router.post("/setup")
def setup(body: Credentials, request: Request, db: Session = Depends(get_session)) -> dict:
    repo = AdminRepo(db)
    if repo.count() > 0:
        raise HTTPException(409, "already set up")
    if not get_settings().allow_web_setup:
        raise HTTPException(403, "web setup disabled: run `python -m app.cli create-operator` on the server")
    user = repo.create(body.username, hash_password(body.password))  # the first operator is an admin
    operator_login(request, user)
    accounts.audit(db, user.username, "operator.login", detail="first-run setup")
    return {"user": user.username}


def _failed(db: Session, username: str, reason: str) -> None:
    # The username only - never the password or the code.
    accounts.audit(db, "system", "operator.login_failed", detail=f"username {username[:64]!r}: {reason}")


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_session)) -> dict:
    ip = client_ip(request)
    key = f"op|{ip}|{body.username.lower()}"
    LOGIN_PER_IP.hit(ip)
    LOGIN_PER_ACCOUNT.hit(key)  # every attempt counts until a successful sign-in resets it
    user = AdminRepo(db).get(body.username)
    if not user or not verify_password(body.password, user.password_hash):
        _failed(db, body.username, "wrong username or password")
        raise HTTPException(401, "invalid credentials")
    if user.disabled:
        _failed(db, body.username, "operator disabled")
        raise HTTPException(401, "invalid credentials")
    how = "password"
    if user.mfa_enabled:
        if not (body.code or "").strip():
            raise HTTPException(401, MFA_NEEDED)
        left = mfa.recovery_codes_left(user)
        if not mfa.verify_second_factor(user, body.code):
            _failed(db, body.username, "wrong two-factor code")
            raise HTTPException(401, MFA_WRONG)
        how = "recovery code" if mfa.recovery_codes_left(user) < left else "authenticator code"
        db.add(user)  # a used recovery code is removed
        db.commit()
        db.refresh(user)
    LOGIN_PER_ACCOUNT.reset(key)
    operator_login(request, user)
    accounts.audit(db, user.username, "operator.login", detail=how)
    return {"user": user.username}


@router.post("/logout")
def logout(request: Request) -> dict:
    # Only the operator signs out; a customer session in the same browser stays.
    operator_logout(request)
    return {"ok": True}


def _bump(db: Session, user: AdminUser) -> None:
    user.session_version += 1
    db.add(user)
    db.commit()
    db.refresh(user)


@router.post("/logout-all")
def logout_all(request: Request, user: AdminUser = Depends(current_operator), db: Session = Depends(get_session)) -> dict:
    """Sign this operator out everywhere (every browser, including this one)."""
    _bump(db, user)
    operator_logout(request)
    accounts.audit(db, user.username, "operator.logout_all")
    return {"ok": True}


def me_out(user: AdminUser) -> dict:
    return {
        "user": user.username,
        "username": user.username,
        "role": user.role,
        "mfa_enabled": user.mfa_enabled,
        "mfa_required": get_settings().admin_mfa_required,
        "mfa_setup_needed": mfa_missing(user),
        "recovery_codes_left": mfa.recovery_codes_left(user) if user.mfa_enabled else 0,
    }


@router.get("/me")
def me(user: AdminUser = Depends(current_operator)) -> dict:
    return me_out(user)


# --- two-factor authentication (TOTP) ------------------------------------------------------------


def _limit(request: Request, user: AdminUser) -> str:
    key = f"opmfa|{client_ip(request)}|{user.username.lower()}"
    LOGIN_PER_ACCOUNT.hit(key)
    return key


@router.post("/mfa/setup")
def mfa_setup(user: AdminUser = Depends(current_operator), db: Session = Depends(get_session)) -> dict:
    """A new (pending) secret: two-factor stays off until /mfa/enable confirms a code from the app."""
    if user.mfa_enabled:
        raise HTTPException(409, "two-factor authentication is already on")
    secret = mfa.new_secret()
    user.totp_secret_enc = mfa.encrypt_secret(secret)
    db.add(user)
    db.commit()
    return {"secret": secret, "otpauth_uri": mfa.otpauth_uri(secret, user.username)}


class CodeBody(BaseModel):
    code: str = Field(min_length=6, max_length=32)


@router.post("/mfa/enable")
def mfa_enable(body: CodeBody, request: Request, user: AdminUser = Depends(current_operator),
               db: Session = Depends(get_session)) -> dict:
    if user.mfa_enabled:
        raise HTTPException(409, "two-factor authentication is already on")
    if not user.totp_secret_enc:
        raise HTTPException(400, "start the setup first")
    key = _limit(request, user)
    if not mfa.verify_totp(user, body.code):
        raise HTTPException(400, "That code is not valid. Check the time on your phone and try again.")
    LOGIN_PER_ACCOUNT.reset(key)
    codes, hashes = mfa.new_recovery_codes()
    user.mfa_enabled = True
    user.recovery_codes = hashes
    _bump(db, user)  # other sessions end; this one continues
    operator_login(request, user)
    accounts.audit(db, user.username, "operator.mfa_enabled")
    return {"recovery_codes": codes, "me": me_out(user)}


class DisableBody(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=6, max_length=32)


@router.post("/mfa/disable")
def mfa_disable(body: DisableBody, request: Request, user: AdminUser = Depends(current_operator),
                db: Session = Depends(get_session)) -> dict:
    if not user.mfa_enabled:
        raise HTTPException(409, "two-factor authentication is not on")
    key = _limit(request, user)
    if not verify_password(body.password, user.password_hash) or not mfa.verify_second_factor(user, body.code):
        db.rollback()  # a recovery code is not spent on a failed attempt
        raise HTTPException(403, "wrong password or code")
    LOGIN_PER_ACCOUNT.reset(key)
    user.mfa_enabled = False
    user.totp_secret_enc = None
    user.recovery_codes = None
    _bump(db, user)
    operator_login(request, user)
    accounts.audit(db, user.username, "operator.mfa_disabled")
    return {"me": me_out(user)}
