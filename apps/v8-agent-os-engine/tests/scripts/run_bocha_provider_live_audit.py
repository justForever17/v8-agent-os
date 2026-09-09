"""Narrow, opt-in Bocha API smoke using one existing config profile.

The key is read only in memory and is never printed, persisted, or copied into
the engine's canonical config.  Without ``--live`` this script must not read
the config or import provider code.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlparse


def safe_result_summary(result: dict, elapsed_ms: int) -> dict:
    rows = result.get("results")
    valid = isinstance(rows, list) and bool(rows) and all(
        isinstance(row, dict) and row.get("source") == "bocha" and bool(row.get("title"))
        and urlparse(str(row.get("url") or "")).scheme in {"http", "https"}
        and bool(urlparse(str(row.get("url") or "")).hostname)
        for row in rows
    )
    accepted = result.get("ok") is True and valid
    return {"ok": accepted, "status": "ok" if accepted else "error",
            "statusCode": result.get("statusCode"), "resultCount": len(rows) if isinstance(rows, list) else 0,
            "elapsedMs": elapsed_ms, "failureClass": result.get("failureClass") if not result.get("ok") else (None if valid else "invalid_or_empty_results")}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="perform one real Bocha request")
    parser.add_argument("--config", required=True, help="read-only config JSON path")
    parser.add_argument("--query", default="LangChain latest release")
    args = parser.parse_args()
    if not args.live:
        print("refused: pass --live to read config or call Bocha")
        return 2
    config_path = Path(args.config).expanduser()
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        provider = (((config.get("systemBase") or {}).get("webFetch") or {}).get("providers") or {}).get("bocha") or {}
        api_key = str(provider.get("apiKey") or provider.get("credential") or provider.get("key") or "").strip()
        if not api_key:
            auth_env = str(provider.get("authEnv") or "").strip()
            import os
            api_key = str(os.environ.get(auth_env) or "").strip() if auth_env else ""
        if not api_key:
            print(json.dumps({"ok": False, "status": "unconfigured", "reasonCode": "bocha_api_key_missing"}))
            return 3
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from core.tools.bocha_provider import bocha_search
        started = time.monotonic()
        result = bocha_search(args.query, api_key=api_key, limit=3, timeout_seconds=15)
        summary = safe_result_summary(result, int((time.monotonic() - started) * 1000))
        print(json.dumps(summary, ensure_ascii=False))
        return 0 if summary["ok"] else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "status": "error", "failureClass": "harness_error", "reasonCode": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
