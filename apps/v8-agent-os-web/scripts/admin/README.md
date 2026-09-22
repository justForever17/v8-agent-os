# Admin Verification Scripts

These scripts exercise the Web-hosted Admin surface after the Admin migration.
They must be run from `apps/v8-agent-os-web` so the production build, `node_modules`, and isolated state paths resolve to the Web host.

- `admin-experience-fixture.mjs` is synthetic browser data only; it never reads user state.
- `sync-lobe-icons.mjs` refreshes the checked-in model icon assets and generated Admin catalog from `@lobehub/icons-static-svg`.
- `verify-*.mjs` and `verify-*.py` are targeted UI or contract checks. Scripts requiring `--live`, credentials, a built Web app, or a running Phone must say so at invocation and do not claim a real provider or device side effect from a fixture.

The old standalone Admin paths are retired. Routes used by these fixtures go through `/api/admin/*`; the Phone URLs in `verify-device-connect-ui.mjs` intentionally remain `/api/client/*` because they target the Phone surface.
