/*
 * ola - Bluetooth Low Energy Wi-Fi setup for Android phones (Chrome Web Bluetooth in the ola account).
 *
 * ESP-IDF protocomm over BLE (NimBLE), security scheme 2 (SRP6a + AES-256-GCM) with the watch's setup
 * password as the SRP password: the phone proves it knows the password shown on the watch, and the
 * Wi-Fi password travels encrypted. No fallback to an unauthenticated scheme. Protocol:
 * protocol/BLE_PROVISIONING.md. It runs together with the "ola-XXXX" setup network (iPhone + recovery)
 * and only while the watch is in Wi-Fi setup.
 */
#pragma once

#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Called (from a worker task) once the watch joined the new network and saved it: the app restarts
 * a few seconds later, after the phone has read the result. */
typedef void (*ble_prov_done_cb_t)(void);

/* Advertise as `device_name` ("ola-XXXX") and accept setup sessions secured with `setup_pass`. */
esp_err_t ble_prov_start(const char *device_name, const char *setup_pass, ble_prov_done_cb_t done);
void      ble_prov_stop(void);
bool      ble_prov_active(void);

#ifdef __cplusplus
}
#endif
