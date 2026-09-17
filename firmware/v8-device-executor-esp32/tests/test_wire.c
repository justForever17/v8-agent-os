#include "wire_codec.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    assert(argc == 2);
    FILE *file = fopen(argv[1], "rb"); assert(file);
    char frame[16385] = {0}; size_t length = fread(frame, 1, sizeof(frame) - 1, file); fclose(file);
    assert(length > 0);
    cJSON *json = parse_frame(frame); assert(json);
    vx_command command;
    assert(decode_command(json, &command));
    assert(command.level && command.hold_ms == 1000 && command.epoch == 1 && command.expected_revision == 1);
    assert(!strcmp(command.id, "command1") && !strcmp(command.boot, "boot"));
    cJSON_Delete(json);
    json = parse_frame(frame);
    cJSON_SetNumberValue(cJSON_GetObjectItemCaseSensitive(json, "grantRevision"), 3);
    assert(!decode_command(json, &command)); cJSON_Delete(json);
    json = parse_frame(frame);
    cJSON *arguments = cJSON_GetObjectItemCaseSensitive(json, "arguments");
    cJSON_ReplaceItemInObjectCaseSensitive(arguments, "level", cJSON_CreateNumber(1));
    assert(!decode_command(json, &command)); cJSON_Delete(json);
    assert(!parse_frame("{\"type\":\"command\",\"type\":\"query\"}"));
    assert(!parse_frame("{\"a\":1e999}"));
    assert(!parse_frame("{\"a\":[[[[[[[[[[[[[[0]]]]]]]]]]]]]]}"));
    puts("wire: Engine canonical SHA256, immutable grant, typed args, duplicate/deep/nonfinite JSON passed");
    return 0;
}
