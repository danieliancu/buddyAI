"""Database tables (SQLModel). Portable: no SQLite-specific types or SQL.

Schema changes go through Alembic migrations (server/migrations).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AdminUser(SQLModel, table=True):
    __tablename__ = "admin_users"
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True, max_length=64)
    password_hash: str
    created_at: datetime = Field(default_factory=utcnow)


class Account(SQLModel, table=True):
    """A customer: one person who owns one or more watches."""

    __tablename__ = "accounts"
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True, max_length=254)  # stored lower-case
    password_hash: Optional[str] = None  # None until set (e.g. account created at checkout)
    name: str = Field(default="", max_length=120)
    country: Optional[str] = Field(default=None, max_length=2)  # ISO 3166-1 alpha-2
    email_verified_at: Optional[datetime] = None
    status: str = Field(default="active", max_length=16)  # active | suspended | deleted
    session_version: int = 1  # bump to log out every session (password change/reset)
    created_at: datetime = Field(default_factory=utcnow)
    last_login_at: Optional[datetime] = None


class AuthToken(SQLModel, table=True):
    """One-time tokens sent by email (verify address, reset/set password). Only the hash is stored."""

    __tablename__ = "auth_tokens"
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="accounts.id", index=True)
    purpose: str = Field(max_length=16)  # verify_email | reset_password
    token_hash: str = Field(index=True, max_length=64)
    expires_at: datetime
    used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)


class AuditLog(SQLModel, table=True):
    """Operator actions on customer data (GDPR accountability)."""

    __tablename__ = "audit_log"
    id: Optional[int] = Field(default=None, primary_key=True)
    actor: str = Field(max_length=80)  # operator username, "account:<id>" or "system"
    action: str = Field(max_length=64)
    account_id: Optional[int] = Field(default=None, index=True)
    device_id: Optional[str] = Field(default=None, max_length=64)
    detail: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)


class Device(SQLModel, table=True):
    __tablename__ = "devices"
    id: str = Field(primary_key=True, max_length=64)  # device_id reported by the watch
    account_id: Optional[int] = Field(default=None, foreign_key="accounts.id", index=True)
    name: str = Field(default="BuddyAI Watch", max_length=80)
    hw_model: str = Field(default="", max_length=64)
    fw_version: str = Field(default="", max_length=32)
    token_hash: Optional[str] = Field(default=None, index=True, max_length=128)
    paired_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    last_ip: Optional[str] = Field(default=None, max_length=64)
    battery_pct: Optional[int] = None
    charging: Optional[bool] = None
    rssi: Optional[int] = None


class DeviceSettingsRow(SQLModel, table=True):
    __tablename__ = "device_settings"
    device_id: str = Field(primary_key=True, foreign_key="devices.id", max_length=64)
    version: int = 1
    data: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    updated_at: datetime = Field(default_factory=utcnow)


class Persona(SQLModel, table=True):
    __tablename__ = "personas"
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: Optional[int] = Field(default=None, foreign_key="accounts.id", index=True)  # None = system
    name: str = Field(max_length=80)
    system_prompt: str
    is_default: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"
    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: str = Field(foreign_key="devices.id", index=True, max_length=64)
    started_at: datetime = Field(default_factory=utcnow)
    last_activity_at: datetime = Field(default_factory=utcnow)


class Turn(SQLModel, table=True):
    __tablename__ = "turns"
    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: str = Field(foreign_key="devices.id", index=True, max_length=64)
    account_id: Optional[int] = Field(default=None, index=True)  # owner at the time of the turn
    conversation_id: Optional[int] = Field(default=None, foreign_key="conversations.id", index=True)
    session_id: str = Field(max_length=64)
    turn_no: int
    language: str = Field(max_length=8)
    status: str = Field(default="active", max_length=16)  # active|completed|aborted|error|no_speech
    user_text: str = ""
    assistant_text: str = ""
    error: Optional[str] = None
    stt_provider: Optional[str] = Field(default=None, max_length=32)
    llm_model: Optional[str] = Field(default=None, max_length=64)
    tts_provider: Optional[str] = Field(default=None, max_length=32)
    # Latencies in ms (None when the stage did not happen)
    stt_ms: Optional[int] = None  # end of speech -> final transcript
    llm_first_token_ms: Optional[int] = None  # LLM request -> first token
    tts_first_audio_ms: Optional[int] = None  # first text chunk to TTS -> first PCM
    ttfa_server_ms: Optional[int] = None  # end of speech -> first frame sent
    ttfa_device_ms: Optional[int] = None  # end of speech -> device reported first frame
    created_at: datetime = Field(default_factory=utcnow, index=True)
    finished_at: Optional[datetime] = None


class UsageRecord(SQLModel, table=True):
    __tablename__ = "usage_records"
    id: Optional[int] = Field(default=None, primary_key=True)
    turn_id: Optional[int] = Field(default=None, foreign_key="turns.id", index=True)
    device_id: str = Field(index=True, max_length=64)
    account_id: Optional[int] = Field(default=None, index=True)  # who is billed
    kind: str = Field(max_length=16)  # stt | llm | tts
    provider: str = Field(max_length=32)
    model: str = Field(max_length=64)
    unit: str = Field(max_length=16)  # audio_second | input_token | output_token | character
    quantity: float
    cost_usd: Optional[float] = None
    created_at: datetime = Field(default_factory=utcnow, index=True)


class PricingRule(SQLModel, table=True):
    __tablename__ = "pricing_rules"
    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(max_length=32)
    model: str = Field(max_length=64)
    unit: str = Field(max_length=16)
    price_usd: float
    note: str = ""
    updated_at: datetime = Field(default_factory=utcnow)


class FirmwareRelease(SQLModel, table=True):
    __tablename__ = "firmware_releases"
    id: Optional[int] = Field(default=None, primary_key=True)
    version: str = Field(max_length=32, index=True)
    filename: str = Field(max_length=255)
    sha256: str = Field(max_length=64)
    size: int
    notes: str = ""
    uploaded_at: datetime = Field(default_factory=utcnow)
