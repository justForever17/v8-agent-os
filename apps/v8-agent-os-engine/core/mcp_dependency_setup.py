"""Prepare catalog stdio launchers in owned directories, with process-scoped sources.

Package-manager scripts are external side effects; only our directory/config
publication is transactional. No shell text from a README is ever executed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from core.interprocess_lock import interprocess_file_lock
from core.mcp_connection_setup import connection_failure, process_mirror_environment
from core.process_launch import run_windowless_bounded
from core.v8_agent_os_paths import V8_AGENT_OS_HOME
from core.extensions_store_operations import checkpoint


def _run(argv: list[str], env: dict[str, str], cwd: Path) -> None:
    # Package tools never receive MCP runtime secrets. Diagnostics are classified
    # from an ephemeral stream, not stored in operation records or API output.
    with tempfile.TemporaryFile(mode="w+b") as output:
        result = run_windowless_bounded(argv, cwd=str(cwd), env=env, timeout=180,
                                       stdout=output, stderr=output, check=False)
        if result.returncode:
            output.seek(max(0, output.tell() - 16384))
            message = output.read().decode("utf-8", errors="replace")
            raise ValueError(connection_failure(RuntimeError(message)))


def prepare_stdio(config: dict[str, Any], *, target: str) -> dict[str, Any]:
    command = Path(str(config.get("command") or "")).stem.lower()
    if command not in {"npx", "uvx"}:
        return config
    args = [str(value) for value in config.get("args") or []]
    while args and args[0] in {"-y", "--yes"}:
        args.pop(0)
    executable_name = ""
    if command == "uvx" and len(args) > 2 and args[0] == "--from":
        specification, executable_name, args = args[1], args[2], args[3:]
    elif args:
        specification, args = args[0], args[1:]
    else:
        raise ValueError("本地 MCP 缺少包名。")
    pattern = r"(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9_.-]+(?:@[A-Za-z0-9_.-]+)?" if command == "npx" else r"[A-Za-z0-9_.-]+(?:==[A-Za-z0-9_.+-]+)?"
    if not re.fullmatch(pattern, specification) or specification.startswith(("-", ".")):
        raise ValueError("本地准备仅接受明确 npm/Python 包名；Git URL 或其他入口请使用已安装页配置。")
    mirror = process_mirror_environment(config)
    environment = {k: v for k, v in os.environ.items() if not re.search(r"token|secret|password|api.?key|authorization", k, re.I)}
    environment.update(mirror)
    environment.update(CI="1", NO_COLOR="1", UV_PYTHON_DOWNLOADS="never")
    package = specification.split("==")[0] if command == "uvx" else specification.rsplit("@", 1)[0] if "@" in specification[1:] else specification
    version = specification[len(package):].lstrip("@=") or "latest"
    integrity = ""
    if command == "npx":
        from core.extensions_store_service import _fetch_json
        registry = mirror.get("npm_config_registry", "https://registry.npmjs.org")
        environment["npm_config_registry"] = registry
        metadata = _fetch_json(f"{registry}/{quote(package, safe='@')}/{quote(version)}")
        version = str(metadata.get("version") or "")
        integrity = str((metadata.get("dist") or {}).get("integrity") or "")
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9_.-]+)?", version) or not integrity.startswith("sha512-"):
            raise ValueError("包源缺少可校验的固定版本与 integrity。")
        specification = f"{package}@{version}"
    digest = hashlib.sha256(json.dumps([target, command, specification, mirror], sort_keys=True).encode()).hexdigest()[:24]
    root = V8_AGENT_OS_HOME / "extensions" / "mcp-packages"
    root.mkdir(parents=True, exist_ok=True)
    destination = root / digest
    with interprocess_file_lock(root / f"{digest}.lock", timeout_seconds=240):
        receipt_path = destination / "v8-receipt.json"
        if receipt_path.is_file():
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if Path(receipt["executable"]).is_file():
                return {**config, "command": receipt["command"], "args": receipt["prefixArgs"] + args,
                        "x-v8-package-receipt": receipt}
        checkpoint("preparing_dependencies", can_cancel=False)
        with tempfile.TemporaryDirectory(prefix=".prepare-", dir=root) as temporary:
            staging = Path(temporary)
            if command == "npx":
                npm = shutil.which("npm") or shutil.which("npm.cmd")
                node = shutil.which("node")
                if not npm or not node:
                    raise ValueError("请先安装本机 Node.js 与 npm，再继续此操作。")
                _run([npm, "install", "--prefix", str(staging), "--no-audit", "--no-fund", "--save-exact", specification], environment, staging)
                lock = json.loads((staging / "package-lock.json").read_text(encoding="utf-8"))
                if (lock.get("packages", {}).get(f"node_modules/{package}", {}).get("integrity")) != integrity:
                    raise ValueError("已安装包与来源 integrity 不匹配。")
                package_dir = staging / "node_modules" / package
                manifest = json.loads((package_dir / "package.json").read_text(encoding="utf-8"))
                bins = manifest.get("bin")
                relative_bin = bins if isinstance(bins, str) else next(iter(bins.values())) if isinstance(bins, dict) and len(bins) == 1 else None
                if not relative_bin:
                    raise ValueError("包提供多个或没有可执行入口，请在已安装页明确选择。")
                binary = (package_dir / relative_bin).resolve()
                if not binary.is_relative_to(staging.resolve()) or not binary.is_file():
                    raise ValueError("包入口不在已验证安装目录内。")
                relative = binary.relative_to(staging.resolve())
                launch_command = node
                prefix_args = [str(destination / relative)]
                executable = prefix_args[0]
            else:
                uv = shutil.which("uv")
                if not uv:
                    raise ValueError("请先安装本机 uv，再继续 Python MCP 准备。")
                # Virtual environments contain absolute paths; build directly at
                # the owned final path, which is not referenced by config yet.
                staging = destination
                staging.mkdir(parents=True, exist_ok=True)
                environment.update(UV_TOOL_DIR=str(staging / "tools"), UV_TOOL_BIN_DIR=str(staging / "bin"))
                try:
                    _run([uv, "tool", "install", "--python", sys.executable, specification], environment, staging)
                except Exception:
                    shutil.rmtree(staging)
                    raise
                tool_name = executable_name or package
                if not re.fullmatch(r"[A-Za-z0-9_-]+", tool_name):
                    raise ValueError("Python MCP 可执行入口无效。")
                normalized = re.sub(r"[-_.]+", "-", package).lower()
                relative = Path("tools") / normalized / ("Scripts" if os.name == "nt" else "bin") / (tool_name + (".exe" if os.name == "nt" else ""))
                if not (staging / relative).is_file():
                    raise ValueError("Python 包没有生成指定 MCP 入口。")
                executable = str(destination / relative)
                launch_command, prefix_args = executable, []
                metadata_files = list((staging / "tools" / normalized).glob("**/*.dist-info/METADATA"))
                for metadata_file in metadata_files:
                    text = metadata_file.read_text(encoding="utf-8", errors="replace")
                    if re.search(rf"(?im)^Name: {re.escape(package)}$", text):
                        version = re.search(r"(?m)^Version: (.+)$", text).group(1)
                        break
            if command == "npx":
                staging.rename(destination)
            receipt = {"package": package, "version": version, "integrity": integrity, "source": "domestic" if mirror else "original",
                       "command": launch_command, "prefixArgs": prefix_args, "executable": executable}
            (destination / "v8-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
            return {**config, "command": launch_command, "args": prefix_args + args, "x-v8-package-receipt": receipt}
