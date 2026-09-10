# V8OS controlled Windows system operations

An explicitly installed, local SYSTEM broker authenticates a configured **local
administrator account** and starts a bounded command under that account's verified
elevated primary token. Engine stays unelevated. The authenticated execution account
may differ from the Engine user; IPC callers are restricted to the SID selected at
installation. No Windows credential provider is modified by this component.

The Windows x64 development host completed real credential authentication and
verified administrator-token execution on 2026-09-10, including a real Supervisor
and Web approval/resume flow. Offline tests and compilation remain a different
evidence class; other architectures and account-policy combinations are unverified.

## Entry points

`v8-system-operations.exe --status` reports `registered`, `serviceRunning`,
`callerAllowed`, `available`, `elevated=false`, `verified=false`. A built executable
does not make `registered` or `available` true. Availability requires the caller's
SID and an authenticated pipe peer matching the SCM service PID and image.

`--execute` accepts exactly this UTF-8 JSON stdin contract, followed by EOF:

| Field | Contract |
| --- | --- |
| action | `run_privileged` |
| command | Original Engine approval description; **never parsed or executed here** |
| argv | 1–64 strings, including the absolute local executable as argv[0]; encoded command line below Windows' 32K limit |
| cwd | Absolute local working directory |
| timeoutSeconds | Integer, 5–600 seconds |
| username/domain/password | Explicit Admin credential; local account only, domain `.`/empty/local machine; password never a model argument |
| requestId | UUID operation ID |
| expiresAt | UTC Unix milliseconds, future and no more than 60 seconds away at acceptance |

Total request size is at most 256 KiB. Unknown/duplicate fields, malformed JSON,
NULs, unsupported actions, remote account names, service-account syntax, invalid
expiry, relative paths and excessive argv are rejected. Native quoting preserves
each argv value; cmd.exe `/d /s /c` preserves the approved final command text
without CRT quote escaping. There is no alternate-command fallback. Engine must
resolve argv[0]/cwd before hashing the approval payload. Credential expiry applies
to admission and launch, while `timeoutSeconds` bounds the running command.

The result uses `ok`, `code/status`, `executed` (nullable if unknown), `elevated`,
`verified`, `exitCode`, `stdout`, `stderr`, `outputTruncated`, `outputEncoding`,
`processTreeStopped`, `protocolVersion=1`. Only exit 0 under the verified target
administrator token, with process-tree cleanup confirmed, is `ok=true`.
`verified` describes that execution contract, not whether arbitrary user business
requirements were fulfilled. Caller code must inspect JSON, not the helper's exit
code: successful transport can carry a rejected or failed operation.

## Authentication and process boundaries

- The protected pipe grants SYSTEM and exactly the installed `ClientSid`, denies
  NETWORK and uses `PIPE_REJECT_REMOTE_CLIENTS`. Server impersonation verifies the
  caller SID; clients verify the pipe's SYSTEM ownership plus the live SCM service
  PID and exact registered executable path, cross-checked with the protected
  component registration, before sending a password. Ordinary clients need not
  open a SYSTEM process for its image; SCM owns that running-service identity.
- The service claims a short-lived UUID replay tombstone under its own protected
  registry key before authentication. Failed attempts also consume the ID. Engine
  retains its separate durable approval/credential-revision operation state.
- The SYSTEM service launches only its **fixed own executable** as a short-lived
  worker. User command strings are not part of that worker's argv/environment.
  Its 15-second authentication preparation deadline prevents synchronous LogonUser
  from hanging the service indefinitely. The outer job controls worker descendants.
- The worker uses LogonUserW(INTERACTIVE) once; a limited token may select its linked
  token. A duplicated primary token must have actual elevation, enabled (not
  deny-only) Administrators membership, high integrity and the authenticated SID.
  SYSTEM/LocalService/NetworkService are explicitly excluded. LogonUser success
  alone is insufficient.
- CreateProcessAsUserW creates the target **suspended**. It is assigned to a kill-on-
  close job, its actual process token is checked again, and only then is it resumed.
  Local-account authentication does not require the original Engine user to already
  be an administrator. Remote/domain account execution is not part of this contract.
- The target runs in background session 0, with stdin=NUL, an explicit small user
  environment and only its three standard handles inherited. It never receives
  the credential, service pipe or SYSTEM service environment. This is for bounded
  commands, not GUI interaction or detached service launch.
- Timeout, client disconnect and service stop kill the worker job; process-tree
  completion is queried, not inferred from merely requesting termination. Completed
  side effects cannot be rolled back by cancellation. Missing worker results expose
  `executed=null`/`outcome_unknown`, never a guaranteed zero-effect claim.
- Stdout/stderr are each capped at 64 KiB, further bytes are drained/discarded and
  truncation is explicit. Text is projected as UTF-8 with replacement for invalid
  bytes. Request/password/raw native exceptions are never printed. A command may
  itself access or print sensitive data under its approved administrator authority;
  Engine's concrete resource/Safety boundaries remain necessary. This broker is
  not a kernel filesystem sandbox or a promise that arbitrary scripts are harmless.

## Build and non-elevating tests

```powershell
./build.ps1 -Arch x64
./build.ps1 -Arch arm64
./build/x64/protocol-tests.exe
python -X utf8 ./test_privileged_client.py
./install.ps1 -Action Install -WhatIf
./install.ps1 -Action Uninstall -WhatIf
```

The matching VS 2022 C++ target compiler and Windows SDK must already be installed.
Build output is ignored under `build/<arch>/`. Reused JSON/handle/pipe/clock helpers
live in `../v8-native-common`; neither business protocol is merged with the other.
Changes there also require SessionUnlock's 75 protocol + 21 Credential checks and
9 CLI/installer dry-run cases.

Development validation (2026-09-10): x64 MSVC 14.44.35207 / SDK 10.0.26100.0 build
passed with `/W4 /WX /guard:cf`. The 100 native checks include actual local pipe
impersonation/spoof rejection, a reduced-privilege token, argv round-tripping and
termination of two owned **unelevated** processes. Eleven CLI/installation WhatIf
tests passed. Actual service installation and UAC cancellation were exercised.
Local administrator execution was verified in 1188 ms; two configured-provider
Web approval runs executed in 1641/1468 ms. The latter retained one tool identity,
one physical execution and the same rendered result after refresh. Removal and
restoration of the service/provider passed without changing system providers.
ARM64 needs the missing target compiler and physical machine. Broader token-policy,
replay-registry races and privileged descendant-cancellation cases remain separate
acceptance targets, not implied by successful local-account execution.

## Explicit installation and removal

Installation requires one authorized UAC elevation. `ClientSid` is the SID of the
**unelevated Engine user**, supplied by the calling Admin flow; do not take an
over-the-shoulder administrator's SID as the Engine identity.

```powershell
./install.ps1 -Action Install -Arch x64 -ClientSid <actual-Engine-user-SID>
./install.ps1 -Action Uninstall -Arch x64
```

Only `V8SystemOperations`, `HKLM\SOFTWARE\V8AgentOS\SystemOperations` and
`Program Files\V8AgentOS\SystemOperations` are managed. Service executable and
registry binding are not writable by ordinary users. Identical installation is
idempotent; explicit repair updates its own binary with rollback, while replacing
the caller identity requires explicit uninstall first. A service
with this name pointing to another image is refused. Uninstall stops its own job
and removes only its own service/config/files. `ClientPath` is recorded for Engine
discovery. Windows sign-in providers, UAC policy and OS accounts are unchanged.

Before production acceptance, use an owned disposable Windows VM: install with a
known local test administrator and a separately bound standard Engine user; prove
the actual child SID/elevation and a small owned-file side effect. Test wrong
password, a standard-account credential, foreign caller SID, malformed/replayed/
expired requests, service spoofing, disconnect during authentication and execution,
timeout with descendants, nonzero exit and excessive output. Uninstall and verify
that no service/children remain. Repeat on native ARM64; cross-compiling does not
prove ARM64 token/service behavior. Do not automatically retry authentication.

## Primary references

- [LogonUserW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-logonuserw): authenticates credentials; a returned token is not proof of elevation.
- [CreateProcessAsUserW](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessasuserw): primary-token, privilege, session and inherited-handle constraints.
- [Processes in the client security context](https://learn.microsoft.com/en-us/windows/win32/secauthz/processes-in-the-client-security-context): LocalSystem service use of explicit client tokens.
- [TOKEN_ELEVATION_TYPE](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ne-winnt-token_elevation_type): limited, full and default token distinctions.
- [GetTokenInformation](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-gettokeninformation): token facts and access requirements.
