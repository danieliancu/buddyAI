# Bluetooth LE Wi-Fi setup (Android)

The ola account's setup page (`/my/setup`, Chrome on Android, HTTPS) sends the home Wi-Fi to the watch over
Bluetooth Low Energy. Implementation:

- watch: `firmware/components/ble_prov`;
- browser: `web/src/ble/`.

iPhone uses the watch's `ola-XXXX` setup network instead (iOS browsers have no Web Bluetooth). That network
is also the Android recovery path.

## Security

- **Scheme**: ESP-IDF protocomm **security scheme 2**, the maintained ESP-IDF provisioning security, as used by Espressif's own provisioning apps:
  - **SRP-6a** (RFC 5054 3072-bit group, g = 5, SHA-512), username `wifiprov`;
  - then **AES-256-GCM** with the first 32 bytes of the SRP session key;
  - a 12-byte nonce from the watch whose last 4 bytes count every message, big-endian (`sec_patch_ver` 1).
- **Password**: the watch's **setup password**. It is 8 characters from `ABCDEFGHJKMNPQRSTUVWXYZ23456789`, about 2^39.6 combinations. It is shown only on the watch screen, generated on the watch, stored in its NVS, and never sent to the server. It is rotated after every successful setup and erased by a factory reset. There is **no fleet-wide secret**.
- **Why SRP**: SRP is a password-authenticated key exchange.
  - Someone who records the radio traffic cannot test password guesses offline.
  - An active attacker gets one guess per session.
  - The watch counts failed sessions: after 3 it refuses new ones for 30 s.
  - Both sides prove knowledge of the password: the phone checks the watch's proof (`H(A, M, K)`) before sending anything.
- **No downgrade**: the firmware is built with security 0 and 1 disabled (`sdkconfig.defaults`). The browser refuses a watch whose `proto-ver` is not `sec_ver: 2`, or whose session answer is not scheme 2.
- **Never in clear text, never logged, never sent to the ola server**: the Wi-Fi password and the setup password.
- **BLE link**: no OS pairing/bonding. Security is at the application layer, so Chrome shows no pairing dialog.
- **Same password for the setup network**: the `ola-XXXX` network uses the setup password as its WPA2 key. One password protects both ways in.

## GATT layout

| | UUID |
|---|---|
| Service | `6f6cffff-6177-4f6c-a5e7-3c9d0b1e5a01` |
| `prov-session` | `6f6cff51-…` (the service UUID with `ffff` → endpoint id) |
| `prov-config` | `6f6cff52-…` |
| `proto-ver` | `6f6cff53-…` |
| `ola-scan` | `6f6cff54-…` |

The watch advertises the service and the name `ola-XXXX` (the same suffix as its setup network), and only while it is in Wi-Fi setup. Each request is a GATT **write** followed by a **read** of the same characteristic (protocomm). Attribute values are at most 512 bytes.

## Endpoints

1. **`proto-ver`** (plaintext): any request; returns
   `{"prov":{"ver":"v1.1","sec_ver":2,"sec_patch_ver":1,"cap":[]},"ola":{"ver":1,"cap":["scan"],"band":"2.4GHz"}}`.
2. **`prov-session`**: the `session.proto` / `sec2.proto` handshake (Command0: username + 384-byte public key A → Response0: B + salt; Command1: proof M → Response1: watch proof + nonce). A wrong password makes the watch fail the Command1 write.
3. **`prov-config`** (encrypted): ESP-IDF `wifi_config.proto`:
   - `CmdSetConfig {ssid, passphrase}`. The watch validates it (`prov_util`): SSID 1–32 bytes; password empty, 8–63 printable ASCII characters, or 64 hex digits. Invalid input gets status `InvalidArgument`.
   - `CmdApplyConfig`. The watch tries to join (two attempts, 20 s each; no retry after an authentication failure) and saves to NVS only after getting an IP address.
   - `CmdGetStatus`. Polled by the phone: `Connecting`, `Connected {ip4_addr, ssid}`, or `ConnectionFailed` with `AuthError` (wrong password) or `NetworkNotFound` (absent, out of range, or 5 GHz only). After `Connected` the watch restarts about 4 s later.
4. **`ola-scan`** (encrypted, JSON):
   - Request: `{}`, or `{"refresh":true}` to start a background scan.
   - Response: `{"scanning":bool,"aps":[{"s":ssid,"r":rssi,"a":authmode}]}`, strongest first, trimmed to fit one attribute.

## Interoperability

- The browser client (`web/src/ble/srp6a.ts`, `sec2.ts`, `proto.ts`) is checked byte for byte against vectors produced with ESP-IDF's `tools/esp_prov` (SRP-6a values and AES-GCM with the counter nonce): `web/src/ble/__fixtures__/srp6a-esp-prov.json`.
- A simulated watch (`web/src/ble/fakeWatch.ts`) runs the whole flow in tests. Real-hardware checks are listed in `docs/SETUP_TEST_CHECKLIST.md`.
- Espressif's provisioning tools can talk to the watch too: `esp_prov.py --transport ble --service_name ola-XXXX --sec_ver 2 --sec2_username wifiprov --sec2_pwd <setup password>`. Use the GATT UUIDs above if name lookup fails. This is handy for bench tests.
