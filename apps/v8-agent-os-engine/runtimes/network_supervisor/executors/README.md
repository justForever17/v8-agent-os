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

`android.observe` returns a bounded accessibility tree for an allowed app, marked
`partial` when incomplete. `android.action` supports node click, long click, set
text and scrolling, and pixel-coordinate `tap`/`swipe` anchored to a screenshot.
Node actions require the observed node-map revision; gestures require the actual
frame ID, geometry revision, viewport, rotation and dimensions. Both recheck the
device, boot, local control session, app and window. An observed frame expires for
actions after ten seconds and is superseded by the next observation.

`android.capture` captures an allowed window on Android 14+. Android 11–13 can
capture the display only with an explicit local opt-in and an additional
`android.capture/display` grant. Display scope can include other apps/system
chrome and cannot authorize gestures. Gestures require Android 14+ window capture
and a fresh secure-window probe. Password nodes, Phone's own approval/settings
surfaces and protected system surfaces are excluded. Platform screenshot/gesture
behavior remains subject to physical-device verification. The module shares
Phone's Android process and UID; arbitrary iOS cross-app control is unsupported.

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
| `POST /api/executor/media` | Executor; reserve one bounded JPEG for its current capture command |
| `PUT /api/executor/media/{id}` | Same executor; stream JPEG to the exact reserved handle |
| `DELETE /api/executor/media/{id}` | Same executor; discard unpublished media |
| `DELETE /api/client/executors/media/{id}` | Owned human; delete the captured image |
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

## Captured media

Android encodes one metadata-free sRGB baseline JPEG, longest edge at most 1600
pixels and at most 2 MiB. WSS carries only frame references. The HTTPS upload
checks the executor identity, current command, lease, grants, cancellation,
deadline, exact manifest and content hash. Server validates JPEG markers,
dimensions and transport integrity without claiming to decode image pixels.
Only a matching successful receipt publishes a session-owned artifact through
the existing artifact store. Device credentials cannot read human artifacts.

Capture status returns `screenshotRef` and the artifact's existing authenticated
content URL. Pass its exact `filePath` to `vision_media_analyzer`; this uses the
existing model selection, image ordering, permissions and budget. A persisted
upload record and session ownership qualify the native JPEG path; a caller flag,
filename or forged artifact metadata cannot qualify it. Minimal Server needs no
Pillow/NumPy for this path. Ordinary media input still requires its media pack.

Unpublished media expires at the command deadline. Published images expire
after 24 hours, are immediately unreadable at expiry or explicit deletion, and
expired bytes are reaped on the next reservation. The per-device stored-image
budget is 64 MiB. Local Stop cancels capture/upload work, but cannot retract
already transmitted bytes or an already dispatched platform gesture. A failed,
cancelled or stale callback cannot publish a new screenshot.

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

`tests/network/test_executor_media.py` covers upload, ownership, cancellation,
frame drift, deletion and existing visual-tool dispatch. Run
`tests/scripts/run_executor_media_server_smoke.py --live --require-no-imaging --output <isolated-directory>`
in a Server environment to exercise real loopback HTTPS/WSS, tool/episode,
artifact and model-input construction with synthetic device/model boundaries.
It never contacts a phone or provider. These layers do not prove Android
screenshot/gesture behavior or a real model's interpretation.
