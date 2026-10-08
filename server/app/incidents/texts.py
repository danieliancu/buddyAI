"""ola Diagnostics: plain British-English explanations per reason code (the Simple view)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.incidents.classify import CONFIRMED, PROBABLE, ROUTINE_RESETS

# reason_code -> (short title, cause, recommended action)
REASONS: dict[str, tuple[str, str, str]] = {
    "watch_crash": (
        "The watch firmware crashed and restarted",
        "The firmware hit a fatal error (panic or CPU lock-up) and the chip restarted itself.",
        "If it happens again, note what the watch was doing and send the logs to the firmware team. "
        "Check whether a firmware update is available.",
    ),
    "watch_freeze": (
        "The watch firmware froze and the watchdog restarted it",
        "A watchdog timer fired because part of the firmware stopped responding.",
        "Note what the watch was doing. Repeated freezes on the same firmware version need a firmware fix.",
    ),
    "watch_power_fault": (
        "The watch restarted because its power supply dropped",
        "The supply voltage fell too low (brownout or power glitch) - usually a weak or empty battery, "
        "or a poor USB cable or charger.",
        "Charge the watch fully. If it is on a cable, try a different cable and charger.",
    ),
    "watch_power_lost": (
        "The watch lost power while it was connected",
        "The watch powered up from cold after a session that had not ended: the battery probably ran flat, "
        "or the watch was switched off.",
        "Check the battery level history. If the battery was not low, ask whether the watch was switched off.",
    ),
    "watch_restart": (
        "The watch restarted normally",
        "A routine restart: power on, a firmware update, setup, or a reset over USB.",
        "No action needed.",
    ),
    "watch_restart_unknown": (
        "The watch restarted for an unrecognised reason",
        "The chip reported a reset reason that ola does not recognise.",
        "No action needed unless it repeats; then check the firmware logs.",
    ),
    "watch_restarted_in_session": (
        "The session ended because the watch restarted",
        "The watch restarted while it was connected; see the restart event for the reason.",
        "See the restart event.",
    ),
    "watch_low_memory": (
        "The connection dropped while the watch was very low on memory",
        "The lowest free memory recorded on the watch was very small, which can make the network stack fail.",
        "Report it to the firmware team with the firmware version. A restart of the watch clears it.",
    ),
    "reconnect_requested": (
        "The watch reconnected on request",
        "The connection was restarted on purpose by the watch (for example after a Wi-Fi or server change).",
        "No action needed.",
    ),
    "wifi_lost": (
        "The watch lost its Wi-Fi connection",
        "The Wi-Fi link between the watch and the access point dropped.",
        "Check the customer's Wi-Fi and router. If the reason mentions authentication, check the password.",
    ),
    "wifi_weak_signal": (
        "The watch lost Wi-Fi with a weak signal",
        "The Wi-Fi link dropped while the signal was weak (RSSI at or below -80 dBm).",
        "Ask the customer to use the watch closer to the router, or add a Wi-Fi extender.",
    ),
    "dns_failure": (
        "The watch could not look up the server's address",
        "The DNS lookup for the server's name failed: the network had no working internet or DNS.",
        "Check the customer's internet connection. If many watches report this, check the server's DNS records.",
    ),
    "tls_failure": (
        "The secure (TLS) connection could not be set up",
        "The TLS handshake between the watch and the server failed. Network filtering, a wrong clock or a "
        "certificate problem can cause this.",
        "Check the server certificate. If only one network is affected, check for a captive portal or filtering.",
    ),
    "tcp_connect_failure": (
        "The watch could not reach the server",
        "The TCP connection to the server could not be opened (refused or timed out).",
        "Check the customer's internet. If many watches report this at once, check that the server is reachable.",
    ),
    "tcp_failure": (
        "The network connection was reset or timed out",
        "The TCP connection between the watch and the server failed (reset, timeout or unreachable network).",
        "Usually temporary. If it repeats on one watch, check the customer's network.",
    ),
    "connection_timeout": (
        "The connection stopped responding",
        "The watch stopped receiving answers to its keep-alive pings, so it closed the connection.",
        "Usually a network problem. If many watches report this at once, check the server's load.",
    ),
    "server_unavailable": (
        "The server's front door answered, but the ola server did not",
        "The proxy returned a 5xx error to the watch's connection request: the ola server process was "
        "probably down or restarting.",
        "Check the server logs and uptime monitoring around this time.",
    ),
    "server_closed_internal_error": (
        "The server closed the connection after an internal error",
        "The server sent close code 1011 (internal error).",
        "Check the server logs for an exception at this time.",
    ),
    "server_closed_service_restart": (
        "The server restarted",
        "The server sent close code 1012 (service restart): a deploy or restart.",
        "No action needed if a deploy was planned.",
    ),
    "server_closed_try_again_later": (
        "The server was overloaded",
        "The server sent close code 1013 (try again later).",
        "Check server load.",
    ),
    "server_closed_going_away": (
        "The server was shutting down",
        "The server sent close code 1001 (going away): a deploy or restart.",
        "No action needed if a deploy was planned.",
    ),
    "server_replaced": (
        "A newer connection from the same watch replaced this one",
        "The watch connected again before the old connection was closed, so the old link had probably "
        "already gone.",
        "No action needed unless it repeats often; then check the customer's network.",
    ),
    "closed_going_away": (
        "The connection was closed as 'going away'",
        "Close code 1001 arrived. The ola server does not send it; the proxy in front of it does when it "
        "reloads or restarts. That does not show whether anything was wrong.",
        "If it coincides with a deploy or proxy reload, no action is needed.",
    ),
    "server_token_revoked": (
        "The watch's access was revoked",
        "An operator or the owner removed the watch.",
        "No action needed.",
    ),
    "server_disconnected_by_operator": (
        "An operator disconnected the watch",
        "The connection was closed from the admin.",
        "No action needed.",
    ),
    "server_shutdown": (
        "The server shut down while the watch was connected",
        "The ola server process stopped (deploy or restart) and closed the session.",
        "No action needed if a deploy was planned; otherwise check why the server stopped.",
    ),
    "server_exception": (
        "The server hit an unexpected error",
        "An exception was raised in the ola server while handling this watch.",
        "Look up the exception in the server logs at this time and fix the code path.",
    ),
    "usage_lease_lost": (
        "The server lost its usage record for a running turn",
        "The turn's usage lease could not be renewed, usually because the database was slow or unreachable.",
        "Check the database's health and latency at this time.",
    ),
    "stt_failure": ("Speech recognition failed", "The speech-to-text provider returned an error.",
                    "Check the STT provider's status and credit."),
    "stt_timeout": ("Speech recognition timed out", "The speech-to-text provider did not answer in time.",
                    "Check the STT provider's status. Repeated timeouts may need a provider switch."),
    "llm_failure": ("The AI model failed to answer", "The language-model provider returned an error.",
                    "Check the LLM provider's status, quota and credit."),
    "llm_timeout": ("The AI model timed out", "The language-model provider did not answer in time.",
                    "Check the LLM provider's status and latency."),
    "tts_failure": ("Voice generation failed", "The text-to-speech provider returned an error.",
                    "Check the TTS provider's status and credit."),
    "tts_timeout": ("Voice generation timed out", "The text-to-speech provider did not answer in time.",
                    "Check the TTS provider's status and latency."),
    "embedding_failure": ("Memory search failed", "The embeddings provider returned an error.",
                          "Check the embeddings provider's status and credit."),
    "embedding_timeout": ("Memory search timed out", "The embeddings provider did not answer in time.",
                          "Check the embeddings provider's status."),
    "provider_failure": ("An AI provider failed", "An AI provider returned an error.",
                         "Check the providers' status (Admin → System → Test connection)."),
    "provider_timeout": ("An AI provider timed out", "An AI provider did not answer in time.",
                         "Check the providers' status and latency."),
    "watch_silent": (
        "The server stopped hearing from the watch",
        "No data arrived from the watch for the idle timeout, so the server closed the session. "
        "This alone does not show whether the watch, its network or the internet was at fault.",
        "Wait for the watch's own report after it reconnects; it often names the cause.",
    ),
    "connection_closed_unknown": (
        "The connection closed without a reason",
        "The server saw the connection end without a clean close. Nothing so far shows which side caused it.",
        "Wait for the watch's report after it reconnects. If it never reconnects, check the watch and its Wi-Fi.",
    ),
    "link_lost_unknown": (
        "The connection was lost; the cause is not known",
        "The watch reported a generic connection error with no further details. This does not show whether "
        "the watch, its network or the server was at fault.",
        "If it repeats, update the watch to firmware 0.1.1 or later, which reports the exact error.",
    ),
    "connect_failed_unknown": (
        "The watch could not connect; the cause is not known",
        "Connection attempts failed without a recognisable error.",
        "Check the customer's internet and the server's reachability.",
    ),
    "hello_timeout": (
        "The server did not answer the watch's hello in time",
        "The connection opened, but no reply arrived. A slow network or a busy server can both cause this.",
        "If many watches report it at once, check the server's load.",
    ),
    "turn_interrupted": (
        "A conversation was cut off",
        "A conversation turn was still running when the connection closed.",
        "See the related events for why the connection closed.",
    ),
    "unknown": ("Unclassified event", "No rule matches this event.", "Check the technical details."),
}

WHERE = {
    "watch": "On the watch itself.",
    "connection": "Between the watch and the server (Wi-Fi, the customer's internet or the network path).",
    "server": "On the ola server or one of its providers.",
    "undetermined": "Not determined from the evidence available.",
}

_CONF_PREFIX = {CONFIRMED: "Confirmed: ", PROBABLE: "Probably: "}


def reason(reason_code: str) -> tuple[str, str, str]:
    """(title, cause, action); an unlisted provider stage falls back to the generic provider texts."""
    if reason_code in REASONS:
        return REASONS[reason_code]
    if reason_code.endswith("_timeout"):
        return REASONS["provider_timeout"]
    if reason_code.endswith("_failure"):
        return REASONS["provider_failure"]
    return REASONS["unknown"]


def title(reason_code: str) -> str:
    return reason(reason_code)[0]


def _fmt_s(s: int) -> str:
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min {s % 60} s"
    return f"{s // 3600} h {s % 3600 // 60} min"


def explain(inc: Any, events: list[Any]) -> dict[str, str]:
    """The six Simple-view answers for an incident (a DiagIncident and its DeviceIssue events)."""
    _, cause, action = reason(inc.reason_code)
    if inc.confidence not in _CONF_PREFIX:
        cause = "Not known. " + cause
    else:
        cause = _CONF_PREFIX[inc.confidence] + cause
    kinds = {e.kind for e in events}
    mid_turn = "turn_interrupted" in kinds or any((e.detail or {}).get("mid_turn") for e in events)
    affected = []
    if mid_turn:
        affected.append("A conversation was cut off before the answer finished.")
    if inc.category == "server" and inc.turn_id is not None and not mid_turn:
        affected.append("One conversation turn failed; the watch was told to try again later.")
    offline = max((int((e.detail or {}).get("offline_ms") or 0) for e in events), default=0)
    if offline:
        affected.append(f"The watch was offline for about {_fmt_s(offline // 1000)}.")
    if any(e.kind == "reboot" and e.reason not in ROUTINE_RESETS for e in events):
        affected.append("The watch restarted.")
    if not affected:
        affected.append("The watch's connection to ola." if inc.category != "server" else "The ola service for this watch.")
    if inc.recovered_at is not None:
        recovered = f"Yes - recovered at {_hhmm(inc.recovered_at)}."
    elif inc.legacy:
        recovered = "Not recorded (this event predates ola Diagnostics)."
    else:
        recovered = "Not yet - the watch has not reconnected or completed a turn since."
    return {
        "what": title(inc.reason_code),
        "where": WHERE.get(inc.category, WHERE["undetermined"]),
        "cause": cause,
        "affected": " ".join(affected),
        "recovered": recovered,
        "next_step": action,
    }


def _hhmm(dt: datetime) -> str:
    return dt.strftime("%d %b %Y, %H:%M:%S UTC")
