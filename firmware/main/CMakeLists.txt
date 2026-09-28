idf_component_register(
    SRCS "app_main.c"
    INCLUDE_DIRS "."
    REQUIRES board audio net protocol_client settings ui ota
             nvs_flash esp_event esp_timer esp_system app_update
)
