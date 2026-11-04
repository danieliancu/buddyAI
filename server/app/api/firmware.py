"""Firmware releases and OTA offers.

Download URLs are public (firmware is not secret); integrity comes from sha256 in the offer and,
in production, from secure boot / signed images (M5).
"""

from __future__ import annotations

import hashlib
import json
import struct

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session, col, select

from app import accounts, firmware_signature, ota_rollout
from app.api.system import base_url
from app.config import get_settings
from app.db.models import Device, DeviceOta, FirmwareRelease, utcnow
from app.db.repositories import FirmwareRepo
from app.db.session import get_session
from app.security import require_admin, require_admin_role

router = APIRouter(tags=["firmware"])
ESP_IMAGE_MAGIC = 0xE9
APP_DESC_MAGIC = 0xABCD5432
MAX_SIZE = 6 * 1024 * 1024  # ota slot size (partitions.csv)


def _fw_dir():
    d = get_settings().data_dir / "firmware"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_app_version(image: bytes) -> str | None:
    """Reads esp_app_desc_t.version from an ESP32 app image (offset 32 = 24B header + 8B segment header)."""
    if len(image) < 32 + 48 or image[0] != ESP_IMAGE_MAGIC:
        return None
    (magic,) = struct.unpack_from("<I", image, 32)
    if magic != APP_DESC_MAGIC:
        return None
    return image[32 + 16 : 32 + 48].split(b"\x00", 1)[0].decode("ascii", "replace") or None


@router.get("/api/firmware", dependencies=[Depends(require_admin)])
def list_releases(db: Session = Depends(get_session)) -> list[dict]:
    return [r.model_dump() for r in FirmwareRepo(db).list()]


@router.post("/api/firmware")
async def upload(
    file: UploadFile = File(...), notes: str = Form(""), version: str = Form(""), hw_model: str = Form(""),
    channel: str = Form("dev"), db: Session = Depends(get_session), user: str = Depends(require_admin_role),
) -> dict:
    if channel not in ("dev", "release"):
        raise HTTPException(400, "channel must be dev or release")
    data = await file.read(MAX_SIZE + 1)
    if len(data) > MAX_SIZE:
        raise HTTPException(413, "image larger than the OTA partition")
    if not data or data[0] != ESP_IMAGE_MAGIC:
        raise HTTPException(400, "not an ESP32 application image (.bin)")
    ver = version.strip() or parse_app_version(data) or "unknown"
    sha = hashlib.sha256(data).hexdigest()
    verified = False
    if channel == "release":
        key = get_settings().firmware_signing_pubkey
        if not key:
            raise HTTPException(400, "release images need BUDDYAI_FIRMWARE_SIGNING_PUBKEY on the server")
        verified, why = firmware_signature.verify(data, firmware_signature.load_public_key(key))
        if not verified:
            accounts.audit(db, user, "firmware.upload_refused", detail=f"{file.filename}: {why}")
            raise HTTPException(400, f"signature check failed: {why}")
    rel = FirmwareRepo(db).add(
        FirmwareRelease(version=ver, filename=file.filename or "firmware.bin", sha256=sha, size=len(data), notes=notes,
                        hw_model=hw_model.strip()[:64], channel=channel, signature_verified=verified)
    )
    (_fw_dir() / f"{rel.id}.bin").write_bytes(data)
    accounts.audit(db, user, "firmware.upload", detail=f"release {rel.id}: {rel.version} {rel.filename} sha256 {sha[:16]}")
    db.refresh(rel)  # the audit's commit expired it
    return rel.model_dump()


@router.get("/fw/{release_id}.bin")
def download(release_id: int, db: Session = Depends(get_session)) -> FileResponse:
    rel = FirmwareRepo(db).get(release_id)
    path = _fw_dir() / f"{release_id}.bin"
    if not rel or not path.exists():
        raise HTTPException(404, "release not found")
    return FileResponse(path, media_type="application/octet-stream", filename=f"ola-{rel.version}.bin")


def _release(db: Session, release_id: int) -> FirmwareRelease:
    rel = FirmwareRepo(db).get(release_id)
    if not rel:
        raise HTTPException(404, "release not found")
    return rel


@router.get("/api/firmware/{release_id}/rollout", dependencies=[Depends(require_admin)])
def rollout(release_id: int, request: Request, db: Session = Depends(get_session)) -> dict:
    """Where a release stands: stage, attempts per state, how many watches the stage really reaches, failures."""
    rel = _release(db, release_id)
    ota_rollout.expire_silent(db)
    devices = db.exec(select(Device).where(Device.hw_model == rel.hw_model, Device.fw_channel == rel.channel,
                                           col(Device.revoked_at).is_(None))).all() if rel.hw_model else []
    attempts = db.exec(select(DeviceOta).where(DeviceOta.release_id == release_id)
                       .order_by(col(DeviceOta.created_at).desc())).all()
    latest: dict[str, DeviceOta] = {}
    for a in attempts:
        latest.setdefault(a.device_id, a)
    return {
        "auto_enabled": get_settings().ota_auto_enabled,
        "stage": rel.stage, "next_stage": ota_rollout.next_stage(rel.stage), "paused": rel.paused, "paused_reason": rel.paused_reason,
        "signature_verified": rel.signature_verified, "signing_evidence": bool(rel.signing_evidence),
        "tally": ota_rollout.tally(db, release_id),
        "fleet": len(devices),
        "in_stage": sum(1 for d in devices if ota_rollout.in_stage(d, rel)) if rel.stage != ota_rollout.DEVELOPMENT else 0,
        "pilot_devices": [{"id": d.id, "name": d.name, "fw_version": d.fw_version, "pilot": d.ota_pilot} for d in devices],
        "watches": [
            {"device_id": a.device_id, "attempt_id": a.attempt_id, "state": a.state, "reason": a.reason,
             "previous_version": a.previous_version, "counts_as_failure": ota_rollout.counts_as_failure(a),
             "reviewed": a.reviewed_at is not None, "updated_at": a.updated_at}
            for a in latest.values()
        ],
    }


class PromoteBody(BaseModel):
    to: str


@router.post("/api/firmware/{release_id}/promote")
def promote(release_id: int, body: PromoteBody, db: Session = Depends(get_session), user: str = Depends(require_admin_role)) -> dict:
    rel = _release(db, release_id)
    was = rel.stage
    try:
        ota_rollout.promote(db, rel, body.to)
    except ota_rollout.PromotionRefused as exc:
        raise HTTPException(409, str(exc)) from None
    accounts.audit(db, user, "firmware.promote", detail=f"release {rel.id} {rel.version}: {was} -> {rel.stage}")
    db.refresh(rel)  # the audit's commit expired it
    return rel.model_dump()


class PauseBody(BaseModel):
    reason: str = Field("paused by an operator", max_length=200)


@router.post("/api/firmware/{release_id}/pause")
def pause(release_id: int, body: PauseBody, db: Session = Depends(get_session), user: str = Depends(require_admin_role)) -> dict:
    rel = _release(db, release_id)
    rel.paused, rel.paused_reason = True, body.reason
    db.add(rel)
    db.commit()
    accounts.audit(db, user, "firmware.pause", detail=f"release {rel.id} {rel.version}: {body.reason}")
    db.refresh(rel)  # the audit's commit expired it
    return rel.model_dump()


@router.post("/api/firmware/{release_id}/resume")
def resume(release_id: int, db: Session = Depends(get_session), user: str = Depends(require_admin_role)) -> dict:
    rel = _release(db, release_id)
    left = ota_rollout.tally(db, release_id)["unreviewed_failures"]
    if left:
        raise HTTPException(409, f"{left} failed attempt(s) still need review.")
    rel.paused, rel.paused_reason = False, ""
    db.add(rel)
    db.commit()
    accounts.audit(db, user, "firmware.resume", detail=f"release {rel.id} {rel.version}")
    db.refresh(rel)  # the audit's commit expired it
    return rel.model_dump()


@router.post("/api/firmware/attempts/{attempt_id}/review")
def review_attempt(attempt_id: str, db: Session = Depends(get_session), user: str = Depends(require_admin_role)) -> dict:
    row = db.get(DeviceOta, attempt_id)
    if not row:
        raise HTTPException(404, "attempt not found")
    row.reviewed_at = utcnow()
    db.add(row)
    db.commit()
    accounts.audit(db, user, "firmware.attempt_reviewed", device_id=row.device_id, detail=f"attempt {attempt_id}: {row.state} {row.reason}")
    return {"ok": True}


class SignedTestBody(BaseModel):
    unit_mac: str = Field(min_length=6, max_length=32)
    valid_image_accepted: bool
    unsigned_image_refused: bool
    foreign_key_refused: bool
    modified_image_refused: bool
    notes: str = Field("", max_length=500)


@router.post("/api/firmware/{release_id}/signed-test")
def record_signed_test(release_id: int, body: SignedTestBody, db: Session = Depends(get_session), user: str = Depends(require_admin_role)) -> dict:
    """Evidence of the signed-update tests on a real watch; all four must have passed (firmware/README.md)."""
    rel = _release(db, release_id)
    if not (body.valid_image_accepted and body.unsigned_image_refused and body.foreign_key_refused and body.modified_image_refused):
        raise HTTPException(409, "All four tests must pass before this can be recorded.")
    rel.signing_evidence = json.dumps({**body.model_dump(), "by": user, "at": utcnow().isoformat(), "version": rel.version})
    db.add(rel)
    db.commit()
    accounts.audit(db, user, "firmware.signed_test", detail=f"release {rel.id} {rel.version} on {body.unit_mac}")
    db.refresh(rel)  # the audit's commit expired it
    return rel.model_dump()


class PilotBody(BaseModel):
    pilot: bool


@router.put("/api/devices/{device_id}/ota-pilot")
def set_pilot(device_id: str, body: PilotBody, db: Session = Depends(get_session), user: str = Depends(require_admin_role)) -> dict:
    dev = db.get(Device, device_id)
    if not dev:
        raise HTTPException(404, "device not found")
    dev.ota_pilot = body.pilot
    db.add(dev)
    db.commit()
    accounts.audit(db, user, "firmware.pilot", device_id=device_id, detail="on" if body.pilot else "off")
    return {"ok": True, "pilot": dev.ota_pilot}


class OtaBody(BaseModel):
    release_id: int


@router.post("/api/devices/{device_id}/ota")
async def offer_ota(device_id: str, body: OtaBody, request: Request, db: Session = Depends(get_session),
                    user: str = Depends(require_admin_role)) -> dict:
    rel = FirmwareRepo(db).get(body.release_id)
    if not rel:
        raise HTTPException(404, "release not found")
    dev = db.get(Device, device_id)
    if not dev:
        raise HTTPException(404, "device not found")
    if rel.hw_model and dev.hw_model and rel.hw_model != dev.hw_model:
        raise HTTPException(409, f"this release is for {rel.hw_model}; the watch is a {dev.hw_model}")
    if device_id not in request.app.state.hub.connections:
        raise HTTPException(409, "device is offline")
    attempt = ota_rollout.new_attempt(db, device_id, rel, dev.fw_version)
    offer = ota_rollout.offer_of(rel, attempt.attempt_id)
    if not await request.app.state.hub.offer_ota(device_id, offer):
        raise HTTPException(409, "device is offline")
    accounts.audit(db, user, "firmware.ota_push", device_id=device_id, detail=f"release {rel.id}: {rel.version}")
    return {"ok": True, **offer}
