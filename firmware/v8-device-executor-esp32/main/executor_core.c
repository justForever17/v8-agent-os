#include "executor_core.h"
#include <string.h>

static bool copy_id(char *out, const char *in) {
    if (!in || !*in || strlen(in) >= VX_ID) return false;
    strcpy(out, in); return true;
}
static uint64_t server_now(vx_executor *e, uint64_t mono) {
    return mono < e->anchor_mono_ms ? UINT64_MAX : e->anchor_server_ms + mono - e->anchor_mono_ms;
}
static bool save(vx_executor *e) {
    if (e->driver.persist(e->driver.context, &e->journal)) return true;
    e->healthy = false; e->armed = false; e->connected = false;
    e->driver.write_output(e->driver.context, false);
    return false;
}
static void emit(vx_executor *e, vx_record *r, vx_status state, uint64_t mono) {
    r->status = state; r->seq++; r->mono_ms = mono;
    r->observed_unix_ms = server_now(e, mono);
    r->resource_revision = e->revision;
    /* Completion is not reported before durable storage. Power loss during the
       driver leaves STARTED on flash, recovered as UNKNOWN on next boot. */
    if (!save(e)) r->status = VX_UNKNOWN;
    e->driver.receipt(e->driver.context, r);
}
bool vx_init(vx_executor *e, const vx_journal *journal, vx_driver driver,
             const char *authority, const char *device, const char *boot,
             unsigned local_capabilities, uint32_t max_hold_ms) {
    memset(e, 0, sizeof(*e));
    e->driver = driver;
    if (!driver.persist || !driver.write_output || !driver.read_output || !driver.read_sensor || !driver.receipt) return false;
    driver.write_output(driver.context, false);
    if (!copy_id(e->authority, authority) || !copy_id(e->device, device) || !copy_id(e->boot, boot)) return false;
    e->journal.version = 1;
    if (journal) {
        if (journal->version != 1) return false; /* Corruption never means empty. */
        e->journal = *journal;
    }
    e->capability_revision = 1; e->revision = 1;
    e->local_capabilities = local_capabilities;
    e->max_hold_ms = max_hold_ms; e->healthy = max_hold_ms > 0;
    for (unsigned i = 0; i < VX_RECORDS; ++i) {
        vx_record *r = &e->journal.records[i];
        if (r->status == VX_STARTED || r->status == VX_RECEIVED) {
            r->status = r->status == VX_STARTED ? VX_UNKNOWN : VX_CANCELLED;
            r->seq++; r->acknowledged = false;
        }
    }
    return e->healthy && save(e);
}
bool vx_arm(vx_executor *e, const char *session) {
    if (!e->healthy || e->armed || !copy_id(e->session, session)) return false;
    e->armed = true; e->connected = false; return true;
}
void vx_stop(vx_executor *e, uint64_t mono) {
    (void)mono;
    e->armed = false; e->connected = false; e->session[0] = 0;
    e->output_until_mono = 0; e->revision++;
    if (!e->driver.write_output(e->driver.context, false)) e->healthy = false;
}
void vx_disconnect(vx_executor *e, uint64_t mono) {
    (void)mono;
    e->connected = false; e->output_until_mono = 0; e->revision++;
    if (!e->driver.write_output(e->driver.context, false)) e->healthy = false;
}
bool vx_session(vx_executor *e, uint64_t epoch, uint64_t grant, unsigned grants,
                uint64_t server_ms, uint64_t lease_until_ms, uint64_t mono) {
    if (!e->armed || !e->healthy || epoch < e->journal.highest_epoch || !epoch || !grant
        || lease_until_ms <= server_ms || lease_until_ms - server_ms > 30000) return false;
    if (e->connected && (epoch != e->epoch || grant != e->grant)) return false;
    if (epoch == e->epoch && (!e->connected || server_now(e, mono) >= e->lease_until_ms)) return false;
    /* Never renew a locally expired lease under the same epoch. */
    if (e->connected && server_now(e, mono) >= e->lease_until_ms) return false;
    if (e->anchor_server_ms && server_ms + 1000 < server_now(e, mono)) return false;
    if (epoch > e->journal.highest_epoch) {
        e->journal.highest_epoch = epoch;
        if (!save(e)) return false;
    }
    uint64_t previous_now = e->anchor_server_ms ? server_now(e, mono) : 0;
    if (server_ms < previous_now) server_ms = previous_now;
    if (server_ms >= lease_until_ms) return false;
    e->epoch = epoch; e->grant = grant; e->grants = grants & e->local_capabilities;
    e->anchor_server_ms = server_ms; e->anchor_mono_ms = mono; e->lease_until_ms = lease_until_ms;
    e->connected = true; return true;
}
const vx_record *vx_query(vx_executor *e, const char *id) {
    for (unsigned i = 0; i < VX_RECORDS; ++i)
        if (e->journal.records[i].status != VX_EMPTY && !strcmp(e->journal.records[i].command.id, id)) return &e->journal.records[i];
    return NULL;
}
const char *vx_execute(vx_executor *e, const vx_command *c, uint64_t mono) {
    const vx_record *prior = vx_query(e, c->id);
    if (prior) {
        if (strcmp(prior->command.digest, c->digest)) return "command_id_conflict";
        e->driver.receipt(e->driver.context, prior); return NULL;
    }
    if (!e->healthy || !e->armed || !e->connected) return "requires_local_arm";
    if (strcmp(c->authority, e->authority) || strcmp(c->device, e->device)
        || strcmp(c->boot, e->boot) || strcmp(c->session, e->session)) return "identity_stale";
    if (c->epoch != e->epoch || c->grant != e->grant || c->capability_revision != e->capability_revision) return "authority_revision_stale";
    uint64_t now = server_now(e, mono);
    if (now >= e->lease_until_ms || now >= c->deadline_ms || c->deadline_ms > e->lease_until_ms
        || now < c->issued_ms || !c->ttl_ms || c->ttl_ms > 30000 || c->deadline_ms - c->issued_ms > c->ttl_ms) return "expired";
    if (!(c->capability & e->grants) || (c->capability != VX_HEALTH && c->capability != VX_SENSOR && c->capability != VX_SET)) return "not_granted";
    if (c->capability == VX_SET && (c->expected_revision != e->revision || !c->hold_ms || c->hold_ms > 30000)) return "resource_revision_or_hold_invalid";
    vx_record *record = NULL;
    for (unsigned i = 0; i < VX_RECORDS; ++i) {
        vx_record *candidate = &e->journal.records[i];
        if (candidate->status == VX_EMPTY || (candidate->acknowledged && candidate->status >= VX_SUCCEEDED
            && now > candidate->command.deadline_ms && candidate->command.epoch < e->epoch)) {
            record = candidate; break;
        }
    }
    if (!record) return "journal_full";
    memset(record, 0, sizeof(*record)); record->command = *c;
    emit(e, record, VX_RECEIVED, mono);
    if (!e->healthy) return "journal_write_failed";
    emit(e, record, VX_STARTED, mono);
    if (!e->healthy) return "journal_write_failed";
    bool ok = true;
    if (c->capability == VX_SET) {
        uint64_t hold = c->hold_ms < e->max_hold_ms ? c->hold_ms : e->max_hold_ms;
        if (hold > c->deadline_ms - now) hold = c->deadline_ms - now;
        e->output_until_mono = c->level ? mono + hold : 0;
        ok = e->driver.write_output(e->driver.context, c->level);
        e->revision++;
        record->readback = e->driver.read_output(e->driver.context);
        ok = ok && record->readback == c->level;
    } else if (c->capability == VX_SENSOR) {
        record->readback = e->driver.read_sensor(e->driver.context);
    } else record->readback = e->driver.read_output(e->driver.context);
    emit(e, record, ok ? VX_SUCCEEDED : VX_FAILED, mono);
    return e->healthy ? NULL : "journal_write_failed";
}
void vx_ack(vx_executor *e, const char *id, uint64_t seq) {
    for (unsigned i = 0; i < VX_RECORDS; ++i) {
        vx_record *r = &e->journal.records[i];
        if (!strcmp(r->command.id, id) && r->seq == seq && r->status >= VX_SUCCEEDED && !r->acknowledged) {
            r->acknowledged = true; save(e); return;
        }
    }
}
void vx_tick(vx_executor *e, uint64_t mono) {
    if ((e->output_until_mono && mono >= e->output_until_mono)
        || (e->connected && server_now(e, mono) >= e->lease_until_ms)) {
        if (!e->driver.write_output(e->driver.context, false)) e->healthy = false;
        e->output_until_mono = 0; e->revision++;
        if (server_now(e, mono) >= e->lease_until_ms) e->connected = false;
    }
}
const char *vx_status_name(vx_status s) {
    static const char *names[] = {"empty", "received", "started", "succeeded", "failed", "rejected", "cancelled", "unknown_outcome"};
    return s <= VX_UNKNOWN ? names[s] : "unknown_outcome";
}
