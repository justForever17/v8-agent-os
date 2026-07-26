from __future__ import annotations

from typing import Any


def preview_text(value: Any, *, limit: int = 400) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    if len(text) <= limit:
        return text
    head = max(1, int(limit * 0.65))
    tail = max(1, limit - head - 20)
    return f"{text[:head].rstrip()} ... {text[-tail:].lstrip()}"


def compact_dict(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in dict(payload or {}).items() if value not in (None, "", [], {})}


def limited_list(values: Any, *, limit: int = 6) -> list[Any]:
    if values is None:
        return []
    try:
        rows = list(values)
    except TypeError:
        rows = [values]
    return rows[: max(0, int(limit or 0))]


def count_nodes(value: Any) -> int:
    if isinstance(value, dict):
        return len(value) + sum(count_nodes(item) for item in value.values())
    if isinstance(value, list):
        return len(value) + sum(count_nodes(item) for item in value)
    return 1 if value not in (None, "") else 0


def artifact_summary(artifact: Any, *, detail: bool = False) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        return {}
    metadata = dict(artifact.get("metadata") or {})
    payload = {
        "artifactId": artifact.get("artifactId"),
        "canvasOperationId": metadata.get("canvasOperationId"),
        "kind": artifact.get("kind"),
        "mimeType": artifact.get("mimeType"),
        "title": artifact.get("title"),
        "sizeBytes": artifact.get("sizeBytes"),
    }
    if detail:
        payload.update(
            {
                "sourcePath": artifact.get("sourcePath") or artifact.get("path"),
                "previewUrl": artifact.get("previewUrl"),
                "contentUrl": artifact.get("contentUrl"),
                "externalUrl": artifact.get("externalUrl"),
            }
        )
    return compact_dict(payload)


def job_summary(job: Any) -> dict[str, Any]:
    if not isinstance(job, dict):
        return {}
    request = dict(job.get("request") or {})
    artifacts = list(job.get("artifacts") or [])
    artifact_ids = [
        str((artifact or {}).get("artifactId") or (artifact or {}).get("id") or "").strip()
        for artifact in artifacts
        if isinstance(artifact, dict)
    ]
    return compact_dict(
        {
            "jobId": job.get("jobId"),
            "canvasOperationId": job.get("canvasOperationId") or request.get("canvasOperationId"),
            "sourceId": job.get("sourceId") or request.get("sourceId"),
            "maskSourceId": job.get("maskSourceId") or request.get("maskSourceId"),
            "projectId": job.get("projectId"),
            "workspaceId": job.get("workspaceId"),
            "modality": job.get("modality") or request.get("modality"),
            "operationKind": job.get("operationKind") or request.get("operationKind"),
            "status": job.get("status"),
            "adapter": job.get("adapter"),
            "providerId": request.get("providerId") or job.get("providerId"),
            "model": request.get("model") or job.get("model"),
            "preset": request.get("preset"),
            "ratio": request.get("ratio"),
            "artifactCount": len(artifacts),
            "artifactIds": [item for item in artifact_ids if item][:3],
            "qualityStatus": job.get("qualityStatus"),
            "policyRejectReason": preview_text(job.get("policyRejectReason"), limit=140),
            "error": preview_text(job.get("error"), limit=140),
            "createdAt": job.get("createdAt"),
            "completedAt": job.get("completedAt"),
            "detailTool": (
                f"creative_media_jobs(action='get', request={{'jobId': '{job.get('jobId')}'}})"
                if job.get("jobId")
                else None
            ),
        }
    )


def job_detail(job: Any) -> dict[str, Any]:
    if not isinstance(job, dict):
        return {}
    request = dict(job.get("request") or {})
    provider_response = dict(job.get("providerResponse") or {})
    detail = job_summary(job)
    detail["workspacePath"] = job.get("workspacePath")
    detail["artifacts"] = [artifact_summary(item, detail=True) for item in list(job.get("artifacts") or [])[:4] if isinstance(item, dict)]
    detail["request"] = compact_dict(
        {
            "modality": request.get("modality"),
            "providerId": request.get("providerId"),
            "model": request.get("model"),
            "operationKind": request.get("operationKind"),
            "ratio": request.get("ratio"),
            "preset": request.get("preset"),
            "promptPreview": preview_text(request.get("prompt"), limit=700),
            "negativePromptPreview": preview_text(request.get("negativePrompt"), limit=280),
            "referenceAssetIds": limited_list(request.get("referenceAssetIds"), limit=8),
            "seed": request.get("seed"),
        }
    )
    if provider_response:
        detail["providerResponseSummary"] = {
            "keys": list(provider_response.keys())[:12],
            "nodeCount": count_nodes(provider_response),
        }
    return compact_dict(detail)


def catalog_presenter(
    catalog: dict[str, Any],
    *,
    modality: str | None = None,
    operation_kind: str | None = None,
    detail_level: str = "summary",
    limit: int = 20,
    cursor: str | None = None,
) -> dict[str, Any]:
    normalized_modality = str(modality or "").strip().lower()
    normalized_operation = str(operation_kind or "").strip().lower()
    normalized_detail = str(detail_level or "summary").strip().lower()
    effective_limit = max(1, min(int(limit or 20), 50))
    if normalized_detail == "summary" and not normalized_modality and not normalized_operation:
        effective_limit = min(effective_limit, 3)
    offset = 0
    try:
        offset = max(0, int(str(cursor or "0").strip() or "0"))
    except Exception:
        offset = 0

    modality_rows: dict[str, Any] = {}
    total_rows = 0
    for modality_key, providers in dict(catalog.get("modalities") or {}).items():
        key = str(modality_key or "").strip().lower()
        if normalized_modality and key != normalized_modality:
            continue
        filtered = []
        for provider in list(providers or []):
            if not isinstance(provider, dict):
                continue
            operation_kinds = [
                str(item).strip()
                for item in list(provider.get("operationKinds") or provider.get("operations") or [])
                if str(item).strip()
            ]
            if normalized_operation and operation_kinds and normalized_operation not in {item.lower() for item in operation_kinds}:
                continue
            filtered.append(provider)
        total_rows += len(filtered)
        rows = []
        for provider in filtered[offset : offset + effective_limit]:
            model_ids = list(provider.get("modelIds") or provider.get("models") or [])
            operation_kinds = [
                str(item).strip()
                for item in list(provider.get("operationKinds") or provider.get("operations") or [])
                if str(item).strip()
            ]
            rows.append(
                compact_dict(
                    {
                        "id": provider.get("id"),
                        "displayName": provider.get("displayName") or provider.get("name"),
                        "adapter": provider.get("adapter"),
                        "status": provider.get("status"),
                        "executable": provider.get("executable"),
                        "operationKinds": operation_kinds[:8 if normalized_detail in {"detail", "diagnostic", "full"} else 3],
                        "modelCount": len(model_ids),
                        "modelSamples": model_ids[:8 if normalized_detail in {"detail", "diagnostic", "full"} else 2],
                        "notes": limited_list(provider.get("notes"), limit=3)
                        if normalized_detail in {"detail", "diagnostic", "full"}
                        else [],
                    }
                )
            )
        modality_rows[key or str(modality_key)] = rows

    registry = catalog.get("mediaModelCapabilityRegistry")
    overrides = catalog.get("modelCapabilityOverrides")
    next_cursor = str(offset + effective_limit) if total_rows > offset + effective_limit else None
    if normalized_detail == "summary" and not normalized_modality and not normalized_operation:
        modality_summaries: list[dict[str, Any]] = []
        top_candidates: list[dict[str, Any]] = []
        for modality_key, providers in dict(catalog.get("modalities") or {}).items():
            provider_rows = [item for item in list(providers or []) if isinstance(item, dict)]
            executable_rows = [item for item in provider_rows if bool(item.get("executable"))]
            for provider in (executable_rows or provider_rows)[:3]:
                model_ids = list(provider.get("modelIds") or provider.get("models") or [])
                row = compact_dict(
                    {
                        "id": provider.get("id"),
                        "displayName": provider.get("displayName") or provider.get("name"),
                        "adapter": provider.get("adapter"),
                        "status": provider.get("status"),
                        "modelSamples": model_ids[:2],
                    }
                )
                if len(top_candidates) < 3 and bool(provider.get("executable")):
                    top_candidates.append({"modality": modality_key, **row})
            modality_summaries.append(
                compact_dict(
                    {
                        "modality": modality_key,
                        "providerCount": len(provider_rows),
                        "executableCount": len(executable_rows),
                    }
                )
            )
        return compact_dict(
            {
                "ok": True,
                "action": "creative_media_catalog",
                "detailLevel": normalized_detail,
                "summary": "Creative Media executable capability overview; use filtered detail for provider/model matrix.",
                "modalityCount": len(modality_summaries),
                "executableCandidateCount": sum(int(item.get("executableCount") or 0) for item in modality_summaries),
                "topCandidates": top_candidates,
                "modalities": modality_summaries,
                "catalogOnlyReminder": "Catalog-only providers are discovery hints, not executable adapters.",
                "detailTool": "creative_media_catalog(modality=..., operation_kind=..., detail_level='detail')",
            }
        )
    payload = compact_dict(
        {
            "ok": True,
            "action": "creative_media_catalog",
            "detailLevel": normalized_detail,
            "version": catalog.get("version"),
            "updatedAt": catalog.get("updatedAt"),
            "filters": compact_dict({"modality": normalized_modality or None, "operationKind": normalized_operation or None}),
            "limit": effective_limit,
            "cursor": str(offset) if offset else None,
            "nextCursor": next_cursor,
            "hasMore": bool(next_cursor),
            "runtimeAdapters": [
                compact_dict({"id": item.get("id"), "modalities": item.get("modalities"), "executable": item.get("executable")})
                for item in list(catalog.get("runtimeAdapters") or [])
                if isinstance(item, dict)
            ],
            "modalities": modality_rows,
            "catalogOnlyReminder": "music/model3d entries are catalog or planning surfaces unless an executable adapter says otherwise.",
            "detailTool": "creative_media_catalog(modality=..., operation_kind=..., detail_level='detail', cursor=nextCursor)",
        }
    )
    if normalized_detail in {"detail", "diagnostic", "full"}:
        payload = compact_dict(
            {
                **payload,
                "statusLevels": catalog.get("statusLevels"),
                "notes": limited_list(catalog.get("notes"), limit=6),
                "registrySummary": {
                    "type": type(registry).__name__,
                    "topLevelKeys": list(registry.keys())[:12] if isinstance(registry, dict) else [],
                    "nodeCount": count_nodes(registry),
                },
                "overrideSummary": {
                    "type": type(overrides).__name__,
                    "topLevelKeys": list(overrides.keys())[:12] if isinstance(overrides, dict) else [],
                    "nodeCount": count_nodes(overrides),
                },
            }
        )
    return payload
