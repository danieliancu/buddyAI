"""/ws/device: one DeviceConnection per watch session (PROTOCOL.md)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from app import entitlements, languages, usage_notices
from app.audio.codec import OpusDecoder
from app.config import get_settings, load_providers_config
from app.db.models import Account, utcnow
from app.db.repositories import ConversationRepo, DeviceRepo, ItemRepo, SettingsRepo, TurnRepo, UsageRepo
from app.db.session import session_scope
from app.device_settings import DEVICE_EDITABLE, DeviceSettings, device_view
from app.gateway.hub import DeviceHub, PairingError
from app.items import KINDS, device_full
from app.gateway.protocol import KIND_UPLINK, AudioFrame, Envelope, ProtocolError, parse_message
from app.pipeline.conversation import ConversationPipeline
from app.pipeline.metrics import mono_ms
from app.pipeline.turn import TurnContext, TurnResult
from app.pricing.pricing import ProviderPricingConfig
from app.providers.base import ProviderError
from app.reminders import deliver_due
from app.security import hash_device_token, is_valid_pairing_code

log = logging.getLogger(__name__)
router = APIRouter()

MAX_RECENT_TURNS = 8


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
                    break
                if msg.get("bytes") is not None:
                    self._on_binary(msg["bytes"])
                elif msg.get("text") is not None:
                    await self._on_text(msg["text"])
        except (asyncio.TimeoutError, WebSocketDisconnect):
            pass
        except _Close:
            pass
        finally:
            await self._teardown()

    async def _teardown(self) -> None:
        if self.active:
            await self._cancel_active()
        self.hub.unregister(self)
        await self.close()

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
            "abort": self._on_abort,
            "playback_started": self._on_playback_started,
            "playback_done": self._on_playback_done,
            "settings_changed": self._on_settings_changed,
            "status": self._on_status,
            "item_open": self._on_item_open,
            "item_delete": self._on_item_delete,
            "item_done": self._on_item_done,
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
            with session_scope() as db:
                dev = DeviceRepo(db).by_token_hash(hash_device_token(token))
                if dev is None or dev.id != device_id:
                    dev = None
                else:
                    DeviceRepo(db).touch(dev.id, fw_version=fw, hw_model=hw, last_ip=self._client_ip())
                    settings, version = SettingsRepo(db).ensure(dev.id)
                    account_id = dev.account_id
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
            self.env.session_id = uuid.uuid4().hex
            rates = (msg.get("audio") or {}).get("downlink_rates") or [16000]
            preferred = load_providers_config()["audio"]["downlink_rate_preferred"]
            self.downlink_rate = preferred if preferred in rates else 16000
            await self.hub.register(self)
            await self.send_json(
                "hello_ack",
                server_time=int(utcnow().timestamp() * 1000),
                settings=device_view(settings),
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

    def _reservation_key(self, turn_id: int) -> str:
        return f"{self.device_id}:{self.env.session_id}:{turn_id}"

    def _client_ip(self) -> str | None:
        return self.ws.client.host if self.ws.client else None

    # --- turns --------------------------------------------------------------------------------

    async def _on_listen_start(self, msg: dict[str, Any]) -> None:
        turn_id = msg.get("turn_id")
        if not isinstance(turn_id, int) or turn_id <= self.last_turn_id:
            await self.send_json("error", code="bad_request", message="turn_id must increase", turn_id=turn_id)
            return
        if self.active:
            await self._cancel_active()
        self.last_turn_id = turn_id
        # Checks the plan and reserves a little allowance for this turn (released in _finish_turn), so
        # several watches of one account cannot start turns past the limit at the same time.
        decision = await entitlements.begin_turn(self.account_id, self._reservation_key(turn_id))
        if not decision.allowed:
            await self.send_json("error", turn_id, code=decision.code, message=decision.message)
            await self.send_json("turn_end", turn_id, status="error")
            self.hub.publish({"type": "turn_refused", "device_id": self.device_id, "code": decision.code})
            return
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
        turn.task = asyncio.create_task(self._run_turn(turn), name=f"turn-{self.device_id}-{turn_id}")

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

    async def _on_abort(self, msg: dict[str, Any]) -> None:
        if self.active and msg.get("turn_id") == self.active.turn_id:
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

    async def _run_turn(self, turn: TurnContext) -> None:
        result = TurnResult("error", "internal", "unexpected error")
        try:
            result = await self.pipeline.run(turn, self)
            if turn.cancelled:
                result = TurnResult("aborted")
        except asyncio.CancelledError:
            result = TurnResult("aborted")
        except ProviderError as exc:
            log.warning("turn %s/%s %s", self.device_id, turn.turn_id, exc)
            result = TurnResult("error", f"{exc.stage}_failed", exc.message)
        except Exception as exc:  # noqa: BLE001
            log.exception("turn %s/%s crashed", self.device_id, turn.turn_id)
            result = TurnResult("error", "internal", str(exc))
        finally:
            await self._finish_turn(turn, result)

    async def _finish_turn(self, turn: TurnContext, result: TurnResult) -> None:
        live = self._is_live(turn)
        if turn.language != languages.AUTO and turn.user_text:
            self.last_language = turn.language
        if result.error_code and live:
            await self.send_json("error", turn.turn_id, code=result.error_code, message=result.error_message or "")
        if live:
            await self.send(turn, "state", state="idle")
        # turn_end is bookkeeping: always sent (the watch accepts it even for old turns).
        await self.send_json("turn_end", turn.turn_id, status=result.status)
        if self.active is turn:
            self.active = None
        if turn.settings_changed:  # volume, language... changed by voice: applies after the reply
            await self.hub.push_settings(self.device_id)
            self.hub.publish({"type": "settings_changed", "device_id": self.device_id})
        if turn.items_changed and turn.account_id is not None:
            await self.hub.push_items(turn.account_id)
            self.hub.items_changed(turn.account_id)
        if turn.pending_open is not None and live and result.status == "completed":
            if "list" in turn.pending_open:
                await self.send_json("items_open", kind=turn.pending_open["list"])
            else:
                await self.send_json("item_show", item=turn.pending_open["item"])
        self._persist(turn, result)
        entitlements.end_turn(turn.account_id, self._reservation_key(turn.turn_id))
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

    def _persist(self, turn: TurnContext, result: TurnResult) -> None:
        if turn.db_id is None:
            return
        with session_scope() as db:
            TurnRepo(db).update(
                turn.db_id,
                status=result.status,
                language=turn.language,
                user_text=turn.user_text,
                assistant_text=turn.assistant_text,
                error=result.error_message if result.error_code else None,
                finished_at=utcnow(),
                llm_model=next((u.model for u in turn.usage if u.kind == "llm"), None),
                stt_provider=next((u.provider for u in turn.usage if u.kind == "stt"), None),
                tts_provider=next((u.provider for u in turn.usage if u.kind == "tts"), None),
                **turn.marks.as_db_fields(),
            )
            pricing = ProviderPricingConfig.load(db)
            # A turn that failed on our side (provider/server error) is not charged to the customer's
            # allowance; its cost stays visible to the operator. Aborted / no-speech turns count.
            UsageRepo(db).add_many(
                pricing.records(
                    turn.usage,
                    self.device_id,
                    turn.db_id,
                    turn.account_id,
                    turn_status=result.status,
                    billable=result.status != "error",
                )
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
        if view is None:
            await self.hub.push_items(self.account_id, only=self)  # the watch's list is stale
            return
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


@router.websocket("/ws/device")
async def device_endpoint(ws: WebSocket) -> None:
    conn = DeviceConnection(ws, ws.app.state.hub, ws.app.state.pipeline)
    await conn.run()
