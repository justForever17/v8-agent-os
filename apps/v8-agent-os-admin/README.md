# V8 Agent OS Admin

Admin provides optional configuration, observability, runtime governance, and system setup. Engine owns Owner identity, pairing, device credentials, and session execution; local chat and Phone do not need Admin to remain open.

Start the local Engine, then run from this directory:

```sh
npm ci
npm run dev
```

The launcher manages the Auth.js signing secret; no project `.env` file is required. The default address is `http://127.0.0.1:9528`.

For installation and the full desktop preview, see the [Quick Start](../../docs/V8_AGENT_OS_QUICK_START_ZH.md).
