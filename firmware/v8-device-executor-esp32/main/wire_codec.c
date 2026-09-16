#include "wire_codec.h"
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <math.h>
#ifdef VX_HOST
#include <openssl/sha.h>
static int mbedtls_sha256(const unsigned char *p, size_t n, unsigned char *out, int is224) { (void)is224; return SHA256(p,n,out) ? 0 : -1; }
#else
#include "mbedtls/sha256.h"
#endif

const char *string(cJSON *root, const char *key) {
    cJSON *value = cJSON_GetObjectItemCaseSensitive(root, key);
    return cJSON_IsString(value) ? value->valuestring : "";
}
bool number(cJSON *root, const char *key, uint64_t *value) {
    cJSON *item = cJSON_GetObjectItemCaseSensitive(root, key);
    if (!cJSON_IsNumber(item) || !isfinite(item->valuedouble) || item->valuedouble < 0
        || item->valuedouble > 9007199254740991.0 || floor(item->valuedouble) != item->valuedouble) return false;
    *value = (uint64_t)item->valuedouble; return true;
}
static bool copy_field(char *out, size_t size, cJSON *root, const char *key) {
    const char *text = string(root, key);
    if (!*text || strlen(text) >= size) return false;
    strcpy(out, text); return true;
}
unsigned capability(const char *name, const char *resource) {
    if (!strcmp(name, "device.health") && !strcmp(resource, "device")) return VX_HEALTH;
    if (!strcmp(name, "sensor.read") && !strcmp(resource, "sensor.input")) return VX_SENSOR;
    if (!strcmp(name, "actuator.set") && !strcmp(resource, "logic.led")) return VX_SET;
    return 0;
}
static bool valid_tree(cJSON *root, unsigned depth) {
    if (depth > 12) return false;
    unsigned count = 0;
    for (cJSON *item = root->child; item; item = item->next) {
        if (++count > 256) return false;
        if (cJSON_IsObject(root)) for (cJSON *other = item->next; other; other = other->next)
            if (!strcmp(item->string, other->string)) return false;
        if ((cJSON_IsArray(item) || cJSON_IsObject(item)) && !valid_tree(item, depth + 1)) return false;
        if (cJSON_IsNumber(item) && !isfinite(item->valuedouble)) return false;
    }
    return true;
}
cJSON *parse_frame(const char *text) {
    if (strlen(text) > 16384) return NULL;
    int depth = 0; bool quote = false, escape = false;
    for (const char *p = text; *p; ++p) {
        if (escape) { escape = false; continue; }
        if (quote && *p == '\\') { escape = true; continue; }
        if (*p == '"') { quote = !quote; continue; }
        if (!quote && (*p == '{' || *p == '[') && ++depth > 12) return NULL;
        if (!quote && (*p == '}' || *p == ']') && --depth < 0) return NULL;
    }
    if (depth || quote) return NULL;
    cJSON *root = cJSON_ParseWithOpts(text, NULL, true);
    if (!cJSON_IsObject(root) || !valid_tree(root, 0)) { cJSON_Delete(root); return NULL; }
    return root;
}
bool decode_command(cJSON *root, vx_command *command) {
    memset(command, 0, sizeof(*command));
    if (!copy_field(command->id, sizeof(command->id), root, "commandId")
        || !copy_field(command->digest, sizeof(command->digest), root, "commandDigest")
        || !copy_field(command->authority, sizeof(command->authority), root, "authorityId")
        || !copy_field(command->device, sizeof(command->device), root, "deviceId")
        || !copy_field(command->boot, sizeof(command->boot), root, "bootId")
        || !copy_field(command->session, sizeof(command->session), root, "controlSessionId")) return false;
    if (!number(root, "leaseEpoch", &command->epoch) || !number(root, "grantRevision", &command->grant)
        || !number(root, "capabilityRevision", &command->capability_revision)
        || !number(root, "issuedUnixMs", &command->issued_ms) || !number(root, "deadlineUnixMs", &command->deadline_ms)
        || !number(root, "ttlMs", &command->ttl_ms)) return false;
    command->capability = capability(string(root, "capability"), string(root, "resourceId"));
    cJSON *arguments = cJSON_GetObjectItemCaseSensitive(root, "arguments");
    cJSON *precondition = cJSON_GetObjectItemCaseSensitive(root, "precondition");
    if (!cJSON_IsObject(arguments) || !cJSON_IsObject(precondition) || !command->capability) return false;
    if (command->capability == VX_SET) {
        uint64_t hold;
        cJSON *level = cJSON_GetObjectItemCaseSensitive(arguments, "level");
        if (cJSON_GetArraySize(arguments) != 2 || !cJSON_IsBool(level) || !number(arguments, "maxHoldMs", &hold)
            || !hold || hold > 30000 || !number(precondition, "resourceRevision", &command->expected_revision)) return false;
        command->level = cJSON_IsTrue(level); command->hold_ms = (uint32_t)hold;
    } else if (command->capability == VX_HEALTH && arguments->child) return false;
    else if (command->capability == VX_SENSOR && arguments->child) {
        uint64_t age;
        if (cJSON_GetArraySize(arguments) != 1 || !number(arguments, "maxAgeMs", &age) || age > 60000) return false;
    }
    /* Engine sends canonical sorted JSON. cJSON preserves object insertion
       order, so removing digest reproduces the exact canonical action bytes. */
    cJSON_DeleteItemFromObjectCaseSensitive(root, "commandDigest");
    char *canonical = cJSON_PrintUnformatted(root);
    if (!canonical) return false;
    uint8_t hash[32]; char expected[65];
    int hashed = mbedtls_sha256((const unsigned char *)canonical, strlen(canonical), hash, 0); free(canonical);
    if (hashed != 0) return false;
    for (unsigned i = 0; i < 32; ++i) sprintf(expected + i * 2, "%02x", hash[i]);
    return !strcmp(expected, command->digest);
}
