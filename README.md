# BuddyAI

AI voice companion for the Waveshare ESP32-S3-Touch-AMOLED-2.06 watch.

| Folder | What |
|---|---|
| [protocol/](protocol/PROTOCOL.md) | Watch ↔ server protocol v1 (WebSocket, JSON + Opus) |
| [server/](server/README.md) | FastAPI server: device gateway, voice pipeline, REST API, hosts the web app |
| [web/](web/) | Admin web app (React + Vite + TypeScript + Tailwind) |
| [firmware/](firmware/) | Watch firmware (ESP-IDF 5.5, LVGL 9) |

## 1. Run the server and web app (Windows, PowerShell)

```powershell
cd C:\_work\BuddyAI\server
.\.venv\Scripts\python -m app.main
```

Open http://localhost:8765. On first run, create the admin account.

- **AI keys:** put `BUDDYAI_OPENAI_API_KEY=sk-...` in `server\.env`, or enter the key on the **System** page.
  Use **Test connection** there to check STT, LLM and TTS.
- **No keys yet:** run `$env:BUDDYAI_MOCK_PROVIDERS="true"` before starting to get simulated AI.
- **Switch AI vendor:** set `BUDDYAI_AI_PROFILE=openai` or `qwen` in `server\.env`, then restart. See [server/README.md](server/README.md).
- **Costs:** shown in GBP. Vendors bill in USD; set the USD→GBP rate on the Usage page.
- **Other devices on your network:** use `http://<pc-ip>:8765`. Allow Python through Windows Firewall on private networks.

The watch's server address is shown on the Devices page, for example `ws://192.168.1.10:8765/ws/device`.

Rebuild the web app after changing it:

```powershell
cd C:\_work\BuddyAI\web
npm install
npm run build        # output: web\dist, served by the server
npm run dev          # dev server with hot reload, proxies to the server on :8765
```

## 2. Try it without the watch

```powershell
cd C:\_work\BuddyAI\server
.\.venv\Scripts\python tools\fake_watch.py pair           # enter the 6-digit code under Devices > Add watch
.\.venv\Scripts\python tools\fake_watch.py ask --wav tests\samples\en_1.wav --lang en --save out\reply.wav
```

## 3. Flash the watch

Connect the watch by USB-C. In PowerShell:

```powershell
. "C:\Espressif\tools\Microsoft.v5.5.4.PowerShell_profile.ps1"
cd C:\_work\BuddyAI\firmware
idf.py build
idf.py -p COM5 flash monitor      # replace COM5 with the watch's port (Device Manager > Ports)
```

If flashing does not start, hold **BOOT**, press **RESET**, then release **BOOT**.

To bake a server address into development builds, set it in `idf.py menuconfig` → BuddyAI → Default server URL.

First boot:
1. The watch opens the Wi-Fi network `BuddyAI-XXXX`. Join it from your phone; the setup page opens.
2. Pick your Wi-Fi network, enter its password, and optionally the server URL, e.g. `ws://192.168.1.10:8765/ws/device`.
   If you leave the URL empty, the watch looks for the server on the local network (mDNS).
3. The watch shows a 6-digit code. Enter it in the web app under **Devices → Add watch**.
4. Tap the microphone and talk.

## Status

- **Server:** feature-complete for the MVP. 25 automated tests, including end-to-end tests with the watch simulator (abort, reconnect, TTFA).
- **Web app:** complete, checked against the running server.
- **Firmware:** builds cleanly but has **not been tested on hardware yet**. First checks on the real watch:
  - which microphone channel is used, and the mic gain;
  - display brightness and touch orientation;
  - power rails and the battery reading;
  - speaker quality.
- **Not verified yet:** Romanian speech recognition quality with real recordings, and latency with real providers.
- **Production hardening (M5), not started:** WSS/HTTPS, flash/NVS encryption, secure boot, signed OTA, login rate limiting.
