# V8 Agent OS Web

Web is the chat, task, and workspace interface used by the desktop Shell. Its same-origin API proxies to Engine; Admin is an optional configuration surface.

Start the local Engine, then run from this directory:

```sh
npm ci
npm run dev
```

The launcher manages the local authentication setup; no project `.env` file is required. Web normally uses `http://127.0.0.1:9527`; use `v8os status --json` or `v8os open web` to find the managed address if that port is occupied.

For installation and the full desktop preview, see the [Quick Start](../../docs/V8_AGENT_OS_QUICK_START_ZH.md).
