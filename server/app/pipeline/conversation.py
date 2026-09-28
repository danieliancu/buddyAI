"""ConversationPipeline: VAD + STTProvider -> LLMProvider -> SemanticSpeechChunker -> TTSProvider -> Opus.

Runs as one asyncio task per turn. Cancelling that task (tap-to-interrupt / new turn) stops every
stage: sub-tasks live in a TaskGroup and provider sessions are closed in `finally` blocks.
The pipeline knows only the provider interfaces and TurnIO, never WebSockets or vendors.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from app import languages
from app.audio.codec import OpusEncoder, apply_gain
from app.db.repositories import ConversationRepo, PersonaRepo
from app.db.session import session_scope
from app.pipeline.chunker import ChunkerConfig, SemanticSpeechChunker, clean_for_speech, strip_emoji
from app.pipeline.metrics import mono_ms
from app.pipeline.turn import TurnContext, TurnIO, TurnResult
from app.pipeline.vad import EndpointDetector, SpeechProbability, make_probability
from app.providers.base import ProviderError, UsageItem
from app.providers.llm.base import LLMRequest
from app.providers.router import ProviderRouter
from app.providers.tts.base import TTSRequest

log = logging.getLogger(__name__)

UPLINK_RATE = 16000
UPLINK_STALL_S = 3.0  # watch stopped sending audio (e.g. Wi-Fi hiccup) -> end the utterance

MessagesBuilder = Callable[[TurnContext, str], list[dict[str, str]]]


def build_messages_from_db(turn: TurnContext, user_text: str) -> list[dict[str, str]]:
    s = turn.settings
    with session_scope() as db:
        persona = PersonaRepo(db).get(s.persona_id, turn.account_id)
        history = (
            ConversationRepo(db).history(turn.conversation_id, s.history_turns, turn.account_id)
            if turn.conversation_id
            else []
        )
        persona_prompt = persona.system_prompt if persona else "You are a helpful voice assistant."
        past = [(t.user_text, t.assistant_text) for t in history]
    now = datetime.now(ZoneInfo(s.timezone))
    lang = languages.display_name(turn.language)
    if turn.auto_language:
        language_rule = (
            "Reply in the same language the user used in their last message"
            f" (it appears to be {lang}), even if earlier messages were in another language."
        )
    else:
        language_rule = f"Always reply in {lang}."
    system = "\n".join(
        filter(
            None,
            [
                persona_prompt,
                language_rule,
                "Your reply is spoken aloud through a small smartwatch speaker: be brief and conversational, "
                f"at most about {s.max_reply_chars} characters. Plain sentences only: no markdown, no lists, "
                "no emojis, no URLs. Write numbers, dates and units the way they should be spoken.",
                f"Current local date and time: {now:%A %Y-%m-%d %H:%M} ({s.timezone}).",
                s.custom_instructions.strip(),
            ],
        )
    )
    messages = [{"role": "system", "content": system}]
    for u, a in past:
        messages += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
    messages.append({"role": "user", "content": user_text})
    return messages


class ConversationPipeline:
    def __init__(
        self,
        router: ProviderRouter,
        chunker_config: ChunkerConfig,
        vad_factory: Callable[[], SpeechProbability] = make_probability,
        messages_builder: MessagesBuilder = build_messages_from_db,
        downlink_bitrate: int = 32000,
    ) -> None:
        self.router = router
        self.chunker_config = chunker_config
        self.vad_factory = vad_factory
        self.messages_builder = messages_builder
        self.downlink_bitrate = downlink_bitrate

    async def run(self, turn: TurnContext, io: TurnIO) -> TurnResult:
        user_text = await self._listen(turn, io)
        if not user_text:
            await io.send(turn, "state", state="idle")
            return TurnResult("no_speech")
        turn.user_text = user_text
        await io.send(turn, "state", state="thinking")
        await self._reply(turn, io)
        return TurnResult("completed")

    # --- listening: VAD + streaming STT ---------------------------------------------------

    async def _listen(self, turn: TurnContext, io: TurnIO) -> str:
        s = turn.settings
        stt = self.router.stt(turn.language)

        async def on_partial(text: str) -> None:
            await io.send(turn, "stt_result", text=text, final=False)

        detector = EndpointDetector(
            self.vad_factory(), sensitivity=s.vad_sensitivity, max_duration_ms=s.max_listen_s * 1000
        )
        await io.send(turn, "state", state="listening")
        session = await stt.start(turn.language, UPLINK_RATE, on_partial)
        audio_bytes = 0
        reason = "vad"
        arrivals: list[tuple[float, float]] = []  # (audio end ms, arrival mono ms)
        try:
            while True:
                try:
                    item = await asyncio.wait_for(turn.audio_in.get(), UPLINK_STALL_S)
                except asyncio.TimeoutError:
                    item = None
                if turn.cancelled:
                    raise asyncio.CancelledError()
                if item is None:
                    reason = "no_speech" if not detector.speech_started else "vad"
                    turn.marks.speech_end = mono_ms()
                    break
                pcm, arrived = item
                audio_bytes += len(pcm)
                arrivals.append((audio_bytes / 2 / UPLINK_RATE * 1000, arrived))
                await session.send(pcm)
                events = [e for e in detector.feed(pcm) if e.kind != "speech_start"]
                if events:
                    ev = events[0]
                    reason = {"speech_end": "vad"}.get(ev.kind, ev.kind)
                    # Real end of speech = arrival time of the audio that contained it.
                    turn.marks.speech_end = next((t for end, t in arrivals if end >= ev.audio_ms), arrived)
                    break
            turn.listening = False
            await io.send(turn, "listen_stop", reason=reason)
            turn.usage.append(UsageItem("stt", stt.name, stt.model, "audio_second", audio_bytes / 2 / UPLINK_RATE))
            if reason == "no_speech":
                return ""
            text = (await session.finish()).strip()
            turn.marks.stt_final = mono_ms()
            if turn.auto_language and text:
                detected, _confidence = languages.detect(
                    text, prefer=[turn.fallback_language, turn.settings.preferred_language]
                )
                turn.language = detected or turn.fallback_language or languages.DEFAULT_LANGUAGE
            await io.send(turn, "stt_result", text=text, final=True, language=turn.language)
            return text
        finally:
            turn.listening = False
            await session.close()

    # --- reply: LLM -> chunker -> TTS -> Opus ----------------------------------------------

    async def _reply(self, turn: TurnContext, io: TurnIO) -> None:
        s = turn.settings
        llm, model = self.router.llm(s)
        tts_sel = self.router.tts(turn.language, s)
        request = LLMRequest(
            messages=self.messages_builder(turn, turn.user_text),
            model=model,
            max_tokens=max(64, s.max_reply_chars // 2),
            params=self.router.llm_params(),
        )
        deltas: asyncio.Queue[str | None] = asyncio.Queue()
        fragments: asyncio.Queue[str | None] = asyncio.Queue()
        reply_parts: list[str] = []
        tts_chars = 0

        async def pump_llm() -> None:
            turn.marks.llm_request = mono_ms()
            usage_in = usage_out = 0
            try:
                async for chunk in llm.stream(request):
                    if chunk.delta:
                        if turn.marks.llm_first_token is None:
                            turn.marks.llm_first_token = mono_ms()
                        reply_parts.append(chunk.delta)
                        caption = strip_emoji(chunk.delta)
                        if caption:
                            await io.send(turn, "llm_text", delta=caption)
                        await deltas.put(chunk.delta)
                    if chunk.input_tokens is not None:
                        usage_in, usage_out = chunk.input_tokens, chunk.output_tokens or 0
            finally:
                turn.usage.append(UsageItem("llm", llm.name, model, "input_token", usage_in))
                turn.usage.append(UsageItem("llm", llm.name, model, "output_token", usage_out))
                await deltas.put(None)

        async def chunk_text() -> None:
            nonlocal tts_chars
            chunker = SemanticSpeechChunker(turn.language, self.chunker_config)
            try:
                while True:
                    deadline = chunker.next_deadline_ms()
                    timeout = None if deadline is None else max(0.0, (deadline - mono_ms()) / 1000)
                    finished = False
                    try:
                        delta = await asyncio.wait_for(deltas.get(), timeout)
                    except asyncio.TimeoutError:
                        out = chunker.poll(mono_ms())
                    else:
                        if delta is None:
                            out, finished = chunker.finish(), True
                        else:
                            out = chunker.feed(delta, mono_ms())
                    for frag in out:
                        if turn.marks.tts_first_text is None:
                            turn.marks.tts_first_text = mono_ms()
                        tts_chars += len(frag)
                        await fragments.put(frag)
                    if finished:
                        return
            finally:
                await fragments.put(None)

        async def fragment_iter() -> AsyncIterator[str]:
            while (frag := await fragments.get()) is not None:
                yield frag

        encoder = OpusEncoder(turn.downlink_rate, bitrate=self.downlink_bitrate)
        started = False
        gain = 1.0
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(pump_llm())
                tg.create_task(chunk_text())
                tts_request = TTSRequest(
                    voice=tts_sel.voice, language=turn.language, speech_rate=s.speech_rate, instructions=tts_sel.instructions
                )
                async for pcm in tts_sel.provider.stream(fragment_iter(), tts_request):
                    if turn.marks.tts_first_pcm is None:
                        turn.marks.tts_first_pcm = mono_ms()
                    for packet in encoder.encode(apply_gain(pcm.pcm, gain), pcm.sample_rate):
                        if not started:
                            started = True
                            await io.send(turn, "tts_start", sample_rate=turn.downlink_rate, language=turn.language)
                            await io.send(turn, "state", state="speaking")
                        await io.send_audio(turn, packet)
            for packet in encoder.flush():
                await io.send_audio(turn, packet)
        except BaseExceptionGroup as eg:
            raise _first_error(eg) from None
        finally:
            turn.assistant_text = clean_for_speech("".join(reply_parts))
            turn.usage.append(UsageItem("tts", tts_sel.provider.name, tts_sel.provider.model, "character", tts_chars))
        if not reply_parts:
            raise ProviderError("llm", "empty reply")
        if started:
            await io.send(turn, "tts_end")


def _first_error(eg: BaseExceptionGroup) -> BaseException:
    """Unwrap a TaskGroup failure, preferring provider errors (they carry the failing stage)."""
    leaves: list[BaseException] = []

    def walk(e: BaseException) -> None:
        if isinstance(e, BaseExceptionGroup):
            for sub in e.exceptions:
                walk(sub)
        else:
            leaves.append(e)

    walk(eg)
    return next((e for e in leaves if isinstance(e, ProviderError)), leaves[0])
