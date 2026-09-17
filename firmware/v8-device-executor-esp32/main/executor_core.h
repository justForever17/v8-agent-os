#ifndef V8_EXECUTOR_CORE_H
#define V8_EXECUTOR_CORE_H
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

#define VX_ID 193
#define VX_RECORDS 32
#define VX_HEALTH 1u
#define VX_SENSOR 2u
#define VX_SET 4u
typedef enum { VX_EMPTY, VX_RECEIVED, VX_STARTED, VX_SUCCEEDED, VX_FAILED,
               VX_REJECTED, VX_CANCELLED, VX_UNKNOWN } vx_status;
typedef struct {
    char id[VX_ID], digest[65], authority[VX_ID], device[VX_ID], boot[VX_ID], session[VX_ID];
    uint64_t epoch, grant, capability_revision, issued_ms, deadline_ms, ttl_ms;
    unsigned capability;
    uint64_t expected_revision;
    uint32_t hold_ms;
    bool level;
} vx_command;
typedef struct {
    vx_command command;
    vx_status status;
    uint64_t seq, mono_ms, observed_unix_ms, resource_revision;
    bool readback, acknowledged;
} vx_record;
typedef struct {
    uint32_t version;
    uint64_t highest_epoch;
    vx_record records[VX_RECORDS];
} vx_journal;
typedef struct {
    void *context;
    bool (*persist)(void *, const vx_journal *);
    bool (*write_output)(void *, bool);
    bool (*read_output)(void *);
    bool (*read_sensor)(void *);
    void (*receipt)(void *, const vx_record *);
} vx_driver;
typedef struct {
    vx_journal journal;
    vx_driver driver;
    char authority[VX_ID], device[VX_ID], boot[VX_ID], session[VX_ID];
    bool armed, connected, healthy;
    uint64_t epoch, grant, capability_revision, revision;
    uint64_t anchor_server_ms, anchor_mono_ms, lease_until_ms, output_until_mono;
    uint32_t max_hold_ms;
    unsigned local_capabilities, grants;
} vx_executor;

bool vx_init(vx_executor *, const vx_journal *, vx_driver, const char *authority,
             const char *device, const char *boot, unsigned local_capabilities, uint32_t max_hold_ms);
bool vx_arm(vx_executor *, const char *control_session);
void vx_stop(vx_executor *, uint64_t now_mono);
void vx_disconnect(vx_executor *, uint64_t now_mono);
bool vx_session(vx_executor *, uint64_t epoch, uint64_t grant, unsigned grants,
                uint64_t server_ms, uint64_t lease_until_ms, uint64_t now_mono);
const vx_record *vx_query(vx_executor *, const char *id);
const char *vx_execute(vx_executor *, const vx_command *, uint64_t now_mono);
void vx_ack(vx_executor *, const char *id, uint64_t seq);
void vx_tick(vx_executor *, uint64_t now_mono);
const char *vx_status_name(vx_status);
#endif
