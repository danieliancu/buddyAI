/*
 * ola Diagnostics helpers, see diag_codes.h
 */
#include "diag_codes.h"

#include <string.h>

void diag_ws_merge(diag_ws_t *dst, const diag_ws_t *src)
{
    if (src->err_type) {
        dst->err_type = src->err_type;
    }
    if (src->tls_err) {
        dst->tls_err = src->tls_err;
    }
    if (src->tls_stack) {
        dst->tls_stack = src->tls_stack;
    }
    if (src->sock_errno) {
        dst->sock_errno = src->sock_errno;
    }
    if (src->hs_status) {
        dst->hs_status = src->hs_status;
    }
    if (src->close_code) {
        dst->close_code = src->close_code;
    }
}

bool diag_ws_empty(const diag_ws_t *w)
{
    return !w->err_type && !w->tls_err && !w->tls_stack && !w->sock_errno && !w->hs_status && !w->close_code;
}

uint32_t diag_hash(const void *data, size_t len)
{
    const uint8_t *p = data;
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < len; i++) {
        h ^= p[i];
        h *= 16777619u;
    }
    return h;
}

void diag_rtc_seal(diag_rtc_t *r)
{
    r->magic = DIAG_RTC_MAGIC;
    r->session[DIAG_SESSION_LEN - 1] = '\0';
    r->sum = diag_hash(r, offsetof(diag_rtc_t, sum));
}

bool diag_rtc_valid(const diag_rtc_t *r)
{
    return r->magic == DIAG_RTC_MAGIC && r->sum == diag_hash(r, offsetof(diag_rtc_t, sum))
           && memchr(r->session, '\0', DIAG_SESSION_LEN) != NULL;
}

void diag_rtc_set_session(diag_rtc_t *r, const char *session)
{
    memset(r->session, 0, DIAG_SESSION_LEN);
    if (session) {
        size_t n = strnlen(session, DIAG_SESSION_LEN - 1);
        memcpy(r->session, session, n);
    }
}
