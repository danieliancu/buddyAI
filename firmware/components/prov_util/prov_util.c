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
