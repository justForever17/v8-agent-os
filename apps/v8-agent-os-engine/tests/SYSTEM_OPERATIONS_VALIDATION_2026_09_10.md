# Controlled system operations — validation, 2026-09-10

Scope: independent Supervisor `system_operations` tool, Admin OS credential configuration,
separate Windows elevation/session-unlock components, ordinary minimal-mode actions and
concrete core protection. This work does not introduce a new runtime or Planner.

## Evidence by layer

| Layer | Verified behavior | Limits |
| --- | --- | --- |
| Engine contracts | 394 selected Safety/tool-access/schema/surface tests, 61 subtests passed; subsequent credential/identity/setup refinements passed 27 selected tests | Overlapping counts, not a claimed whole-repository total; one existing Starlette/httpx deprecation warning |
| Real framework callbacks | 67 transcript/tool-routing/validation tests passed; previous approval-error and unstable-ID implementation fails the new counterexamples | Uses installed LangChain/ToolNode, no provider billing |
| Windows native x64 | Unlock 75 real-pipe/protocol assertions + 21 Credential assertions; privilege 100 assertions; 20 CLI/installation dry-run tests | MSVC `/W4 /WX`; no ARM64 compiler/physical-host claim |
| OS credential store | Four synthetic Unicode/whitespace/quote/backslash/emoji strings round-tripped exactly; removed afterward. Save now compares the value in memory before changing the credential reference | Does not print values, digests or credential references; save does not authenticate a password |
| Windows physical elevation | Installed broker authenticated the configured local administrator, verified the actual child token, exit 0 and stopped process tree: 1188 ms | A bounded command, not proof of arbitrary business correctness or kernel isolation |
| Windows physical unlock | Explicitly locked local console; candidate client confirmed unlock in 329 ms; default installed client repeated the lock→unlock in 391 ms | Same-account local Windows session only. No Hello-only/MSA/AD/RDP/native ARM64 claim |
| Windows component recovery | Actual uninstall removed own service/provider, reinstall restored both, system provider registrations unchanged | Original report file needed elevated read; test report now explicitly grants its caller read access. Credentials untouched |
| Main uninstaller integration | 14 selected tests passed including actual NSIS hook compilation, UAC-cancel/false-receipt/locked-file fault boundaries, real 32/64-bit PowerShell component-plan equality and dry-runs; earlier 36 related Shell release/cleanup tests passed | Actual component recovery above is distinct from full-product uninstall. ARM64 selection is only a fixture contract |
| Real provider + Web | Manual approval first left operation `prepared`, no execution. After clicking the actual Web approval, same run/call completed once in 1468 ms; UI displayed one tool call and final result | A prior run exposed approval-as-failure and callback-ID drift; historical wrong events were not rewritten |
| Live/refresh parity | Fixed live and reloaded full chat viewport SHA-256 both `3eda74e37e36c727b9af8e7ca80b3531eecb1f927ee063d065cf21f679a4850b` | Owned synthetic acceptance conversation only |
| Admin UI | Production build, typecheck, 5201 i18n keys validated. Isolated production Admin browser test checks wrong-account message, current-account fill, exact submitted Unicode password, clearing, reload and independent profiles | OS API is faked only in this UI contract; real credential/API and OS authentication are separate tests above |
| Linux WSL | Eight real sudo/PAM cases: correct/wrong password, no password on app stdin, nonzero exit, deadline, long Chinese arguments, cancellation after output EOF, NOPASSWD; own account/rule removed | Linux WSL, not macOS. Cancellation-after-EOF sample: 56 ms |
| Preview/package preparation | Admin/Web production builds and `v8os preview --rebuild` passed; portable runtime/payload probe passed including native binaries. A child PATH without Git correctly reported optional degradation and passed | Payload preparation is not a clean NSIS install or a release CI run |

The two Web provider runs used the configured provider. In the final run, approval event
to resumed tool start was about 1.35 s; approval to tool completion about 3.10 s. Human
approval wait is excluded. These are individual observations, not a statistically valid
cross-version performance claim.

## Defects found by this work

- Fresh-database tests missed the existing schema fast path: bump schema 2→3, verify
  upgrade/reopen preserves the original session. New tables contain credential references
  and operation records, never the password.
- Late approval could revive a cancelled run. Approval checks now validate current run
  and owner; the executor repeats checks and atomically claims one operation.
- Ordinary `Remove-Item`, data-root names and words like `profile` were treated as hard
  safety evidence. Ordinary operations now follow the selected mode; concrete control,
  authentication and native execution files retain protection, including installed copies.
- Command core-write detection missed Set-Content, redirection and relative cwd. A bounded
  pure target parser is separate from the existing Guardian policy owner. Core denial is
  checked before preliminary allow/review. Arbitrary dynamic code remains outside static proof.
- Windows cmd returned a raw command line where elevation expected argv; final native cmd
  serialization now preserves approved nested quotes and is tested with a real child.
- Cancellation checks missed fast helpers and POSIX children closing stdout/stderr early.
- Windows PowerShell `-File -Confirm:$false` failed before installer execution; explicit
  `-Unattended` retains UAC and avoids the unsupported switch binding.
- A 32-bit cleanup helper resolved Program Files (x86), rejected the actual Provider path
  and returned 1603. Reading the native registry view produces the same component plan
  in both process architectures; both dry-runs now return 0. A missing COM dependency
  is also reported as setup failure instead of leaving the Admin card waiting forever.
- Ordinary callers could not query SYSTEM process handles/session IDs. Identity now uses
  SCM or WTS plus SYSTEM pipe ownership; it does not accept a name-only ordinary-user spoof.
- Immediate `DisconnectNamedPipe` discarded unread replies. Bounded receipt ACKs are covered
  by real-pipe failure/control experiments.
- Configured account name differed from the actual renamed Windows account. No-password
  account validation and current-account fill prevent silently accepting an unknown account.
- Approval control flow was labelled tool failure and callback UUIDs duplicated the card.
  The actual ToolCall ID now follows each invocation; expected approval is not an error.

## Reproduction and rollback

Run selected pytest from the Engine directory. Native README files contain exact x64/
ARM64 build and non-installing tests. `tests/scripts/run_system_operations_windows_live.py
--help` exposes explicit `--live --allow-side-effects` stages. Never run lock/authentication
against an unprepared user session; never pass the OS password through arguments or files.

UI tests run `npm run verify:admin-login-interaction` from Admin. Their temporary owner and
mock OS boundary are isolated from the real user's credentials. Source Preview uses the
existing CLI owner; its outer startup deadline now covers actual nested Shell stages.

Component rollback/removal uses only its own installer/registration and preserves system
sign-in providers. No forced LogonUI termination or global authentication policy change.
Before reverting to an Engine binary supporting schema 2, restore a pre-upgrade SQLite
backup while Engine is stopped; do not falsify `user_version` on a live database or delete
credential references to force an old binary to open it. New-version reopen is tested;
an old release installer is not claimed tested in this change.

## Remaining platform evidence

Native ARM64, macOS sudo, domain/passwordless account variants, remote sessions, and
privileged descendant cancellation across all device policies require matching hosts.
Phone uses the existing authenticated approval proxy with the operation-owner check;
there is no physical Phone tap/restore claim here. Whole-product clean install/uninstall
has not been run; NSIS integration and actual component uninstall/reinstall are separate
evidence above. OS credential storage and SQLite are separate systems, so this report
also does not claim atomic rollback across arbitrary OS-store or disk failures. This report does not claim a new
GitHub Actions run, published tag, full-repository green test suite or weaker-model Skill eval.
