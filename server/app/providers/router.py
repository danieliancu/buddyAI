"""Provider selection from config/providers.json + device settings + language.

The active profile (BUDDYAI_AI_PROFILE -> config/providers.<profile>.json) decides which vendors are
used. The pipeline only receives interfaces; no vendor specifics leak upward.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import Settings, get_settings, load_providers_config
from app.device_settings import DeviceSettings
from app.providers.llm.base import LLMProvider
from app.providers.stt.base import STTProvider
from app.providers.tts.base import TTSProvider


STAGES = {
    "qwen_asr": "stt",
    "openai_stt": "stt",
    "qwen": "llm",
    "openai": "llm",
    "qwen_tts": "tts",
    "azure_tts": "tts",
    "openai_tts": "tts",
}


@dataclass
class TTSSelection:
    provider: TTSProvider
    voice: str
    instructions: str = ""


class ProviderRouter:
    def __init__(self, settings: Settings | None = None, config: dict[str, Any] | None = None) -> None:
        self.settings = settings or get_settings()
        self.config = config or load_providers_config()
        self._cache: dict[tuple, Any] = {}

    # --- factories (cached per credentials so key changes from the web UI take effect) ---

    def _cached(self, key: tuple, factory):
        if key not in self._cache:
            self._cache[key] = factory()
        return self._cache[key]

    def _build(self, provider: str, model: str):
        s = self.settings
        if s.mock_providers:
            from app.providers import mock

            return {"stt": mock.MockSTT, "llm": mock.MockLLM, "tts": mock.MockTTS}[self._stage(provider)]()
        dashscope = s.provider_key("dashscope_api_key")
        if provider == "qwen_asr":
            from app.providers.stt.qwen import QwenStreamingSTT

            return self._cached(
                ("qwen_asr", model, dashscope),
                lambda: QwenStreamingSTT(dashscope, s.dashscope_asr_ws_url, model),
            )
        if provider == "qwen":
            from app.providers.llm.qwen import QwenLLM

            return self._cached(("qwen", dashscope), lambda: QwenLLM(dashscope, s.dashscope_llm_base_url))
        if provider == "qwen_tts":
            from app.providers.tts.qwen import QwenRealtimeTTS

            return self._cached(
                ("qwen_tts", model, dashscope),
                lambda: QwenRealtimeTTS(dashscope, s.dashscope_realtime_ws_url, model),
            )
        if provider == "azure_tts":
            from app.providers.tts.azure import AzureTTS

            key, region = s.provider_key("azure_speech_key"), s.provider_key("azure_speech_region")
            return self._cached(("azure_tts", key, region), lambda: AzureTTS(key, region or "westeurope"))
        openai_key = s.provider_key("openai_api_key")
        if provider == "openai_stt":
            from app.providers.stt.openai import OpenAIRealtimeSTT

            return self._cached(
                ("openai_stt", model, openai_key),
                lambda: OpenAIRealtimeSTT(openai_key, s.openai_realtime_ws_url, model),
            )
        if provider == "openai":
            from app.providers.llm.openai import OpenAILLM

            return self._cached(("openai", openai_key), lambda: OpenAILLM(openai_key, s.openai_base_url))
        if provider == "openai_tts":
            from app.providers.tts.openai import OpenAITTS

            return self._cached(
                ("openai_tts", model, openai_key), lambda: OpenAITTS(openai_key, s.openai_base_url, model)
            )
        raise ValueError(f"unknown provider {provider!r}")

    @staticmethod
    def _stage(provider: str) -> str:
        return STAGES.get(provider, "llm")

    # --- selection -------------------------------------------------------------------

    def stt(self, language: str) -> STTProvider:
        cfg = self.config["stt"]
        sel = cfg.get("by_language", {}).get(language) or cfg["default"]
        return self._build(sel["provider"], sel["model"])

    def llm_models(self) -> list[dict[str, str]]:
        return self.config["llm"]["models"]

    def llm(self, settings: DeviceSettings) -> tuple[LLMProvider, str]:
        cfg = self.config["llm"]
        models = {m["id"]: m for m in cfg["models"]}
        model_id = settings.llm_model if settings.llm_model in models else cfg["default_model"]
        return self._build(models[model_id]["provider"], model_id), model_id

    def llm_params(self) -> dict[str, Any]:
        return dict(self.config["llm"].get("params", {}))

    def tts_options(self) -> dict[str, Any]:
        return self.config["tts"]["by_language"]

    def tts(self, language: str, settings: DeviceSettings) -> TTSSelection:
        sel = self.config["tts"]["by_language"][language]
        wanted = settings.tts_voice_ro if language == "ro" else settings.tts_voice_en
        voice = wanted if wanted in sel["voices"] else sel["default_voice"]
        return TTSSelection(self._build(sel["provider"], sel["model"]), voice, sel.get("instructions", ""))
