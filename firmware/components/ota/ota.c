/*
 * ola - firmware update, see ota.h
 */
#include "ota.h"

#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_https_ota.h"
#include "esp_http_client.h"
#include "esp_crt_bundle.h"
#include "esp_partition.h"
#include "esp_app_desc.h"
#include "esp_system.h"
#include "mbedtls/sha256.h"
#include "sdkconfig.h"

static const char *TAG = "ota";

typedef struct {
    char            url[256];
    char            version[32];
    uint8_t         sha256[32];
    uint32_t        size;
    ota_status_cb_t cb;
    void           *ctx;
} ota_job_t;

static volatile bool s_busy;

static void report(ota_job_t *job, ota_state_t st, int pct)
{
    if (job->cb) {
        job->cb(st, pct, job->ctx);
    }
}

static bool parse_hex32(const char *hex, uint8_t out[32])
{
    if (!hex || strlen(hex) != 64) {
        return false;
    }
    for (int i = 0; i < 32; i++) {
        char b[3] = { hex[2 * i], hex[2 * i + 1], 0 };
        if (!isxdigit((unsigned char)b[0]) || !isxdigit((unsigned char)b[1])) {
            return false;
        }
        out[i] = (uint8_t)strtol(b, NULL, 16);
    }
    return true;
}

/* SHA-256 over the first `len` bytes of a partition. */
static esp_err_t partition_sha256(const esp_partition_t *part, uint32_t len, uint8_t out[32])
{
    const size_t chunk = 4096;
    uint8_t *buf = malloc(chunk);
    if (!buf) {
        return ESP_ERR_NO_MEM;
    }
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0);
    esp_err_t err = ESP_OK;
    for (uint32_t off = 0; off < len; off += chunk) {
        size_t n = (len - off) < chunk ? (len - off) : chunk;
        err = esp_partition_read(part, off, buf, n);
        if (err != ESP_OK) {
            break;
        }
        mbedtls_sha256_update(&ctx, buf, n);
    }
    mbedtls_sha256_finish(&ctx, out);
    mbedtls_sha256_free(&ctx);
    free(buf);
    return err;
}

static void ota_task(void *arg)
{
    ota_job_t *job = arg;
    ESP_LOGI(TAG, "update to %s from %s (%lu bytes)", job->version, job->url, (unsigned long)job->size);

    esp_http_client_config_t http = {
        .url = job->url,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .timeout_ms = 15000,
        .keep_alive_enable = true,
        .buffer_size = 4096,
        .buffer_size_tx = 1024,
    };
    esp_https_ota_config_t cfg = {
        .http_config = &http,
    };
    esp_https_ota_handle_t h = NULL;
    esp_err_t err = esp_https_ota_begin(&cfg, &h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "begin failed: %s", esp_err_to_name(err));
        goto fail;
    }

    int last_pct = -1;
    while ((err = esp_https_ota_perform(h)) == ESP_ERR_HTTPS_OTA_IN_PROGRESS) {
        int total = esp_https_ota_get_image_size(h);
        if (total <= 0) {
            total = (int)job->size;
        }
        int pct = total > 0 ? (int)(100LL * esp_https_ota_get_image_len_read(h) / total) : 0;
        if (pct / 5 != last_pct / 5) {
            last_pct = pct;
            report(job, OTA_STATE_DOWNLOADING, pct);
        }
    }
    if (err != ESP_OK || !esp_https_ota_is_complete_data_received(h)) {
        ESP_LOGE(TAG, "download failed: %s", esp_err_to_name(err));
        esp_https_ota_abort(h);
        goto fail;
    }

    /* Integrity: hash what was written to the update partition. */
    report(job, OTA_STATE_VERIFYING, 100);
    const esp_partition_t *part = esp_ota_get_next_update_partition(NULL);
    uint32_t written = (uint32_t)esp_https_ota_get_image_len_read(h);
    if (job->size && written != job->size) {
        ESP_LOGE(TAG, "size mismatch: got %lu expected %lu", (unsigned long)written, (unsigned long)job->size);
        esp_https_ota_abort(h);
        goto fail;
    }
    uint8_t digest[32];
    if (!part || partition_sha256(part, written, digest) != ESP_OK || memcmp(digest, job->sha256, 32) != 0) {
        ESP_LOGE(TAG, "SHA-256 mismatch - rejecting image");
        esp_https_ota_abort(h);
        goto fail;
    }

    /* With CONFIG_SECURE_SIGNED_ON_UPDATE_NO_SECURE_BOOT (release builds) or
     * hardware Secure Boot, esp_ota_end() inside finish() verifies the image's
     * signature block against the key that signed the running app and fails
     * with ESP_ERR_OTA_VALIDATE_FAILED / ESP_ERR_IMAGE_INVALID otherwise. */
    err = esp_https_ota_finish(h);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "finish failed: %s%s", esp_err_to_name(err),
                 err == ESP_ERR_OTA_VALIDATE_FAILED ? " (image invalid or signature check failed)" : "");
        goto fail;
    }
    ESP_LOGI(TAG, "update verified, restarting");
    report(job, OTA_STATE_DONE, 100);
    vTaskDelay(pdMS_TO_TICKS(1500));
    esp_restart();

fail:
    report(job, OTA_STATE_FAILED, 0);
    free(job);
    s_busy = false;
    vTaskDelete(NULL);
}

esp_err_t ota_start(const char *url, const char *version, const char *sha256_hex,
                    uint32_t size, ota_status_cb_t cb, void *ctx)
{
    if (s_busy) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!url || strlen(url) >= sizeof(((ota_job_t *)0)->url)) {
        return ESP_ERR_INVALID_ARG;
    }
#if CONFIG_BUDDYAI_RELEASE_BUILD
    /* Release builds download firmware over TLS only (esp_https_ota also
     * refuses http:// because CONFIG_ESP_HTTPS_OTA_ALLOW_HTTP is off). */
    if (strncmp(url, "https://", 8) != 0) {
        ESP_LOGE(TAG, "release build: OTA URL must be https:// - offer ignored");
        return ESP_ERR_INVALID_ARG;
    }
#endif
    ota_job_t *job = calloc(1, sizeof(*job));
    if (!job) {
        return ESP_ERR_NO_MEM;
    }
    if (!parse_hex32(sha256_hex, job->sha256)) {
        ESP_LOGE(TAG, "offer without valid sha256 - ignored");
        free(job);
        return ESP_ERR_INVALID_ARG;
    }
    strlcpy(job->url, url, sizeof(job->url));
    strlcpy(job->version, version ? version : "?", sizeof(job->version));
    job->size = size;
    job->cb = cb;
    job->ctx = ctx;
    s_busy = true;
    if (xTaskCreatePinnedToCore(ota_task, "ota", 8192, job, 3, NULL, 0) != pdPASS) {
        free(job);
        s_busy = false;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

bool ota_in_progress(void)
{
    return s_busy;
}

void ota_mark_app_valid(void)
{
    const esp_partition_t *running = esp_ota_get_running_partition();
    esp_ota_img_states_t st;
    if (esp_ota_get_state_partition(running, &st) == ESP_OK && st == ESP_OTA_IMG_PENDING_VERIFY) {
        ESP_LOGI(TAG, "marking running image valid");
        esp_ota_mark_app_valid_cancel_rollback();
    }
}

const char *ota_running_version(void)
{
    return esp_app_get_description()->version;
}
