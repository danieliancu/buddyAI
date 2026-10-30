"""Server configuration.

Precedence for provider keys: environment / .env  >  data/provider_keys.json (set from the web UI).
Model lists, voices, chunker parameters and default prices live in config/providers.json,
never in business logic.
"""

from __future__ import annotations

import json
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BUDDYAI_", env_file=SERVER_DIR / ".env", extra="ignore"
    )

    host: str = "0.0.0.0"
    port: int = 8765
    data_dir: Path = SERVER_DIR / "data"
    database_url: str = ""
    public_url: str = ""
    mdns_enabled: bool = True
    mock_providers: bool = False
    # AI provider profile: selects config/providers.<ai_profile>.json (e.g. "openai", "qwen").
    ai_profile: str = "openai"
    providers_config: Path | None = None  # explicit override of the profile file
    web_dist: Path = SERVER_DIR.parent / "web" / "dist"

    dashscope_api_key: str = ""
    dashscope_llm_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    dashscope_asr_ws_url: str = "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference"
    dashscope_realtime_ws_url: str = "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_realtime_ws_url: str = "wss://api.openai.com/v1/realtime?intent=transcription"

    azure_speech_key: str = ""
    azure_speech_region: str = "westeurope"

    # Web search backend (app/search.py): legacy_openai = OpenAI hosted web search (the rollback);
    # ddgs_brave = DDGS, then the Brave Search API as fallback, Trafilatura for page text (app/websearch).
    search_backend: str = "legacy_openai"
    brave_api_key: str = ""  # server side only: never sent to the watch, the web app or a log line
    brave_monthly_budget_usd: float = 5.0  # Brave is skipped for the rest of the month once this is used
    brave_price_per_request_usd: float = 0.005  # Brave "Search" plan, $5 / 1,000 requests (verify)
    brave_max_requests_per_search: int = 2  # the paid fallback, per web_search call
    search_total_deadline_s: float = 12.0  # all searching of one turn (searches, pages, the answering call)
    ddgs_request_timeout_s: float = 3.0
    brave_request_timeout_s: float = 4.0
    search_max_results_per_request: int = 8
    search_max_extracted_sources: int = 3  # pages read per search round
    search_context_token_budget: int = 1800  # evidence sent to the answering model per call (approx. tokens)
    extraction_max_bytes: int = 1_500_000
    extraction_timeout_s: float = 2.5  # download + Trafilatura of one page
    ddgs_circuit_breaker_threshold: int = 3  # technical failures in a row before DDGS is switched off...
    ddgs_circuit_breaker_cooldown_s: float = 120.0  # ...for this long, then one trial request
    search_max_concurrency: int = 4  # DDGS searches / page downloads at once (per process)
    ddgs_region: str = ""  # e.g. "uk-en"; "" = from the watch's time zone

    # Cost display: stored costs are USD; shown as display_currency (editable in the web app)
    display_currency: str = "GBP"
    usd_to_display_rate: float = 0.75  # approximate USD->GBP; update it in the web app (Usage > Currency)

    # Customer app / email
    app_url: str = ""  # public URL of the customer app, used in email links (e.g. https://app.example.com)
    email_backend: str = "console"  # console | smtp
    email_from: str = "ola <no-reply@localhost>"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    # The console backend writes email bodies (with their one-time links) to the log. None = automatic: only
    # when public_url is not https (local development); never on a public server unless set to true.
    email_console_bodies: bool | None = None

    # Shop & subscription (Stripe). Billing is off until stripe_secret_key is set: every watch is
    # then entitled (development / private use).
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_watch_gbp: str = ""  # one-time price ids from the Stripe dashboard
    stripe_price_watch_eur: str = ""
    stripe_price_care_gbp: str = ""  # monthly "ola Care" price ids
    stripe_price_care_eur: str = ""
    stripe_shipping_rates_gbp: str = ""  # comma-separated shipping rate ids
    stripe_shipping_rates_eur: str = ""
    care_trial_days: int = 30
    # Local tests only (ignored with a live key, see the properties below): checkout without Stripe Tax, and
    # without Stripe's Terms of Service checkbox (needs a ToS URL in the dashboard). With the checkbox off,
    # consent rests on the site's ola Care checkbox alone and the terms are shown above Stripe's Pay button.
    stripe_automatic_tax: bool = True
    stripe_require_tos: bool = True
    # Plan prices, allowances and thresholds are operator settings (table billing_settings, app/plan.py).
    site_url: str = ""  # public marketing site, for checkout success/cancel redirects
    # UK + EU (post-Brexit shipping to the EU needs customs/IOSS handling — see deploy/README.md)
    ship_countries: str = (
        "GB,IE,AT,BE,BG,HR,CY,CZ,DK,EE,FI,FR,DE,GR,HU,IT,LV,LT,LU,MT,NL,PL,PT,RO,SK,SI,ES,SE"
    )

    @property
    def billing_enabled(self) -> bool:
        return bool(self.stripe_secret_key)

    @property
    def stripe_live(self) -> bool:
        return self.stripe_secret_key.startswith(("sk_live", "rk_live"))

    @property
    def stripe_tax_on(self) -> bool:
        return self.stripe_automatic_tax or self.stripe_live

    @property
    def stripe_tos_on(self) -> bool:
        return self.stripe_require_tos or self.stripe_live

    # Production
    forwarded_allow_ips: str = "127.0.0.1"  # proxies trusted for X-Forwarded-For ("*" inside Docker behind Caddy)
    log_json: bool = False
    # First-run operator setup from the browser. Keep it off on public servers and create the
    # operator with `python -m app.cli create-operator` instead.
    allow_web_setup: bool = True
    # Operators must use two-factor authentication (TOTP): without it, only enrolment works (app/security.py).
    admin_mfa_required: bool = False

    # Session / protocol
    # Closes a session that sent nothing for this long. The watch pings every 30 s (firmware before the
    # power work: 15 s); WebSocket control frames don't count, only messages.
    session_idle_timeout_s: int = 75
    # Retention periods (app/retention.py has the policy; changing them needs business/legal approval).
    # ola Diagnostics: incidents not updated for this many days are deleted (0 = keep forever)
    incident_retention_days: int = 90
    conversation_retention_days: int = 0  # turns + conversations; 0 = kept until the user/account deletes them
    voice_ops_retention_days: int = 7  # voice-change idempotency records (voice_operations)
    memory_superseded_days: int = 90  # replaced memories kept for undo/audit, then deleted
    memory_jobs_days: int = 7  # finished/dead memory jobs
    audit_retention_days: int = 0  # audit_log; 0 = kept
    search_cache_grace_days: int = 1  # expired web-search answers are deleted after this many days
    retention_interval_s: int = 3600  # how often the retention job runs
    # AI usage operations (app/usage_ops.py): the lease a process holds on a running operation, how often it
    # renews it, how often expired leases are recovered, and the database lock wait for admission.
    usage_lease_s: int = 90
    usage_heartbeat_s: int = 20
    usage_recovery_interval_s: int = 30
    usage_lock_timeout_ms: int = 3000
    usage_admit_retries: int = 5
    pairing_code_ttl_s: int = 300
    conversation_idle_minutes: int = 30

    # Long-term memory (app/memory). Remembering on request and learning from conversations are on by default
    # (each account can switch either off); vectors are off until switched on (deploy/README.md).
    memory_enabled: bool = True  # "remember that...", recall, the Memory page (each account can switch parts off)
    memory_accounts: str = ""  # comma-separated account ids allowed while rolling out; "" = every account
    memory_embeddings_enabled: bool = False  # vectors for memories (needs pgvector on PostgreSQL, migration 0022)
    memory_vector_retrieval: bool = False  # semantic recall when an account has more memories than fit the prompt
    memory_inference_enabled: bool = True  # learn facts from conversations (each account can switch it off)
    memory_max_active: int = 300  # per account
    memory_embed_timeout_ms: int = 350  # the query embedding inside a turn; slower -> word matching instead
    memory_max_distance: float = 0.55  # cosine distance above which a memory is not relevant
    memory_job_interval_s: int = 15

    def memory_on_for(self, account_id: int | None) -> bool:
        if not self.memory_enabled or account_id is None:
            return False
        allowed = {a.strip() for a in self.memory_accounts.split(",") if a.strip()}
        return not allowed or str(account_id) in allowed

    def model_post_init(self, __context: Any) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.providers_config is None:
            self.providers_config = SERVER_DIR / "config" / f"providers.{self.ai_profile}.json"
        if not self.database_url:
            self.database_url = f"sqlite:///{(self.data_dir / 'buddyai.db').as_posix()}"

    # --- secrets that live in data/ -------------------------------------------------

    @property
    def secret_key(self) -> str:
        """Cookie-signing key, generated once and persisted."""
        path = self.data_dir / "secret.key"
        if not path.exists():
            path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        return path.read_text(encoding="utf-8").strip()

    @property
    def _keys_file(self) -> Path:
        return self.data_dir / "provider_keys.json"

    def _stored_keys(self) -> dict[str, str]:
        if self._keys_file.exists():
            return json.loads(self._keys_file.read_text(encoding="utf-8"))
        return {}

    def provider_key(self, name: str) -> str:
        """name: openai_api_key | dashscope_api_key | azure_speech_key | azure_speech_region | brave_api_key"""
        env_value = getattr(self, name, "")
        return env_value or self._stored_keys().get(name, "")

    def store_provider_keys(self, values: dict[str, str]) -> None:
        stored = self._stored_keys()
        stored.update({k: v for k, v in values.items() if v is not None})
        self._keys_file.write_text(json.dumps(stored, indent=2), encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def load_providers_config() -> dict[str, Any]:
    return json.loads(get_settings().providers_config.read_text(encoding="utf-8"))
