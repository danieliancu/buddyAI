/*
 * ola - firmware update (ota_available, PROTOCOL.md section 3.2)
 *
 * Downloads the image with esp_https_ota (CA bundle for https), verifies the
 * SHA-256 of the written image against the offer, then switches the boot
 * partition and restarts.
 *
 * Signatures: release builds (sdkconfig.release) enable
 * CONFIG_SECURE_SIGNED_APPS_NO_SECURE_BOOT + CONFIG_SECURE_SIGNED_ON_UPDATE_NO_SECURE_BOOT
 * (Secure Boot V2 RSA-3072 signature block appended to the image at build
 * time). esp_ota_end() then rejects any image not signed with the same key as
 * the running app. The optional `signature` field of ota_available is not
 * needed for this and is ignored. Release builds also refuse non-https URLs.
 *
 * After an update the new image is on probation (ESP_OTA_IMG_PENDING_VERIFY). It is confirmed only when the
 * local checks passed, the server answered and it then ran stably for a minute. A failed local check, or
 * no server session after 5 tries with Wi-Fi up, rolls back at once; so does any restart during probation
 * (the bootloader's rule). No Wi-Fi: the image simply waits, because that says nothing about the image.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    OTA_STATE_DOWNLOADING,
    OTA_STATE_VERIFYING,
    OTA_STATE_DONE,         /* device restarts right after */
    OTA_STATE_FAILED,       /* why: ota_last_failure() */
    OTA_STATE_CONFIRMED,    /* the new image passed its probation */
} ota_state_t;

typedef void (*ota_status_cb_t)(ota_state_t state, int progress_pct, void *ctx);

/* attempt_id: the server's id of this try, kept across the restart to report the outcome ("" or NULL: none). */
esp_err_t ota_start(const char *url, const char *version, const char *sha256_hex, uint32_t size,
                    const char *attempt_id, ota_status_cb_t cb, void *ctx);
bool      ota_in_progress(void);
const char *ota_running_version(void);
/* "network", "integrity", "image_invalid" or "write": why the last ota_start failed. */
const char *ota_last_failure(void);
/* True if `candidate` is an older a.b.c version than `running` (unparsable: false). */
bool      ota_version_older(const char *candidate, const char *running);

/* Call once at boot, after settings_init (NVS): notes probation, or a rollback from the previous run. */
void      ota_boot_init(ota_status_cb_t cb, void *ctx);
bool      ota_on_probation(void);
/* A local check of the new image. A failure during probation rolls back and restarts; otherwise nothing. */
void      ota_health_local(const char *check, bool ok);
/* The server answered on this session: starts the stable minute that ends probation. */
void      ota_health_exchange_ok(void);
/* No session although Wi-Fi is up. The 5th during probation rolls back and restarts. */
void      ota_health_session_failed(void);

/* What the next hello reports (false: nothing), until a new update replaces it. */
bool      ota_report_confirmed(char attempt_id[33], char version[32]);
bool      ota_report_rollback(char attempt_id[33], char version[32], char check[24], char reset_reason[16]);

#ifdef __cplusplus
}
#endif
