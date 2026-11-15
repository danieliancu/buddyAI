idf_component_register(
    SRCS "net_wifi.c" "net_scan.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "."
    REQUIRES esp_netif
    PRIV_REQUIRES esp_wifi esp_event esp_timer settings
)
