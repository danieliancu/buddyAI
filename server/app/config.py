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

    # Cost display: stored costs are USD; shown as display_currency (editable in the web app)
    display_currency: str = "GBP"
    usd_to_display_rate: float = 0.75  # approximate USD->GBP; update it in the web app (Usage > Currency)

    # Session / protocol
    session_idle_timeout_s: int = 45
    pairing_code_ttl_s: int = 300
    conversation_idle_minutes: int = 30

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
        """name: openai_api_key | dashscope_api_key | azure_speech_key | azure_speech_region"""
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
