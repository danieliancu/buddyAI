idf_component_register(
    SRCS "settings.c"
    INCLUDE_DIRS "include"
    REQUIRES json nvs_flash prov_util
    PRIV_REQUIRES esp_hw_support
)
