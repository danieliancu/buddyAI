idf_component_register(
    SRCS "ota.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES esp_https_ota app_update esp_http_client esp_partition esp_app_format esp_timer mbedtls nvs_flash
)
