# Fixed remote executors

An executor runs a small set of explicitly enabled device actions. The Engine's
Supervisor performs reasoning and uses `device_broker`; the existing Safety and
runtime episode queue govern each action. Devices do not run a model or accept
shell commands. Human Phone pairing and executor control use separate credentials.

## Android Phone

Phone settings includes **Local executor** on Android. The native module is part
of the Phone APK and starts disabled. Select a trusted HTTPS Engine origin, enroll,
choose the allowed application package names, grant the Android accessibility and
notification permissions manually, then enable control. HTTPS/WSS must validate
the Engine certificate. Switching the chat profile does not move this binding.

The persistent notification and the local executor screen offer **Stop**. Stop
disarms native admission immediately and invalidates the control session, including
when chat is logged out or the Engine cannot be reached. Forgetting the bound chat
profile stops that executor. Revocation uses the independent device credential;
an offline revocation remains pending. A process restart requires local re-enabling.

The first Android capabilities are `android.observe` and `android.action` for
allowed external apps. Observation returns a bounded accessibility tree, marked
`partial` when incomplete. Actions are click, long click, set text and scrolling
on nodes from that observation. Each action rechecks device, boot, control session,
app, window, node map and node identity before applying, and then observes again.
Password nodes and Phone's own approval/settings surfaces are unavailable to the
executor. Screenshots, coordinate tapping and arbitrary iOS cross-app control are
not part of these capabilities. The module shares Phone's Android process and UID.

## Engine interface

All routes terminate in the Engine. The existing Phone listener exposes only the
following explicit routes; Admin is not required in the device execution path.

| Route | Caller and purpose |
| --- | --- |
| `POST /api/client/executors/tickets` | Authenticated human; `{deviceClass,name,baseUrl}`; 120-second, single-use ticket |
| `POST /api/executor/enroll` | Native host; `{ticket,authorityId,deviceClass}`; independent credential and zero initial grants |
| `GET /api/client/executors` | Human; device availability, capabilities and grants |
| `PUT /api/client/executors/{id}/grants` | Human; `{expectedRevision,grants:[{capability,resourceId}]}`; compare-and-swap revision |
| `DELETE /api/client/executors/{id}` | Human; revoke this device |
| `POST /api/executor/revoke` | Executor credential; revoke only itself |
| `WS /api/executor/ws` | Executor Bearer credential only; bounded v1 control frames |
| `GET /api/client/executors/commands/{id}` | Human; immutable command and device receipts |
| `POST /api/client/executors/commands/{id}/cancel` | Human; request cancellation |
| `POST /api/client/executors/commands/{id}/reconcile` | Human; acknowledge an expired unknown outcome with a note; never marks it successful |

Supervisor `device_broker` exposes `list`, `execute`, `status`, and `cancel`.
`execute` requires the precise device/resource capability, typed arguments and a
current observation or resource revision. It creates one `device_action` episode
and returns its command/episode IDs. Device grants cannot be edited through this
tool. The Engine persists credential verifiers, grants, epochs, commands and
receipts in the identity service's existing `state.db` transactions.

## Receipts and recovery

`received` and `started` are progress. `succeeded` means the driver completed;
`businessVerification` remains `unverified` until the task's actual outcome is
assessed from evidence. GPIO readback is an electrical logic observation, and an
Android action acceptance is not proof that the intended business operation worked.

Each immutable command binds its SHA-256 digest, device/authority, boot, locally
armed control session, lease epoch, grant/capability revision and deadline. The
30-second device lease renews while connected; command TTL is at most 30 seconds
and cannot be reset. New connections fence older epochs. Frames are bounded to
16 KiB and reject duplicate JSON keys, non-finite numbers and excessive depth.

The Engine commits before sending. Loss after sending becomes `unknown_outcome`,
including loss before the first acknowledgement. Reconnection queries historical
receipts; it never resends an action automatically. Repeated command IDs return
the stored result; changed content conflicts. New mutations remain blocked while
an unknown action needs reconciliation. Cancellation and a late completion are
recorded as separate facts; cancellation cannot undo an already applied effect.

These tables are additive. Older Engine builds can leave them intact but cannot
operate executors. Stop local executors and revoke their grants before rolling
back the Engine. Preserve the tables for later receipt reconciliation; do not
clear identity or journal data to recover a stalled action.

## ESP32 and verification

See [the ESP32 bench firmware](../../../../../firmware/v8-device-executor-esp32/README.md) for
the pinned build, low-voltage reference wiring, local Stop, provisioning and
host fault tests. Actual board, sensor, relay, power and load acceptance must be
performed on the selected hardware. A firmware build or simulator result does
not establish physical GPIO or load operation.

Focused Engine tests live in `tests/network/test_device_executor.py`. The explicit
`tests/scripts/run_device_executor_live.py --live --allow-side-effects` harness
creates an isolated synthetic owner and TLS endpoint for the separate Android
fixture package. Its `/fixture/*` fault injection endpoints exist only in that
test server. Never expose this bench server or use it with private application data.
