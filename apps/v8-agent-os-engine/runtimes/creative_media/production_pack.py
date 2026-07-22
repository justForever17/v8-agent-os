from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


PRODUCTION_PACK_STAGES: tuple[str, ...] = (
    "brief",
    "proposal",
    "script",
    "scene_plan",
    "asset_manifest",
    "edit_decisions",
    "render_report",
    "final_review",
)

_STAGE_TITLES: dict[str, str] = {
    "brief": "Brief",
    "proposal": "Proposal",
    "script": "Script",
    "scene_plan": "Scene Plan",
    "asset_manifest": "Asset Manifest",
    "edit_decisions": "Edit Decisions",
    "render_report": "Render Report",
    "final_review": "Final Review",
}


def _text(value: Any, *, limit: int = 500) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    compact = re.sub(r"\s+", " ", raw)
    if len(compact) > limit:
        return compact[: max(0, limit - 1)].rstrip() + "..."
    return compact


def _as_list(value: Any, *, limit: int = 8) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    elif isinstance(value, tuple):
        items = list(value)
    else:
        items = [value]
    return [item for item in items if item not in (None, "", [], {})][: max(0, int(limit))]


def _safe_id(prefix: str, payload: dict[str, Any]) -> str:
    raw = str(payload.get("productionPackId") or payload.get("packId") or payload.get("id") or "").strip()
    if raw:
        return raw
    seed = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def _stage_payload(stage: str, value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = {"summary": value}
    if not isinstance(value, dict):
        value = {}
    status = str(value.get("status") or "draft").strip() or "draft"
    return {
        "stage": stage,
        "title": _text(value.get("title") or _STAGE_TITLES.get(stage) or stage, limit=80),
        "status": status,
        "summary": _text(value.get("summary") or value.get("content") or value.get("answer"), limit=900),
        "artifactRefs": _as_list(value.get("artifactRefs") or value.get("artifacts"), limit=12),
        "detailRefs": _as_list(value.get("detailRefs") or value.get("refs"), limit=12),
        "approvals": _as_list(value.get("approvals"), limit=6),
        "proof": _as_list(value.get("proof") or value.get("checks"), limit=8),
    }


def build_production_pack(request: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(request or {})
    stages = payload.get("stages") if isinstance(payload.get("stages"), dict) else {}
    provider_lock = payload.get("providerLock") if isinstance(payload.get("providerLock"), dict) else {}
    sample_approval = payload.get("sampleApproval") if isinstance(payload.get("sampleApproval"), dict) else {}
    pack: dict[str, Any] = {
        "productionPackId": _safe_id("cm_pack", payload),
        "title": _text(payload.get("title") or payload.get("goal") or "Creative Media Production Pack", limit=120),
        "goal": _text(payload.get("goal") or payload.get("brief") or payload.get("request"), limit=800),
        "stageOrder": list(PRODUCTION_PACK_STAGES),
        "providerLock": {
            "providerId": _text(payload.get("providerId") or provider_lock.get("providerId"), limit=120),
            "modelId": _text(payload.get("modelId") or provider_lock.get("modelId"), limit=160),
            "reason": _text(payload.get("providerLockReason") or provider_lock.get("reason"), limit=300),
        },
        "sampleApproval": {
            "status": _text(payload.get("sampleStatus") or sample_approval.get("status") or "required", limit=80),
            "sampleArtifactRefs": _as_list(payload.get("sampleArtifactRefs") or sample_approval.get("sampleArtifactRefs"), limit=12),
            "decisionRef": _text(payload.get("sampleDecisionRef") or sample_approval.get("decisionRef"), limit=200),
        },
        "artifactProof": _as_list(payload.get("artifactProof") or payload.get("proof"), limit=16),
        "referenceMedia": _as_list(payload.get("referenceMedia") or payload.get("references"), limit=16),
        "qa": dict(payload.get("qa") or {}),
    }
    for stage in PRODUCTION_PACK_STAGES:
        pack[stage] = _stage_payload(stage, payload.get(stage) or stages.get(stage))
    return pack


def _bullet(value: Any) -> str:
    if isinstance(value, dict):
        label = value.get("title") or value.get("name") or value.get("artifactId") or value.get("id") or value.get("ref") or value.get("path")
        ref = value.get("artifactId") or value.get("id") or value.get("ref")
        suffix = value.get("status") or value.get("kind") or value.get("modality") or value.get("type")
        text = _text(label or value, limit=160)
        if ref and str(ref) != str(label):
            text = f"{text} [{_text(ref, limit=80)}]"
        if suffix:
            text = f"{text} ({_text(suffix, limit=80)})"
        return text
    return _text(value, limit=180)


def production_pack_markdown(pack: dict[str, Any]) -> str:
    lines: list[str] = [
        "## CreativeMediaProductionPack",
        f"结果：生产包 `{pack.get('productionPackId')}` 已整理。",
    ]
    if pack.get("goal"):
        lines.append(f"目标：{pack['goal']}")
    lines.append("")
    lines.append("### 阶段")
    for stage in PRODUCTION_PACK_STAGES:
        item = pack.get(stage) if isinstance(pack.get(stage), dict) else {}
        status = item.get("status") or "draft"
        summary = item.get("summary") or "待补齐"
        lines.append(f"- {stage}: {status} - {summary}")
    provider = pack.get("providerLock") if isinstance(pack.get("providerLock"), dict) else {}
    provider_line = "未锁定"
    if provider.get("providerId") or provider.get("modelId"):
        provider_line = " / ".join(item for item in [provider.get("providerId"), provider.get("modelId")] if item)
    lines.extend(["", "### Provider Lock", f"- {provider_line}"])
    if provider.get("reason"):
        lines.append(f"- 原因：{provider['reason']}")
    approval = pack.get("sampleApproval") if isinstance(pack.get("sampleApproval"), dict) else {}
    lines.extend(["", "### Sample Approval", f"- 状态：{approval.get('status') or 'required'}"])
    for ref in _as_list(approval.get("sampleArtifactRefs"), limit=6):
        lines.append(f"- 样片：{_bullet(ref)}")
    lines.append("")
    lines.append("### Artifact Proof")
    proofs = _as_list(pack.get("artifactProof"), limit=8)
    if proofs:
        lines.extend(f"- {_bullet(item)}" for item in proofs)
    else:
        lines.append("- 还没有产物证明；生成后必须补 artifactId、文件类型、可播放/可打开检查。")
    lines.extend(
        [
            "",
            "### 回流要求",
            "- subagent 回流必须保留 providerLock、sampleApproval、artifactProof、qa 状态；缺任一项时只能标记为待补齐或降级，不能声称复杂媒体交付完成。",
            "",
            "下一步：先完成样片审批，再批量生成；最终交付前运行 `creative_media_quality(action='qa_check')`。",
        ]
    )
    return "\n".join(lines).strip()


def rank_candidates_markdown(
    candidates: list[dict[str, Any]],
    *,
    modality: str | None = None,
    operation_kind: str | None = None,
    goal: str | None = None,
    limit: int = 8,
) -> str:
    filtered: list[dict[str, Any]] = []
    modality_norm = str(modality or "").strip()
    operation_norm = str(operation_kind or "").strip()
    for item in candidates:
        if modality_norm and str(item.get("modality") or "") != modality_norm:
            continue
        if operation_norm and str(item.get("operationKind") or "") != operation_norm:
            continue
        filtered.append(dict(item))

    def _score(item: dict[str, Any]) -> tuple[int, int, str]:
        available = 1 if item.get("available") else 0
        executable = 0 if item.get("briefOnly") else 1
        return (available, executable, str(item.get("candidateId") or item.get("modelId") or ""))

    ranked = sorted(filtered, key=_score, reverse=True)[: max(1, min(int(limit or 8), 20))]
    lines = ["## Creative Media 模型选择", f"结果：找到 {len(filtered)} 个候选，展示前 {len(ranked)} 个。"]
    if goal:
        lines.append(f"目标：{_text(goal, limit=220)}")
    lines.append("")
    if not ranked:
        lines.append("没有匹配候选。下一步：检查 Model Hub 是否已接入对应媒体类型和操作类型。")
        return "\n".join(lines).strip()
    for index, item in enumerate(ranked, start=1):
        label = item.get("modelId") or item.get("candidateId") or "unknown-model"
        provider = item.get("providerName") or item.get("providerId") or "unknown-provider"
        operation = item.get("operationKind") or operation_norm or "unknown-operation"
        status = "可执行" if item.get("available") and not item.get("briefOnly") else "需确认"
        if item.get("briefOnly"):
            status = "仅 Brief"
        lines.append(f"{index}. {label} - {provider}")
        lines.append(f"   - 类型：{item.get('modality') or modality_norm or 'unknown'} / {operation}")
        lines.append(f"   - 状态：{status}")
        if item.get("modelRef"):
            lines.append(f"   - modelRef：{item['modelRef']}")
        if not item.get("available"):
            lines.append("   - 风险：当前配置不可用或缺少 provider 凭据。")
    lines.append("")
    lines.append("下一步：锁定一个 provider/model 后再生成样片；不要把完整模型目录当作交付结果。")
    return "\n".join(lines).strip()


def build_reference_media_pack(request: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(request or {})
    media = _as_list(payload.get("media") or payload.get("references") or payload.get("artifacts"), limit=24)
    return {
        "referencePackId": _safe_id("cm_ref", payload),
        "goal": _text(payload.get("goal") or payload.get("prompt"), limit=500),
        "media": media,
        "audioTranscript": _text(payload.get("audioTranscript") or payload.get("transcript"), limit=1200),
        "visualStyle": _text(payload.get("visualStyle") or payload.get("style"), limit=1000),
        "shotStructure": _text(payload.get("shotStructure") or payload.get("shots"), limit=1000),
        "reusableAssets": _as_list(payload.get("reusableAssets"), limit=16),
        "missing": [
            key
            for key in ("audioTranscript", "visualStyle", "shotStructure", "reusableAssets")
            if not payload.get(key) and not (key == "audioTranscript" and payload.get("transcript"))
        ],
    }


def reference_media_markdown(pack: dict[str, Any]) -> str:
    lines = ["## Reference Media Preflight", f"结果：参考媒体包 `{pack.get('referencePackId')}` 已整理。"]
    if pack.get("goal"):
        lines.append(f"目标：{pack['goal']}")
    lines.extend(["", "### 需要进入制作上下文的内容"])
    for key, label in (
        ("audioTranscript", "音频转写"),
        ("visualStyle", "视觉风格"),
        ("shotStructure", "镜头结构"),
    ):
        value = pack.get(key)
        lines.append(f"- {label}：{value or '待分析'}")
    assets = _as_list(pack.get("reusableAssets"), limit=8)
    if assets:
        lines.append("- 可复用素材：" + "；".join(_bullet(item) for item in assets))
    else:
        lines.append("- 可复用素材：待标注")
    media = _as_list(pack.get("media"), limit=8)
    if media:
        lines.extend(["", "### 引用"])
        lines.extend(f"- {_bullet(item)}" for item in media)
    missing = _as_list(pack.get("missing"), limit=8)
    if missing:
        lines.extend(
            [
                "",
                "下一步：先用 `vision_media_analyzer` 或文件读取工具补齐："
                + ", ".join(str(item) for item in missing),
                "参考媒体分析未补齐前，不要进入批量生成。",
            ]
        )
    return "\n".join(lines).strip()


def build_sample_approval_packet(request: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(request or {})
    media = _as_list(payload.get("media") or payload.get("artifacts") or payload.get("samples"), limit=12)
    questions = _as_list(payload.get("questions"), limit=8)
    if not questions:
        questions = [
            {
                "id": "sample_direction",
                "question": "请选择最适合继续批量制作的样片方向。",
                "type": "single",
                "options": [
                    {"id": "approve", "label": "通过这个方向"},
                    {"id": "revise", "label": "需要调整"},
                ],
                "allowCustom": True,
            }
        ]
    return {
        "question": _text(payload.get("question") or "确认样片方向", limit=180),
        "details": _text(payload.get("details") or payload.get("goal"), limit=800),
        "selection_mode": "multiple" if str(payload.get("selection_mode") or payload.get("selectionMode") or "").lower() == "multiple" else "single",
        "media": media,
        "questions": questions,
    }


def sample_approval_markdown(packet: dict[str, Any]) -> str:
    lines = [
        "## Sample Approval Packet",
        "结果：已生成可交给 `ask_user` 的样片确认包。",
        f"问题：{packet.get('question')}",
    ]
    if packet.get("details"):
        lines.append(f"说明：{packet['details']}")
    media = _as_list(packet.get("media"), limit=8)
    if media:
        lines.extend(["", "### 样片"])
        lines.extend(f"- {_bullet(item)}" for item in media)
    lines.extend(["", "### 调用 ask_user 时传入"])
    lines.append("- question: 上面的确认问题")
    lines.append("- details: 当前样片目标和限制")
    lines.append("- media/artifacts: 上方样片列表")
    lines.append("- selection_mode: " + str(packet.get("selection_mode") or "single"))
    lines.append("- questions: 保留多轮选择题；用户回答后再批量生成")
    lines.append("- productionPack: 用户决定写回 ProductionPack.sampleApproval，再继续批量生成")
    return "\n".join(lines).strip()


def _ffprobe_metadata(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"metadataStatus": "ffprobe_unavailable"}
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # pragma: no cover - host tool availability varies
        return {"metadataStatus": "ffprobe_failed", "error": _text(exc, limit=180)}
    if completed.returncode != 0:
        return {"metadataStatus": "ffprobe_failed", "error": _text(completed.stderr, limit=180)}
    try:
        payload = json.loads(completed.stdout or "{}")
    except Exception:
        return {"metadataStatus": "ffprobe_parse_failed"}
    streams = list(payload.get("streams") or [])
    video = next((item for item in streams if item.get("codec_type") == "video"), {})
    audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
    fmt = dict(payload.get("format") or {})
    return {
        "metadataStatus": "ok",
        "duration": fmt.get("duration"),
        "width": video.get("width"),
        "height": video.get("height"),
        "hasAudio": bool(audio),
        "streamCount": len(streams),
    }


def run_artifact_qa(request: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(request or {})
    artifacts = []
    surface_checks = []
    for item in _as_list(payload.get("artifacts") or payload.get("files"), limit=32):
        if isinstance(item, str):
            normalized = {"sourcePath": item}
            artifacts.append(normalized)
        elif isinstance(item, dict):
            normalized = dict(item)
            normalized["sourcePath"] = str(
                normalized.get("sourcePath") or normalized.get("path") or normalized.get("localPath") or ""
            ).strip()
            artifacts.append(normalized)
        else:
            continue
        source_path = str(normalized.get("sourcePath") or "")
        path = Path(source_path) if source_path else None
        surface_checks.append(
            {
                "title": _text(normalized.get("title") or normalized.get("artifactId") or source_path or "artifact", limit=160),
                "kind": str(normalized.get("kind") or normalized.get("modality") or (path.suffix.lstrip(".") if path else "file")),
                "path": source_path,
                "exists": bool(path and path.is_file()),
                "sizeBytes": path.stat().st_size if path and path.is_file() else 0,
            }
        )
    subtitle_checks = []
    for item in _as_list(payload.get("subtitles"), limit=12):
        path_text = str(item.get("path") if isinstance(item, dict) else item).strip()
        path = Path(path_text) if path_text else None
        subtitle_checks.append({"path": path_text, "exists": bool(path and path.is_file())})
    from runtimes.creative_media.runtime import creative_media_runtime

    quality_job = creative_media_runtime.create_quality_job(
        {
            **payload,
            "artifacts": artifacts,
            "autoRepair": bool(payload.get("autoRepair", False)),
        }
    )
    return {
        "qaReportId": quality_job.get("qualityJobId"),
        "status": quality_job.get("status"),
        "summary": quality_job.get("summary"),
        "qualityProfile": quality_job.get("qualityProfile"),
        "checks": surface_checks,
        "qualityChecks": list(quality_job.get("checks") or []),
        "subtitleChecks": subtitle_checks,
        "missingRequiredKinds": next(
            (
                list(item.get("missingKinds") or [])
                for item in list(quality_job.get("checks") or [])
                if item.get("name") == "required_artifact_kinds"
            ),
            [],
        ),
        "warnings": list(quality_job.get("warnings") or []),
        "failures": list(quality_job.get("failures") or []),
        "repairAttempts": list(quality_job.get("repairAttempts") or []),
        "requiredFeaturePackId": quality_job.get("requiredFeaturePackId"),
        "passed": quality_job.get("status") == "passed",
    }


def artifact_qa_markdown(report: dict[str, Any]) -> str:
    lines = [
        "## Creative Media QA",
        f"结果：{'通过' if report.get('passed') else '需要处理'}",
        f"- {report.get('summary') or '质量检查已完成。'}",
        "",
        "### 产物检查",
    ]
    checks = _as_list(report.get("checks"), limit=32)
    if not checks:
        lines.append("- 没有提供产物。")
    for item in checks:
        status = "存在" if item.get("exists") else "缺失"
        lines.append(f"- {item.get('title')}: {status}, {item.get('kind')}, {item.get('sizeBytes') or 0} bytes")
    quality_checks = _as_list(report.get("qualityChecks"), limit=32)
    if quality_checks:
        lines.extend(["", "### 质量门禁"])
    for item in quality_checks:
        state = "通过" if item.get("ok") else "需处理"
        label = item.get("name") or "检查项"
        lines.append(f"- {label}: {state}")
        if item.get("name") == "image_subject_analysis":
            subject = dict(item.get("subject") or {})
            lines.append(
                f"  - 主体占比 {subject.get('areaRatio', '—')}；位置 {subject.get('centroid', '—')}；裁切 {subject.get('touchesEdges') or '无'}"
            )
        elif item.get("name") == "media_duration_positive":
            lines.append(f"  - 时长 {item.get('durationSeconds', '—')}s")
        elif item.get("name") == "video_dimensions":
            lines.append(f"  - 分辨率 {item.get('width', '—')}x{item.get('height', '—')}")
        elif item.get("name") == "audio_stream_present":
            lines.append(f"  - {'含音频流' if item.get('ok') else '缺少音频流'}")
    if report.get("requiredFeaturePackId"):
        lines.extend(["", "### 需要能力增强", "- 安装图像分析增强包后可在本机完成复杂背景主体分割。"])
    missing = _as_list(report.get("missingRequiredKinds"), limit=8)
    if missing:
        lines.extend(["", "### 缺失关键产物"])
        lines.extend(f"- {item}" for item in missing)
    subtitle_checks = _as_list(report.get("subtitleChecks"), limit=12)
    if subtitle_checks:
        lines.extend(["", "### 字幕"])
        lines.extend(f"- {item.get('path')}: {'存在' if item.get('exists') else '缺失'}" for item in subtitle_checks)
    lines.append("")
    lines.append("下一步：缺失项先补齐；通过后再做最终交付说明。")
    return "\n".join(lines).strip()
