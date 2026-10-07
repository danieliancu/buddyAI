"""Customer API (/api/me/...): each account sees and changes only its own watches and data.

Every device route goes through `owned_device`, which returns 404 for a watch the account does
not own (never 403, so device ids of other customers cannot be probed).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app import accounts, care_activation, email
from app.api.common import (
    ItemBody,
    ItemDoneBody,
    ItemPinBody,
    PersonaBody,
    VoiceSampleBody,
    conversations_out,
    device_out,
    hub_of,
    options,
    patch_settings,
    read_settings,
    voice_sample,
)
from app.db.models import Account, Device, Order, Persona
from app.db.repositories import (
    ConversationRepo,
    DeviceRepo,
    ItemLimitError,
    ItemRepo,
    ItemTextError,
    ItemTimeError,
    PersonaRepo,
)
from app.db.session import get_session, session_scope
from app.gateway.hub import PairingError
from app.items import web_view
from app.ratelimit import LOGIN_PER_ACCOUNT, LOGIN_PER_IP, RESET_PER_EMAIL, SIGNUP_PER_IP, client_ip
from app.security import is_valid_pairing_code

router = APIRouter(prefix="/api/me", tags=["customer"])
log = logging.getLogger(__name__)


# --- session ----------------------------------------------------------------------------------


def _login(request: Request, acc: Account) -> None:
    request.session["account_id"] = acc.id
    request.session["account_v"] = acc.session_version


def current_account(request: Request, db: Session = Depends(get_session)) -> Account:
    """Dependency: the logged-in, active customer account, or 401."""
    acc_id = request.session.get("account_id")
    acc = db.get(Account, acc_id) if acc_id else None
    if acc is None or acc.status != "active" or request.session.get("account_v") != acc.session_version:
        request.session.pop("account_id", None)
        raise HTTPException(401, "not signed in")
    return acc


def verified_account(acc: Account = Depends(current_account)) -> Account:
    if acc.email_verified_at is None:
        raise HTTPException(403, "please confirm your email address first")
    return acc


def owned_device(device_id: str, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> Device:
    dev = DeviceRepo(db).owned(device_id, acc.id)
    if dev is None:
        raise HTTPException(404, "watch not found")
    return dev


def _err(exc: accounts.AccountError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


# --- sign-up / sign-in / email ------------------------------------------------------------------


class SignupBody(BaseModel):
    email: str
    password: str
    name: str = Field("", max_length=120)
    country: str | None = Field(None, max_length=2)


@router.post("/signup")
async def signup(body: SignupBody, request: Request, db: Session = Depends(get_session)) -> dict:
    SIGNUP_PER_IP.hit(client_ip(request))
    try:
        acc = accounts.create(db, body.email, body.password, body.name, body.country)
    except accounts.AccountError as exc:
        raise _err(exc) from exc
    token = accounts.issue_token(db, acc, "verify_email")
    await email.send(email.verify_email(acc.email, accounts.link("/verify-email", token)))
    _login(request, acc)
    return accounts.public(acc)


class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/login")
def login(body: LoginBody, request: Request, db: Session = Depends(get_session)) -> dict:
    ip = client_ip(request)
    key = f"{ip}|{body.email.strip().lower()}"
    LOGIN_PER_IP.hit(ip)
    LOGIN_PER_ACCOUNT.hit(key)
    try:
        acc = accounts.authenticate(db, body.email, body.password)
    except accounts.AccountError as exc:
        raise _err(exc) from exc
    LOGIN_PER_ACCOUNT.reset(key)
    _login(request, acc)
    return accounts.public(acc)


@router.post("/logout")
def logout(request: Request) -> dict:
    request.session.pop("account_id", None)
    request.session.pop("account_v", None)
    return {"ok": True}


class TokenBody(BaseModel):
    token: str


@router.post("/verify-email")
def verify_email(body: TokenBody, db: Session = Depends(get_session)) -> dict:
    try:
        acc = accounts.consume_token(db, body.token, "verify_email")
    except accounts.AccountError as exc:
        raise _err(exc) from exc
    accounts.mark_verified(db, acc)
    return {"ok": True}


@router.post("/verify-email/resend")
async def resend_verification(request: Request, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    RESET_PER_EMAIL.hit(f"verify|{acc.email}")
    if acc.email_verified_at is None:
        token = accounts.issue_token(db, acc, "verify_email")
        await email.send(email.verify_email(acc.email, accounts.link("/verify-email", token)))
    return {"ok": True}


class ForgotBody(BaseModel):
    email: str


@router.post("/password/forgot")
async def forgot_password(body: ForgotBody, request: Request, db: Session = Depends(get_session)) -> dict:
    """Always 200: the response never reveals whether an account exists."""
    SIGNUP_PER_IP.hit(f"forgot|{client_ip(request)}")
    try:
        addr = accounts.normalize_email(body.email)
    except accounts.AccountError:
        return {"ok": True}
    acc = accounts.by_email(db, addr)
    if acc and acc.status == "active":
        with contextlib.suppress(HTTPException):  # silently cap emails per address
            RESET_PER_EMAIL.hit(f"reset|{addr}")
            token = accounts.issue_token(db, acc, "reset_password")
            await email.send(email.reset_password(acc.email, accounts.link("/reset-password", token)))
    return {"ok": True}


class ResetBody(BaseModel):
    token: str
    password: str


@router.post("/password/reset")
def reset_password(body: ResetBody, request: Request, db: Session = Depends(get_session)) -> dict:
    """Also used to set the first password of an account created at checkout."""
    try:
        accounts.check_password(body.password)
        acc = accounts.consume_token(db, body.token, ("reset_password", "set_password"))
        accounts.set_password(db, acc, body.password)
        accounts.invalidate_tokens(db, acc, ("reset_password", "set_password"))  # one password link works once
    except accounts.AccountError as exc:
        raise _err(exc) from exc
    accounts.mark_verified(db, acc)  # the reset link proves the address
    _login(request, acc)
    return accounts.public(acc)


# --- profile ----------------------------------------------------------------------------------


@router.get("")
def me(acc: Account = Depends(current_account)) -> dict:
    return accounts.public(acc)


class ProfileBody(BaseModel):
    name: str | None = Field(None, max_length=120)
    country: str | None = Field(None, max_length=2)


@router.patch("")
def update_profile(body: ProfileBody, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    if body.name is not None:
        acc.name = body.name.strip()
    if body.country is not None:
        acc.country = body.country.upper() or None
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return accounts.public(acc)


class PasswordChangeBody(BaseModel):
    current_password: str
    new_password: str


@router.post("/password/change")
def change_password(
    body: PasswordChangeBody, request: Request, acc: Account = Depends(current_account), db: Session = Depends(get_session)
) -> dict:
    try:
        accounts.authenticate(db, acc.email, body.current_password)
    except accounts.AccountError as exc:
        raise HTTPException(403, "current password is wrong") from exc
    try:
        accounts.set_password(db, acc, body.new_password)
    except accounts.AccountError as exc:
        raise _err(exc) from exc
    _login(request, acc)  # keep this session, others are logged out (session_version bumped)
    return {"ok": True}


# --- watches ------------------------------------------------------------------------------------


@router.get("/devices")
def my_devices(request: Request, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> list[dict]:
    hub = hub_of(request)
    return [device_out(d, hub) for d in DeviceRepo(db).list(acc.id)]


class PairBody(BaseModel):
    code: str
    name: str = Field("My ola", min_length=1, max_length=80)


@router.post("/devices/pair")
async def pair(body: PairBody, request: Request, acc: Account = Depends(verified_account), db: Session = Depends(get_session)) -> dict:
    if not is_valid_pairing_code(body.code):
        raise HTTPException(400, "the code has 6 digits")
    if not care_activation.can_set_up_watch(db, acc):
        raise HTTPException(403, "no watch order found for this account")
    try:
        device_id = await hub_of(request).pair(body.code, body.name, acc.id)
    except PairingError as exc:
        raise HTTPException(404, "code not found or expired — check the code on your watch") from exc
    await email.send(email.watch_paired(acc.email, body.name))
    # The watch is paired (server-side): this, and only this, starts a pending ola Care trial. A failure
    # leaves the watch paired and the activation retryable; it never fails the pairing.
    try:
        await care_activation.activate(acc.id)
    except Exception:  # noqa: BLE001
        log.warning("care activation after pairing failed for account %s", acc.id, exc_info=True)
    db.expire_all()
    return {"device_id": device_id, "care": care_activation.public_state(db, acc.id)}


# --- watch setup (onboarding) ----------------------------------------------------------------------


def _onboarding(db: Session, acc: Account) -> dict[str, Any]:
    """Setup progress, derived from the server state (only the phone choice is stored), so a refresh, a new
    sign-in or the link in the welcome email resumes where the customer left off."""
    order = db.exec(select(Order).where(Order.account_id == acc.id).order_by(col(Order.id).desc())).first()
    watches = [d for d in DeviceRepo(db).list(acc.id) if d.paired_at is not None and d.revoked_at is None]
    care = care_activation.public_state(db, acc.id)
    return {
        "eligible": care_activation.can_set_up_watch(db, acc),
        "order": None if order is None else {"id": order.id, "status": order.status, "created_at": order.created_at},
        "has_password": acc.password_hash is not None,
        "email_verified": acc.email_verified_at is not None,
        "platform": acc.setup_platform,
        "watches": len(watches),
        "care": care,
        "complete": bool(watches) and (care is None or care["status"] in ("active", "not_eligible")),
    }


@router.get("/onboarding")
def get_onboarding(acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    return _onboarding(db, acc)


class OnboardingBody(BaseModel):
    platform: Literal["android", "iphone"] | None = None


@router.put("/onboarding")
def put_onboarding(body: OnboardingBody, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    acc.setup_platform = body.platform
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return _onboarding(db, acc)


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.patch("/devices/{device_id}")
def rename(body: RenameBody, request: Request, dev: Device = Depends(owned_device), db: Session = Depends(get_session)) -> dict:
    dev = DeviceRepo(db).rename(dev.id, body.name)
    return device_out(dev, hub_of(request))


@router.delete("/devices/{device_id}")
async def remove(request: Request, dev: Device = Depends(owned_device), db: Session = Depends(get_session)) -> dict:
    """Remove the watch from the account: its history is erased and the watch returns to pairing."""
    device_id = dev.id
    ConversationRepo(db).delete_device_history(device_id)
    DeviceRepo(db).revoke(device_id, unassign=True)
    hub = hub_of(request)
    hub.forget_owner(device_id)
    await hub.revoke(device_id)
    return {"ok": True}


@router.get("/devices/{device_id}/settings")
def get_settings_(dev: Device = Depends(owned_device), db: Session = Depends(get_session)) -> dict:
    return read_settings(db, dev.id)


@router.patch("/devices/{device_id}/settings")
async def patch_settings_(
    changes: dict[str, Any], request: Request, dev: Device = Depends(owned_device), db: Session = Depends(get_session)
) -> dict:
    return await patch_settings(db, hub_of(request), dev.id, changes, dev.account_id)


@router.get("/devices/{device_id}/conversations")
def conversations(dev: Device = Depends(owned_device), db: Session = Depends(get_session)) -> list[dict]:
    return conversations_out(db, dev.id, dev.account_id)


@router.delete("/devices/{device_id}/conversations")
def delete_history(dev: Device = Depends(owned_device), db: Session = Depends(get_session)) -> dict:
    return {"deleted_turns": ConversationRepo(db).delete_device_history(dev.id)}


@router.get("/options")
def options_(request: Request, acc: Account = Depends(current_account)) -> dict:
    return options(request)


@router.post("/voice-sample")
async def voice_sample_(body: VoiceSampleBody, request: Request, acc: Account = Depends(current_account)) -> Response:
    return await voice_sample(body, request, account_id=acc.id)


# --- personas: system + own ----------------------------------------------------------------------


def _persona_out(p: Persona) -> dict:
    return {**p.model_dump(exclude={"account_id"}), "own": p.account_id is not None}


def _own_persona(db: Session, persona_id: int, acc: Account) -> Persona:
    p = db.get(Persona, persona_id)
    if not p or p.account_id != acc.id:
        raise HTTPException(404, "persona not found")
    return p


@router.get("/personas")
def personas(acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> list[dict]:
    return [_persona_out(p) for p in PersonaRepo(db).list(acc.id)]


@router.post("/personas")
def create_persona(body: PersonaBody, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    return _persona_out(PersonaRepo(db).upsert(None, body.name, body.system_prompt, False, acc.id))


@router.put("/personas/{persona_id}")
def update_persona(
    persona_id: int,
    body: PersonaBody,
    request: Request,
    background: BackgroundTasks,
    acc: Account = Depends(current_account),
    db: Session = Depends(get_session),
) -> dict:
    _own_persona(db, persona_id, acc)
    out = _persona_out(PersonaRepo(db).upsert(persona_id, body.name, body.system_prompt, False, acc.id))
    background.add_task(hub_of(request).refresh_chat_titles, acc.id)  # the name shown on the watches
    return out


@router.delete("/personas/{persona_id}")
def delete_persona(
    persona_id: int,
    request: Request,
    background: BackgroundTasks,
    acc: Account = Depends(current_account),
    db: Session = Depends(get_session),
) -> dict:
    _own_persona(db, persona_id, acc)
    PersonaRepo(db).delete(persona_id)
    background.add_task(hub_of(request).refresh_chat_titles, acc.id)
    return {"ok": True}


# --- notes and reminders ---------------------------------------------------------------------------


def _utc_due(body: ItemBody) -> datetime | None:
    if body.kind != "reminder":
        return None
    if body.due_at is None:
        raise HTTPException(422, [{"loc": ["due_at"], "msg": "a reminder needs a date and time", "type": "value_error"}])
    due = body.due_at if body.due_at.tzinfo else body.due_at.replace(tzinfo=timezone.utc)
    return due.astimezone(timezone.utc)


def _utc_end(body: ItemBody) -> datetime | None:
    if body.kind != "reminder" or body.end_at is None:
        return None
    end = body.end_at if body.end_at.tzinfo else body.end_at.replace(tzinfo=timezone.utc)
    return end.astimezone(timezone.utc)


def _time_error(exc: ItemTimeError) -> HTTPException:
    return HTTPException(422, [{"loc": ["end_at"], "msg": str(exc), "type": "value_error"}])


def _own_item(db: Session, acc: Account, kind: str, number: int):
    it = ItemRepo(db).get(acc.id, kind, number) if kind in ItemRepo.KINDS else None
    if it is None:
        raise HTTPException(404, f"{kind} not found")
    return it


async def _items_changed(request: Request, account_id: int) -> None:
    hub = hub_of(request)
    await hub.push_items(account_id)
    hub.items_changed(account_id)


@router.get("/items")
def items(kind: str | None = None, acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> list[dict]:
    return [web_view(it) for it in ItemRepo(db).list(acc.id, kind if kind in ItemRepo.KINDS else None)]


@router.post("/items")
async def create_item(
    body: ItemBody, request: Request, acc: Account = Depends(current_account), db: Session = Depends(get_session)
) -> dict:
    try:
        it = ItemRepo(db).create(
            acc.id,
            body.kind,
            body.text,
            _utc_due(body),
            _utc_end(body),
            body.notify_before_min,
            location=body.location,
            participants=body.participants,
        )
    except ItemTimeError as exc:
        raise _time_error(exc) from None
    except ItemLimitError:
        raise HTTPException(409, f"you can keep at most {ItemRepo.MAX_PER_KIND} of these; delete some first") from None
    except ItemTextError as exc:
        raise HTTPException(422, [{"loc": ["text"], "msg": str(exc), "type": "value_error"}]) from None
    out = web_view(it)
    await _items_changed(request, acc.id)
    return out


@router.put("/items/{kind}/{number}")
async def update_item(
    kind: str,
    number: int,
    body: ItemBody,
    request: Request,
    acc: Account = Depends(current_account),
    db: Session = Depends(get_session),
) -> dict:
    it = _own_item(db, acc, kind, number)
    body.kind = it.kind  # the kind of an item never changes
    try:
        it = ItemRepo(db).update(
            it,
            text=body.text,
            due_at=_utc_due(body),
            end_at=_utc_end(body),
            notify_before_min=body.notify_before_min,
            location=body.location,
            participants=body.participants,
        )
    except ItemTimeError as exc:
        raise _time_error(exc) from None
    except ItemTextError as exc:
        raise HTTPException(422, [{"loc": ["text"], "msg": str(exc), "type": "value_error"}]) from None
    out = web_view(it)
    await _items_changed(request, acc.id)
    return out


@router.put("/items/note/{number}/pin")
async def set_note_pinned(
    number: int,
    body: ItemPinBody,
    request: Request,
    acc: Account = Depends(current_account),
    db: Session = Depends(get_session),
) -> dict:
    it = ItemRepo(db).update(_own_item(db, acc, "note", number), pinned=body.pinned)
    out = web_view(it)
    await _items_changed(request, acc.id)
    return out


@router.put("/items/reminder/{number}/done")
async def set_reminder_done(
    number: int,
    body: ItemDoneBody,
    request: Request,
    acc: Account = Depends(current_account),
    db: Session = Depends(get_session),
) -> dict:
    it = ItemRepo(db).update(_own_item(db, acc, "reminder", number), done=body.done)
    out = web_view(it)
    await _items_changed(request, acc.id)
    return out


@router.delete("/items/{kind}/{number}")
async def delete_item(
    kind: str, number: int, request: Request, acc: Account = Depends(current_account), db: Session = Depends(get_session)
) -> dict:
    ItemRepo(db).delete(_own_item(db, acc, kind, number))
    await _items_changed(request, acc.id)
    return {"ok": True}


# --- usage this month ----------------------------------------------------------------------------


@router.get("/usage")
def usage(acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> dict:
    """Conversations in the current allowance period. Internal costs are never sent to customers."""
    from app import allowance as allowance_mod
    from app import entitlements

    a = entitlements.allowance(db, acc)
    return {
        "period_start": a.period.start,
        "reset_at": a.period.end,
        "questions": allowance_mod.activity_count(db, acc.id, a.period),
    }


# --- GDPR -------------------------------------------------------------------------------------


@router.get("/export")
def export(acc: Account = Depends(current_account), db: Session = Depends(get_session)) -> Response:
    import json

    data = json.dumps(accounts.export(db, acc), default=str, ensure_ascii=False, indent=2)
    return Response(
        data,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="ola-my-data.json"'},
    )


class DeleteAccountBody(BaseModel):
    password: str


@router.delete("")
async def delete_account(
    body: DeleteAccountBody, request: Request, acc: Account = Depends(current_account), db: Session = Depends(get_session)
) -> dict:
    try:
        accounts.authenticate(db, acc.email, body.password)
    except accounts.AccountError as exc:
        raise HTTPException(403, "wrong password") from exc
    device_ids = accounts.delete_account(db, acc)
    hub = hub_of(request)
    for device_id in device_ids:
        hub.forget_owner(device_id)
        await hub.revoke(device_id)
    request.session.pop("account_id", None)
    request.session.pop("account_v", None)
    return {"ok": True}


# --- live events for this account's watches ------------------------------------------------------


@router.websocket("/live")
async def live(ws: WebSocket) -> None:
    acc_id = ws.session.get("account_id")
    with session_scope() as db:
        acc = db.get(Account, acc_id) if acc_id else None
        ok = acc is not None and acc.status == "active" and ws.session.get("account_v") == acc.session_version
    if not ok:
        await ws.close(code=4401)
        return
    await ws.accept()
    hub = ws.app.state.hub
    queue = hub.subscribe(account_id=acc_id)

    async def drain_client() -> None:
        with contextlib.suppress(WebSocketDisconnect):
            while True:
                await ws.receive_text()

    reader = asyncio.create_task(drain_client())
    try:
        while not reader.done():
            try:
                event = await asyncio.wait_for(queue.get(), 20)
            except asyncio.TimeoutError:
                event = {"type": "keepalive"}
            await ws.send_json(event)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(queue)
        reader.cancel()
