"""STTProvider interface: streaming speech-to-text for one utterance."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable

PartialCallback = Callable[[str], Awaitable[None]]


class STTSession(ABC):
    """One utterance. send() PCM s16le mono 16 kHz as it arrives, then finish() for the final text."""

    @abstractmethod
    async def send(self, pcm: bytes) -> None: ...

    @abstractmethod
    async def finish(self) -> str:
        """Signal end of audio and return the final transcript."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources; safe to call more than once (used on cancellation)."""


class STTProvider(ABC):
    name: str
    model: str

    @abstractmethod
    async def start(self, language: str, sample_rate: int, on_partial: PartialCallback | None) -> STTSession: ...
