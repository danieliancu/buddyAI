/*
 * ola - Wi-Fi station, SNTP, mDNS discovery
 */
#include <string.h>
#include <stdlib.h>
#include <time.h>
#include <sys/time.h>
#include "freertos/FreeRTOS.h"
#include "esp_log.h"
#include "esp_check.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "esp_timer.h"
#include "mdns.h"
#include "net.h"
#include "net_priv.h"

static const char *TAG = "net";

#define BACKOFF_MIN_MS      1000
#define BACKOFF_MAX_MS      60000

static net_event_cb_t   s_cb;
static void            *s_cb_ctx;
static esp_timer_handle_t s_reconnect_timer;
static uint32_t         s_backoff_ms = BACKOFF_MIN_MS;
static volatile bool    s_connected;
static volatile int     s_last_disc_reason;    /* wifi_err_reason_t of the last drop, 0 = none */
static bool             s_sta_configured;
static bool             s_mdns_ready;
static bool             s_sntp_started;

esp_netif_t *g_net_sta_netif;
esp_netif_t *g_net_ap_netif;
bool g_net_wifi_started;

void net_emit(net_event_t ev)
{
    if (s_cb) {
        s_cb(ev, s_cb_ctx);
    }
}

static void reconnect_cb(void *arg)
{
    if (s_sta_configured && !s_connected) {
        ESP_LOGI(TAG, "reconnecting...");
        esp_wifi_connect();
    }
}

static void schedule_reconnect(void)
{
    if (!s_sta_configured) {
        return;
    }
    esp_timer_stop(s_reconnect_timer);
    esp_timer_start_once(s_reconnect_timer, (uint64_t)s_backoff_ms * 1000);
    ESP_LOGI(TAG, "retry in %lu ms", (unsigned long)s_backoff_ms);
    s_backoff_ms *= 2;
    if (s_backoff_ms > BACKOFF_MAX_MS) {
        s_backoff_ms = BACKOFF_MAX_MS;
    }
}

static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT) {
        switch (id) {
        case WIFI_EVENT_STA_START:
            if (s_sta_configured) {
                esp_wifi_connect();
            }
            break;
        case WIFI_EVENT_STA_DISCONNECTED: {
            wifi_event_sta_disconnected_t *d = data;
            ESP_LOGW(TAG, "STA disconnected (reason %d)", d ? d->reason : -1);
            bool was = s_connected;
            s_connected = false;
            if (was && d) {
                s_last_disc_reason = d->reason;
            }
            if (was) {
                net_emit(NET_EVT_STA_DISCONNECTED);
            }
            /* While the portal runs, the AP must stay on a fixed channel;
             * pause STA retries to keep the phone connection stable. */
            if (!net_portal_active()) {
                schedule_reconnect();
            }
            break;
        }
        default:
            break;
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = data;
        ESP_LOGI(TAG, "got IP " IPSTR, IP2STR(&e->ip_info.ip));
        s_connected = true;
        s_backoff_ms = BACKOFF_MIN_MS;
        net_emit(NET_EVT_STA_CONNECTED);
    }
}

esp_err_t net_init(net_event_cb_t cb, void *ctx)
{
    s_cb = cb;
    s_cb_ctx = ctx;

    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "netif");
    esp_err_t err = esp_event_loop_create_default();
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        return err;
    }
    g_net_sta_netif = esp_netif_create_default_wifi_sta();
    g_net_ap_netif = esp_netif_create_default_wifi_ap();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&cfg), TAG, "wifi init");
    ESP_RETURN_ON_ERROR(esp_wifi_set_storage(WIFI_STORAGE_RAM), TAG, "storage");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, NULL), TAG, "evt");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, NULL), TAG, "evt");

    const esp_timer_create_args_t targs = {
        .callback = reconnect_cb,
        .name = "wifi_retry",
    };
    ESP_RETURN_ON_ERROR(esp_timer_create(&targs, &s_reconnect_timer), TAG, "timer");

    esp_netif_set_hostname(g_net_sta_netif, "buddyai-watch");
    return ESP_OK;
}

esp_err_t net_wifi_connect(const char *ssid, const char *pass)
{
    wifi_config_t wc = {0};
    strlcpy((char *)wc.sta.ssid, ssid, sizeof(wc.sta.ssid));
    strlcpy((char *)wc.sta.password, pass ? pass : "", sizeof(wc.sta.password));
    wc.sta.threshold.authmode = (pass && pass[0]) ? WIFI_AUTH_WPA_PSK : WIFI_AUTH_OPEN;
    wc.sta.pmf_cfg.capable = true;
    wc.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    wc.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;

    s_sta_configured = true;
    wifi_mode_t mode = WIFI_MODE_NULL;
    esp_wifi_get_mode(&mode);
    if (mode != WIFI_MODE_APSTA) {
        ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "mode");
    }
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &wc), TAG, "config");
    ESP_LOGI(TAG, "connecting to '%s'", ssid);
    esp_err_t err = ESP_OK;
    if (!g_net_wifi_started) {
        err = esp_wifi_start();         /* STA_START triggers the first connect */
        g_net_wifi_started = (err == ESP_OK);
    } else {
        esp_wifi_disconnect();
        err = esp_wifi_connect();
    }
    esp_wifi_set_ps(WIFI_PS_MIN_MODEM);
    return err;
}

bool net_wifi_connected(void)
{
    return s_connected;
}

int net_wifi_last_disconnect_reason(void)
{
    return s_last_disc_reason;
}

int net_wifi_rssi(void)
{
    if (!s_connected) {
        return 0;
    }
    int rssi = 0;
    if (esp_wifi_sta_get_rssi(&rssi) != ESP_OK) {
        return 0;
    }
    return rssi;
}

void net_wifi_set_power_save(bool enable)
{
    esp_wifi_set_ps(enable ? WIFI_PS_MIN_MODEM : WIFI_PS_NONE);
}

/* ------------------------------------------------------------------------- */
/* Time                                                                       */
/* ------------------------------------------------------------------------- */

void net_set_timezone(const char *tz_posix)
{
    if (tz_posix && tz_posix[0]) {
        setenv("TZ", tz_posix, 1);
        tzset();
    }
}

bool net_time_valid(void)
{
    time_t now = time(NULL);
    return now > 1704067200; /* 2024-01-01 */
}

static void sntp_sync_cb(struct timeval *tv)
{
    ESP_LOGI(TAG, "SNTP time synchronized");
    net_emit(NET_EVT_TIME_SYNCED);
}

void net_sntp_start(void)
{
    if (s_sntp_started) {
        return;
    }
    esp_sntp_config_t cfg = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    cfg.sync_cb = sntp_sync_cb;
    if (esp_netif_sntp_init(&cfg) == ESP_OK) {
        s_sntp_started = true;
    }
}

/* ------------------------------------------------------------------------- */
/* mDNS                                                                       */
/* ------------------------------------------------------------------------- */

esp_err_t net_mdns_find_server(char *url, size_t len, uint32_t timeout_ms)
{
    if (!s_connected) {
        return ESP_ERR_INVALID_STATE;
    }
    if (!s_mdns_ready) {
        ESP_RETURN_ON_ERROR(mdns_init(), TAG, "mdns init");
        mdns_hostname_set("buddyai-watch");
        s_mdns_ready = true;
    }

    mdns_result_t *results = NULL;
    esp_err_t err = mdns_query_ptr("_buddyai", "_tcp", timeout_ms, 4, &results);
    if (err != ESP_OK || !results) {
        ESP_LOGI(TAG, "mDNS: no _buddyai._tcp service found");
        return ESP_ERR_NOT_FOUND;
    }

    err = ESP_ERR_NOT_FOUND;
    for (mdns_result_t *r = results; r && err != ESP_OK; r = r->next) {
        const char *path = "/ws/device";
        const char *scheme = "ws";
        for (size_t i = 0; i < r->txt_count; i++) {
            if (r->txt[i].key && r->txt[i].value) {
                if (strcmp(r->txt[i].key, "path") == 0) {
                    path = r->txt[i].value;
                } else if (strcmp(r->txt[i].key, "scheme") == 0) {
                    scheme = r->txt[i].value;
                }
            }
        }
        for (mdns_ip_addr_t *a = r->addr; a; a = a->next) {
            if (a->addr.type == ESP_IPADDR_TYPE_V4) {
                snprintf(url, len, "%s://" IPSTR ":%u%s", scheme, IP2STR(&a->addr.u_addr.ip4), r->port, path);
                ESP_LOGI(TAG, "mDNS: found server %s", url);
                err = ESP_OK;
                break;
            }
        }
    }
    mdns_query_results_free(results);
    return err;
}
