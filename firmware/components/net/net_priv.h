/* ola - net component internals */
#pragma once

#include <stdbool.h>
#include "esp_netif.h"
#include "net.h"

extern esp_netif_t *g_net_sta_netif;
extern esp_netif_t *g_net_ap_netif;
extern bool g_net_wifi_started;

void net_emit(net_event_t ev);
