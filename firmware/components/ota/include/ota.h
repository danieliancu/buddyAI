/*
 * BuddyAI - firmware update (ota_available, PROTOCOL.md section 3.2)
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
    OTA_STATE_FAILED,
} ota_state_t;

typedef void (*ota_status_cb_t)(ota_state_t state, int progress_pct, void *ctx);

esp_err_t ota_start(const char *url, const char *version, const char *sha256_hex,
                    uint32_t size, ota_status_cb_t cb, void *ctx);
bool      ota_in_progress(void);
/* Confirm the running image (cancels rollback). Call after a healthy session. */
void      ota_mark_app_valid(void);
const char *ota_running_version(void);

#ifdef __cplusplus
}
#endif
