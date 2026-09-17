#include "provision.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <sys/time.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/uart.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_timer.h"
#include "cJSON.h"
#include "sdkconfig.h"

static bool string_field(cJSON *root, const char *key, char *out, size_t size, bool required) {
    cJSON *value = cJSON_GetObjectItemCaseSensitive(root, key);
    if (!value) return !required;
    if (!cJSON_IsString(value) || strlen(value->valuestring) >= size) return false;
    strcpy(out, value->valuestring); return true;
}
static bool origin_valid(const char *value) {
    if (strncmp(value, "https://", 8) || !value[8]) return false;
    return !strpbrk(value + 8, "/@?#\\ \t\r\n");
}
bool v8_provision(v8_config *config, nvs_handle_t store) {
    size_t length = sizeof(*config);
    esp_err_t existing = nvs_get_blob(store, "config", config, &length);
    if (existing != ESP_OK && existing != ESP_ERR_NVS_NOT_FOUND) return false;
    if (existing == ESP_OK && (length != sizeof(*config) || config->version != 1)) return false;
    char bound_origin[sizeof(config->origin)], bound_authority[sizeof(config->authority)];
    strcpy(bound_origin, config->origin); strcpy(bound_authority, config->authority);
    /* No RTC trust is assumed. Every boot waits for a physically supplied time
       anchor before TLS. Existing identity can use a time-only provisioning. */
    uart_config_t uart = {.baud_rate = 115200, .data_bits = UART_DATA_8_BITS, .parity = UART_PARITY_DISABLE,
                         .stop_bits = UART_STOP_BITS_1, .flow_ctrl = UART_HW_FLOWCTRL_DISABLE};
    if (uart_param_config(UART_NUM_0, &uart) != ESP_OK || uart_driver_install(UART_NUM_0, 9216, 0, 0, NULL, 0) != ESP_OK) return false;
    static char input[8192];
    size_t used = 0;
    int64_t until = esp_timer_get_time() + 120000000;
    while (esp_timer_get_time() < until) {
        if (gpio_get_level(CONFIG_V8_STOP_GPIO) != 0) { used = 0; vTaskDelay(pdMS_TO_TICKS(20)); continue; }
        char ch;
        if (uart_read_bytes(UART_NUM_0, &ch, 1, pdMS_TO_TICKS(20)) != 1) continue;
        if (ch == '\n') break;
        if (used + 1 >= sizeof(input)) { memset(input, 0, sizeof(input)); return false; }
        input[used++] = ch;
    }
    input[used] = 0;
    cJSON *root = cJSON_Parse(input);
    memset(input, 0, sizeof(input));
    if (!root) return false;
    bool ok = string_field(root, "ssid", config->ssid, sizeof(config->ssid), existing != ESP_OK)
        && string_field(root, "password", config->password, sizeof(config->password), existing != ESP_OK)
        && string_field(root, "origin", config->origin, sizeof(config->origin), existing != ESP_OK)
        && string_field(root, "authorityId", config->authority, sizeof(config->authority), existing != ESP_OK)
        && string_field(root, "ticket", config->ticket, sizeof(config->ticket), !config->credential[0])
        && string_field(root, "ca", config->ca, sizeof(config->ca), false);
    cJSON *time = cJSON_GetObjectItemCaseSensitive(root, "trustedUnixMs");
    ok = ok && cJSON_IsNumber(time) && time->valuedouble >= 1700000000000.0
        && time->valuedouble <= 9007199254740991.0 && time->valuedouble >= config->trusted_unix_ms
        && origin_valid(config->origin);
    if (existing == ESP_OK && config->credential[0]
        && (strcmp(bound_origin, config->origin) || strcmp(bound_authority, config->authority))) ok = false;
    if (ok) {
        config->version = 1;
        config->trusted_unix_ms = (uint64_t)time->valuedouble;
        struct timeval tv = {.tv_sec = config->trusted_unix_ms / 1000, .tv_usec = (config->trusted_unix_ms % 1000) * 1000};
        settimeofday(&tv, NULL);
        ok = nvs_set_blob(store, "config", config, sizeof(*config)) == ESP_OK && nvs_commit(store) == ESP_OK;
    }
    cJSON_Delete(root);
    uart_driver_delete(UART_NUM_0);
    return ok;
}
typedef struct { char data[2048]; size_t used; bool overflow; } response_buffer;
static esp_err_t response(esp_http_client_event_t *event) {
    response_buffer *out = event->user_data;
    if (event->event_id == HTTP_EVENT_ON_DATA) {
        if (event->data_len < 0 || out->used + event->data_len >= sizeof(out->data)) { out->overflow = true; return ESP_FAIL; }
        memcpy(out->data + out->used, event->data, event->data_len); out->used += event->data_len;
    }
    return ESP_OK;
}
bool v8_enroll(v8_config *config, nvs_handle_t store) {
    if (config->credential[0]) return true;
    if (!config->ticket[0] || !origin_valid(config->origin)) return false;
    char url[320]; snprintf(url, sizeof(url), "%s/api/executor/enroll", config->origin);
    response_buffer out = {0};
    esp_http_client_config_t http = {.url = url, .timeout_ms = 10000, .disable_auto_redirect = true,
        .event_handler = response, .user_data = &out, .crt_bundle_attach = config->ca[0] ? NULL : esp_crt_bundle_attach,
        .cert_pem = config->ca[0] ? config->ca : NULL};
    esp_http_client_handle_t client = esp_http_client_init(&http);
    if (!client) return false;
    cJSON *body = cJSON_CreateObject();
    cJSON_AddStringToObject(body, "ticket", config->ticket); cJSON_AddStringToObject(body, "authorityId", config->authority);
    cJSON_AddStringToObject(body, "deviceClass", "esp32");
    char *serialized = cJSON_PrintUnformatted(body);
    esp_http_client_set_method(client, HTTP_METHOD_POST);
    esp_http_client_set_header(client, "Content-Type", "application/json");
    esp_http_client_set_post_field(client, serialized, strlen(serialized));
    bool ok = esp_http_client_perform(client) == ESP_OK && esp_http_client_get_status_code(client) == 200 && !out.overflow;
    esp_http_client_cleanup(client);
    memset(serialized, 0, strlen(serialized)); free(serialized); cJSON_Delete(body);
    cJSON *result = ok ? cJSON_Parse(out.data) : NULL;
    const cJSON *authority = result ? cJSON_GetObjectItemCaseSensitive(result, "authorityId") : NULL;
    ok = cJSON_IsString(authority) && !strcmp(authority->valuestring, config->authority)
        && string_field(result, "credential", config->credential, sizeof(config->credential), true)
        && string_field(result, "deviceId", config->device, sizeof(config->device), true);
    if (ok) {
        memset(config->ticket, 0, sizeof(config->ticket));
        ok = nvs_set_blob(store, "config", config, sizeof(*config)) == ESP_OK && nvs_commit(store) == ESP_OK;
    }
    cJSON_Delete(result); memset(&out, 0, sizeof(out)); return ok;
}
