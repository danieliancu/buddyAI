"""Database tables (SQLModel). Portable: no SQLite-specific types or SQL.

Schema changes go through Alembic migrations (server/migrations).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, BigInteger, Column, Index, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.db.vector import EmbeddingVector


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
    # Long-term memory (app/memory): "remember that..." on request, and learning from conversations (opt-in).
    memory_explicit: bool = True  # "remember that..." is stored
    memory_use: bool = True  # memories are recalled in conversations
    memory_learn: bool = True  # facts are learned from conversations (the customer can switch it off)
    # Watch setup: the phone the customer chose in the onboarding (android | iphone); None = not chosen yet.
    setup_platform: Optional[str] = Field(default=None, max_length=8)


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
    # paid | shipped | delivered | refunded | cancelled | payment_pending | payment_failed (delayed methods)
    status: str = Field(default="paid", max_length=16)
    shipping_name: str = Field(default="", max_length=200)
    shipping_address: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    country: Optional[str] = Field(default=None, max_length=2)
    carrier: str = Field(default="", max_length=60)
    tracking_number: str = Field(default="", max_length=120)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    shipped_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    # None = legacy bundle checkout (watch + Care trial started at purchase); "watch" = the watch alone,
    # card saved, ola Care trial started when the watch is paired (app/care_activation.py).
    checkout_flow: Optional[str] = Field(default=None, max_length=16)


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


class BillingConsent(SQLModel, table=True):
    """Proof that the buyer agreed to future ola Care charges on the card saved at checkout: the exact
    terms shown, their version and hash, and both acceptances (site checkbox + Stripe Checkout terms).
    A billing record: kept when the account is deleted, like orders."""

    __tablename__ = "billing_consents"
    id: Optional[int] = Field(default=None, primary_key=True)
    stripe_checkout_session_id: str = Field(index=True, unique=True, max_length=255)
    account_id: Optional[int] = Field(default=None, index=True)
    order_id: Optional[int] = Field(default=None, index=True)
    stripe_customer_id: Optional[str] = Field(default=None, max_length=64)
    stripe_payment_method_id: Optional[str] = Field(default=None, max_length=255)
    status: str = Field(default="pending", max_length=16)  # pending | accepted | missing
    terms_version: str = Field(max_length=32)
    terms_text: str
    terms_sha256: str = Field(max_length=64)
    amount_minor: int  # the recurring Care price agreed, minor units of `currency`
    currency: str = Field(max_length=3)
    interval: str = Field(default="month", max_length=8)
    trial_days: int
    trial_start_rule: str = Field(default="on_pairing", max_length=16)
    site_accepted_at: datetime
    site_ip: str = Field(default="", max_length=64)
    site_user_agent: str = Field(default="", max_length=300)
    stripe_tos_consent: Optional[str] = Field(default=None, max_length=16)  # session.consent.terms_of_service
    stripe_consent_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CareActivation(SQLModel, table=True):
    """The ola Care trial waiting for the watch: created when a watch-only order is paid, turned into a
    Stripe subscription when a watch is first paired to the account. One row per account.

    awaiting_pairing -> activating (leased) -> active | failed (retried) ; not_eligible (no trial)."""

    __tablename__ = "care_activations"
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="accounts.id", unique=True, index=True)
    order_id: Optional[int] = Field(default=None, index=True)
    consent_id: Optional[int] = Field(default=None)
    stripe_customer_id: str = Field(max_length=64)
    payment_method_id: Optional[str] = Field(default=None, max_length=255)
    currency: str = Field(default="gbp", max_length=3)
    status: str = Field(default="awaiting_pairing", max_length=20)
    reason: str = Field(default="", max_length=40)  # not_eligible: why (complimentary, trial_used, ...)
    stripe_subscription_id: Optional[str] = Field(default=None, unique=True, max_length=255)
    attempts: int = 0
    last_error_code: str = Field(default="", max_length=40)
    lease_until: Optional[datetime] = None
    next_retry_at: Optional[datetime] = None
    activated_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class BillingSettings(SQLModel, table=True):
    """Operator-editable plan settings (one row, id=1). Amounts in GBP pence.

    Selling prices (what the customer pays) are separate from allowances (the internal AI provider
    cost we absorb). The Stripe price ids stay in the environment (test vs live keys)."""

    __tablename__ = "billing_settings"
    id: int = Field(default=1, primary_key=True)
    enforce: bool = False  # check subscriptions/allowances even without Stripe keys
    care_price_pence: int = 799  # existing databases keep their value (operator: Usage → Plan settings)
    care_allowance_pence: int = 250
    topup_price_pence: int = 199
    topup_allowance_pence: int = 65
    thresholds: str = Field(default="80,95,100", max_length=40)  # % of the allowance that notify
    usd_gbp_rate: str = Field(default="0.75", max_length=16)  # USD -> GBP for provider costs (Decimal text)
    reserve_pence: int = 3  # held per running turn so parallel watches cannot overshoot
    admission_paused: bool = False  # maintenance drain: no new AI operation is admitted
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
    period_key: Optional[str] = Field(default=None, max_length=80)  # the allowance period it tops up (stable id)


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
    purpose: str = Field(max_length=16)  # verify_email | reset_password | set_password (welcome, 7 days)
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
    # The server's session id of the latest authenticated hello: the session a report from older firmware
    # (no "prev_session") refers to on the next hello (ola Diagnostics, app/incidents).
    last_session_id: Optional[str] = Field(default=None, max_length=64)


class DeviceIssue(SQLModel, table=True):
    """One diagnostic event (ola Diagnostics): a reboot or lost connection reported by the watch in its
    hello, or something the server saw itself. Events are grouped into a DiagIncident."""

    __tablename__ = "device_issues"
    __table_args__ = (Index("uq_device_issues_dedup", "device_id", "dedup_key", unique=True),)
    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: str = Field(max_length=64, index=True)
    account_id: Optional[int] = Field(default=None, index=True)
    # reboot | disconnect | turn_interrupted | server_timeout | ws_close | server_exception | provider_failure
    # | lease_lost | server_shutdown
    kind: str = Field(max_length=32)
    severity: str = Field(default="warn", max_length=8)  # error | warn | info
    reason: str = Field(default="", max_length=64)
    detail: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    fw_version: str = Field(default="", max_length=32)
    created_at: datetime = Field(default_factory=utcnow, index=True)  # when the server stored it
    # Since migration 0026 (NULL on older rows: never back-filled with invented details)
    incident_id: Optional[int] = Field(default=None, index=True)
    detected_by: Optional[str] = Field(default=None, max_length=8)  # watch | server
    category: Optional[str] = Field(default=None, max_length=16)  # watch | connection | server | undetermined
    confidence: Optional[str] = Field(default=None, max_length=12)  # confirmed | probable | unknown
    suspected_component: Optional[str] = Field(default=None, max_length=32)
    reason_code: Optional[str] = Field(default=None, max_length=48)
    session_id: Optional[str] = Field(default=None, max_length=64, index=True)
    turn_id: Optional[int] = None
    occurred_at: Optional[datetime] = None  # when it happened (a watch report arrives later)
    dedup_key: Optional[str] = Field(default=None, max_length=96)


class DiagIncident(SQLModel, table=True):
    """ola Diagnostics: one incident = the events that share a correlation key (one session, one turn or
    one restart report). The classification is recomputed from all its events (app/incidents/classify.py)."""

    __tablename__ = "diag_incidents"
    __table_args__ = (
        Index("ix_diag_incidents_time", "occurred_at", "id"),
        Index("ix_diag_incidents_device_time", "device_id", "occurred_at"),
        Index("ix_diag_incidents_category_time", "category", "occurred_at"),
    )
    id: Optional[int] = Field(default=None, primary_key=True)
    device_id: str = Field(max_length=64)
    account_id: Optional[int] = Field(default=None)
    correlation_key: str = Field(max_length=160, unique=True)
    category: str = Field(default="undetermined", max_length=16)
    severity: str = Field(default="warn", max_length=8)
    confidence: str = Field(default="unknown", max_length=12)
    reason_code: str = Field(default="", max_length=48)
    suspected_component: Optional[str] = Field(default=None, max_length=32)
    detected_by: str = Field(default="server", max_length=8)  # who saw the primary event
    primary_event_id: Optional[int] = None
    session_id: Optional[str] = Field(default=None, max_length=64, index=True)
    turn_id: Optional[int] = None
    fw_version: str = Field(default="", max_length=32)
    event_count: int = 0
    legacy: bool = False  # made by migration 0026 from a pre-diagnostics issue
    occurred_at: datetime = Field(default_factory=utcnow)
    recovered_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow)


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
    """A note (text only) or a reminder (due time, optional end time, short text).

    Numbered per (account, kind); a deleted number is reused by the next item.
    """

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("account_id", "kind", "number", name="uq_items_account_kind_number"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    # Stable id for voice references: numbers are reused after a delete, uids never are.
    uid: str = Field(default_factory=lambda: uuid.uuid4().hex, max_length=32, unique=True)
    # Bumped on every content write (not when a reminder fires): a pending voice confirmation or a
    # selection made earlier is refused when the item changed since.
    version: int = Field(default=1, sa_column_kwargs={"server_default": "1"})
    account_id: int = Field(foreign_key="accounts.id", index=True)
    kind: str = Field(max_length=16)  # note | reminder
    number: int
    text: str = Field(default="", max_length=10000)
    due_at: Optional[datetime] = Field(default=None, index=True)  # reminders only (UTC)
    end_at: Optional[datetime] = None  # reminders only, optional: end of a time range (UTC, after due_at)
    notify_before_min: Optional[int] = None  # reminders only, optional: also alert this many minutes before
    early_fired_at: Optional[datetime] = None  # set once a watch received that advance alert
    location: Optional[str] = Field(default=None, max_length=120)  # reminders, optional: where
    participants: Optional[str] = Field(default=None, max_length=200)  # reminders, optional: "Ana, Mihai"
    pinned: bool = False  # notes: kept at the top of the notes list
    fired_at: Optional[datetime] = None  # set once a watch received the reminder
    done_at: Optional[datetime] = None  # reminders only: marked completed (it no longer fires or counts as overdue)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class VoiceOperation(SQLModel, table=True):
    """A change made by voice (create / change / copy / delete), recorded in the same transaction as the
    change. `op_key` identifies the operation (account, device, session, turn, tool, arguments): a technical
    retry finds it and gets the stored result instead of a second copy / line. `reported` turns true when
    the turn that made it ended normally - otherwise the next turn is told the change was saved."""

    __tablename__ = "voice_operations"
    id: Optional[int] = Field(default=None, primary_key=True)
    op_key: str = Field(max_length=64, unique=True)
    account_id: int = Field(index=True)
    device_id: str = Field(max_length=64)
    session_id: str = Field(max_length=64)
    turn_id: int
    tool: str = Field(max_length=32)
    summary: str = Field(default="", max_length=300)
    result: str = ""
    reported: bool = False
    created_at: datetime = Field(default_factory=utcnow, index=True)


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
    mode: str = Field(default="chat", max_length=16)  # chat | note | reminder (voice edit) | edit (older edit turns)
    tools: str = Field(default="", max_length=255)  # tools the reply used, comma-separated (web_search, item_create:note...)
    search_note: str = ""  # web search of the reply: "query -> answer" (kept in the model's history)
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
    # The AI operation this cost belongs to (usage_operations), its stable dedup key (operation:stage:seq:
    # provider:model:unit - the same call reported twice is one row) and the period the operation was
    # accepted in (late costs stay there). NULL on rows written before operations existed.
    operation_id: Optional[int] = Field(default=None, foreign_key="usage_operations.id")
    dedup_key: Optional[str] = Field(default=None, max_length=160)
    period_key: Optional[str] = Field(default=None, max_length=80)
    stage: Optional[str] = Field(default=None, max_length=16)


class UsageOperation(SQLModel, table=True):
    """One AI-consuming operation (a watch turn, a voice sample, an operator test): the database is the
    authority for admitting it, the allowance it reserves, who executes it (lease + token) and how it ended.
    See app/usage_ops.py and docs/BILLING.md."""

    __tablename__ = "usage_operations"
    id: Optional[int] = Field(default=None, primary_key=True)
    op_uid: str = Field(max_length=32)
    account_id: Optional[int] = None  # None: a watch without an owner (operator stock) or an operator test
    device_id: str = Field(max_length=64)
    request_key: str = Field(max_length=128)  # r:<request_id> | l:<device>:<session>:<turn> | s:<uuid>
    request_kind: str = Field(max_length=8)  # client | legacy | server
    request_fingerprint: str = Field(max_length=64)
    kind: str = Field(max_length=16)  # chat | note | reminder | voice_sample | operator_test
    period_key: Optional[str] = Field(default=None, max_length=80)
    period_kind: Optional[str] = Field(default=None, max_length=16)
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None
    source: str = Field(max_length=16)  # stripe | complimentary | calendar | internal | unenforced | unowned | operator
    subscription_id: Optional[int] = None
    enforced: bool = False
    state: str = Field(max_length=12)  # reserved | running | settled | cancelled | expired
    cost_certainty: str = Field(default="pending", max_length=12)  # pending | exact | unpriced | uncertain
    reserved_micro: int = Field(default=0, sa_type=BigInteger)
    recorded_cost_micro: int = Field(default=0, sa_type=BigInteger)
    billable_cost_micro: Optional[int] = Field(default=None, sa_type=BigInteger)
    unpriced_count: int = 0
    billable: Optional[bool] = None
    result_status: Optional[str] = Field(default=None, max_length=16)
    reason: Optional[str] = Field(default=None, max_length=32)
    reason_detail: Optional[str] = Field(default=None, max_length=200)
    owner: Optional[str] = Field(default=None, max_length=80)
    exec_token: Optional[str] = Field(default=None, max_length=32)
    accepted_at: datetime = Field(default_factory=utcnow)
    lease_expires_at: Optional[datetime] = None
    heartbeat_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    last_cost_at: Optional[datetime] = None
    turn_db_id: Optional[int] = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PricingRule(SQLModel, table=True):
    __tablename__ = "pricing_rules"
    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(max_length=32)
    model: str = Field(max_length=64)
    unit: str = Field(max_length=24)
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


class Memory(SQLModel, table=True):
    """One remembered fact about the user or their people (app/memory). Owned by one account; every query
    filters on account_id. device_id set = only that watch's wearer. Forgetting deletes the row."""

    __tablename__ = "memories"
    id: Optional[int] = Field(default=None, primary_key=True)
    uid: str = Field(default_factory=lambda: uuid.uuid4().hex, max_length=32, unique=True)
    account_id: int = Field(foreign_key="accounts.id", index=True)
    device_id: Optional[str] = Field(default=None, foreign_key="devices.id", max_length=64)
    kind: str = Field(max_length=16)  # preference | profile | person | routine | goal | project | other
    content: str = Field(max_length=300)  # one atomic fact
    content_hash: str = Field(max_length=64)  # sha256 of the normalized content (dedup)
    subject: Optional[str] = Field(default=None, max_length=60)  # e.g. "user", "person:maria"
    attribute: Optional[str] = Field(default=None, max_length=60)  # e.g. "favourite_drink"
    origin: str = Field(max_length=12)  # explicit | inferred | web
    status: str = Field(default="active", max_length=12)  # active | pending | superseded
    confidence: Optional[float] = None  # inferred only (0-1, after server validation)
    confirmed_at: Optional[datetime] = None  # the user asked for it or confirmed it; None = unconfirmed inference
    sensitivity: str = Field(default="normal", max_length=12)  # normal | special
    source_turn_id: Optional[int] = Field(default=None, foreign_key="turns.id")  # nulled before turns are deleted
    supersedes_id: Optional[int] = Field(default=None, foreign_key="memories.id")
    valid_until: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    version: int = 1
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MemoryJob(SQLModel, table=True):
    """Durable background work for memories (embed / extract / purge), leased like usage operations."""

    __tablename__ = "memory_jobs"
    id: Optional[int] = Field(default=None, primary_key=True)
    uid: str = Field(default_factory=lambda: uuid.uuid4().hex, max_length=32, unique=True)
    kind: str = Field(max_length=12)  # embed | extract | purge
    account_id: int = Field(index=True)
    memory_id: Optional[int] = Field(default=None, foreign_key="memories.id")
    conversation_id: Optional[int] = Field(default=None, foreign_key="conversations.id")
    upto_turn_id: Optional[int] = None  # extract: the last turn included
    state: str = Field(default="pending", max_length=12)  # pending | running | done | failed | dead
    attempts: int = 0
    run_after: datetime = Field(default_factory=utcnow, index=True)
    lease_expires_at: Optional[datetime] = None
    owner: Optional[str] = Field(default=None, max_length=80)
    last_error: Optional[str] = Field(default=None, max_length=200)  # error class only, never user content
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MemoryEmbedding(SQLModel, table=True):
    """A memory's vector in one model space (model_key = provider:model:dims). Derived data: deleted with
    the memory, recomputed when the text (text_hash) or the model changes."""

    __tablename__ = "memory_embeddings"
    __table_args__ = (UniqueConstraint("memory_id", "model_key", name="uq_memory_embeddings_model"),)
    id: Optional[int] = Field(default=None, primary_key=True)
    memory_id: int = Field(foreign_key="memories.id")
    account_id: int
    model_key: str = Field(max_length=96)
    dims: int
    text_hash: str = Field(max_length=64)
    embedding: Any = Field(sa_column=Column("embedding", EmbeddingVector(), nullable=False))
    created_at: datetime = Field(default_factory=utcnow)
