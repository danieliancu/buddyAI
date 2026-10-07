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
/* wifi_err_reason_t of the last failed connection attempt since net_wifi_connect() (0 = none yet):
 * Bluetooth setup reports "wrong password" / "network not found" from it. */
int       net_wifi_attempt_reason(void);
/* Modem sleep while idle; disabled during a conversation turn for latency. */
void      net_wifi_set_power_save(bool enable);

/* ---- provisioning portal ---- */
/* The "ola-XXXX" setup network is WPA2-protected with `ap_pass` (the watch's setup password, 8..63
 * characters, shown on the watch). */
esp_err_t   net_portal_start(const char *ap_pass);
void        net_portal_stop(void);
bool        net_portal_active(void);
const char *net_portal_ssid(void);          /* "ola-XXXX" */

/* Nearby networks seen by the portal's scan (also offered over Bluetooth setup). */
typedef struct {
    char    ssid[33];
    int8_t  rssi;
    uint8_t auth;       /* wifi_auth_mode_t */
    uint8_t channel;
} net_ap_info_t;
int       net_scan_results(net_ap_info_t *out, int max);
esp_err_t net_scan_refresh(void);          /* non-blocking; results update when the scan ends */
bool      net_scan_running(void);

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
