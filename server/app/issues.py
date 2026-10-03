"""Watch issues (admin Issues tab): reboots and lost connections reported by the watch in its hello
(PROTOCOL.md §3.1 "boot" / "link"), plus session problems the server sees itself."""

from __future__ import annotations

from typing import Any

from app.db.models import DeviceIssue
from app.db.repositories import IssueRepo
from app.db.session import session_scope

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
    "ws_error": "Connection error (network or server unreachable)",
    "ws_disconnected": "Connection dropped (no answer to pings)",
    "ws_closed": "The server closed the connection",
    "hello_timeout": "The server did not answer the hello",
    "reconnect": "Reconnect requested on the watch",
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


def from_hello(msg: dict[str, Any]) -> list[DeviceIssue]:
    """Issues carried by a token hello: "boot" (first hello after a restart) and "link" (the
    previous session of this boot ended)."""
    out: list[DeviceIssue] = []
    boot = msg.get("boot")
    if isinstance(boot, dict):
        reason = str(boot.get("reset_reason") or "unknown")[:64]
        up = _int(boot.get("prev_uptime_s"))
        detail = {"prev_uptime_s": up} if up is not None else {}
        out.append(
            DeviceIssue(kind="reboot", reason=reason, severity=RESET_REASONS.get(reason, ("warn", ""))[0], detail=detail)
        )
    link = msg.get("link")
    if isinstance(link, dict):
        reason = str(link.get("drop") or "unknown")[:64]
        detail: dict[str, Any] = {"mid_turn": bool(link.get("mid_turn"))}
        for k in ("offline_ms", "wifi_reason", "rssi", "session_s"):
            if _int(link.get(k)) is not None:
                detail[k] = _int(link.get(k))
        out.append(DeviceIssue(kind="disconnect", reason=reason, severity="warn", detail=detail))
    return out


def record(device_id: str, account_id: int | None, fw: str, issues: list[DeviceIssue]) -> list[dict[str, Any]]:
    """Store the issues; returns them as issue_out() dicts (for the live event)."""
    saved = []
    with session_scope() as db:
        repo = IssueRepo(db)
        for i in issues:
            i.device_id, i.account_id, i.fw_version = device_id, account_id, fw
            saved.append(issue_out(repo.add(i)))
    return saved
