/*
 * ola - Bluetooth Low Energy Wi-Fi setup (see ble_prov.h and protocol/BLE_PROVISIONING.md).
 *
 * GATT service OLA_SERVICE_UUID with one characteristic per protocomm endpoint:
 *   proto-ver    (0xFF53) version + capabilities JSON, plaintext
 *   prov-session (0xFF51) security 2 handshake (SRP6a, username "wifiprov", the watch's setup password)
 *   prov-config  (0xFF52) ESP-IDF wifi_config protobuf: set SSID/password, apply, get status (encrypted)
 *   ola-scan     (0xFF54) nearby 2.4 GHz networks as compact JSON (encrypted)
 * Credentials are validated, tried, and saved to NVS only once the watch got an IP address; a wrong
 * password or a missing network is reported back and the previous settings stay, so a retry is safe.
 */
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "cJSON.h"
#include "protocomm.h"
#include "protocomm_ble.h"
#include "protocomm_security.h"
#include "protocomm_security2.h"
#include "esp_srp.h"
#include "wifi_provisioning/wifi_config.h"
#include "net.h"
#include "settings.h"
#include "prov_util.h"
#include "ble_prov.h"

static const char *TAG = "ble_prov";

#define SRP_USERNAME            "wifiprov"
#define SRP_SALT_LEN            16
#define MAX_FAILED_HANDSHAKES   3
#define LOCKOUT_US              (30LL * 1000 * 1000)
#define CONNECT_TIMEOUT_US      (20LL * 1000 * 1000)
#define CONNECT_ATTEMPTS        2
#define SCAN_JSON_BUDGET        440     /* a BLE attribute holds at most 512 bytes (with the GCM tag) */
#define SCAN_MAX_APS            12

/* 6f6cffff-6177-4f6c-a5e7-3c9d0b1e5a01, little-endian as NimBLE stores it. Characteristic UUIDs replace
 * bytes 12..13 (the "ffff" of the first group) with the endpoint's 16-bit id. */
static const uint8_t OLA_SERVICE_UUID[16] = {
    0x01, 0x5a, 0x1e, 0x0b, 0x9d, 0x3c, 0xe7, 0xa5, 0x6c, 0x4f, 0x77, 0x61, 0xff, 0xff, 0x6c, 0x6f,
};

static protocomm_ble_name_uuid_t s_endpoints[] = {
    { "prov-session", 0xFF51 },
    { "prov-config",  0xFF52 },
    { "proto-ver",    0xFF53 },
    { "ola-scan",     0xFF54 },
};

static const char VERSION_JSON[] =
    "{\"prov\":{\"ver\":\"v1.1\",\"sec_ver\":2,\"sec_patch_ver\":1,\"cap\":[]},"
    "\"ola\":{\"ver\":1,\"cap\":[\"scan\"],\"band\":\"2.4GHz\"}}";

static protocomm_t                  *s_pc;
static bool                          s_active;
static char                         *s_salt;
static char                         *s_verifier;
static int                           s_verifier_len;
static protocomm_security2_params_t  s_sec2_params;
static protocomm_security_t          s_sec;           /* security 2 with a failed-attempt limit */
static int                           s_failed;
static int64_t                       s_lockout_until;
static ble_prov_done_cb_t            s_done;

static wifi_prov_config_set_data_t   s_cfg;
static bool                          s_have_cfg;
static volatile bool                 s_busy;
static volatile bool                 s_attempted;
static volatile wifi_prov_sta_state_t       s_state = WIFI_PROV_STA_DISCONNECTED;
static volatile wifi_prov_sta_fail_reason_t s_fail_reason = WIFI_PROV_STA_AP_NOT_FOUND;
static char                          s_ip[IP4ADDR_STRLEN_MAX];

/* ------------------------------------------------------------------------- */
/* Session: security 2 with a limit on wrong passwords                        */
/* ------------------------------------------------------------------------- */

static esp_err_t limited_session_handler(protocomm_security_handle_t handle, const void *sec_params,
                                         uint32_t session_id, const uint8_t *inbuf, ssize_t inlen,
                                         uint8_t **outbuf, ssize_t *outlen, void *priv_data)
{
    int64_t now = esp_timer_get_time();
    if (now < s_lockout_until) {
        ESP_LOGW(TAG, "setup session refused: too many wrong setup passwords, wait a moment");
        return ESP_FAIL;
    }
    esp_err_t err = protocomm_security2.security_req_handler(handle, sec_params, session_id, inbuf, inlen,
                                                             outbuf, outlen, priv_data);
    if (err != ESP_OK && ++s_failed >= MAX_FAILED_HANDSHAKES) {
        s_failed = 0;
        s_lockout_until = now + LOCKOUT_US;
        ESP_LOGW(TAG, "%d failed setup sessions: Bluetooth setup paused for 30 s", MAX_FAILED_HANDSHAKES);
    }
    return err;
}

/* ------------------------------------------------------------------------- */
/* Wi-Fi config handlers (wifi_config.proto)                                  */
/* ------------------------------------------------------------------------- */

static esp_err_t get_status(wifi_prov_config_get_data_t *resp, wifi_prov_ctx_t **ctx)
{
    memset(resp, 0, sizeof(*resp));
    resp->wifi_state = s_state;
    if (s_state == WIFI_PROV_STA_CONNECTED) {
        strlcpy(resp->conn_info.ip_addr, s_ip, sizeof(resp->conn_info.ip_addr));
        strlcpy(resp->conn_info.ssid, s_cfg.ssid, sizeof(resp->conn_info.ssid));
    } else if (s_state == WIFI_PROV_STA_DISCONNECTED) {
        resp->fail_reason = s_fail_reason;
    }
    return ESP_OK;
}

static esp_err_t set_config(const wifi_prov_config_set_data_t *req, wifi_prov_ctx_t **ctx)
{
    if (s_busy) {
        return ESP_ERR_INVALID_STATE;
    }
    size_t ssid_len = strnlen(req->ssid, sizeof(req->ssid));
    size_t pass_len = strnlen(req->password, sizeof(req->password));
    wifi_cred_result_t v = wifi_cred_validate(req->ssid, ssid_len, req->password, pass_len);
    if (v != WIFI_CRED_OK) {
        ESP_LOGW(TAG, "credentials refused (check %d)", (int)v);
        return ESP_ERR_INVALID_ARG;
    }
    memcpy(&s_cfg, req, sizeof(s_cfg));
    s_have_cfg = true;
    s_failed = 0;   /* an authenticated client: wrong-password counting starts again */
    ESP_LOGI(TAG, "credentials received for '%s'", s_cfg.ssid);
    return ESP_OK;
}

static bool reason_is_not_found(int reason)
{
    return reason == 0 || reason == WIFI_REASON_NO_AP_FOUND || reason == WIFI_REASON_BEACON_TIMEOUT ||
           reason == WIFI_REASON_NO_AP_FOUND_W_COMPATIBLE_SECURITY ||
           reason == WIFI_REASON_NO_AP_FOUND_IN_AUTHMODE_THRESHOLD ||
           reason == WIFI_REASON_NO_AP_FOUND_IN_RSSI_THRESHOLD;
}

static void connect_task(void *arg)
{
    bool ok = false;
    int reason = 0;
    vTaskDelay(pdMS_TO_TICKS(300));     /* let the BLE response go out first */
    for (int attempt = 0; attempt < CONNECT_ATTEMPTS && !ok; attempt++) {
        reason = 0;
        net_wifi_connect(s_cfg.ssid, s_cfg.password);
        vTaskDelay(pdMS_TO_TICKS(300));
        int64_t until = esp_timer_get_time() + CONNECT_TIMEOUT_US;
        while (esp_timer_get_time() < until) {
            if (net_wifi_connected()) {
                ok = true;
                break;
            }
            reason = net_wifi_attempt_reason();
            if (reason) {
                break;
            }
            vTaskDelay(pdMS_TO_TICKS(200));
        }
        if (!ok && !reason_is_not_found(reason)) {
            break;      /* wrong password: retrying would not help */
        }
    }
    if (ok) {
        esp_netif_ip_info_t ip = {0};
        esp_netif_t *sta = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
        if (sta && esp_netif_get_ip_info(sta, &ip) == ESP_OK) {
            esp_ip4addr_ntoa(&ip.ip, s_ip, sizeof(s_ip));
        }
        settings_set_wifi(s_cfg.ssid, s_cfg.password);
        settings_rotate_setup_pass();   /* the password shown during this setup stops working */
        s_state = WIFI_PROV_STA_CONNECTED;
        ESP_LOGI(TAG, "joined '%s': saved", s_cfg.ssid);
    } else {
        esp_wifi_disconnect();
        s_fail_reason = reason_is_not_found(reason) ? WIFI_PROV_STA_AP_NOT_FOUND : WIFI_PROV_STA_AUTH_ERROR;
        s_state = WIFI_PROV_STA_DISCONNECTED;
        ESP_LOGW(TAG, "could not join '%s' (reason %d): nothing saved", s_cfg.ssid, reason);
    }
    memset(s_cfg.password, 0, sizeof(s_cfg.password));
    s_have_cfg = false;
    s_busy = false;
    if (ok && s_done) {
        s_done();
    }
    vTaskDelete(NULL);
}

static esp_err_t apply_config(wifi_prov_ctx_t **ctx)
{
    if (!s_have_cfg || s_busy) {
        return ESP_ERR_INVALID_STATE;
    }
    s_busy = true;
    s_attempted = true;
    s_state = WIFI_PROV_STA_CONNECTING;
    if (xTaskCreate(connect_task, "ble_prov_wifi", 4096, NULL, 5, NULL) != pdPASS) {
        s_busy = false;
        s_state = WIFI_PROV_STA_DISCONNECTED;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

static wifi_prov_config_handlers_t s_handlers = {
    .get_status_handler = get_status,
    .set_config_handler = set_config,
    .apply_config_handler = apply_config,
    .ctx = NULL,
};

/* ------------------------------------------------------------------------- */
/* Scan endpoint: request {"refresh":true}? -> {"scanning":b,"aps":[{"s","r","a"}]} */
/* ------------------------------------------------------------------------- */

static esp_err_t scan_handler(uint32_t session_id, const uint8_t *inbuf, ssize_t inlen,
                              uint8_t **outbuf, ssize_t *outlen, void *priv_data)
{
    if (inbuf && inlen > 0 && inlen < 64) {
        char req[64];
        memcpy(req, inbuf, inlen);
        req[inlen] = '\0';
        cJSON *j = cJSON_Parse(req);
        if (j && cJSON_IsTrue(cJSON_GetObjectItem(j, "refresh"))) {
            net_scan_refresh();
        }
        cJSON_Delete(j);
    }
    net_ap_info_t aps[SCAN_MAX_APS];
    int n = net_scan_results(aps, SCAN_MAX_APS);
    char *str = NULL;
    for (; n >= 0; n--) {   /* drop the weakest networks until the answer fits one attribute */
        cJSON *root = cJSON_CreateObject();
        cJSON_AddBoolToObject(root, "scanning", net_scan_running());
        cJSON *arr = cJSON_AddArrayToObject(root, "aps");
        for (int i = 0; i < n; i++) {
            cJSON *o = cJSON_CreateObject();
            cJSON_AddStringToObject(o, "s", aps[i].ssid);
            cJSON_AddNumberToObject(o, "r", aps[i].rssi);
            cJSON_AddNumberToObject(o, "a", aps[i].auth);
            cJSON_AddItemToArray(arr, o);
        }
        str = cJSON_PrintUnformatted(root);
        cJSON_Delete(root);
        if (!str || strlen(str) <= SCAN_JSON_BUDGET) {
            break;
        }
        cJSON_free(str);
        str = NULL;
    }
    if (!str) {
        return ESP_ERR_NO_MEM;
    }
    *outlen = strlen(str);
    *outbuf = malloc(*outlen);
    if (!*outbuf) {
        cJSON_free(str);
        return ESP_ERR_NO_MEM;
    }
    memcpy(*outbuf, str, *outlen);
    cJSON_free(str);
    return ESP_OK;
}

/* ------------------------------------------------------------------------- */

static void free_srp(void)
{
    if (s_salt) {
        free(s_salt);
        s_salt = NULL;
    }
    if (s_verifier) {
        free(s_verifier);
        s_verifier = NULL;
    }
}

esp_err_t ble_prov_start(const char *device_name, const char *setup_pass, ble_prov_done_cb_t done)
{
    if (s_active) {
        return ESP_OK;
    }
    if (!device_name || !setup_pass_valid(setup_pass)) {
        return ESP_ERR_INVALID_ARG;
    }
    s_done = done;
    s_state = WIFI_PROV_STA_DISCONNECTED;
    s_attempted = false;
    s_failed = 0;
    s_lockout_until = 0;

    /* The verifier is derived on the watch from its own setup password: nothing secret is shared
     * with the server or other watches. */
    free_srp();
    if (esp_srp_gen_salt_verifier(SRP_USERNAME, strlen(SRP_USERNAME), setup_pass, strlen(setup_pass),
                                  &s_salt, SRP_SALT_LEN, &s_verifier, &s_verifier_len) != ESP_OK) {
        ESP_LOGE(TAG, "SRP verifier generation failed");
        free_srp();
        return ESP_FAIL;
    }
    s_sec2_params.salt = s_salt;
    s_sec2_params.salt_len = SRP_SALT_LEN;
    s_sec2_params.verifier = s_verifier;
    s_sec2_params.verifier_len = (uint16_t)s_verifier_len;

    s_pc = protocomm_new();
    if (!s_pc) {
        free_srp();
        return ESP_ERR_NO_MEM;
    }
    protocomm_ble_config_t cfg = {0};
    strlcpy(cfg.device_name, device_name, sizeof(cfg.device_name));
    memcpy(cfg.service_uuid, OLA_SERVICE_UUID, sizeof(cfg.service_uuid));
    cfg.nu_lookup = s_endpoints;
    cfg.nu_lookup_count = sizeof(s_endpoints) / sizeof(s_endpoints[0]);
    cfg.ble_bonding = 0;        /* application-level security (SRP); no OS pairing dialog */
    cfg.ble_link_encryption = 0;

    s_sec = protocomm_security2;
    s_sec.security_req_handler = limited_session_handler;

    esp_err_t err = protocomm_ble_start(s_pc, &cfg);
    if (err == ESP_OK) {
        err = protocomm_set_version(s_pc, "proto-ver", VERSION_JSON);
    }
    if (err == ESP_OK) {
        err = protocomm_set_security(s_pc, "prov-session", &s_sec, &s_sec2_params);
    }
    if (err == ESP_OK) {
        err = protocomm_add_endpoint(s_pc, "prov-config", wifi_prov_config_data_handler, &s_handlers);
    }
    if (err == ESP_OK) {
        err = protocomm_add_endpoint(s_pc, "ola-scan", scan_handler, NULL);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "start failed: %s", esp_err_to_name(err));
        protocomm_ble_stop(s_pc);
        protocomm_delete(s_pc);
        s_pc = NULL;
        free_srp();
        return err;
    }
    s_active = true;
    ESP_LOGI(TAG, "Bluetooth setup advertising as '%s'", device_name);
    return ESP_OK;
}

void ble_prov_stop(void)
{
    if (!s_active) {
        return;
    }
    protocomm_remove_endpoint(s_pc, "ola-scan");
    protocomm_remove_endpoint(s_pc, "prov-config");
    protocomm_unset_security(s_pc, "prov-session");
    protocomm_unset_version(s_pc, "proto-ver");
    protocomm_ble_stop(s_pc);   /* stops advertising and the NimBLE host */
    protocomm_delete(s_pc);
    s_pc = NULL;
    free_srp();
    memset(&s_cfg, 0, sizeof(s_cfg));
    s_active = false;
    ESP_LOGI(TAG, "Bluetooth setup stopped");
}

bool ble_prov_active(void)
{
    return s_active;
}
