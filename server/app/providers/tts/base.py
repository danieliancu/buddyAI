"""TTSProvider interface: streaming text chunks in, PCM chunks out."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass


@dataclass
class TTSRequest:
    voice: str
    language: str
    speech_rate: float = 1.0
    instructions: str = ""  # style prompt, for providers that support it
    # Called with the character count of every synthesis request actually sent (incl. retries/twins).
    on_request: Callable[[int], None] | None = None


@dataclass
class PCMChunk:
    pcm: bytes  # s16le mono
    sample_rate: int


class TTSProvider(ABC):
    name: str
    model: str

    @abstractmethod
    def stream(self, text_chunks: AsyncIterator[str], request: TTSRequest) -> AsyncIterator[PCMChunk]:
        """Consume text fragments as they are produced and yield audio as soon as it is available."""
