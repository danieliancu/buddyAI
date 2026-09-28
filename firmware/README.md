# BuddyAI watch firmware

Firmware for the BuddyAI voice assistant watch.

- Board: Waveshare **ESP32-S3-Touch-AMOLED-2.06** (ESP32-S3R8, 8 MB octal PSRAM, 32 MB flash,
  410×502 CO5300 AMOLED, FT3168 touch, ES8311 + ES7210 audio, AXP2101 PMU, PCF85063 RTC)
- Framework: **ESP-IDF v5.5.4**, LVGL 9.5
- Protocol: [`../protocol/PROTOCOL.md`](../protocol/PROTOCOL.md) (v1)

```
main/                 app_main.c (wiring, app loop, factory reset), Kconfig.projbuild
components/board      pins, PMU/RTC, display + touch, audio codecs
components/ui         LVGL screens (watchface, settings, pairing, errors, OTA, reset)
components/audio      Opus capture / playback
components/net        Wi-Fi, SoftAP setup portal, SNTP, mDNS
components/protocol_client  WebSocket session, turns, pairing, errors
components/settings   NVS storage (namespace "buddyai")
components/ota        esp_https_ota + SHA-256 check (+ signature check in release)
sdkconfig.defaults    development configuration (default)
sdkconfig.release     release overlay (customer units)
keys/                 signing key location - never committed (see keys/README.md)
```

## 1. Prerequisites

Install ESP-IDF v5.5.4 (Espressif Installation Manager). On this machine, open PowerShell and load
the environment:

```powershell
. "C:\Espressif\tools\Microsoft.v5.5.4.PowerShell_profile.ps1"
cd C:\_work\BuddyAI\firmware
```

Component dependencies (LVGL, esp_lvgl_port, codecs, Opus, websocket client, mDNS…) are fetched by
the component manager on the first build (`managed_components/`, `dependencies.lock`).

## 2. Development build (default)

```powershell
idf.py build
```

Uses `sdkconfig.defaults` only (`sdkconfig` is generated and not committed). Development builds:

- accept `ws://` and `wss://` server URLs, try mDNS `_buddyai._tcp` discovery on the LAN;
- accept `http://` and `https://` OTA URLs (LAN test server), SHA-256 checked, **unsigned**;
- log at INFO level.

Optional: a default server for your LAN, so you do not have to type it in the portal:
`idf.py menuconfig` → *BuddyAI* → *Default server URL* (e.g. `ws://192.168.1.10:8765/ws/device`).

## 3. Flash and monitor

Connect the watch with USB-C (USB-Serial/JTAG of the ESP32-S3; on Windows it shows up as a COM port).

```powershell
idf.py -p COM5 flash monitor        # replace COM5 with your port
```

- Leave the monitor with `Ctrl+]`.
- If the port does not appear or flashing fails: hold **BOOT**, press and release **RESET**
  (or plug in USB while holding BOOT), release BOOT, flash again, then press RESET.
- Erase everything (NVS, OTA data) during development: `idf.py -p COM5 erase-flash`.

## 4. First boot (setup portal)

1. Without Wi-Fi credentials the watch starts a SoftAP **`BuddyAI-XXXX`** and shows the Wi-Fi setup screen.
2. Join that network with a phone; the captive portal opens (otherwise browse to `http://192.168.4.1`).
3. Choose the Wi-Fi network, enter the password and optionally a **server URL**:
   - development: `ws://<pc-ip>:8765/ws/device` or `wss://…`; empty = default URL / last server / mDNS;
   - release builds: **`wss://` only**; empty = the built-in default (`CONFIG_BUDDYAI_DEFAULT_SERVER_URL`).
4. Save → the watch restarts, joins Wi-Fi and connects.
5. An unpaired watch shows a **6-digit pairing code**. In the BuddyAI web app choose *Add watch* and
   enter the code. The watch stores its device token and shows the watchface.

The portal can be reopened any time from quick settings (swipe left/up on the watchface → *Wi-Fi setup*).

## 5. Factory reset (unpair locally)

1. Hold the **BOOT** button for **8 seconds** (the watch must be running; holding BOOT *while resetting*
   enters the ROM download mode instead).
2. The watch shows **"Reset watch?"**. Tap **Reset** to confirm. Nothing happens if you do not tap
   within 10 s.
3. The watch erases the NVS namespace `buddyai` (Wi-Fi credentials, server URL, last server, device
   token, settings) and restarts into the setup portal.

This only unpairs the watch **locally**. The owner removes the watch from their account in the
BuddyAI app (that revokes the token on the server).

The PWR key is not used for this: it is wired to the AXP2101 PWRON pin, whose long press is a
hardware power-off.

## 6. Server error screens (PROTOCOL.md §7)

| Server error | Watch behaviour |
|---|---|
| `subscription_required` (reply to `listen_start`) | Mic/uplink stopped at once, "Subscription needed — Open the BuddyAI app to renew BuddyAI Care." for 6 s (tap to dismiss), then idle. No automatic retry. |
| `limit_reached` (reply to `listen_start`) | Same pattern, "Monthly limit reached — Your assistant will be back on the 1st of next month." |
| `account_inactive` (reply to `hello`, server closes) | "Account inactive — Contact BuddyAI support.", token kept, reconnect every **10 min** until a session succeeds; a mic tap meanwhile shows the screen again. |
| `protocol_unsupported` | "Update required", reconnect every 10 min. |

For the two turn-level errors the websocket task marks the turn dead as soon as the frame arrives, so
not a single further uplink audio frame is sent.

## 7. Release build (customer firmware)

A release build is the development firmware plus `sdkconfig.release`:

| | Development | Release |
|---|---|---|
| `CONFIG_BUDDYAI_RELEASE_BUILD` | n | **y** |
| Server URL | any `ws://` / `wss://`, mDNS discovery | **`wss://` only** (portal, stored and default URLs), mDNS off |
| Default server | empty | `wss://api.example.com/ws/device` — **replace example.com** |
| OTA | `http://` or `https://`, unsigned | **`https://` only**, image **signature verified** (RSA-3072, SBV2 scheme) |
| App rollback | on | on (image confirmed after the first server session) |
| Log level | INFO | WARN |
| Secure Boot / flash encryption / NVS encryption | off | off (production units only, §8) |

### 7.1 Signing key (once)

See [`keys/README.md`](keys/README.md). In short, on an offline machine:

```powershell
espsecure.py generate_signing_key --version 2 --scheme rsa3072 keys/buddyai_signing_key.pem
```

The key is **never committed** (`keys/.gitignore`, `*.pem` in `.gitignore`). Copy it into
`firmware/keys/` only while building a release.

### 7.2 Configure

Edit `sdkconfig.release` and replace `api.example.com` with the real API domain in
`CONFIG_BUDDYAI_DEFAULT_SERVER_URL`.

### 7.3 Build

Always use a **separate build directory and sdkconfig**, so the development configuration is not
modified (options already present in an existing `sdkconfig` would otherwise win over the overlay):

```powershell
idf.py -B build-release -D SDKCONFIG=build-release/sdkconfig `
       -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.release" build
```

The build signs `build-release/buddyai_watch.bin` with `keys/buddyai_signing_key.pem` (the build
fails if the key is missing). Flash a board with the release build:

```powershell
idf.py -B build-release -D SDKCONFIG=build-release/sdkconfig -p COM5 flash monitor
```

### 7.4 Publishing an OTA update

1. Bump `PROJECT_VER` in `CMakeLists.txt`, build the release as above.
2. Upload `build-release/buddyai_watch.bin` (signed) to an **https** URL.
3. Offer it from the server (`ota_available` with `version`, `url`, `sha256` of the *signed* .bin, `size`).
4. The watch downloads, checks size + SHA-256, then `esp_ota_end()` verifies the signature against the
   key that signed the **running** firmware. Unsigned or foreign-key images are rejected. After the
   reboot the new image is confirmed once it reaches a server session; otherwise the bootloader rolls
   back to the previous image.

Note: a watch running a *development* (unsigned) build does not check signatures. The first release
image has to be flashed over USB (or OTA'd from a dev build); from then on only images signed with the
same key are accepted.

## 8. PRODUCTION UNITS ONLY — irreversible

> **WARNING — read fully before starting.**
> Secure Boot V2 and flash encryption in *release* mode **burn eFuses permanently** on the first boot.
> Afterwards the chip only runs firmware signed with *your* key, the flash cannot be read or
> re-flashed in plaintext over USB, JTAG is disabled and the ROM download mode is restricted
> (Secure Download Mode). If the signing key is lost the unit can never be updated again.
>
> **Never do this on a development board** — it cannot be undone, and a development board used for
> this is locked for development forever. Do it only on finished customer units, after the exact
> release firmware has been tested on an unlocked board.

What is enabled: Secure Boot V2 (RSA-3072, same key as the release OTA signatures), flash encryption
(XTS-AES-128, key generated on the chip, release mode), NVS encryption with the flash-encryption
based key scheme (XTS keys generated into the encrypted `nvs_keys` partition, `0x21000` in
`partitions_production.csv`), Secure Download Mode, partition table moved to `0x10000`.

### 8.1 Production overlay

Create `firmware/sdkconfig.production` with exactly this content (it is layered **after**
`sdkconfig.release`):

```
# PRODUCTION UNITS ONLY - burns eFuses on first boot (irreversible)
# Hardware Secure Boot V2 replaces "signed apps without secure boot"
# CONFIG_SECURE_SIGNED_APPS_NO_SECURE_BOOT is not set
CONFIG_SECURE_BOOT=y
CONFIG_SECURE_BOOT_V2_ENABLED=y
CONFIG_SECURE_SIGNED_APPS_RSA_SCHEME=y
CONFIG_SECURE_BOOT_BUILD_SIGNED_BINARIES=y
CONFIG_SECURE_BOOT_SIGNING_KEY="keys/buddyai_signing_key.pem"
# Flash encryption, release mode
CONFIG_SECURE_FLASH_ENC_ENABLED=y
CONFIG_SECURE_FLASH_ENCRYPTION_AES128=y
CONFIG_SECURE_FLASH_ENCRYPTION_MODE_RELEASE=y
CONFIG_SECURE_ENABLE_SECURE_ROM_DL_MODE=y
# NVS encryption, keys protected by flash encryption (nvs_keys partition)
CONFIG_NVS_ENCRYPTION=y
CONFIG_NVS_SEC_KEY_PROTECT_USING_FLASH_ENC=y
# The signed Secure Boot + flash encryption bootloader (~0x8870 bytes) does not
# fit below 0x8000: move the partition table (layout in partitions_production.csv)
CONFIG_PARTITION_TABLE_OFFSET=0x10000
CONFIG_PARTITION_TABLE_CUSTOM_FILENAME="partitions_production.csv"
```

**Partition layout:** production units use `partitions_production.csv` (table at `0x10000`,
everything shifted; same partition sizes). The app reads the partition table from the offset it was
built with, so **OTA images for production units must be built with this production overlay**, and
images built with only `sdkconfig.release` are for unlocked release/test units. Offer each fleet its
own image (e.g. by `fw_version` suffix or device list on the server).

### 8.2 Steps (per unit)

1. **Check the unit is fresh** (nothing burnt yet):
   ```powershell
   espefuse.py -p COM5 summary
   ```
   Expect `SECURE_BOOT_EN = False`, `SPI_BOOT_CRYPT_CNT = 0`, all `KEY_PURPOSE_n = USER`/empty.
   If anything is already set, stop.
2. **Build** (key present in `keys/`):
   ```powershell
   idf.py -B build-prod -D SDKCONFIG=build-prod/sdkconfig `
          -D SDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.release;sdkconfig.production" build
   ```
   Check the log: `bootloader.bin` must fit below the partition table (`0x8000`); the build fails
   otherwise.
3. **Flash the signed bootloader** (with Secure Boot enabled `idf.py flash` does not write it):
   ```powershell
   idf.py -B build-prod -D SDKCONFIG=build-prod/sdkconfig -p COM5 bootloader-flash
   ```
4. **Flash partition table, otadata and app** (plaintext; encrypted on the chip at first boot):
   ```powershell
   idf.py -B build-prod -D SDKCONFIG=build-prod/sdkconfig -p COM5 flash
   ```
5. **First boot — do not remove power** (≈1 min):
   ```powershell
   idf.py -B build-prod -D SDKCONFIG=build-prod/sdkconfig -p COM5 monitor
   ```
   The bootloader burns the Secure Boot key digest, generates the flash-encryption key, encrypts the
   bootloader/partition table/app/`nvs_keys` in place, burns the release-mode eFuses and resets.
   The app then logs `release build x.y.z: secure boot ON, flash encryption ON` (WARN level).
6. **Verify** from the boot log: the bootloader reports secure boot / flash encryption being enabled
   on the first boot, and every later boot the app logs `secure boot ON, flash encryption ON`.
   `espefuse.py` can **no longer** read the eFuses once Secure Download Mode is active (ESP-IDF
   limitation), so run `espefuse.py summary` only *before* step 3 (step 1).
7. **Functional check**: setup portal, pairing, one conversation, then an OTA of a signed test
   release. Record the unit's MAC / device id.

From now on the unit is updated **only by signed OTA**.

## 9. Hardware checks still pending

The firmware builds but has not yet run on the watch. To verify on the first board:

- **Microphone channel**: the uplink takes I2S RX left slot = ES7210 MIC1 (`board_audio.c`). Check that
  speech is captured (not silence / the AEC loopback MIC3), and tune the mic gain (`TODO(M0)`).
- **Display brightness**: CO5300 brightness command (0x51) range, the 0 %–100 % mapping, dimming and
  screen-off/on (`board_display.c`).
- **Touch orientation**: FT3168 coordinates vs. the 410×502 panel (`swap_xy` / `mirror_x` / `mirror_y`
  are all 0) and the column gap `0x16`.
- **AXP2101 rails**: the firmware leaves the power rails at the PMU's power-on defaults and only
  configures charger/ADC/gauge; confirm display, touch, audio codecs and PA are powered, charging
  current (400 mA) and battery percentage.
- **Speaker**: ES8311 output + NS4150B PA (GPIO46) — volume range, hiss when idle, clipping at 100 %.
- **BOOT button (GPIO0)**: level while running (factory-reset long press).
- **Release/production**: signed OTA accepted and a foreign-key image rejected; NVS encryption and
  flash encryption on a production unit (§8) — on a *sacrificial* unit first.
