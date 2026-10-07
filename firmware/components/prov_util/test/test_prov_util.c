/*
 * Unit tests for prov_util (Unity). On the watch: build a test app with this component
 * (idf.py -T prov_util in a unit-test project), or compile prov_util.c + this file with any host C
 * compiler and Unity (see firmware/README.md, "Tests").
 */
#include <string.h>
#include "unity.h"
#include "prov_util.h"

static uint32_t s_seq;
static uint32_t counter_rnd(void)
{
    /* deterministic, covers every byte value */
    uint32_t b = s_seq++ & 0xFF;
    return b | (b << 8) | (b << 16) | (b << 24);
}

static uint32_t high_rnd(void)
{
    return 0xFFFFFFFFu;  /* every byte is rejected ... */
}

static uint32_t s_calls;
static uint32_t mostly_high_rnd(void)
{
    return (s_calls++ % 2) ? 0x05050505u : high_rnd();  /* ... until a usable byte comes */
}

TEST_CASE("setup password: length, alphabet, valid", "[prov_util]")
{
    char p[SETUP_PASS_LEN + 1];
    s_seq = 0;
    for (int i = 0; i < 64; i++) {
        setup_pass_generate(counter_rnd, p);
        TEST_ASSERT_EQUAL(SETUP_PASS_LEN, strlen(p));
        TEST_ASSERT_TRUE(setup_pass_valid(p));
        for (int j = 0; j < SETUP_PASS_LEN; j++) {
            TEST_ASSERT_NOT_NULL(strchr(SETUP_PASS_ALPHABET, p[j]));
            TEST_ASSERT_NULL(strchr("0O1IL", p[j]));
        }
    }
}

TEST_CASE("setup password: biased bytes are rejected", "[prov_util]")
{
    char p[SETUP_PASS_LEN + 1];
    s_calls = 0;
    setup_pass_generate(mostly_high_rnd, p);
    TEST_ASSERT_EQUAL_STRING("FFFFFFFF", p);  /* 0x05 -> alphabet[5] */
}

TEST_CASE("setup password: consecutive passwords differ", "[prov_util]")
{
    char a[SETUP_PASS_LEN + 1], b[SETUP_PASS_LEN + 1];
    s_seq = 0;
    setup_pass_generate(counter_rnd, a);
    setup_pass_generate(counter_rnd, b);
    TEST_ASSERT_NOT_EQUAL(0, strcmp(a, b));
}

TEST_CASE("setup password: validation and grouping", "[prov_util]")
{
    char g[12];
    TEST_ASSERT_FALSE(setup_pass_valid("K7P4M9X"));
    TEST_ASSERT_FALSE(setup_pass_valid("K7P4M9X0"));  /* 0 is not in the alphabet */
    TEST_ASSERT_FALSE(setup_pass_valid(NULL));
    setup_pass_grouped("K7P4M9XQ", g, sizeof(g));
    TEST_ASSERT_EQUAL_STRING("K7P4 M9XQ", g);
}

TEST_CASE("wifi credentials", "[prov_util]")
{
    const char *hex64 = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    char long_ssid[40];
    memset(long_ssid, 'a', 33);
    TEST_ASSERT_EQUAL(WIFI_CRED_OK, wifi_cred_validate("Home", 4, "correct horse", 13));
    TEST_ASSERT_EQUAL(WIFI_CRED_OK, wifi_cred_validate("Cafe", 4, "", 0));  /* open network */
    TEST_ASSERT_EQUAL(WIFI_CRED_OK, wifi_cred_validate("Home", 4, hex64, 64));
    TEST_ASSERT_EQUAL(WIFI_CRED_SSID_EMPTY, wifi_cred_validate("", 0, "password1", 9));
    TEST_ASSERT_EQUAL(WIFI_CRED_SSID_TOO_LONG, wifi_cred_validate(long_ssid, 33, "password1", 9));
    TEST_ASSERT_EQUAL(WIFI_CRED_OK, wifi_cred_validate(long_ssid, 32, "password1", 9));
    TEST_ASSERT_EQUAL(WIFI_CRED_PASS_TOO_SHORT, wifi_cred_validate("Home", 4, "short", 5));
    TEST_ASSERT_EQUAL(WIFI_CRED_PASS_TOO_LONG, wifi_cred_validate("Home", 4,
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdeZ", 64));
    TEST_ASSERT_EQUAL(WIFI_CRED_PASS_BAD_CHAR, wifi_cred_validate("Home", 4, "pass\tword", 9));
}

TEST_CASE("wifi QR payload escapes special characters", "[prov_util]")
{
    char out[96];
    TEST_ASSERT_TRUE(wifi_qr_payload("ola-1A2B", "K7P4M9XQ", out, sizeof(out)));
    TEST_ASSERT_EQUAL_STRING("WIFI:T:WPA;S:ola-1A2B;P:K7P4M9XQ;;", out);
    TEST_ASSERT_TRUE(wifi_qr_payload("a;b", "c:d", out, sizeof(out)));
    TEST_ASSERT_EQUAL_STRING("WIFI:T:WPA;S:a\\;b;P:c\\:d;;", out);
    TEST_ASSERT_FALSE(wifi_qr_payload("ola-1A2B", "K7P4M9XQ", out, 20));
}
