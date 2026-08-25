# Project UI Workbench Test Matrix

Evidence date: 2026-08-25
Base HEAD: `2950bee348470f30ab2b00fa092b27df529feb28`

## Scope

The project workbench may start only the selected workspace project's existing `scripts.dev` through the governed terminal broker. It may write only a uniquely proven local source range inside the active workspace. Dynamic expressions, ambiguous mappings, dependency/build output, external URLs, and files outside the workspace remain read-only or rejected.

## Current Evidence

| Layer | Coverage | Result |
|---|---|---|
| STATIC | Python/Node syntax, Web typecheck, ESLint, i18n, `git diff --check` | PASS; ESLint has one pre-existing `<img>` warning and no errors |
| UNIT / CONTRACT | Project inspection and permission gate; fixed dev command; process cleanup; CSS Modules; Vue scoped style; React static inline style/text; dynamic style rejection; exact undo | PASS, Engine UI Patch `26 passed` |
| WEB CONTRACT | Project source entry points, explicit proxy allowlist, HMR bridge, bounded project reload, source proof and Human Surface | PASS, `16 passed` including existing Workbench contracts |
| REAL-LOCAL | Real governed terminal, Vite React dev server, authorized proxy/WebSocket, headless Edge, style HMR, text commit reload, two exact undos, process cleanup | PASS |
| BUILD | Web Next.js production build | PASS |
| PREVIEW | `v8os preview --rebuild`; Engine/Admin/Web/Shell start; desktop and 390px project-mode screenshots; no horizontal overflow, console error, or page error | PASS |
| REAL-LOCAL VUE/NUXT | Real Vue/Nuxt dev server HMR | NOT-RUN; local locked dependencies do not include Vue or `@vitejs/plugin-vue` |
| REAL-PROVIDER | External model/provider behavior | NOT-APPLICABLE |

## Reproduction

```powershell
$py = 'apps/v8-agent-os-engine/.venv/Scripts/python.exe'

& $py -m pytest apps/v8-agent-os-engine/tests/core/test_ui_patch.py -q
node --test `
  apps/v8-agent-os-web/tests/ui-patch-project-contract.test.cjs `
  apps/v8-agent-os-web/tests/workbench-human-surface-contract.test.cjs
npm --prefix apps/v8-agent-os-web run typecheck
npm --prefix apps/v8-agent-os-web run validate:i18n
npm --prefix apps/v8-agent-os-web run build

& $py apps/v8-agent-os-engine/tests/scripts/run_ui_patch_project_live.py --live
.\v8os.cmd preview --rebuild
& $py apps/v8-agent-os-web/tests/ui_patch_project_preview_live.py --live --output-dir <temporary-output-directory>
```

## Rollback

- Every source write prepares a durable before-image and hash-checked transaction before atomic replacement.
- Automatic undo refuses when the source hash has drifted.
- The real-local harness verifies React style and text rollback in the running page, then verifies both the proxy and owned dev terminal stop.
- Code rollback must remain possible through a normal revert of the scoped feature commit; no migration or persistent schema change is introduced.
