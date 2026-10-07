idf_component_register(
    SRCS "ble_prov.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES protocomm wifi_provisioning bt esp_wifi esp_netif esp_timer heap json net settings prov_util
)
