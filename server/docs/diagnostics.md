# ola Diagnostics

Admin → **ola Diagnostics** (`/admin/diagnostics`, API `/api/incidents`, admin only). It shows what went wrong
on the watches, their connections and the server: related events are grouped into one incident, and the cause
is classified deterministically from the evidence (no AI calls). When the evidence doesn't establish a cause,
the incident says so.

Code: `app/incidents/` (`events.py` builds events, `classify.py` the rules, `service.py` storage / grouping /
retry / retention, `texts.py` the plain-English explanations), `app/api/incidents.py`, the hooks in
`app/gateway/device_ws.py`. Firmware: `components/protocol_client` (`diag_codes.c` + the hello reports).

## Event schema

One event = one row of `device_issues` (the table predates diagnostics, so history is preserved).

| Field | Meaning |
|---|---|
| `kind` | `reboot`, `disconnect` (from the watch); `turn_interrupted`, `server_timeout`, `ws_close`, `server_exception`, `provider_failure`, `lease_lost`, `server_shutdown` (from the server) |
| `reason` | Raw reason: reset reason, `link.drop`, close code, exception class, `llm_failed`… |
| `detected_by` | `watch` or `server`: who observed it. **Not** who caused it. |
| `category`, `confidence`, `suspected_component`, `reason_code` | The event's own classification |
| `session_id`, `turn_id` | Correlation identifiers (the server's per-connection session id; the watch's turn number) |
| `occurred_at` | When it happened (a watch report is dated back by `offline_ms`); `created_at` = when it was stored |
| `detail` | Evidence only: Wi-Fi reason, RSSI, `ws{type,tls,tls_stack,errno,hs,close}`, heap, uptime, exception class… Never audio, transcripts, tokens or exception messages; provider error text is capped at 120 characters and redacted if it contains the conversation |
| `dedup_key` | `w:{report_id}:{boot|link}` (watch 0.1.1+), `l:{fingerprint}:{time}` (watch 0.1.0), `s:{session}:{kind}:{turn}` (server). Unique per device |
| `incident_id` | The incident it belongs to |

An incident (`diag_incidents`) holds the result of classifying all its events: `category`, `severity`, `confidence`,
`reason_code`, `suspected_component`, `detected_by`, `primary_event_id`, `occurred_at` (earliest event),
`recovered_at`, `event_count`, `legacy`.

## Correlation

Events are grouped by identifiers, never by time alone:

| Key | Groups |
|---|---|
| `sess:{device}:{session}` | Everything about one connection: the server's view of its end (`ws_close`, `server_timeout`, `turn_interrupted`, `server_exception`, `server_shutdown`) and the watch's report of the same drop (`link.prev_session`, firmware 0.1.1+) or of a restart that cut it (`boot.prev_session`). |
| `turn:{device}:{session}:{turn}` | A turn that failed without a disconnect (provider error or timeout, exception, usage lease lost). |
| `boot:{device}:{dedup}` | A restart not tied to a session (power-on, update). |
| `link:{device}:{dedup}` | A report that names no session (connection attempts that failed since boot). |

Firmware 0.1.0 doesn't name the session in its report. The server then uses `devices.last_session_id`, the
session of the device's previous authenticated hello, which is stored in the database and so survives a server
restart. These links are marked `link_basis: "inferred"` and their confidence is capped at `probable`. A
0.1.0 report repeated after a lost `hello_ack` matches its earlier copy by fingerprint, within 120 s of the
drop time (10 min for restarts), so it doesn't join the newer session.

**Arrival order.** Events can arrive in any order and late: the watch's report comes after it reconnects, the
server's events at teardown. Each insert locks the incident row (`SELECT … FOR UPDATE` on PostgreSQL),
re-reads all its events and reclassifies from scratch, so the result doesn't depend on order.

**Duplicates.** The unique index on `(device_id, dedup_key)` makes a duplicate a no-op, including concurrent
writers and retries.

## Classification rules

The strongest evidence wins: confirmed beats probable, which beats unknown. When confidence is equal, Watch
beats Server, which beats Connection, which beats Undetermined, because a fault inside the watch or the server
explains a dropped connection better than the drop explains them. A secondary event (`turn_interrupted`,
`drop: "reboot"`) is never the cause.

| Evidence | Category / component | Confidence |
|---|---|---|
| Reset reason `panic`, `cpu_lockup` / `int_wdt`, `task_wdt`, `wdt` | Watch / firmware (`watch_crash` / `watch_freeze`) | confirmed |
| Reset reason `brownout`, `pwr_glitch` | Watch / power | confirmed |
| Routine reset (`power_on`, `software`, `usb`…) | Watch, info | confirmed |
| `server_shutdown` event, or server-side close code 1012 | Server / process | confirmed |
| Server exception (class only) | Server / gateway or database | confirmed |
| Provider error, by stage (`*_timeout` when it timed out) | Server / stt, llm or tts | confirmed |
| Usage lease lost | Server / database | probable |
| `link.drop = wifi_lost` (+ `wifi_reason`; RSSI ≤ −80 dBm → `wifi_weak_signal`) | Connection / wifi | confirmed (probable if inferred) |
| `ws.tls = 0x8001` (`ESP_ERR_ESP_TLS_CANNOT_RESOLVE_HOSTNAME`) | Connection / dns | confirmed |
| Close code from the server 1011 / 1012 / 1013 | Server / process | confirmed |
| Close code 4001 / 4002 (token revoked, operator disconnect) | Server / gateway, info | confirmed |
| Close code 4000 (replaced by the watch's own newer connection) | Connection, info | probable |
| Close code 1001 "going away" (sent by the proxy, never by the ola server) | Undetermined | unknown |
| Handshake HTTP 5xx | Server / proxy | probable |
| TLS handshake errors (`0x8009`, `0x8015`, `0x801A`) | Connection / tls | probable |
| `0x8004`, `0x8006`, or errno `ECONNRESET`, `ETIMEDOUT`, `ECONNREFUSED`, `EHOSTUNREACH`… | Connection / tcp | probable |
| `ws.type = 2` (pong timeout) | Connection / network | probable |
| `min_heap` < 20 KB, nothing stronger | Watch / memory | probable |
| Only the reconnect attempts carry a network error (`retry_ws`) | Connection | probable |
| Generic `ws_error`, `ws_closed`, `ws_disconnected` with no details; `ws_close` 1006; `server_timeout` alone | **Undetermined** | unknown |

`ws_error` and `ws_closed` never put the blame on the server by themselves. Only a close code that only the
server sends, a 5xx, or a server-side event does.

Severity: firmware faults and server failures are `error`, drops are `warn`, routine restarts are `info`. A
secondary event never raises an incident to `error`.

Recovery (`recovered_at`):
- a connection or restart incident recovers at the watch's next authenticated hello;
- a turn incident recovers at the watch's next completed turn;
- a server event stored after the watch has already reconnected is recovered as soon as it is stored;
- legacy incidents show "Not tracked" and are excluded from the "Not recovered" filter.

Recovery marks that fail to store are retried like events. One narrow race remains: the old session's
teardown write and the new hello's recovery update run in parallel transactions. The write re-reads the
device's latest session just before its commit, which closes most of it. If the race is still lost, the
incident shows as open until the watch's next session.

## Reliability

- Events are written off the event loop (`asyncio.to_thread`), fire-and-forget from the gateway. A diagnostics
  failure is logged and never reaches the watch.
- If storing fails (database unavailable), events and recovery marks wait in a bounded in-memory queue (500
  writes, oldest dropped). The queue is per process and is lost on restart. `maintenance_loop` retries every 30 s. Dedup keys make the retry safe.
- A server shutdown is recorded as the cause of the sessions it ends: uvicorn closes them with code 1012, which the
  server stores as `ws_close` 1012, and any session still registered when the shutdown hook runs also gets a
  `server_shutdown` event. Pending writes are drained (5 s cap).
- Retention: `BUDDYAI_INCIDENT_RETENTION_DAYS` (default 90; `0` keeps everything). Incidents not updated
  since then are deleted with their events, hourly.
- Firmware: reports live in RAM until `hello_ack`, plus a 56-byte checksummed RTC block (uptime, minimum
  heap, live session id, running turn). There are no flash writes and no heap allocations. Error details are
  copied out of the websocket event in its callback under a spinlock.

## Firmware / server compatibility

| Watch | Server | Result |
|---|---|---|
| 0.1.0 | new | Works. Reports are grouped by inferred session, dedup by fingerprint, no `ws` details (generic drops stay Undetermined). |
| 0.1.1 | new | Full evidence: session and turn ids, `report_id`, websocket/TLS/errno details, heap, restarts that cut a session. |
| 0.1.1 | old (pre-0026) | Works: the old server reads `boot` / `link` as before and ignores the new fields. |

## Deployment

1. Deploy the server. Migration `0026` runs at start-up: it adds one table and nullable columns, and copies
   each existing issue into a legacy incident. The copy uses only what was stored: reset reasons and
   `wifi_lost` become Watch / Connection, everything else Undetermined, and no session, turn or recovery data
   is invented.
2. Check Admin → ola Diagnostics, and Admin → Usage & performance (renamed from "Usage & diagnostics").
3. Publish firmware 0.1.1 (Admin → Firmware) and send the OTA update.

**Rollback.**
- Server: `alembic downgrade 0025` drops the new table and columns. The old event rows stay, with their
  original fields.
- Firmware: 0.1.0 can be reinstalled. Its RTC layout has a different magic number, so neither version
  misreads the other's data after a restart.

## Troubleshooting by reason code

| Reason code | What to check |
|---|---|
| `wifi_lost`, `wifi_weak_signal` | Customer's router and distance; `wifi_reason` 15/202/204: password; 201: network not found |
| `dns_failure`, `tcp_connect_failure`, `tls_failure` | Customer's internet, captive portal or filtering. If many watches report it at once: server DNS, certificate, reachability |
| `connection_timeout`, `tcp_failure` | Network quality; if many watches at once: server load |
| `server_unavailable`, `server_closed_*`, `server_shutdown` | Deploy times, server logs, uptime monitor (`/readyz`) |
| `server_exception` | Server log at that time: the exception class is in the Technical view |
| `stt_*`, `llm_*`, `tts_*` | Provider status, quota and credit (Admin → System → Test connection) |
| `usage_lease_lost` | Database health and latency |
| `watch_crash`, `watch_freeze`, `watch_low_memory` | Firmware version, what the watch was doing; collect serial logs |
| `watch_power_fault`, `watch_power_lost` | Battery level history, cable and charger |
| `link_lost_unknown`, `connection_closed_unknown`, `watch_silent` | Not enough evidence. Wait for the watch's report; update the watch to 0.1.1 for detailed errors |

## Hardware validation checklist (needs a real watch)

- [ ] Switch the router off during a conversation, then back on. Expected: one **Connection / wifi_lost**
      incident, confirmed, with turn interrupted grouped under it, recovered on reconnect.
- [ ] Walk out of range slowly. Expected: `wifi_weak_signal`, or `wifi_lost` with RSSI in the evidence.
- [ ] Point the watch at a hostname that doesn't resolve. Expected: **Connection / dns_failure**
      (`ws.tls 0x8001`), reported as `connect_failed` with `fails`.
- [ ] Block the server port (connection refused). Expected: `tcp_connect_failure` or `tcp_failure` with errno.
- [ ] Restart the server during a turn. Expected: **Server / server_shutdown**, confirmed; the watch's report
      joins the same incident.
- [ ] Stop the server process behind the proxy. Expected: `server_unavailable` (handshake 502/503).
- [ ] Force a watchdog reset (debug build) during a session. Expected: **Watch / watch_freeze**, with the
      server's view of the drop grouped under it (`boot.prev_session`).
- [ ] Unplug the battery or cable during a session. Expected: a routine `power_on`. The RTC is lost on power
      loss, so the session can't be named.
- [ ] Repeat a drop with the `hello_ack` blocked. Expected: the report is resent and stored once.
- [ ] Confirm that audio streaming and turn latency are unchanged (Admin → Usage & performance →
      Performance).
