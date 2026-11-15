# UK PSTI assessment (Product Security and Telecommunications Infrastructure Act 2022, security requirements regulations 2023)

Prepared 12 October 2026 from the firmware on 12 October 2026. Engineering assessment, not legal advice. **The draft statement below is not approved, not signed and must not be published or supplied until completed and approved.**

## Scope

The olácompanion watch connects to the internet (Wi-Fi) and is a consumer connectable product. The business imports the Waveshare board, fits its own battery and firmware and sells it under its own brand, which is likely to make it the **manufacturer** for PSTI purposes (to confirm with an adviser).

## Requirement 1 — no universal default passwords

| Check | Finding | Evidence | Status |
|---|---|---|---|
| Wi-Fi setup | done on the watch itself: the user picks a network from the scan list and types its password on the touch keyboard. No setup network (soft-AP / captive portal), no Bluetooth provisioning, so no device setup password exists | `components/net`, `ui_wifi.c` | IMPLEMENTED (firmware change in progress, Nov 2026) — re-review the release build |
| Hardcoded credentials | none found in `main/` and `components/` | review | IMPLEMENTED_NOT_VERIFIED (no automated scan) |
| Device identifier as secret | `device_id` (from MAC) is an identifier only; access uses a server-issued token (only its hash is kept server-side); pairing uses a random 6-digit code valid 5 minutes | `protocol_client.c:366,486,941`, `server/app/security.py` | IMPLEMENTED |
| Unauthenticated management endpoints | no captive portal (removed with the setup network); screen-capture server (`/snap`, port 8080) **dev builds only** (compiled out by `CONFIG_BUDDYAI_RELEASE_BUILD`) | `main/debug_snap.c` | IMPLEMENTED for release builds; units currently run **dev** firmware → must ship release firmware |

## Requirement 2 — how to report security issues

In the repository: `/security/` page and `/.well-known/security.txt`, contact **ola@olacompanion.com** (decided 12 Oct 2026). Acknowledgement target: 5 working days.
Status: IMPLEMENTED; effective once the site is deployed (not verified here). Mailbox monitoring is an operational duty (owner: business owner).

## Requirement 3 — minimum security update period

Decision (12 Oct 2026): **security updates for at least two years from the date of purchase**, published on `/security/`.
Open points (legal review): PSTI expects the period to be clear to the customer, ideally expressed so an end date can be determined; check whether "two years from the date of purchase" satisfies this or whether a fixed end date per model is better. Updates are delivered over the air (server push, `ota_available`).

## Related device-security findings (not PSTI requirements)

- Release firmware: wss-only with CA-bundle verification; signed OTA (RSA-3072) over HTTPS, SHA-256 check, rollback (`sdkconfig.release`). **Not yet on units.**
- Secure Boot V2, flash encryption, NVS encryption, Secure Download Mode: production overlay `firmware/sdkconfig.production` — not enabled; requires spare-unit testing (irreversible eFuses).
- OTA downgrade is not blocked by firmware (only identical version is ignored) — MEDIUM; recommend a monotonic version check or Secure Boot anti-rollback in a later firmware release.
- Wi-Fi and token are stored unencrypted in NVS until NVS encryption is enabled — HIGH before sale.

## RED cybersecurity (separate from PSTI)

For GB, radio equipment is placed on the market under the Radio Equipment Regulations 2017 (UKCA). The EU RED delegated act (EN 18031-1/-2/-3) applies to the **EU** market from 1 August 2025; whether and how GB adopts equivalent essential requirements must be checked for the chosen UKCA route. Treat EN 18031 as best practice for the technical file and confirm with the test lab. Status: REQUIRES_EXTERNAL_ACTION.

## Draft statement of compliance (NOT APPROVED — do not publish)

> **Statement of compliance — Product Security and Telecommunications Infrastructure (Security Requirements for Relevant Connectable Products) Regulations 2023**
>
> Product: olácompanion watch, model [model identifier — for completion], hardware Waveshare ESP32-S3-Touch-AMOLED-2.06 with EEMB LP402535 battery, firmware [version — for completion].
> Manufacturer: Ola Technologies London Ltd, [registered office address — for completion], company number [for completion].
> We declare that, in our opinion, we have complied with the applicable security requirements in Schedule 1 to the Regulations:
> 1. Passwords: the product has no universal default password; it has no setup network or device password — the user types their own Wi-Fi password on the device.
> 2. Reporting security issues: report to ola@olacompanion.com — see https://www.olacompanion.com/security/. We aim to acknowledge reports within 5 working days and to keep the reporter informed.
> 3. Security update period: at least two years from the date of purchase [end-date wording — for confirmation].
> Signed: [name, function — for completion]   Date: [for completion]   Place: [for completion]
