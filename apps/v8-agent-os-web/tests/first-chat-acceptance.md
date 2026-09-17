# First response acceptance

All output directories must be outside Git. Use isolated state and synthetic
workspaces; never copy production transcripts, cookies or plaintext credentials.

## Production React with controlled HTTP/SSE

Run `python apps/v8-agent-os-web/tests/prepare_resident_ui_fixture.py` from the
repository. This copies the actual React tree to `tmp/resident-web-ui`, replacing
only server authentication/profile actions; it does not replace chat projection.
Build there with `node node_modules/next/dist/bin/next build --webpack` and serve
with `node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 22827`.

Run `first_chat_ui.py --out <output>` with each `--scenario`:

- `normal`: create workspace/session, submit, consume a text event.
- `recorded-burst`: deliver user receipt and first text in one browser frame.
- `snapshot-before-event`: the canonical assistant snapshot precedes the delta.
- `failed`: accepted run fails before text; stale running snapshot follows.
- `delayed-instance`: hold the instance manifest through workspace/session
  creation; no editable composer or submission is allowed until identity arrives.

`--assert-fixed` checks stable message DOM identities, no third assistant,
transparent three-dot waiting state, error visibility, input recovery and reload.
`--reduced-motion` checks stationary dots. `--no-screenshots` is for repeated
timing runs; retain representative images once. The report records API counts,
DOM identity changes and event-to-DOM latency. These are controlled transport
tests, not real provider or installer evidence.

`resident_web_ui.py` separately performs 30 actual React session switches and
checks drafts, attachments, failed submit, stale queue snapshots and one active
session subscription. Shell `tests/resident-electron-live.cjs --live` exercises
the actual Electron resident registry/preload over synthetic HTTP surfaces.

## Production Web BFF and real Engine

Start `run_first_chat_upgrade_live.py` from Engine `tests/scripts/` with `--live`,
an explicit checkout, isolated state, and nonconflicting ports. Start the actual
production Web with the same `V8_AGENT_OS_HOME` using the repository managed
launcher; it obtains its local identity and bridge credentials normally.

`first_chat_live.py --live --url <web-url> --state <isolated-state> --out <output>`
creates a workspace/session through the UI, submits `你好`, observes real SSE,
and checks page identity, final history, draft reload and subscription count.
Model calls require the already governed model configuration. Use a few samples
for correctness; use the controlled fixture for repeated performance samples.

For deterministic failure, start the Engine launcher with `--fault-503` in a
fresh isolated state and run the browser harness with `--expect-failure`.
The endpoint proof must show an actual HTTP 503. An open provider circuit is a
different case: use `--expected-failure-text "Provider circuit"` and report it
separately. `--inspect-failed-session <id>` reopens an existing synthetic failure
without submitting again, verifies its persisted reason, restores the input for
editing, reloads, and checks the provider request count remains unchanged.

The Edit again action only restores an empty composer and focuses it. It never
automatically submits, resumes a run, or retries an outcome that is unknown.
Windows 11 source/Preview evidence does not establish Windows 10 upgrade or
installer correctness.
