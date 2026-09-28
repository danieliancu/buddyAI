idf_component_register(
    SRCS "net_wifi.c" "net_portal.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "."
    REQUIRES esp_netif
    PRIV_REQUIRES esp_wifi esp_event esp_timer esp_http_server lwip settings
)
