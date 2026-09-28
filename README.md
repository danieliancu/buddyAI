# BuddyAI

AI voice companion watch (Waveshare ESP32-S3-Touch-AMOLED-2.06), sold directly to consumers in the UK and EU.

| Folder | What |
|---|---|
| [protocol/](protocol/PROTOCOL.md) | Watch ↔ server protocol v1 (WebSocket, JSON + Opus) |
| [server/](server/README.md) | FastAPI server: watch gateway, voice pipeline (OpenAI or Qwen), customer accounts, shop + subscription (Stripe), REST API |
| [web/](web/) | Web app: customer area (`/my`) and operator area (`/admin`) |
| [site/](site/) | Public marketing site (Astro, static) |
| [firmware/](firmware/README.md) | Watch firmware (ESP-IDF 5.5, LVGL 9) |
| [deploy/](deploy/README.md) | Production: Docker, Caddy (HTTPS), PostgreSQL, backups — **step-by-step server runbook** |

## Run everything on your PC

```powershell
cd C:\_work\BuddyAI\server
.\.venv\Scripts\python -m app.main
```

- **Web app:** http://localhost:8765
  - `/signup` and `/my` for customers;
  - `/admin` for you, the operator.
  - Emails (confirmation, password reset) are printed in the server window.
- **AI:** put `BUDDYAI_OPENAI_API_KEY=sk-...` in `server\.env`, or enter it under **Admin → System**.
  Without a key, run `$env:BUDDYAI_MOCK_PROVIDERS="true"` before starting the server to get simulated answers.
- **Marketing site:** `cd site; npm run dev`, then open http://localhost:4321.
- **Shop:** it stays closed until Stripe keys are set; see [deploy/README.md](deploy/README.md) §6.

To rebuild the web app after changing it, run `cd web; npm install; npm run build`.

## Try it without the watch

```powershell
cd C:\_work\BuddyAI\server
.\.venv\Scripts\python tools\fake_watch.py --device-id my-watch-1 pair       # enter the code in /my → Add watch
.\.venv\Scripts\python tools\fake_watch.py --device-id my-watch-1 ask --wav tests\samples\en_1.wav --lang auto
.\.venv\Scripts\python tools\fake_watch.py --device-id my-watch-1 online     # stay connected (Ctrl+C to stop)
```

## Flash the watch

See [firmware/README.md](firmware/README.md). In short:

```powershell
. "C:\Espressif\tools\Microsoft.v5.5.4.PowerShell_profile.ps1"
cd C:\_work\BuddyAI\firmware
idf.py build
idf.py -p COM5 flash monitor
```

First boot:
1. The watch opens the Wi-Fi network `BuddyAI-XXXX`. Join it from your phone and enter your Wi-Fi details and the server address.
2. The watch shows a 6-digit code. Enter it in the web app under **My watches → Add watch**.

## Status

| Area | State |
|---|---|
| Server | Complete for launch scope. 63 automated tests: tenant isolation, shop/webhooks, languages, end-to-end with the watch simulator. Also verified on PostgreSQL and in the Docker image. |
| Web app | Customer + operator areas, checked live in a browser (desktop and mobile). |
| Marketing site | 16 pages, Lighthouse 95–100. Legal pages are DRAFTs. |
| Firmware | Builds (dev, release and production configs). **Not yet tested on hardware.** |
| Deployment | Docker images build; Caddy config validated; runbook in `deploy/README.md`. |

**Before selling:**
- **Hardware tests:** microphone, display, touch, battery, speaker, OTA.
- **Real-AI tests:** with your OpenAI key, check recognition in each language you advertise.
- **Placeholders:** fill every `TODO(owner)` in `site/` (prices, company details).
- **Legal:** have a lawyer review the legal pages.
- **Business checklist:** complete the list in `deploy/README.md` §9 (company, VAT/OSS, ICO, UKCA/CE, Stripe).
