# V8OS authorized Windows session unlock

Independent Windows V2 Credential Provider and local native client. The provider
submits an explicitly supplied password to Windows authentication; it neither
bypasses Windows authentication nor wraps, replaces, filters or disables system
credential providers. It runs inside LogonUI only; no SYSTEM service is installed.

The Windows x64 development host completed an installed lock → authentication →
unlock sequence on 2026-09-10. Compilation and non-installing tests alone are not
that proof. Other architectures, account types and device policies remain separate
compatibility targets.

## Contract

`v8-session-unlock.exe --status` reads the invoking process's session. It returns
`sessionId`, `registered`/`providerInstalled`, `locked` (boolean, or null if unknown),
`available`, `status`, `verified`, `submitted` and `protocolVersion=1`.
`available=true` requires a locked local active console session, matching process
user, registered component and a verified live LogonUI pipe endpoint. Merely having
a built executable is not an installation or readiness proof. Already-unlocked
status can be verified even when the provider is not installed.

`v8-session-unlock.exe --unlock` reads one bounded UTF-8 JSON object from stdin,
then EOF. Do not pass secrets through arguments, environment variables or files.
The exact six fields are:

| Field | Type and source |
| --- | --- |
| username | String; Engine's configured OS account, matching the WTS-observed account name; never a model argument |
| domain | String; the WTS-observed machine/domain, or empty to select that same account; arbitrary remote domains are not resolved |
| password | String; nonempty OS password, maximum 1024 UTF-16 code units |
| requestId | Canonical UUID operation ID; each authorization gets one ID |
| expiresAt | UTC Unix milliseconds, strictly future and at most 60 seconds away |
| sessionId | Integer from the current native status, verified again at execution |

The caller must close stdin and impose its own process deadline. CLI output is one
JSON line, with no username, password, path, raw authentication status or request
body. Exit 0 means a verified already-unlocked/unlocked action (or a readable status
query); rejected/unknown actions exit 2. Consumers must inspect the JSON operation
result, not just the exit code.

For unlock, `verified=true` is returned only after native WTS observation confirms
the original local console session and same account are unlocked. Receipt of a
request or `CredentialsChanged` is not success. Windows can reject passwords with
`authentication_failed`, `password_expired` or `account_restricted`; Hello-only or
passwordless accounts are not made password-capable by this DLL. An unresolved
account/unknown state remains an explicit failure. No credential rotation or retry
is performed automatically.

## Permission and cancellation

- Engine owns credential configuration, user/session/run/tool/credential-revision
  binding, approval and its durable operation state. A native request is an
  execution boundary, not a replacement for that authorization.
- Pipe: `\\.\pipe\V8AgentOS.SessionUnlock.v1.<sessionId>`. Its protected ACL grants
  SYSTEM and the session's exact user SID, denies NETWORK, and rejects remote pipe
  connections. Server impersonation validates client token SID and session. Before
  sending any secret, the client verifies the kernel pipe owner is SYSTEM, and the
  serving PID/session/name is the matching `LogonUI.exe` reported by WTS. Ordinary
  users need not open the SYSTEM process; its kernel-owned pipe remains mandatory.
- The provider supports CPUS_LOGON/CPUS_UNLOCK_WORKSTATION only when the active local
  console session already has a logged-in, locked user. V2 user-array SID must match.
  It exposes no manual password field and no general logon/other-user operation.
- Request UUID/expiry replay tombstones live in this component's protected registry
  key. Atomic key creation claims one request, including failed authentication.
  Entries expire; capacity is bounded. Failure to claim means no submission. This
  persists **no password, username or request body** and survives LogonUI recreation.
- One request is serialized at most once. Expiry, disconnected client and stop are
  checked before serialization returns to Windows; deselection clears pending data.
  Once submitted to LogonUI/LSA, Windows provides no supported retraction. A lost
  reply or cancellation after submission returns `outcome_unknown`, `verified=false`
  and `submitted=null` when submission cannot be established. Recheck native state
  before deciding whether a new authorization is necessary; never blindly retry.
- Sensitive buffers are cleared after use. This does not protect against a hostile
  administrator/SYSTEM debugger, kernel compromise, page/crash dumps, or an Engine
  that puts the secret into logs before launching this client.

## Build and non-installing tests

Use VS 2022 C++ Build Tools and Windows 10/11 SDK. The target compiler/library must
be installed for each architecture; the script does not download dependencies.
Builds also use the adjacent `../v8-native-common` source-only JSON/IPC helpers;
the produced DLL/client remain standalone and do not load a shared helper DLL.

```powershell
./build.ps1 -Arch x64
./build.ps1 -Arch arm64
./build/x64/protocol-tests.exe
./build/x64/provider-tests.exe
python -X utf8 ./test_session_unlock_client.py
./install.ps1 -Action Install -WhatIf
./install.ps1 -Action Uninstall -WhatIf
```

Output lives in ignored `build/<arch>/`; packaging must include the matching DLL
and client. Do not distribute the test executables. Protocol tests cover malformed
and duplicate fields, truncated JSON, Unicode, version, expiry and scope rejection.
Production Credential tests cover no request, invalid/repeated serialization,
disconnected client, deselection, failure mapping and unsupported scenarios. These
tests do not contact LSA with a password or change Windows login configuration.

Development validation: MSVC 14.44.35207 / SDK 10.0.26100.0 x64 `/W4 /WX` build;
75 protocol/real-pipe checks, 21 production Credential checks and 9 CLI/installer
dry-run cases passed. Real default installed-client unlock confirmed locked state
before and unlocked state after in 391 ms; a prior candidate-client run took 329 ms.
Component removal/restoration preserved the other Windows providers. Real tests
exposed and fixed ordinary-user process-query permissions and premature pipe
disconnect; bounded receipt ACKs retain results. DLL exports remain
`DllGetClassObject` and `DllCanUnloadNow`. ARM64 compiler/native-machine acceptance
is unavailable locally; no ARM64 success is claimed. Broader account/policy and
replay-registry race tests remain separate from this local-account physical proof.

## Explicit installation and recovery

Installation changes OS login configuration and requires a separately authorized
administrator operation. Keep at least one working Windows system sign-in option.
Use a disposable VM first, with console access and a known test OS password.

```powershell
./install.ps1 -Action Install -Arch x64
./install.ps1 -Action Uninstall -Arch x64
```

The script writes only its own CLSID registration
`{793345F6-C96B-472A-A780-387839440068}`, `HKLM\SOFTWARE\V8AgentOS\SessionUnlock`
and `Program Files\V8AgentOS\SessionUnlock`. It records `ClientPath` for discovery,
locks component files against ordinary-user modification and preserves all system
providers. Same-binary installation is idempotent; explicit repair replaces its
own files with recovery copies. A loaded DLL can require signing in normally
before repair; authentication processes are never killed. Failure rolls back its
own new registration. Uninstall
unregisters first; a DLL still held by LogonUI is retained until it can be removed.
It never kills authentication processes or forces a reboot.

For physical acceptance, record system-provider registration before/after; install
the candidate, verify status, lock only the owned VM console, then pass the test
password from an in-memory launcher. Assert the WTS same-user unlocked state and a
usable desktop. Cover wrong password, expired/repeated UUID, user switch, disconnect
before submission, missing component and native recovery sign-in; then uninstall
and verify recovery. Repeat on native ARM64 separately. Passwordless/Hello, AD/MSA,
RDP, lock-screen dismissal policy and LogonUI callback timing remain separate
physical compatibility cases; this component deliberately rejects remote sessions.

## Primary references

- [Microsoft V2 Credential Provider sample](https://github.com/microsoft/Windows-classic-samples/tree/main/Samples/CredentialProvider/cpp): user-array association and serialization contracts; this implementation does not vendor the sample UI.
- [Credential providers in Windows](https://learn.microsoft.com/en-us/windows/win32/secauthn/credential-providers-in-windows): retain system recovery providers; providers collect credentials, Windows enforces authentication.
- [CredentialsChanged](https://learn.microsoft.com/en-us/windows/win32/api/credentialprovider/nf-credentialprovider-icredentialproviderevents-credentialschanged): asynchronous re-enumeration for auto submission.
- [WTSINFOEX_LEVEL1_W](https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/ns-wtsapi32-wtsinfoex_level1_w): session lock state; Windows 7's reversed flags are outside this Windows 10+ target.
