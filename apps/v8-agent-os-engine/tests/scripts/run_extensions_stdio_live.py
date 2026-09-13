"""Explicit live package preparation and MCP tools call on a temporary directory."""
import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--live", action="store_true", required=True)
parser.add_argument("--allow-side-effects", action="store_true", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


async def exercise(config, directory):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    async with stdio_client(StdioServerParameters(command=config["command"], args=config["args"])) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool("list_directory", {"path": str(directory)})
            assert not result.isError
            assert "fixture.txt" in json.dumps(result.model_dump())
            return len(tools.tools)


with tempfile.TemporaryDirectory(prefix="v8-stdio-live-") as temporary:
    root = Path(temporary)
    os.environ["V8_AGENT_OS_HOME"] = str(root / "state")
    from core.mcp_dependency_setup import prepare_stdio
    directory = root / "allowed"
    directory.mkdir()
    (directory / "fixture.txt").write_text("synthetic fixture", encoding="utf-8")
    prepared = prepare_stdio({"type": "stdio", "command": "npx",
        "args": ["--yes", "@modelcontextprotocol/server-filesystem@2026.8.31", str(directory)],
        "x-v8-package-source": "domestic"}, target="isolated-live-filesystem")
    count = asyncio.run(exercise(prepared, directory))
    receipt = prepared["x-v8-package-receipt"]
    report = {"scope": "public npm mirror install, fixed package, real MCP initialize/list_tools/list_directory",
        "package": receipt["package"], "version": receipt["version"], "integrity": receipt["integrity"],
        "source": receipt["source"], "toolCount": count, "sideEffectScope": "temporary owned directory",
        "limitations": ["Windows only", "No private endpoint, hosted deployment, or external business API"]}
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
