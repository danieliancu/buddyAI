idf_component_register(
    SRCS "app_main.c" "debug_snap.c"
    INCLUDE_DIRS "."
    REQUIRES board audio net ble_prov protocol_client settings ui ota esp_driver_usb_serial_jtag
             nvs_flash esp_event esp_http_server lvgl__lvgl esp_timer esp_system app_update bootloader_support
)
