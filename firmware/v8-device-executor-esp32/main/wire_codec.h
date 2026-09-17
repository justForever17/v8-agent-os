#ifndef V8_WIRE_CODEC_H
#define V8_WIRE_CODEC_H
#include "executor_core.h"
#include "cJSON.h"
const char *string(cJSON *, const char *);
bool number(cJSON *, const char *, uint64_t *);
unsigned capability(const char *, const char *);
cJSON *parse_frame(const char *);
bool decode_command(cJSON *, vx_command *);
#endif
