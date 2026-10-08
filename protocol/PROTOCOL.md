# ola Device Protocol — v1

Contract between the watch firmware and the ola server. Source of truth for both sides.
Any incompatible change increments `protocol_version`.

## 1. Transport

- One WebSocket per device: `<server_url>` e.g. `ws://192.168.1.10:8765/ws/device` (dev) or
  `wss://api.buddyai.example/ws/device` (prod). The firmware treats both identically.
- **Text frames** carry JSON control messages (UTF-8).
- **Binary frames** carry audio (one Opus packet per frame, see §4).
- Keepalive: the device sends `ping` every 15 s when idle; the server closes a session after
  45 s without any frame. WebSocket-level ping/pong is also accepted.

## 2. Envelope

Every JSON message has these fields:

| Field | Type | Notes |
|---|---|---|
| `type` | string | Message type (see §3). |
| `protocol_version` | int | `1`. |
| `session_id` | string \| null | Assigned by the server in `hello_ack`; `null` before that. |
| `turn_id` | uint32 \| null | Conversation turn; `null` when not turn-related. |
| `sequence_number` | uint32 | Monotonic per direction per session, starting at 1. |
| `timestamp` | int64 | Sender clock, ms since Unix epoch (`0` if the device clock is not set yet). |

Message-specific fields sit at the top level next to the envelope fields.

### Turns

- A **turn** = one user utterance → STT → LLM → TTS reply.
- `turn_id` is a `uint32` generated **by the device** at `listen_start`, strictly increasing within
  a session (start at 1). The server maps `(session_id, turn_id)` to its own global turn record.
- At most one turn is active. Starting a new turn implicitly aborts the previous one.

## 3. Messages

### 3.1 Device → Server

| type | Fields | Meaning |
|---|---|---|
| `hello` | `device_id`, `fw_version`, `hw_model`, `token?`, `pairing_code?`, `audio: {uplink_rate, downlink_rates[]}`, `boot?`, `link?` | First message. `token` for a paired device, `pairing_code` (6 digits) for an unpaired one. Diagnostic reports (token hello only, repeated until a `hello_ack`; admin **ola Diagnostics**, see §3.1.1): `boot: {reset_reason, prev_uptime_s?, …}` on the first session after a restart (`reset_reason`: `power_on`\|`software`\|`panic`\|`int_wdt`\|`task_wdt`\|`wdt`\|`brownout`\|`usb`\|…; `prev_uptime_s` when it survived the restart), `link: {drop, offline_ms, mid_turn, wifi_reason?, rssi?, session_s?, …}` when the previous session ended or connecting failed (`drop`: `wifi_lost`\|`ws_error`\|`ws_disconnected`\|`ws_closed`\|`reconnect`\|`reboot`\|`connect_failed`\|`hello_timeout`). |
| `listen_start` | `turn_id`, `request_id?`, `language?`, `mode?`, `note?`, `reminder?` | User tapped the mic; uplink audio for `turn_id` follows. `request_id`: a fresh random id (16-64 characters `[0-9A-Za-z_-]`, the firmware sends 32 hex) for this new turn. The server admits and charges one request id at most once. A resent `listen_start` with an id it already answered gets `turn_end {status, duplicate: true}` (no new turn, no cost); one still running gets `error duplicate`. The watch never re-sends an id automatically: a retry by the user is a new turn with a new id. Older firmware omits it (identified by session + `turn_id`). `language`: `"auto"` or an ISO 639-1 code. `mode: "note"` + `note` (number): note edit mode - the sentence only edits that note (line operations, low-cost model, no spoken reply); the watch starts the next `listen_start` itself while its mic stays open. `mode: "reminder"` + `reminder` (number): reminder edit mode, the same for one reminder (change its time, end, advance notice, place, people, text or completed state, delete it, undo; after a change `item_show` shows it again, after a delete `items_open` opens the list). A turn with no speech in either mode is not stored or billed. |
| `listen_end` | `turn_id` | The user stopped listening (note mode's stop button): end the sentence now and process what was said - unlike `abort`, which discards it. |
| `abort` | `turn_id`, `reason` (`user_tap`\|`timeout`\|`error`) | Cancel the given turn (tap-to-interrupt). |
| `playback_started` | `turn_id` | First downlink audio frame of the turn was received and queued (TTFA end point, §6). |
| `playback_done` | `turn_id` | Device finished playing the reply. |
| `settings_changed` | `base_version`, `changes: {…}` | User changed settings on the watch (subset of §5). |
| `status` | `battery_pct`, `charging`, `rssi`, `free_heap` | Periodic telemetry (≤ 1/min). |
| `ping` | — | Keepalive. |
| `item_open` | `kind` (`note`\|`reminder`), `number` | User tapped an item in the list; server replies `item_show` (or a fresh `items` if it no longer exists). |
| `item_delete` | `kind`, `number` | User deleted an item on the watch; server replies with a fresh `items` to every watch of the account. |
| `item_pin` | `number`, `pinned` | Pin / unpin a note (pinned notes are listed first); server replies with a fresh `items` to every watch of the account. |
| `item_done` | `kind` (`reminder`), `number`, `done` | User completed (`true`) or reopened (`false`) a reminder; server replies with a fresh `items` to every watch of the account. |

#### 3.1.1 Diagnostic reports (firmware 0.1.1+)

All fields are optional and additive: firmware 0.1.0 sends only the fields above, and servers that predate
them ignore the rest. Malformed values are dropped field by field; a hello is never refused because of its
reports.

| Report | Field | Meaning |
|---|---|---|
| `boot` | `report_id` | 8 hex characters, random per report: the server stores a repeated report once. |
| | `prev_session` | The server `session_id` that was live when the chip restarted (RTC memory). The restart joins that session's incident. |
| | `prev_min_heap` | Lowest free heap (bytes) before the restart. |
| | `uptime_s` | Seconds since this boot (dates the restart). |
| `link` | `report_id` | As above. |
| | `prev_session` | The `session_id` of the session that ended. Without it (0.1.0) the server uses the device's previous session, marked `link_basis: "inferred"`. |
| | `turn_id` | The turn that was running (`mid_turn`). |
| | `uptime_s`, `heap`, `min_heap` | At the drop. |
| | `ws` | The websocket client's error details, non-zero fields only: `type` (1 TCP transport, 2 pong timeout, 3 handshake, 4 server close), `tls` (`esp_tls_last_esp_err`, e.g. `0x8001` DNS failure), `tls_stack` (mbedTLS code), `errno` (socket errno), `hs` (HTTP status of the upgrade), `close` (close code received from the server). |
| | `fails`, `retry_ws` | Failed connection attempts after the drop, and the details of the last one that had any. |

`drop: "reboot"` = the session ended because the watch restarted (sent with the `boot` report). `drop:
"connect_failed"` / `"hello_timeout"` = no session ended, but connecting failed `fails` times before this hello.

### 3.2 Server → Device

| type | Fields | Meaning |
|---|---|---|
| `pairing_pending` | `expires_in_s` | Unpaired device registered its code; waiting for the admin. |
| `paired` | `device_token` | Admin accepted the code. Device stores the token and sends a new `hello` with `token`. |
| `hello_ack` | `session_id`, `server_time`, `settings`, `settings_version`, `downlink_rate` | Session established. |
| `listen_stop` | `turn_id`, `reason` (`vad`\|`max_duration`\|`no_speech`) | Server detected end of speech; device stops the uplink. |
| `state` | `turn_id`, `state` (`idle`\|`listening`\|`thinking`\|`speaking`) | UI state hint. |
| `stt_result` | `turn_id`, `text`, `final`, `language?` | Transcript (partials optional). The final result carries the language used for the reply (detected when `auto`). |
| `llm_text` | `turn_id`, `delta` | Reply text as it streams (for on-screen caption). |
| `llm_display` | `turn_id`, `text` | Optional, before the first `llm_text`: the answer's key value (≤ 16 chars, e.g. `21°C`, `14:30`, `£3.50`). The watch shows only this, in large type, while the full reply is spoken. |
| `tts_start` | `turn_id`, `sample_rate`, `language?` | Downlink audio for the turn follows; `language` = reply language. |
| `tts_end` | `turn_id` | No more downlink audio for the turn. |
| `turn_end` | `turn_id`, `status` (`completed`\|`aborted`\|`error`), `expect_reply?` | Server finished the turn. `expect_reply: true` (chat mode): the reply asked something the current operation needs ("which one?", "delete it?") - the watch listens again once the reply has been played. Every such question sets it again, so a clarification followed by a confirmation keeps the mic coming back; a turn without it (done, cancelled, nothing heard) or a tap ends that. Older firmware ignores it (the user taps the mic to answer). |
| `settings_update` | `settings`, `settings_version` | Full device-facing settings (§5). |
| `ota_available` | `version`, `url`, `sha256`, `size`, `signature?` | Firmware update offer. |
| `error` | `code`, `message`, `turn_id?` | See §7. |
| `pong` | — | Reply to `ping`. |
| `languages` | `items: [{code, label, name}]` | Every supported language for the watch's language picker (`label` renderable on the watch, `name` in English for search). Sent after `hello_ack`. |
| `items` | `notes: [{number, preview, subtitle, pinned}]` (pinned first; `preview` = first line = title, `subtitle` = the next line), `reminders: [{number, text, due_local, end_local, notify_before, location, participants, overdue, done}]` | Notes/reminders snapshot (§3.3). Sent after `hello_ack` and whenever the account's items change. |
| `items_open` | `kind` | Open the notes or reminders list (the user asked to see them, or deleted the item from its own screen). After a voice request it is sent **before** `turn_end` (after the fresh `items`); a watch in note / reminder edit mode closes the edit mode on it. |
| `item_show` | `item: {kind, number, text, due_local?, overdue?, done?, pinned?, changed_line?}` | Open this item full-screen (also right after a voice create or change of that item). After a voice request it is sent before `turn_end` (after the fresh `items`). `changed_line` (note mode): the 1-based line just added or changed, to highlight. `listen: true` + `question` (from the chat): the user's words fit several places inside this item; once the chat reply has been played the watch starts this item's edit mode and shows `question` - the item's screen continues from there (older firmware: the item opens, the user taps its mic). |
| `reminder_fire` | `item: {…as item_show}` | A reminder is due: wake the screen, beep, show it full-screen. |
| `notice` | `level` (`info`\|`warning`\|`limit`), `text` | Short account notice, e.g. "You've used 80% of your monthly AI interactions." or, at 100 %, "Monthly AI interactions used up. Renews on 8 Nov." (usage thresholds, once per threshold and billing period). `turn_id: null`; sent after `turn_end`. The watch keeps it until no conversation is running (including playback), then shows it for a few seconds; it never interrupts a conversation. Older firmware ignores it. |

### 3.3 Notes and reminders

Notes and reminders are separate lists that belong to the account, so every watch of the account gets
the same lists. A note is text only (up to 10000 characters); a reminder has a due time, an optional end time
(`end_local`, for a range such as 09:30–10:00, otherwise null) and a short text (up to 80 characters). A reminder whose time has passed is `overdue` until it is completed, deleted or
rescheduled. A completed reminder (`done: true`) is listed after the open ones, never fires and is not
`overdue`; rescheduling it opens it again. Numbers are per kind (note #1 and reminder #1 can both exist); a new item
takes the lowest free number, so after deleting #1 from #1, #2, #3 the next item is #1 again.
`due_local` is `"YYYY-MM-DD HH:MM"` in the watch's time zone. Note rows carry only a `preview` (first
line); the full text arrives with `item_show`. A reminder is delivered once: if no watch is online
when it comes due, it is sent on the next `hello` (up to 24 h late). These messages have `turn_id: null`
and are additive to protocol v1 (older firmware ignores them).

**Voice references, questions and deletions.** Users name items by what they contain ("the list with the
cat food", "the meeting with Stefan on Thursday"); the server finds them (server-side search over the whole
account) and refers to them by a stable id that is never sent to the watch - numbers are reused after a
delete, so an edit-mode `listen_start` whose number now points to another item than the one the server last
showed under it is refused (`error` + `turn_end {status: error}`). When several items could match, the
server asks (chat: spoken; edit modes: `llm_display`) and the next sentence answers. **In the chat
(dialog screen) nothing is deleted by voice in the turn that asks for it**: deleting an item, note lines, or a
reminder's place / people / end / advance alert is only prepared; it runs after an explicit yes in a later
chat turn of the same session (within 120 s; "no", another request, the expiry or opening an item's screen
cancel it). **On an item's own screen (note / reminder edit modes) everything applies at once, deletions
too** - no confirmation there. The one question asked on an item's screen (and in the chat) is *which one*
when the user's words fit several places ("delete the milk" with "Milk" on line 1 and "Whole milk" on line 10;
"without Mihai" with two Mihais); the answer applies it. **After a confirmed deletion in the chat the watch
stays on the dialog** (nothing to show): no `items_open` after an item is deleted, no `item_show` after only
lines or details were removed. The item is shown (`item_show`) only when the same request also added or
changed something. On an item's own screen a change shows it again (`item_show`) and deleting the item opens
the list (`items_open`).

## 4. Binary audio frames

Header: 12 bytes, big-endian, followed by one Opus packet.

| Offset | Size | Field |
|---|---|---|
| 0 | 1 | `kind`: `0x01` uplink mic audio, `0x02` downlink TTS audio |
| 1 | 1 | `flags`: reserved, `0` |
| 2 | 2 | `codec`: `0` = Opus |
| 4 | 4 | `turn_id` (uint32) |
| 8 | 4 | `frame_seq` (uint32, per turn per direction, from 0) |
| 12 | n | Opus packet |

- **Uplink**: Opus, mono, 16 kHz, 60 ms per packet (960 samples), VOIP application.
- **Downlink**: Opus, mono, `sample_rate` from `tts_start` (16000 or 24000), 60 ms per packet.
- No WAV/MP3 containers for realtime audio.

## 5. Device-facing settings

```json
{
  "language": "auto",
  "quick_languages": [
    {"code": "auto", "label": "Auto"},
    {"code": "en", "label": "English"},
    {"code": "ro", "label": "Română"}
  ],
  "volume": 70,
  "brightness": 80,
  "screen_timeout_s": 15,
  "tz_posix": "EET-2EEST,M3.5.0/3,M10.5.0/4",
  "theme": {
    "preset": "midnight",
    "accent": "#4F8CFF",
    "background": "#000000",
    "clock": "#FFFFFF",
    "text": "#B0B8C8"
  },
  "max_listen_s": 35,
  "chat_title": "Coach"
}
```

- `chat_title`: title of the watch's dialog screen, the active persona's name (up to 40 characters);
  `""` for the default persona, when the watch shows its own greeting ("Olá!"). Read-only for the watch;
  resent when the persona is changed, renamed or deleted.

- `max_listen_s`: the watch's whole listening window for one question: the server's wait for the
  first word (`wait_for_speech_s`, default 20) plus the longest question from the first word
  (default 15). Silence before the first word is not sent to speech-to-text.

- The server is the source of truth. `settings_version` increments on every change.
- `language`: `"auto"` (reply in the language the user speaks) or an ISO 639-1 code.
  `quick_languages` (max 3) are the options for the watch's quick toggle; labels are always
  renderable with the watch fonts (Latin, Greek, Cyrillic).
- The watch may change `language`, `preferred_language`, `volume`, `brightness`, `theme`
  through `settings_changed`. The clock is always 24-hour. The server applies them, bumps the version and replies with `settings_update`.
- AI-only settings (persona, model, voice, VAD sensitivity…) stay on the server.

## 6. Cancellation and stale-frame rules

1. The device keeps `active_turn_id`. Any JSON message or binary frame whose `turn_id` differs from
   `active_turn_id` is **dropped** (except `turn_end` for bookkeeping).
2. Tap during `speaking`/`thinking`: the device stops playback, flushes its playback buffer, sends
   `abort {turn_id}`, then immediately starts a new turn with `turn_id + 1` (`listen_start`).
3. On `abort` (or a `listen_start` with a newer `turn_id`) the server cancels STT/LLM/TTS for the old turn,
   drops its queued audio, never sends another frame for it and replies `turn_end {status: "aborted"}`.
4. Result: audio from a previous reply can never play after a new turn has started.

## 7. Error codes

| code | Meaning | Device action |
|---|---|---|
| `protocol_unsupported` | Unknown `protocol_version` | Show "update required", stop reconnecting fast. |
| `unauthorized` | Token invalid/revoked | Delete token, go to pairing screen. |
| `pairing_expired` | Code expired | Generate a new code, send new `hello`. |
| `bad_request` | Malformed message | Log. |
| `stt_failed` / `llm_failed` / `tts_failed` | Provider error during a turn (e.g. no provider credit, timeout) | `message` is a generic "try again later" text; the reason is never shown to the user (server log only). Show "can't answer right now, try again later", go idle. The watch shows the same when a turn times out or the connection drops mid-turn. |
| `busy` | Server overloaded. Older firmware (no `request_id`) also gets it instead of `busy_concurrent`, `service_unavailable` and `duplicate` | Show "Server busy — try again in a moment", go idle. |
| `busy_concurrent` | Other conversations of the same account are running and hold the account's last AI interactions (reply to `listen_start`, followed by `turn_end {status: error}`) | Show "Conversations in progress — try again when they finish", go idle. No automatic retry. |
| `service_unavailable` | The server cannot decide on the admission right now (billing database unreachable, maintenance drain) | Show "Server busy", go idle. |
| `duplicate` | A `listen_start` re-used a `request_id` that is still running | Ignore (stale). |
| `request_conflict` | A `request_id` was re-used for a different request | Log. |
| `subscription_required` | Owner has no active/trial ola Care subscription (reply to `listen_start`, followed by `turn_end {status: error}`) | Show "Subscription needed — open the ola app", go idle. |
| `limit_reached` | The account's AI interactions for the current billing period are used up (ola Care: 1,000 per month, shared by all its watches; AI cost never causes it) | Show "Monthly usage reached — answers again when it resets; extra usage in the app", go idle. |
| `account_inactive` | Owner account suspended or closed (reply to `hello`, then close) | Show "Account inactive — contact support"; retry slowly. |
| `internal` | Unexpected server error | Show error, go idle. |

## 8. Session flow

```
Unpaired:  hello{pairing_code} → pairing_pending → (admin enters code) → paired{device_token}
           → device stores token, sends hello{token}
Paired:    hello{token} → hello_ack
Turn:      listen_start{turn_id} → [0x01 frames] → listen_stop → state thinking → stt_result
           → llm_text* → tts_start → [0x02 frames] → tts_end → turn_end
Device:    playback_started (first frame queued) … playback_done
```

## 9. TTFA (Time To First Audio)

`TTFA = t(first downlink frame received by device) − t(real end of user speech)`.

- Server: end of speech = VAD end-point on the audio timeline (`speech_end_at`), converted to wall clock.
- Server estimate: `first_frame_sent_at − speech_end_at + rtt/2` (RTT from ping/pong).
- Device report: `playback_started.timestamp`, corrected with the clock offset from `hello_ack.server_time`.
- Targets (MVP): p50 < 1.5 s, p95 < 2.5 s.
