/*
 * Unit tests for prov_util (Unity). On the watch: build a test app with this component
 * (idf.py -T prov_util in a unit-test project), or compile prov_util.c + this file with any host C
 * compiler and Unity (see firmware/README.md, "Tests").
 */
#include <stdio.h>
#include <string.h>
#include "unity.h"
#include "prov_util.h"

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

TEST_CASE("saved wifi: newest first, a full list drops the oldest", "[prov_util]")
{
    wifi_saved_list_t l = {0};
    char name[8];
    for (int i = 0; i < WIFI_SAVED_MAX + 2; i++) {
        snprintf(name, sizeof(name), "net%d", i);
        wifi_list_add(&l, name, "password1", true);
    }
    TEST_ASSERT_EQUAL(WIFI_SAVED_MAX, l.count);
    TEST_ASSERT_EQUAL_STRING("net6", l.net[0].ssid);
    TEST_ASSERT_EQUAL_STRING("net2", l.net[WIFI_SAVED_MAX - 1].ssid);
    /* saving a known network again moves it to the front without a duplicate */
    wifi_list_add(&l, "net3", "newpass12", true);
    TEST_ASSERT_EQUAL(WIFI_SAVED_MAX, l.count);
    TEST_ASSERT_EQUAL_STRING("net3", l.net[0].ssid);
    TEST_ASSERT_EQUAL_STRING("newpass12", l.net[0].pass);
    TEST_ASSERT_EQUAL_STRING("net6", l.net[1].ssid);
}

TEST_CASE("saved wifi: an untried password keeps the working one as fallback", "[prov_util]")
{
    wifi_saved_list_t l = {0};
    wifi_list_add(&l, "Home", "goodpass1", true);
    wifi_list_add(&l, "Office", "office123", true);
    wifi_list_add(&l, "Home", "typo-pass", false);
    TEST_ASSERT_EQUAL(3, l.count);
    TEST_ASSERT_EQUAL_STRING("typo-pass", l.net[0].pass);
    TEST_ASSERT_FALSE(l.net[0].verified);
    TEST_ASSERT_EQUAL_STRING("goodpass1", l.net[2].pass);
    /* a second untried password replaces the first untried one only */
    wifi_list_add(&l, "Home", "typo-2222", false);
    TEST_ASSERT_EQUAL(3, l.count);
    TEST_ASSERT_EQUAL_STRING("typo-2222", l.net[0].pass);
    /* it fails: removed, the working password is still there */
    TEST_ASSERT_TRUE(wifi_list_remove(&l, "Home", "typo-2222"));
    TEST_ASSERT_EQUAL(2, l.count);
    TEST_ASSERT_EQUAL_STRING("Office", l.net[0].ssid);
    TEST_ASSERT_FALSE(wifi_list_remove(&l, "Home", "nope-nope"));
}

TEST_CASE("saved wifi: joining verifies and promotes", "[prov_util]")
{
    wifi_saved_list_t l = {0};
    wifi_list_add(&l, "Home", "oldpass11", true);
    wifi_list_add(&l, "Office", "office123", true);
    wifi_list_add(&l, "Home", "newpass11", false);
    TEST_ASSERT_TRUE(wifi_list_joined(&l, "Home", "newpass11"));
    TEST_ASSERT_EQUAL(2, l.count);                     /* the old Home password is gone */
    TEST_ASSERT_EQUAL_STRING("Home", l.net[0].ssid);
    TEST_ASSERT_EQUAL_STRING("newpass11", l.net[0].pass);
    TEST_ASSERT_TRUE(l.net[0].verified);
    TEST_ASSERT_FALSE(wifi_list_joined(&l, "Home", "newpass11"));   /* nothing to write */
    TEST_ASSERT_TRUE(wifi_list_joined(&l, "Office", "office123"));
    TEST_ASSERT_EQUAL_STRING("Office", l.net[0].ssid);
    TEST_ASSERT_TRUE(wifi_list_joined(&l, "Cafe", ""));             /* unknown: added */
    TEST_ASSERT_EQUAL(3, l.count);
}

TEST_CASE("saved wifi: pick the strongest, the recent one wins a near tie", "[prov_util]")
{
    wifi_saved_list_t l = {0};
    wifi_list_add(&l, "Office", "office123", true);
    wifi_list_add(&l, "Home", "goodpass1", true);       /* Home is the most recent */
    const char *ssids[] = { "Neighbour", "Office", "Home", "Office" };
    int8_t rssi[] = { -30, -50, -70, -45 };
    TEST_ASSERT_EQUAL(1, wifi_list_pick(&l, ssids, rssi, 4, 8));    /* Office, 25 dB stronger */
    rssi[2] = -52;
    TEST_ASSERT_EQUAL(0, wifi_list_pick(&l, ssids, rssi, 4, 8));    /* Home, only 7 dB weaker */
    const char *other[] = { "Neighbour" };
    TEST_ASSERT_EQUAL(-1, wifi_list_pick(&l, other, rssi, 1, 8));
    TEST_ASSERT_EQUAL(-1, wifi_list_pick(&l, ssids, rssi, 0, 8));
    /* a network just set up (not tried yet) is tried first while in range, even if weaker */
    wifi_list_add(&l, "Cafe", "cafe12345", false);
    const char *with_cafe[] = { "Office", "Cafe" };
    int8_t cafe_rssi[] = { -40, -80 };
    TEST_ASSERT_EQUAL(0, wifi_list_pick(&l, with_cafe, cafe_rssi, 2, 8));
    TEST_ASSERT_EQUAL(2, wifi_list_pick(&l, with_cafe, cafe_rssi, 1, 8));   /* out of range: Office */
}

TEST_CASE("saved wifi: sanitize drops broken entries", "[prov_util]")
{
    wifi_saved_list_t l = {0};
    wifi_list_add(&l, "Home", "goodpass1", true);
    wifi_list_add(&l, "Office", "office123", true);
    memset(l.net[0].ssid, 'x', sizeof(l.net[0].ssid));   /* no NUL */
    wifi_list_sanitize(&l);
    TEST_ASSERT_EQUAL(1, l.count);
    TEST_ASSERT_EQUAL_STRING("Home", l.net[0].ssid);
    l.count = 99;
    wifi_list_sanitize(&l);
    TEST_ASSERT_EQUAL(0, l.count);
}
