"""ola Diagnostics: deterministic root-cause classification (no I/O, no AI).

`classify_event` labels one event from its own evidence. `classify_incident` picks the incident's cause from
all its events: the strongest evidence wins (confirmed > probable > unknown), whatever order the events
arrived in. A secondary event (an interrupted turn) is a consequence, never a cause. The component that
noticed a problem is not assumed to have caused it: a generic "ws_error" / "ws_closed" / a server-side idle
timeout say only that the connection ended, so they stay UNDETERMINED until better evidence arrives.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

WATCH, CONNECTION, SERVER, UNDETERMINED = "watch", "connection", "server", "undetermined"
CATEGORIES = (WATCH, CONNECTION, SERVER, UNDETERMINED)
CONFIRMED, PROBABLE, UNKNOWN = "confirmed", "probable", "unknown"
CONFIDENCES = (CONFIRMED, PROBABLE, UNKNOWN)
SEVERITIES = ("error", "warn", "info")

_CONF_RANK = {CONFIRMED: 3, PROBABLE: 2, UNKNOWN: 1}
_SEV_RANK = {"error": 3, "warn": 2, "info": 1}
# Equal confidence, different categories: a fault inside the watch or the server explains a dropped
# connection better than the drop explains them (documented in server/docs/diagnostics.md).
_CAT_RANK = {WATCH: 4, SERVER: 3, CONNECTION: 2, UNDETERMINED: 1}

# esp_reset_reason() names sent by the firmware
CRASH_RESETS = {"panic", "cpu_lockup"}
FREEZE_RESETS = {"int_wdt", "task_wdt", "wdt"}
POWER_RESETS = {"brownout", "pwr_glitch"}
ROUTINE_RESETS = {"power_on", "software", "usb", "jtag", "external", "deepsleep"}

# ESP-IDF 5.5 esp_tls_errors.h
ESP_TLS_CANNOT_RESOLVE_HOSTNAME = 0x8001
ESP_TLS_FAILED_CONNECT_TO_HOST = 0x8004
ESP_TLS_CONNECTION_TIMEOUT = 0x8006
ESP_TLS_TCP_CLOSED_FIN = 0x8008
ESP_TLS_SERVER_HANDSHAKE_TIMEOUT = 0x8009
ESP_TLS_NAMES = {
    0x8001: "ESP_ERR_ESP_TLS_CANNOT_RESOLVE_HOSTNAME",
    0x8002: "ESP_ERR_ESP_TLS_CANNOT_CREATE_SOCKET",
    0x8003: "ESP_ERR_ESP_TLS_UNSUPPORTED_PROTOCOL_FAMILY",
    0x8004: "ESP_ERR_ESP_TLS_FAILED_CONNECT_TO_HOST",
    0x8005: "ESP_ERR_ESP_TLS_SOCKET_SETOPT_FAILED",
    0x8006: "ESP_ERR_ESP_TLS_CONNECTION_TIMEOUT",
    0x8007: "ESP_ERR_ESP_TLS_SE_FAILED",
    0x8008: "ESP_ERR_ESP_TLS_TCP_CLOSED_FIN",
    0x8009: "ESP_ERR_ESP_TLS_SERVER_HANDSHAKE_TIMEOUT",
    0x8015: "ESP_ERR_MBEDTLS_X509_CRT_PARSE_FAILED",
    0x801A: "ESP_ERR_MBEDTLS_SSL_HANDSHAKE_FAILED",
    0x8018: "ESP_ERR_MBEDTLS_SSL_WRITE_FAILED",
    0x801D: "ESP_ERR_MBEDTLS_SSL_READ_FAILED",
}
_TLS_HANDSHAKE = {0x8009, 0x8015, 0x801A}
# esp_websocket_client 1.8 esp_websocket_error_type_t
WS_ERR_TYPES = {0: "none", 1: "tcp_transport", 2: "pong_timeout", 3: "handshake", 4: "server_close"}
WS_TCP_TRANSPORT, WS_PONG_TIMEOUT, WS_HANDSHAKE, WS_SERVER_CLOSE = 1, 2, 3, 4
# lwIP errno values (lwip/errno.h)
ERRNO_NAMES = {104: "ECONNRESET", 110: "ETIMEDOUT", 111: "ECONNREFUSED", 113: "EHOSTUNREACH", 118: "EHOSTUNREACH",
               103: "ECONNABORTED", 128: "ENOTCONN", 32: "EPIPE", 5: "EIO", 11: "EAGAIN"}
_NETWORK_ERRNOS = {104, 110, 111, 113, 118, 103, 128, 32}
# Close codes the server sends (RFC 6455 + app/gateway/hub.py)
# 1001 "going away" is not here: the ola server never sends it, a proxy (Caddy reload) does - not a server fault.
SERVER_FAULT_CLOSE = {1011: "internal_error", 1012: "service_restart", 1013: "try_again_later"}
OPERATOR_CLOSE = {4001: "token_revoked", 4002: "disconnected_by_operator"}

WEAK_RSSI_DBM = -80
LOW_HEAP_BYTES = 20 * 1024


@dataclass(frozen=True)
class EventClass:
    category: str
    confidence: str
    reason_code: str
    component: str | None
    severity: str
    secondary: bool = False  # a consequence of something else (never the incident's cause)


@dataclass(frozen=True)
class IncidentClass:
    category: str
    confidence: str
    reason_code: str
    component: str | None
    severity: str
    detected_by: str
    primary_index: int | None  # index of the primary event in the input list


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _ws_evidence(ws: dict[str, Any], inferred: bool) -> EventClass | None:
    """The esp_websocket_client error details captured on the watch (firmware 0.1.1+)."""
    tls = _int(ws.get("tls")) or 0
    errno = _int(ws.get("errno")) or 0
    etype = _int(ws.get("type")) or 0
    hs = _int(ws.get("hs")) or 0
    close = _int(ws.get("close")) or 0
    sure = PROBABLE if inferred else CONFIRMED
    if close in SERVER_FAULT_CLOSE:
        return EventClass(SERVER, sure, "server_closed_" + SERVER_FAULT_CLOSE[close], "process", "error")
    if close in OPERATOR_CLOSE:  # a deliberate action on the server side, not a fault
        return EventClass(SERVER, sure, "server_" + OPERATOR_CLOSE[close], "gateway", "info")
    if close == 4000:  # replaced by the watch's own newer connection: the old link had already gone
        return EventClass(CONNECTION, PROBABLE, "server_replaced", "network", "info")
    if close == 1001:
        return EventClass(UNDETERMINED, UNKNOWN, "closed_going_away", "proxy", "warn")
    if tls == ESP_TLS_CANNOT_RESOLVE_HOSTNAME:
        return EventClass(CONNECTION, sure, "dns_failure", "dns", "warn")
    if hs >= 500:
        # The proxy answered but the app behind it did not (502/503/504): most likely the server process.
        return EventClass(SERVER, PROBABLE, "server_unavailable", "proxy", "error")
    if tls in _TLS_HANDSHAKE:
        return EventClass(CONNECTION, PROBABLE, "tls_failure", "tls", "warn")
    if tls == ESP_TLS_CONNECTION_TIMEOUT or tls == ESP_TLS_FAILED_CONNECT_TO_HOST:
        return EventClass(CONNECTION, PROBABLE, "tcp_connect_failure", "tcp", "warn")
    if etype == WS_PONG_TIMEOUT:
        return EventClass(CONNECTION, PROBABLE, "connection_timeout", "network", "warn")
    if errno in _NETWORK_ERRNOS:
        return EventClass(CONNECTION, PROBABLE, "tcp_failure", "tcp", "warn")
    return None


def classify_event(kind: str, reason: str, detail: dict[str, Any] | None, detected_by: str = "") -> EventClass:
    d = detail or {}
    inferred = d.get("link_basis") == "inferred"
    if kind == "reboot":
        if reason in CRASH_RESETS:
            return EventClass(WATCH, CONFIRMED, "watch_crash", "firmware", "error")
        if reason in FREEZE_RESETS:
            return EventClass(WATCH, CONFIRMED, "watch_freeze", "firmware", "error")
        if reason in POWER_RESETS:
            return EventClass(WATCH, CONFIRMED, "watch_power_fault", "power", "error")
        if reason == "power_on" and d.get("prev_session"):
            # The power went off while connected: flat battery or switched off (the chip cannot tell which).
            return EventClass(WATCH, PROBABLE, "watch_power_lost", "power", "warn")
        if reason in ROUTINE_RESETS:
            return EventClass(WATCH, CONFIRMED, "watch_restart", "firmware", "info")
        return EventClass(WATCH, UNKNOWN, "watch_restart_unknown", "firmware", "warn")
    if kind == "disconnect":
        if reason == "reboot":  # the session ended because the watch restarted (see the reboot event)
            return EventClass(WATCH, PROBABLE, "watch_restarted_in_session", "firmware", "warn", secondary=True)
        if reason == "reconnect":
            return EventClass(WATCH, CONFIRMED, "reconnect_requested", "firmware", "info")
        if reason == "wifi_lost":
            weak = (_int(d.get("rssi")) or 0) < 0 and (_int(d.get("rssi")) or 0) <= WEAK_RSSI_DBM
            return EventClass(CONNECTION, PROBABLE if inferred else CONFIRMED,
                              "wifi_weak_signal" if weak else "wifi_lost", "wifi", "warn")
        if reason == "pong_timeout":  # the watch's own keep-alive check: no reply from the server in time
            return EventClass(CONNECTION, PROBABLE, "connection_timeout", "network", "warn")
        ws = d.get("ws") if isinstance(d.get("ws"), dict) else {}
        found = _ws_evidence(ws, inferred)
        if found is not None:
            return found
        heap = _int(d.get("min_heap"))
        if heap is not None and 0 < heap < LOW_HEAP_BYTES:
            return EventClass(WATCH, PROBABLE, "watch_low_memory", "memory", "warn")
        retry = d.get("retry_ws") if isinstance(d.get("retry_ws"), dict) else {}
        if retry:  # the drop itself carried nothing, but the reconnect attempts that followed failed for a reason
            later = _ws_evidence(retry, True)
            if later is not None and later.category == CONNECTION:
                return EventClass(CONNECTION, PROBABLE, later.reason_code, later.component, "warn")
        if reason == "connect_failed":
            return EventClass(UNDETERMINED, UNKNOWN, "connect_failed_unknown", None, "warn")
        if reason == "hello_timeout":
            return EventClass(UNDETERMINED, UNKNOWN, "hello_timeout", None, "warn")
        return EventClass(UNDETERMINED, UNKNOWN, "link_lost_unknown", None, "warn")
    if kind == "turn_interrupted":
        return EventClass(UNDETERMINED, UNKNOWN, "turn_interrupted", None, "warn", secondary=True)
    if kind == "server_timeout":
        return EventClass(UNDETERMINED, UNKNOWN, "watch_silent", None, "warn")
    if kind == "ws_close":
        code = _int(d.get("code"))
        if code == 1012:  # "service restart": only the server sends it (uvicorn closing sessions on shutdown)
            return EventClass(SERVER, CONFIRMED, "server_shutdown", "process", "warn")
        if code == 1011:  # "internal error": the watch's client never sends it
            return EventClass(SERVER, PROBABLE, "server_closed_internal_error", "gateway", "error")
        return EventClass(UNDETERMINED, UNKNOWN, "connection_closed_unknown", None, "warn")
    if kind == "server_shutdown":
        return EventClass(SERVER, CONFIRMED, "server_shutdown", "process", "warn")
    if kind == "server_exception":
        comp = "database" if d.get("db") else (str(d.get("component") or "gateway"))[:32]
        return EventClass(SERVER, CONFIRMED, "server_exception", comp, "error")
    if kind == "provider_failure":
        stage = str(d.get("stage") or "provider")[:16]
        code = f"{stage}_timeout" if d.get("timeout") else f"{stage}_failure"
        return EventClass(SERVER, CONFIRMED, code, stage, "error")
    if kind == "lease_lost":
        return EventClass(SERVER, PROBABLE, "usage_lease_lost", "database", "error")
    return EventClass(UNDETERMINED, UNKNOWN, "unknown", None, "warn")


def classify_incident(events: Iterable[dict[str, Any]]) -> IncidentClass:
    """`events`: dicts with kind, reason, detail, detected_by, occurred_at (any order). The result does not
    depend on the order: ties are broken by stable fields only."""
    evs = list(events)
    if not evs:
        return IncidentClass(UNDETERMINED, UNKNOWN, "unknown", None, "warn", "server", None)
    classes = [classify_event(e.get("kind", ""), e.get("reason", ""), e.get("detail"), e.get("detected_by", ""))
               for e in evs]

    def rank(i: int) -> tuple:
        c, e = classes[i], evs[i]
        return (not c.secondary, _CONF_RANK[c.confidence], _CAT_RANK[c.category], _SEV_RANK[c.severity],
                # stable tie-breakers, independent of arrival order
                str(e.get("kind", "")), str(e.get("reason", "")), str(e.get("dedup_key") or ""))

    best = max(range(len(evs)), key=rank)
    primary = classes[best]
    # Severity: the primary event's, raised only by other events with a cause of their own. Consequences
    # (secondary events) and the undetermined views of the same drop are explained by the primary: an update
    # restart that closed the connection stays "info", an interrupted turn never makes it an "error".
    sev = primary.severity
    if primary.secondary and sev == "error":
        sev = "warn"
    for i, c in enumerate(classes):
        if i == best or c.secondary or c.category == UNDETERMINED:
            continue
        if _SEV_RANK[c.severity] > _SEV_RANK[sev]:
            sev = c.severity
    return IncidentClass(primary.category, primary.confidence, primary.reason_code, primary.component, sev,
                         str(evs[best].get("detected_by") or "server"), best)
