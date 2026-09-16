# V8 Agent OS Engine

Engine owns session execution, Owner identity, device pairing, runtimes, memory, automation, MCP, skills, permissions, and recovery.

From this directory on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

On Linux/macOS:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python main.py
```

The default local address is `http://127.0.0.1:9530`. The `/v1` control plane is authenticated for local services; Phone uses the authenticated client gateway.

See the [Quick Start](../../docs/V8_AGENT_OS_QUICK_START_ZH.md), [API reference](../../docs/V8_AGENT_OS_API_REFERENCE_ZH.md), and [test map](tests/README.md).
