from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from PIL import Image, ImageColor

from core.artifact_store import artifact_store
from core.database import db
from core.workspace_capability import build_workspace_binding, resolve_workspace_tool_path
from erc.runtime_context import get_runtime_context
from runtimes.creative_media.image_analysis import analyze_image, compare_images, evaluate_quality_profile

__all__ = [
    "creative_media_alpha_inspect",
    "creative_media_image_compare",
    "creative_media_psd_inspect",
    "creative_media_psd_export_preview",
    "creative_media_psd_compose_template",
]

_PS_COMPATIBLE_MIME = "image/vnd.adobe.photoshop"
_DEFAULT_PSD_DIR = ".v8/creative-media/psd"
_MAX_MARKDOWN_ITEMS = 8


def _runtime_context() -> dict[str, Any]:
    try:
        return dict(get_runtime_context() or {})
    except Exception:
        return {}


def _compact_error(title: str, summary: str, *, next_action: str | None = None) -> str:
    lines = [f"### {title}", "", f"Status: blocked", "", "Result:", f"- {summary}"]
    if next_action:
        lines.extend(["", "Next:", f"- {next_action}"])
    return "\n".join(lines)


def _artifact_source_path(artifact_id: str) -> str | None:
    artifact = db.get_runtime_artifact(str(artifact_id or "").strip())
    if not artifact:
        return None
    for key in ("source_path", "sourcePath"):
        value = artifact.get(key)
        if value:
            return str(value)
    return None


def _workspace_label(path: Path, binding: Any | None = None) -> str:
    roots: list[Path] = []
    if binding is not None:
        for attr in ("active_workspace_root", "main_workspace_root"):
            root = getattr(binding, attr, None)
            if root:
                roots.append(Path(root).expanduser().resolve(strict=False))
    resolved = path.expanduser().resolve(strict=False)
    for root in roots:
        try:
            return str(resolved.relative_to(root)).replace("\\", "/")
        except Exception:
            continue
    return path.name


def _resolve_input_path(
    *,
    path: str | None = None,
    artifact_id: str | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> tuple[Path | None, str | None, str | None, Any | None]:
    context = runtime_context or _runtime_context()
    binding = build_workspace_binding(context, runtime_kind="creative_media")
    artifact = str(artifact_id or "").strip()
    if artifact:
        source = _artifact_source_path(artifact)
        if not source:
            return None, None, f"Artifact `{artifact}` was not found or has no local source path.", binding
        resolved = Path(source).expanduser().resolve(strict=False)
        return resolved, f"artifact `{artifact}`", None, binding
    raw_path = str(path or "").strip()
    if not raw_path:
        return None, None, "Provide `path` or `artifact_id`.", binding
    preflight = resolve_workspace_tool_path(raw_path, runtime_context=context, runtime_kind="creative_media")
    if not preflight.get("ok"):
        return None, None, str(preflight.get("summary") or preflight.get("error") or "Path is outside the active workspace."), binding
    resolved = Path(str(preflight.get("resolvedPath") or "")).expanduser().resolve(strict=False)
    return resolved, _workspace_label(resolved, binding), None, binding


def _safe_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return parsed if parsed > 0 else default


def _clean_layer_name(value: Any, index: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        text = f"Layer {index + 1}"
    return text[:80]


def _hex_to_rgba(value: Any, default: tuple[int, int, int, int] = (0, 0, 0, 0)) -> tuple[int, int, int, int]:
    text = str(value or "").strip()
    if not text or text.lower() in {"transparent", "none"}:
        return default
    try:
        rgba = ImageColor.getcolor(text, "RGBA")
        return tuple(int(item) for item in rgba)
    except Exception:
        return default


def _psd_tools_status() -> tuple[Any | None, Any | None, Any | None, str | None]:
    try:
        from psd_tools import PSDImage  # type: ignore
    except Exception as exc:
        return None, None, None, f"psd-tools is not installed: {exc}"
    try:
        from psd_tools.api.layers import PixelLayer  # type: ignore
    except Exception as exc:
        return PSDImage, None, None, f"psd-tools PixelLayer API is unavailable: {exc}"
    try:
        from psd_tools.constants import Compression  # type: ignore
    except Exception:
        Compression = None
    return PSDImage, PixelLayer, Compression, None


def _open_preview_image(source: Path) -> Image.Image:
    suffix = source.suffix.lower()
    if suffix == ".psd":
        PSDImage, _, _, error = _psd_tools_status()
        if error or PSDImage is None:
            raise RuntimeError(error or "psd-tools is unavailable")
        composite = PSDImage.open(str(source)).composite()
        return composite.convert("RGBA")
    return Image.open(source).convert("RGBA")


def _markdown_kv(title: str, rows: list[tuple[str, Any]], *, status: str | None = None, next_items: list[str] | None = None) -> str:
    lines = [f"### {title}", ""]
    if status:
        lines.append(f"Status: {status}")
        lines.append("")
    lines.append("Result:")
    for key, value in rows[:_MAX_MARKDOWN_ITEMS]:
        if value not in (None, "", [], {}):
            lines.append(f"- {key}: {value}")
    if next_items:
        lines.extend(["", "Next:"])
        for item in next_items[:_MAX_MARKDOWN_ITEMS]:
            lines.append(f"- {item}")
    return "\n".join(lines)


def _record_artifact(path: Path, *, workspace_path: str | None, title: str, metadata: dict[str, Any]) -> dict[str, Any]:
    context = _runtime_context()
    return artifact_store.record_local_file(
        file_path=path,
        session_id=str(context.get("session_id") or context.get("sessionId") or "") or None,
        run_id=str(context.get("run_id") or context.get("runId") or "") or None,
        workspace_path=workspace_path,
        metadata=metadata,
        source_component="creative_media.psd_tools",
        node="creative_media_psd_tools",
    ) | {"title": title}


def _default_output_path(binding: Any, stem: str, suffix: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", stem).strip("-") or "psd-output"
    digest = hashlib.sha1(f"{safe}:{suffix}".encode("utf-8")).hexdigest()[:8]
    root = Path(getattr(binding, "active_workspace_root", Path.cwd())) / _DEFAULT_PSD_DIR
    return root / f"{safe}-{digest}{suffix}"


@tool
def creative_media_alpha_inspect(path: str = "", artifact_id: str = "", expected_background: str = "auto") -> str:
    """Inspect whether an image/PSD has real alpha, solid background, or likely fake transparency. Returns concise Markdown."""

    source, label, error, binding = _resolve_input_path(path=path, artifact_id=artifact_id)
    if error or source is None:
        return _compact_error(
            "Creative Media Alpha Inspect",
            error or "Source was not resolved.",
            next_action="Provide a workspace-relative image/PSD path or a V8 artifact id.",
        )
    if not source.exists():
        return _compact_error(
            "Creative Media Alpha Inspect",
            f"`{label}` does not exist.",
            next_action="Generate or provide the asset first, then run alpha inspection again.",
        )
    try:
        report = analyze_image(source)
    except Exception as exc:
        return _compact_error(
            "Creative Media Alpha Inspect",
            f"Could not inspect `{label}`: {exc}",
            next_action="If this is a PSD, install psd-tools or export a PNG preview first.",
        )
    next_items = []
    alpha = dict(report.get("alpha") or {})
    subject = dict(report.get("subject") or {})
    if report.get("requiredFeaturePackId"):
        next_items.append("Install the 图像分析增强包, then rerun this inspection for complex opaque backgrounds.")
    elif alpha.get("status") != "true_alpha":
        next_items.append("Use non-destructive background cleanup or regenerate the asset with a real alpha channel.")
    else:
        next_items.append("This asset can be used as a PSD layer source.")
    return _markdown_kv(
        "Creative Media Alpha Inspect",
        [
            ("source", label),
            ("size", f"{report.get('width')}x{report.get('height')}"),
            ("alpha", alpha.get("status")),
            ("transparent pixels", alpha.get("transparentPixels")),
            ("translucent pixels", alpha.get("translucentPixels")),
            ("subject mask", subject.get("maskSource")),
            ("subject area", subject.get("areaRatio")),
            ("subject bounds", subject.get("bbox")),
            ("edge clipping", subject.get("touchesEdges")),
        ],
        status=str(alpha.get("status") or report.get("status") or "review_required"),
        next_items=next_items,
    )


@tool
def creative_media_image_compare(
    reference_path: str = "",
    reference_artifact_id: str = "",
    candidate_path: str = "",
    candidate_artifact_id: str = "",
    quality_profile: str = "character_reference",
) -> str:
    """Compare subject scale, position, margins, clipping, and alpha coverage across two images."""

    reference, reference_label, reference_error, _ = _resolve_input_path(
        path=reference_path,
        artifact_id=reference_artifact_id,
    )
    candidate, candidate_label, candidate_error, _ = _resolve_input_path(
        path=candidate_path,
        artifact_id=candidate_artifact_id,
    )
    if reference_error or reference is None:
        return _compact_error("Creative Media Image Compare", reference_error or "Reference image was not resolved.")
    if candidate_error or candidate is None:
        return _compact_error("Creative Media Image Compare", candidate_error or "Candidate image was not resolved.")
    try:
        result = compare_images(reference, candidate)
        comparison = dict(result.get("comparison") or {})
        evaluation = evaluate_quality_profile(
            dict(result.get("candidate") or {}),
            quality_profile,
            comparison=comparison,
        )
    except Exception as exc:
        return _compact_error("Creative Media Image Compare", f"Image comparison failed: {exc}")
    return _markdown_kv(
        "Creative Media Image Compare",
        [
            ("reference", reference_label),
            ("candidate", candidate_label),
            ("profile", evaluation.get("profile")),
            ("subject scale delta", comparison.get("areaRatioDelta")),
            ("subject position shift", comparison.get("centerShift")),
            ("bounding box overlap", comparison.get("bboxIoU")),
            ("clipping", comparison.get("clippingChange")),
            ("violations", evaluation.get("violations")),
        ],
        status=str(evaluation.get("status") or "review_required"),
        next_items=["Use the comparison evidence to preserve subject scale and composition in the next generation."],
    )


@tool
def creative_media_psd_inspect(path: str = "", artifact_id: str = "", max_layers: int = 40) -> str:
    """Inspect a PSD layer tree and return a compact Markdown layer summary. Requires psd-tools."""

    source, label, error, _binding = _resolve_input_path(path=path, artifact_id=artifact_id)
    if error or source is None:
        return _compact_error("Creative Media PSD Inspect", error or "Source was not resolved.", next_action="Provide a PSD path or artifact id.")
    if not source.exists():
        return _compact_error("Creative Media PSD Inspect", f"`{label}` does not exist.", next_action="Create or provide a PSD first.")
    PSDImage, _PixelLayer, _Compression, dependency_error = _psd_tools_status()
    if dependency_error or PSDImage is None:
        return _compact_error("Creative Media PSD Inspect", dependency_error or "psd-tools is unavailable.", next_action="Install psd-tools, then rerun the inspection.")
    try:
        psd = PSDImage.open(str(source))
        layer_limit = max(1, min(int(max_layers or 40), 200))
        layer_lines: list[str] = []
        for index, layer in enumerate(psd.descendants()):
            if index >= layer_limit:
                layer_lines.append(f"... {len(list(psd.descendants())) - layer_limit} more layers omitted")
                break
            name = _clean_layer_name(getattr(layer, "name", ""), index)
            visible = "visible" if getattr(layer, "visible", True) else "hidden"
            bbox = getattr(layer, "bbox", None)
            layer_lines.append(f"{index + 1}. {name} ({visible}, bbox={bbox})")
    except Exception as exc:
        return _compact_error("Creative Media PSD Inspect", f"Could not parse `{label}`: {exc}", next_action="Verify the file is a valid PSD.")
    return _markdown_kv(
        "Creative Media PSD Inspect",
        [
            ("source", label),
            ("canvas", f"{getattr(psd, 'width', '?')}x{getattr(psd, 'height', '?')}"),
            ("layers", len(list(psd.descendants()))),
            ("layer preview", "\n  " + "\n  ".join(layer_lines[:layer_limit])),
        ],
        status="readable",
        next_items=["Use creative_media_psd_export_preview for a flattened preview or creative_media_psd_compose_template to create a raster-layer PSD."],
    )


@tool
def creative_media_psd_export_preview(path: str = "", artifact_id: str = "", output_path: str = "", dry_run: bool = False) -> str:
    """Export a flattened preview PNG from an image/PSD. With dry_run=True, only reports the planned output path."""

    source, label, error, binding = _resolve_input_path(path=path, artifact_id=artifact_id)
    if error or source is None:
        return _compact_error("Creative Media PSD Export Preview", error or "Source was not resolved.", next_action="Provide a source image/PSD path or artifact id.")
    if output_path:
        preflight = resolve_workspace_tool_path(output_path, runtime_context=_runtime_context(), runtime_kind="creative_media")
        if not preflight.get("ok"):
            return _compact_error("Creative Media PSD Export Preview", str(preflight.get("summary") or preflight.get("error")), next_action="Choose an output path inside the active workspace.")
        target = Path(str(preflight.get("resolvedPath") or "")).expanduser().resolve(strict=False)
    else:
        target = _default_output_path(binding, source.stem + "-preview", ".png")
    if dry_run:
        return _markdown_kv(
            "Creative Media PSD Export Preview",
            [("source", label), ("planned output", _workspace_label(target, binding)), ("dry run", "yes")],
            status="planned",
            next_items=["Run again with dry_run=false to write the preview artifact."],
        )
    if not source.exists():
        return _compact_error("Creative Media PSD Export Preview", f"`{label}` does not exist.", next_action="Create or provide the source first.")
    try:
        image = _open_preview_image(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target, format="PNG")
        artifact = _record_artifact(
            target,
            workspace_path=str(getattr(binding, "active_workspace_root", "")) or None,
            title=target.name,
            metadata={"tool": "creative_media_psd_export_preview", "source": label},
        )
    except Exception as exc:
        return _compact_error("Creative Media PSD Export Preview", f"Could not export preview: {exc}", next_action="Inspect the source and dependency status, then retry.")
    return _markdown_kv(
        "Creative Media PSD Export Preview",
        [
            ("source", label),
            ("output", _workspace_label(target, binding)),
            ("artifact", artifact.get("artifactId")),
            ("content", artifact.get("contentUrl")),
        ],
        status="succeeded",
        next_items=["Use the preview artifact for human review; keep the PSD/source path for editable handoff."],
    )


@tool
def creative_media_psd_compose_template(request: dict[str, Any]) -> str:
    """Create a simple raster-layer PSD from image layers, plus a PNG preview. Use dryRun to validate the layer manifest first."""

    payload = dict(request or {})
    canvas = dict(payload.get("canvas") or {})
    layers = list(payload.get("layers") or [])
    context = _runtime_context()
    binding = build_workspace_binding(context, runtime_kind="creative_media")
    width = _safe_int(canvas.get("width"), 1024)
    height = _safe_int(canvas.get("height"), 1024)
    background = _hex_to_rgba(canvas.get("background"), (0, 0, 0, 0))
    dry_run = bool(payload.get("dryRun") if "dryRun" in payload else payload.get("dry_run"))
    if not layers:
        return _compact_error("Creative Media PSD Compose Template", "The layer manifest is empty.", next_action="Provide at least one layer with path/artifactId, name, x, and y.")

    planned_layers: list[str] = []
    resolved_layers: list[dict[str, Any]] = []
    for index, raw_layer in enumerate(layers[:60]):
        if not isinstance(raw_layer, dict):
            continue
        name = _clean_layer_name(raw_layer.get("name"), index)
        layer_path = str(raw_layer.get("path") or "").strip()
        layer_artifact = str(raw_layer.get("artifactId") or raw_layer.get("artifact_id") or "").strip()
        x = int(raw_layer.get("x") or raw_layer.get("left") or 0)
        y = int(raw_layer.get("y") or raw_layer.get("top") or 0)
        planned_layers.append(f"{index + 1}. {name} at ({x},{y})")
        if dry_run:
            continue
        source, label, error, _ = _resolve_input_path(path=layer_path, artifact_id=layer_artifact, runtime_context=context)
        if error or source is None:
            return _compact_error("Creative Media PSD Compose Template", f"Layer `{name}` was not resolved: {error}", next_action="Fix the layer source path/artifact id and retry.")
        if not source.exists():
            return _compact_error("Creative Media PSD Compose Template", f"Layer `{name}` source `{label}` does not exist.", next_action="Generate or attach the layer source first.")
        resolved_layers.append({"name": name, "source": source, "label": label, "x": x, "y": y, "visible": bool(raw_layer.get("visible", True))})

    output_path = str(payload.get("outputPath") or payload.get("output_path") or "").strip()
    preview_path = str(payload.get("previewPath") or payload.get("preview_path") or "").strip()
    psd_target = Path(output_path) if output_path else _default_output_path(binding, str(payload.get("name") or "layered-asset"), ".psd")
    if not psd_target.is_absolute():
        psd_target = Path(getattr(binding, "active_workspace_root", Path.cwd())) / psd_target
    png_target = Path(preview_path) if preview_path else psd_target.with_suffix(".png")
    if not png_target.is_absolute():
        png_target = Path(getattr(binding, "active_workspace_root", Path.cwd())) / png_target

    if dry_run:
        return _markdown_kv(
            "Creative Media PSD Compose Template",
            [
                ("canvas", f"{width}x{height}"),
                ("planned PSD", _workspace_label(psd_target, binding)),
                ("planned preview", _workspace_label(png_target, binding)),
                ("layers", "\n  " + "\n  ".join(planned_layers[:_MAX_MARKDOWN_ITEMS])),
                ("dry run", "yes"),
            ],
            status="planned",
            next_items=["Run again with dryRun=false after alpha inspection passes for each source layer."],
        )

    PSDImage, PixelLayer, Compression, dependency_error = _psd_tools_status()
    if dependency_error or PSDImage is None or PixelLayer is None:
        return _compact_error("Creative Media PSD Compose Template", dependency_error or "psd-tools is unavailable.", next_action="Install psd-tools, then run this tool again.")

    try:
        preview = Image.new("RGBA", (width, height), background)
        psd = PSDImage.new(mode="RGBA", size=(width, height), color=background)
        compression = getattr(Compression, "RLE", None) if Compression is not None else None
        for layer in resolved_layers:
            image = _open_preview_image(Path(layer["source"]))
            if not layer.get("visible", True):
                continue
            preview.alpha_composite(image, (int(layer["x"]), int(layer["y"])))
            kwargs: dict[str, Any] = {
                "layer_name": layer["name"],
                "top": int(layer["y"]),
                "left": int(layer["x"]),
            }
            if compression is not None:
                kwargs["compression"] = compression
            psd.append(PixelLayer.frompil(image, psd, **kwargs))

        psd_target.parent.mkdir(parents=True, exist_ok=True)
        png_target.parent.mkdir(parents=True, exist_ok=True)
        psd.save(str(psd_target))
        preview.save(png_target, format="PNG")
        workspace_path = str(getattr(binding, "active_workspace_root", "")) or None
        psd_artifact = _record_artifact(
            psd_target,
            workspace_path=workspace_path,
            title=psd_target.name,
            metadata={"tool": "creative_media_psd_compose_template", "layerCount": len(resolved_layers), "mimeHint": _PS_COMPATIBLE_MIME},
        )
        preview_artifact = _record_artifact(
            png_target,
            workspace_path=workspace_path,
            title=png_target.name,
            metadata={"tool": "creative_media_psd_compose_template", "role": "flattened_preview"},
        )
    except Exception as exc:
        return _compact_error(
            "Creative Media PSD Compose Template",
            f"PSD composition failed: {exc}",
            next_action="Verify psd-tools API compatibility and use PNG preview export as a temporary review artifact.",
        )

    return _markdown_kv(
        "Creative Media PSD Compose Template",
        [
            ("canvas", f"{width}x{height}"),
            ("PSD", f"{_workspace_label(psd_target, binding)} ({psd_artifact.get('artifactId')})"),
            ("preview", f"{_workspace_label(png_target, binding)} ({preview_artifact.get('artifactId')})"),
            ("layers", "\n  " + "\n  ".join(planned_layers[:_MAX_MARKDOWN_ITEMS])),
            ("preview content", preview_artifact.get("contentUrl")),
        ],
        status="succeeded",
        next_items=["Hand off the PSD artifact for editable source and the preview artifact for visual review."],
    )
