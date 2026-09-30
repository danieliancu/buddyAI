"""Database tables (SQLModel). Portable: no SQLite-specific types or SQL.

Schema changes go through Alembic migrations (server/migrations).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, BigInteger, Column, UniqueConstraint
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
    stripe_customer_id: Optional[str] = Field(default=None, index=True, max_length=64)
    allowance_override: Optional[float] = None  # AI cost allowance per period in GBP; None = plan default
    allowance_warned_month: Optional[str] = Field(default=None, max_length=7)  # legacy (pre-0007 80% email)
    # Internal/operator account (the built-in owner of operator stock): never limited, costs still tracked.
    internal: bool = False


class Order(SQLModel, table=True):
    """A watch purchase, created from a completed Stripe Checkout session."""

    __tablename__ = "orders"
    id: Optional[int] = Field(default=None, primary_key=True)
    stripe_session_id: str = Field(index=True, unique=True, max_length=255)
    stripe_payment_intent: Optional[str] = Field(default=None, max_length=255)
    account_id: Optional[int] = Field(default=None, foreign_key="accounts.id", index=True)
    email: str = Field(max_length=254)
    currency: str = Field(max_length=3)
    amount_total: int  # minor units (pence / cents), including tax and shipping
    amount_tax: int = 0
    amount_shipping: int = 0
    status: str = Field(default="paid", max_length=16)  # paid | shipped | delivered | refunded | cancelled
    shipping_name: str = Field(default="", max_length=200)
    shipping_address: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    country: Optional[str] = Field(default=None, max_length=2)
    carrier: str = Field(default="", max_length=60)
    tracking_number: str = Field(default="", max_length=120)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    shipped_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None


class Subscription(SQLModel, table=True):
    """"ola Care": a mirror of a Stripe subscription (kept in sync by webhooks), or an
    operator-granted complimentary/test entitlement (source="complimentary", no Stripe ids)."""

    __tablename__ = "subscriptions"
    id: Optional[int] = Field(default=None, primary_key=True)
    source: str = Field(default="stripe", max_length=16)  # stripe | complimentary
    stripe_subscription_id: Optional[str] = Field(default=None, index=True, unique=True, max_length=255)
    stripe_customer_id: Optional[str] = Field(default=None, index=True, max_length=64)
    account_id: Optional[int] = Field(default=None, foreign_key="accounts.id", index=True)
    status: str = Field(max_length=24)  # trialing | active | past_due | canceled | unpaid | incomplete...
    trial_end: Optional[datetime] = None
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None  # complimentary: the grant ends here
    cancel_at_period_end: bool = False
    allowance_pence: Optional[int] = None  # complimentary grants: allowance per period (None = plan default)
    granted_by: Optional[str] = Field(default=None, max_length=80)
    note: str = Field(default="", max_length=200)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class BillingSettings(SQLModel, table=True):
    """Operator-editable plan settings (one row, id=1). Amounts in GBP pence.

    Selling prices (what the customer pays) are separate from allowances (the internal AI provider
    cost we absorb). The Stripe price ids stay in the environment (test vs live keys)."""

    __tablename__ = "billing_settings"
    id: int = Field(default=1, primary_key=True)
    enforce: bool = False  # check subscriptions/allowances even without Stripe keys
    care_price_pence: int = 799
    care_allowance_pence: int = 250
    topup_price_pence: int = 199
    topup_allowance_pence: int = 65
    thresholds: str = Field(default="80,95,100", max_length=40)  # % of the allowance that notify
    usd_gbp_rate: str = Field(default="0.75", max_length=16)  # USD -> GBP for provider costs (Decimal text)
    reserve_pence: int = 3  # held per running turn so parallel watches cannot overshoot
    updated_at: datetime = Field(default_factory=utcnow)
    updated_by: str = Field(default="", max_length=80)


class TopUp(SQLModel, table=True):
    """A one-time purchase of extra allowance for the current period (Stripe Checkout, mode=payment)."""

    __tablename__ = "topups"
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="accounts.id", index=True)
    stripe_session_id: Optional[str] = Field(default=None, index=True, unique=True, max_length=255)
    stripe_payment_intent: Optional[str] = Field(default=None, index=True, max_length=255)
    amount_pence: int  # what the customer pays
    allowance_pence: int  # extra allowance granted once paid
    status: str = Field(default="pending", max_length=16)  # pending | paid | expired | refunded
    period_start: datetime
    period_end: datetime
    created_at: datetime = Field(default_factory=utcnow)
    paid_at: Optional[datetime] = None
    refunded_at: Optional[datetime] = None


class RevenueEvent(SQLModel, table=True):
    """Money received (Stripe), for the operator's revenue vs provider cost view."""

    __tablename__ = "revenue_events"
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: str = Field(index=True, unique=True, max_length=255)  # invoice / session id
    account_id: Optional[int] = Field(default=None, index=True)
    kind: str = Field(max_length=16)  # subscription | topup | watch | refund
    amount_pence: int  # gross (VAT included), negative for refunds; minor units of `currency`
    tax_pence: int = 0  # VAT included in amount_pence (net = amount - tax)
    currency: str = Field(default="gbp", max_length=3)
    created_at: datetime = Field(default_factory=utcnow, index=True)


class UsageNotice(SQLModel, table=True):
    """A usage threshold (80/95/100 %) reached in one allowance period, and where it was shown."""

    __tablename__ = "usage_notices"
    __table_args__ = (UniqueConstraint("account_id", "period_start", "threshold", name="uq_usage_notice"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(index=True)
    period_start: datetime
    threshold: int
    created_at: datetime = Field(default_factory=utcnow)
    dismissed_web_at: Optional[datetime] = None
    shown_watch_at: Optional[datetime] = None


class SearchCache(SQLModel, table=True):
    """Web search answers, reused while fresh. `scope` "shared" only for public facts (weather,
    transport, businesses, news); anything else is kept per account."""

    __tablename__ = "search_cache"
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True, max_length=64)  # sha256 of scope|category|location|language|query
    scope: str = Field(max_length=32)
    category: str = Field(max_length=16)
    location: str = Field(default="", max_length=80)
    language: str = Field(default="", max_length=8)
    query: str = Field(default="", max_length=300)
    answer: str = ""
    fetched_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(index=True)
    hits: int = 0


class StripeEvent(SQLModel, table=True):
    """Processed webhook event ids (Stripe may deliver an event more than once)."""

    __tablename__ = "stripe_events"
    id: str = Field(primary_key=True, max_length=255)
    type: str = Field(max_length=80)
    processed_at: datetime = Field(default_factory=utcnow)


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
    name: str = Field(default="ola Watch", max_length=80)
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


class Item(SQLModel, table=True):
    """A note (text only) or a reminder (due time + short text).

    Numbered per (account, kind); a deleted number is reused by the next item.
    """

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("account_id", "kind", "number", name="uq_items_account_kind_number"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="accounts.id", index=True)
    kind: str = Field(max_length=16)  # note | reminder
    number: int
    text: str = Field(default="", max_length=10000)
    due_at: Optional[datetime] = Field(default=None, index=True)  # reminders only (UTC)
    fired_at: Optional[datetime] = None  # set once a watch received the reminder
    done_at: Optional[datetime] = None  # reminders only: marked completed (it no longer fires or counts as overdue)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


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
    unit: str = Field(max_length=24)  # audio_second | input_token | cached_input_token | output_token | ...
    quantity: float
    cost_usd: Optional[float] = None  # None = no pricing rule (flagged, never treated as free)
    cost_micro_gbp: Optional[int] = Field(default=None, sa_type=BigInteger)  # millionths of a pound, frozen at the day's rate
    fx_rate: Optional[float] = None  # USD -> GBP used for cost_micro_gbp
    billable: bool = True  # False: counts for the operator, not against the customer (server/provider errors)
    mock: bool = False  # mock providers (development): excluded from financial figures
    # Survive history deletion (turn_id is then cleared): per-turn cost and activity counts.
    turn_uid: Optional[str] = Field(default=None, index=True, max_length=40)
    turn_status: Optional[str] = Field(default=None, max_length=16)  # completed | aborted | error | no_speech
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
