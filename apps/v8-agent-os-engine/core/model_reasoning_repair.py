from __future__ import annotations

import shutil
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

from langchain_core.messages import HumanMessage

from core.json_safe import to_jsonable
from core.llm_factory import llm_factory
from core.model_control_plane import model_control_plane
from core.model_ref import make_model_ref
from core.reasoning_surface_contract import (
    detect_unverified_reasoning_field,
    evaluate_reasoning_payload,
    is_trusted_reasoning_surface,
    normalize_reasoning_surface,
    resolve_reasoning_surface_for_metadata,
)
from core.v8_agent_os_paths import CONFIG_JSON_PATH, V8_AGENT_OS_HOME


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _surface_signature(surface: Any) -> dict[str, Any]:
    normalized = normalize_reasoning_surface(surface)
    return {
        "mode": normalized.get("mode"),
        "trust": normalized.get("trust"),
        "requestStyle": normalized.get("requestStyle"),
        "displayKind": normalized.get("displayKind"),
        "responseFields": list(normalized.get("responseFields") or []),
    }


def _extract_reasoning_tokens(payload: Any) -> int:
    candidate = to_jsonable(payload)
    stack = [candidate]
    best = 0
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                normalized_key = str(key or "").lower()
                if normalized_key in {"reasoning_tokens", "reasoningtokens"}:
                    try:
                        best = max(best, int(value or 0))
                    except Exception:
                        pass
                elif normalized_key in {"completion_tokens_details", "completiontokensdetails"} and isinstance(value, dict):
                    try:
                        best = max(best, int(value.get("reasoning_tokens") or value.get("reasoningTokens") or 0))
                    except Exception:
                        pass
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(item for item in current if isinstance(item, (dict, list)))
    return best


def _extract_reasoning_preview(payload: Any, field: str) -> str:
    if not field:
        return ""
    current: Any = to_jsonable(payload)
    for part in field.split("."):
        if not part or part.startswith("content["):
            return ""
        if not isinstance(current, dict) or part not in current:
            return ""
        current = current.get(part)
    if isinstance(current, str):
        return current.strip()[:120]
    return ""


class ModelReasoningRepairService:
    """Run a live, low-budget model probe and persist the observed reasoning surface."""

    def __init__(self, *, control_plane: Any = model_control_plane, config_path: Path = CONFIG_JSON_PATH):
        self.control_plane = control_plane
        self.config_path = Path(config_path)

    def _resolve_metadata(self, model_id: str, *, provider_id: str = "") -> Dict[str, Any]:
        target_model_id = make_model_ref(provider_id, model_id) if provider_id and "::" not in model_id else model_id
        meta = llm_factory._resolve_model_metadata(target_model_id)  # noqa: SLF001 - internal service helper
        record = self.control_plane.get_model_record(target_model_id, provider_id=provider_id)
        if not meta.get("is_found") or not record:
            raise ValueError(f"模型 {model_id} 未在 models.json 中注册，或存在重名模型需要指定 Provider。")
        return {
            **meta,
            "provider_record": dict(record.get("provider") or {}),
            "model_record": dict(record.get("model") or {}),
        }

    def _run_probe(self, *, runtime_model_id: str) -> dict[str, Any]:
        started = time.perf_counter()
        client = llm_factory.create_chat_model(
            runtime_model_id,
            temperature=0,
            max_tokens=64,
            streaming=True,
            _role="reasoning_repair_probe",
        )
        prompt = (
            "If your API supports a separate reasoning field, use it briefly. "
            "Final answer must be exactly: OK"
        )
        payloads: list[Any] = []
        final_text_parts: list[str] = []
        streaming_used = True
        try:
            for chunk in client.stream([HumanMessage(content=prompt)]):
                payload = to_jsonable(chunk)
                payloads.append(payload)
                content = getattr(chunk, "content", "")
                if isinstance(content, str) and content:
                    final_text_parts.append(content)
        except Exception:
            streaming_used = False
            fallback_client = llm_factory.create_chat_model(
                runtime_model_id,
                temperature=0,
                max_tokens=64,
                streaming=False,
                _role="reasoning_repair_probe",
            )
            response = fallback_client.invoke([HumanMessage(content=prompt)])
            payloads.append(to_jsonable(response))
            content = getattr(response, "content", "")
            if isinstance(content, str) and content:
                final_text_parts.append(content)

        reasoning_tokens = max([_extract_reasoning_tokens(item) for item in payloads] or [0])
        return {
            "elapsedMs": round((time.perf_counter() - started) * 1000, 2),
            "payloads": payloads,
            "streamingUsed": streaming_used,
            "textPreview": "".join(final_text_parts).strip()[:120],
            "reasoningTokens": reasoning_tokens,
        }

    def _select_surface(self, meta: dict[str, Any], probe: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        resolved_surface = resolve_reasoning_surface_for_metadata(
            {
                "provider_id": meta.get("provider_id"),
                "model_id": meta.get("model_id"),
                "provider_record": meta.get("provider_record"),
                "model_record": meta.get("model_record"),
            }
        )
        payloads = list(probe.get("payloads") or [])
        matched: dict[str, Any] = {}
        for payload in payloads:
            decision = evaluate_reasoning_payload(resolved_surface, payload)
            if decision.get("accepted") and not decision.get("reasoningUnverified"):
                matched = decision
                break

        if is_trusted_reasoning_surface(resolved_surface):
            surface = {
                **normalize_reasoning_surface(resolved_surface),
                "source": "reasoning_repair_probe",
                "repairedAt": datetime.now(timezone.utc).isoformat(),
            }
            if matched.get("matchedField"):
                surface["probeMatchedField"] = matched.get("matchedField")
            return surface, {
                "status": "trusted_contract_observed" if matched else "trusted_contract_registered",
                "matchedField": matched.get("matchedField") or "",
            }

        for payload in payloads:
            field = detect_unverified_reasoning_field(payload)
            if field:
                return {
                    "mode": "provider_reasoning",
                    "trust": "adapter_verified",
                    "requestStyle": _safe_text(meta.get("api_standard") or "openai_compatible"),
                    "responseFields": [field],
                    "displayKind": "provider_reasoning",
                    "sourceRefs": [{"source": "reasoning_repair_probe"}],
                    "notes": "Registered from a live V8 reasoning repair probe. The field is separated from normal content.",
                    "source": "reasoning_repair_probe",
                    "repairedAt": datetime.now(timezone.utc).isoformat(),
                }, {
                    "status": "adapter_verified_field_observed",
                    "matchedField": field,
                    "reasoningPreview": _extract_reasoning_preview(payload, field),
                }

        if int(probe.get("reasoningTokens") or 0) > 0:
            return None, {"status": "no_visible_reasoning_field", "reasoningTokens": int(probe.get("reasoningTokens") or 0)}
        return None, {"status": "no_reasoning_signal"}

    def _backup_config(self) -> str:
        backup_dir = V8_AGENT_OS_HOME / "backups" / "model_reasoning_repair"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_dir / f"config-{stamp}.json"
        if self.config_path.exists():
            shutil.copy2(self.config_path, backup_path)
        else:
            backup_path.write_text("{}", encoding="utf-8")
        return str(backup_path)

    def _write_surface(self, *, provider_id: str, model_id: str, surface: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
        config = deepcopy(self.control_plane.get_config())
        provider_data = ((config.get("providers") or {}).get(provider_id) or {})
        models = provider_data.get("models") if isinstance(provider_data, dict) else {}
        if not isinstance(models, dict) or model_id not in models:
            raise ValueError(f"模型 {make_model_ref(provider_id, model_id)} 不存在，无法写入 reasoningSurface。")
        model_meta = dict(models.get(model_id) or {})
        old_surface = model_meta.get("reasoningSurface")
        old_signature = _surface_signature(old_surface)
        new_signature = _surface_signature(surface)
        if old_signature == new_signature and _safe_text((_as_dict(old_surface)).get("source")) == "reasoning_repair_probe":
            return {
                "saveStatus": "no_change",
                "oldReasoningSurface": old_surface,
                "newReasoningSurface": old_surface,
                "backupPath": "",
            }
        backup_path = self._backup_config()
        model_meta["reasoningSurface"] = surface
        models[model_id] = model_meta
        provider_data["models"] = models
        config["providers"][provider_id] = provider_data
        config.setdefault("reasoningSurfaceRepairs", [])
        if isinstance(config["reasoningSurfaceRepairs"], list):
            config["reasoningSurfaceRepairs"].append(
                {
                    "providerId": provider_id,
                    "modelId": model_id,
                    "oldMode": old_signature.get("mode"),
                    "newMode": new_signature.get("mode"),
                    "newTrust": new_signature.get("trust"),
                    "matchedField": decision.get("matchedField") or "",
                    "status": decision.get("status") or "saved",
                    "repairedAt": surface.get("repairedAt"),
                    "backupPath": backup_path,
                }
            )
        self.control_plane.save_config(config)
        return {
            "saveStatus": "saved",
            "oldReasoningSurface": old_surface,
            "newReasoningSurface": surface,
            "backupPath": backup_path,
        }

    def repair_reasoning_surface(
        self,
        *,
        model_id: str,
        provider_id: str = "",
        model_ref: str = "",
        persist: bool = True,
    ) -> Dict[str, Any]:
        runtime_model_id = _safe_text(model_ref or (make_model_ref(provider_id, model_id) if provider_id else model_id))
        if not runtime_model_id:
            raise ValueError("modelId or modelRef is required")
        meta = self._resolve_metadata(runtime_model_id, provider_id=provider_id)
        wire_model_id = _safe_text(meta.get("model_id") or model_id or runtime_model_id)
        model_ref = _safe_text(meta.get("model_ref") or runtime_model_id)
        provider_id = _safe_text(meta.get("provider_id") or provider_id or "unknown")
        model_type = _safe_text((meta.get("model_record") or {}).get("type") or "TEXT").upper()
        capability_class = _safe_text(meta.get("capability_class") or "")
        if capability_class in {"embedding", "reranker", "media_generation"} or model_type in {"EMBEDDING", "RERANK", "RERANKER", "MEDIA", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D"}:
            return {
                "ok": False,
                "status": "unsupported_model_type",
                "modelId": wire_model_id,
                "modelRef": model_ref,
                "providerId": provider_id,
                "message": "Reasoning repair only supports chat/text models.",
            }
        probe = self._run_probe(runtime_model_id=model_ref)
        surface, decision = self._select_surface(meta, probe)
        if not surface:
            return {
                "ok": True,
                "status": decision.get("status") or "no_reasoning_signal",
                "saveStatus": "no_change",
                "modelId": wire_model_id,
                "modelRef": model_ref,
                "providerId": provider_id,
                "latencyMs": probe.get("elapsedMs"),
                "streamingUsed": bool(probe.get("streamingUsed")),
                "reasoningTokens": probe.get("reasoningTokens") or 0,
                "message": "No separated reasoning field was visible in the probe response.",
            }
        if persist:
            write_result = self._write_surface(
                provider_id=provider_id,
                model_id=wire_model_id,
                surface=surface,
                decision=decision,
            )
        else:
            old_surface = deepcopy(meta.get("model_record") or {}).get("reasoningSurface")
            if (
                _surface_signature(old_surface) == _surface_signature(surface)
                and _safe_text(_as_dict(old_surface).get("source")) == "reasoning_repair_probe"
            ):
                write_result = {
                    "saveStatus": "no_change",
                    "oldReasoningSurface": old_surface,
                    "newReasoningSurface": old_surface,
                    "backupPath": "",
                }
            else:
                write_result = {
                    "saveStatus": "pending",
                    "oldReasoningSurface": old_surface,
                    "newReasoningSurface": surface,
                    "backupPath": "",
                }
        return {
            "ok": True,
            "status": decision.get("status") or "saved",
            "saveStatus": write_result.get("saveStatus"),
            "modelId": wire_model_id,
            "modelRef": model_ref,
            "providerId": provider_id,
            "latencyMs": probe.get("elapsedMs"),
            "streamingUsed": bool(probe.get("streamingUsed")),
            "reasoningTokens": probe.get("reasoningTokens") or 0,
            "matchedField": decision.get("matchedField") or surface.get("probeMatchedField") or "",
            "reasoningPreview": decision.get("reasoningPreview") or "",
            "oldReasoningSurface": write_result.get("oldReasoningSurface"),
            "newReasoningSurface": write_result.get("newReasoningSurface"),
            "backupPath": write_result.get("backupPath") or "",
            "message": (
                "Reasoning surface already matches the verified contract."
                if write_result.get("saveStatus") == "no_change"
                else "Reasoning surface repaired."
            ),
        }


model_reasoning_repair_service = ModelReasoningRepairService()
