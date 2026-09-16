# V8OS cold-start test guide

The cold-start harness observes managed local processes. It does not build Admin/Web or replace Electron Shell interaction checks.

## Current limitation

The harness currently requires Engine, Admin, and Web readiness but invokes the default `v8os start`, which now starts Engine and Web. Its three-service smoke is therefore not an acceptance gate for the current default startup. Do not interpret historical timings as current results. Use the CLI unit tests and verify the intended component set before running a service-starting harness.

## Reproducible checks

From the CLI directory, `npm test` exercises probe markers, option parsing, completion status, and cleanup contracts without starting the full desktop.

For actual desktop acceptance, `v8os preview --rebuild` rebuilds and restarts the source tree's owned preview services. Confirm that Engine and Web start, the Shell shows an interactive chat surface, and Admin starts when its configuration entry is opened. Keep build duration separate from runtime startup.

A readiness check must validate the expected service and response payload. A generic HTTP 200 page, open port, or control descriptor alone does not prove the intended surface is ready.

## Measurement and recovery

Use a clean, isolated environment and record the component set, build, mode, and hardware for every run. Compare sufficient samples under the same conditions before selecting a performance budget.

The existing cold-start harness requires target processes to be stopped and target ports to be free before execution. It records preflight separately and scopes cleanup to its managed components. Store reports outside Git under the configured report directory; never commit machine logs or state.
