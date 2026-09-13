"""Ordinary MCP setup reuses OS credential references and process-local mirrors."""
from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from core.security.credentials import credential_ref_store

_SECRET = re.compile(r"token|secret|password|api.?key|authorization|credential", re.I)


def secure_mcp_config(config: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    result = deepcopy(config)
    created: list[str] = []
    secret_env_names = result.pop("x-v8-secret-env-inputs", [])
    try:
        endpoint = result.pop("url", "")
        if endpoint == "********" and result.get("endpointRef"):
            endpoint = ""
        if endpoint:
            parsed = urlsplit(endpoint)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("MCP 连接地址必须是完整 HTTP/SSE 地址，不能包含用户名密码。")
            reference = credential_ref_store.put(endpoint)
            created.append(reference)
            result.update(endpointRef=reference, endpointHost=parsed.hostname)
        refs = result.setdefault("x-v8-credential-refs", {})
        for field, target in (("env", "env"), ("headers", "header")):
            for name, value in list((result.get(field) or {}).items()):
                bound = [key for key, binding in refs.items() if isinstance(binding, dict) and
                         binding.get("target") == target and binding.get("targetName") == name]
                if bound and (not value or value == "********"):
                    del result[field][name]
                    continue
                if value and (field == "headers" or _SECRET.search(name) or name in secret_env_names):
                    reference = credential_ref_store.put(str(value))
                    created.append(reference)
                    for key in bound:
                        refs.pop(key, None)
                    refs[f"{target}:{name}"] = {"target": target, "targetName": name, "secretRef": reference}
                    del result[field][name]
        return result, created
    except Exception:
        discard_credentials(created)
        raise


def discard_credentials(references: list[str]) -> None:
    for reference in references:
        credential_ref_store.delete(reference)


def process_mirror_environment(config: dict[str, Any]) -> dict[str, str]:
    if config.get("x-v8-package-source") != "domestic":
        return {}
    # Do not propagate private source authentication to a public mirror.
    env = config.get("env") or {}
    refs = config.get("x-v8-credential-refs") or {}
    if any(re.search(r"npm.*(token|auth)|uv.*(password|username)|pip.*(password|token)", str(k), re.I)
           for k in [*env, *refs]):
        raise ValueError("私有包凭据不能发往公共镜像，请选择原始源。")
    command = Path(str(config.get("command") or "")).name.lower().removesuffix(".cmd").removesuffix(".exe")
    args = " ".join(str(x) for x in config.get("args") or [])
    if "--registry" in args or "--index" in args:
        raise ValueError("该配置已指定包源，请先确认来源后再启用镜像。")
    if command in {"npm", "npx"}:
        return {"npm_config_registry": "https://registry.npmmirror.com"}
    if command in {"uv", "uvx"}:
        return {"UV_DEFAULT_INDEX": "https://pypi.tuna.tsinghua.edu.cn/simple"}
    if command in {"pip", "pip3"}:
        return {"PIP_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple"}
    return {}


def connection_failure(exc: BaseException) -> str:
    # Classify locally; never expose raw URL/headers/argv or exception chains.
    text = str(exc).lower()
    if "401" in text or "403" in text or "unauthorized" in text:
        return "连接授权无效或已过期，请在原服务刷新连接或凭据。"
    if "github.com" in text or "git+" in text:
        return "GitHub 源码或构建依赖不可达；包镜像不能代理此下载。"
    if any(s in text for s in ("postinstall", "node-gyp", "playwright", "binary", "wheel")):
        return "运行时或二进制依赖准备失败，请检查系统、架构与供应商下载源。"
    if any(s in text for s in ("npm", "registry", "pypi", "enotfound", "econnreset")):
        return "包源或依赖下载失败；请检查所选包源并重试，不会自动切源。"
    if isinstance(exc, FileNotFoundError):
        return "本机未找到所需 Node/Python/uv 运行时或命令。"
    return f"MCP 初始化或连接失败（{type(exc).__name__}），请检查入口、依赖和服务状态。"
