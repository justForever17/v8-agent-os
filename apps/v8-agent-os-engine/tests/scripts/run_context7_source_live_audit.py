"""One opt-in Context7 documentation lookup through the actual V8 MCP manager.

Only the selected existing server configuration is projected into memory. The
client session is owned by this isolated process, not borrowed from Engine.
No model request, config writeback, unrelated MCP server, or raw docs logging.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from unittest.mock import patch
from urllib.parse import urlparse, urlunparse


QUERY = "LangChain Python create_agent tool calling: show the import and tools argument in a minimal example"


def document_summary(payload: dict) -> dict:
    text = str(payload.get("text") or "")
    urls = []
    for candidate in re.findall(r"https?://[^\s<>\]\)\"']+", text):
        parsed = urlparse(candidate)
        if parsed.hostname not in {"docs.langchain.com", "reference.langchain.com", "python.langchain.com", "github.com"}:
            continue
        if parsed.username or parsed.password:
            continue
        if parsed.hostname == "github.com" and not parsed.path.startswith("/langchain-ai/"):
            continue
        urls.append(urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", "")))
    checks = {"providerReturnedText": payload.get("ok") is True and bool(text),
              "officialSourceLink": bool(urls), "createAgentExample": "create_agent" in text and "tools" in text}
    return {"ok": all(checks.values()), "checks": checks, "textChars": len(text),
            "textSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "sourceUrls": list(dict.fromkeys(urls))[:8], "libraryId": payload.get("libraryId"),
            "failureClass": None if all(checks.values()) else "context7_document_unverified"}


async def run_audit(server_name: str, server_config: dict) -> dict:
    from core.storage import storage
    from runtimes.extensions.mcp.client import MCPManager
    from core.tools import research_broker

    manager = MCPManager()
    calls = []
    original_call = manager.call_tool

    async def call_once_per_step(**kwargs):
        if len(calls) >= 2:
            raise RuntimeError("context7_audit_two_calls_exhausted")
        entry = {"tool": kwargs["tool_name"], "argumentsShape": sorted(kwargs.get("arguments") or {})}
        calls.append(entry)
        started = time.monotonic()
        try:
            return await original_call(**kwargs)
        finally:
            entry["elapsedMs"] = int((time.monotonic() - started) * 1000)

    with patch.object(storage, "get_mcp_config", return_value={"mcpServers": {server_name: deepcopy(server_config)}}), \
         patch.object(research_broker, "mcp_manager", manager), patch.object(manager, "call_tool", side_effect=call_once_per_step):
        try:
            await manager.refresh_server(server_name)
            # refresh_server has marked this instance initialized; the current
            # Research helper reuses it without bootstrapping other servers.
            connected = server_name in manager.sessions
            if not connected:
                return {"ok": False, "failureClass": "context7_not_connected", "calls": calls}
            payload = await asyncio.wait_for(research_broker._call_context7_source_async(QUERY), timeout=40)
            return {**document_summary(payload), "calls": calls, "connectedServers": list(manager.sessions),
                    "route": "research_context7_source->MCPManager->configured_remote"}
        finally:
            await asyncio.wait_for(manager.cleanup(), timeout=8)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--mcp-config", default=str(Path.home() / ".v8-agent-os" / "mcp.json"))
    parser.add_argument("--server", default="context7")
    parser.add_argument("--isolated-root", required=True)
    args = parser.parse_args(argv)
    if not args.live:
        print("refused: --live is required before reading configuration or connecting MCP")
        return 2
    started = time.monotonic()
    try:
        selected = (json.loads(Path(args.mcp_config).read_text(encoding="utf-8")).get("mcpServers") or {}).get(args.server)
        if not isinstance(selected, dict) or selected.get("disabled"):
            raise ValueError("context7_server_unavailable")
        parsed = urlparse(str(selected.get("url") or ""))
        if parsed.scheme != "https" or parsed.hostname != "mcp.context7.com" or selected.get("command") or selected.get("oauth"):
            raise ValueError("context7_audit_requires_configured_official_remote")
        isolated = Path(args.isolated_root).resolve()
        isolated.mkdir(parents=True, exist_ok=False)
        os.environ["V8_AGENT_OS_HOME"] = str(isolated)
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        logging.disable(logging.CRITICAL)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            report = asyncio.run(run_audit(args.server, selected))
        report["elapsedMs"] = int((time.monotonic() - started) * 1000)
        (isolated / "context7-source-live-results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        report = {"ok": False, "failureClass": type(exc).__name__, "elapsedMs": int((time.monotonic() - started) * 1000)}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
