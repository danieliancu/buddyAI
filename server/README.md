# ola Server

FastAPI server: device gateway (WebSocket, [PROTOCOL.md](../protocol/PROTOCOL.md)), voice pipeline, REST API and web app hosting.

```
Watch ──Opus 16k──► /ws/device ─► Opus decode ─► Silero VAD ─► STTProvider
                                                              ─► LLMProvider (streaming)
                                                              ─► SemanticSpeechChunker
                                                              ─► TTSProvider
Watch ◄──Opus 24k── PCM → Opus (turn_id + frame_seq) ◄───────┘
```

## AI profiles (switch vendors with one variable)

`BUDDYAI_AI_PROFILE` in `.env` selects `config/providers.<profile>.json` (restart the server after changing it):

| Profile | STT | LLM (default / option) | TTS | Keys |
|---|---|---|---|---|
| `openai` (default) | `gpt-live-transcribe` (realtime) | `gpt-6-luna` / `gpt-6-sol` | `gpt-4o-mini-tts` (RO + EN) | `BUDDYAI_OPENAI_API_KEY` |
| `qwen` | `qwen-audio-3.1-asr-flash-streaming` | `qwen-flash` / `qwen-plus` | EN: `qwen3-tts-flash-realtime`, RO: Azure Neural | `BUDDYAI_DASHSCOPE_API_KEY`, `BUDDYAI_AZURE_SPEECH_KEY` |

Models, voices, TTS style instructions and default prices live in the profile file, so changing a
model means editing JSON, not code. A new vendor = one class per stage in `app/providers/` + a line in
`providers/router.py`. Use **Sistem → Test conexiune** in the web app to check each stage.

## Quick start (Windows, PowerShell)

```powershell
cd server
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env          # fill in keys, or set them later from the web app (Sistem)
.\.venv\Scripts\python -m app.main
```

- Web app: `http://<pc-ip>:8765` (build it first: `cd ..\web; npm install; npm run build`).
- Device endpoint (what the watch needs as `server_url`): `ws://<pc-ip>:8765/ws/device`.
- Allow TCP 8765 (and UDP 5353 for mDNS) in Windows Firewall for private networks.
- Without API keys: set `BUDDYAI_MOCK_PROVIDERS=true`. The whole pipeline then runs with offline
  mock STT/LLM/TTS, which is useful for protocol and firmware work.

Requires Python ≥ 3.11. Database: SQLite in `data/`, migrations applied automatically at startup (Alembic).

## Configuration

| What | Where |
|---|---|
| Ports, URLs, mock mode, keys | `.env` (`BUDDYAI_*`, see `.env.example`) |
| API keys set from the web app | `data/provider_keys.json` (env wins) |
| Models, voices, chunker, audio rates, default prices | `config/providers.<profile>.json` |
| Prices used for cost estimates | DB table `pricing_rules` (seeded from config, editable in the web app) |

Switching providers or models means changing `BUDDYAI_AI_PROFILE` or editing the profile file. The pipeline only sees the
`STTProvider` / `LLMProvider` / `TTSProvider` interfaces (`app/providers/*/base.py`).

## Watch simulator (`tools/fake_watch.py`)

Runs the real protocol (hello, pairing, Opus streaming, abort, reconnect) and measures TTFA.

```powershell
.\.venv\Scripts\python tools\fake_watch.py pair                                   # enter the code in the web app
.\.venv\Scripts\python tools\fake_watch.py ask --wav tests\samples\ro_1.wav --lang ro --save out\reply.wav
.\.venv\Scripts\python tools\fake_watch.py bench --wav-dir tests\samples --lang en --repeat 3 --strict
.\.venv\Scripts\python tools\fake_watch.py abort-test --wav tests\samples\en_2.wav --abort-after-ms 400
.\.venv\Scripts\python tools\fake_watch.py reconnect-test --wav tests\samples\en_3.wav
.\.venv\Scripts\python tools\fake_watch.py scenario tests\scenarios\smoke.json
```

`tools/make_samples.ps1` regenerates the sample WAVs with Windows voices. The Romanian samples are
spoken by an English voice, so they only exercise VAD and latency. **Record real Romanian speech**
(`tests/samples/ro_*.wav`) to evaluate Romanian STT quality.

TTFA = real end of the user's speech → first reply audio frame received by the watch
(targets: p50 < 1.5 s, p95 < 2.5 s). The simulator reports it per turn together with the stage timeline.
The server stores per-stage latencies for every turn (web app → Usage & Diagnostics).

## Tests

```powershell
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
```

Unit tests cover the chunker, protocol framing, Opus codec, VAD endpointing and settings. End-to-end
tests start a real server with mock providers and drive it with the simulator, including
abort/stale-frame checks.

## Accounts, shop and roles

- **Operators** (you): `/admin` in the web app, `app/api/devices.py`, `accounts_admin.py`, `usage.py`,
  `system.py`, `firmware.py`, orders in `shop.py`. On a public server, create the operator with
  `python -m app.cli create-operator <name>` (web setup is disabled by `BUDDYAI_ALLOW_WEB_SETUP=false`).
- **Customers**: `/api/me/...` (`app/api/me.py`). Every device route checks ownership (`DeviceRepo.owned`),
  and other customers' watches answer 404. History never carries over when a watch changes owner.
  Customers can export or delete their data.
- **Emails** (`app/email.py`): printed to the log by default (`BUDDYAI_EMAIL_BACKEND=console`), SMTP in production.
- **Shop** (`app/billing.py`, `app/api/shop.py`): one Stripe Checkout sells the watch together with the
  "ola Care" subscription (trial first). Webhooks keep orders and subscriptions in sync.
  `app/entitlements.py` refuses turns without an active or trial subscription, or once the monthly
  fair-use allowance is used up.
  Billing stays off until `BUDDYAI_STRIPE_SECRET_KEY` is set, so every watch is then allowed.
- **Languages** (`app/languages.py`, `config/languages.json`): 57 languages; `auto` detects the
  spoken language (lingua).

Deployment (Docker, PostgreSQL, HTTPS, backups): see [deploy/README.md](../deploy/README.md).

## Layout

```
app/gateway/     device_ws.py (session, turns, cancellation), hub.py (connections, pairing, live events), protocol.py
app/pipeline/    conversation.py (ConversationPipeline), chunker.py, vad.py, turn.py, metrics.py
app/providers/   stt/ llm/ tts/ (base + qwen/azure), mock.py, router.py
app/pricing/     ProviderPricingConfig
app/db/          models.py, repositories.py, session.py      migrations/ (Alembic)
app/api/         auth, devices, usage (+diagnostics, pricing), system (keys, provider tests), firmware (OTA), live (web events)
tools/           fake_watch.py, make_samples.ps1
```

## Known open points

- **Romanian STT/TTS quality** is not verified for either profile yet. OpenAI docs do not list
  Romanian explicitly for `gpt-live-transcribe`, and Qwen docs do not confirm it for its ASR model. Validate
  with real Romanian recordings (`fake_watch.py ask --lang ro`).
- VAD uses Silero v5 ONNX on one thread (`models/silero_vad.onnx`). Multi-threaded VAD backends stall
  under CPU load, so do not switch back to them.
- VAD end silence (`vad_sensitivity`: 500/700/900 ms) is the largest fixed part of TTFA.
- Production hardening (M5): WSS/HTTPS, login rate limiting, signed OTA images.
