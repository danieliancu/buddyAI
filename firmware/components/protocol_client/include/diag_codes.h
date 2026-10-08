/*
 * ola Diagnostics - plain-C helpers for the connection diagnostics the watch reports in its hello
 * (protocol/PROTOCOL.md section 3.1 "link.ws"). No ESP-IDF dependencies: host-tested
 * (components/protocol_client/test/host/run.sh).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* The esp_websocket_client error details of one connection (0 = not reported). */
typedef struct {
    int32_t err_type;   /* esp_websocket_error_type_t: 1 TCP transport, 2 pong timeout, 3 handshake, 4 server close */
    int32_t tls_err;    /* esp_tls_last_esp_err (ESP_ERR_ESP_TLS_* / ESP_ERR_MBEDTLS_*) */
    int32_t tls_stack;  /* esp_tls_stack_err (mbedTLS error code) */
    int32_t sock_errno; /* esp_transport_sock_errno */
    int32_t hs_status;  /* esp_ws_handshake_status_code (HTTP status of the upgrade request) */
    int32_t close_code; /* RFC 6455 close code received from the server */
} diag_ws_t;

/* esp_websocket_client sends ERROR (no details yet) and then DISCONNECTED (with the TLS / socket details),
 * or CLOSED: each non-zero field of `src` overwrites the one in `dst`, so the details survive whatever
 * order the events arrive in. */
void diag_ws_merge(diag_ws_t *dst, const diag_ws_t *src);
bool diag_ws_empty(const diag_ws_t *w);

/* State kept in RTC memory across a crash / watchdog / software restart (not a power loss). Sealed with
 * a checksum: anything left from another firmware version or garbage after power-on is ignored. */
#define DIAG_SESSION_LEN 36  /* 32 hex chars + NUL, padded so the struct has no holes */
typedef struct {
    uint32_t magic;
    uint32_t uptime_s;                  /* seconds since boot, updated by the protocol task */
    uint32_t min_heap;                  /* esp_get_minimum_free_heap_size() of this boot */
    uint32_t turn_id;                   /* turn running now, 0 = none */
    char     session[DIAG_SESSION_LEN]; /* server session id while connected, "" = none */
    uint32_t sum;
} diag_rtc_t;

#define DIAG_RTC_MAGIC 0x6F6C6132u

uint32_t diag_hash(const void *data, size_t len);  /* FNV-1a 32 */
void diag_rtc_seal(diag_rtc_t *r);
bool diag_rtc_valid(const diag_rtc_t *r);
void diag_rtc_set_session(diag_rtc_t *r, const char *session);  /* NULL / "" clears; truncates safely */

#ifdef __cplusplus
}
#endif
