/* ola - net component internals */
#pragma once

#include <stdbool.h>
#include "esp_netif.h"
#include "net.h"

extern esp_netif_t *g_net_sta_netif;
extern bool g_net_wifi_started;

void net_emit(net_event_t ev);
void net_scan_done(void);         /* WIFI_EVENT_SCAN_DONE of an asynchronous scan */
bool net_wifi_connecting(void);   /* a connection attempt is running: no scan now */
