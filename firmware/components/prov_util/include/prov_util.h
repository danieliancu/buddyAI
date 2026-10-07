/*
 * ola - Wi-Fi setup helpers without hardware dependencies (unit tested in components/prov_util/test).
 *
 *  - the per-watch setup password, shown on the watch: the WPA2 key of the "ola-XXXX" setup network
 *    and the Security 2 (SRP6a) password of Bluetooth setup
 *  - validation of the home Wi-Fi credentials received from the phone
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define SETUP_PASS_LEN       8
/* No look-alike characters (0/O, 1/I/L): the password is read from the watch and typed by hand. */
#define SETUP_PASS_ALPHABET  "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

/* Fill out[SETUP_PASS_LEN + 1] with a uniformly random password (rejection sampling, no modulo bias).
 * `rnd` returns 32 random bits (esp_random on the watch). */
void setup_pass_generate(uint32_t (*rnd)(void), char out[SETUP_PASS_LEN + 1]);

/* True if `pass` is a well-formed setup password (length and alphabet). */
bool setup_pass_valid(const char *pass);

/* "K7P4M9XQ" -> "K7P4 M9XQ" (out >= SETUP_PASS_LEN + 2). */
void setup_pass_grouped(const char *pass, char *out, size_t len);

typedef enum {
    WIFI_CRED_OK = 0,
    WIFI_CRED_SSID_EMPTY,
    WIFI_CRED_SSID_TOO_LONG,      /* > 32 bytes */
    WIFI_CRED_PASS_TOO_SHORT,     /* WPA needs 8..63 characters (or 64 hex digits) */
    WIFI_CRED_PASS_TOO_LONG,
    WIFI_CRED_PASS_BAD_CHAR,      /* a passphrase is printable ASCII */
} wifi_cred_result_t;

/* Home network credentials: SSID 1..32 bytes (any bytes except NUL); password empty (open network),
 * 8..63 printable ASCII characters, or exactly 64 hex digits (a raw PSK). */
wifi_cred_result_t wifi_cred_validate(const char *ssid, size_t ssid_len, const char *pass, size_t pass_len);

/* The Wi-Fi QR code payload for joining the setup network from a phone camera:
 * WIFI:T:WPA;S:<ssid>;P:<pass>;; with \ ; , : " escaped. Returns false if it does not fit. */
bool wifi_qr_payload(const char *ssid, const char *pass, char *out, size_t len);

#ifdef __cplusplus
}
#endif
