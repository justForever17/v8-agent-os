/* ESP32-C3 low-voltage bench endpoint. No remote scripts, raw pins or LLM. */
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <math.h>
#include <stdatomic.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/gpio.h"
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_random.h"
#include "esp_efuse.h"
#include "esp_log.h"
#include "esp_crt_bundle.h"
#include "esp_websocket_client.h"
#include "nvs_flash.h"
#include "mbedtls/sha256.h"
#include "cJSON.h"
#include "executor_core.h"
#include "provision.h"
#include "wire_codec.h"

#define FRAME_BYTES 16384
static vx_executor executor;
static v8_config config;
static nvs_handle_t store;
static esp_websocket_client_handle_t socket_client;
static QueueHandle_t inbound, outbound;
static atomic_bool transport_online, stop_requested, arm_requested;
static esp_timer_handle_t output_timer;
static portMUX_TYPE output_lock = portMUX_INITIALIZER_UNLOCKED;
static char receive_frame[FRAME_BYTES + 1];
static size_t received_bytes;
static uint64_t now_ms(void) { return esp_timer_get_time() / 1000; }
static void random_id(char *out) {
    uint8_t bytes[16]; esp_fill_random(bytes, sizeof(bytes));
    for (unsigned i = 0; i < sizeof(bytes); ++i) sprintf(out + i * 2, "%02x", bytes[i]);
}
static void safe_output(void *unused) {
    (void)unused;
    portENTER_CRITICAL(&output_lock);
    gpio_set_level(CONFIG_V8_OUTPUT_GPIO, 0);
    portEXIT_CRITICAL(&output_lock);
}
static bool write_output(void *unused, bool level) {
    (void)unused;
    esp_timer_stop(output_timer);
    portENTER_CRITICAL(&output_lock);
    bool allowed = !level || (!atomic_load(&stop_requested) && atomic_load(&transport_online));
    esp_err_t result = gpio_set_level(CONFIG_V8_OUTPUT_GPIO, level && allowed);
    portEXIT_CRITICAL(&output_lock);
    if (level && allowed) {
        uint64_t mono = now_ms();
        uint64_t hold = executor.output_until_mono > mono ? executor.output_until_mono - mono : 0;
        if (!hold || esp_timer_start_once(output_timer, hold * 1000) != ESP_OK) { safe_output(NULL); return false; }
    }
    return result == ESP_OK && allowed;
}
static bool read_output(void *unused) { (void)unused; return gpio_get_level(CONFIG_V8_OUTPUT_GPIO) != 0; }
static bool read_sensor(void *unused) { (void)unused; return gpio_get_level(CONFIG_V8_SENSOR_GPIO) != 0; }
static bool persist(void *unused, const vx_journal *journal) {
    (void)unused;
    return nvs_set_blob(store, "journal", journal, sizeof(*journal)) == ESP_OK && nvs_commit(store) == ESP_OK;
}
static bool send_json(cJSON *json) {
    char *text = cJSON_PrintUnformatted(json);
    cJSON_Delete(json);
    if (!text) return false;
    if (strlen(text) > FRAME_BYTES || xQueueSend(outbound, &text, 0) != pdTRUE) { free(text); return false; }
    return true;
}
static void identity_fields(cJSON *json, const vx_command *command) {
    cJSON_AddStringToObject(json, "authorityId", command->authority);
    cJSON_AddStringToObject(json, "deviceId", command->device);
    cJSON_AddStringToObject(json, "bootId", command->boot);
    cJSON_AddStringToObject(json, "controlSessionId", command->session);
}
static void receipt(void *unused, const vx_record *record) {
    (void)unused;
    const vx_command *command = &record->command;
    cJSON *json = cJSON_CreateObject();
    cJSON_AddStringToObject(json, "type", "receipt"); cJSON_AddNumberToObject(json, "protocolVersion", 1);
    identity_fields(json, command);
    cJSON_AddStringToObject(json, "commandId", command->id); cJSON_AddStringToObject(json, "commandDigest", command->digest);
    cJSON_AddNumberToObject(json, "leaseEpoch", command->epoch); cJSON_AddNumberToObject(json, "grantRevision", command->grant);
    cJSON_AddStringToObject(json, "status", vx_status_name(record->status));
    cJSON_AddNumberToObject(json, "receiptSeq", record->seq); cJSON_AddNumberToObject(json, "deviceMonotonicMs", record->mono_ms);
    if (record->status == VX_SUCCEEDED) {
        cJSON *observation = cJSON_AddObjectToObject(json, "observation");
        identity_fields(observation, command);
        const char *resource = command->capability == VX_SET ? "logic.led" : command->capability == VX_SENSOR ? "sensor.input" : "device";
        cJSON_AddStringToObject(observation, "resourceId", resource);
        cJSON_AddNumberToObject(observation, "resourceRevision", record->resource_revision);
        cJSON_AddBoolToObject(observation, "value", record->readback);
        cJSON_AddStringToObject(observation, "unit", "digital_level");
        cJSON_AddStringToObject(observation, "quality", "gpio_readback_only");
        cJSON_AddStringToObject(observation, "physicalEffect", "unverified");
        cJSON_AddNumberToObject(observation, "observedUnixMs", record->observed_unix_ms);
    }
    send_json(json); /* A dropped network receipt remains queryable in NVS. */
}
static void handle_frame(char *text) {
    cJSON *root = parse_frame(text);
    if (!root) { atomic_store(&stop_requested, true); return; }
    const char *type = string(root, "type");
    if (!strcmp(type, "session") || !strcmp(type, "lease")) {
        uint64_t epoch, grant, server, until, version;
        bool ok = number(root, "protocolVersion", &version) && version == 1
            && !strcmp(string(root, "deviceId"), executor.device) && !strcmp(string(root, "authorityId"), executor.authority)
            && !strcmp(string(root, "bootId"), executor.boot) && !strcmp(string(root, "controlSessionId"), executor.session)
            && number(root, "leaseEpoch", &epoch) && number(root, "grantRevision", &grant)
            && number(root, "serverUnixMs", &server) && number(root, "leaseExpiresUnixMs", &until);
        unsigned allowed = 0;
        cJSON *grants = cJSON_GetObjectItemCaseSensitive(root, "grants");
        if (!cJSON_IsArray(grants)) ok = false;
        else for (cJSON *item = grants->child; item; item = item->next) allowed |= capability(string(item, "capability"), string(item, "resourceId"));
        if (!ok || !vx_session(&executor, epoch, grant, allowed, server, until, now_ms())) atomic_store(&stop_requested, true);
    } else if (!strcmp(type, "command")) {
        vx_command command;
        uint64_t version;
        if (!number(root, "protocolVersion", &version) || version != 1 || !decode_command(root, &command)) atomic_store(&stop_requested, true);
        else {
            const char *failure = vx_execute(&executor, &command, now_ms());
            if (failure && !vx_query(&executor, command.id)) {
                vx_record rejected = {.command = command, .status = VX_REJECTED, .seq = 1, .mono_ms = now_ms()};
                receipt(NULL, &rejected);
            }
        }
    } else if (!strcmp(type, "query")) {
        const vx_record *record = vx_query(&executor, string(root, "commandId"));
        if (record) receipt(NULL, record); /* Missing history stays unknown on Engine. */
    } else if (!strcmp(type, "receipt_ack")) {
        uint64_t seq;
        if (number(root, "receiptSeq", &seq)) vx_ack(&executor, string(root, "commandId"), seq);
    } else if (!strcmp(type, "cancel")) {
        const vx_record *record = vx_query(&executor, string(root, "commandId"));
        if (record && !strcmp(record->command.digest, string(root, "commandDigest"))) {
            safe_output(NULL); executor.revision++; executor.output_until_mono = 0;
            receipt(NULL, record); /* Completed GPIO write remains completed, cancellation does not undo history. */
        }
    } else if (!strcmp(type, "stop")) atomic_store(&stop_requested, true);
    else atomic_store(&stop_requested, true);
    cJSON_Delete(root);
}
static void hello(void) {
    cJSON *root = cJSON_CreateObject();
    cJSON_AddStringToObject(root, "type", "hello"); cJSON_AddNumberToObject(root, "protocolVersion", 1);
    cJSON_AddStringToObject(root, "authorityId", executor.authority); cJSON_AddStringToObject(root, "deviceId", executor.device);
    cJSON_AddStringToObject(root, "bootId", executor.boot); cJSON_AddStringToObject(root, "controlSessionId", executor.session);
    cJSON_AddNumberToObject(root, "capabilityRevision", 1); cJSON_AddBoolToObject(root, "localEnabled", executor.armed);
    cJSON *capabilities = cJSON_AddArrayToObject(root, "capabilities");
    const char *names[] = {"device.health", "sensor.read", "actuator.set"};
    const char *resources[] = {"device", "sensor.input", "logic.led"};
    for (int i = 0; i < 3; ++i) {
        cJSON *item = cJSON_CreateObject(); cJSON_AddStringToObject(item, "capability", names[i]);
        cJSON_AddStringToObject(item, "resourceId", resources[i]); cJSON_AddItemToArray(capabilities, item);
    }
    send_json(root);
}
static void websocket_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg; (void)base;
    esp_websocket_event_data_t *event = data;
    if (id == WEBSOCKET_EVENT_CONNECTED) { received_bytes = 0; atomic_store(&transport_online, true); hello(); }
    else if (id == WEBSOCKET_EVENT_DISCONNECTED || id == WEBSOCKET_EVENT_ERROR) {
        atomic_store(&transport_online, false); safe_output(NULL);
    } else if (id == WEBSOCKET_EVENT_DATA) {
        if (event->op_code == 0x8) { atomic_store(&transport_online, false); safe_output(NULL); return; }
        if (event->op_code != 0x1 && event->op_code != 0x0) return;
        if (event->payload_offset == 0 && event->op_code == 0x1) received_bytes = 0;
        if (event->data_len < 0 || event->payload_len > FRAME_BYTES || received_bytes + event->data_len > FRAME_BYTES) {
            atomic_store(&stop_requested, true); safe_output(NULL); return;
        }
        memcpy(receive_frame + received_bytes, event->data_ptr, event->data_len); received_bytes += event->data_len;
        if (event->fin && event->payload_offset + event->data_len == event->payload_len) {
            receive_frame[received_bytes] = 0;
            char *copy = malloc(received_bytes + 1);
            if (!copy) { atomic_store(&stop_requested, true); return; }
            memcpy(copy, receive_frame, received_bytes + 1); received_bytes = 0;
            if (xQueueSend(inbound, &copy, 0) != pdTRUE) { free(copy); atomic_store(&stop_requested, true); safe_output(NULL); }
        }
    }
}
static void wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg; (void)data;
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) { atomic_store(&transport_online, false); safe_output(NULL); esp_wifi_connect(); }
}
static void safety_task(void *unused) {
    (void)unused; bool pressed = false, was_armed = false; uint64_t since = 0;
    for (;;) {
        bool down = gpio_get_level(CONFIG_V8_STOP_GPIO) == 0;
        if (down && !pressed) {
            pressed = true; since = now_ms(); was_armed = executor.armed;
            if (was_armed) { atomic_store(&stop_requested, true); safe_output(NULL); }
        }
        if (down && !was_armed && now_ms() - since >= 2000) { atomic_store(&arm_requested, true); was_armed = true; }
        if (!down) pressed = false;
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
void app_main(void) {
    if (CONFIG_V8_OUTPUT_GPIO == CONFIG_V8_SENSOR_GPIO || CONFIG_V8_STOP_GPIO == CONFIG_V8_OUTPUT_GPIO
        || CONFIG_V8_STOP_GPIO == CONFIG_V8_SENSOR_GPIO) return;
    gpio_config_t output = {.pin_bit_mask = 1ULL << CONFIG_V8_OUTPUT_GPIO, .mode = GPIO_MODE_INPUT_OUTPUT};
    gpio_config_t input = {.pin_bit_mask = 1ULL << CONFIG_V8_SENSOR_GPIO, .mode = GPIO_MODE_INPUT, .pull_down_en = GPIO_PULLDOWN_ENABLE};
    gpio_config_t stop = {.pin_bit_mask = 1ULL << CONFIG_V8_STOP_GPIO, .mode = GPIO_MODE_INPUT, .pull_up_en = GPIO_PULLUP_ENABLE};
    if (gpio_config(&output) != ESP_OK || gpio_config(&input) != ESP_OK || gpio_config(&stop) != ESP_OK) return;
    safe_output(NULL);
    /* The SDK can generate an HMAC eFuse key when absent. This firmware never
       authorizes that irreversible operation: a separately provisioned key is
       mandatory, and an ordinary flash/boot must leave all eFuses unchanged. */
    if (esp_efuse_get_key_purpose(EFUSE_BLK_KEY0 + CONFIG_NVS_SEC_HMAC_EFUSE_KEY_ID) != ESP_EFUSE_KEY_PURPOSE_HMAC_UP) {
        ESP_LOGW("v8_executor", "Device NVS key is not provisioned; executor remains disabled");
        return;
    }
    esp_timer_create_args_t timer = {.callback = safe_output, .name = "v8_safe_output"};
    if (esp_timer_create(&timer, &output_timer) != ESP_OK || nvs_flash_init() != ESP_OK
        || nvs_open("v8_executor", NVS_READWRITE, &store) != ESP_OK) return;
    if (!v8_provision(&config, store)) return;
    esp_netif_init(); esp_event_loop_create_default(); esp_netif_create_default_wifi_sta();
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    if (esp_wifi_init(&init) != ESP_OK) return;
    esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event, NULL);
    wifi_config_t wifi = {0};
    memcpy(wifi.sta.ssid, config.ssid, strlen(config.ssid)); memcpy(wifi.sta.password, config.password, strlen(config.password));
    wifi.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    esp_wifi_set_mode(WIFI_MODE_STA); esp_wifi_set_config(WIFI_IF_STA, &wifi); esp_wifi_start(); esp_wifi_connect();
    uint64_t deadline = now_ms() + 30000;
    esp_netif_ip_info_t info = {0};
    while (now_ms() < deadline) {
        esp_netif_get_ip_info(esp_netif_get_handle_from_ifkey("WIFI_STA_DEF"), &info);
        if (info.ip.addr) break;
        vTaskDelay(pdMS_TO_TICKS(100));
    }
    if (!info.ip.addr || !v8_enroll(&config, store)) return;
    inbound = xQueueCreate(2, sizeof(char *)); outbound = xQueueCreate(8, sizeof(char *));
    if (!inbound || !outbound) return;
    static vx_journal journal;
    size_t length = sizeof(journal);
    esp_err_t loaded = nvs_get_blob(store, "journal", &journal, &length);
    if ((loaded != ESP_OK && loaded != ESP_ERR_NVS_NOT_FOUND) || (loaded == ESP_OK && length != sizeof(journal))) return;
    char boot[33]; random_id(boot);
    vx_driver driver = {NULL, persist, write_output, read_output, read_sensor, receipt};
    if (!vx_init(&executor, loaded == ESP_OK ? &journal : NULL, driver, config.authority, config.device, boot, 7, CONFIG_V8_MAX_HOLD_MS)) return;
    char uri[320], headers[180];
    snprintf(uri, sizeof(uri), "wss://%.247s/api/executor/ws", config.origin + 8);
    snprintf(headers, sizeof(headers), "Authorization: Bearer %s\r\n", config.credential);
    esp_websocket_client_config_t ws = {.uri = uri, .headers = headers, .subprotocol = "v8.device-executor.v1",
        .cert_pem = config.ca[0] ? config.ca : NULL, .crt_bundle_attach = config.ca[0] ? NULL : esp_crt_bundle_attach,
        .skip_cert_common_name_check = false, .disable_auto_reconnect = true, .network_timeout_ms = 5000,
        .buffer_size = 2048, .task_stack = 6144};
    socket_client = esp_websocket_client_init(&ws);
    memset(headers, 0, sizeof(headers));
    if (!socket_client) return;
    esp_websocket_register_events(socket_client, WEBSOCKET_EVENT_ANY, websocket_event, NULL);
    xTaskCreate(safety_task, "v8_local_stop", 2048, NULL, 12, NULL);
    bool started = false, was_online = false;
    uint64_t ping_at = 0, retry_at = 0;
    for (;;) {
        if (atomic_exchange(&stop_requested, false)) {
            vx_stop(&executor, now_ms()); safe_output(NULL);
            if (started) esp_websocket_client_stop(socket_client);
            started = false; atomic_store(&transport_online, false);
            char *stale;
            while (xQueueReceive(inbound, &stale, 0) == pdTRUE) free(stale);
            while (xQueueReceive(outbound, &stale, 0) == pdTRUE) free(stale);
        }
        if (atomic_exchange(&arm_requested, false) && !executor.armed) { char session[33]; random_id(session); vx_arm(&executor, session); }
        bool online = atomic_load(&transport_online);
        if (was_online && !online) { vx_disconnect(&executor, now_ms()); retry_at = now_ms() + 1000 + esp_random() % 2000; }
        was_online = online;
        if (executor.armed && !online && now_ms() >= retry_at) {
            if (started) esp_websocket_client_stop(socket_client);
            started = esp_websocket_client_start(socket_client) == ESP_OK; retry_at = now_ms() + 5000 + esp_random() % 2000;
        }
        vx_tick(&executor, now_ms());
        char *text = NULL;
        if (xQueueReceive(inbound, &text, 0) == pdTRUE) { handle_frame(text); free(text); }
        if (xQueueReceive(outbound, &text, 0) == pdTRUE) {
            if (online && executor.armed) esp_websocket_client_send_text(socket_client, text, strlen(text), pdMS_TO_TICKS(100));
            free(text);
        }
        if (online && now_ms() >= ping_at) {
            esp_websocket_client_send_text(socket_client, "{\"type\":\"ping\"}", 15, pdMS_TO_TICKS(100)); ping_at = now_ms() + 10000;
        }
        vx_tick(&executor, now_ms());
        if (!executor.healthy) { vx_stop(&executor, now_ms()); safe_output(NULL); }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
