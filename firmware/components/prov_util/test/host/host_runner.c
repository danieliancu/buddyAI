/*
 * Runs components/prov_util/test/test_prov_util.c on a PC (no watch needed): prov_util is plain C.
 * ESP-IDF's TEST_CASE("name", "[tag]") is mapped to a small registry; assertions are Unity's own.
 * Build + run: firmware/components/prov_util/test/host/run.sh
 */
#include "unity.h"

typedef struct {
    const char *name;
    void (*fn)(void);
} host_case_t;

static host_case_t s_cases[64];
static int s_count;

#define HOST_CAT2(a, b) a##b
#define HOST_CAT(a, b) HOST_CAT2(a, b)
#define TEST_CASE(name_, tags_)                                                       \
    static void HOST_CAT(host_case_, __LINE__)(void);                                 \
    __attribute__((constructor)) static void HOST_CAT(host_reg_, __LINE__)(void)      \
    {                                                                                 \
        s_cases[s_count].name = name_;                                                \
        s_cases[s_count++].fn = HOST_CAT(host_case_, __LINE__);                       \
    }                                                                                 \
    static void HOST_CAT(host_case_, __LINE__)(void)

#include "../test_prov_util.c"

void setUp(void) {}
void tearDown(void) {}

int main(void)
{
    UNITY_BEGIN();
    for (int i = 0; i < s_count; i++) {
        UnityDefaultTestRun(s_cases[i].fn, s_cases[i].name, 0);
    }
    return UNITY_END();
}
