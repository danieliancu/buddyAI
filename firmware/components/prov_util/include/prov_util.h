/*
 * ola - Wi-Fi setup helpers without hardware dependencies (unit tested in components/prov_util/test).
 *
 *  - the per-watch setup password, shown on the watch: the WPA2 key of the "ola-XXXX" setup network
 *    and the Security 2 (SRP6a) password of Bluetooth setup
 *  - validation of the home Wi-Fi credentials received from the phone
 *  - the list of saved Wi-Fi networks and the choice among them after a scan
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

/* ---- saved Wi-Fi networks ----
 * Most recently joined first. A network saved by the setup page (not tried yet) is "unverified": it is
 * tried first, but it does not replace a working password for the same network until it connects, so a
 * mistyped password cannot lock the watch out of a network it already knew. */
#define WIFI_SAVED_MAX       5
#define WIFI_SAVED_SSID_MAX  33
#define WIFI_SAVED_PASS_MAX  65

typedef struct {
    char ssid[WIFI_SAVED_SSID_MAX];
    char pass[WIFI_SAVED_PASS_MAX];
    bool verified;              /* has connected at least once */
} wifi_saved_t;

typedef struct {
    wifi_saved_t net[WIFI_SAVED_MAX];
    int          count;
} wifi_saved_list_t;

/* Put a network at the front. A verified one replaces every entry with that SSID; an unverified one
 * replaces only unverified entries with that SSID. When the list is full the oldest entry goes. */
void wifi_list_add(wifi_saved_list_t *l, const char *ssid, const char *pass, bool verified);
/* The watch joined `ssid` with `pass`: mark it verified and move it to the front (other entries with that
 * SSID go). Returns false if nothing changed (already first and verified): no flash write needed. */
bool wifi_list_joined(wifi_saved_list_t *l, const char *ssid, const char *pass);
/* Remove the entry with exactly this SSID and password. Returns true if one was removed. */
bool wifi_list_remove(wifi_saved_list_t *l, const char *ssid, const char *pass);
/* Keep only well-formed entries (NUL-terminated, non-empty SSID), e.g. after reading flash. */
void wifi_list_sanitize(wifi_saved_list_t *l);

/* Choose which saved network to join from a scan (`n` access points: SSIDs and RSSI in dBm, any order,
 * duplicates allowed). An unverified network in range comes first (it was just set up); otherwise the
 * strongest saved network in range wins, and one used more recently wins over it if it is at most `tie_db`
 * weaker. Returns the index in `l`, or -1 if no saved network was seen. */
int wifi_list_pick(const wifi_saved_list_t *l, const char *const *ssids, const int8_t *rssi, int n, int tie_db);

#ifdef __cplusplus
}
#endif
