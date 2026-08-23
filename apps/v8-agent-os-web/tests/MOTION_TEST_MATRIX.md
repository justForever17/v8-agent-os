# Cross-client motion acceptance matrix

Date: 2026-08-24

Scope: Web Workbench capture/resize, Web and Phone reduced motion, continuous-motion
budgets, Phone history/terminal behavior, and symmetric exit lifecycles. This matrix
separates executable behavior checks from source contracts and real-device checks.

| ID | Layer | Scenario | Command / evidence | Result |
| --- | --- | --- | --- | --- |
| MOT-DYN-01 | Dynamic unit | Resize frame coalescing, one commit, pointer ownership, recorder isolation, stale camera grant | `node --test apps/v8-agent-os-web/tests/workbench-motion-behavior.test.mjs` | PASS, 6/6 |
| MOT-DYN-02 | Dynamic unit | Cross-client visibility/reduced policy, finite frames, retained exit deadline, voice clock | `node --test apps/v8-agent-os-web/tests/cross-client-motion-behavior.test.mjs` | PASS, 7/7 |
| MOT-DYN-03 | Dynamic unit | Hidden control semantics, pinned terminal following, one scroll task per frame | `node --test apps/v8-agent-os-phone/tests/motion-interaction-behavior.test.mjs` | PASS, 3/3 |
| MOT-CON-01 | Source contract | Existing Web cross-client, collaboration, Canvas, and exit wiring contracts | Web `*.test.cjs` and `*.test.mjs` suite | PASS for motion scope |
| MOT-CI-01 | CI harness | Phone dynamic `.test.mjs` files run beside existing `.test.cjs` contracts | `.github/workflows/ci.yml` phone test step | PASS, wired |
| MOT-STATIC-01 | Static | Web TypeScript and i18n | `npm run typecheck`; `npm run validate:i18n` | PASS |
| MOT-STATIC-02 | Static | Phone TypeScript and i18n | `npm run typecheck`; `npm run validate:i18n` | PASS |
| MOT-BUILD-01 | Production build | Admin/Web production bundles and managed local clients | `v8os preview --rebuild` | PASS |
| MOT-BUILD-02 | Phone bundle | Android Hermes export | `npm run export:android` | PASS, 3572 modules |
| MOT-SMOKE-01 | Real local | Engine/Admin/Web/Shell managed Preview; HTTP health | `v8os status --json`; ports 9530/9528/9527 | PASS, all expected services running and HTTP 200 |
| MOT-BROWSER-01 | Browser | Reduced and standard computed styles, page errors, console errors, screenshots | `python apps/v8-agent-os-web/tests/motion_reduced_e2e.py` | PASS |
| MOT-BASE-01 | Baseline comparison | Full Web light suite | Current tree: 166/167; detached HEAD: 5/6 for the failing file | BASELINE FAIL, unrelated compact SSE contract |
| MOT-DOCTOR-01 | Dependency baseline | Expo SDK package alignment | `npm run doctor` | BASELINE FAIL, 20 existing patch-version mismatches |
| MOT-ROLLBACK-01 | Recoverability | Staged patch can be reversed cleanly | `git diff --cached --binary | git apply --check --reverse` | PASS |
| MOT-DEVICE-01 | Real device | Phone reduced-motion toggle, hidden history touch, terminal manual scroll, HUD exit | Physical Android/iOS device | NOT RUN, no device attached |
| MOT-PERF-01 | Real device | Foreground/background FPS, CPU/GPU, and battery comparison | Physical Android/iOS performance tooling | NOT RUN, no device attached |
| MOT-CAMERA-01 | Real hardware | Permission prompt, active recording, Workbench close, OS camera indicator release | Interactive browser with camera hardware | NOT RUN, headless smoke has no camera hardware |

## Browser readiness note

The `/chat` route keeps realtime requests open, so Playwright never observes global
`networkidle`. The browser harness records this as `networkIdleObserved: false` and
uses `load`, stylesheet readiness, `document.fonts.ready`, and two animation frames
before evaluating computed styles. It does not report this fallback as network idle.

## Baseline exceptions

- `turn-window-loading-contract.test.cjs` already fails at HEAD because the Web SSE
  route contains `surface=web` while the contract expects `surface=web&compact=1`.
- Expo Doctor already reports the installed Expo 55 packages one patch behind its
  current recommendations. Dependency versions were not changed by this motion work.

Dynamic unit tests and the browser smoke complement the existing regular-expression
contracts. They are not substitutes for the three explicitly unrun real-device and
real-camera rows above.
