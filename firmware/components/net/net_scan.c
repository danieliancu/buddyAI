/*
 * ola - nearby Wi-Fi networks for the watch's Wi-Fi screen (an asynchronous scan, strongest first,
 * de-duplicated, hidden networks skipped). Wi-Fi is set up on the watch only: no setup network, no portal.
 */
#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "net.h"
#include "net_priv.h"

static const char *TAG = "scan";

#define MAX_SCAN_RESULTS    16

static net_ap_info_t  s_scan[MAX_SCAN_RESULTS];
static int            s_scan_count;
static volatile bool  s_async_scan;
static portMUX_TYPE   s_scan_mux = portMUX_INITIALIZER_UNLOCKED;

/* Copy the driver's AP list into the cache (strongest first, de-duplicated, hidden ones skipped). */
static void collect_scan_results(void)
{
    uint16_t n = MAX_SCAN_RESULTS;
    wifi_ap_record_t *recs = calloc(n, sizeof(wifi_ap_record_t));
    if (!recs) {
        esp_wifi_clear_ap_list();
        return;
    }
    net_ap_info_t found[MAX_SCAN_RESULTS];
    int count = 0;
    if (esp_wifi_scan_get_ap_records(&n, recs) == ESP_OK) {
        for (int i = 0; i < n; i++) {
            const char *name = (const char *)recs[i].ssid;
            if (!name[0]) {
                continue;
            }
            bool dup = false;
            for (int j = 0; j < count; j++) {
                if (strcmp(found[j].ssid, name) == 0) {
                    dup = true;
                    break;
                }
            }
            if (!dup) {
                strlcpy(found[count].ssid, name, sizeof(found[0].ssid));
                found[count].rssi = recs[i].rssi;
                found[count].auth = (uint8_t)recs[i].authmode;
                found[count].channel = recs[i].primary;
                count++;
            }
        }
    }
    free(recs);
    taskENTER_CRITICAL(&s_scan_mux);
    memcpy(s_scan, found, sizeof(net_ap_info_t) * count);
    s_scan_count = count;
    taskEXIT_CRITICAL(&s_scan_mux);
    ESP_LOGI(TAG, "scan found %d networks", count);
}

void net_scan_done(void)
{
    if (s_async_scan) {
        collect_scan_results();
        s_async_scan = false;
    }
}

esp_err_t net_scan_refresh(void)
{
    if (s_async_scan) {
        return ESP_OK;
    }
    if (!g_net_wifi_started) {
        return ESP_ERR_INVALID_STATE;
    }
    if (net_wifi_connecting()) {
        return ESP_ERR_INVALID_STATE;   /* a scan would cancel the connection attempt; the caller retries */
    }
    wifi_scan_config_t sc = { .show_hidden = false };
    s_async_scan = true;
    esp_err_t err = esp_wifi_scan_start(&sc, false);
    if (err != ESP_OK) {
        s_async_scan = false;
        ESP_LOGW(TAG, "scan not started: %s", esp_err_to_name(err));
    }
    return err;
}

bool net_scan_running(void)
{
    return s_async_scan;
}

int net_scan_results(net_ap_info_t *out, int max)
{
    taskENTER_CRITICAL(&s_scan_mux);
    int n = s_scan_count < max ? s_scan_count : max;
    memcpy(out, s_scan, sizeof(net_ap_info_t) * n);
    taskEXIT_CRITICAL(&s_scan_mux);
    return n;
}
