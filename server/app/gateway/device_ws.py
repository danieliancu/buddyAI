"""/ws/device: one DeviceConnection per watch session (PROTOCOL.md)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app import languages, usage_notices, usage_ops
from app.audio.codec import OpusDecoder
from app.config import get_settings, load_providers_config
from app.db.models import Account, utcnow
from app.db.repositories import ConversationRepo, DeviceRepo, ItemRepo, PersonaRepo, SettingsRepo, TurnRepo, VoiceOpRepo
from app.db.session import session_scope
from app.device_settings import DEVICE_EDITABLE, DeviceSettings, device_view
from app.gateway.hub import DeviceHub, PairingError
from app.incidents import events as diag_events, service as diagnostics
from app import cost_monitor
from app.interactions import counts_as_interaction
from app.items import KINDS, device_full
from app.voice_context import STORE
from app.gateway.protocol import KIND_UPLINK, AudioFrame, Envelope, ProtocolError, parse_message
from app.pipeline.conversation import ConversationPipeline
from app.pipeline.metrics import mono_ms
from app.pipeline.turn import TurnContext, TurnResult
from app.providers.base import ProviderError
from app.reminders import deliver_due
from app.security import hash_device_token, is_valid_pairing_code

log = logging.getLogger(__name__)
TRY_LATER = "Ola can't answer right now. Please try again a little later."
router = APIRouter()

MAX_RECENT_TURNS = 8
ABORT_REASONS = ("user_tap", "timeout", "error")
ABORT_TEXT = {
    "user_tap": "Stopped on the watch",
    "timeout": "The watch stopped waiting for the answer (30 s); not charged",
    "error": "Stopped by the watch after an error",
    "connection_lost": "The connection closed during the turn",
}


def abort_text(result: TurnResult, turn: TurnContext) -> str | None:
    """Why an aborted turn stopped (stored in turns.error, shown in the admin)."""
    if result.status != "aborted":
        return None
    return ABORT_TEXT.get(turn.abort_reason or "", "Aborted")


class DeviceConnection:
    def __init__(self, ws: WebSocket, hub: DeviceHub, pipeline: ConversationPipeline) -> None:
        self.ws = ws
        self.hub = hub
        self.pipeline = pipeline
        self.env = Envelope()
        self.device_id = ""
        self.authenticated = False
        self.settings = DeviceSettings()
        self.downlink_rate = 16000
        self.ui_state = "idle"
        self.active: TurnContext | None = None
        self.last_turn_id = 0
        self.recent: dict[int, TurnContext] = {}
        self.last_language: str | None = None  # last detected reply language in this session
        self.account_id: int | None = None  # owner of this watch
        self._send_lock = asyncio.Lock()
        self._decoder: OpusDecoder | None = None
        self._closed = False
        self._fw = ""
        # (kind, number) -> uid of the item the server last showed / fired under that number on this watch
        self._shown: dict[tuple[str, int], str] = {}
        self._timed_out = False  # the session ended because the watch went silent
        self._close_code: int | None = None  # the watch's websocket close code (1006 = no close frame)
        self._crash: BaseException | None = None  # an unexpected exception ended the session

    # --- sending (TurnIO + generic) --------------------------------------------------------

    async def send_json(self, type_: str, turn_id: int | None = None, **fields: Any) -> bool:
        if self._closed or self.ws.client_state != WebSocketState.CONNECTED:
            return False
        async with self._send_lock:
            try:
                await self.ws.send_text(self.env.build(type_, turn_id, **fields))
                return True
            except Exception:  # connection dropped mid-send
                return False

    def _is_live(self, turn: TurnContext) -> bool:
        return turn is self.active and not turn.cancelled

    @staticmethod
    def _stop_if_cancelled(turn: TurnContext) -> None:
        # Belt and braces: asyncio.wait_for on Python 3.11 can swallow a cancel() that races with a
        # completed await. Any output attempt from a cancelled turn therefore stops the pipeline.
        if turn.cancelled:
            raise asyncio.CancelledError()

    async def send(self, turn: TurnContext, type_: str, **fields: Any) -> bool:
        self._stop_if_cancelled(turn)
        if not self._is_live(turn):
            return False  # stale-turn protection: nothing from an old turn reaches the watch
        if type_ == "state":
            self.ui_state = fields["state"]
            self.hub.publish({"type": "device_state", "device_id": self.device_id, "state": self.ui_state})
        return await self.send_json(type_, turn.turn_id, **fields)

    async def send_audio(self, turn: TurnContext, opus_packet: bytes) -> bool:
        self._stop_if_cancelled(turn)
        if not self._is_live(turn) or self._closed:
            return False
        frame = AudioFrame(kind=0x02, turn_id=turn.turn_id, frame_seq=turn.downlink_seq, payload=opus_packet)
        async with self._send_lock:
            try:
                await self.ws.send_bytes(frame.pack())
            except Exception:
                return False
        if turn.downlink_seq == 0:
            turn.marks.first_frame_sent = mono_ms()
        turn.downlink_seq += 1
        return True

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if not self._closed:
            self._closed = True
            with contextlib.suppress(Exception):
                await self.ws.close(code=code, reason=reason)

    # --- main loop ---------------------------------------------------------------------------

    async def run(self) -> None:
        await self.ws.accept()
        timeout = get_settings().session_idle_timeout_s
        try:
            while True:
                msg = await asyncio.wait_for(self.ws.receive(), timeout)
                if msg["type"] == "websocket.disconnect":
                    self._close_code = msg.get("code")
                    break
                if msg.get("bytes") is not None:
                    self._on_binary(msg["bytes"])
                elif msg.get("text") is not None:
                    await self._on_text(msg["text"])
        except asyncio.TimeoutError:
            self._timed_out = True
        except WebSocketDisconnect as exc:
            self._close_code = exc.code
        except _Close:
            pass
        except Exception as exc:  # noqa: BLE001 - an unexpected server error ends this session only
            log.exception("session %s/%s crashed", self.device_id, self.env.session_id)
            self._crash = exc
        finally:
            await self._teardown()

    async def _teardown(self) -> None:
        STORE.drop_session(self.env.session_id or "")  # no voice context survives the connection
        if self.authenticated:
            self._record_session_issues()
        if self.active:
            self.active.abort_reason = self.active.abort_reason or "connection_lost"
            await self._cancel_active()
        self.hub.unregister(self)
        await self.close()

    def _record_session_issues(self) -> None:
        """What the server saw of this session's end (ola Diagnostics). The server noticing a drop does not
        make it the cause: these events stay "undetermined" until the watch's report or a confirmed server
        fault explains them (app/incidents/classify.py)."""
        now, sid = utcnow(), self.env.session_id
        found = []
        mid_turn = self.active is not None and not self.active.cancelled
        if mid_turn:
            found.append(diag_events.server_event("turn_interrupted", self.device_id, sid, now,
                                                  turn_id=self.active.turn_id))
        if self._crash is not None:
            found.append(diag_events.server_event("server_exception", self.device_id, sid, now,
                                                  reason=type(self._crash).__name__, detail=_exc_detail(self._crash)))
        elif self._timed_out:
            found.append(diag_events.server_event("server_timeout", self.device_id, sid, now,
                                                  detail={"timeout_s": get_settings().session_idle_timeout_s,
                                                          "mid_turn": mid_turn}))
        elif not self._closed and self._close_code not in (1000, 1001):
            # The watch's side ended without a clean close (1006: no close frame - the link just went away).
            found.append(diag_events.server_event("ws_close", self.device_id, sid, now,
                                                  reason=str(self._close_code or ""),
                                                  detail={"code": self._close_code, "mid_turn": mid_turn}))
        self._diag(found)

    def _diag(self, found: list[diag_events.Event], **kw: Any) -> None:
        """Store diagnostic events in the background: never blocks or breaks the session."""
        if found or kw:
            diagnostics.record_soon(self.hub, self.device_id, self.account_id, self._fw, found, **kw)

    async def _on_text(self, text: str) -> None:
        try:
            msg = parse_message(text)
        except ProtocolError as exc:
            await self.send_json("error", code=exc.code, message=exc.message)
            if exc.code == "protocol_unsupported":
                raise _Close() from exc
            return
        kind = msg["type"]
        if kind == "hello":
            await self._on_hello(msg)
            return
        if kind == "ping":
            await self.send_json("pong")
            return
        if not self.authenticated:
            await self.send_json("error", code="unauthorized", message="send hello with a valid token first")
            return
        handler = {
            "listen_start": self._on_listen_start,
            "listen_end": self._on_listen_end,
            "abort": self._on_abort,
            "playback_started": self._on_playback_started,
            "playback_done": self._on_playback_done,
            "settings_changed": self._on_settings_changed,
            "status": self._on_status,
            "item_open": self._on_item_open,
            "item_delete": self._on_item_delete,
            "item_done": self._on_item_done,
            "item_pin": self._on_item_pin,
        }.get(kind)
        if handler is None:
            await self.send_json("error", code="bad_request", message=f"unknown type {kind!r}")
            return
        await handler(msg)

    # --- hello / pairing ------------------------------------------------------------------

    async def _on_hello(self, msg: dict[str, Any]) -> None:
        device_id = str(msg.get("device_id") or "")[:64]
        if not device_id:
            await self.send_json("error", code="bad_request", message="device_id required")
            return
        self.device_id = device_id
        token, code = msg.get("token"), msg.get("pairing_code")
        hw, fw = str(msg.get("hw_model", ""))[:64], str(msg.get("fw_version", ""))[:32]
        if token:
            owner_status: str | None = None
            account_id: int | None = None
            new_session = uuid.uuid4().hex
            last_session: str | None = None
            with session_scope() as db:
                dev = DeviceRepo(db).by_token_hash(hash_device_token(token))
                if dev is None or dev.id != device_id:
                    dev = None
                else:
                    # The previous session's id: what a report from firmware 0.1.0 refers to (ola Diagnostics)
                    last_session = dev.last_session_id
                    DeviceRepo(db).touch(dev.id, fw_version=fw, hw_model=hw, last_ip=self._client_ip(),
                                         last_session_id=new_session)
                    settings, version = SettingsRepo(db).ensure(dev.id)
                    account_id = dev.account_id
                    chat_title = PersonaRepo(db).chat_title(settings.persona_id, account_id)
                    owner = db.get(Account, account_id) if account_id else None
                    owner_status = owner.status if owner else None
            if dev is None:
                await self.send_json("error", code="unauthorized", message="unknown or revoked token")
                raise _Close()
            if owner_status not in (None, "active"):
                await self.send_json("error", code="account_inactive", message="account suspended or closed")
                raise _Close()
            self.account_id = account_id
            self.settings = settings
            self.authenticated = True
            STORE.drop_device(device_id)  # a new session: nothing pending from an earlier one
            self._fw = fw
            self.env.session_id = new_session
            rates = (msg.get("audio") or {}).get("downlink_rates") or [16000]
            preferred = load_providers_config()["audio"]["downlink_rate_preferred"]
            self.downlink_rate = preferred if preferred in rates else 16000
            await self.hub.register(self)
            # restart / lost connection before this hello; earlier incidents of this watch have recovered
            self._diag(diag_events.from_hello(msg, device_id, last_session, utcnow()), started_session=new_session)
            await self.send_json(
                "hello_ack",
                server_time=int(utcnow().timestamp() * 1000),
                settings=device_view(settings, chat_title),
                settings_version=version,
                downlink_rate=self.downlink_rate,
            )
            await self.send_json("languages", items=languages.watch_languages())  # the watch's language picker
            if account_id is not None:
                await self.hub.push_items(account_id, only=self)
                await deliver_due(self.hub, account_id)  # reminders that came due while offline
                await self.hub.push_usage_notice(account_id)  # a threshold reached while this watch was off
            return
        if is_valid_pairing_code(code):
            try:
                self.hub.add_pending(code, self, hw, fw)
            except PairingError as exc:
                await self.send_json("error", code="bad_request", message=str(exc))
                return
            await self.send_json("pairing_pending", expires_in_s=self.hub.pairing_ttl_s)
            return
        await self.send_json("error", code="bad_request", message="hello needs token or 6-digit pairing_code")

    def _request_key(self, msg: dict[str, Any], turn_id: int) -> tuple[str, str]:
        """(request key, kind): the watch's request_id, or - older firmware - this session's turn number."""
        rid = msg.get("request_id")
        if isinstance(rid, str):
            return f"r:{rid}", "client"
        return f"l:{self.device_id}:{self.env.session_id}:{turn_id}", "legacy"

    async def _refuse(self, turn_id: int, code: str, legacy: bool, message: str = "") -> None:
        # Older firmware does not know the new codes: it shows its "Server busy" screen for "busy".
        sent = "busy" if legacy and code in usage_ops.NEW_CODES else code
        await self.send_json("error", turn_id, code=sent, message=message or usage_ops.MESSAGES.get(code, code))
        await self.send_json("turn_end", turn_id, status="error")
        self.hub.publish({"type": "turn_refused", "device_id": self.device_id, "code": code})

    def _client_ip(self) -> str | None:
        return self.ws.client.host if self.ws.client else None

    # --- turns --------------------------------------------------------------------------------

    async def _on_listen_start(self, msg: dict[str, Any]) -> None:
        turn_id = msg.get("turn_id")
        if not isinstance(turn_id, int) or turn_id <= self.last_turn_id:
            await self.send_json("error", code="bad_request", message="turn_id must increase", turn_id=turn_id)
            return
        # Edit modes: "note" (+ "note": n) or "reminder" (+ "reminder": n) - one item, edited by voice.
        edit_kind = msg.get("mode") if msg.get("mode") in ("note", "reminder") else None
        note_mode = edit_kind is not None
        note_number = msg.get(edit_kind) if edit_kind else None
        edit_uid = None
        if note_mode:
            ok = self.account_id is not None and isinstance(note_number, int)
            if ok:
                with session_scope() as db:
                    it = ItemRepo(db).get(self.account_id, edit_kind, note_number)
                ok = it is not None
                # The watch names the item by its number, which is reused after a delete: the edit session is
                # bound to the item the server last showed under that number - never another one.
                shown = self._shown.get((edit_kind, note_number))
                if ok and shown is not None and shown != it.uid:
                    ok = False
                if ok:
                    edit_uid = it.uid
                    self._shown[(edit_kind, note_number)] = it.uid
            if not ok:
                await self.send_json(
                    "error", turn_id, code="bad_request", message=f"{edit_kind} mode needs an existing {edit_kind}"
                )
                await self.send_json("turn_end", turn_id, status="error")
                self.last_turn_id = turn_id
                return
        request_key, request_kind = self._request_key(msg, turn_id)
        legacy = request_kind == "legacy"
        # The same request again (a resend): never a second operation. Checked before the running turn is
        # cancelled, so a resend cannot stop the turn it duplicates.
        same = next((t for t in self.recent.values() if not legacy and t.request_key == request_key), None)
        if same is not None:
            self.last_turn_id = turn_id
            if same.final_status is not None:  # already answered: report it, spend nothing
                await self.send_json("turn_end", turn_id, status=same.final_status, duplicate=True)
            else:
                await self._refuse(turn_id, usage_ops.DUPLICATE, legacy)
            return
        if self.active:
            await self._cancel_active()
        self.last_turn_id = turn_id
        # The database admits the turn and reserves a little allowance for it (app/usage_ops.py), shared by
        # every server process: several watches of one account cannot start turns past the limit together.
        fp = usage_ops.fingerprint(self.device_id, edit_kind or "chat", edit_uid)
        req = usage_ops.AdmitRequest(device_id=self.device_id, request_key=request_key, request_kind=request_kind,
                                     kind=edit_kind or "chat", account_id=self.account_id, fingerprint=fp)
        adm = await usage_ops.in_pool(usage_ops.ADMIT_POOL, usage_ops.admit, req)
        if not adm.allowed:
            if adm.duplicate == "finished" and not legacy:  # already answered: report it, spend nothing
                await self.send_json("turn_end", turn_id, status=adm.finished_status or "completed", duplicate=True)
                return
            await self._refuse(turn_id, adm.code or usage_ops.SERVICE_UNAVAILABLE, legacy, adm.message)
            return
        try:
            await self._start_turn(msg, turn_id, edit_kind, note_number, edit_uid, adm, request_key)
        except Exception as exc:
            log.exception("turn %s/%s could not start", self.device_id, turn_id)
            self._diag([diag_events.server_event("server_exception", self.device_id, self.env.session_id, utcnow(),
                                                 turn_id=turn_id, reason=type(exc).__name__,
                                                 detail={**_exc_detail(exc), "where": "turn_start"}, per_turn=True)])
            await usage_ops.in_pool(usage_ops.ADMIT_POOL, self._settle_quietly, adm.op_id, adm.exec_token,
                                    "error", False, [], None, "start_failed")
            await self.send_json("turn_end", turn_id, status="error")
            return
        if self.account_id is not None:
            await usage_notices.evaluate(self.account_id)

    @staticmethod
    def _settle_quietly(op_id, token, status, billable, items, turn_db_id, reason, interaction=False):
        try:
            return usage_ops.settle(op_id, token, status=status, billable=billable, items=items,
                                    turn_db_id=turn_db_id, reason=reason, interaction=interaction)
        except Exception:  # noqa: BLE001 - database down: retried later; recovery expires it otherwise
            log.warning("usage settle of op %s failed; queued", op_id, exc_info=True)
            usage_ops.queue_settle(op_id, token, status=status, billable=billable, items=items,
                                   turn_db_id=turn_db_id, reason=reason, interaction=interaction)
            return None

    async def _start_turn(self, msg, turn_id, edit_kind, note_number, edit_uid, adm, request_key) -> None:
        note_mode = edit_kind is not None
        requested = msg.get("language")
        language = requested if isinstance(requested, str) and languages.is_known(requested) else self.settings.language
        turn = TurnContext(
            turn_id=turn_id,
            session_id=self.env.session_id or "",
            device_id=self.device_id,
            language=language,
            settings=self.settings,
            downlink_rate=self.downlink_rate,
            fallback_language=self.last_language,
            account_id=self.account_id,
        )
        if note_mode:
            # Stored only if something was said (_finish_turn), and outside the chat history.
            turn.mode, turn.note_number, turn.edit_uid = edit_kind, note_number, edit_uid
        else:
            with session_scope() as db:
                conv = ConversationRepo(db).current(self.device_id, get_settings().conversation_idle_minutes)
                row = TurnRepo(db).create(
                    device_id=self.device_id,
                    account_id=self.account_id,
                    conversation_id=conv.id,
                    session_id=turn.session_id,
                    turn_no=turn_id,
                    language=language,
                )
                turn.db_id, turn.conversation_id = row.id, conv.id
        self._decoder = OpusDecoder(16000)
        self.active = turn
        self.recent[turn_id] = turn
        for old in sorted(self.recent)[:-MAX_RECENT_TURNS]:
            del self.recent[old]
        turn.op_id, turn.exec_token, turn.request_key = adm.op_id, adm.exec_token, request_key
        turn.task = asyncio.create_task(self._run_turn(turn), name=f"turn-{self.device_id}-{turn_id}")
        turn.lease_task = asyncio.create_task(self._keep_lease(turn, adm.lease_s), name=f"lease-{turn_id}")

    def _on_binary(self, data: bytes) -> None:
        if not self.authenticated:
            return
        try:
            frame = AudioFrame.unpack(data)
        except ValueError:
            return
        turn = self.active
        if frame.kind != KIND_UPLINK or turn is None or frame.turn_id != turn.turn_id or not turn.listening:
            return  # stale or unexpected frame
        try:
            pcm = self._decoder.decode(frame.payload) if self._decoder else b""
        except Exception:
            log.debug("bad opus packet from %s", self.device_id)
            return
        if pcm:
            turn.audio_in.put_nowait((pcm, mono_ms()))

    async def _on_listen_end(self, msg: dict[str, Any]) -> None:
        """The watch's stop button: end the sentence now and transcribe what was said (not an abort)."""
        turn = self.active
        if turn is not None and msg.get("turn_id") == turn.turn_id and turn.listening:
            turn.end_requested = True
            turn.audio_in.put_nowait(None)

    async def _on_abort(self, msg: dict[str, Any]) -> None:
        if self.active and msg.get("turn_id") == self.active.turn_id:
            reason = msg.get("reason")
            self.active.abort_reason = reason if reason in ABORT_REASONS else "error"
            await self._cancel_active()

    async def _cancel_active(self) -> None:
        turn, self.active = self.active, None
        if turn is None:
            return
        turn.cancelled = True  # from here on send()/send_audio() drop everything for this turn
        if turn.task and not turn.task.done():
            turn.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await turn.task

    async def _keep_lease(self, turn: TurnContext, lease_s: int) -> None:
        """Heartbeat while the turn runs, and write the costs reported so far. When the lease cannot be
        extended (expired, or the database unreachable until it would have) the turn stops: no new paid
        stage may start without a valid lease."""
        interval = max(1.0, float(get_settings().usage_heartbeat_s))
        deadline = asyncio.get_running_loop().time() + lease_s
        while True:
            await asyncio.sleep(interval)
            if turn.task is None or turn.task.done() or turn.op_id is None:
                return
            ok: bool | None
            try:
                self._flush_usage(turn)
                ok = await usage_ops.in_pool(usage_ops.LEASE_POOL, usage_ops.heartbeat, turn.op_id, turn.exec_token)
            except Exception:  # noqa: BLE001
                log.warning("turn %s/%s heartbeat failed", self.device_id, turn.turn_id, exc_info=True)
                ok = None if asyncio.get_running_loop().time() + interval < deadline - 5 else False
            if ok:
                deadline = asyncio.get_running_loop().time() + lease_s
            elif ok is False:
                log.warning("turn %s/%s lost its usage lease: stopping", self.device_id, turn.turn_id)
                turn.lease_lost = True
                self._diag([diag_events.server_event("lease_lost", self.device_id, turn.session_id, utcnow(),
                                                     turn_id=turn.turn_id, per_turn=True)])
                if self.active is turn:
                    turn.abort_reason = turn.abort_reason or "lease_lost"
                    await self._cancel_active()
                elif turn.task and not turn.task.done():
                    turn.cancelled = True
                    turn.task.cancel()
                return

    def _flush_usage(self, turn: TurnContext) -> None:
        """Queue the provider usage reported since the last flush (intermediate costs, idempotent)."""
        n = len(turn.usage)
        if turn.op_id is None or n <= turn.usage_flushed:
            return
        items = list(enumerate(turn.usage))[turn.usage_flushed:n]
        turn.usage_flushed = n
        usage_ops.LEASE_POOL.submit(self._record_quietly, turn.op_id, items, turn.db_id)

    @staticmethod
    def _record_quietly(op_id, items, turn_db_id) -> None:
        try:
            usage_ops.record_costs(op_id, items, turn_db_id)
        except Exception:  # noqa: BLE001 - the final settle writes them again (same dedup keys)
            log.warning("usage record of op %s failed", op_id, exc_info=True)

    async def _run_turn(self, turn: TurnContext) -> None:
        result = TurnResult("error", "internal", "unexpected error")
        try:
            if turn.op_id is not None and not await usage_ops.in_pool(
                    usage_ops.LEASE_POOL, usage_ops.mark_running, turn.op_id, turn.exec_token):
                turn.lease_lost = True  # expired before the first paid call: nothing is spent
                raise ProviderError("usage", "usage lease lost before start")
            result = await self.pipeline.run(turn, self)
            if turn.cancelled:
                result = TurnResult("aborted")
        except asyncio.CancelledError:
            result = TurnResult("aborted")
        except ProviderError as exc:
            log.warning("turn %s/%s %s", self.device_id, turn.turn_id, exc)
            result = TurnResult("error", f"{exc.stage}_failed", exc.message)
            self._diag_provider(turn, exc)
        except Exception as exc:  # noqa: BLE001
            log.exception("turn %s/%s crashed", self.device_id, turn.turn_id)
            result = TurnResult("error", "internal", str(exc))
            self._diag([diag_events.server_event("server_exception", self.device_id, turn.session_id, utcnow(),
                                                 turn_id=turn.turn_id, reason=type(exc).__name__,
                                                 detail={**_exc_detail(exc), "where": "turn"}, per_turn=True)])
        finally:
            await self._finish_turn(turn, result)

    def _diag_provider(self, turn: TurnContext, exc: ProviderError) -> None:
        if exc.stage == "usage":  # the usage lease expired before the first paid call
            ev = diag_events.server_event("lease_lost", self.device_id, turn.session_id, utcnow(), turn_id=turn.turn_id,
                                          per_turn=True)
        else:
            detail = {"stage": exc.stage, "timeout": _is_timeout(exc), "exception": _cause_name(exc),
                      "message": _safe_message(exc.message, turn)}
            ev = diag_events.server_event("provider_failure", self.device_id, turn.session_id, utcnow(),
                                          turn_id=turn.turn_id, reason=f"{exc.stage}_failed", detail=detail,
                                          per_turn=True)
        self._diag([ev])

    async def _finish_turn(self, turn: TurnContext, result: TurnResult) -> None:
        live = self._is_live(turn)
        if turn.language != languages.AUTO and turn.user_text:
            self.last_language = turn.language
        if result.error_code and live and not result.notified:
            # Provider/internal failures (e.g. no provider credit, timeouts) never reach the user with
            # their reason: the details stay in the log and the turn record.
            hidden = result.error_code == "internal" or result.error_code.endswith("_failed")
            message = TRY_LATER if hidden else result.error_message or ""
            await self.send_json("error", turn.turn_id, code=result.error_code, message=message)
        if live:
            await self.send(turn, "state", state="idle")
        if turn.items_changed and turn.account_id is not None:  # the fresh list first
            await self.hub.push_items(turn.account_id)
            self.hub.items_changed(turn.account_id)
        # Before turn_end: an edit-mode turn that deleted its item opens the list, and the watch closes the
        # edit mode on it instead of reopening the mic for the next sentence.
        if turn.pending_open is not None and live and result.status == "completed":
            if "list" in turn.pending_open:
                await self.send_json("items_open", kind=turn.pending_open["list"])
                if turn.account_id is not None:
                    STORE.clear_open(STORE.get(turn.account_id, self.device_id, turn.session_id))
            else:
                view = turn.pending_open["item"]
                if turn.pending_uid and isinstance(view.get("number"), int):
                    self._shown[(view.get("kind"), view["number"])] = turn.pending_uid
                await self.send_json("item_show", item=view)
        # turn_end is bookkeeping: always sent (the watch accepts it even for old turns). After an apology
        # the watch ends the turn like a reply (it would cut the spoken apology on "error"). expect_reply: the
        # reply asked something the operation needs (which one? confirm?) - the watch listens again.
        status = "completed" if result.notified else result.status
        extra = {"expect_reply": True} if turn.expect_reply and turn.mode == "chat" and status == "completed" and live else {}
        await self.send_json("turn_end", turn.turn_id, status=status, **extra)
        turn.final_status = status
        if result.status == "completed":
            self._diag([], turn_ok=True)  # earlier failed-turn incidents of this watch have recovered
        if status == "completed" and turn.account_id is not None:
            with session_scope() as db:  # the user heard about the changes made in this turn
                VoiceOpRepo(db).mark_reported(turn.session_id, turn.turn_id)
        if self.active is turn:
            self.active = None
        if turn.settings_changed:  # volume, language... changed by voice: applies after the reply
            await self.hub.push_settings(self.device_id)
            self.hub.publish({"type": "settings_changed", "device_id": self.device_id})
        if turn.mode in ("note", "reminder") and turn.db_id is None and result.status != "no_speech" and turn.usage:
            with session_scope() as db:  # edit-mode turns: stored only when something was said
                row = TurnRepo(db).create(
                    device_id=self.device_id,
                    account_id=turn.account_id,
                    conversation_id=None,  # not part of the chat history
                    session_id=turn.session_id,
                    turn_no=turn.turn_id,
                    language=turn.language if turn.language != languages.AUTO else "",
                )
                turn.db_id = row.id
        self._persist(turn, result)
        self._schedule_learning(turn, result)
        lease_task = getattr(turn, "lease_task", None)
        if lease_task is not None:
            lease_task.cancel()
        if turn.op_id is not None:
            # The end of the operation. interaction: whether it uses one of the customer's AI interactions
            # (app/interactions.py: an understood request that completed, or that the user cancelled; never
            # silence, a failure on our side or a dropped connection). billable: whether its costs belong to
            # the customer's consumption in the finance reports (internal; costs never limit the customer).
            reason = "lease_lost" if turn.lease_lost else turn.abort_reason
            billable = result.status != "error" and reason not in ("timeout", "lease_lost", "shutdown")
            counted = counts_as_interaction(turn.mode, result.status, reason, turn.user_text, turn.edit_outcome)
            await usage_ops.in_pool(usage_ops.ADMIT_POOL, self._settle_quietly, turn.op_id, turn.exec_token,
                                    result.status, billable, list(enumerate(turn.usage)), turn.db_id, reason,
                                    counted)
            if turn.account_id is not None:
                cost_monitor.evaluate_soon(self.hub, turn.account_id)  # internal monitoring, never blocks
        if turn.account_id is not None:
            new = await usage_notices.evaluate(turn.account_id)
            if new:  # a usage threshold was crossed: web banner / dialog, and a short note on the watch
                self.hub.publish({"type": "usage_threshold", "account_id": turn.account_id, "threshold": max(new)})
                await self.hub.push_usage_notice(turn.account_id)
        self.hub.publish(
            {
                "type": "turn_end",
                "device_id": self.device_id,
                "turn_id": turn.turn_id,
                "status": result.status,
                "user_text": turn.user_text,
                "assistant_text": turn.assistant_text,
                **turn.marks.as_db_fields(),
            }
        )

    def _schedule_learning(self, turn: TurnContext, result: TurnResult) -> None:
        """Opt-in memory learning: after a completed chat turn, the conversation is (re)scheduled to be read
        once it has gone quiet (one model call per conversation, app/memory/extract.py)."""
        if (result.status != "completed" or turn.mode != "chat" or turn.account_id is None
                or not turn.conversation_id or turn.db_id is None):
            return
        try:
            from app.memory import prefs
            from app.memory.extract import schedule

            if not prefs(turn.account_id).learn:
                return
            with session_scope() as db:
                schedule(db, turn.account_id, turn.conversation_id, turn.db_id, get_settings().conversation_idle_minutes)
        except Exception as exc:  # noqa: BLE001 - learning never affects the turn
            log.warning("memory learning not scheduled: %s", type(exc).__name__)

    def _persist(self, turn: TurnContext, result: TurnResult) -> None:
        if turn.db_id is None:
            return
        with session_scope() as db:
            TurnRepo(db).update(
                turn.db_id,
                status=result.status,
                mode=turn.mode,
                tools=",".join(turn.tools_used)[:255],
                search_note=turn.search_note,
                language=turn.language,
                user_text=turn.user_text,
                assistant_text=turn.assistant_text,
                error=result.error_message if result.error_code else abort_text(result, turn),
                finished_at=utcnow(),
                llm_model=next((u.model for u in turn.usage if u.kind == "llm"), None),
                stt_provider=next((u.provider for u in turn.usage if u.kind == "stt"), None),
                tts_provider=next((u.provider for u in turn.usage if u.kind == "tts"), None),
                **turn.marks.as_db_fields(),
            )

    async def _on_playback_started(self, msg: dict[str, Any]) -> None:
        turn = self.recent.get(msg.get("turn_id"))
        if turn is None or turn.marks.device_first_frame is not None:
            return
        turn.marks.device_first_frame = mono_ms()
        if turn.task and turn.task.done() and turn.db_id is not None:
            with session_scope() as db:  # turn already persisted: patch the device TTFA
                TurnRepo(db).update(turn.db_id, ttfa_device_ms=turn.marks.as_db_fields()["ttfa_device_ms"])

    async def _on_playback_done(self, msg: dict[str, Any]) -> None:
        self.hub.publish({"type": "playback_done", "device_id": self.device_id, "turn_id": msg.get("turn_id")})

    # --- settings / status ----------------------------------------------------------------

    async def _on_settings_changed(self, msg: dict[str, Any]) -> None:
        changes = {k: v for k, v in (msg.get("changes") or {}).items() if k in DEVICE_EDITABLE}
        if changes:
            try:
                with session_scope() as db:
                    SettingsRepo(db).update(self.device_id, changes)
            except ValueError as exc:
                await self.send_json("error", code="bad_request", message=f"invalid settings: {exc}")
        await self.hub.push_settings(self.device_id)
        self.hub.publish({"type": "settings_changed", "device_id": self.device_id})

    # --- notes / reminders ------------------------------------------------------------------

    def _item_ref(self, msg: dict[str, Any]) -> tuple[str, int] | None:
        kind, number = msg.get("kind"), msg.get("number")
        if self.account_id is None or kind not in KINDS or not isinstance(number, int):
            return None
        return kind, number

    async def _on_item_open(self, msg: dict[str, Any]) -> None:
        ref = self._item_ref(msg)
        if ref is None:
            await self.send_json("error", code="bad_request", message="item_open needs kind and number")
            return
        with session_scope() as db:
            it = ItemRepo(db).get(self.account_id, *ref)
            view = device_full(it, self.settings.timezone) if it else None
        if view is None or it is None:
            await self.hub.push_items(self.account_id, only=self)  # the watch's list is stale
            return
        self._shown[ref] = it.uid
        from app.voice_tools import summary  # the item opened by tap is "this" for the next sentence

        STORE.set_open(STORE.get(self.account_id, self.device_id, self.env.session_id or ""), it,
                       summary(it, self.settings.timezone), self.last_turn_id)
        await self.send_json("item_show", item=view)

    async def _on_item_delete(self, msg: dict[str, Any]) -> None:
        ref = self._item_ref(msg)
        if ref is None:
            await self.send_json("error", code="bad_request", message="item_delete needs kind and number")
            return
        with session_scope() as db:
            repo = ItemRepo(db)
            it = repo.get(self.account_id, *ref)
            if it is not None:
                repo.delete(it)
        await self.hub.push_items(self.account_id)
        self.hub.items_changed(self.account_id)

    async def _on_item_pin(self, msg: dict[str, Any]) -> None:
        number, pinned = msg.get("number"), msg.get("pinned")
        if self.account_id is None or not isinstance(number, int) or not isinstance(pinned, bool):
            await self.send_json("error", code="bad_request", message="item_pin needs number and pinned")
            return
        with session_scope() as db:
            repo = ItemRepo(db)
            it = repo.get(self.account_id, "note", number)
            if it is not None:
                repo.update(it, pinned=pinned)
        await self.hub.push_items(self.account_id)
        self.hub.items_changed(self.account_id)

    async def _on_item_done(self, msg: dict[str, Any]) -> None:
        ref = self._item_ref(msg)
        done = msg.get("done")
        if ref is None or ref[0] != "reminder" or not isinstance(done, bool):
            await self.send_json("error", code="bad_request", message="item_done needs a reminder number and done")
            return
        with session_scope() as db:
            repo = ItemRepo(db)
            it = repo.get(self.account_id, *ref)
            if it is not None:
                repo.update(it, done=done)
        await self.hub.push_items(self.account_id)
        self.hub.items_changed(self.account_id)

    async def _on_status(self, msg: dict[str, Any]) -> None:
        fields = {
            "battery_pct": msg.get("battery_pct"),
            "charging": msg.get("charging"),
            "rssi": msg.get("rssi"),
        }
        with session_scope() as db:
            DeviceRepo(db).touch(self.device_id, **{k: v for k, v in fields.items() if v is not None})
        self.hub.publish({"type": "device_status", "device_id": self.device_id, **fields})


class _Close(Exception):
    """Internal: end the session after an unrecoverable protocol error."""


def _exc_detail(exc: BaseException) -> dict[str, Any]:
    """An exception for diagnostics: class names only (messages can hold user data)."""
    from sqlalchemy.exc import SQLAlchemyError

    db = isinstance(exc, SQLAlchemyError) or isinstance(exc.__cause__, SQLAlchemyError)
    return {"exception": type(exc).__name__, "cause": _cause_name(exc), "db": db,
            "component": "database" if db else "gateway"}


def _cause_name(exc: BaseException) -> str | None:
    cause = exc.__cause__ or exc.__context__
    return type(cause).__name__ if cause is not None else None


def _is_timeout(exc: BaseException) -> bool:
    seen: BaseException | None = exc
    for _ in range(4):
        if seen is None:
            break
        if isinstance(seen, TimeoutError) or "Timeout" in type(seen).__name__:
            return True
        seen = seen.__cause__ or seen.__context__
    text = str(getattr(exc, "message", exc)).lower()
    return "timeout" in text or "timed out" in text


def _safe_message(message: str, turn: TurnContext) -> str:
    """A provider's error text, short, and never with the conversation in it (any 12 characters of what was
    said or answered, anywhere in the message)."""
    msg = (message or "")[:120]
    low = msg.lower()
    for said in (turn.user_text, turn.assistant_text):
        text = (said or "").lower()
        if len(text) >= 8 and (text in low or any(low[i:i + 12] in text for i in range(max(0, len(low) - 11)))):
            return "[redacted: contained conversation text]"
    return msg


@router.websocket("/ws/device")
async def device_endpoint(ws: WebSocket) -> None:
    conn = DeviceConnection(ws, ws.app.state.hub, ws.app.state.pipeline)
    await conn.run()
