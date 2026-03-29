from __future__ import annotations

import argparse
from pathlib import Path
import sys


def _bootstrap_repo() -> None:
    current = Path(__file__).resolve()
    engine_root = current.parents[2]
    if str(engine_root) not in sys.path:
        sys.path.insert(0, str(engine_root))


_bootstrap_repo()

from core.storage import storage  # noqa: E402


DEFAULT_PROVIDER_ID = "local_vllm_rerank"
DEFAULT_PROVIDER_NAME = "本地 vLLM Rerank"
DEFAULT_MODEL_ID = "Alibaba-NLP/gte-multilingual-reranker-base"
DEFAULT_BASE_URL = "http://127.0.0.1:8012/v1"
DEFAULT_API_KEY = "local-vllm-rerank"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="注册本地 vLLM rerank provider/model 到 models.json")
    parser.add_argument("--provider-id", default=DEFAULT_PROVIDER_ID)
    parser.add_argument("--provider-name", default=DEFAULT_PROVIDER_NAME)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--bind-global-reranker", action="store_true")
    parser.add_argument("--bind-global-reranker-if-empty", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = storage.get_models_config() or {}
    providers = config.setdefault("providers", {})
    provider_id = str(args.provider_id).strip()
    model_id = str(args.model_id).strip()

    provider_payload = providers.setdefault(provider_id, {"provider": {}, "models": {}})
    provider_payload["provider"] = {
        **dict(provider_payload.get("provider") or {}),
        "name": str(args.provider_name).strip() or provider_id,
        "description": "本机独立 vLLM rerank 服务",
        "base_url": str(args.base_url).strip(),
        "api_key": str(args.api_key).strip(),
        "api_standard": "openai",
        "type": "LOCAL",
        "is_enabled": True,
    }
    provider_payload["models"] = {
        **dict(provider_payload.get("models") or {}),
        model_id: {
            **dict((provider_payload.get("models") or {}).get(model_id) or {}),
            "name": model_id,
            "type": "RERANK",
            "contextWindow": 8192,
            "maxTokens": 0,
            "temperature": 0.0,
            "priority": 50,
            "stabilityTier": "stable",
            "isEnabled": True,
            "capabilities": {"rerank": True},
            "capabilityClass": "reranker",
        },
    }
    if args.bind_global_reranker or args.bind_global_reranker_if_empty:
        roles = config.setdefault("roles", {})
        if args.bind_global_reranker:
            roles["reranker"] = model_id
        elif not str(roles.get("reranker") or "").strip():
            roles["reranker"] = model_id

    storage.save_models_config(config)
    print(f"[v8chat] 已注册 provider={provider_id} model={model_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
