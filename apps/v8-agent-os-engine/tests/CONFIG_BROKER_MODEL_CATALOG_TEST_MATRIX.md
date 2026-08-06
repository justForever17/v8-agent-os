# Config Broker and Model Catalog Test Matrix

## Scope

This matrix covers the recoverable model configuration control plane and the
secret-free managed Model Hub overlay. It does not treat a catalog preset as a
connected runtime model, and it never performs a paid provider call by default.

Authority boundaries:

- `provider_catalog.json`: immutable built-in product catalog.
- `model_provider_catalog.managed.json`: Config Broker managed overlay.
- `model_provider_catalog.custom.json`: user custom provider overlay.
- model config plus OS credential references: connected runtime truth.
- live provider verification: explicit side-effecting acceptance only.

Precedence is `custom > managed > builtin > media-derived`.

## Required Matrix

| Area | Positive case | Negative or recovery case | Harness |
| --- | --- | --- | --- |
| Managed provider | Add and partially update provider, model, and channel by id | Reject duplicate ids, unsafe URLs, secrets, incomplete new providers, and OAuth path changes | `test_model_provider_catalog_managed.py` |
| Managed file failure | Runtime catalog continues with builtin/custom providers | Strict managed read reports invalid; verified backup restore retains exact original bytes and supports rollback | `test_model_provider_catalog_managed.py`, `test_config_broker_extended_contract.py` |
| Catalog projection | Preserve reasoning, media, retrieval, availability, source, and protocol facts | Missing HTTP(S) endpoint is explicitly non-connectable | catalog connection matrix |
| Existing credential | Exact endpoint, API standard, realm, transport, channel, and auth contract reuse the existing ref | Endpoint or auth drift requires a new secure UI action | `test_config_broker_extended_contract.py` |
| Credential replacement | Superseded refs are durably recorded in the private transaction and retained through the rollback window | A new target cannot silently drop a credential; stale-transaction cleanup remains retryable after restart | `test_config_broker_extended_contract.py`, `test_model_connection_tester.py` |
| Catalog discovery | Existing exact provider credential may perform provider discovery | Missing or stale credential is blocked before network I/O | mocked contract test; real provider is `--live` only |
| Discovery credential scope | A public model list may be previewed without a credential; an exact target or declared credential realm may reuse one | A stored key is never sent to an unrelated `modelsUrl`, including redirects | `test_config_broker_extended_contract.py`, `test_model_ref_control_plane.py` |
| Catalog connect | OAuth, no-auth, chat, media, embedding, and rerank records use one shared materializer | Unsupported wire protocol and placeholder endpoints are blocked during prepare | catalog and model-control tests |
| Verification | Chat uses the connection tester; media/retrieval use static runtime contract verification | A failed chat probe rolls back the target record | Config Broker control-plane tests |
| Model lifecycle | Enable, disable, or remove one unbound model | Bound model removal is blocked; sibling drift stops rollback | extended contract tests |
| Role lifecycle | Assign or unbind a role and registered Subagent | Unknown/ineligible roles or models are blocked | Config Broker tests |
| Internal role bundle | Memory and Config Registry can atomically assign and unbind multiple runtime roles | Stale model facts, partial projection, and restart recovery cannot leave a half-applied role set | `test_config_broker_role_bundle.py`, `test_config_broker_internal_model_writes.py` |
| Policy bundle | Update validated governance, budgets, routing, and role temperature | Unknown fields, invalid routes, and stale target revisions are blocked | extended contract tests |
| Human/Agent surface | Bounded provider and model ids remain visible | URLs, refs, keys, raw JSON, and managed local paths remain hidden | catalog surface tests |
| Native tool dispatch | Every prepare/list/discover mode forwards runtime owner/session/run identity | Pydantic-reserved `model_config` is not exposed; public name is `model_settings` | native dispatch tests |

## Reproducible Commands

From `apps/v8-agent-os-engine`:

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests/runtime_core/test_config_broker_control_plane.py `
  tests/runtime_core/test_config_broker_extended_contract.py `
  tests/runtime_core/test_config_broker_native_catalog_dispatch.py `
  tests/runtime_core/test_config_broker_catalog_surface.py `
  tests/runtime_core/test_config_broker_role_bundle.py `
  tests/api/test_config_broker_internal_model_writes.py `
  tests/core/test_model_provider_catalog_managed.py `
  tests/core/test_provider_catalog_assets.py `
  tests/model_control/test_model_catalog_connection_contract.py `
  tests/model_control/test_model_protocol_registry.py `
  tests/model_control/test_model_endpoint_binding.py `
  tests/model_control/test_model_ref_control_plane.py `
  tests/model_control/test_media_model_capability_registry.py `
  tests/model_control/test_model_runtime_auth_contract.py `
  tests/model_control/test_model_reasoning_repair.py `
  tests/model_control/test_model_connection_tester.py `
  tests/safety/test_cross_platform_network_safety.py -q

.\.venv\Scripts\python.exe tests/scripts/run_model_catalog_connection_matrix.py `
  --max-load-ms 2000 --max-duration-ms 5000
```

## Local Baseline

The full no-network catalog plan measured about 67.6 seconds before static media
projection caching. The final guarded rerun measured 1,608.9 ms total on the
current Windows acceptance host, with 352.9 ms spent loading the effective
catalog. This is a local dry-run result, not a provider benchmark; rerun the
harness rather than treating these machine-specific values as universal budgets.

The current matrix contains 111 providers and 255 model presets. Of those,
153 produce an executable V8OS connection plan and 102 are intentionally
blocked because the preset is catalog metadata only or has no configured
HTTP(S) endpoint. Unexpected planning failures remain a test failure. These
counts describe the checked-in catalog at the time of the run; the harness is
the authority when presets change.

## Live Acceptance

Real provider discovery and chat connection verification require an explicit
live run with an already configured credential. Media generation, image edits,
audio/video creation, and paid retrieval calls remain separate explicit live
acceptance. Mock, dry-run, and HTTP 200 results must not be reported as provider
success.

## Technical Debt

`supersededCredentialRefs` is the durable handoff for delayed credential
garbage collection. The current iteration intentionally does not delete an old
credential immediately after Provider replacement or removal because the same
transaction remains an exact rollback point. A later control-plane iteration
must add an explicit transaction finalize/retention policy that deletes only
superseded refs which are no longer referenced by any current Provider and are
outside the rollback window. Until then, these refs are recoverable but may
remain in the OS credential store longer than necessary.

The deprecated whole-model configuration writes in
`/models`, `/models/control-plane`, and Config Registry's `models` domain remain
compatibility-only bypasses. No current V8OS Admin page writes through them;
active Provider, binding, default, reasoning, policy, and runtime-role writes
use Config Broker transactions. A later deprecation cycle must add call-volume
telemetry, migrate any external caller, and then close these bulk writes. Runtime
observed embedding/rerank limits remain telemetry updates rather than operator
configuration transactions.
