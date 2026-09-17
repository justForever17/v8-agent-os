#ifndef V8_PROVISION_H
#define V8_PROVISION_H
#include <stdbool.h>
#include <stdint.h>
#include "nvs.h"
typedef struct {
    uint32_t version;
    char ssid[33], password[65], origin[256], authority[193], device[193], credential[128], ticket[128], ca[4096];
    uint64_t trusted_unix_ms;
} v8_config;
/* Physical button + raw non-echo UART; secrets never go to console or argv. */
bool v8_provision(v8_config *config, nvs_handle_t store);
bool v8_enroll(v8_config *config, nvs_handle_t store);
#endif
