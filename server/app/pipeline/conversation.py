"""ConversationPipeline: VAD + STTProvider -> LLMProvider -> SemanticSpeechChunker -> TTSProvider -> Opus.

Runs as one asyncio task per turn. Cancelling that task (tap-to-interrupt / new turn) stops every
stage: sub-tasks live in a TaskGroup and provider sessions are closed in `finally` blocks.
The pipeline knows only the provider interfaces and TurnIO, never WebSockets or vendors.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app import languages
from app.audio.codec import OpusEncoder, apply_gain
from app.db.repositories import ConversationRepo, PersonaRepo
from app.db.session import session_scope
from app.db.repositories import ItemRepo, ItemTextError
from app.items import AssistantTools, device_full
from app import notes_edit, reminder_edit
from app.search import SEARCH_INSTRUCTIONS, SEARCH_RULE, SEARCH_TOOL, WebSearch
from app.pipeline.chunker import ChunkerConfig, SemanticSpeechChunker, clean_for_speech, strip_emoji
from app import edit_texts
from app.confirm_words import classify_answer
from app.db.models import Item, VoiceOperation, utcnow
from app.db.repositories import ItemConflictError, VoiceOpRepo
from sqlalchemy.exc import IntegrityError
from app.pipeline.apology import not_understood
from app.voice_context import STORE, VoiceContext, now as voice_now, op_key
from app.voice_tools import (
    AmbiguousLine,
    AmbiguousPerson,
    check_line_ambiguity,
    ToolCallCtx,
    execute_confirmation,
    reask_after_change,
    resolve_line_matches,
)
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
# The end of speech is detected after END_SILENCE_MS of silence (700 ms by default). The first
# SILENCE_SEND_MS of a pause still go to the STT (word endings); the rest is held back and only sent
# if the user speaks again, so the silence that merely confirms the end is never billed.
SILENCE_SEND_MS = 240

# Web search is an on-demand tool with a cache (app/search.py): the model decides when current or local
# information is needed; nothing hidden is attached to every request.
SEARCH_NOTE_CHARS = 350  # search answer kept with a turn for the history
HISTORY_REPLY_CHARS = 300  # older replies are trimmed in the prompt (the stored history is untouched)

MAX_TOOL_ROUNDS = 3  # LLM calls that may end in tool calls; the next one gets no tools and must answer
NOTE_WAIT_S = 30  # note mode: a turn with no speech ends after this (the watch then listens again)
NOTE_SENTENCE_S = 30  # note mode: the longest sentence
NOTE_MAX_TOKENS = 300

MessagesBuilder = Callable[[TurnContext, str], list[dict[str, Any]]]


def search_note(arguments: str, result: str) -> str:
    """'query -> answer' of a web_search call, kept with the turn for the next questions' context."""
    try:
        query = str(json.loads(arguments or "{}").get("query") or "")
        r = json.loads(result)
    except (ValueError, AttributeError):
        return ""
    answer = r.get("answer") if r.get("ok") else "nothing reliable found"
    return f"{query[:150]} -> {str(answer)[:SEARCH_NOTE_CHARS]}"


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
        past = [(t.user_text, t.assistant_text, t.search_note) for t in history]
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
                s.custom_instructions.strip(),
            ],
        )
    )
    # The system prompt stays byte-identical between turns (provider prompt caching); what changes every
    # minute - the clock - goes in a short note just before the question.
    messages = [{"role": "system", "content": system}]
    for u, a, found in past:
        if len(a) > HISTORY_REPLY_CHARS:
            a = a[:HISTORY_REPLY_CHARS].rsplit(" ", 1)[0] + " …"
        messages += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
        if found:  # what the search found, not only what was said aloud ("... in the UEFA Nations League")
            messages.append({"role": "system", "content": f"Web search behind that reply: {found}"})
    messages.append({"role": "system", "content": f"Now: {now:%A %Y-%m-%d %H:%M} ({s.timezone})."})
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
        try:
            user_text = await self._listen(turn, io)
        except ProviderError as exc:
            if exc.stage != "stt":
                raise
            # The question was recorded but not transcribed (provider stalled / failed): apologise and ask
            # to repeat it, instead of the "try again later" screen. Not charged (status error).
            await self._apologise(turn, io)
            return TurnResult("error", "stt_failed", exc.message, notified=True)
        if not user_text:
            await io.send(turn, "state", state="idle")
            return TurnResult("no_speech")
        turn.user_text = user_text
        await io.send(turn, "state", state="thinking")
        await self.respond(turn, io)
        return TurnResult("completed")

    async def respond(self, turn: TurnContext, io: TurnIO) -> None:
        """Answer a transcribed sentence: a pending deletion is decided first (by the server, from the user's
        words - never by the model), then the mode's reply."""
        pending = None
        if turn.account_id is not None:
            if turn.mode in ("note", "reminder"):
                # The note / reminder screen applies everything at once (no confirmations there); a question
                # left open in the dialog is dropped when the user moves to an item's screen.
                if not turn.edit_uid:
                    await asyncio.to_thread(self._edit_item, turn, turn.mode)  # bind the edit session to the item's uid
                STORE.cancel_confirmation(self._voice_ctx(turn))
            else:
                pending = await self._confirmation_gate(turn, io)
            if pending == "handled":
                return
        if turn.mode == "note":
            await self._note_reply(turn, io)
        elif turn.mode == "reminder":
            await self._reminder_reply(turn, io)
        else:
            await self._reply(turn, io)
        if pending == "keep_if_ignored" and turn.edit_outcome != "ignored":
            STORE.cancel_confirmation(self._voice_ctx(turn))  # another instruction: the question is dropped

    # --- voice context and confirmations ------------------------------------------------------

    def _voice_ctx(self, turn: TurnContext) -> VoiceContext:
        assert turn.account_id is not None
        return STORE.get(turn.account_id, turn.device_id, turn.session_id)

    def _call_ctx(self, turn: TurnContext) -> ToolCallCtx:
        assert turn.account_id is not None
        return ToolCallCtx(turn.account_id, turn.device_id, turn.session_id, turn.turn_id, turn.settings.timezone,
                           turn.mode, self._edit_lang(turn), turn.user_text)

    def _answer_languages(self, turn: TurnContext) -> list[str]:
        s = turn.settings
        return [x for x in (turn.language, turn.fallback_language, s.preferred_language, s.language) if x and x != languages.AUTO]

    async def _confirmation_gate(self, turn: TurnContext, io: TurnIO) -> str | None:
        """None: nothing pending (or cancelled) - answer normally. "handled": this sentence was the answer to
        a pending deletion. "keep_if_ignored": edit mode, another sentence - keep the question only if the
        edit model ignores it (background speech)."""
        ctx = self._voice_ctx(turn)
        pc = STORE.pending_confirmation(ctx)
        if pc is None:
            return None
        if pc.mode != turn.mode or pc.edit_uid != turn.edit_uid:
            STORE.cancel_confirmation(ctx)  # another screen or item: the question is dropped
            return None
        strict = turn.mode != "chat"  # the mic stays open in edit modes: "yes, delete" is needed there
        answer = classify_answer(turn.user_text, self._answer_languages(turn), strict=strict)
        lang = turn.language if turn.language != languages.AUTO else (turn.fallback_language or turn.settings.preferred_language or "en")
        if answer == "yes":
            taken = STORE.take_confirmation(ctx, pc.id, turn.turn_id, turn.mode, turn.edit_uid)
            if taken is None:
                return None
            call = self._call_ctx(turn)
            res = await asyncio.to_thread(execute_confirmation, call, taken)
            turn.assistant_text = f"(confirmed: {res.status}) {res.facts}"
            if res.status == "done":
                turn.items_changed = True
                turn.pending_open, turn.pending_uid = res.open, res.open_uid
                if turn.mode == "chat":
                    await self._reply(turn, io, facts=f"Server result: {res.facts}. Tell the user in one short sentence that it is done.")
                return "handled"
            if res.status == "changed":
                new = await asyncio.to_thread(reask_after_change, call, taken)
                if turn.mode == "chat":
                    facts = (f"Server result: nothing was deleted because it changed meanwhile; it is now: {new.facts}. Ask the user to confirm again."
                             if new else "Server result: nothing was deleted because it changed meanwhile. Tell the user and ask what they want.")
                    turn.expect_reply = new is not None
                    await self._reply(turn, io, facts=facts)
                else:
                    await io.send(turn, "llm_display", text=edit_texts.text(lang, "changed"))
                return "handled"
            key = "gone" if res.status == "missing" else "error"
            if turn.mode == "chat":
                await self._reply(turn, io, facts=f"Server result: {res.facts}. Tell the user briefly.")
            else:
                await io.send(turn, "llm_display", text=edit_texts.text(lang, key))
            return "handled"
        if answer == "no":
            STORE.cancel_confirmation(ctx)
            turn.assistant_text = "(confirmation refused: nothing deleted)"
            if turn.mode == "chat":
                await self._reply(turn, io, facts="Server result: the user said no, nothing was deleted. Say so in a few words.")
            else:
                await io.send(turn, "llm_display", text=edit_texts.text(lang, "cancelled"))
            return "handled"
        if answer == "unclear":
            pc.unclear_count += 1
            if pc.unclear_count >= 2:
                STORE.cancel_confirmation(ctx)
                turn.assistant_text = "(confirmation unclear twice: cancelled)"
                if turn.mode == "chat":
                    await self._reply(turn, io, facts="Server result: the answer was not clear, so nothing was deleted. Say so briefly.")
                else:
                    await io.send(turn, "llm_display", text=edit_texts.text(lang, "cancelled"))
                return "handled"
            turn.assistant_text = "(confirmation unclear: asked again)"
            turn.expect_reply = True
            if turn.mode == "chat":
                await self._reply(turn, io, facts=f"Server: still waiting for a clear yes or no to: {pc.facts}. Ask again briefly.")
            else:
                await io.send(turn, "llm_display", text=edit_texts.text(lang, "say_confirm"))
            return "handled"
        # another request
        if turn.mode == "chat":
            STORE.cancel_confirmation(ctx)
            return None
        return "keep_if_ignored"

    def _context_messages(self, turn: TurnContext) -> list[dict[str, Any]]:
        """Server state for the model (refs, open item, pending choice / confirmation) and changes that were
        saved although the user may not have heard it (a reply that failed)."""
        if turn.account_id is None:
            return []
        out = []
        note = STORE.context_note(self._voice_ctx(turn))
        if note:
            out.append({"role": "system", "content": note})
        with session_scope() as db:
            repo = VoiceOpRepo(db)
            since = utcnow() - timedelta(minutes=15)
            missed = [o for o in repo.unreported(turn.account_id, turn.device_id, since)
                      if not (o.session_id == turn.session_id and o.turn_id == turn.turn_id)]
            if missed:
                out.append({"role": "system", "content": "Saved earlier although the reply may not have reached the "
                            "user (do not do these again unless the user explicitly asks again): "
                            + "; ".join(o.summary for o in missed[-5:])})
                repo.acknowledge([o.id for o in missed])
        return out

    async def _apologise(self, turn: TurnContext, io: TurnIO) -> None:
        """'Sorry, I didn't catch that. Could you say it again?' - spoken in chat mode, shown in edit modes."""
        s = turn.settings
        guess = turn.language if turn.language != languages.AUTO else (turn.fallback_language or s.preferred_language)
        language, text = not_understood(guess)
        turn.assistant_text = text
        if turn.mode != "chat":
            await io.send(turn, "llm_display", text=text)
            return
        await io.send(turn, "llm_text", delta=text)
        try:
            tts_sel = self.router.tts(language, s)
            encoder = OpusEncoder(turn.downlink_rate, bitrate=self.downlink_bitrate)

            async def one() -> AsyncIterator[str]:
                yield text

            started = False
            request = TTSRequest(voice=tts_sel.voice, language=language, speech_rate=s.speech_rate,
                                 instructions=tts_sel.instructions)
            async for pcm in tts_sel.provider.stream(one(), request):
                for packet in encoder.encode(pcm.pcm, pcm.sample_rate):
                    if not started:
                        started = True
                        await io.send(turn, "tts_start", sample_rate=turn.downlink_rate, language=language)
                        await io.send(turn, "state", state="speaking")
                    await io.send_audio(turn, packet)
            for packet in encoder.flush():
                await io.send_audio(turn, packet)
            turn.usage.append(UsageItem("tts", tts_sel.provider.name, tts_sel.provider.model, "character", len(text)))
        except ProviderError as exc:  # the text is already on the screen
            log.warning("apology not spoken: %s", exc)

    # --- listening: VAD + streaming STT ---------------------------------------------------

    async def _listen(self, turn: TurnContext, io: TurnIO) -> str:
        s = turn.settings
        stt = self.router.stt(turn.language)

        async def on_partial(text: str) -> None:
            await io.send(turn, "stt_result", text=text, final=False)

        note = turn.mode in ("note", "reminder")  # edit modes: one short sentence per turn
        detector = EndpointDetector(
            self.vad_factory(),
            sensitivity=s.vad_sensitivity,
            # Note mode: the watch starts the next sentence right away, so a quiet stretch just ends
            # this (free) turn; one sentence is at most NOTE_SENTENCE_S long.
            no_speech_timeout_ms=(NOTE_WAIT_S if note else s.wait_for_speech_s) * 1000,
            max_duration_ms=(NOTE_SENTENCE_S if note else s.max_listen_s) * 1000,
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
        held: list[bytes] = []  # pause audio not sent (yet)
        preroll_limit = UPLINK_RATE * 2 * PREROLL_MS // 1000
        timeline_bytes = 0  # all audio received (VAD timeline)
        audio_bytes = 0  # audio sent to the STT (billed)
        reason = "vad"
        arrivals: list[tuple[float, float]] = []  # (audio end ms, arrival mono ms)
        stt_billed = False
        try:
            while True:
                try:
                    item = await asyncio.wait_for(turn.audio_in.get(), UPLINK_STALL_S)
                except asyncio.TimeoutError:
                    item = None
                if turn.cancelled:
                    raise asyncio.CancelledError()
                if item is None:  # uplink stalled, or the watch's stop button (listen_end)
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
                elif detector.silence_ms > SILENCE_SEND_MS:
                    held.append(pcm)  # sent only if speech resumes
                else:
                    for chunk in held:
                        await session.send(chunk)
                        audio_bytes += len(chunk)
                    held.clear()
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
            stt_billed = True
            turn.usage.append(UsageItem("stt", stt.name, stt.model, "audio_second", audio_bytes / 2 / UPLINK_RATE))
            if reason == "no_speech" or session is None:
                return ""
            text = (await session.finish()).strip()
            turn.marks.stt_final = mono_ms()
            fallback = getattr(session, "fallback_usage", None)  # (model, seconds) when re-transcribed
            if fallback:
                turn.usage.append(UsageItem("stt", stt.name, fallback[0], "audio_second", fallback[1]))
            if turn.auto_language and text:
                detected, _confidence = languages.detect(
                    text, prefer=[turn.fallback_language, turn.settings.preferred_language]
                )
                turn.language = detected or turn.fallback_language or languages.DEFAULT_LANGUAGE
            await io.send(turn, "stt_result", text=text, final=True, language=turn.language)
            return text
        finally:
            turn.listening = False
            if not stt_billed and audio_bytes:  # cancelled while listening: the audio sent is still paid
                turn.usage.append(UsageItem("stt", stt.name, stt.model, "audio_second", audio_bytes / 2 / UPLINK_RATE))
            if opening is not None:  # never used (no speech, or cancelled)
                opening.cancel()
                with contextlib.suppress(BaseException):
                    session = await opening
            if session is not None:
                await session.close()

    # --- reply: LLM -> chunker -> TTS -> Opus ----------------------------------------------

    async def _reply(self, turn: TurnContext, io: TurnIO, facts: str | None = None) -> None:
        """The spoken reply. `facts`: a server result to put into words (no tools: the model can only report
        what the server did)."""
        s = turn.settings
        llm, model = self.router.llm(s)
        tts_sel = self.router.tts(turn.language, s)
        web_search = self.router.web_search(s, llm)
        searcher = None
        if web_search is not None and hasattr(llm, "web_search"):
            hosted = self.router.hosted_search_tool(web_search)
            city = (hosted.get("user_location") or {}).get("city", "")

            async def run_search(query: str, location: str, language: str):
                return await llm.web_search(  # type: ignore[attr-defined]
                    query, location, language, model=model, tool=hosted, params=self.router.llm_params(),
                    instructions=SEARCH_INSTRUCTIONS, timezone_name=s.timezone,
                )

            searcher = WebSearch(run_search, web_search.get("cache_ttl_s"), provider=llm.name, model=model)
            turn.searcher, turn.search_city = searcher, city
        messages = self.messages_builder(turn, turn.user_text)
        tool_defs = (self.tools.definitions(turn.account_id) if self.tools else []) + ([SEARCH_TOOL] if searcher else [])
        tool_defs = tool_defs or None
        # server state goes right before the user's sentence (the cached system prompt stays identical)
        extra_msgs = [{"role": "system", "content": facts}] if facts else self._context_messages(turn)
        if facts:
            tool_defs = None
        if extra_msgs and messages:
            messages = [*messages[:-1], *extra_msgs, messages[-1]]
        extra_rules = [SEARCH_RULE] if searcher else []
        if self.tools:
            extra_rules += self.tools.rules(turn.account_id)
        if extra_rules and messages and messages[0]["role"] == "system":
            messages[0] = {**messages[0], "content": "\n".join([messages[0]["content"], *extra_rules])}
        request = LLMRequest(
            messages=messages,
            model=model,
            max_tokens=max(64, s.max_reply_chars // 2),
            params=self.router.llm_params(),
            web_search=None,  # searching goes through the web_search tool (app/search.py)
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
            usage_in = usage_cached = usage_out = searches = 0
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
                            usage_cached += min(chunk.cached_input_tokens, chunk.input_tokens)
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
                    # notes / reminders / settings: no large value on screen (a search answer may have one)
                    tools_used = tools_used or any(c.name != SEARCH_TOOL["name"] for c in calls)
                    for c in calls:
                        msgs.append(
                            {"role": "tool", "tool_call_id": c.id, "content": await self._run_tool(turn, c)}
                        )
                rest = display.flush()
                if rest:
                    await emit_text(rest)
            finally:
                turn.usage.append(UsageItem("llm", llm.name, model, "input_token", usage_in - usage_cached))
                turn.usage.append(UsageItem("llm", llm.name, model, "cached_input_token", usage_cached))
                turn.usage.append(UsageItem("llm", llm.name, model, "output_token", usage_out))
                if searches:  # hosted search inside the conversation call (legacy path)
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

        sent_chars = 0  # characters actually sent to the TTS provider (hedged twins included)

        def on_tts_request(chars: int) -> None:
            nonlocal sent_chars
            sent_chars += chars

        encoder = OpusEncoder(turn.downlink_rate, bitrate=self.downlink_bitrate)
        started = False
        gain = 1.0
        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(pump_llm())
                tg.create_task(chunk_text())
                tts_request = TTSRequest(
                    voice=tts_sel.voice, language=turn.language, speech_rate=s.speech_rate, instructions=tts_sel.instructions,
                    on_request=on_tts_request,
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
            # Providers that report their requests are billed on what was really sent (an aborted reply
            # is not charged for text never synthesized; a hedged twin request is). Others: text queued.
            billed = sent_chars if getattr(tts_sel.provider, "reports_requests", False) else tts_chars
            turn.usage.append(UsageItem("tts", tts_sel.provider.name, tts_sel.provider.model, "character", billed))
        if not reply_parts:
            raise ProviderError("llm", "empty reply")
        if display.value is None and not tools_used and asks_for_value(turn.user_text):
            value = guess_value(turn.assistant_text)  # the model forgot the [[value]] tag
            if value and not echoes_question(value, turn.user_text):
                await io.send(turn, "llm_display", text=value)
        if started:
            await io.send(turn, "tts_end")


    # --- note edit mode: one sentence -> line operations on one note -----------------------------

    def _edit_item(self, turn: TurnContext, kind: str) -> Item | None:
        """The item being edited: by the uid the gateway bound to this edit session (or by number)."""
        assert turn.account_id is not None
        with session_scope() as db:
            repo = ItemRepo(db)
            if turn.edit_uid:
                it = repo.get_by_uid(turn.account_id, turn.edit_uid)
            else:
                it = repo.get(turn.account_id, kind, turn.note_number) if turn.note_number is not None else None
                if it is not None:
                    turn.edit_uid = it.uid
            return it if it is not None and it.kind == kind else None

    async def _edit_llm(self, turn: TurnContext, system: str, context: str, tool: dict[str, Any]) -> tuple[ToolCall | None, str]:
        """One edit-mode model call (low-cost model, minimal prompt, one tool). The previous question asked in
        this edit session is added, so a short answer ("line 5", "the second one") has its context."""
        ctx = self._voice_ctx(turn)
        follow = ctx.edit_followup
        messages = [{"role": "system", "content": system}, {"role": "system", "content": context}]
        if follow and follow.get("uid") == turn.edit_uid and follow.get("until", 0) > voice_now():
            messages.append({"role": "system", "content": f"You asked: {follow['question']!r} after the user said: {follow['user_text']!r}. "
                             "This sentence may answer it."})
        ctx.edit_followup = None
        messages.append({"role": "user", "content": turn.user_text})
        llm, model = self.router.llm(turn.settings.model_copy(update={"llm_model": ""}))
        request = LLMRequest(messages=messages, model=model, max_tokens=NOTE_MAX_TOKENS,
                             params=self.router.llm_params(), tools=[tool])
        usage_in = usage_cached = usage_out = 0
        calls: list[ToolCall] = []
        reply: list[str] = []
        turn.marks.llm_request = mono_ms()
        try:
            async for chunk in llm.stream(request):
                if chunk.delta:
                    reply.append(chunk.delta)
                if chunk.input_tokens is not None:
                    usage_in += chunk.input_tokens
                    usage_cached += min(chunk.cached_input_tokens, chunk.input_tokens)
                    usage_out += chunk.output_tokens or 0
                if chunk.tool_calls:
                    calls = chunk.tool_calls
        finally:
            turn.usage.append(UsageItem("llm", llm.name, model, "input_token", usage_in - usage_cached))
            turn.usage.append(UsageItem("llm", llm.name, model, "cached_input_token", usage_cached))
            turn.usage.append(UsageItem("llm", llm.name, model, "output_token", usage_out))
        return next((c for c in calls if c.name == tool["name"]), None), strip_emoji("".join(reply)).strip()[:80]

    async def _edit_no_call(self, turn: TurnContext, io: TurnIO, reply: str) -> None:
        """No tool call: background speech (ignored), another item (hint) or a short question."""
        word = reply.strip(" .").upper()
        if word == notes_edit.IGNORE:
            turn.assistant_text = "(ignored: not an instruction)"
            turn.edit_outcome = "ignored"
            return
        lang = self._edit_lang(turn)
        if word == "OTHER":
            turn.assistant_text = "(another item: not applied)"
            await io.send(turn, "llm_display", text=edit_texts.text(lang, "other_item"))
            return
        turn.assistant_text = reply
        if reply:
            self._voice_ctx(turn).edit_followup = {"question": reply, "user_text": turn.user_text, "uid": turn.edit_uid,
                                                   "until": voice_now() + 120}
            await io.send(turn, "llm_display", text=reply)

    def _edit_lang(self, turn: TurnContext) -> str:
        return turn.language if turn.language != languages.AUTO else (turn.fallback_language or turn.settings.preferred_language or "en")

    def _edit_op(self, turn: TurnContext, tool: str, args: dict[str, Any]) -> VoiceOperation:
        assert turn.account_id is not None
        return VoiceOperation(op_key=op_key(turn.account_id, turn.device_id, turn.session_id, turn.turn_id, tool, args),
                              account_id=turn.account_id, device_id=turn.device_id, session_id=turn.session_id,
                              turn_id=turn.turn_id, tool=tool, summary=f"{tool}: {json.dumps(args, ensure_ascii=False)[:250]}",
                              result="{}")

    # --- note edit mode: one sentence -> line operations on one note ----------------------------------

    async def _note_reply(self, turn: TurnContext, io: TurnIO) -> None:
        """Apply one sentence to the note. Low-cost model, minimal prompt, one tool, no TTS. Line deletions
        are prepared and confirmed by the user in the next sentence."""
        if turn.account_id is None or (turn.note_number is None and not turn.edit_uid):
            raise ProviderError("llm", "note mode needs an owner and a note")
        account_id = turn.account_id
        it = self._edit_item(turn, "note")
        if it is None:
            await io.send(turn, "llm_display", text="?")
            return
        version, lines = it.version, notes_edit.note_lines(it.text)
        call, reply = await self._edit_llm(turn, notes_edit.NOTE_SYSTEM, f"Note #{it.number}:\n{notes_edit.numbered(lines)}",
                                           notes_edit.NOTE_EDIT_TOOL)
        if call is None:
            await self._edit_no_call(turn, io, reply)
            return
        lang = self._edit_lang(turn)
        try:
            ops = notes_edit.parse_ops(call.arguments)
            undo = notes_edit.undo_get(account_id, it.uid, version)
            if not any(isinstance(o, dict) and o.get("op") == "undo" for o in ops):
                ops = resolve_line_matches(lines, ops)
                check_line_ambiguity(lines, ops, turn.user_text)  # "the milk" on two lines: ask which
            new_lines, highlight = notes_edit.apply_note_ops(lines, ops, undo)
        except AmbiguousLine as exc:
            lang = self._edit_lang(turn)
            options = [f"{edit_texts.text(lang, 'line', n=str(x['line']))} \u00ab{x['text'][:24]}\u00bb" for x in exc.lines[:3]]
            await self._edit_no_call(turn, io, edit_texts.which_of(lang, options))
            return
        except (ValueError, ItemTextError) as exc:
            log.info("note edit refused: %s", exc)
            turn.assistant_text = f"(not applied: {exc})"
            await io.send(turn, "llm_display", text="?")
            return
        try:
            with session_scope() as db:
                it2 = ItemRepo(db).set_note_text(it, "\n".join(new_lines), expected_version=version,
                                                op=self._edit_op(turn, "note_edit", {"uid": it.uid, "ops": ops}))
                view = device_full(it2, turn.settings.timezone)
        except ItemConflictError:
            turn.assistant_text = "(not applied: the note changed meanwhile)"
            await io.send(turn, "llm_display", text=edit_texts.text(lang, "conflict"))
            return
        except IntegrityError:  # the same operation already ran (a technical retry)
            turn.assistant_text = "(already applied)"
            return
        notes_edit.undo_set(account_id, it.uid, lines, it2.version)
        STORE.refresh_version(self._voice_ctx(turn), it2.uid, it2.version)
        log.info("note #%s edit %s", it.number, call.arguments[:200])
        turn.assistant_text = f"note_edit {call.arguments[:300]}"
        turn.items_changed = True
        turn.changed_line = highlight
        # changed_line: 0 = the title, n = numbered line n (the watch's numbers), null = none
        turn.pending_open, turn.pending_uid = {"item": {**view, "changed_line": highlight}}, it2.uid

    # --- reminder edit mode: one sentence -> changes to one reminder ----------------------------

    async def _reminder_reply(self, turn: TurnContext, io: TurnIO) -> None:
        """Apply one sentence to the reminder. Low-cost model, minimal prompt, one tool, no TTS. A deletion or
        a removed detail is prepared and confirmed by the user in the next sentence."""
        if turn.account_id is None or (turn.note_number is None and not turn.edit_uid):
            raise ProviderError("llm", "reminder mode needs an owner and a reminder")
        account_id, tz = turn.account_id, turn.settings.timezone
        it = self._edit_item(turn, "reminder")
        if it is None:
            await io.send(turn, "llm_display", text="?")
            return
        version = it.version
        call, reply = await self._edit_llm(turn, reminder_edit.REMINDER_SYSTEM, reminder_edit.context(it, tz),
                                           reminder_edit.REMINDER_EDIT_TOOL)
        if call is None:
            await self._edit_no_call(turn, io, reply)
            return
        lang = self._edit_lang(turn)
        op = self._edit_op(turn, "reminder_edit", {"uid": it.uid, "args": call.arguments})
        try:
            result = await asyncio.to_thread(reminder_edit.apply, account_id, it.uid, version, call.arguments, tz, op)
        except ItemConflictError:
            turn.assistant_text = "(not applied: the reminder changed meanwhile)"
            await io.send(turn, "llm_display", text=edit_texts.text(lang, "conflict"))
            return
        except IntegrityError:
            turn.assistant_text = "(already applied)"
            return
        except AmbiguousPerson as exc:  # "without Mihai" with two Mihais: ask which
            await self._edit_no_call(turn, io, edit_texts.which_of(lang, exc.names[:3]))
            return
        except ValueError as exc:
            log.info("reminder edit refused: %s", exc)
            turn.assistant_text = f"(not applied: {exc})"
            await io.send(turn, "llm_display", text="?")
            return
        log.info("reminder #%s edit %s", it.number, call.arguments[:200])
        if result.deleted:  # deleted from its own screen: back to the reminders list
            turn.assistant_text = f"reminder_edit {call.arguments[:300]}"
            turn.items_changed = True
            turn.pending_open = {"list": "reminder"}
            return
        turn.assistant_text = f"reminder_edit {call.arguments[:300]}"
        turn.items_changed = True
        if result.version is not None:
            STORE.refresh_version(self._voice_ctx(turn), it.uid, result.version)
        turn.pending_open, turn.pending_uid = {"item": result.view}, it.uid

    async def _run_tool(self, turn: TurnContext, call: ToolCall) -> str:
        label = call.name
        try:
            kind = json.loads(call.arguments or "{}").get("kind")
            if isinstance(kind, str) and kind:
                label = f"{call.name}:{kind[:16]}"
        except (ValueError, AttributeError):
            pass
        if label not in turn.tools_used:
            turn.tools_used.append(label)
        searcher = turn.searcher
        if call.name == SEARCH_TOOL["name"] and searcher is not None:
            # Answers are in English whatever the user's language: the reply model translates, and one
            # cached answer serves every language.
            outcome = await searcher.run(
                call.arguments, account_id=turn.account_id, device_id=turn.device_id, tz=turn.settings.timezone,
                language="en", default_location=turn.search_city,
            )
            turn.usage.extend(outcome.usage)
            turn.search_note = search_note(call.arguments, outcome.result)
            log.info("web_search(%s) -> %s", call.arguments[:120], "cache hit" if outcome.cache_hit else "searched")
            return outcome.result
        assert self.tools is not None
        call_ctx = self._call_ctx(turn) if turn.account_id is not None else None
        out = await asyncio.to_thread(
            self.tools.execute, turn.account_id, turn.settings.timezone, call.name, call.arguments, turn.device_id,
            call_ctx,
        )
        log.info("tool %s(%s) -> %s", call.name, call.arguments, out.result[:200])
        turn.items_changed |= out.changed
        turn.settings_changed |= out.settings_changed
        turn.expect_reply |= out.awaits_answer
        if out.open is not None:
            turn.pending_open, turn.pending_uid = out.open, out.open_uid
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

