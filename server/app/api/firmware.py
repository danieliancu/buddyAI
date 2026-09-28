"""Firmware releases and OTA offers.

Download URLs are public (firmware is not secret); integrity comes from sha256 in the offer and,
in production, from secure boot / signed images (M5).
"""

from __future__ import annotations

import hashlib
import struct

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlmodel import Session

from app.api.system import base_url
from app.config import get_settings
from app.db.models import FirmwareRelease
from app.db.repositories import FirmwareRepo
from app.db.session import get_session
from app.security import require_admin

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


@router.post("/api/firmware", dependencies=[Depends(require_admin)])
async def upload(
    file: UploadFile = File(...), notes: str = Form(""), version: str = Form(""), db: Session = Depends(get_session)
) -> dict:
    data = await file.read(MAX_SIZE + 1)
    if len(data) > MAX_SIZE:
        raise HTTPException(413, "image larger than the OTA partition")
    if not data or data[0] != ESP_IMAGE_MAGIC:
        raise HTTPException(400, "not an ESP32 application image (.bin)")
    ver = version.strip() or parse_app_version(data) or "unknown"
    sha = hashlib.sha256(data).hexdigest()
    rel = FirmwareRepo(db).add(
        FirmwareRelease(version=ver, filename=file.filename or "firmware.bin", sha256=sha, size=len(data), notes=notes)
    )
    (_fw_dir() / f"{rel.id}.bin").write_bytes(data)
    return rel.model_dump()


@router.get("/fw/{release_id}.bin")
def download(release_id: int, db: Session = Depends(get_session)) -> FileResponse:
    rel = FirmwareRepo(db).get(release_id)
    path = _fw_dir() / f"{release_id}.bin"
    if not rel or not path.exists():
        raise HTTPException(404, "release not found")
    return FileResponse(path, media_type="application/octet-stream", filename=f"buddyai-{rel.version}.bin")


class OtaBody(BaseModel):
    release_id: int


@router.post("/api/devices/{device_id}/ota", dependencies=[Depends(require_admin)])
async def offer_ota(device_id: str, body: OtaBody, request: Request, db: Session = Depends(get_session)) -> dict:
    rel = FirmwareRepo(db).get(body.release_id)
    if not rel:
        raise HTTPException(404, "release not found")
    offer = {"version": rel.version, "url": f"{base_url()}/fw/{rel.id}.bin", "sha256": rel.sha256, "size": rel.size}
    if not await request.app.state.hub.offer_ota(device_id, offer):
        raise HTTPException(409, "device is offline")
    return {"ok": True, **offer}
