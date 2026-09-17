idf_component_register(SRCS "main.c" "executor_core.c" "provision.c" "wire_codec.c" INCLUDE_DIRS "."
    REQUIRES esp_driver_gpio esp_driver_uart esp_timer esp_wifi esp_event esp_netif nvs_flash
             esp_http_client esp_websocket_client mbedtls json esp_system efuse)
