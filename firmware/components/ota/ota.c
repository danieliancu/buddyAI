/*
 * ola - firmware update, see ota.h
 */
#include "ota.h"

#include <stdio.h>
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
#include "esp_timer.h"
#include "nvs.h"
#include "mbedtls/sha256.h"
#include "sdkconfig.h"

static const char *TAG = "ota";

#define NVS_NS              "ota"
#define KEY_ATTEMPT         "att"       /* the update just installed: its attempt id and version */
#define KEY_VERSION         "ver"
#define KEY_CHECK           "rb_check"  /* the check that rolled it back */
#define KEY_RESET           "rb_reset"  /* set once the rollback was seen: why the new image stopped */
#define PROBATION_STABLE_US (60 * 1000 * 1000)
#define PROBATION_MIN_HEAP  (30 * 1024)
#define PROBATION_MAX_FAILS 5

typedef struct {
    char            url[256];
    char            version[32];
    char            attempt[33];
    uint8_t         sha256[32];
    uint32_t        size;
    ota_status_cb_t cb;
    void           *ctx;
} ota_job_t;

static volatile bool s_busy;
static const char   *s_failure = "";
static bool           s_probation;
static int            s_session_fails;
static esp_timer_handle_t s_stable_timer;
static ota_status_cb_t s_boot_cb;
static void          *s_boot_ctx;

static void store_str(const char *key, const char *value)
{
    nvs_handle_t h;
    if (nvs_open(NVS_NS, NVS_READWRITE, &h) == ESP_OK) {
        if (value) {
            nvs_set_str(h, key, value);
        } else {
            nvs_erase_key(h, key);
        }
        nvs_commit(h);
        nvs_close(h);
    }
}

static bool load_str(const char *key, char *out, size_t len)
{
    nvs_handle_t h;
    bool ok = false;
    out[0] = 0;
    if (nvs_open(NVS_NS, NVS_READONLY, &h) == ESP_OK) {
        size_t n = len;
        ok = nvs_get_str(h, key, out, &n) == ESP_OK && out[0];
        nvs_close(h);
    }
    return ok;
}

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
        s_failure = err == ESP_ERR_OTA_VALIDATE_FAILED ? "image_invalid" : "network";
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
        s_failure = err == ESP_ERR_OTA_VALIDATE_FAILED ? "image_invalid" : "network";
        esp_https_ota_abort(h);
        goto fail;
    }

    /* Integrity: hash what was written to the update partition. */
    report(job, OTA_STATE_VERIFYING, 100);
    const esp_partition_t *part = esp_ota_get_next_update_partition(NULL);
    uint32_t written = (uint32_t)esp_https_ota_get_image_len_read(h);
    if (job->size && written != job->size) {
        ESP_LOGE(TAG, "size mismatch: got %lu expected %lu", (unsigned long)written, (unsigned long)job->size);
        s_failure = "integrity";
        esp_https_ota_abort(h);
        goto fail;
    }
    uint8_t digest[32];
    if (!part || partition_sha256(part, written, digest) != ESP_OK || memcmp(digest, job->sha256, 32) != 0) {
        ESP_LOGE(TAG, "SHA-256 mismatch - rejecting image");
        s_failure = "integrity";
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
        s_failure = err == ESP_ERR_OTA_VALIDATE_FAILED ? "image_invalid" : "write";
        goto fail;
    }
    ESP_LOGI(TAG, "update verified, restarting");
    /* Kept across the restart: the outcome is reported to the server from the next hello. */
    store_str(KEY_ATTEMPT, job->attempt[0] ? job->attempt : NULL);
    store_str(KEY_VERSION, job->version);
    store_str(KEY_CHECK, NULL);
    store_str(KEY_RESET, NULL);
    report(job, OTA_STATE_DONE, 100);
    vTaskDelay(pdMS_TO_TICKS(1500));
    esp_restart();

fail:
    report(job, OTA_STATE_FAILED, 0);
    free(job);
    s_busy = false;
    vTaskDelete(NULL);
}

esp_err_t ota_start(const char *url, const char *version, const char *sha256_hex, uint32_t size,
                    const char *attempt_id, ota_status_cb_t cb, void *ctx)
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
    strlcpy(job->attempt, attempt_id ? attempt_id : "", sizeof(job->attempt));
    s_failure = "write";
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

const char *ota_running_version(void)
{
    return esp_app_get_description()->version;
}

const char *ota_last_failure(void)
{
    return s_failure;
}

bool ota_version_older(const char *candidate, const char *running)
{
    int a[3] = { 0 }, b[3] = { 0 };
    if (!candidate || !running || sscanf(candidate, "%d.%d.%d", &a[0], &a[1], &a[2]) != 3 ||
        sscanf(running, "%d.%d.%d", &b[0], &b[1], &b[2]) != 3) {
        return false;
    }
    for (int i = 0; i < 3; i++) {
        if (a[i] != b[i]) {
            return a[i] < b[i];
        }
    }
    return false;
}

static const char *reset_name(esp_reset_reason_t r)
{
    switch (r) {
    case ESP_RST_POWERON: return "power_on";
    case ESP_RST_USB:     return "usb";
    case ESP_RST_SW:      return "software";
    case ESP_RST_PANIC:   return "panic";
    case ESP_RST_INT_WDT:
    case ESP_RST_TASK_WDT:
    case ESP_RST_WDT:     return "watchdog";
    case ESP_RST_BROWNOUT: return "brownout";
    default:              return "other";
    }
}

static void roll_back(const char *check)
{
    ESP_LOGE(TAG, "new image failed '%s': rolling back", check);
    store_str(KEY_CHECK, check);
    esp_ota_mark_app_invalid_rollback_and_reboot();
    /* Only returns if no other image can boot: stay on this one rather than restart for ever. */
    ESP_LOGE(TAG, "rollback not possible");
    s_probation = false;
}

static void on_stable(void *arg)
{
    if (!s_probation) {
        return;
    }
    if (esp_get_free_heap_size() < PROBATION_MIN_HEAP) {
        roll_back("memory");
        return;
    }
    ESP_LOGI(TAG, "new image passed its checks: confirmed");
    esp_ota_mark_app_valid_cancel_rollback();
    s_probation = false;
    if (s_boot_cb) {
        s_boot_cb(OTA_STATE_CONFIRMED, 100, s_boot_ctx);
    }
}

void ota_boot_init(ota_status_cb_t cb, void *ctx)
{
    s_boot_cb = cb;
    s_boot_ctx = ctx;
    esp_ota_img_states_t st;
    s_probation = esp_ota_get_state_partition(esp_ota_get_running_partition(), &st) == ESP_OK &&
                  st == ESP_OTA_IMG_PENDING_VERIFY;
    if (s_probation) {
        ESP_LOGI(TAG, "new image on probation");
        return;
    }
    char want[32], reset[16];
    if (!load_str(KEY_VERSION, want, sizeof(want)) || strcmp(want, ota_running_version()) == 0 ||
        load_str(KEY_RESET, reset, sizeof(reset))) {
        return;
    }
    /* An update finished, yet another image runs: the bootloader went back. The reset reason is about the image
     * that stopped only on this first boot after it, so it is kept now. */
    const esp_partition_t *bad = esp_ota_get_last_invalid_partition();
    esp_app_desc_t desc;
    if (bad && esp_ota_get_partition_description(bad, &desc) == ESP_OK && strcmp(desc.version, want) == 0) {
        store_str(KEY_RESET, reset_name(esp_reset_reason()));
        ESP_LOGW(TAG, "rolled back from %s", want);
    }
}

bool ota_on_probation(void)
{
    return s_probation;
}

void ota_health_local(const char *check, bool ok)
{
    if (s_probation && !ok) {
        roll_back(check);
    }
}

void ota_health_exchange_ok(void)
{
    if (!s_probation || s_stable_timer) {
        return;
    }
    const esp_timer_create_args_t args = { .callback = on_stable, .name = "ota_stable" };
    if (esp_timer_create(&args, &s_stable_timer) == ESP_OK) {
        esp_timer_start_once(s_stable_timer, PROBATION_STABLE_US);
    }
}

void ota_health_session_failed(void)
{
    if (s_probation && !s_stable_timer && ++s_session_fails >= PROBATION_MAX_FAILS) {
        roll_back("server_session");
    }
}

bool ota_report_confirmed(char attempt_id[33], char version[32])
{
    esp_ota_img_states_t st;
    if (s_probation || !load_str(KEY_ATTEMPT, attempt_id, 33) || !load_str(KEY_VERSION, version, 32) ||
        strcmp(version, ota_running_version()) != 0) {
        return false;
    }
    return esp_ota_get_state_partition(esp_ota_get_running_partition(), &st) == ESP_OK && st == ESP_OTA_IMG_VALID;
}

bool ota_report_rollback(char attempt_id[33], char version[32], char check[24], char reset_reason[16])
{
    if (!load_str(KEY_RESET, reset_reason, 16) || !load_str(KEY_ATTEMPT, attempt_id, 33) ||
        !load_str(KEY_VERSION, version, 32)) {
        return false;
    }
    load_str(KEY_CHECK, check, 24);
    return true;
}
