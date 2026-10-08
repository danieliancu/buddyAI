"""Readable one-line summaries of diagnostic events for the older /api/issues list. The diagnostics
themselves (events, incidents, classification) live in app/incidents."""

from __future__ import annotations

from typing import Any

from app.db.models import DeviceIssue

# esp_reset_reason() as sent by the firmware -> (severity, plain-English meaning)
RESET_REASONS: dict[str, tuple[str, str]] = {
    "brownout": ("error", "Power dropped too low (brownout): weak USB supply or cable, or no battery"),
    "panic": ("error", "Firmware crash (panic)"),
    "int_wdt": ("error", "Firmware froze (interrupt watchdog)"),
    "task_wdt": ("error", "Firmware froze (task watchdog)"),
    "wdt": ("error", "Firmware froze (watchdog)"),
    "pwr_glitch": ("error", "Power glitch"),
    "cpu_lockup": ("error", "CPU lock-up"),
    "power_on": ("info", "Powered on (cable plugged in, or the power was cut completely)"),
    "software": ("info", "Restarted by the firmware (update, setup or reset)"),
    "usb": ("info", "Reset over USB (flashing or serial monitor)"),
    "jtag": ("info", "Reset over JTAG"),
    "external": ("info", "External reset pin"),
    "deepsleep": ("info", "Woke from deep sleep"),
}

# Why the watch's last session ended ("link.drop")
DROP_REASONS: dict[str, str] = {
    "wifi_lost": "Wi-Fi connection lost",
    "ws_error": "Connection error (cause not reported)",
    "ws_disconnected": "Connection dropped (cause not reported)",
    "ws_closed": "Connection closed (cause not reported)",
    "hello_timeout": "No answer to the hello in time",
    "reconnect": "Reconnect requested on the watch",
    "reboot": "The watch restarted during the session",
    "connect_failed": "The watch could not connect",
}

# wifi_err_reason_t codes worth naming (ESP-IDF esp_wifi_types.h)
WIFI_REASONS: dict[int, str] = {
    2: "authentication expired",
    3: "the access point dropped the watch",
    4: "association expired",
    8: "the watch left the network",
    15: "Wi-Fi handshake timed out (wrong password?)",
    200: "access point signal lost (beacon timeout)",
    201: "network not found (hotspot off or out of range)",
    202: "authentication failed (wrong password?)",
    203: "association failed",
    204: "Wi-Fi handshake timed out",
    205: "connection failed",
}


def _int(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def describe(issue: DeviceIssue) -> str:
    """One readable sentence for the Issues tab."""
    d = issue.detail or {}
    if issue.kind == "reboot":
        text = RESET_REASONS.get(issue.reason, ("warn", f"Restarted (reset reason: {issue.reason or 'unknown'})"))[1]
        up = _int(d.get("prev_uptime_s"))
        return text + (f"; it had been running for {_fmt_s(up)}" if up else "")
    if issue.kind == "disconnect":
        text = DROP_REASONS.get(issue.reason, f"Connection lost ({issue.reason or 'unknown'})")
        wr = _int(d.get("wifi_reason"))
        if wr is not None:
            text += f": {WIFI_REASONS.get(wr, f'Wi-Fi reason {wr}')}"
        if d.get("mid_turn"):
            text += " - during a conversation"
        off = _int(d.get("offline_ms"))
        if off is not None:
            text += f"; back after {_fmt_s(off // 1000)}"
        return text
    if issue.kind == "turn_interrupted":
        return "The connection closed while a conversation turn was running"
    if issue.kind == "server_timeout":
        return f"No data from the watch for {d.get('timeout_s', '?')} s; the server closed the session"
    if issue.reason_code:
        from app.incidents.texts import title

        return title(issue.reason_code)
    return issue.kind


def _fmt_s(s: int) -> str:
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60} s"
    return f"{s // 3600} h {s % 3600 // 60} min"


def issue_out(issue: DeviceIssue, device_name: str | None = None) -> dict[str, Any]:
    return {
        "id": issue.id,
        "device_id": issue.device_id,
        "device_name": device_name,
        "account_id": issue.account_id,
        "kind": issue.kind,
        "severity": issue.severity,
        "reason": issue.reason,
        "summary": describe(issue),
        "detail": issue.detail,
        "fw_version": issue.fw_version,
        "created_at": issue.created_at,
    }
