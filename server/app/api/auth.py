"""Admin auth: first-run setup, login/logout, current user. Session cookie (signed)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db.repositories import AdminRepo
from app.db.session import get_session
from app.ratelimit import LOGIN_PER_ACCOUNT, LOGIN_PER_IP, client_ip
from app.security import hash_password, require_admin, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)


@router.get("/status")
def status(request: Request, db: Session = Depends(get_session)) -> dict:
    return {"needs_setup": AdminRepo(db).count() == 0, "user": request.session.get("admin")}


@router.post("/setup")
def setup(body: Credentials, request: Request, db: Session = Depends(get_session)) -> dict:
    repo = AdminRepo(db)
    if repo.count() > 0:
        raise HTTPException(409, "already set up")
    repo.create(body.username, hash_password(body.password))
    request.session.pop("account_id", None)  # one role per browser session
    request.session["admin"] = body.username
    return {"user": body.username}


@router.post("/login")
def login(body: Credentials, request: Request, db: Session = Depends(get_session)) -> dict:
    ip = client_ip(request)
    key = f"op|{ip}|{body.username.lower()}"
    LOGIN_PER_IP.hit(ip)
    LOGIN_PER_ACCOUNT.hit(key)
    user = AdminRepo(db).get(body.username)
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "invalid credentials")
    LOGIN_PER_ACCOUNT.reset(key)
    request.session.pop("account_id", None)  # one role per browser session
    request.session["admin"] = user.username
    return {"user": user.username}


@router.post("/logout")
def logout(request: Request) -> dict:
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(user: str = Depends(require_admin)) -> dict:
    return {"user": user}
