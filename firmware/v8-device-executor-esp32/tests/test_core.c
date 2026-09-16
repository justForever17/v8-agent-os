#include "executor_core.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

typedef struct { vx_journal flash; bool level; int writes, persist_calls, fail_at, receipts; } bench;
static bool persist(void *ctx, const vx_journal *j) {
    bench *b = ctx;
    if (++b->persist_calls == b->fail_at) return false;
    b->flash = *j; return true;
}
static bool output(void *ctx, bool level) { bench *b = ctx; b->level = level; if (level) b->writes++; return true; }
static bool readback(void *ctx) { return ((bench *)ctx)->level; }
static void received(void *ctx, const vx_record *r) { (void)r; ((bench *)ctx)->receipts++; }
static void setup(vx_executor *e, bench *b) {
    memset(b, 0, sizeof(*b));
    vx_driver driver = {b, persist, output, readback, readback, received};
    assert(vx_init(e, NULL, driver, "authority", "device", "boot", 7, 500));
    assert(!e->armed && !b->level);
    assert(vx_arm(e, "arm"));
    assert(vx_session(e, 1, 2, 7, 10000, 40000, 100));
}
static vx_command command(vx_executor *e) {
    vx_command c = {0};
    strcpy(c.id, "command1"); strcpy(c.digest, "digest1"); strcpy(c.authority, "authority"); strcpy(c.device, "device");
    strcpy(c.boot, "boot"); strcpy(c.session, "arm");
    c.epoch = 1; c.grant = 2; c.capability_revision = 1; c.issued_ms = 10000; c.deadline_ms = 12000; c.ttl_ms = 2000;
    c.capability = VX_SET; c.expected_revision = e->revision; c.hold_ms = 2000; c.level = true; return c;
}
int main(void) {
    vx_executor e; bench b;
    setup(&e, &b); vx_command c = command(&e);
    assert(vx_execute(&e, &c, 100) == NULL && b.writes == 1 && b.receipts == 3);
    assert(vx_execute(&e, &c, 100) == NULL && b.writes == 1);
    strcpy(c.digest, "different"); assert(!strcmp(vx_execute(&e, &c, 100), "command_id_conflict"));
    vx_tick(&e, 600); assert(!b.level); /* local maxHold wins without network */
    setup(&e, &b); c = command(&e); c.epoch = 0; assert(vx_execute(&e, &c, 100) && b.writes == 0);
    c = command(&e); c.grant = 1; assert(vx_execute(&e, &c, 100) && b.writes == 0);
    c = command(&e); strcpy(c.boot, "old"); assert(vx_execute(&e, &c, 100) && b.writes == 0);
    c = command(&e); strcpy(c.session, "old-arm"); assert(vx_execute(&e, &c, 100) && b.writes == 0);
    c = command(&e); assert(vx_execute(&e, &c, 2100) && b.writes == 0);
    vx_stop(&e, 100); c = command(&e); assert(vx_execute(&e, &c, 100) && b.writes == 0);
    assert(!vx_session(&e, 2, 2, 7, 10001, 40001, 101)); /* remote cannot re-arm */
    setup(&e, &b); c = command(&e); b.fail_at = b.persist_calls + 2;
    assert(vx_execute(&e, &c, 100) && b.writes == 0); /* STARTED fsync failure */
    setup(&e, &b); c = command(&e); b.fail_at = b.persist_calls + 3;
    assert(vx_execute(&e, &c, 100) && b.writes == 1 && !b.level);
    assert(b.flash.records[0].status == VX_STARTED); /* effect happened, completion not stored */
    vx_journal flash = b.flash; b.fail_at = 0;
    assert(vx_init(&e, &flash, e.driver, "authority", "device", "boot2", 7, 500));
    assert(vx_query(&e, "command1")->status == VX_UNKNOWN && !e.armed);
    assert(vx_arm(&e, "arm2")); assert(vx_session(&e, 2, 2, 7, 11000, 41000, 100));
    assert(vx_execute(&e, &c, 100) == NULL && b.writes == 1); /* old receipt only */
    setup(&e, &b); c = command(&e); assert(!vx_execute(&e, &c, 100));
    vx_disconnect(&e, 101); assert(!b.level); /* safe output on disconnect */
    setup(&e, &b); c = command(&e);
    for (int i = 0; i < VX_RECORDS; ++i) { snprintf(c.id, sizeof(c.id), "command%d", i); c.expected_revision = e.revision; assert(!vx_execute(&e, &c, 100)); }
    strcpy(c.id, "overflow"); c.expected_revision = e.revision;
    assert(!strcmp(vx_execute(&e, &c, 100), "journal_full") && b.writes == VX_RECORDS);
    puts("core: duplicate/conflict/epoch/grant/boot/session/expiry/local-stop/watchdog/flash-failure/reboot-unknown/journal-full passed");
    return 0;
}
