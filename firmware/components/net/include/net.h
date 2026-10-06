/*
 * ola - networking
 *
 *  - Wi-Fi station with automatic reconnect and exponential backoff
 *  - SoftAP "ola-XXXX" + captive portal (DNS hijack + HTTP form) for
 *    SSID / password (+ optional server_url in development builds)
 *  - SNTP time sync (system clock; the app mirrors it to the RTC)
 *  - optional mDNS discovery of the server (_buddyai._tcp)
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    NET_EVT_STA_CONNECTED,      /* got IP */
    NET_EVT_STA_DISCONNECTED,   /* lost link / IP (reconnect is automatic) */
    NET_EVT_PORTAL_STARTED,
    NET_EVT_PORTAL_SAVED,       /* new credentials stored - app should reboot */
    NET_EVT_TIME_SYNCED,        /* SNTP set the system clock */
} net_event_t;

typedef void (*net_event_cb_t)(net_event_t ev, void *ctx);

esp_err_t net_init(net_event_cb_t cb, void *ctx);

/* ---- station ---- */
esp_err_t net_wifi_connect(const char *ssid, const char *pass);
bool      net_wifi_connected(void);
int       net_wifi_rssi(void);              /* dBm, 0 if not connected */
int       net_wifi_last_disconnect_reason(void);  /* wifi_err_reason_t of the last lost connection, 0 = none */
/* Modem sleep while idle; disabled during a conversation turn for latency. */
void      net_wifi_set_power_save(bool enable);

/* ---- provisioning portal ---- */
esp_err_t   net_portal_start(void);
void        net_portal_stop(void);
bool        net_portal_active(void);
const char *net_portal_ssid(void);          /* "ola-XXXX" */

/* ---- time ---- */
void      net_set_timezone(const char *tz_posix);
void      net_sntp_start(void);
bool      net_time_valid(void);             /* system clock looks sane (>= 2024) */

/* ---- discovery ---- */
/* Browse _buddyai._tcp; builds "ws://<ip>:<port><path>" (TXT "path", default
 * /ws/device; TXT "scheme" ws|wss, default ws). */
esp_err_t net_mdns_find_server(char *url, size_t len, uint32_t timeout_ms);

#ifdef __cplusplus
}
#endif
