/*
 * Unit tests for diag_codes (ola Diagnostics). On the watch: the unity test app; on a PC: test/host/run.sh.
 */
#include <string.h>
#include "unity.h"
#include "diag_codes.h"

TEST_CASE("ws merge keeps the details whatever the event order", "[diag]")
{
    diag_ws_t error_ev = { 0 };                                     /* WEBSOCKET_EVENT_ERROR: nothing yet */
    diag_ws_t disc_ev = { .err_type = 1, .tls_err = 0x8001, .sock_errno = 104 };
    diag_ws_t a = { 0 }, b = { 0 };
    diag_ws_merge(&a, &error_ev);
    diag_ws_merge(&a, &disc_ev);
    diag_ws_merge(&b, &disc_ev);
    diag_ws_merge(&b, &error_ev);
    TEST_ASSERT_EQUAL_MEMORY(&a, &b, sizeof(a));
    TEST_ASSERT_EQUAL_INT32(0x8001, a.tls_err);
    TEST_ASSERT_EQUAL_INT32(104, a.sock_errno);
}

TEST_CASE("ws merge: newer non-zero values win, zeros never erase", "[diag]")
{
    diag_ws_t w = { .err_type = 1, .close_code = 0 };
    diag_ws_t closed = { .err_type = 4, .close_code = 1012 };
    diag_ws_merge(&w, &closed);
    TEST_ASSERT_EQUAL_INT32(4, w.err_type);
    TEST_ASSERT_EQUAL_INT32(1012, w.close_code);
    TEST_ASSERT_FALSE(diag_ws_empty(&w));
    diag_ws_t empty = { 0 };
    TEST_ASSERT_TRUE(diag_ws_empty(&empty));
}

TEST_CASE("rtc state: sealed is valid, any change or garbage is not", "[diag]")
{
    diag_rtc_t r;
    memset(&r, 0xA5, sizeof(r));                                   /* RTC memory after power-on: garbage */
    TEST_ASSERT_FALSE(diag_rtc_valid(&r));
    memset(&r, 0, sizeof(r));
    r.uptime_s = 4000;
    r.turn_id = 3;
    diag_rtc_set_session(&r, "0123456789abcdef0123456789abcdef");
    diag_rtc_seal(&r);
    TEST_ASSERT_TRUE(diag_rtc_valid(&r));
    TEST_ASSERT_EQUAL_STRING("0123456789abcdef0123456789abcdef", r.session);
    r.uptime_s++;                                                    /* changed without sealing */
    TEST_ASSERT_FALSE(diag_rtc_valid(&r));
    diag_rtc_seal(&r);
    r.magic = 0x6F6C6131u;                                           /* the 0.1.0 layout's magic */
    TEST_ASSERT_FALSE(diag_rtc_valid(&r));
}

TEST_CASE("rtc session: cleared by NULL, long ids truncated and terminated", "[diag]")
{
    diag_rtc_t r = { 0 };
    char longid[80];
    memset(longid, 'f', sizeof(longid) - 1);
    longid[sizeof(longid) - 1] = '\0';
    diag_rtc_set_session(&r, longid);
    TEST_ASSERT_EQUAL_size_t(DIAG_SESSION_LEN - 1, strlen(r.session));
    diag_rtc_set_session(&r, NULL);
    TEST_ASSERT_EQUAL_STRING("", r.session);
    diag_rtc_seal(&r);
    TEST_ASSERT_TRUE(diag_rtc_valid(&r));
}

TEST_CASE("hash is FNV-1a", "[diag]")
{
    TEST_ASSERT_EQUAL_HEX32(0x811C9DC5u, diag_hash("", 0));
    TEST_ASSERT_EQUAL_HEX32(0xE40C292Cu, diag_hash("a", 1));
}
