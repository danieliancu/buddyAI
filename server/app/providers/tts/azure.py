"""Azure Neural TTS over REST (raw 24 kHz PCM streaming)."""

from __future__ import annotations

from typing import Any
from xml.sax.saxutils import escape

from app import languages
from app.providers.base import ProviderError
from app.providers.tts.base import TTSRequest
from app.providers.tts.http_stream import HTTPStreamingTTS



class AzureTTS(HTTPStreamingTTS):
    name = "azure_tts"
    model = "azure-neural"
    label = "Azure"

    def __init__(self, key: str, region: str, **kw: Any) -> None:
        super().__init__(**kw)
        self.key, self.region = key, region

    def check_configured(self) -> None:
        if not self.key:
            raise ProviderError("tts", "Azure Speech key is not configured")

    def ssml(self, text: str, request: TTSRequest) -> str:
        lang = languages.get(request.language)
        locale = (lang.azure_locale if lang else None) or "en-GB"
        rate = f"{round((request.speech_rate - 1.0) * 100):+d}%"
        return (
            f"<speak version='1.0' xml:lang='{locale}'><voice name='{escape(request.voice)}'>"
            f"<prosody rate='{rate}'>{escape(text)}</prosody></voice></speak>"
        )

    def build_request(self, text: str, request: TTSRequest) -> dict[str, Any]:
        return {
            "url": f"https://{self.region}.tts.speech.microsoft.com/cognitiveservices/v1",
            "headers": {
                "Ocp-Apim-Subscription-Key": self.key,
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "raw-24khz-16bit-mono-pcm",
                "User-Agent": "ola",
            },
            "content": self.ssml(text, request),
        }
