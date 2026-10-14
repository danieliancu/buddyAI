/*
 * ola - Wi-Fi setup helpers (pure C, see prov_util.h).
 */
#include <string.h>
#include "prov_util.h"

#define ALPHABET_LEN (sizeof(SETUP_PASS_ALPHABET) - 1)

void setup_pass_generate(uint32_t (*rnd)(void), char out[SETUP_PASS_LEN + 1])
{
    /* Bytes >= 248 (= 8 * 31) are discarded so that every character is equally likely. */
    const uint32_t limit = 256 - (256 % ALPHABET_LEN);
    int n = 0;
    while (n < SETUP_PASS_LEN) {
        uint32_t r = rnd();
        for (int i = 0; i < 4 && n < SETUP_PASS_LEN; i++, r >>= 8) {
            uint32_t b = r & 0xFF;
            if (b < limit) {
                out[n++] = SETUP_PASS_ALPHABET[b % ALPHABET_LEN];
            }
        }
    }
    out[SETUP_PASS_LEN] = '\0';
}

bool setup_pass_valid(const char *pass)
{
    if (!pass || strlen(pass) != SETUP_PASS_LEN) {
        return false;
    }
    for (int i = 0; i < SETUP_PASS_LEN; i++) {
        if (!strchr(SETUP_PASS_ALPHABET, pass[i])) {
            return false;
        }
    }
    return true;
}

void setup_pass_grouped(const char *pass, char *out, size_t len)
{
    if (!out || len == 0) {
        return;
    }
    if (!pass || strlen(pass) != SETUP_PASS_LEN || len < SETUP_PASS_LEN + 2) {
        out[0] = '\0';
        return;
    }
    memcpy(out, pass, 4);
    out[4] = ' ';
    memcpy(out + 5, pass + 4, 4);
    out[9] = '\0';
}

static bool is_hex(char c)
{
    return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F');
}

wifi_cred_result_t wifi_cred_validate(const char *ssid, size_t ssid_len, const char *pass, size_t pass_len)
{
    if (!ssid || ssid_len == 0 || memchr(ssid, '\0', ssid_len)) {
        return ssid && ssid_len > 0 && ssid[0] != '\0' ? WIFI_CRED_SSID_TOO_LONG : WIFI_CRED_SSID_EMPTY;
    }
    if (ssid_len > 32) {
        return WIFI_CRED_SSID_TOO_LONG;
    }
    if (!pass || pass_len == 0) {
        return WIFI_CRED_OK;  /* open network */
    }
    if (pass_len == 64) {
        for (size_t i = 0; i < 64; i++) {
            if (!is_hex(pass[i])) {
                return WIFI_CRED_PASS_TOO_LONG;  /* 64 characters are only allowed as a hex PSK */
            }
        }
        return WIFI_CRED_OK;
    }
    if (pass_len < 8) {
        return WIFI_CRED_PASS_TOO_SHORT;
    }
    if (pass_len > 63) {
        return WIFI_CRED_PASS_TOO_LONG;
    }
    for (size_t i = 0; i < pass_len; i++) {
        unsigned char c = (unsigned char)pass[i];
        if (c < 0x20 || c > 0x7E) {
            return WIFI_CRED_PASS_BAD_CHAR;
        }
    }
    return WIFI_CRED_OK;
}

static bool append_escaped(char *out, size_t len, size_t *o, const char *s)
{
    for (; *s; s++) {
        bool esc = strchr("\\;,:\"", *s) != NULL;
        if (*o + (esc ? 2 : 1) >= len) {
            return false;
        }
        if (esc) {
            out[(*o)++] = '\\';
        }
        out[(*o)++] = *s;
    }
    return true;
}

bool wifi_qr_payload(const char *ssid, const char *pass, char *out, size_t len)
{
    if (!ssid || !pass || !out || len == 0) {
        return false;
    }
    size_t o = 0;
    const char *head = "WIFI:T:WPA;S:";
    if (strlen(head) >= len) {
        return false;
    }
    memcpy(out, head, strlen(head));
    o = strlen(head);
    if (!append_escaped(out, len, &o, ssid) || o + 3 >= len) {
        return false;
    }
    memcpy(out + o, ";P:", 3);
    o += 3;
    if (!append_escaped(out, len, &o, pass) || o + 3 > len - 1) {
        return false;
    }
    memcpy(out + o, ";;", 2);
    o += 2;
    out[o] = '\0';
    return true;
}

/* ---- saved Wi-Fi networks ---- */

#define RSSI_NOT_SEEN   (-1000)

static void list_remove_at(wifi_saved_list_t *l, int i)
{
    memmove(&l->net[i], &l->net[i + 1], sizeof(l->net[0]) * (size_t)(l->count - i - 1));
    l->count--;
    memset(&l->net[l->count], 0, sizeof(l->net[0]));
}

static void list_insert_front(wifi_saved_list_t *l, const wifi_saved_t *e)
{
    if (l->count == WIFI_SAVED_MAX) {
        l->count--;     /* the oldest goes */
    }
    memmove(&l->net[1], &l->net[0], sizeof(l->net[0]) * (size_t)l->count);
    l->net[0] = *e;
    l->count++;
}

void wifi_list_add(wifi_saved_list_t *l, const char *ssid, const char *pass, bool verified)
{
    if (!ssid || !ssid[0]) {
        return;
    }
    for (int i = l->count - 1; i >= 0; i--) {
        if (strcmp(l->net[i].ssid, ssid) == 0 && (verified || !l->net[i].verified)) {
            list_remove_at(l, i);
        }
    }
    wifi_saved_t e;
    memset(&e, 0, sizeof(e));
    strncpy(e.ssid, ssid, sizeof(e.ssid) - 1);
    strncpy(e.pass, pass ? pass : "", sizeof(e.pass) - 1);
    e.verified = verified;
    list_insert_front(l, &e);
    memset(&e, 0, sizeof(e));
}

bool wifi_list_joined(wifi_saved_list_t *l, const char *ssid, const char *pass)
{
    if (!ssid || !ssid[0]) {
        return false;
    }
    pass = pass ? pass : "";
    if (l->count > 0 && l->net[0].verified && strcmp(l->net[0].ssid, ssid) == 0 &&
        strcmp(l->net[0].pass, pass) == 0) {
        bool dup = false;
        for (int i = 1; i < l->count; i++) {
            dup |= strcmp(l->net[i].ssid, ssid) == 0;
        }
        if (!dup) {
            return false;
        }
    }
    wifi_list_add(l, ssid, pass, true);
    return true;
}

bool wifi_list_remove(wifi_saved_list_t *l, const char *ssid, const char *pass)
{
    pass = pass ? pass : "";
    for (int i = 0; i < l->count; i++) {
        if (strcmp(l->net[i].ssid, ssid) == 0 && strcmp(l->net[i].pass, pass) == 0) {
            list_remove_at(l, i);
            return true;
        }
    }
    return false;
}

void wifi_list_sanitize(wifi_saved_list_t *l)
{
    if (l->count < 0 || l->count > WIFI_SAVED_MAX) {
        l->count = 0;
    }
    for (int i = l->count - 1; i >= 0; i--) {
        wifi_saved_t *e = &l->net[i];
        if (!memchr(e->ssid, '\0', sizeof(e->ssid)) || !memchr(e->pass, '\0', sizeof(e->pass)) || !e->ssid[0]) {
            list_remove_at(l, i);
        }
    }
}

int wifi_list_pick(const wifi_saved_list_t *l, const char *const *ssids, const int8_t *rssi, int n, int tie_db)
{
    int best[WIFI_SAVED_MAX];
    int strongest = RSSI_NOT_SEEN;
    for (int i = 0; i < l->count; i++) {
        best[i] = RSSI_NOT_SEEN;
        for (int j = 0; j < n; j++) {
            if (ssids[j] && strcmp(ssids[j], l->net[i].ssid) == 0 && rssi[j] > best[i]) {
                best[i] = rssi[j];
            }
        }
        if (best[i] > strongest) {
            strongest = best[i];
        }
    }
    for (int i = 0; i < l->count; i++) {
        if (!l->net[i].verified && best[i] != RSSI_NOT_SEEN) {
            return i;   /* just set up and in range: try it first */
        }
    }
    for (int i = 0; strongest != RSSI_NOT_SEEN && i < l->count; i++) {
        if (best[i] != RSSI_NOT_SEEN && best[i] >= strongest - tie_db) {
            return i;   /* the most recently used of the strong ones */
        }
    }
    return -1;
}
