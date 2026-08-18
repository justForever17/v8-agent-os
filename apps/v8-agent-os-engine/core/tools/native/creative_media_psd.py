from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from PIL import Image, ImageColor

from core.artifact_store import artifact_store
from core.creative_media_resource_authority import (
    CreativeMediaResourceAuthorityError,
    creative_media_resource_authority,
)
from core.workspace_capability import build_workspace_binding, resolve_workspace_tool_path
from erc.runtime_context import get_runtime_context
from runtimes.creative_media.image_analysis import analyze_image, compare_images, evaluate_quality_profile

__all__ = [
    "creative_media_alpha_inspect",
    "creative_media_image_compare",
    "creative_media_psd_inspect",
    "creative_media_psd_export_preview",
    "creative_media_psd_compose_template",
    "compose_psd_document",
    "edit_psd_document",
    "inspect_psd_manifest",
    "render_psd_preview_image",
]

_PS_COMPATIBLE_MIME = "image/vnd.adobe.photoshop"
_DEFAULT_PSD_DIR = ".v8/creative-media/psd"
_MAX_MARKDOWN_ITEMS = 8
_MAX_PSD_LAYERS = 200
_MAX_PSD_DIMENSION = 32768
_MAX_PSD_PIXELS = 268_435_456


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
    scope = {
        "session_id": str(context.get("session_id") or context.get("sessionId") or "").strip(),
        "workspace_id": str(context.get("workspace_id") or context.get("workspaceId") or "").strip(),
        "project_id": str(context.get("project_id") or context.get("projectId") or "").strip(),
        "workspace_path": str(context.get("workspace_path") or context.get("workspacePath") or "").strip(),
    }
    artifact = str(artifact_id or "").strip()
    raw_path = str(path or "").strip()
    if not artifact and not raw_path:
        return None, None, "Provide `path` or `artifact_id`.", binding
    try:
        if artifact:
            authorized = creative_media_resource_authority.resolve_artifact(
                artifact_id=artifact,
                require_local=True,
                **scope,
            )
            return authorized.path, f"artifact `{artifact}`", None, binding
        authorized = creative_media_resource_authority.resolve_path(path=raw_path, **scope)
        return authorized.path, _workspace_label(authorized.path, binding), None, binding
    except CreativeMediaResourceAuthorityError:
        return None, None, "Media resource is not available in the current session scope.", binding


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


def _bounded_dimension(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(1, min(parsed, _MAX_PSD_DIMENSION))


def _bounded_percent(value: Any, default: float = 100.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    return max(0.0, min(parsed, 100.0))


def _assert_canvas_bounds(width: int, height: int) -> None:
    if width * height > _MAX_PSD_PIXELS:
        raise ValueError("PSD canvas exceeds the governed pixel limit")


def _layer_children(layer: Any) -> list[Any]:
    try:
        return list(layer) if bool(layer.is_group()) else []
    except (AttributeError, TypeError):
        return []


def _layer_manifest(layer: Any, *, layer_path: str, parent_path: str, index: int, counter: list[int], limit: int) -> dict[str, Any]:
    if counter[0] >= limit:
        raise ValueError(f"PSD exceeds the governed layer limit of {limit}")
    counter[0] += 1
    bbox = getattr(layer, "bbox", None)
    bounds = [int(value) for value in bbox] if bbox is not None else [0, 0, 0, 0]
    children = [
        _layer_manifest(
            child,
            layer_path=f"{layer_path}/{child_index}",
            parent_path=layer_path,
            index=child_index,
            counter=counter,
            limit=limit,
        )
        for child_index, child in enumerate(_layer_children(layer))
    ]
    blend_mode = getattr(layer, "blend_mode", "")
    return {
        "layerPath": layer_path,
        "parentPath": parent_path,
        "index": index,
        "layerId": getattr(layer, "layer_id", None),
        "name": _clean_layer_name(getattr(layer, "name", ""), index),
        "kind": "group" if children or bool(getattr(layer, "is_group", lambda: False)()) else str(getattr(layer, "kind", "pixel") or "pixel"),
        "visible": bool(getattr(layer, "visible", True)),
        "opacityPercent": round(float(getattr(layer, "opacity", 255) or 0) * 100.0 / 255.0, 2),
        "left": bounds[0],
        "top": bounds[1],
        "right": bounds[2],
        "bottom": bounds[3],
        "width": max(0, bounds[2] - bounds[0]),
        "height": max(0, bounds[3] - bounds[1]),
        "blendMode": str(getattr(blend_mode, "value", blend_mode) or "normal"),
        "children": children,
    }


def inspect_psd_manifest(source: Path, *, max_layers: int = _MAX_PSD_LAYERS) -> dict[str, Any]:
    """Return a bounded, structured PSD layer tree for trusted UI/runtime consumers."""

    PSDImage, _PixelLayer, _Compression, dependency_error = _psd_tools_status()
    if dependency_error or PSDImage is None:
        raise RuntimeError(dependency_error or "psd-tools is unavailable")
    psd = PSDImage.open(str(source))
    width = int(getattr(psd, "width", 0) or 0)
    height = int(getattr(psd, "height", 0) or 0)
    _assert_canvas_bounds(width, height)
    limit = max(1, min(int(max_layers or _MAX_PSD_LAYERS), _MAX_PSD_LAYERS))
    counter = [0]
    layers = [
        _layer_manifest(layer, layer_path=str(index), parent_path="", index=index, counter=counter, limit=limit)
        for index, layer in enumerate(list(psd))
    ]
    return {
        "schema": "v8.creative_media.psd_manifest.v1",
        "width": width,
        "height": height,
        "colorMode": str(getattr(getattr(psd, "color_mode", ""), "name", getattr(psd, "color_mode", "")) or ""),
        "depth": int(getattr(psd, "depth", 0) or 0),
        "layerCount": counter[0],
        "layers": layers,
    }


def render_psd_preview_image(source: Path) -> Image.Image:
    image = _open_preview_image(source)
    _assert_canvas_bounds(image.width, image.height)
    return image


def _layer_image(raw_layer: dict[str, Any]) -> Image.Image:
    image = _open_preview_image(Path(raw_layer["source"]))
    requested_width = raw_layer.get("width")
    requested_height = raw_layer.get("height")
    scale_percent = _bounded_percent(raw_layer.get("scalePercent"), 100.0)
    if requested_width in (None, "") and requested_height in (None, ""):
        width = _bounded_dimension(round(image.width * scale_percent / 100.0), image.width)
        height = _bounded_dimension(round(image.height * scale_percent / 100.0), image.height)
    elif requested_width in (None, ""):
        height = _bounded_dimension(requested_height, image.height)
        width = _bounded_dimension(round(image.width * height / max(1, image.height)), image.width)
    elif requested_height in (None, ""):
        width = _bounded_dimension(requested_width, image.width)
        height = _bounded_dimension(round(image.height * width / max(1, image.width)), image.height)
    else:
        width = _bounded_dimension(requested_width, image.width)
        height = _bounded_dimension(requested_height, image.height)
    _assert_canvas_bounds(width, height)
    if (width, height) != image.size:
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    opacity = _bounded_percent(raw_layer.get("opacityPercent"), 100.0)
    if opacity < 100.0:
        alpha = image.getchannel("A").point(lambda value: round(value * opacity / 100.0))
        image.putalpha(alpha)
    return image


def compose_psd_document(
    *,
    output_path: Path,
    preview_path: Path,
    canvas: dict[str, Any],
    layers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compose ordered raster sources into a real PSD and flattened preview."""

    if not layers or len(layers) > 60:
        raise ValueError("PSD composition requires between 1 and 60 layers")
    width = _bounded_dimension(canvas.get("width"), 1920)
    height = _bounded_dimension(canvas.get("height"), 1080)
    _assert_canvas_bounds(width, height)
    background = _hex_to_rgba(canvas.get("background"), (0, 0, 0, 0))
    PSDImage, PixelLayer, Compression, dependency_error = _psd_tools_status()
    if dependency_error or PSDImage is None or PixelLayer is None:
        raise RuntimeError(dependency_error or "psd-tools is unavailable")
    psd = PSDImage.new(mode="RGBA", size=(width, height), color=background)
    compression = getattr(Compression, "RLE", None) if Compression is not None else None
    for index, raw_layer in enumerate(layers):
        image = _layer_image(raw_layer)
        x = max(-_MAX_PSD_DIMENSION, min(int(raw_layer.get("x") or 0), _MAX_PSD_DIMENSION))
        y = max(-_MAX_PSD_DIMENSION, min(int(raw_layer.get("y") or 0), _MAX_PSD_DIMENSION))
        kwargs: dict[str, Any] = {"top": y, "left": x}
        if compression is not None:
            kwargs["compression"] = compression
        layer = PixelLayer.frompil(image, psd, **kwargs)
        layer.name = _clean_layer_name(raw_layer.get("name"), index)
        layer.visible = bool(raw_layer.get("visible", True))
        layer.opacity = round(_bounded_percent(raw_layer.get("opacityPercent"), 100.0) * 255.0 / 100.0)
        psd.append(layer)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    psd.save(str(output_path))
    psd.composite().convert("RGBA").save(preview_path, format="PNG")
    return inspect_psd_manifest(output_path)


def _index_psd_layers(psd: Any) -> dict[str, tuple[Any, Any]]:
    indexed: dict[str, tuple[Any, Any]] = {}

    def visit(container: Any, prefix: str) -> None:
        for index, layer in enumerate(list(container)):
            path = f"{prefix}/{index}" if prefix else str(index)
            indexed[path] = (layer, container)
            if _layer_children(layer):
                visit(layer, path)

    visit(psd, "")
    return indexed


def edit_psd_document(
    *,
    source_path: Path,
    output_path: Path,
    preview_path: Path,
    edits: list[dict[str, Any]],
) -> dict[str, Any]:
    """Apply bounded, non-destructive layer property and hierarchy edits to a PSD copy."""

    if not edits or len(edits) > _MAX_PSD_LAYERS:
        raise ValueError("PSD layer editing requires between 1 and 200 edits")
    PSDImage, _PixelLayer, _Compression, dependency_error = _psd_tools_status()
    if dependency_error or PSDImage is None:
        raise RuntimeError(dependency_error or "psd-tools is unavailable")
    psd = PSDImage.open(str(source_path))
    indexed = _index_psd_layers(psd)
    resolved: list[tuple[dict[str, Any], Any, Any]] = []
    for raw_edit in edits:
        edit = dict(raw_edit or {})
        layer_path = str(edit.get("layerPath") or "").strip().strip("/")
        if layer_path not in indexed:
            raise ValueError(f"PSD layer path is unavailable: {layer_path}")
        layer, parent = indexed[layer_path]
        resolved.append((edit, layer, parent))
    for edit, layer, _parent in resolved:
        if "name" in edit:
            layer.name = _clean_layer_name(edit.get("name"), 0)
        if "visible" in edit:
            layer.visible = bool(edit.get("visible"))
        if "opacityPercent" in edit:
            layer.opacity = round(_bounded_percent(edit.get("opacityPercent"), 100.0) * 255.0 / 100.0)
        if "x" in edit or "y" in edit:
            x = int(edit.get("x") if "x" in edit else getattr(layer, "left", 0))
            y = int(edit.get("y") if "y" in edit else getattr(layer, "top", 0))
            layer.offset = (
                max(-_MAX_PSD_DIMENSION, min(x, _MAX_PSD_DIMENSION)),
                max(-_MAX_PSD_DIMENSION, min(y, _MAX_PSD_DIMENSION)),
            )
    for edit, layer, current_parent in resolved:
        if "order" not in edit and "targetParentPath" not in edit:
            continue
        target_parent_path = str(edit.get("targetParentPath") or "").strip().strip("/")
        if target_parent_path and target_parent_path.startswith(f"{str(edit.get('layerPath')).strip().strip('/')}/"):
            raise ValueError("A PSD group cannot be moved into its own descendant")
        target_parent = psd if not target_parent_path else indexed.get(target_parent_path, (None, None))[0]
        if target_parent is None or (target_parent is not psd and not _layer_children(target_parent) and not bool(getattr(target_parent, "is_group", lambda: False)())):
            raise ValueError(f"PSD target parent is not a group: {target_parent_path}")
        if layer in list(current_parent):
            current_parent.remove(layer)
        target_order = max(0, min(int(edit.get("order") or 0), len(list(target_parent))))
        target_parent.insert(target_order, layer)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.parent.mkdir(parents=True, exist_ok=True)
    psd.save(str(output_path))
    psd.composite().convert("RGBA").save(preview_path, format="PNG")
    return inspect_psd_manifest(output_path)


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
    try:
        layer_limit = max(1, min(int(max_layers or 40), 200))
        manifest = inspect_psd_manifest(source, max_layers=layer_limit)
        layer_lines: list[str] = []

        def collect(items: list[dict[str, Any]], depth: int = 0) -> None:
            for layer in items:
                visible = "visible" if layer.get("visible") else "hidden"
                layer_lines.append(
                    f"{'  ' * depth}{layer.get('layerPath')}. {layer.get('name')} "
                    f"({visible}, bbox=({layer.get('left')}, {layer.get('top')}, {layer.get('right')}, {layer.get('bottom')}))"
                )
                collect(list(layer.get("children") or []), depth + 1)

        collect(list(manifest.get("layers") or []))
    except Exception as exc:
        return _compact_error("Creative Media PSD Inspect", f"Could not parse `{label}`: {exc}", next_action="Verify the file is a valid PSD.")
    return _markdown_kv(
        "Creative Media PSD Inspect",
        [
            ("source", label),
            ("canvas", f"{manifest.get('width')}x{manifest.get('height')}"),
            ("layers", manifest.get("layerCount")),
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

    scope = {
        "session_id": str(context.get("session_id") or context.get("sessionId") or "").strip(),
        "workspace_id": str(context.get("workspace_id") or context.get("workspaceId") or "").strip(),
        "project_id": str(context.get("project_id") or context.get("projectId") or "").strip(),
        "workspace_path": str(context.get("workspace_path") or context.get("workspacePath") or "").strip(),
    }
    output_path = str(payload.get("outputPath") or payload.get("output_path") or "").strip()
    preview_path = str(payload.get("previewPath") or payload.get("preview_path") or "").strip()
    requested_psd = (
        Path(output_path)
        if output_path
        else _default_output_path(binding, str(payload.get("name") or "layered-asset"), ".psd")
    )
    requested_preview = Path(preview_path) if preview_path else requested_psd.with_suffix(".png")
    try:
        psd_target = creative_media_resource_authority.resolve_output_path(
            path=str(requested_psd),
            **scope,
        ).path
        png_target = creative_media_resource_authority.resolve_output_path(
            path=str(requested_preview),
            **scope,
        ).path
    except CreativeMediaResourceAuthorityError:
        return _compact_error(
            "Creative Media PSD Compose Template",
            "Output paths are not available in the current session scope.",
            next_action="Choose PSD and preview paths inside the current workspace.",
        )

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
        resolved_layers.append({
            "name": name,
            "source": source,
            "label": label,
            "x": x,
            "y": y,
            "width": raw_layer.get("width"),
            "height": raw_layer.get("height"),
            "scalePercent": raw_layer.get("scalePercent", raw_layer.get("scale")),
            "opacityPercent": raw_layer.get("opacityPercent", raw_layer.get("opacity", 100)),
            "visible": bool(raw_layer.get("visible", True)),
        })

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

    try:
        compose_psd_document(
            output_path=psd_target,
            preview_path=png_target,
            canvas={"width": width, "height": height, "background": canvas.get("background")},
            layers=resolved_layers,
        )
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
