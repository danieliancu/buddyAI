"""ConversationPipeline: VAD + STTProvider -> LLMProvider -> SemanticSpeechChunker -> TTSProvider -> Opus.

Runs as one asyncio task per turn. Cancelling that task (tap-to-interrupt / new turn) stops every
stage: sub-tasks live in a TaskGroup and provider sessions are closed in `finally` blocks.
The pipeline knows only the provider interfaces and TurnIO, never WebSockets or vendors.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app import languages
from app.audio.codec import OpusEncoder, apply_gain
from app.db.repositories import ConversationRepo, PersonaRepo
from app.db.session import session_scope
from app.items import AssistantTools
from app.pipeline.chunker import ChunkerConfig, SemanticSpeechChunker, clean_for_speech, strip_emoji
from app.pipeline.display_tag import DISPLAY_RULE, DisplayTagFilter, asks_for_value, echoes_question, guess_value
from app.pipeline.metrics import mono_ms
from app.pipeline.turn import TurnContext, TurnIO, TurnResult
from app.pipeline.vad import EndpointDetector, SpeechProbability, make_probability
from app.providers.base import ProviderError, UsageItem
from app.providers.llm.base import LLMRequest, ToolCall
from app.providers.router import ProviderRouter
from app.providers.stt.base import STTSession
from app.providers.tts.base import TTSRequest

log = logging.getLogger(__name__)

UPLINK_RATE = 16000
UPLINK_STALL_S = 3.0  # watch stopped sending audio (e.g. Wi-Fi hiccup) -> end the utterance
PREROLL_MS = 500  # audio kept from before the detected first word, so its start is not clipped

# Each web search costs money and adds delay: search only for current or local facts.
WEB_SEARCH_RULE = (
    "You can search the web, but only when the answer depends on current or local information you cannot "
    "know: today's weather, news, live traffic or transport, opening hours, current prices, recent results, "
    "a specific local business. For those, search instead of saying you have no real-time access. Do NOT "
    "search for general knowledge, definitions, maths, history, science, advice, jokes, small talk, notes "
    "or reminders: answer those directly. Search at most once per question. Say the answer only: never "
    "mention sources, websites or links."
)

MAX_TOOL_ROUNDS = 3  # LLM calls that may end in tool calls; the next one gets no tools and must answer

MessagesBuilder = Callable[[TurnContext, str], list[dict[str, Any]]]


def build_messages_from_db(turn: TurnContext, user_text: str) -> list[dict[str, Any]]:
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
                DISPLAY_RULE,
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
        tools: AssistantTools | None = None,
    ) -> None:
        self.router = router
        self.chunker_config = chunker_config
        self.vad_factory = vad_factory
        self.messages_builder = messages_builder
        self.downlink_bitrate = downlink_bitrate
        self.tools = tools

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
            self.vad_factory(),
            sensitivity=s.vad_sensitivity,
            no_speech_timeout_ms=s.wait_for_speech_s * 1000,
            max_duration_ms=s.max_listen_s * 1000,
        )
        await io.send(turn, "state", state="listening")
        # Silence before the first word never reaches the (billed) STT: audio is sent only once the
        # local VAD hears speech, starting with the last PREROLL_MS. The connection itself is free, so
        # it opens now: its handshake (~1.5 s, sometimes much longer) overlaps the wait for speech.
        opening: asyncio.Task[STTSession] | None = asyncio.create_task(
            stt.start(turn.language, UPLINK_RATE, on_partial)
        )
        session = None
        preroll: list[bytes] = []
        preroll_limit = UPLINK_RATE * 2 * PREROLL_MS // 1000
        timeline_bytes = 0  # all audio received (VAD timeline)
        audio_bytes = 0  # audio sent to the STT (billed)
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
                timeline_bytes += len(pcm)
                arrivals.append((timeline_bytes / 2 / UPLINK_RATE * 1000, arrived))
                all_events = detector.feed(pcm)
                if session is None:
                    preroll.append(pcm)
                    while sum(map(len, preroll)) - len(preroll[0]) >= preroll_limit:
                        preroll.pop(0)
                    if detector.speech_started:
                        task, opening = opening, None
                        try:
                            session = await task
                        except ProviderError as exc:
                            log.warning("turn %s stt: %s, retrying once", turn.turn_id, exc)
                            session = await stt.start(turn.language, UPLINK_RATE, on_partial)
                        for chunk in preroll:
                            await session.send(chunk)
                            audio_bytes += len(chunk)
                        preroll.clear()
                else:
                    await session.send(pcm)
                    audio_bytes += len(pcm)
                events = [e for e in all_events if e.kind != "speech_start"]
                if events:
                    ev = events[0]
                    reason = {"speech_end": "vad"}.get(ev.kind, ev.kind)
                    # Real end of speech = arrival time of the audio that contained it.
                    turn.marks.speech_end = next((t for end, t in arrivals if end >= ev.audio_ms), arrived)
                    break
            turn.listening = False
            await io.send(turn, "listen_stop", reason=reason)
            turn.usage.append(UsageItem("stt", stt.name, stt.model, "audio_second", audio_bytes / 2 / UPLINK_RATE))
            if reason == "no_speech" or session is None:
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
            if opening is not None:  # never used (no speech, or cancelled)
                opening.cancel()
                with contextlib.suppress(BaseException):
                    session = await opening
            if session is not None:
                await session.close()

    # --- reply: LLM -> chunker -> TTS -> Opus ----------------------------------------------

    async def _reply(self, turn: TurnContext, io: TurnIO) -> None:
        s = turn.settings
        llm, model = self.router.llm(s)
        tts_sel = self.router.tts(turn.language, s)
        web_search = self.router.web_search(s, llm)
        messages = self.messages_builder(turn, turn.user_text)
        tool_defs = self.tools.definitions(turn.account_id) if self.tools else None
        extra_rules = [WEB_SEARCH_RULE] if web_search is not None else []
        if self.tools:
            extra_rules += self.tools.rules(turn.account_id)
        if extra_rules and messages and messages[0]["role"] == "system":
            messages[0] = {**messages[0], "content": "\n".join([messages[0]["content"], *extra_rules])}
        request = LLMRequest(
            messages=messages,
            model=model,
            max_tokens=max(64, s.max_reply_chars // 2),
            params=self.router.llm_params(),
            web_search=web_search,
        )
        deltas: asyncio.Queue[str | None] = asyncio.Queue()
        fragments: asyncio.Queue[str | None] = asyncio.Queue()
        reply_parts: list[str] = []
        tts_chars = 0

        display = DisplayTagFilter()
        tools_used = False

        async def emit_text(delta: str) -> None:
            reply_parts.append(delta)
            caption = strip_emoji(delta)
            if caption:
                await io.send(turn, "llm_text", delta=caption)
            await deltas.put(delta)

        async def pump_llm() -> None:
            turn.marks.llm_request = mono_ms()
            usage_in = usage_out = searches = 0
            nonlocal tools_used
            msgs = list(messages)
            try:
                for round_no in range(MAX_TOOL_ROUNDS + 1):
                    last = round_no == MAX_TOOL_ROUNDS
                    req = replace(request, messages=msgs, tools=None if last else tool_defs)
                    calls: list[ToolCall] | None = None
                    round_text: list[str] = []
                    async for chunk in llm.stream(req):
                        had_value = display.value is not None
                        delta = display.feed(chunk.delta) if chunk.delta else ""
                        if (
                            display.value is not None
                            and not had_value
                            and not tools_used
                            and not echoes_question(display.value, turn.user_text)
                        ):
                            await io.send(turn, "llm_display", text=display.value)  # e.g. "21°C", shown large
                        if delta:
                            if turn.marks.llm_first_token is None:
                                turn.marks.llm_first_token = mono_ms()
                            if round_no and not round_text and reply_parts and not reply_parts[-1].endswith(" "):
                                delta = " " + delta  # text after a tool call continues the same reply
                            round_text.append(delta)
                            await emit_text(delta)
                        if chunk.input_tokens is not None:
                            usage_in += chunk.input_tokens
                            usage_out += chunk.output_tokens or 0
                            searches += chunk.web_searches
                        if chunk.tool_calls:
                            calls = chunk.tool_calls
                    if not calls or not tool_defs or last:
                        break
                    msgs.append(
                        {
                            "role": "assistant",
                            "content": "".join(round_text) or None,
                            "tool_calls": [
                                {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                                for c in calls
                            ],
                        }
                    )
                    tools_used = True  # notes / reminders: no large value on screen
                    for c in calls:
                        msgs.append(
                            {"role": "tool", "tool_call_id": c.id, "content": await self._run_tool(turn, c)}
                        )
                rest = display.flush()
                if rest:
                    await emit_text(rest)
            finally:
                turn.usage.append(UsageItem("llm", llm.name, model, "input_token", usage_in))
                turn.usage.append(UsageItem("llm", llm.name, model, "output_token", usage_out))
                turn.usage.append(UsageItem("llm", llm.name, model, "web_search_call", searches))
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
        if display.value is None and not tools_used and asks_for_value(turn.user_text):
            value = guess_value(turn.assistant_text)  # the model forgot the [[value]] tag
            if value and not echoes_question(value, turn.user_text):
                await io.send(turn, "llm_display", text=value)
        if started:
            await io.send(turn, "tts_end")


    async def _run_tool(self, turn: TurnContext, call: ToolCall) -> str:
        assert self.tools is not None
        out = await asyncio.to_thread(
            self.tools.execute, turn.account_id, turn.settings.timezone, call.name, call.arguments, turn.device_id
        )
        log.info("tool %s(%s) -> %s", call.name, call.arguments, out.result[:200])
        turn.items_changed |= out.changed
        turn.settings_changed |= out.settings_changed
        if out.open is not None:
            turn.pending_open = out.open
        return out.result


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
