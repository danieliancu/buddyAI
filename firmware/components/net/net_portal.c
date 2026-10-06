/*
 * ola - SoftAP provisioning with captive portal.
 *
 * AP "ola-XXXX" (open, XXXX = last MAC bytes). A tiny DNS server answers
 * every query with the AP address so phones pop up the portal; unknown HTTP
 * paths (generate_204, hotspot-detect.html, ...) redirect to the form.
 * The form stores SSID / password in NVS. Development builds also offer an
 * optional server_url; release builds have no such field, so a stranger on the
 * open AP cannot point the watch at their own server.
 */
#include <string.h>
#include <stdlib.h>
#include <ctype.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_check.h"
#include "esp_wifi.h"
#include "esp_mac.h"
#include "esp_http_server.h"
#include "sdkconfig.h"
#include "lwip/sockets.h"
#include "lwip/inet.h"
#include "settings.h"
#include "net.h"
#include "net_priv.h"

static const char *TAG = "portal";

#define PORTAL_IP           "192.168.4.1"
#define MAX_SCAN_RESULTS    16
#define DNS_PORT            53

/* Development builds only: the optional Server URL field (e.g. a LAN server). */
#if !CONFIG_BUDDYAI_RELEASE_BUILD
#define PORTAL_URL_PLACEHOLDER  "ws://192.168.1.10:8765/ws/device"
#define PORTAL_URL_HINT         "ws:// or wss:// &mdash; leave empty for automatic discovery."
#endif

static httpd_handle_t s_httpd;
static TaskHandle_t   s_dns_task;
static volatile bool  s_dns_run;
static bool           s_active;
static char           s_ap_ssid[20];
static char           s_scan[MAX_SCAN_RESULTS][33];
static int            s_scan_count;

/* ------------------------------------------------------------------------- */
/* DNS hijack                                                                 */
/* ------------------------------------------------------------------------- */

static void dns_task(void *arg)
{
    int sock = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (sock < 0) {
        ESP_LOGE(TAG, "dns socket failed");
        vTaskDelete(NULL);
        return;
    }
    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port = htons(DNS_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    if (bind(sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        ESP_LOGE(TAG, "dns bind failed");
        close(sock);
        vTaskDelete(NULL);
        return;
    }
    struct timeval tv = { .tv_sec = 1, .tv_usec = 0 };
    setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));

    uint8_t buf[512];
    const uint32_t ip = inet_addr(PORTAL_IP);
    while (s_dns_run) {
        struct sockaddr_in from;
        socklen_t flen = sizeof(from);
        int len = recvfrom(sock, buf, sizeof(buf) - 16, 0, (struct sockaddr *)&from, &flen);
        if (len < 12) {
            continue;
        }
        /* Only standard queries with at least one question. */
        if ((buf[2] & 0x80) || ((buf[4] << 8) | buf[5]) == 0) {
            continue;
        }
        /* Walk the first question name to find its end. */
        int p = 12;
        while (p < len && buf[p] != 0) {
            p += buf[p] + 1;
        }
        p += 5; /* zero byte + QTYPE + QCLASS */
        if (p > len) {
            continue;
        }
        /* Header: response, recursion available, 1 question, 1 answer. */
        buf[2] = 0x81;
        buf[3] = 0x80;
        buf[4] = 0; buf[5] = 1;
        buf[6] = 0; buf[7] = 1;
        buf[8] = buf[9] = buf[10] = buf[11] = 0;
        int o = p;
        const uint8_t answer[] = {
            0xC0, 0x0C,             /* name: pointer to question */
            0x00, 0x01,             /* type A */
            0x00, 0x01,             /* class IN */
            0x00, 0x00, 0x00, 0x3C, /* TTL 60 s */
            0x00, 0x04,             /* rdlength */
        };
        memcpy(buf + o, answer, sizeof(answer));
        o += sizeof(answer);
        memcpy(buf + o, &ip, 4);
        o += 4;
        sendto(sock, buf, o, 0, (struct sockaddr *)&from, flen);
    }
    close(sock);
    vTaskDelete(NULL);
}

/* ------------------------------------------------------------------------- */
/* HTTP                                                                       */
/* ------------------------------------------------------------------------- */

static void url_decode(char *s)
{
    char *o = s;
    for (; *s; s++) {
        if (*s == '+') {
            *o++ = ' ';
        } else if (*s == '%' && isxdigit((unsigned char)s[1]) && isxdigit((unsigned char)s[2])) {
            char hex[3] = { s[1], s[2], 0 };
            *o++ = (char)strtol(hex, NULL, 16);
            s += 2;
        } else {
            *o++ = *s;
        }
    }
    *o = '\0';
}

/* Minimal HTML escaping for values echoed into the page. */
static void send_escaped(httpd_req_t *req, const char *s)
{
    char buf[8];
    for (; *s; s++) {
        switch (*s) {
        case '<': httpd_resp_sendstr_chunk(req, "&lt;"); break;
        case '>': httpd_resp_sendstr_chunk(req, "&gt;"); break;
        case '&': httpd_resp_sendstr_chunk(req, "&amp;"); break;
        case '"': httpd_resp_sendstr_chunk(req, "&quot;"); break;
        default:
            buf[0] = *s;
            buf[1] = 0;
            httpd_resp_sendstr_chunk(req, buf);
        }
    }
}

static const char PAGE_HEAD[] =
    "<!DOCTYPE html><html><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width,initial-scale=1'>"
    "<title>ola setup</title><style>"
    "body{font-family:system-ui,sans-serif;background:#0b0e14;color:#e6e9ef;margin:0;padding:24px}"
    "main{max-width:420px;margin:auto}h1{font-size:1.4em;color:#4F8CFF}"
    "label{display:block;margin:16px 0 6px;font-size:.9em;color:#b0b8c8}"
    "input{width:100%;box-sizing:border-box;padding:12px;border-radius:10px;border:1px solid #2a3140;"
    "background:#141925;color:#e6e9ef;font-size:1em}"
    "button{margin-top:24px;width:100%;padding:14px;border:0;border-radius:10px;background:#4F8CFF;"
    "color:#fff;font-size:1.05em}small{color:#7d8699}"
    "</style></head><body><main>";

static esp_err_t root_get(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_sendstr_chunk(req, PAGE_HEAD);
    httpd_resp_sendstr_chunk(req,
        "<h1>ola &middot; Wi-Fi</h1>"
        "<form method='POST' action='/save'>"
        "<label>Wi-Fi network</label>"
        "<input name='ssid' list='nets' required maxlength='32' autocomplete='off'><datalist id='nets'>");
    for (int i = 0; i < s_scan_count; i++) {
        httpd_resp_sendstr_chunk(req, "<option value=\"");
        send_escaped(req, s_scan[i]);
        httpd_resp_sendstr_chunk(req, "\">");
    }
    httpd_resp_sendstr_chunk(req,
        "</datalist>"
        "<label>Password</label>"
        "<input name='password' type='password' maxlength='64'>");
#if !CONFIG_BUDDYAI_RELEASE_BUILD
    httpd_resp_sendstr_chunk(req,
        "<label>Server URL <small>(optional)</small></label>"
        "<input name='server_url' maxlength='190' placeholder='" PORTAL_URL_PLACEHOLDER "' value=\"");
    char url[SETTINGS_URL_MAX];
    if (settings_get_server_url(url, sizeof(url))) {
        send_escaped(req, url);
    }
    httpd_resp_sendstr_chunk(req, "\"><small>" PORTAL_URL_HINT "</small>");
#endif
    httpd_resp_sendstr_chunk(req, "<button type='submit'>Save</button></form></main></body></html>");
    httpd_resp_sendstr_chunk(req, NULL);
    return ESP_OK;
}

static bool form_field(const char *body, const char *key, char *out, size_t len)
{
    if (httpd_query_key_value(body, key, out, len) != ESP_OK) {
        out[0] = '\0';
        return false;
    }
    url_decode(out);
    return true;
}

static esp_err_t save_post(httpd_req_t *req)
{
    if (req->content_len <= 0 || req->content_len > 1024) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "bad form");
        return ESP_FAIL;
    }
    char *body = calloc(1, req->content_len + 1);
    if (!body) {
        return ESP_ERR_NO_MEM;
    }
    int got = 0;
    while (got < req->content_len) {
        int r = httpd_req_recv(req, body + got, req->content_len - got);
        if (r <= 0) {
            free(body);
            return ESP_FAIL;
        }
        got += r;
    }

    char ssid[SETTINGS_SSID_MAX * 3], pass[SETTINGS_PASS_MAX * 3], url[SETTINGS_URL_MAX * 3];
    form_field(body, "ssid", ssid, sizeof(ssid));
    form_field(body, "password", pass, sizeof(pass));
#if CONFIG_BUDDYAI_RELEASE_BUILD
    url[0] = '\0';     /* no Server URL field: a posted value is ignored */
#else
    form_field(body, "server_url", url, sizeof(url));
#endif
    free(body);

    bool url_ok = url[0] == '\0' || strncmp(url, "wss://", 6) == 0 || strncmp(url, "ws://", 5) == 0;
    if (ssid[0] == '\0' || strlen(ssid) >= SETTINGS_SSID_MAX || strlen(pass) >= SETTINGS_PASS_MAX ||
        strlen(url) >= SETTINGS_URL_MAX || !url_ok) {
        httpd_resp_set_type(req, "text/html; charset=utf-8");
        httpd_resp_sendstr_chunk(req, PAGE_HEAD);
        httpd_resp_sendstr_chunk(req, "<h1>Invalid input</h1><p>Check the network name"
#if !CONFIG_BUDDYAI_RELEASE_BUILD
                                      " and the server URL (must start with ws:// or wss://)"
#endif
                                      ".</p><a href='/'>Back</a></main></body></html>");
        httpd_resp_sendstr_chunk(req, NULL);
        return ESP_OK;
    }

    settings_set_wifi(ssid, pass);
    settings_set_server_url(url);   /* release: always "", clearing a URL an older firmware stored */
    ESP_LOGI(TAG, "credentials saved for '%s' (server_url %s)", ssid, url[0] ? url : "<auto>");

    httpd_resp_set_type(req, "text/html; charset=utf-8");
    httpd_resp_sendstr_chunk(req, PAGE_HEAD);
    httpd_resp_sendstr_chunk(req, "<h1>Saved &#10003;</h1><p>The watch restarts and joins <b>");
    send_escaped(req, ssid);
    httpd_resp_sendstr_chunk(req, "</b>.</p></main></body></html>");
    httpd_resp_sendstr_chunk(req, NULL);

    net_emit(NET_EVT_PORTAL_SAVED);
    return ESP_OK;
}

static esp_err_t redirect_any(httpd_req_t *req)
{
    httpd_resp_set_status(req, "302 Found");
    httpd_resp_set_hdr(req, "Location", "http://" PORTAL_IP "/");
    httpd_resp_send(req, NULL, 0);
    return ESP_OK;
}

/* ------------------------------------------------------------------------- */

static void scan_networks(void)
{
    s_scan_count = 0;
    wifi_scan_config_t sc = { .show_hidden = false };
    if (esp_wifi_scan_start(&sc, true) != ESP_OK) {
        return;
    }
    uint16_t n = MAX_SCAN_RESULTS;
    wifi_ap_record_t *recs = calloc(n, sizeof(wifi_ap_record_t));
    if (!recs) {
        esp_wifi_clear_ap_list();
        return;
    }
    if (esp_wifi_scan_get_ap_records(&n, recs) == ESP_OK) {
        for (int i = 0; i < n; i++) {
            const char *name = (const char *)recs[i].ssid;
            if (!name[0]) {
                continue;
            }
            bool dup = false;
            for (int j = 0; j < s_scan_count; j++) {
                if (strcmp(s_scan[j], name) == 0) {
                    dup = true;
                    break;
                }
            }
            if (!dup) {
                strlcpy(s_scan[s_scan_count++], name, sizeof(s_scan[0]));
            }
        }
    }
    free(recs);
    ESP_LOGI(TAG, "scan found %d networks", s_scan_count);
}

const char *net_portal_ssid(void)
{
    if (!s_ap_ssid[0]) {
        uint8_t mac[6] = {0};
        esp_read_mac(mac, ESP_MAC_WIFI_SOFTAP);
        snprintf(s_ap_ssid, sizeof(s_ap_ssid), "ola-%02X%02X", mac[4], mac[5]);
    }
    return s_ap_ssid;
}

bool net_portal_active(void)
{
    return s_active;
}

esp_err_t net_portal_start(void)
{
    if (s_active) {
        return ESP_OK;
    }
    ESP_LOGI(TAG, "starting SoftAP portal '%s'", net_portal_ssid());

    wifi_config_t ap = {0};
    strlcpy((char *)ap.ap.ssid, net_portal_ssid(), sizeof(ap.ap.ssid));
    ap.ap.ssid_len = strlen(net_portal_ssid());
    ap.ap.channel = 1;
    ap.ap.max_connection = 4;
    ap.ap.authmode = WIFI_AUTH_OPEN;

    s_active = true;    /* pauses STA reconnect attempts */
    esp_wifi_disconnect();
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_APSTA), TAG, "mode");
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_AP, &ap), TAG, "ap cfg");
    if (!g_net_wifi_started) {
        ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "start");
        g_net_wifi_started = true;
    }
    esp_wifi_set_ps(WIFI_PS_NONE);
    scan_networks();

    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.uri_match_fn = httpd_uri_match_wildcard;
    cfg.max_uri_handlers = 6;
    cfg.lru_purge_enable = true;
    cfg.stack_size = 6144;
    ESP_RETURN_ON_ERROR(httpd_start(&s_httpd, &cfg), TAG, "httpd");
    const httpd_uri_t root = { .uri = "/", .method = HTTP_GET, .handler = root_get };
    const httpd_uri_t save = { .uri = "/save", .method = HTTP_POST, .handler = save_post };
    const httpd_uri_t any_get = { .uri = "/*", .method = HTTP_GET, .handler = redirect_any };
    httpd_register_uri_handler(s_httpd, &root);
    httpd_register_uri_handler(s_httpd, &save);
    httpd_register_uri_handler(s_httpd, &any_get);

    s_dns_run = true;
    xTaskCreatePinnedToCore(dns_task, "dns_hijack", 3072, NULL, 4, &s_dns_task, 0);

    net_emit(NET_EVT_PORTAL_STARTED);
    return ESP_OK;
}

void net_portal_stop(void)
{
    if (!s_active) {
        return;
    }
    s_dns_run = false;      /* task exits within its 1 s receive timeout */
    if (s_httpd) {
        httpd_stop(s_httpd);
        s_httpd = NULL;
    }
    esp_wifi_set_mode(WIFI_MODE_STA);
    s_active = false;
    esp_wifi_connect();
}
