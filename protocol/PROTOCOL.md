# BuddyAI Device Protocol — v1

Contract between the watch firmware and the BuddyAI server. Source of truth for both sides.
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
| `hello` | `device_id`, `fw_version`, `hw_model`, `token?`, `pairing_code?`, `audio: {uplink_rate, downlink_rates[]}` | First message. `token` for a paired device, `pairing_code` (6 digits) for an unpaired one. |
| `listen_start` | `turn_id`, `language?` | User tapped the mic; uplink audio for `turn_id` follows. `language`: `"auto"` or an ISO 639-1 code. |
| `abort` | `turn_id`, `reason` (`user_tap`\|`timeout`\|`error`) | Cancel the given turn (tap-to-interrupt). |
| `playback_started` | `turn_id` | First downlink audio frame of the turn was received and queued (TTFA end point, §6). |
| `playback_done` | `turn_id` | Device finished playing the reply. |
| `settings_changed` | `base_version`, `changes: {…}` | User changed settings on the watch (subset of §5). |
| `status` | `battery_pct`, `charging`, `rssi`, `free_heap` | Periodic telemetry (≤ 1/min). |
| `ping` | — | Keepalive. |

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
| `tts_start` | `turn_id`, `sample_rate`, `language?` | Downlink audio for the turn follows; `language` = reply language. |
| `tts_end` | `turn_id` | No more downlink audio for the turn. |
| `turn_end` | `turn_id`, `status` (`completed`\|`aborted`\|`error`) | Server finished the turn. |
| `settings_update` | `settings`, `settings_version` | Full device-facing settings (§5). |
| `ota_available` | `version`, `url`, `sha256`, `size`, `signature?` | Firmware update offer. |
| `error` | `code`, `message`, `turn_id?` | See §7. |
| `pong` | — | Reply to `ping`. |

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
  "time_24h": true,
  "tz_posix": "EET-2EEST,M3.5.0/3,M10.5.0/4",
  "theme": {
    "preset": "midnight",
    "accent": "#4F8CFF",
    "background": "#000000",
    "clock": "#FFFFFF",
    "text": "#B0B8C8"
  },
  "max_listen_s": 15
}
```

- The server is the source of truth. `settings_version` increments on every change.
- `language`: `"auto"` (reply in the language the user speaks) or an ISO 639-1 code.
  `quick_languages` (max 3) are the options for the watch's quick toggle; labels are always
  renderable with the watch fonts (Latin, Greek, Cyrillic).
- The watch may change `language`, `volume`, `brightness`, `theme`, `time_24h`
  through `settings_changed`. The server applies them, bumps the version and replies with `settings_update`.
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
| `stt_failed` / `llm_failed` / `tts_failed` | Provider error during a turn | Show error, go idle. |
| `busy` | Server overloaded | Retry later. |
| `subscription_required` | Owner has no active/trial BuddyAI Care subscription (reply to `listen_start`, followed by `turn_end {status: error}`) | Show "Subscription needed — open the BuddyAI app", go idle. |
| `limit_reached` | Monthly fair-use allowance used up | Show "Monthly limit reached — resets on the 1st", go idle. |
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
