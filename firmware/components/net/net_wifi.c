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
#include "sdkconfig.h"
#include "esp_netif_sntp.h"
#include "esp_timer.h"
#include "mdns.h"
#include "net.h"
#include "net_priv.h"
#include "settings.h"

static const char *TAG = "net";

#define BACKOFF_MIN_MS      1000
#define BACKOFF_MAX_MS      60000
#define PICK_SCAN_MAX       20      /* access points read from one scan */
#define PICK_TIE_DB         8       /* a more recently used network wins if at most this many dB weaker */
#define AUTH_FAILS_TO_FORGET 2      /* an untried password rejected this often is dropped */

static net_event_cb_t   s_cb;
static void            *s_cb_ctx;
static esp_timer_handle_t s_reconnect_timer;
static uint32_t         s_backoff_ms = BACKOFF_MIN_MS;
static volatile bool    s_connected;
static volatile int     s_last_disc_reason;    /* wifi_err_reason_t of the last drop, 0 = none */
static volatile int     s_attempt_reason;      /* last failed attempt since net_wifi_connect(), 0 = none */
static bool             s_sta_configured;
static bool             s_mdns_ready;
static bool             s_sntp_started;

/* Saved-network mode (net_wifi_connect_saved): each attempt scans and joins the best saved network in
 * range; after a lost connection the same network is tried once more first, without a scan. */
static bool             s_saved_mode;
static volatile bool    s_pick_scan;           /* our scan is running (not the portal's) */
static bool             s_retry_same;
static wifi_saved_list_t s_saved;              /* read before each pick */
static wifi_saved_t     s_cur;                 /* the network being joined, or joined */
static int              s_auth_fails;          /* consecutive rejected attempts on s_cur */

/* A network chosen on the watch (net_wifi_join): tried now, saved only once it connects. */
#define JOIN_MISSES_MAX     2                      /* "not found" attempts before giving up */
#define JOIN_KICK_US        (15 * 1000000LL)       /* no news this long after the tap: try again */
#define JOIN_GIVE_UP_US     (30 * 1000000LL)       /* still not on it: "not found", back to the saved ones */
static int64_t          s_join_started_us;
static volatile net_join_state_t s_join = NET_JOIN_IDLE;
static int              s_join_misses;
static bool             s_leaving;             /* we left the current network to join another one */

/* A connection attempt is running (esp_wifi_connect until the IP or the disconnect event): a scan started
 * now would cancel it without any event, so the Wi-Fi screen waits (net_wifi_connecting). */
#define CONNECT_BUSY_MAX_US (15 * 1000000LL)       /* never longer than this, whatever happens */
#define CONNECT_SILENT_US   (10 * 1000000LL)       /* no IP and no disconnect this long: cancel, retry */
static volatile int64_t s_connect_since_us;
static esp_timer_handle_t s_connect_watchdog;

static void schedule_reconnect(void);

/* An attempt the driver never answers (seen at boot with a phone hotspot: no auth step, no disconnect
 * event for a minute) would wait for good: cancel it and go on as after a failure. */
static void connect_watchdog_cb(void *arg)
{
    if (s_connected || !s_connect_since_us) {
        return;
    }
    ESP_LOGW(TAG, "no answer from '%s': trying again", s_cur.ssid[0] ? s_cur.ssid : "?");
    s_connect_since_us = 0;
    esp_wifi_disconnect();
    schedule_reconnect();
}

static esp_err_t sta_connect(void)
{
    s_connect_since_us = esp_timer_get_time();
    esp_err_t err = esp_wifi_connect();
    if (err != ESP_OK) {
        s_connect_since_us = 0;
    } else if (s_connect_watchdog) {
        esp_timer_stop(s_connect_watchdog);
        esp_timer_start_once(s_connect_watchdog, CONNECT_SILENT_US);
    }
    return err;
}

bool net_wifi_connecting(void)
{
    int64_t t = s_connect_since_us;
    return t && esp_timer_get_time() - t < CONNECT_BUSY_MAX_US;
}

esp_netif_t *g_net_sta_netif;
esp_netif_t *g_net_ap_netif;
bool g_net_wifi_started;

void net_emit(net_event_t ev)
{
    if (s_cb) {
        s_cb(ev, s_cb_ctx);
    }
}

static void pick_next(void);

static void reconnect_cb(void *arg)
{
    if (s_sta_configured && !s_connected) {
        ESP_LOGI(TAG, "reconnecting...");
        if (s_saved_mode) {
            pick_next();
        } else {
            sta_connect();
        }
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

/* Radio power save: off during a conversation turn (latency); in screen standby the radio wakes only every
 * CONFIG_BUDDYAI_STANDBY_LISTEN_INTERVAL beacons (WIFI_PS_MAX_MODEM; the access point keeps frames for it, so a
 * reminder arrives a few hundred ms later at most); otherwise at every DTIM beacon (WIFI_PS_MIN_MODEM). */
static bool s_ps_turn, s_ps_standby;

static void apply_ps(void)
{
    if (!g_net_wifi_started) {
        return;
    }
    wifi_ps_type_t ps = s_ps_turn ? WIFI_PS_NONE
                      : (s_ps_standby && CONFIG_BUDDYAI_STANDBY_LISTEN_INTERVAL > 0) ? WIFI_PS_MAX_MODEM
                      : WIFI_PS_MIN_MODEM;
    esp_wifi_set_ps(ps);
}

/* Join s_cur. drop_current: leave the network the station is on first (a manual change of network). */
static esp_err_t sta_join(bool drop_current)
{
    wifi_config_t wc = {0};
    strlcpy((char *)wc.sta.ssid, s_cur.ssid, sizeof(wc.sta.ssid));
    strlcpy((char *)wc.sta.password, s_cur.pass, sizeof(wc.sta.password));
    wc.sta.threshold.authmode = s_cur.pass[0] ? WIFI_AUTH_WPA_PSK : WIFI_AUTH_OPEN;
    wc.sta.pmf_cfg.capable = true;
    wc.sta.scan_method = WIFI_ALL_CHANNEL_SCAN;
    wc.sta.sort_method = WIFI_CONNECT_AP_BY_SIGNAL;
    wc.sta.listen_interval = CONFIG_BUDDYAI_STANDBY_LISTEN_INTERVAL;   /* beacons between wake-ups, standby only */

    s_sta_configured = true;
    s_attempt_reason = 0;
    wifi_mode_t mode = WIFI_MODE_NULL;
    esp_wifi_get_mode(&mode);
    if (mode != WIFI_MODE_APSTA) {
        ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "mode");
    }
    esp_err_t err = esp_wifi_set_config(WIFI_IF_STA, &wc);
    memset(&wc, 0, sizeof(wc));
    ESP_RETURN_ON_ERROR(err, TAG, "config");
    ESP_LOGI(TAG, "connecting to '%s'", s_cur.ssid);
    if (!g_net_wifi_started) {
        err = esp_wifi_start();         /* STA_START triggers the first connect */
        g_net_wifi_started = (err == ESP_OK);
    } else {
        if (drop_current) {
            esp_wifi_disconnect();
        }
        err = sta_connect();
        if (err != ESP_OK && s_saved_mode) {
            schedule_reconnect();
        }
    }
    apply_ps();
    return err;
}

static void join_saved(const wifi_saved_t *n)
{
    if (strcmp(n->ssid, s_cur.ssid) != 0 || strcmp(n->pass, s_cur.pass) != 0) {
        s_auth_fails = 0;
    }
    s_cur = *n;
    sta_join(false);
}

/* The next attempt in saved-network mode: the network just lost, the only saved one, or a scan. */
static void pick_next(void)
{
    if (s_join == NET_JOIN_TRYING) {
        if (esp_timer_get_time() - s_join_started_us < JOIN_GIVE_UP_US) {
            sta_join(false);    /* the network chosen on the watch, again */
            return;
        }
        ESP_LOGW(TAG, "could not join '%s' in time: back to the saved networks", s_cur.ssid);
        s_join = NET_JOIN_NOT_FOUND;
        memset(&s_cur, 0, sizeof(s_cur));
    }
    if (net_scan_running()) {
        schedule_reconnect();   /* the Wi-Fi screen is scanning: the radio is busy */
        return;
    }
    if (s_retry_same && s_cur.ssid[0]) {
        s_retry_same = false;
        sta_join(false);
        return;
    }
    s_retry_same = false;
    if (settings_get_wifi_list(&s_saved) == 0) {
        ESP_LOGW(TAG, "no saved Wi-Fi network");
        return;
    }
    if (s_saved.count == 1) {
        join_saved(&s_saved.net[0]);
        return;
    }
    wifi_scan_config_t sc = { .show_hidden = false };
    s_pick_scan = true;
    if (esp_wifi_scan_start(&sc, false) != ESP_OK) {
        s_pick_scan = false;
        join_saved(&s_saved.net[0]);
    }
}

static void pick_from_scan(void)
{
    uint16_t n = PICK_SCAN_MAX;
    wifi_ap_record_t *recs = calloc(n, sizeof(wifi_ap_record_t));
    int pick = -1;
    if (recs && esp_wifi_scan_get_ap_records(&n, recs) == ESP_OK) {
        const char *ssids[PICK_SCAN_MAX];
        int8_t rssi[PICK_SCAN_MAX];
        for (int i = 0; i < n; i++) {
            ssids[i] = (const char *)recs[i].ssid;
            rssi[i] = recs[i].rssi;
        }
        pick = wifi_list_pick(&s_saved, ssids, rssi, n, PICK_TIE_DB);
    } else {
        esp_wifi_clear_ap_list();
    }
    free(recs);
    if (pick < 0) {
        /* None seen: the most recent one directly (a hidden network answers only when asked by name). */
        ESP_LOGI(TAG, "no saved network seen in the scan");
        pick = 0;
    }
    join_saved(&s_saved.net[pick]);
}

static bool reason_is_auth(int reason)
{
    return reason == WIFI_REASON_AUTH_FAIL || reason == WIFI_REASON_4WAY_HANDSHAKE_TIMEOUT ||
           reason == WIFI_REASON_HANDSHAKE_TIMEOUT || reason == WIFI_REASON_MIC_FAILURE;
}

/* A failed attempt on the network chosen on the watch: wrong password, or not found twice, ends it and
 * the saved networks take over again. */
static void join_attempt_failed(int reason)
{
    if (reason_is_auth(reason)) {
        s_join = NET_JOIN_WRONG_PASSWORD;
    } else if (++s_join_misses >= JOIN_MISSES_MAX) {
        s_join = NET_JOIN_NOT_FOUND;
    } else {
        return;
    }
    ESP_LOGW(TAG, "could not join '%s' (reason %d): back to the saved networks", s_cur.ssid, reason);
    memset(&s_cur, 0, sizeof(s_cur));
    s_retry_same = false;
}

static void saved_attempt_failed(bool was_connected, int reason)
{
    if (was_connected) {
        s_retry_same = true;    /* usually a short drop: the same network again, no scan */
        return;
    }
    /* A password set up but never accepted is dropped after repeated rejections, unless it is the only
     * saved network (then the watch keeps trying and shows "no Wi-Fi", as before). */
    if (reason_is_auth(reason) && !s_cur.verified && s_saved.count > 1 &&
            ++s_auth_fails >= AUTH_FAILS_TO_FORGET) {
        ESP_LOGW(TAG, "'%s' rejected the new password: forgotten, back to the other saved networks", s_cur.ssid);
        settings_forget_wifi(s_cur.ssid, s_cur.pass);
        memset(&s_cur, 0, sizeof(s_cur));
        s_auth_fails = 0;
    }
}

static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT) {
        switch (id) {
        case WIFI_EVENT_STA_START:
            if (s_saved_mode) {
                pick_next();
            } else if (s_sta_configured) {
                sta_connect();
            }
            break;
        case WIFI_EVENT_STA_DISCONNECTED: {
            wifi_event_sta_disconnected_t *d = data;
            ESP_LOGW(TAG, "STA disconnected (reason %d)", d ? d->reason : -1);
            bool was = s_connected;
            s_connected = false;
            s_connect_since_us = 0;
            esp_timer_stop(s_connect_watchdog);
            if (s_leaving) {
                s_leaving = false;      /* left on purpose: now join the network chosen on the watch */
                net_emit(NET_EVT_STA_DISCONNECTED);
                sta_join(false);
                break;
            }
            if (was && d) {
                s_last_disc_reason = d->reason;
            }
            if (d) {
                s_attempt_reason = d->reason;
            }
            if (was) {
                net_emit(NET_EVT_STA_DISCONNECTED);
            }
            /* While the portal runs, the AP must stay on a fixed channel;
             * pause STA retries to keep the phone connection stable. */
            if (!net_portal_active()) {
                if (s_join == NET_JOIN_TRYING && !was) {
                    join_attempt_failed(d ? d->reason : 0);
                } else if (s_saved_mode) {
                    saved_attempt_failed(was, d ? d->reason : 0);
                }
                schedule_reconnect();
            }
            break;
        }
        case WIFI_EVENT_SCAN_DONE:
            if (s_pick_scan) {
                s_pick_scan = false;
                pick_from_scan();
            } else {
                net_portal_scan_done();
            }
            break;
        default:
            break;
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *e = data;
        ESP_LOGI(TAG, "got IP " IPSTR, IP2STR(&e->ip_info.ip));
        s_connected = true;
        s_connect_since_us = 0;
        esp_timer_stop(s_connect_watchdog);
        s_backoff_ms = BACKOFF_MIN_MS;
        if (s_join == NET_JOIN_TRYING) {
            s_join = NET_JOIN_OK;
        }
        if (s_saved_mode) {
            s_auth_fails = 0;
            s_cur.verified = true;
            settings_wifi_joined(s_cur.ssid, s_cur.pass);   /* most recent first; writes only on a change */
        }
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
    const esp_timer_create_args_t wargs = {
        .callback = connect_watchdog_cb,
        .name = "wifi_watchdog",
    };
    ESP_RETURN_ON_ERROR(esp_timer_create(&wargs, &s_connect_watchdog), TAG, "timer");

    esp_netif_set_hostname(g_net_sta_netif, "buddyai-watch");
    return ESP_OK;
}

esp_err_t net_wifi_connect(const char *ssid, const char *pass)
{
    s_saved_mode = false;
    memset(&s_cur, 0, sizeof(s_cur));
    strlcpy(s_cur.ssid, ssid, sizeof(s_cur.ssid));
    strlcpy(s_cur.pass, pass ? pass : "", sizeof(s_cur.pass));
    return sta_join(true);
}

esp_err_t net_wifi_connect_saved(void)
{
    if (settings_get_wifi_list(&s_saved) == 0) {
        return ESP_ERR_NOT_FOUND;
    }
    s_saved_mode = true;
    s_sta_configured = true;
    s_retry_same = false;
    s_attempt_reason = 0;
    wifi_mode_t mode = WIFI_MODE_NULL;
    esp_wifi_get_mode(&mode);
    if (mode != WIFI_MODE_APSTA) {
        ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "mode");
    }
    ESP_LOGI(TAG, "%d saved Wi-Fi network(s)", s_saved.count);
    esp_err_t err = ESP_OK;
    if (!g_net_wifi_started) {
        err = esp_wifi_start();         /* STA_START picks the first network */
        g_net_wifi_started = (err == ESP_OK);
    } else {
        pick_next();
    }
    apply_ps();
    return err;
}

bool net_wifi_connected(void)
{
    return s_connected;
}

esp_err_t net_wifi_join(const char *ssid, const char *pass)
{
    if (!ssid || !ssid[0] || !g_net_wifi_started) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_timer_stop(s_reconnect_timer);
    s_saved_mode = true;
    s_retry_same = false;
    s_backoff_ms = BACKOFF_MIN_MS;
    s_join_misses = 0;
    s_auth_fails = 0;
    memset(&s_cur, 0, sizeof(s_cur));
    strlcpy(s_cur.ssid, ssid, sizeof(s_cur.ssid));
    strlcpy(s_cur.pass, pass ? pass : "", sizeof(s_cur.pass));
    s_join = NET_JOIN_TRYING;
    s_join_started_us = esp_timer_get_time();
    esp_timer_start_once(s_reconnect_timer, JOIN_KICK_US);  /* a retry if no event comes at all */
    if (s_connected) {
        /* Leave first; the disconnect event then starts the join (a connect issued while the driver is
         * still leaving can be dropped, which would leave the screen on "Connecting..." for good). */
        s_leaving = true;
        return esp_wifi_disconnect();
    }
    return sta_join(false);
}

net_join_state_t net_wifi_join_state(void)
{
    return s_join;
}

void net_wifi_join_ack(void)
{
    if (s_join != NET_JOIN_TRYING) {
        s_join = NET_JOIN_IDLE;
    }
}

bool net_wifi_current_ssid(char out[33])
{
    wifi_ap_record_t ap;
    if (!s_connected || esp_wifi_sta_get_ap_info(&ap) != ESP_OK) {
        out[0] = '\0';
        return false;
    }
    strlcpy(out, (const char *)ap.ssid, 33);
    return true;
}

int net_wifi_last_disconnect_reason(void)
{
    return s_last_disc_reason;
}

int net_wifi_attempt_reason(void)
{
    return s_attempt_reason;
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
    s_ps_turn = !enable;
    apply_ps();
}

void net_wifi_set_standby(bool standby)
{
    s_ps_standby = standby;
    apply_ps();
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
