# V8OS Cold-start Test Matrix

The CLI cold-start harness measures real local processes. It does not build Admin/Web and it does not replace the Electron Shell first-paint acceptance.

## Stage contract

| Stage | Clock origin | Passing contract |
| --- | --- | --- |
| `engine_spawn` | Immediately before invoking `v8os start` | CLI recorded a newly started, managed Engine PID and launch ID. |
| `engine_ready` | Immediately before invoking `v8os start` | `GET /readyz` returned 2xx JSON with `service=v8-agent-os-engine` and `ready=true`. |
| `admin_http_ready` | Immediately before invoking `v8os start` | `GET /login` returned 2xx HTML containing the login surface marker. |
| `web_http_ready` | Immediately before invoking `v8os start` | `GET /chat` returned 2xx HTML containing the Web application title marker. |
| `all_ready` | Immediately before invoking `v8os start` | Maximum elapsed time of the three HTTP readiness probes. |

The HTTP probes run concurrently so their measurements do not include artificial probe ordering. A generic 200 page, redirect to the wrong surface, or an open port without the expected payload does not pass.
Process/port preflight runs before this clock starts and is recorded separately as `preflight.durationMs`; it does not consume a startup performance budget.

## Matrix

| Layer | Command | Side effects | Acceptance |
| --- | --- | --- | --- |
| Unit and contract | `npm test` | None beyond test temp files. | Probe markers, retry behavior, option parsing, and fail-closed budgets pass. |
| Real local cold start | `npm run smoke:cold -- --mode start --json` | Starts Engine/Admin/Web, then stops only those three managed components. | All stages pass, Doctor has no failed check, cleanup confirms all three processes and ports stopped. |
| Performance regression gate | `npm run smoke:cold -- --mode start --engine-ready-budget-ms <ms> --admin-ready-budget-ms <ms> --web-ready-budget-ms <ms> --all-ready-budget-ms <ms> --json` | Same as real local cold start. | Every configured budget passes. Unconfigured budgets remain observational and never claim a performance pass. |
| Electron desktop observation | `v8os preview --rebuild` plus the desktop acceptance harness | Rebuilds/restarts the owned desktop preview stack. | Record build time separately from runtime startup and verify a strict same-origin product surface becomes interactive in the Shell: signed-out runs must report `admin-login`, while signed-in runs must report `web`. |

## Governance and recovery

- Preflight requires all three target components to be `stopped` and ports 9530/9528/9527 to be free. If not, the harness exits without stopping anything.
- Once the start attempt is issued, the `finally` path invokes scoped `v8os stop --only engine,admin,web` and verifies both process state and ports.
- Every harness-owned CLI subprocess uses the Windows hidden-window flag; cleanup polling must not flash a console window.
- Reports are written under `~/.v8-agent-os/reports/cli_base/<timestamp>/v8os_cli_cold_start.json` with schema version, stage timings, raw CLI steps, budget checks, and cleanup evidence.
- Establish machine-specific budgets from an accepted baseline run. Compare like-for-like `--mode start` runs; do not mix Next build time, development compilation, or a machine cold boot into the runtime budget.

## Windows acceptance baseline (2026-08-06)

Three consecutive production-mode runs on the acceptance host completed with
scoped cleanup:

| Run | Engine/all ready | Admin ready | Web ready | Engine internal ready | Module import | Episode runner |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `20260806_013811` | 10,612 ms | 4,703 ms | 3,177 ms | 9,645.83 ms | 6,309.00 ms | 1,392.14 ms |
| `20260806_013926` | 13,193 ms | 3,406 ms | 2,695 ms | 12,450.39 ms | 8,731.07 ms | 2,376.90 ms |
| `20260806_014024` | 10,314 ms | 3,481 ms | 2,756 ms | 9,475.07 ms | 6,980.55 ms | 1,210.64 ms |

The Engine/all-ready median is 10,612 ms. Compared with the pre-fix observed
15,204 ms run, the median is 30.2% lower. Episode-runner startup fell from
3,802.37 ms to a 1,392.14 ms median. The readiness contract is also stricter:
the queue must complete a real claim query before Engine readiness, and preview
waits up to 30 seconds for an actual same-origin product surface rather than only a control pipe. This is a failure boundary, not the 15-second performance budget. The accepted surfaces are Web `/chat`, Admin `/login`, and Admin `/admin`; startup/error/data URLs and other paths remain non-ready.

The explicit local regression gate is 15,000 ms for Engine/all-ready, 6,000 ms
for Admin, and 5,000 ms for Web. Run `20260806_014547` passed those budgets at
7,838 / 3,108 / 2,493 ms. These are machine-specific source-tree budgets, not
portable release guarantees.

The final desktop acceptance used `v8os preview --rebuild --json` after the
provider-compatibility patch was moved out of Python module import and into a
post-ready prewarm with a first-model-call fallback. Admin and Web production
builds plus the owned stack restart completed in 141.9 seconds; build time is
recorded separately and is not part of the Engine runtime budget. The rebuilt
Engine reported `readyMs=10,664.78`, including `moduleImportMs=2,355.62` and a
4,439.01 ms episode-runner start. The Shell control descriptor became ready on
the strict Web surface 8.337 seconds after descriptor creation. A Windows
visible-window monitor observed no new terminal window during the rebuild or
the five-second post-ready window; the final replay also found no visible
terminal window, while DPI-aware `PrintWindow` capture confirmed the complete
nonblank Web surface at 1920×1290 / 144 DPI.

Direct Playwright checks against the rebuilt services separated first content
from the Web application's intentional realtime connections: Web reached
DOMContentLoaded in 115 ms and exposed its primary task action in 582 ms; the
Admin login surface reached those points in 63 ms and 131 ms. Both returned 200
with no page or console errors. Electron `PrintWindow` capture at the actual
144-DPI backing size confirmed that the Shell rendered the same nonblank Web
surface; a screen-copy capture is not sufficient when another window owns the
foreground.
