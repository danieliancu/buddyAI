idf_component_register(
    SRCS "protocol_client.c" "diag_codes.c"
    INCLUDE_DIRS "include"
    REQUIRES json
    PRIV_REQUIRES audio net ota settings esp_timer mbedtls
)
