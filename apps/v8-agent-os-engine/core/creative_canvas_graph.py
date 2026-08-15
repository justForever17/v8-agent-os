from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import math
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from core.database import db
from core.workspace_authority import WorkspaceAuthorityDescriptor, workspace_authority_service
from core.workspace_identity import workspace_path_key
from core.workspace_media_library import workspace_media_library
from erc.event_bus import event_bus
from erc.models import RuntimeSource


GRAPH_SCHEMA = "v8.creative_canvas_graph.v1"
GRAPH_VERSION = 3
TEMPLATE_SCHEMA = "v8.creative_canvas_template.v1"
CANVAS_GRAPH_RUN_STATE_TOPIC = "canvas.graph.run.state"
CANVAS_GRAPH_RUN_STATE_SCHEMA = "v8.creative_canvas_graph_run_state.v1"
MAX_GRAPH_NODES = 160
MAX_GRAPH_EDGES = 320
MAX_TEMPLATE_TITLE = 80
MAX_COMMAND_HISTORY = 120
ACTIVE_GRAPH_STATES = {"queued", "running", "cancelling"}
TERMINAL_JOB_STATES = {"succeeded", "failed", "cancelled", "degraded", "rejected"}
RETRYABLE_GRAPH_STATES = {"failed", "interrupted"}
GRAPH_RUN_PUBLIC_STATUS_BY_INTERNAL = {
    "queued": "queued",
    "running": "running",
    "cancelling": "cancelling",
    "cancelled": "cancelled",
    "failed": "failed",
    "interrupted": "interrupted",
    "succeeded": "completed",
}


logger = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback
    return fallback


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _clean_id(value: Any, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", normalized):
        raise CreativeCanvasGraphError(f"{label} is invalid")
    return normalized


def _clean_text(value: Any, *, limit: int) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float, label: str) -> float:
    if isinstance(value, bool):
        raise CreativeCanvasGraphError(f"{label} must be a number")
    try:
        number = float(default if value is None or value == "" else value)
    except (TypeError, ValueError) as exc:
        raise CreativeCanvasGraphError(f"{label} must be a number") from exc
    if not math.isfinite(number):
        raise CreativeCanvasGraphError(f"{label} must be finite")
    return max(minimum, min(number, maximum))


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int, label: str) -> int:
    if isinstance(value, bool):
        raise CreativeCanvasGraphError(f"{label} must be an integer")
    try:
        number = int(default if value is None or value == "" else value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CreativeCanvasGraphError(f"{label} must be an integer") from exc
    return max(minimum, min(number, maximum))


def _normalize_mask(value: Any) -> dict[str, Any] | None:
    mask = _record(value)
    strokes: list[dict[str, Any]] = []
    for raw_stroke in _list(mask.get("strokes"))[-64:]:
        stroke = _record(raw_stroke)
        points: list[dict[str, float]] = []
        for raw_point in _list(stroke.get("points"))[-512:]:
            point = _record(raw_point)
            try:
                raw_x = float(point.get("x"))
                raw_y = float(point.get("y"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(raw_x) or not math.isfinite(raw_y):
                continue
            x = max(0.0, min(raw_x, 1.0))
            y = max(0.0, min(raw_y, 1.0))
            points.append({"x": x, "y": y})
        if not points:
            continue
        strokes.append({
            "id": _clean_id(stroke.get("id") or f"stroke-{len(strokes) + 1}", label="Canvas mask stroke id"),
            "mode": "erase" if stroke.get("mode") == "erase" else "paint",
            "size": _bounded_float(stroke.get("size"), default=0.045, minimum=0.005, maximum=0.2, label="Canvas mask stroke size"),
            "points": points,
        })
    if not strokes:
        return None
    return {
        "revision": _bounded_int(mask.get("revision"), default=0, minimum=0, maximum=2_147_483_647, label="Canvas mask revision"),
        "strokes": strokes,
        "frozenSourceIds": [
            _clean_id(item, label="Canvas frozen mask source id")
            for item in _list(mask.get("frozenSourceIds"))[-20:]
            if str(item or "").strip()
        ],
        "sourceWidth": _bounded_int(mask.get("sourceWidth"), default=1024, minimum=1, maximum=32768, label="Canvas mask width"),
        "sourceHeight": _bounded_int(mask.get("sourceHeight"), default=1024, minimum=1, maximum=32768, label="Canvas mask height"),
    }


@dataclass(frozen=True, slots=True)
class InputPort:
    port_id: str
    media_types: tuple[str, ...]
    minimum: int = 0
    maximum: int = 1
    ordered: bool = False


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    action_id: str
    capability: str
    inputs: tuple[InputPort, ...]
    output_slot: str
    output_media_types: tuple[str, ...]
    requires_prompt: bool
    parameter_editor: str = ""
    network_required: bool = True
    may_incur_cost: bool = True


ANY_REFERENCE_TYPES = ("image", "video", "audio", "model_3d", "psd", "motion", "document", "text")


def _port(
    port_id: str,
    media_types: Iterable[str],
    minimum: int = 0,
    maximum: int = 1,
    *,
    ordered: bool = False,
) -> InputPort:
    return InputPort(port_id, tuple(media_types), minimum, maximum, ordered)


def _action(
    action_id: str,
    capability: str,
    inputs: Iterable[InputPort],
    output_slot: str,
    output_media_types: Iterable[str],
    *,
    requires_prompt: bool,
    parameter_editor: str = "",
    network_required: bool = True,
    may_incur_cost: bool = True,
) -> ActionDefinition:
    return ActionDefinition(
        action_id=action_id,
        capability=capability,
        inputs=tuple(inputs),
        output_slot=output_slot,
        output_media_types=tuple(output_media_types),
        requires_prompt=requires_prompt,
        parameter_editor=parameter_editor,
        network_required=network_required,
        may_incur_cost=may_incur_cost,
    )


ACTION_DEFINITIONS = {
    item.action_id: item
    for item in (
        _action(
            "creative_media.generate_image",
            "image.generate",
            [_port("references", ANY_REFERENCE_TYPES, 0, 8, ordered=True)],
            "image",
            ["image"],
            requires_prompt=True,
        ),
        _action(
            "creative_media.edit_image",
            "image.edit",
            [_port("image", ["image"], 1, 1)],
            "image_derivative",
            ["image"],
            requires_prompt=True,
        ),
        _action(
            "creative_media.edit_image_region",
            "image.edit",
            [_port("image", ["image"], 1, 1), _port("mask", ["mask"], 1, 1)],
            "image_derivative",
            ["image"],
            requires_prompt=True,
        ),
        _action(
            "creative_media.generate_video",
            "video.text_to_video",
            [],
            "video",
            ["video"],
            requires_prompt=True,
        ),
        _action(
            "creative_media.animate_image",
            "video.image_to_video",
            [_port("image", ["image"], 1, 1)],
            "video",
            ["video"],
            requires_prompt=True,
        ),
        _action(
            "creative_media.generate_video_from_keyframes",
            "video.first_last_frame",
            [_port("keyframes", ["image"], 2, 2, ordered=True)],
            "video",
            ["video"],
            requires_prompt=True,
        ),
        _action(
            "creative_media.generate_video_from_references",
            "video.reference_to_video",
            [_port("references", ["image", "video", "audio"], 1, 8, ordered=True)],
            "video",
            ["video"],
            requires_prompt=True,
        ),
        _action(
            "creative_media.generate_voice",
            "voice.tts",
            [_port("references", ["text", "document"], 0, 4, ordered=True)],
            "voice",
            ["audio"],
            requires_prompt=True,
        ),
        _action(
            "creative_media.generate_music",
            "music.generate",
            [_port("references", ANY_REFERENCE_TYPES, 0, 8, ordered=True)],
            "music",
            ["audio"],
            requires_prompt=True,
        ),
        _action(
            "creative_media.generate_model_3d",
            "model3d.generate",
            [_port("references", ["image", "model_3d", "text"], 0, 4, ordered=True)],
            "model_3d",
            ["model_3d"],
            requires_prompt=True,
        ),
        _action(
            "creative_media.compose_psd",
            "image.compose_psd",
            [_port("layers", ["image", "psd"], 1, 60, ordered=True)],
            "psd_document",
            ["psd"],
            requires_prompt=False,
            parameter_editor="psd_composition",
            network_required=False,
            may_incur_cost=False,
        ),
        _action(
            "creative_media.edit_psd_layers",
            "image.edit_psd_layers",
            [_port("psd", ["psd"], 1, 1)],
            "psd_document",
            ["psd"],
            requires_prompt=False,
            parameter_editor="psd_layers",
            network_required=False,
            may_incur_cost=False,
        ),
        _action(
            "creative_media.extract_video_frame_exact",
            "video.extract_frame_exact",
            [_port("video", ["video"], 1, 1)],
            "image_derivative",
            ["image"],
            requires_prompt=False,
            parameter_editor="frame_pick",
            network_required=False,
            may_incur_cost=False,
        ),
        _action(
            "creative_media.trim_video_exact",
            "video.trim_exact",
            [_port("video", ["video"], 1, 1)],
            "video_derivative",
            ["video"],
            requires_prompt=False,
            parameter_editor="time_range",
            network_required=False,
            may_incur_cost=False,
        ),
        _action(
            "creative_media.trim_audio_exact",
            "audio.trim_exact",
            [_port("audio", ["audio"], 1, 1)],
            "audio_derivative",
            ["audio"],
            requires_prompt=False,
            parameter_editor="time_range",
            network_required=False,
            may_incur_cost=False,
        ),
        _action(
            "creative_media.extract_holistic_motion",
            "video.extract_holistic_motion",
            [_port("video", ["video"], 1, 1)],
            "motion_clip",
            ["motion"],
            requires_prompt=False,
            network_required=False,
            may_incur_cost=False,
        ),
        _action(
            "creative_media.render_motion_guidance",
            "video.render_motion_guidance",
            [_port("motion", ["motion"], 1, 1)],
            "motion_guidance_video",
            ["video"],
            requires_prompt=False,
            network_required=False,
            may_incur_cost=False,
        ),
        _action(
            "creative_media.transfer_action_to_character",
            "video.action_transfer",
            [_port("image", ["image"], 1, 1), _port("video", ["video"], 1, 1)],
            "action_transfer_video",
            ["video"],
            requires_prompt=False,
            network_required=True,
            may_incur_cost=True,
        ),
        _action(
            "creative_media.inspect_rigged_model",
            "model3d.inspect_rigged",
            [_port("model", ["model_3d"], 1, 1)],
            "rig_profile",
            ["document"],
            requires_prompt=False,
            network_required=False,
            may_incur_cost=False,
        ),
        _action(
            "creative_media.retarget_motion_godot",
            "model3d.retarget_motion_godot",
            [_port("motion", ["motion"], 1, 1), _port("model", ["model_3d"], 1, 1)],
            "animated_model",
            ["model_3d"],
            requires_prompt=False,
            network_required=False,
            may_incur_cost=False,
        ),
    )
}


class CreativeCanvasGraphError(ValueError):
    pass


class CreativeCanvasGraphConflict(CreativeCanvasGraphError):
    pass


class CreativeCanvasGraphCancelled(CreativeCanvasGraphError):
    pass


def _graph_without_viewport(graph: dict[str, Any]) -> dict[str, Any]:
    return {
        "nodes": _list(graph.get("nodes")),
        "edges": _list(graph.get("edges")),
    }


def _changed_keys(before: dict[str, Any], after: dict[str, Any]) -> set[str]:
    return {
        key
        for key in set(before) | set(after)
        if before.get(key) != after.get(key)
    }


def _command_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any] | None:
    if _graph_without_viewport(before) == _graph_without_viewport(after):
        return None
    before_nodes = {str(item.get("nodeId")): item for item in _list(before.get("nodes")) if isinstance(item, dict)}
    after_nodes = {str(item.get("nodeId")): item for item in _list(after.get("nodes")) if isinstance(item, dict)}
    before_edges = {str(item.get("edgeId")): item for item in _list(before.get("edges")) if isinstance(item, dict)}
    after_edges = {str(item.get("edgeId")): item for item in _list(after.get("edges")) if isinstance(item, dict)}
    added_nodes = sorted(set(after_nodes) - set(before_nodes))
    removed_nodes = sorted(set(before_nodes) - set(after_nodes))
    added_edges = sorted(set(after_edges) - set(before_edges))
    removed_edges = sorted(set(before_edges) - set(after_edges))
    updated_nodes = sorted(
        node_id for node_id in set(before_nodes) & set(after_nodes)
        if before_nodes[node_id] != after_nodes[node_id]
    )
    updated_edges = sorted(
        edge_id for edge_id in set(before_edges) & set(after_edges)
        if before_edges[edge_id] != after_edges[edge_id]
    )
    node_keys = set().union(*(
        _changed_keys(before_nodes[node_id], after_nodes[node_id]) for node_id in updated_nodes
    )) if updated_nodes else set()
    edge_keys = set().union(*(
        _changed_keys(before_edges[edge_id], after_edges[edge_id]) for edge_id in updated_edges
    )) if updated_edges else set()
    if added_nodes and not (removed_nodes or updated_nodes or removed_edges or updated_edges):
        command_kind = "add_nodes"
    elif removed_nodes and not (added_nodes or updated_nodes or added_edges or updated_edges):
        command_kind = "remove_nodes"
    elif (added_edges or removed_edges) and not (added_nodes or removed_nodes or updated_nodes or updated_edges):
        command_kind = "connect_nodes" if added_edges else "disconnect_nodes"
    elif updated_nodes and node_keys <= {"x", "y", "width", "height"} and not (added_nodes or removed_nodes or added_edges or removed_edges or updated_edges):
        command_kind = "move_nodes"
    elif updated_edges and edge_keys <= {"note"} and not (added_nodes or removed_nodes or updated_nodes or added_edges or removed_edges):
        command_kind = "edit_relationship"
    elif updated_nodes and node_keys <= {"prompt", "parameters", "configurationRevision", "mask", "title"} and not (added_nodes or removed_nodes or added_edges or removed_edges or updated_edges):
        command_kind = "configure_nodes"
    else:
        command_kind = "edit_graph"
    payload = {
        "addedNodeIds": added_nodes,
        "removedNodeIds": removed_nodes,
        "updatedNodeIds": updated_nodes,
        "addedEdgeIds": added_edges,
        "removedEdgeIds": removed_edges,
        "updatedEdgeIds": updated_edges,
    }
    inverse = {
        **payload,
        "addedNodeIds": removed_nodes,
        "removedNodeIds": added_nodes,
        "addedEdgeIds": removed_edges,
        "removedEdgeIds": added_edges,
    }
    return {
        "kind": command_kind,
        "affectedNodeIds": sorted(set(added_nodes + removed_nodes + updated_nodes)),
        "payload": payload,
        "inverse": inverse,
    }


def _normalize_action_parameters(definition: ActionDefinition, value: Any) -> dict[str, Any]:
    parameters = _record(value)
    if definition.parameter_editor == "psd_composition":
        canvas = _record(parameters.get("canvas"))
        layers: list[dict[str, Any]] = []
        for index, raw_layer in enumerate(_list(parameters.get("layers"))[:60]):
            layer = _record(raw_layer)
            source_node_id = str(layer.get("sourceNodeId") or "").strip()
            normalized = {
                "sourceNodeId": _clean_id(source_node_id, label="PSD source node id") if source_node_id else "",
                "name": _clean_text(layer.get("name") or f"Layer {index + 1}", limit=80),
                "x": _bounded_int(layer.get("x"), default=0, minimum=-32768, maximum=32768, label="PSD layer x"),
                "y": _bounded_int(layer.get("y"), default=0, minimum=-32768, maximum=32768, label="PSD layer y"),
                "scalePercent": _bounded_float(layer.get("scalePercent"), default=100, minimum=1, maximum=800, label="PSD layer scale"),
                "opacityPercent": _bounded_float(layer.get("opacityPercent"), default=100, minimum=0, maximum=100, label="PSD layer opacity"),
                "visible": bool(layer.get("visible", True)),
                "order": _bounded_int(layer.get("order"), default=index, minimum=0, maximum=59, label="PSD layer order"),
            }
            if layer.get("width") not in (None, ""):
                normalized["width"] = _bounded_int(layer.get("width"), default=1, minimum=1, maximum=32768, label="PSD layer width")
            if layer.get("height") not in (None, ""):
                normalized["height"] = _bounded_int(layer.get("height"), default=1, minimum=1, maximum=32768, label="PSD layer height")
            layers.append(normalized)
        return {
            "canvas": {
                "width": _bounded_int(canvas.get("width"), default=1920, minimum=1, maximum=32768, label="PSD canvas width"),
                "height": _bounded_int(canvas.get("height"), default=1080, minimum=1, maximum=32768, label="PSD canvas height"),
                "background": _clean_text(canvas.get("background") or "transparent", limit=32),
            },
            "layers": sorted(layers, key=lambda item: (int(item["order"]), str(item["sourceNodeId"]))),
        }
    if definition.parameter_editor == "psd_layers":
        edits: list[dict[str, Any]] = []
        for raw_edit in _list(parameters.get("edits"))[:200]:
            edit = _record(raw_edit)
            layer_path = str(edit.get("layerPath") or "").strip().strip("/")
            if not layer_path or not re.fullmatch(r"\d+(?:/\d+)*", layer_path):
                continue
            normalized_edit: dict[str, Any] = {"layerPath": layer_path}
            if "name" in edit:
                normalized_edit["name"] = _clean_text(edit.get("name"), limit=80)
            if "visible" in edit:
                normalized_edit["visible"] = bool(edit.get("visible"))
            if "opacityPercent" in edit:
                normalized_edit["opacityPercent"] = _bounded_float(edit.get("opacityPercent"), default=100, minimum=0, maximum=100, label="PSD layer opacity")
            if "x" in edit:
                normalized_edit["x"] = _bounded_int(edit.get("x"), default=0, minimum=-32768, maximum=32768, label="PSD layer x")
            if "y" in edit:
                normalized_edit["y"] = _bounded_int(edit.get("y"), default=0, minimum=-32768, maximum=32768, label="PSD layer y")
            if "order" in edit:
                normalized_edit["order"] = _bounded_int(edit.get("order"), default=0, minimum=0, maximum=199, label="PSD layer order")
            if "targetParentPath" in edit:
                parent_path = str(edit.get("targetParentPath") or "").strip().strip("/")
                if parent_path and not re.fullmatch(r"\d+(?:/\d+)*", parent_path):
                    raise CreativeCanvasGraphError("PSD target parent path is invalid")
                normalized_edit["targetParentPath"] = parent_path
            if len(normalized_edit) > 1:
                edits.append(normalized_edit)
        return {"edits": edits}
    return parameters


class CreativeCanvasGraphService:
    def __init__(self) -> None:
        self._active_tasks: dict[str, asyncio.Task[Any]] = {}

    def _authority(self, session_id: str, *, require_write: bool = False) -> WorkspaceAuthorityDescriptor:
        normalized = str(session_id or "").strip()
        if not normalized or not db.get_session(normalized):
            raise CreativeCanvasGraphError("Current session is unavailable")
        authority = workspace_authority_service.resolve(runtime_kind="chat", session_id=normalized)
        if not authority.workspace_root:
            raise CreativeCanvasGraphError("Current session has no bound workspace")
        if require_write and not authority.side_effects_allowed:
            raise PermissionError("Current session workspace does not allow Canvas graph writes")
        return authority

    @staticmethod
    def _workspace_key(authority: WorkspaceAuthorityDescriptor) -> str:
        key = workspace_path_key(authority.workspace_root)
        if not key:
            raise CreativeCanvasGraphError("Current session workspace identity is unavailable")
        return key

    def _assert_graph_workspace_authority(
        self,
        *,
        session_id: str,
        graph_id: str,
        authority: WorkspaceAuthorityDescriptor,
    ) -> None:
        row = self._graph_row(session_id=session_id)
        if not row or str(row.get("graph_id") or "") != str(graph_id or ""):
            raise CreativeCanvasGraphError("Canvas graph is not bound to the current session")
        if str(row.get("workspace_key") or "") != self._workspace_key(authority):
            raise CreativeCanvasGraphConflict("Canvas graph workspace binding changed before execution")

    def prepare_direct_execution(
        self,
        *,
        session_id: str,
        graph_id: str,
        graph_revision: int,
        target_node_ids: list[str] | None,
    ) -> dict[str, Any]:
        """Validate a direct Canvas run before it becomes an async background task."""
        authority = self._authority(session_id, require_write=True)
        self._assert_graph_workspace_authority(
            session_id=session_id,
            graph_id=graph_id,
            authority=authority,
        )
        return self.execution_contract_summary(
            session_id=session_id,
            graph_id=graph_id,
            graph_revision=graph_revision,
            target_node_ids=target_node_ids,
        )

    @staticmethod
    def empty_graph(*, graph_id: str = "") -> dict[str, Any]:
        return {
            "schema": GRAPH_SCHEMA,
            "version": GRAPH_VERSION,
            "graphId": graph_id,
            "nodes": [],
            "edges": [],
            "viewport": {"x": 24, "y": 24, "scale": 1},
        }

    @staticmethod
    def action_catalog() -> list[dict[str, Any]]:
        return [
            {
                "actionId": definition.action_id,
                "binding": {"kind": "creative_media", "capability": definition.capability},
                "inputs": [
                    {
                        "portId": port.port_id,
                        "mediaTypes": list(port.media_types),
                        "min": port.minimum,
                        "max": port.maximum,
                        "ordered": port.ordered,
                    }
                    for port in definition.inputs
                ],
                "output": {
                    "portId": "output",
                    "slot": definition.output_slot,
                    "mediaTypes": list(definition.output_media_types),
                },
                "requiresPrompt": definition.requires_prompt,
                "parameterEditor": definition.parameter_editor or None,
                "networkRequired": definition.network_required,
                "mayIncurCost": definition.may_incur_cost,
                "graphCompatible": True,
            }
            for definition in ACTION_DEFINITIONS.values()
        ]

    def _normalize_graph(self, graph: dict[str, Any], *, graph_id: str = "") -> dict[str, Any]:
        raw = _record(graph)
        nodes_raw = _list(raw.get("nodes"))
        edges_raw = _list(raw.get("edges"))
        if len(nodes_raw) > MAX_GRAPH_NODES:
            raise CreativeCanvasGraphError(f"Canvas graph is limited to {MAX_GRAPH_NODES} nodes")
        if len(edges_raw) > MAX_GRAPH_EDGES:
            raise CreativeCanvasGraphError(f"Canvas graph is limited to {MAX_GRAPH_EDGES} edges")

        nodes: list[dict[str, Any]] = []
        node_ids: set[str] = set()
        for index, raw_node in enumerate(nodes_raw):
            node = _record(raw_node)
            node_id = _clean_id(node.get("nodeId") or f"canvas-node-{index + 1}", label="Canvas node id")
            if node_id in node_ids:
                raise CreativeCanvasGraphError("Canvas graph contains duplicate node ids")
            node_ids.add(node_id)
            kind = str(node.get("kind") or "resource").strip().lower()
            if kind not in {"resource", "input", "action", "result", "sink"}:
                raise CreativeCanvasGraphError(f"Unsupported Canvas node kind: {kind}")
            normalized = {
                "nodeId": node_id,
                "kind": kind,
                "x": _bounded_float(node.get("x"), default=0, minimum=-1_000_000, maximum=1_000_000, label="Canvas node x"),
                "y": _bounded_float(node.get("y"), default=0, minimum=-1_000_000, maximum=1_000_000, label="Canvas node y"),
                "width": _bounded_float(node.get("width"), default=280, minimum=180, maximum=720, label="Canvas node width"),
                "height": _bounded_float(node.get("height"), default=190, minimum=96, maximum=720, label="Canvas node height"),
                "title": _clean_text(node.get("title"), limit=160),
            }
            if kind == "resource":
                origin = str(node.get("origin") or "").strip()
                if origin not in {"source", "artifact", "workspace_asset"}:
                    raise CreativeCanvasGraphError("Resource node origin is invalid")
                normalized.update({
                    "origin": origin,
                    "resourceId": _clean_id(node.get("resourceId"), label="Canvas resource id"),
                    "mediaType": str(node.get("mediaType") or "unknown").strip().lower(),
                })
                mask = _normalize_mask(node.get("mask"))
                if mask:
                    normalized["mask"] = mask
            elif kind == "input":
                media_types = [str(item).strip().lower() for item in _list(node.get("acceptedMediaTypes")) if str(item).strip()]
                normalized.update({
                    "acceptedMediaTypes": list(dict.fromkeys(media_types or ["unknown"])),
                    "mediaType": str(node.get("mediaType") or (media_types[0] if media_types else "unknown")),
                })
            elif kind == "action":
                action_id = _clean_id(node.get("actionDefinitionId"), label="Canvas action definition id")
                definition = ACTION_DEFINITIONS.get(action_id)
                if not definition:
                    raise CreativeCanvasGraphError(f"Canvas action is not graph-compatible: {action_id}")
                normalized.update({
                    "actionDefinitionId": action_id,
                    "prompt": _clean_text(node.get("prompt"), limit=12000),
                    "parameters": _normalize_action_parameters(definition, node.get("parameters")),
                    "configurationRevision": _bounded_int(node.get("configurationRevision"), default=1, minimum=1, maximum=2_147_483_647, label="Canvas action configuration revision"),
                })
            elif kind == "result":
                normalized.update({
                    "producerActionNodeId": _clean_id(node.get("producerActionNodeId"), label="Result producer node id"),
                    "outputSlot": _clean_id(node.get("outputSlot") or "output", label="Canvas output slot"),
                    "mediaType": str(node.get("mediaType") or "unknown").strip().lower(),
                })
            else:
                sink_kind = str(node.get("sinkKind") or "preview").strip().lower()
                if sink_kind not in {"preview", "download"}:
                    raise CreativeCanvasGraphError("Canvas sink kind is invalid")
                normalized["sinkKind"] = sink_kind
            nodes.append(normalized)

        edges: list[dict[str, Any]] = []
        edge_ids: set[str] = set()
        for index, raw_edge in enumerate(edges_raw):
            edge = _record(raw_edge)
            edge_id = _clean_id(edge.get("edgeId") or f"canvas-edge-{index + 1}", label="Canvas edge id")
            if edge_id in edge_ids:
                raise CreativeCanvasGraphError("Canvas graph contains duplicate edge ids")
            edge_ids.add(edge_id)
            from_id = _clean_id(edge.get("from"), label="Canvas edge source")
            to_id = _clean_id(edge.get("to"), label="Canvas edge target")
            if from_id == to_id or from_id not in node_ids or to_id not in node_ids:
                raise CreativeCanvasGraphError("Canvas edge references an unavailable node")
            role = str(edge.get("role") or "relation").strip().lower()
            if role not in {"data", "relation"}:
                raise CreativeCanvasGraphError("Canvas edge role is invalid")
            edges.append({
                "edgeId": edge_id,
                "from": from_id,
                "to": to_id,
                "fromPort": str(edge.get("fromPort") or "right"),
                "toPort": str(edge.get("toPort") or "left"),
                "fromPortId": str(edge.get("fromPortId") or ("output" if role == "data" else "relation")).strip(),
                "toPortId": str(edge.get("toPortId") or ("input" if role == "data" else "relation")).strip(),
                "dataType": str(edge.get("dataType") or "unknown").strip().lower(),
                "role": role,
                "order": _bounded_int(edge.get("order"), default=0, minimum=0, maximum=MAX_GRAPH_EDGES, label="Canvas edge order"),
                "note": _clean_text(edge.get("note"), limit=2000),
            })

        viewport = _record(raw.get("viewport"))
        return {
            "schema": GRAPH_SCHEMA,
            "version": GRAPH_VERSION,
            "graphId": graph_id or str(raw.get("graphId") or "").strip(),
            "nodes": nodes,
            "edges": edges,
            "viewport": {
                "x": _bounded_float(viewport.get("x"), default=24, minimum=-1_000_000, maximum=1_000_000, label="Canvas viewport x"),
                "y": _bounded_float(viewport.get("y"), default=24, minimum=-1_000_000, maximum=1_000_000, label="Canvas viewport y"),
                "scale": _bounded_float(viewport.get("scale"), default=1, minimum=0.2, maximum=3, label="Canvas viewport scale"),
            },
        }

    @staticmethod
    def _node_map(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {str(node["nodeId"]): node for node in _list(graph.get("nodes")) if isinstance(node, dict)}

    def _validate_resource(self, *, session_id: str, node: dict[str, Any]) -> None:
        resource_id = str(node.get("resourceId") or "")
        origin = str(node.get("origin") or "")
        if origin == "source":
            source = db.get_session_source(session_id=session_id, source_id=resource_id)
            if not source:
                raise CreativeCanvasGraphError("Canvas source is not bound to the current session")
            source_kind = str(source.get("sourceKind") or source.get("source_kind") or "")
            if source_kind == "canvas_mask" and str(node.get("mediaType") or "") != "mask":
                raise CreativeCanvasGraphError("Internal Canvas masks must keep the mask media type")
        elif origin == "artifact":
            artifact = db.get_runtime_artifact(resource_id)
            if not artifact or str(artifact.get("sessionId") or artifact.get("session_id") or "") != session_id:
                raise CreativeCanvasGraphError("Canvas artifact is not bound to the current session")
        else:
            try:
                workspace_media_library.resolve_asset_path(
                    session_id=session_id,
                    asset_id=resource_id,
                    require_session_use=True,
                )
            except (FileNotFoundError, PermissionError, ValueError) as exc:
                raise CreativeCanvasGraphError(
                    "Canvas workspace asset is not explicitly adopted by the current session"
                ) from exc

    def validate_graph(
        self,
        *,
        session_id: str,
        graph: dict[str, Any],
        allow_unbound_inputs: bool = True,
        validate_resources: bool = True,
        require_executable: bool = False,
        executable_action_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        normalized = self._normalize_graph(graph, graph_id=str(graph.get("graphId") or ""))
        nodes = self._node_map(normalized)
        data_edges = [edge for edge in normalized["edges"] if edge["role"] == "data"]
        incoming: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
        outgoing: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
        for edge in data_edges:
            incoming[edge["to"]].append(edge)
            outgoing[edge["from"]].append(edge)

        for node in nodes.values():
            kind = node["kind"]
            if kind == "resource" and validate_resources:
                self._validate_resource(session_id=session_id, node=node)
            if kind == "action":
                definition = ACTION_DEFINITIONS[node["actionDefinitionId"]]
                executable = require_executable and (
                    executable_action_ids is None or node["nodeId"] in executable_action_ids
                )
                if executable and definition.requires_prompt and not str(node.get("prompt") or "").strip():
                    raise CreativeCanvasGraphError(f"Canvas action requires an instruction: {definition.action_id}")
                by_port: dict[str, list[dict[str, Any]]] = {}
                for edge in incoming[node["nodeId"]]:
                    by_port.setdefault(edge["toPortId"], []).append(edge)
                    source = nodes[edge["from"]]
                    if source["kind"] not in {"resource", "result", "input"}:
                        raise CreativeCanvasGraphError("Canvas action inputs must come from material or result nodes")
                for port in definition.inputs:
                    values = by_port.get(port.port_id, [])
                    minimum = port.minimum if executable else 0
                    if len(values) < minimum or len(values) > port.maximum:
                        raise CreativeCanvasGraphError(
                            f"Canvas action port {definition.action_id}.{port.port_id} requires {minimum}-{port.maximum} inputs"
                        )
                    for edge in values:
                        source = nodes[edge["from"]]
                        if executable and source["kind"] == "input":
                            raise CreativeCanvasGraphError(
                                f"Canvas action port {definition.action_id}.{port.port_id} has an unbound workflow input"
                            )
                        media_type = str(source.get("mediaType") or edge.get("dataType") or "unknown")
                        if media_type not in port.media_types:
                            raise CreativeCanvasGraphError(
                                f"Canvas action port {definition.action_id}.{port.port_id} does not accept {media_type}"
                            )
                unknown_ports = set(by_port) - {port.port_id for port in definition.inputs}
                if unknown_ports:
                    raise CreativeCanvasGraphError(f"Canvas action contains unknown input ports: {', '.join(sorted(unknown_ports))}")
                if definition.parameter_editor == "psd_composition":
                    connected_node_ids = {edge["from"] for edge in by_port.get("layers", [])}
                    configured_node_ids = [
                        str(item.get("sourceNodeId") or "")
                        for item in _list(_record(node.get("parameters")).get("layers"))
                        if str(_record(item).get("sourceNodeId") or "")
                    ]
                    if len(configured_node_ids) != len(set(configured_node_ids)):
                        raise CreativeCanvasGraphError("PSD composition contains duplicate layer bindings")
                    if set(configured_node_ids) - connected_node_ids:
                        raise CreativeCanvasGraphError("PSD composition parameters reference an unconnected layer")
                if executable and definition.parameter_editor == "psd_layers" and not _list(_record(node.get("parameters")).get("edits")):
                    raise CreativeCanvasGraphError("PSD layer editing requires at least one saved layer change")
                results = [nodes[edge["to"]] for edge in outgoing[node["nodeId"]] if nodes[edge["to"]]["kind"] == "result"]
                if len(results) != 1:
                    raise CreativeCanvasGraphError("Each Canvas action must own exactly one persistent result slot")
                result = results[0]
                if result["producerActionNodeId"] != node["nodeId"]:
                    raise CreativeCanvasGraphError("Canvas result slot producer does not match its action")
                if result["outputSlot"] != definition.output_slot:
                    raise CreativeCanvasGraphError("Canvas result slot does not match the action output slot")
                if result["mediaType"] not in definition.output_media_types:
                    raise CreativeCanvasGraphError("Canvas result media type does not match the action output")
            elif kind == "result":
                producers = [edge for edge in incoming[node["nodeId"]] if nodes[edge["from"]]["kind"] == "action"]
                if len(producers) != 1 or producers[0]["from"] != node["producerActionNodeId"]:
                    raise CreativeCanvasGraphError("Canvas result slot must have exactly one action producer")
            elif kind == "sink":
                values = incoming[node["nodeId"]]
                if len(values) > 1 or (values and nodes[values[0]["from"]]["kind"] not in {"resource", "result", "input"}):
                    raise CreativeCanvasGraphError("Canvas preview/download cards require exactly one material input")

        adjacency = {node_id: [] for node_id in nodes}
        indegree = {node_id: 0 for node_id in nodes}
        for edge in data_edges:
            adjacency[edge["from"]].append(edge["to"])
            indegree[edge["to"]] += 1
        queue = [node_id for node_id, degree in indegree.items() if degree == 0]
        visited = 0
        while queue:
            current = queue.pop(0)
            visited += 1
            for target in adjacency[current]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        if visited != len(nodes):
            raise CreativeCanvasGraphError("Canvas executable data edges must form an acyclic graph")
        return normalized

    def _graph_row(self, *, session_id: str) -> dict[str, Any] | None:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM creative_canvas_graphs WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def _active_run(self, *, session_id: str) -> dict[str, Any] | None:
        with db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM creative_canvas_graph_runs
                WHERE session_id = ? AND status IN ('queued', 'running', 'cancelling')
                ORDER BY updated_at DESC LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _retry_state(node_states: dict[str, Any], current_node_id: str) -> dict[str, Any]:
        current = _record(node_states.get(current_node_id)) if current_node_id else {}
        if current:
            return current
        for raw_state in node_states.values():
            state = _record(raw_state)
            if state.get("providerCancellationRemoteTaskMayContinue"):
                return state
            if str(state.get("state") or "").strip().lower() in {"failed", "interrupted"}:
                return state
        return {}

    @staticmethod
    def _project_run(row: dict[str, Any]) -> dict[str, Any]:
        node_states = _record(_json(row.get("node_states_json"), {}))
        status = str(row.get("status") or "idle")
        current_node_id = str(row.get("current_node_id") or "")
        current_state = _record(node_states.get(current_node_id))
        retry_state = CreativeCanvasGraphService._retry_state(node_states, current_node_id)
        error_message = str(row.get("error_message") or current_state.get("error") or "").strip()
        default_error_codes = {
            "cancelled": "user_cancelled",
            "interrupted": "engine_restart_interrupted",
            "failed": "canvas_graph_failed",
        }
        error_code = str(
            current_state.get("errorCode")
            or retry_state.get("errorCode")
            or default_error_codes.get(status)
            or ""
        ).strip()
        remote_task_may_continue = bool(retry_state.get("providerCancellationRemoteTaskMayContinue"))
        can_retry = (
            status in RETRYABLE_GRAPH_STATES
            and retry_state.get("recoverable") is not False
            and not remote_task_may_continue
        )
        return {
            "graphRunId": row.get("graph_run_id"),
            "graphId": row.get("graph_id"),
            "sessionId": row.get("session_id"),
            "canvasOperationId": row.get("canvas_operation_id"),
            "chatRunId": row.get("chat_run_id"),
            "graphRevision": int(row.get("graph_revision") or 0),
            "targetNodeIds": [str(item) for item in _list(_json(row.get("target_node_ids_json"), []))],
            "status": status,
            "currentNodeId": current_node_id or None,
            "nodeStates": node_states,
            "error": {"code": error_code, "message": error_message} if error_code or error_message else None,
            "recovery": {
                "canRetry": can_retry,
                "mode": "failed_branch" if can_retry else None,
                "reason": error_code or None,
            },
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
            "completedAt": row.get("completed_at"),
        }

    def get_run(self, *, session_id: str, graph_run_id: str) -> dict[str, Any]:
        self._authority(session_id)
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM creative_canvas_graph_runs WHERE graph_run_id = ? AND session_id = ? LIMIT 1",
                (graph_run_id, session_id),
            ).fetchone()
        if not row:
            raise CreativeCanvasGraphError("Canvas graph run is not bound to the current session")
        return self._project_run(dict(row))

    def reconcile_startup(self) -> dict[str, int]:
        now = _utc_now()
        interrupted_runs = 0
        interrupted_nodes = 0
        interrupted_event_rows: list[dict[str, Any]] = []
        with db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT * FROM creative_canvas_graph_runs WHERE status IN ('queued', 'running', 'cancelling')"
            ).fetchall()
            for raw_row in rows:
                row = dict(raw_row)
                states = _record(_json(row.get("node_states_json"), {}))
                remote_task_uncertain = False
                for node_id, raw_state in list(states.items()):
                    state = _record(raw_state)
                    prior_state = str(state.get("state") or "queued")
                    if prior_state not in ACTIVE_GRAPH_STATES:
                        continue
                    has_provider_handle = bool(
                        str(state.get("jobId") or "").strip()
                        or str(state.get("providerTaskId") or "").strip()
                        or _record(state.get("providerHandle"))
                    )
                    remote_task_uncertain = remote_task_uncertain or has_provider_handle
                    states[node_id] = {
                        **state,
                        "state": "interrupted",
                        "priorState": prior_state,
                        "recoverable": not has_provider_handle,
                        "errorCode": (
                            "engine_restart_provider_task_unknown"
                            if has_provider_handle
                            else "engine_restart_interrupted"
                        ),
                        "error": (
                            "Engine restarted with an unverified Provider task handle; reconcile it before retry"
                            if has_provider_handle
                            else "Engine restarted before the Canvas action reached a terminal state"
                        ),
                        "interruptedAt": now,
                        **({"providerCancellationRemoteTaskMayContinue": True} if has_provider_handle else {}),
                    }
                    interrupted_nodes += 1
                error_message = (
                    "Engine restarted with an unverified Provider task; retry is blocked until lifecycle reconciliation"
                    if remote_task_uncertain
                    else "Engine restarted before the Canvas graph run reached a terminal state"
                )
                conn.execute(
                    """
                    UPDATE creative_canvas_graph_runs
                    SET status = 'interrupted', node_states_json = ?, current_node_id = NULL,
                        error_message = ?, updated_at = ?, completed_at = ?
                    WHERE graph_run_id = ? AND status IN ('queued', 'running', 'cancelling')
                    """,
                    (
                        json.dumps(states, ensure_ascii=False),
                        error_message,
                        now,
                        now,
                        row["graph_run_id"],
                    ),
                )
                interrupted_event_rows.append({
                    **row,
                    "status": "interrupted",
                    "node_states_json": json.dumps(states, ensure_ascii=False),
                    "current_node_id": None,
                    "error_message": error_message,
                    "updated_at": now,
                    "completed_at": now,
                })
                interrupted_runs += 1
            conn.commit()
        for row in interrupted_event_rows:
            self._emit_graph_run_state_event(row=row, status="interrupted")
        return {"interruptedRuns": interrupted_runs, "interruptedNodes": interrupted_nodes}

    @staticmethod
    def _history_rows(conn: Any, *, graph_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            """
            SELECT * FROM creative_canvas_commands
            WHERE graph_id = ?
            ORDER BY command_sequence ASC
            """,
            (graph_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _history_stacks(rows: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
        undo_stack: list[str] = []
        redo_stack: list[str] = []
        for row in rows:
            direction = str(row.get("direction") or "forward")
            command_id = str(row.get("command_id") or "")
            target_id = str(row.get("target_command_id") or "")
            if direction == "forward":
                if command_id:
                    undo_stack.append(command_id)
                redo_stack.clear()
            elif direction == "undo" and target_id:
                if undo_stack and undo_stack[-1] == target_id:
                    undo_stack.pop()
                    redo_stack.append(target_id)
            elif direction == "redo" and target_id:
                if redo_stack and redo_stack[-1] == target_id:
                    redo_stack.pop()
                    undo_stack.append(target_id)
        return undo_stack, redo_stack

    def _history_summary(self, conn: Any, *, graph_id: str) -> dict[str, Any]:
        rows = self._history_rows(conn, graph_id=graph_id)
        undo_stack, redo_stack = self._history_stacks(rows)
        latest = rows[-1] if rows else {}
        return {
            "canUndo": bool(undo_stack),
            "canRedo": bool(redo_stack),
            "undoDepth": len(undo_stack),
            "redoDepth": len(redo_stack),
            "lastCommand": {
                "commandId": latest.get("command_id"),
                "direction": latest.get("direction"),
                "kind": latest.get("command_kind"),
                "createdAt": latest.get("created_at"),
            } if latest else None,
        }

    @staticmethod
    def _insert_history_command(
        conn: Any,
        *,
        graph_id: str,
        session_id: str,
        base_revision: int,
        result_revision: int,
        direction: str,
        command_kind: str,
        before_graph: dict[str, Any],
        after_graph: dict[str, Any],
        affected_node_ids: list[str],
        payload: dict[str, Any],
        inverse: dict[str, Any],
        target_command_id: str = "",
        actor: str = "human",
    ) -> str:
        command_id = f"canvas-command-{uuid.uuid4().hex}"
        conn.execute(
            """
            INSERT INTO creative_canvas_commands(
                command_id, graph_id, session_id, base_revision, result_revision,
                direction, command_kind, target_command_id, affected_node_ids_json,
                payload_json, inverse_json, before_graph_json, after_graph_json,
                actor, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                command_id,
                graph_id,
                session_id,
                base_revision,
                result_revision,
                direction,
                command_kind,
                target_command_id or None,
                json.dumps(affected_node_ids, ensure_ascii=False),
                json.dumps(payload, ensure_ascii=False),
                json.dumps(inverse, ensure_ascii=False),
                json.dumps(before_graph, ensure_ascii=False),
                json.dumps(after_graph, ensure_ascii=False),
                actor,
                _utc_now(),
            ),
        )
        conn.execute(
            """
            DELETE FROM creative_canvas_commands
            WHERE graph_id = ? AND command_sequence NOT IN (
                SELECT command_sequence FROM creative_canvas_commands
                WHERE graph_id = ?
                ORDER BY command_sequence DESC
                LIMIT ?
            )
            """,
            (graph_id, graph_id, MAX_COMMAND_HISTORY),
        )
        return command_id

    def get_graph(self, *, session_id: str) -> dict[str, Any]:
        self._authority(session_id)
        row = self._graph_row(session_id=session_id)
        if not row:
            return {
                "graph": None,
                "revision": 0,
                "history": {"canUndo": False, "canRedo": False, "undoDepth": 0, "redoDepth": 0, "lastCommand": None},
                "runtime": {"status": "idle", "nodeStates": {}, "outputs": {}},
            }
        graph = _record(_json(row.get("graph_json"), {}))
        with db.get_connection() as conn:
            run = conn.execute(
                "SELECT * FROM creative_canvas_graph_runs WHERE graph_id = ? ORDER BY updated_at DESC LIMIT 1",
                (row["graph_id"],),
            ).fetchone()
            outputs = conn.execute(
                """
                SELECT * FROM creative_canvas_node_outputs
                WHERE graph_id = ? ORDER BY result_node_id, version_index DESC
                """,
                (row["graph_id"],),
            ).fetchall()
            history = self._history_summary(conn, graph_id=str(row["graph_id"]))
        output_map: dict[str, list[dict[str, Any]]] = {}
        for output in outputs:
            item = dict(output)
            output_map.setdefault(str(item["result_node_id"]), []).append({
                "outputVersionId": item["output_version_id"],
                "version": item["version_index"],
                "artifactId": item.get("artifact_id"),
                "jobId": item.get("job_id"),
                "mediaType": item.get("media_type"),
                "outputSlot": item.get("output_slot"),
                "configDigest": item.get("config_digest"),
                "metadata": _record(_json(item.get("metadata_json"), {})),
                "createdAt": item.get("created_at"),
            })
        run_data = dict(run) if run else {}
        run_projection = self._project_run(run_data) if run_data else None
        return {
            "graph": graph,
            "revision": int(row.get("revision") or 0),
            "history": history,
            "runtime": {
                "graphRunId": run_data.get("graph_run_id"),
                "canvasOperationId": run_data.get("canvas_operation_id"),
                "graphRevision": int(run_data.get("graph_revision") or 0) or None,
                "targetNodeIds": [
                    str(item)
                    for item in _list(_json(run_data.get("target_node_ids_json"), []))
                    if str(item).strip()
                ],
                "status": run_data.get("status") or "idle",
                "currentNodeId": run_data.get("current_node_id"),
                "error": run_data.get("error_message"),
                "errorDetail": (run_projection or {}).get("error"),
                "recovery": (run_projection or {}).get("recovery") or {
                    "canRetry": False,
                    "mode": None,
                    "reason": None,
                },
                "nodeStates": _record(_json(run_data.get("node_states_json"), {})),
                "outputs": output_map,
                "updatedAt": run_data.get("updated_at") or row.get("updated_at"),
            },
        }

    def save_graph(
        self,
        *,
        session_id: str,
        graph: dict[str, Any],
        expected_revision: int,
        allow_unbound_inputs: bool = True,
    ) -> dict[str, Any]:
        authority = self._authority(session_id, require_write=True)
        preliminary = self._graph_row(session_id=session_id)
        graph_id = str((preliminary or {}).get("graph_id") or graph.get("graphId") or f"canvas-graph-{uuid.uuid4().hex}")
        normalized = self.validate_graph(
            session_id=session_id,
            graph={**dict(graph or {}), "graphId": graph_id},
            allow_unbound_inputs=allow_unbound_inputs,
        )
        normalized["graphId"] = graph_id
        now = _utc_now()
        with db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT graph_run_id FROM creative_canvas_graph_runs WHERE session_id = ? AND status IN ('queued', 'running', 'cancelling') LIMIT 1",
                (session_id,),
            ).fetchone()
            if active:
                conn.rollback()
                raise CreativeCanvasGraphConflict("Canvas graph is locked while its run is active")
            existing_row = conn.execute(
                "SELECT graph_id, revision, graph_json FROM creative_canvas_graphs WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
            existing = dict(existing_row) if existing_row else None
            current_revision = int((existing or {}).get("revision") or 0)
            if int(expected_revision) != current_revision:
                conn.rollback()
                raise CreativeCanvasGraphConflict(
                    f"Canvas graph revision changed: expected {expected_revision}, current {current_revision}"
                )
            persisted_graph_id = str((existing or {}).get("graph_id") or graph_id)
            normalized["graphId"] = persisted_graph_id
            next_revision = current_revision + 1
            before_graph = _record(_json((existing or {}).get("graph_json"), {})) or self.empty_graph(graph_id=persisted_graph_id)
            before_graph["graphId"] = persisted_graph_id
            conn.execute(
                """
                INSERT INTO creative_canvas_graphs(
                    graph_id, session_id, workspace_key, schema_version, revision,
                    graph_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    schema_version = excluded.schema_version,
                    revision = excluded.revision,
                    graph_json = excluded.graph_json,
                    updated_at = excluded.updated_at
                """,
                (
                    persisted_graph_id,
                    session_id,
                    self._workspace_key(authority),
                    GRAPH_VERSION,
                    next_revision,
                    json.dumps(normalized, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            command = _command_delta(before_graph, normalized)
            if command:
                self._insert_history_command(
                    conn,
                    graph_id=persisted_graph_id,
                    session_id=session_id,
                    base_revision=current_revision,
                    result_revision=next_revision,
                    direction="forward",
                    command_kind=str(command["kind"]),
                    before_graph=before_graph,
                    after_graph=normalized,
                    affected_node_ids=list(command["affectedNodeIds"]),
                    payload=_record(command["payload"]),
                    inverse=_record(command["inverse"]),
                )
            conn.commit()
        return self.get_graph(session_id=session_id)

    def apply_history(
        self,
        *,
        session_id: str,
        direction: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        authority = self._authority(session_id, require_write=True)
        normalized_direction = str(direction or "").strip().lower()
        if normalized_direction not in {"undo", "redo"}:
            raise CreativeCanvasGraphError("Canvas history direction must be undo or redo")
        with db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT graph_run_id FROM creative_canvas_graph_runs WHERE session_id = ? AND status IN ('queued', 'running', 'cancelling') LIMIT 1",
                (session_id,),
            ).fetchone()
            if active:
                conn.rollback()
                raise CreativeCanvasGraphConflict("Canvas graph is locked while its run is active")
            graph_row = conn.execute(
                "SELECT * FROM creative_canvas_graphs WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
            if not graph_row:
                conn.rollback()
                raise CreativeCanvasGraphConflict("Canvas history is empty")
            graph_data = dict(graph_row)
            current_revision = int(graph_data.get("revision") or 0)
            if int(expected_revision) != current_revision:
                conn.rollback()
                raise CreativeCanvasGraphConflict(
                    f"Canvas graph revision changed: expected {expected_revision}, current {current_revision}"
                )
            rows = self._history_rows(conn, graph_id=str(graph_data["graph_id"]))
            undo_stack, redo_stack = self._history_stacks(rows)
            stack = undo_stack if normalized_direction == "undo" else redo_stack
            if not stack:
                conn.rollback()
                raise CreativeCanvasGraphConflict(f"Canvas has nothing to {normalized_direction}")
            target_command_id = stack[-1]
            target = next((row for row in rows if row.get("command_id") == target_command_id), None)
            if not target:
                conn.rollback()
                raise CreativeCanvasGraphConflict("Canvas history target is unavailable")
            current_graph = _record(_json(graph_data.get("graph_json"), {}))
            target_graph = _record(_json(
                target.get("before_graph_json") if normalized_direction == "undo" else target.get("after_graph_json"),
                {},
            ))
            target_graph["graphId"] = str(graph_data["graph_id"])
            normalized = self.validate_graph(
                session_id=session_id,
                graph=target_graph,
                allow_unbound_inputs=True,
            )
            normalized["graphId"] = str(graph_data["graph_id"])
            next_revision = current_revision + 1
            now = _utc_now()
            conn.execute(
                """
                UPDATE creative_canvas_graphs
                SET workspace_key = ?, schema_version = ?, revision = ?, graph_json = ?, updated_at = ?
                WHERE graph_id = ? AND session_id = ?
                """,
                (
                    self._workspace_key(authority),
                    GRAPH_VERSION,
                    next_revision,
                    json.dumps(normalized, ensure_ascii=False),
                    now,
                    graph_data["graph_id"],
                    session_id,
                ),
            )
            self._insert_history_command(
                conn,
                graph_id=str(graph_data["graph_id"]),
                session_id=session_id,
                base_revision=current_revision,
                result_revision=next_revision,
                direction=normalized_direction,
                command_kind=str(target.get("command_kind") or "edit_graph"),
                target_command_id=target_command_id,
                before_graph=current_graph,
                after_graph=normalized,
                affected_node_ids=[str(item) for item in _list(_json(target.get("affected_node_ids_json"), []))],
                payload={"targetCommandId": target_command_id},
                inverse={"targetCommandId": target_command_id},
            )
            conn.commit()
        return self.get_graph(session_id=session_id)

    @staticmethod
    def _preflight_issue_for_error(graph: dict[str, Any], error: Exception) -> dict[str, Any]:
        message = str(error)
        node_id = ""
        capability = ""
        for node in _list(graph.get("nodes")):
            if not isinstance(node, dict):
                continue
            candidate_id = str(node.get("nodeId") or "")
            action_id = str(node.get("actionDefinitionId") or "")
            resource_id = str(node.get("resourceId") or "")
            if candidate_id and candidate_id in message:
                node_id = candidate_id
                capability = action_id
                break
            if action_id and action_id in message:
                node_id = candidate_id
                capability = action_id
                break
            if resource_id and resource_id in message:
                node_id = candidate_id
                break
        lowered = message.lower()
        if "requires an instruction" in lowered:
            code, remediation = "missing-prompt", "configure_action"
        elif "requires" in lowered and "inputs" in lowered:
            code, remediation = "missing-input", "connect_input"
        elif "unbound workflow input" in lowered:
            code, remediation = "unbound-input", "bind_input"
        elif "does not accept" in lowered:
            code, remediation = "incompatible-media", "replace_input"
        elif "source" in lowered or "artifact" in lowered or "workspace asset" in lowered:
            code, remediation = "resource-unavailable", "choose_material"
        elif "cycle" in lowered:
            code, remediation = "cycle", "remove_connection"
        elif "result slot" in lowered or "producer" in lowered:
            code, remediation = "result-slot", "repair_result_slot"
        else:
            code, remediation = "invalid-graph", "inspect_graph"
        return {
            "severity": "error",
            "code": code,
            "nodeId": node_id,
            "capability": capability,
            "detail": message,
            "remediation": remediation,
        }

    def preflight_execution(
        self,
        *,
        session_id: str,
        graph_id: str,
        graph_revision: int,
        target_node_ids: list[str] | None = None,
        available_operation_kinds: set[str] | None = None,
        unavailable_operation_reasons: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self._authority(session_id)
        row = self._graph_row(session_id=session_id)
        if not row or str(row.get("graph_id") or "") != str(graph_id or ""):
            raise CreativeCanvasGraphConflict("Canvas graph is unavailable for the current session")
        if int(row.get("revision") or 0) != int(graph_revision or 0):
            raise CreativeCanvasGraphConflict("Canvas graph revision changed before validation")
        graph = _record(_json(row.get("graph_json"), {}))
        try:
            plan = self._compile_plan(
                session_id=session_id,
                graph=graph,
                target_node_ids=target_node_ids,
            )
        except CreativeCanvasGraphConflict:
            raise
        except (CreativeCanvasGraphError, PermissionError, FileNotFoundError, ValueError) as error:
            issue = self._preflight_issue_for_error(graph, error)
            return {"valid": False, "issues": [issue], "plan": None}

        issues: list[dict[str, Any]] = []
        available = set(available_operation_kinds or set())
        unavailable = dict(unavailable_operation_reasons or {})
        for entry in _list(plan.get("actions")):
            action = _record(entry)
            action_id = str(action.get("actionDefinitionId") or "")
            definition = ACTION_DEFINITIONS.get(action_id)
            if not definition:
                continue
            node_id = str(action.get("actionNodeId") or "")
            if definition.network_required and available_operation_kinds is not None and definition.capability not in available:
                issues.append({
                    "severity": "error",
                    "code": "provider-unconfigured",
                    "nodeId": node_id,
                    "capability": definition.capability,
                    "detail": definition.capability,
                    "remediation": "configure_model",
                })
            if definition.capability in unavailable:
                issues.append({
                    "severity": "error",
                    "code": "local-runtime-unavailable",
                    "nodeId": node_id,
                    "capability": definition.capability,
                    "detail": unavailable[definition.capability],
                    "remediation": "configure_local_runtime",
                })
            if definition.network_required:
                issues.append({
                    "severity": "warning",
                    "code": "network-required",
                    "nodeId": node_id,
                    "capability": definition.capability,
                    "detail": definition.capability,
                    "remediation": "review_run",
                })
            if definition.may_incur_cost:
                issues.append({
                    "severity": "warning",
                    "code": "possible-cost",
                    "nodeId": node_id,
                    "capability": definition.capability,
                    "detail": definition.capability,
                    "remediation": "review_run",
                })
        return {
            "valid": not any(item["severity"] == "error" for item in issues),
            "issues": issues,
            "plan": plan,
        }

    def _compile_plan(
        self,
        *,
        session_id: str,
        graph: dict[str, Any],
        target_node_ids: list[str] | None,
    ) -> dict[str, Any]:
        normalized = self.validate_graph(
            session_id=session_id,
            graph=graph,
            allow_unbound_inputs=True,
        )
        nodes = self._node_map(normalized)
        data_edges = [edge for edge in normalized["edges"] if edge["role"] == "data"]
        incoming: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in nodes}
        for edge in data_edges:
            incoming[edge["to"]].append(edge)

        requested_targets = list(dict.fromkeys(str(item) for item in list(target_node_ids or []) if str(item) in nodes))
        target_actions: set[str] = set()

        def collect(node_id: str) -> None:
            node = nodes[node_id]
            if node["kind"] == "action":
                target_actions.add(node_id)
            if node["kind"] == "result":
                target_actions.add(str(node["producerActionNodeId"]))
            for edge in incoming[node_id]:
                collect(edge["from"])

        for target in requested_targets:
            collect(target)
        if not requested_targets:
            target_actions = {node_id for node_id, node in nodes.items() if node["kind"] == "action"}
            requested_targets = [node_id for node_id, node in nodes.items() if node["kind"] == "sink"] or sorted(target_actions)
        if not target_actions:
            raise CreativeCanvasGraphError("Canvas graph has no executable action nodes")

        normalized = self.validate_graph(
            session_id=session_id,
            graph=normalized,
            allow_unbound_inputs=False,
            require_executable=True,
            executable_action_ids=target_actions,
        )
        nodes = self._node_map(normalized)
        data_edges = [edge for edge in normalized["edges"] if edge["role"] == "data"]
        incoming = {node_id: [] for node_id in nodes}
        for edge in data_edges:
            incoming[edge["to"]].append(edge)

        dependencies: dict[str, set[str]] = {node_id: set() for node_id in target_actions}
        for action_id in target_actions:
            for edge in incoming[action_id]:
                source = nodes[edge["from"]]
                if source["kind"] == "result":
                    producer = str(source["producerActionNodeId"])
                    if producer in target_actions:
                        dependencies[action_id].add(producer)
        ordered: list[str] = []
        ready = sorted(node_id for node_id, values in dependencies.items() if not values)
        remaining = {node_id: set(values) for node_id, values in dependencies.items()}
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for node_id in sorted(remaining):
                if current in remaining[node_id]:
                    remaining[node_id].remove(current)
                    if not remaining[node_id] and node_id not in ordered and node_id not in ready:
                        ready.append(node_id)
        if len(ordered) != len(target_actions):
            raise CreativeCanvasGraphError("Canvas action dependencies contain a cycle")

        entries: list[dict[str, Any]] = []
        for action_id in ordered:
            action_node = nodes[action_id]
            definition = ACTION_DEFINITIONS[action_node["actionDefinitionId"]]
            result_edges = [
                edge
                for edge in data_edges
                if edge["from"] == action_id and nodes[edge["to"]]["kind"] == "result"
            ]
            input_edges = sorted(incoming[action_id], key=lambda item: (item["toPortId"], item["order"], item["edgeId"]))

            context_node_ids: set[str] = set()

            def collect_context(node_id: str) -> None:
                if node_id in context_node_ids:
                    return
                context_node_ids.add(node_id)
                for edge in incoming[node_id]:
                    collect_context(edge["from"])

            collect_context(action_id)
            relationship_notes = [
                {
                    "edgeId": edge["edgeId"],
                    "fromNodeId": edge["from"],
                    "toNodeId": edge["to"],
                    "note": edge["note"],
                }
                for edge in sorted(normalized["edges"], key=lambda item: item["edgeId"])
                if edge.get("note")
                and edge["from"] in context_node_ids
                and edge["to"] in context_node_ids
            ]
            entries.append({
                "actionNodeId": action_id,
                "actionDefinitionId": definition.action_id,
                "capability": definition.capability,
                "prompt": action_node.get("prompt") or "",
                "parameters": action_node.get("parameters") or {},
                "configurationRevision": action_node.get("configurationRevision") or 1,
                "inputs": [
                    {
                        "edgeId": edge["edgeId"],
                        "portId": edge["toPortId"],
                        "sourceNodeId": edge["from"],
                        "sourceKind": nodes[edge["from"]]["kind"],
                        "resource": {
                            "origin": nodes[edge["from"]].get("origin"),
                            "id": nodes[edge["from"]].get("resourceId"),
                            "mediaType": nodes[edge["from"]].get("mediaType"),
                        } if nodes[edge["from"]]["kind"] == "resource" else None,
                        "resultNodeId": edge["from"] if nodes[edge["from"]]["kind"] == "result" else None,
                        "order": edge["order"],
                        "note": edge.get("note") or "",
                    }
                    for edge in input_edges
                ],
                "relationshipNotes": relationship_notes,
                "resultNodeId": result_edges[0]["to"],
                "outputSlot": definition.output_slot,
                "outputMediaType": nodes[result_edges[0]["to"]].get("mediaType"),
                "dependsOn": sorted(dependencies[action_id]),
            })
        return {
            "schema": "v8.creative_canvas_execution_plan.v1",
            "graphId": normalized["graphId"],
            "targetNodeIds": requested_targets,
            "actions": entries,
            "resourceRefs": self._plan_resource_refs(entries),
            "digest": _digest(entries),
        }

    @staticmethod
    def _plan_resource_refs(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for entry in entries:
            for item in entry["inputs"]:
                resource = _record(item.get("resource"))
                origin = str(resource.get("origin") or "")
                resource_id = str(resource.get("id") or "")
                if not origin or not resource_id or (origin, resource_id) in seen:
                    continue
                seen.add((origin, resource_id))
                refs.append({"origin": origin, "id": resource_id, "mediaType": resource.get("mediaType")})
        return refs

    def execution_contract_summary(
        self,
        *,
        session_id: str,
        graph_id: str,
        graph_revision: int,
        target_node_ids: list[str] | None,
    ) -> dict[str, Any]:
        row = self._graph_row(session_id=session_id)
        if not row or str(row.get("graph_id") or "") != str(graph_id or ""):
            raise CreativeCanvasGraphError("Canvas graph is not bound to the current session")
        if int(row.get("revision") or 0) != int(graph_revision):
            raise CreativeCanvasGraphConflict("Canvas graph revision changed before execution")
        graph = _record(_json(row.get("graph_json"), {}))
        return self._compile_plan(session_id=session_id, graph=graph, target_node_ids=target_node_ids)

    @staticmethod
    def _modality_for_operation(operation_kind: str) -> str:
        prefix = str(operation_kind or "").split(".", 1)[0].lower()
        return "voice" if prefix == "audio" else prefix

    def _run_row(self, *, session_id: str, canvas_operation_id: str) -> dict[str, Any] | None:
        with db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM creative_canvas_graph_runs
                WHERE session_id = ? AND canvas_operation_id = ? LIMIT 1
                """,
                (session_id, canvas_operation_id),
            ).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _public_graph_run_status(status: str) -> str:
        return GRAPH_RUN_PUBLIC_STATUS_BY_INTERNAL.get(str(status or "").strip().lower(), "")

    def _emit_graph_run_state_event(
        self,
        *,
        row: dict[str, Any],
        status: str,
        transition: str = "",
        retry_of_graph_run_id: str = "",
    ) -> bool:
        public_status = self._public_graph_run_status(status)
        if not public_status:
            raise CreativeCanvasGraphError(f"Canvas graph run realtime status is unsupported: {status or 'unknown'}")
        normalized_transition = str(transition or "").strip()
        normalized_retry_id = str(retry_of_graph_run_id or "").strip()
        if normalized_transition not in {"", "retry_failed_branch"}:
            raise CreativeCanvasGraphError(f"Canvas graph run realtime transition is unsupported: {normalized_transition}")
        if normalized_transition == "retry_failed_branch" and not normalized_retry_id:
            raise CreativeCanvasGraphError("Canvas failed-branch retry realtime event requires its original graph run id")

        session_id = str(row.get("session_id") or "").strip()
        graph_id = str(row.get("graph_id") or "").strip()
        graph_run_id = str(row.get("graph_run_id") or "").strip()
        canvas_operation_id = str(row.get("canvas_operation_id") or "").strip()
        try:
            expected_updated_at = str(row.get("updated_at") or "").strip()
            with db.get_connection() as conn:
                current = conn.execute(
                    """
                    SELECT status, updated_at
                    FROM creative_canvas_graph_runs
                    WHERE graph_run_id = ? AND session_id = ? LIMIT 1
                    """,
                    (graph_run_id, session_id),
                ).fetchone()
            current_status = str((current or {})["status"] or "") if current else ""
            current_updated_at = str((current or {})["updated_at"] or "") if current else ""
            if (
                not current
                or self._public_graph_run_status(current_status) != public_status
                or (expected_updated_at and current_updated_at != expected_updated_at)
            ):
                logger.warning(
                    "Skipped stale Canvas graph run realtime state "
                    "(session=%s graph_run=%s requested=%s current=%s expected_updated_at=%s current_updated_at=%s)",
                    session_id,
                    graph_run_id,
                    public_status,
                    self._public_graph_run_status(current_status) or current_status or "missing",
                    expected_updated_at or "missing",
                    current_updated_at or "missing",
                )
                return False
            authority = self._authority(session_id)
            self._assert_graph_workspace_authority(
                session_id=session_id,
                graph_id=graph_id,
                authority=authority,
            )
            workspace_id = str(authority.workspace_id or "").strip()
            if not workspace_id:
                raise CreativeCanvasGraphError("Current session workspace id is unavailable for Canvas realtime state")
            payload: dict[str, Any] = {
                "schema": CANVAS_GRAPH_RUN_STATE_SCHEMA,
                "sessionId": session_id,
                "workspaceId": workspace_id,
                "graphId": graph_id,
                "graphRunId": graph_run_id,
                "canvasOperationId": canvas_operation_id,
                "runId": str(row.get("chat_run_id") or "").strip() or None,
                "status": public_status,
            }
            if public_status in {"failed", "interrupted"}:
                node_states = _record(_json(row.get("node_states_json"), {}))
                current_state = _record(node_states.get(str(row.get("current_node_id") or "")))
                retry_state = self._retry_state(node_states, str(row.get("current_node_id") or ""))
                remote_task_may_continue = bool(retry_state.get("providerCancellationRemoteTaskMayContinue"))
                can_retry = (
                    retry_state.get("recoverable") is not False
                    and not remote_task_may_continue
                )
                payload["recovery"] = {
                    "canRetry": can_retry,
                    "mode": "failed_branch" if can_retry else None,
                }
            if normalized_transition:
                payload["transition"] = normalized_transition
            if normalized_retry_id:
                payload["retryOfGraphRunId"] = normalized_retry_id
            emitter = event_bus.create_emitter(
                session_id=session_id,
                conversation_id=session_id,
                run_id=payload["runId"],
                source=RuntimeSource(
                    plane="engine",
                    component="creative_canvas_graph",
                    node="graph_run_state",
                    agent_id=None,
                ),
            )
            emitted = emitter.emit(
                CANVAS_GRAPH_RUN_STATE_TOPIC,
                payload,
                canvas_run_guard={
                    "graphRunId": graph_run_id,
                    "status": str(status or "").strip().lower(),
                    "updatedAt": expected_updated_at,
                },
            )
            if emitted is None:
                logger.warning(
                    "Skipped stale Canvas graph run realtime state at guarded append "
                    "(session=%s graph_run=%s status=%s updated_at=%s)",
                    session_id,
                    graph_run_id,
                    public_status,
                    expected_updated_at,
                )
                return False
            return True
        except Exception:
            # The graph state is already committed. Keep execution truth intact
            # and expose the non-atomic event append through Engine diagnostics.
            logger.exception(
                "Canvas graph run state committed but durable realtime emission failed "
                "(session=%s graph_run=%s status=%s)",
                session_id,
                graph_run_id,
                public_status,
            )
            return False

    def _write_run_state(
        self,
        *,
        graph_run_id: str,
        status: str,
        node_states: dict[str, Any],
        current_node_id: str = "",
        error: str = "",
        completed: bool = False,
        transition: str = "",
        retry_of_graph_run_id: str = "",
        expected_statuses: set[str] | None = None,
    ) -> bool:
        public_status = self._public_graph_run_status(status)
        if not public_status:
            raise CreativeCanvasGraphError(f"Canvas graph run state is unsupported: {status or 'unknown'}")
        now = _utc_now()
        previous_row: dict[str, Any] | None = None
        with db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM creative_canvas_graph_runs WHERE graph_run_id = ? LIMIT 1",
                (graph_run_id,),
            ).fetchone()
            if not row:
                conn.rollback()
                return False
            previous_row = dict(row)
            if expected_statuses is not None and str(previous_row.get("status") or "") not in expected_statuses:
                conn.rollback()
                return False
            conn.execute(
                """
                UPDATE creative_canvas_graph_runs
                SET status = ?, node_states_json = ?, current_node_id = ?, error_message = ?,
                    updated_at = ?, completed_at = CASE
                        WHEN ? THEN ?
                        WHEN ? THEN NULL
                        ELSE completed_at
                    END
                WHERE graph_run_id = ?
                """,
                (
                    status,
                    json.dumps(node_states, ensure_ascii=False),
                    current_node_id or None,
                    error or None,
                    now,
                    1 if completed else 0,
                    now,
                    1 if status in ACTIVE_GRAPH_STATES else 0,
                    graph_run_id,
                ),
            )
            conn.commit()
        previous_public_status = self._public_graph_run_status(str(previous_row.get("status") or ""))
        if previous_public_status == public_status:
            return True
        committed_row = {
            **previous_row,
            "status": status,
            "node_states_json": json.dumps(node_states, ensure_ascii=False),
            "current_node_id": current_node_id or None,
            "error_message": error or None,
            "updated_at": now,
            "completed_at": now if completed else None if status in ACTIVE_GRAPH_STATES else previous_row.get("completed_at"),
        }
        self._emit_graph_run_state_event(
            row=committed_row,
            status=status,
            transition=transition,
            retry_of_graph_run_id=retry_of_graph_run_id,
        )
        return True

    def _run_result_artifact(self, *, graph_run_id: str, result_node_id: str) -> dict[str, Any] | None:
        with db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM creative_canvas_node_outputs
                WHERE graph_run_id = ? AND result_node_id = ? AND artifact_id IS NOT NULL
                ORDER BY version_index DESC LIMIT 1
                """,
                (graph_run_id, result_node_id),
            ).fetchone()
        return dict(row) if row else None

    def _record_output(
        self,
        *,
        graph_run_id: str,
        graph_id: str,
        session_id: str,
        entry: dict[str, Any],
        job: dict[str, Any],
        artifact: dict[str, Any],
    ) -> dict[str, Any]:
        result_node_id = str(entry["resultNodeId"])
        with db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run_row = conn.execute(
                "SELECT status FROM creative_canvas_graph_runs WHERE graph_run_id = ? LIMIT 1",
                (graph_run_id,),
            ).fetchone()
            if not run_row or str(run_row["status"] or "") != "running":
                conn.rollback()
                raise CreativeCanvasGraphCancelled("Canvas graph run was cancelled before output persistence")
            row = conn.execute(
                """
                SELECT MAX(version_index) AS latest
                FROM creative_canvas_node_outputs
                WHERE graph_id = ? AND result_node_id = ?
                """,
                (graph_id, result_node_id),
            ).fetchone()
            version = int((row or {})["latest"] or 0) + 1
            output_version_id = f"canvas-output-{uuid.uuid4().hex}"
            config_digest = _digest({
                "actionDefinitionId": entry["actionDefinitionId"],
                "prompt": entry.get("prompt"),
                "parameters": entry.get("parameters"),
                "configurationRevision": entry.get("configurationRevision"),
                "relationshipNotes": entry.get("relationshipNotes"),
            })
            conn.execute(
                """
                INSERT INTO creative_canvas_node_outputs(
                    output_version_id, graph_run_id, graph_id, session_id, action_node_id,
                    result_node_id, version_index, artifact_id, job_id, media_type,
                    output_slot, config_digest, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    output_version_id,
                    graph_run_id,
                    graph_id,
                    session_id,
                    entry["actionNodeId"],
                    result_node_id,
                    version,
                    artifact.get("artifactId") or artifact.get("id"),
                    job.get("jobId") or job.get("id"),
                    entry.get("outputMediaType"),
                    entry.get("outputSlot"),
                    config_digest,
                    json.dumps({"artifact": artifact}, ensure_ascii=False),
                    _utc_now(),
                ),
            )
            conn.commit()
        return {"artifact": artifact, "version": version, "outputVersionId": output_version_id}

    def _request_for_entry(
        self,
        *,
        entry: dict[str, Any],
        graph_id: str,
        graph_run_id: str,
        session_id: str,
        chat_run_id: str,
        canvas_operation_id: str,
        project_id: str,
        workspace_id: str,
        workspace_path: str,
    ) -> dict[str, Any]:
        operation_kind = str(entry["capability"])
        prompt = str(entry.get("prompt") or "")
        relationship_notes = [
            str(item.get("note") or "").strip()
            for item in _list(entry.get("relationshipNotes"))
            if str(_record(item).get("note") or "").strip()
        ]
        if relationship_notes:
            relationship_context = "\n".join(f"- {note}" for note in relationship_notes)
            prompt = f"{prompt}\n\n关系说明（来自画布）：\n{relationship_context}".strip()
        request = {
            **_record(entry.get("parameters")),
            "modality": self._modality_for_operation(operation_kind),
            "operationKind": operation_kind,
            "prompt": prompt,
            "canvasOperationId": canvas_operation_id,
            "outputSlot": entry.get("outputSlot"),
            "sessionId": session_id,
            "runId": chat_run_id,
            "projectId": project_id,
            "workspaceId": workspace_id,
            "workspacePath": workspace_path,
            "canvasGraphId": graph_id,
            "canvasGraphRunId": graph_run_id,
            "canvasGraphNodeId": entry["actionNodeId"],
            "canvasResultNodeId": entry["resultNodeId"],
        }
        source_ids: list[str] = []
        artifact_ids: list[str] = []
        workspace_asset_ids: list[str] = []
        canvas_inputs: list[dict[str, Any]] = []
        mask_source_id = ""
        for item in entry["inputs"]:
            resource = _record(item.get("resource"))
            if resource:
                origin = str(resource.get("origin") or "")
                resource_id = str(resource.get("id") or "")
                canvas_inputs.append({
                    "portId": str(item.get("portId") or ""),
                    "sourceNodeId": str(item.get("sourceNodeId") or ""),
                    "origin": origin,
                    "id": resource_id,
                    "mediaType": str(resource.get("mediaType") or "unknown"),
                    "order": int(item.get("order") or 0),
                })
                if str(item.get("portId") or "") == "mask":
                    mask_source_id = resource_id
                elif origin == "source":
                    source_ids.append(resource_id)
                elif origin == "artifact":
                    artifact_ids.append(resource_id)
                elif origin == "workspace_asset":
                    workspace_asset_ids.append(resource_id)
                continue
            result_node_id = str(item.get("resultNodeId") or "")
            output = self._run_result_artifact(graph_run_id=graph_run_id, result_node_id=result_node_id)
            if not output or not output.get("artifact_id"):
                raise CreativeCanvasGraphError(f"Upstream Canvas result is unavailable: {result_node_id}")
            output_artifact_id = str(output["artifact_id"])
            artifact_ids.append(output_artifact_id)
            canvas_inputs.append({
                "portId": str(item.get("portId") or ""),
                "sourceNodeId": str(item.get("sourceNodeId") or ""),
                "origin": "artifact",
                "id": output_artifact_id,
                "mediaType": str(item.get("mediaType") or "unknown"),
                "order": int(item.get("order") or 0),
                "resultNodeId": result_node_id,
            })
        if canvas_inputs:
            request["canvasInputs"] = canvas_inputs
        if source_ids:
            request["sourceId"] = source_ids[0]
            if len(source_ids) > 1:
                request["sourceIds"] = source_ids
        if artifact_ids:
            request["artifactId"] = artifact_ids[0]
            if len(artifact_ids) > 1:
                request["artifactIds"] = artifact_ids
        if workspace_asset_ids:
            request["workspaceAssetId"] = workspace_asset_ids[0]
            if len(workspace_asset_ids) > 1:
                request["workspaceAssetIds"] = workspace_asset_ids
        if mask_source_id:
            request["maskSourceId"] = mask_source_id
        return request

    def _run_status(self, *, graph_run_id: str) -> str:
        with db.get_connection() as conn:
            row = conn.execute(
                "SELECT status FROM creative_canvas_graph_runs WHERE graph_run_id = ? LIMIT 1",
                (graph_run_id,),
            ).fetchone()
        return str(row["status"] or "") if row else ""

    @staticmethod
    async def _invoke_job_lifecycle(runtime: Any, method_name: str, job_id: str) -> dict[str, Any]:
        method = getattr(runtime, method_name, None)
        if not callable(method):
            return {
                "status": "unsupported",
                "detailCode": "lifecycle_method_missing",
                "remoteTaskMayContinue": True,
            }
        try:
            result = method(job_id)
            if inspect.isawaitable(result):
                result = await result
            # Older in-process runtimes exposed lifecycle methods for their
            # side effects only.  Preserve that contract as a local
            # completion while keeping every structured Provider outcome
            # fail-closed below.
            if result is None:
                return {
                    "status": "completed",
                    "detailCode": "legacy_lifecycle_completed",
                    "remoteTaskMayContinue": False,
                }
            if isinstance(result, dict):
                status = str(result.get("status") or "").strip().lower()
                if status in {"completed", "unsupported", "failed", "not_active", "not_started"}:
                    return {
                        **result,
                        "status": status,
                        "remoteTaskMayContinue": bool(
                            result.get("remoteTaskMayContinue", status != "completed")
                        ),
                    }
            return {
                "status": "failed",
                "detailCode": "invalid_lifecycle_result",
                "remoteTaskMayContinue": True,
            }
        except Exception as exc:
            return {
                "status": "failed",
                "detailCode": "lifecycle_call_failed",
                "error": _exception_summary(exc),
                "remoteTaskMayContinue": True,
            }

    async def _cancel_and_cleanup_job(self, runtime: Any, job_id: str) -> dict[str, Any]:
        if not job_id:
            return {
                "providerCancellation": "not_started",
                "providerCancellationDetailCode": "no_provider_job_handle",
                # The executor reached the provider boundary but has no
                # durable handle. Treat that as uncertain so retry cannot
                # submit a second remote task over an untracked one.
                "providerCancellationRemoteTaskMayContinue": True,
                "providerCancellationError": "",
                "providerCleanup": "not_started",
                "providerCleanupDetailCode": "no_provider_job_handle",
                "providerCleanupError": "",
            }
        cancellation_report = await self._invoke_job_lifecycle(runtime, "cancel_job", job_id)
        cancellation = str(cancellation_report.get("status") or "failed")
        if cancellation == "unsupported":
            get_job = getattr(runtime, "get_job", None)
            save_job = getattr(runtime, "_save_job", None)
            if callable(get_job) and callable(save_job):
                stored = get_job(job_id, refresh=False)
                if isinstance(stored, dict):
                    stored = dict(stored)
                    stored["status"] = "cancelled"
                    stored["error"] = stored.get("error") or "Parent Canvas graph run was cancelled"
                    stored["completedAt"] = _utc_now()
                    save_job(stored)
        cleanup_report = await self._invoke_job_lifecycle(runtime, "cleanup_job", job_id)
        cleanup = str(cleanup_report.get("status") or "failed")
        return {
            "providerCancellation": cancellation,
            "providerCancellationDetailCode": str(cancellation_report.get("detailCode") or ""),
            "providerCancellationRemoteTaskMayContinue": bool(
                cancellation_report.get("remoteTaskMayContinue", cancellation != "completed")
            ),
            "providerCancellationError": str(cancellation_report.get("error") or ""),
            "providerCleanup": cleanup,
            "providerCleanupDetailCode": str(cleanup_report.get("detailCode") or ""),
            "providerCleanupError": str(cleanup_report.get("error") or ""),
        }

    def _claim_run_cancellation(
        self,
        *,
        session_id: str,
        graph_run_id: str,
        reason: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM creative_canvas_graph_runs WHERE graph_run_id = ? AND session_id = ? LIMIT 1",
                (graph_run_id, session_id),
            ).fetchone()
            if not row:
                conn.rollback()
                raise CreativeCanvasGraphError("Canvas graph run is not bound to the current session")
            run = dict(row)
            status = str(run.get("status") or "")
            if status in {"cancelling", "cancelled"}:
                conn.commit()
                return {"claimed": False, "run": run}
            if status not in {"queued", "running"}:
                conn.rollback()
                raise CreativeCanvasGraphConflict(
                    f"Canvas graph run cannot be cancelled from status={status or 'unknown'}"
                )

            node_states = _record(_json(run.get("node_states_json"), {}))
            current_node_id = str(run.get("current_node_id") or "")
            current_state = _record(node_states.get(current_node_id))
            current_state.update({
                "state": "cancelling",
                "errorCode": reason or "user_cancelled",
                "error": "Canvas graph run cancellation was requested",
                "cancelRequestedAt": now,
                "recoverable": False,
            })
            if current_node_id:
                node_states[current_node_id] = current_state
            cursor = conn.execute(
                """
                UPDATE creative_canvas_graph_runs
                SET status = 'cancelling', node_states_json = ?, current_node_id = ?,
                    error_message = ?, updated_at = ?, completed_at = NULL
                WHERE graph_run_id = ? AND session_id = ? AND status IN ('queued', 'running')
                """,
                (
                    json.dumps(node_states, ensure_ascii=False),
                    current_node_id or None,
                    "Canvas graph run cancellation was requested",
                    now,
                    graph_run_id,
                    session_id,
                ),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                raise CreativeCanvasGraphConflict("Canvas graph run changed before cancellation could be claimed")
            conn.commit()

        claimed_run = {
            **run,
            "status": "cancelling",
            "node_states_json": json.dumps(node_states, ensure_ascii=False),
            "current_node_id": current_node_id or None,
            "error_message": "Canvas graph run cancellation was requested",
            "updated_at": now,
            "completed_at": None,
        }
        self._emit_graph_run_state_event(row=claimed_run, status="cancelling")
        return {
            "claimed": True,
            "run": claimed_run,
            "nodeStates": node_states,
            "currentNodeId": current_node_id,
            "currentState": current_state,
        }

    def _finish_run_cancellation(
        self,
        *,
        session_id: str,
        graph_run_id: str,
        reason: str,
        lifecycle: dict[str, Any],
    ) -> bool:
        now = _utc_now()
        with db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM creative_canvas_graph_runs WHERE graph_run_id = ? AND session_id = ? LIMIT 1",
                (graph_run_id, session_id),
            ).fetchone()
            if not row:
                conn.rollback()
                return False
            run = dict(row)
            if str(run.get("status") or "") not in {"cancelling", "cancelled"}:
                conn.rollback()
                return False
            node_states = _record(_json(run.get("node_states_json"), {}))
            current_node_id = str(run.get("current_node_id") or "")
            if current_node_id:
                current_state = _record(node_states.get(current_node_id))
                current_state.update({
                    **lifecycle,
                    "state": "cancelled",
                    "errorCode": reason or str(current_state.get("errorCode") or "user_cancelled"),
                    "error": "Canvas graph run was cancelled",
                    "recoverable": False,
                    "completedAt": now,
                })
                node_states[current_node_id] = current_state
            conn.execute(
                """
                UPDATE creative_canvas_graph_runs
                SET status = 'cancelled', node_states_json = ?, current_node_id = ?,
                    error_message = ?, updated_at = ?, completed_at = ?
                WHERE graph_run_id = ? AND session_id = ? AND status IN ('cancelling', 'cancelled')
                """,
                (
                    json.dumps(node_states, ensure_ascii=False),
                    current_node_id or None,
                    "Canvas graph run was cancelled",
                    now,
                    now,
                    graph_run_id,
                    session_id,
                ),
            )
            conn.commit()
        if self._public_graph_run_status(str(run.get("status") or "")) != "cancelled":
            self._emit_graph_run_state_event(
                row={
                    **run,
                    "status": "cancelled",
                    "node_states_json": json.dumps(node_states, ensure_ascii=False),
                    "current_node_id": current_node_id or None,
                    "error_message": "Canvas graph run was cancelled",
                    "updated_at": now,
                    "completed_at": now,
                },
                status="cancelled",
            )
        return True

    async def cancel_run(
        self,
        runtime: Any,
        *,
        session_id: str,
        graph_run_id: str,
        reason: str = "user_cancelled",
    ) -> dict[str, Any]:
        self._authority(session_id, require_write=True)
        claim = self._claim_run_cancellation(
            session_id=session_id,
            graph_run_id=graph_run_id,
            reason=reason or "user_cancelled",
        )
        if not claim.get("claimed"):
            return self._project_run(_record(claim.get("run")))
        current_state = _record(claim.get("currentState"))
        lifecycle = await self._cancel_and_cleanup_job(runtime, str(current_state.get("jobId") or ""))
        self._finish_run_cancellation(
            session_id=session_id,
            graph_run_id=graph_run_id,
            reason=reason or "user_cancelled",
            lifecycle=lifecycle,
        )
        active_task = self._active_tasks.get(graph_run_id)
        if active_task and active_task is not asyncio.current_task() and not active_task.done():
            active_task.cancel()
        return self.get_run(session_id=session_id, graph_run_id=graph_run_id)

    async def retry_failed_run(
        self,
        runtime: Any,
        *,
        session_id: str,
        graph_run_id: str,
        claim: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prepared = claim or self.claim_failed_retry(session_id=session_id, graph_run_id=graph_run_id)
        run = _record(prepared.get("run"))
        graph_revision = int(prepared.get("graphRevision") or 0)
        plan = _record(prepared.get("plan"))
        context = _record(plan.get("executionContext"))
        request = {
            "modality": "workflow",
            "operationKind": "canvas.graph.execute",
            "sessionId": session_id,
            "runId": str(run.get("chat_run_id") or ""),
            "graphId": str(run.get("graph_id") or ""),
            "graphRevision": graph_revision,
            "canvasOperationId": str(run.get("canvas_operation_id") or ""),
            "retryGraphRunId": graph_run_id,
            "targetNodeIds": [str(item) for item in _list(_json(run.get("target_node_ids_json"), []))],
            "projectId": str(prepared.get("projectId") or ""),
            "workspaceId": str(prepared.get("workspaceId") or ""),
            "workspacePath": str(prepared.get("workspacePath") or ""),
            "timeoutSeconds": max(30.0, min(float(context.get("timeoutSeconds") or 600), 1800.0)),
        }
        return await self.execute_as_creative_job(runtime, request, retry_claim=prepared)

    def claim_failed_retry(self, *, session_id: str, graph_run_id: str) -> dict[str, Any]:
        authority = self._authority(session_id, require_write=True)
        workspace_key = self._workspace_key(authority)
        now = _utc_now()
        with db.get_connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM creative_canvas_graph_runs WHERE graph_run_id = ? AND session_id = ? LIMIT 1",
                (graph_run_id, session_id),
            ).fetchone()
            if not row:
                conn.rollback()
                raise CreativeCanvasGraphError("Canvas graph run is not bound to the current session")
            run = dict(row)
            graph_row = conn.execute(
                """
                SELECT graph_id, revision, workspace_key
                FROM creative_canvas_graphs
                WHERE graph_id = ? AND session_id = ? LIMIT 1
                """,
                (str(run.get("graph_id") or ""), session_id),
            ).fetchone()
            if not graph_row:
                conn.rollback()
                raise CreativeCanvasGraphError("Canvas graph is not bound to the current session")
            if str(graph_row["workspace_key"] or "") != workspace_key:
                conn.rollback()
                raise CreativeCanvasGraphConflict("Canvas graph workspace binding changed before execution")
            status = str(run.get("status") or "")
            if status not in RETRYABLE_GRAPH_STATES:
                conn.rollback()
                raise CreativeCanvasGraphConflict(f"Canvas graph run cannot retry from status={status or 'unknown'}")
            graph_revision = int(run.get("graph_revision") or 0)
            if int(graph_row["revision"] or 0) != graph_revision:
                conn.rollback()
                raise CreativeCanvasGraphConflict("Canvas graph revision changed before failed-branch retry")
            node_states = _record(_json(run.get("node_states_json"), {}))
            current_node_id = str(run.get("current_node_id") or "")
            current_state = self._retry_state(node_states, current_node_id)
            if current_state.get("recoverable") is False or bool(
                current_state.get("providerCancellationRemoteTaskMayContinue")
            ):
                conn.rollback()
                raise CreativeCanvasGraphConflict(
                    "Canvas graph failed-branch retry is blocked while the provider task may still continue"
                )
            cursor = conn.execute(
                """
                UPDATE creative_canvas_graph_runs
                SET status = 'running', error_message = NULL, completed_at = NULL, updated_at = ?
                WHERE graph_run_id = ? AND session_id = ? AND status IN ('failed', 'interrupted')
                """,
                (now, graph_run_id, session_id),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                raise CreativeCanvasGraphConflict("Canvas graph failed-branch retry was already claimed")
            conn.commit()
        run.update({"status": "running", "error_message": None, "completed_at": None, "updated_at": now})
        self._emit_graph_run_state_event(
            row=run,
            status="running",
            transition="retry_failed_branch",
            retry_of_graph_run_id=graph_run_id,
        )
        plan = _record(_json(run.get("plan_json"), {}))
        return {
            "run": run,
            "projectedRun": self._project_run(run),
            "graphRevision": graph_revision,
            "plan": plan,
            "projectId": str(authority.project_id or ""),
            "workspaceId": str(authority.workspace_id or ""),
            "workspacePath": str(authority.workspace_root or ""),
        }

    async def _wait_for_job(
        self,
        runtime: Any,
        job: dict[str, Any],
        *,
        graph_run_id: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        current = dict(job)
        while str(current.get("status") or "").lower() not in TERMINAL_JOB_STATES:
            if self._run_status(graph_run_id=graph_run_id) in {"cancelling", "cancelled"}:
                raise CreativeCanvasGraphCancelled("Canvas graph run was cancelled")
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Canvas action exceeded its governed execution deadline")
            await asyncio.sleep(1.5)
            current = dict(await runtime.refresh_job(str(current.get("jobId") or current.get("id"))))
        if self._run_status(graph_run_id=graph_run_id) in {"cancelling", "cancelled"}:
            raise CreativeCanvasGraphCancelled("Canvas graph run was cancelled")
        return current

    async def execute_as_creative_job(
        self,
        runtime: Any,
        request: dict[str, Any],
        *,
        retry_claim: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        session_id = str(request.get("sessionId") or "").strip()
        graph_id = str(request.get("graphId") or "").strip()
        graph_revision = int(request.get("graphRevision") or 0)
        canvas_operation_id = str(request.get("canvasOperationId") or "").strip()
        requested_graph_run_id = str(request.get("graphRunId") or "").strip()
        retry_graph_run_id = str(request.get("retryGraphRunId") or "").strip()
        chat_run_id = str(request.get("runId") or "").strip()
        target_node_ids = [str(item) for item in _list(request.get("targetNodeIds")) if str(item).strip()]
        if not session_id or not graph_id or not graph_revision or not canvas_operation_id:
            raise CreativeCanvasGraphError("Canvas graph execution requires session, graph, revision, and operation ids")
        if requested_graph_run_id and not re.fullmatch(r"canvas-run-[a-f0-9]{32}", requested_graph_run_id):
            raise CreativeCanvasGraphError("Canvas graph run id is invalid")
        # Canvas execution is session/workspace authoritative.  A direct Canvas
        # run must never inherit arbitrary client-supplied workspace identity,
        # and the same rule applies to the legacy Supervisor-mediated retry.
        authority = self._authority(session_id, require_write=True)
        self._assert_graph_workspace_authority(
            session_id=session_id,
            graph_id=graph_id,
            authority=authority,
        )
        request = {
            **request,
            "projectId": str(authority.project_id or ""),
            "workspaceId": str(authority.workspace_id or ""),
            "workspacePath": str(authority.workspace_root or ""),
        }

        outer_job = runtime._new_job(modality="workflow", adapter="canvas_graph", request=request)
        outer_job["operationKind"] = "canvas.graph.execute"
        outer_job["canvasOperationId"] = canvas_operation_id
        outer_job["status"] = "running"
        runtime._save_job(outer_job)
        graph_run_id = ""
        active_inner_job: dict[str, Any] = {}
        try:
            claimed_retry = _record(retry_claim)
            if retry_graph_run_id and not claimed_retry:
                claimed_retry = self.claim_failed_retry(
                    session_id=session_id,
                    graph_run_id=retry_graph_run_id,
                )
            claimed_run = _record(claimed_retry.get("run"))
            if retry_graph_run_id:
                if (
                    str(claimed_run.get("graph_run_id") or "") != retry_graph_run_id
                    or str(claimed_run.get("session_id") or "") != session_id
                    or str(claimed_run.get("graph_id") or "") != graph_id
                    or str(claimed_run.get("canvas_operation_id") or "") != canvas_operation_id
                    or int(claimed_run.get("graph_revision") or 0) != graph_revision
                ):
                    raise CreativeCanvasGraphConflict(
                        "Canvas failed-branch retry must reuse its claimed run, operation, and revision"
                    )
                plan = _record(claimed_retry.get("plan"))
            else:
                plan = self.execution_contract_summary(
                    session_id=session_id,
                    graph_id=graph_id,
                    graph_revision=graph_revision,
                    target_node_ids=target_node_ids,
                )
            plan = {
                **plan,
                "executionContext": {
                    "timeoutSeconds": max(30.0, min(float(request.get("timeoutSeconds") or 600), 1800.0)),
                },
            }
            created_graph_run = False
            with db.get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                graph_row = conn.execute(
                    "SELECT revision FROM creative_canvas_graphs WHERE graph_id = ? AND session_id = ? LIMIT 1",
                    (graph_id, session_id),
                ).fetchone()
                if not graph_row or int(graph_row["revision"] or 0) != graph_revision:
                    conn.rollback()
                    raise CreativeCanvasGraphConflict("Canvas graph changed before its run could start")
                existing_row = conn.execute(
                    "SELECT * FROM creative_canvas_graph_runs WHERE session_id = ? AND canvas_operation_id = ? LIMIT 1",
                    (session_id, canvas_operation_id),
                ).fetchone()
                existing = dict(existing_row) if existing_row else None
                if retry_graph_run_id:
                    if not existing or str(existing.get("graph_run_id") or "") != retry_graph_run_id:
                        conn.rollback()
                        raise CreativeCanvasGraphConflict("Canvas failed-branch retry must reuse its original run and operation ids")
                    existing_status = str(existing.get("status") or "")
                    if existing_status in {"cancelling", "cancelled"}:
                        conn.rollback()
                        raise CreativeCanvasGraphCancelled("Canvas graph retry was cancelled before execution started")
                    if existing_status != "running":
                        conn.rollback()
                        raise CreativeCanvasGraphConflict(
                            f"Canvas graph retry claim is no longer active: status={existing_status or 'unknown'}"
                        )
                if existing and int(existing.get("graph_revision") or 0) != graph_revision:
                    conn.rollback()
                    raise CreativeCanvasGraphConflict("Canvas graph operation cannot resume against a different revision")
                if not existing:
                    active = conn.execute(
                        "SELECT graph_run_id FROM creative_canvas_graph_runs WHERE session_id = ? AND status IN ('queued', 'running', 'cancelling') LIMIT 1",
                        (session_id,),
                    ).fetchone()
                    if active:
                        conn.rollback()
                        raise CreativeCanvasGraphConflict("Another Canvas graph run is already active for this session")
                    graph_run_id = requested_graph_run_id or f"canvas-run-{uuid.uuid4().hex}"
                    node_states = {
                        entry["actionNodeId"]: {
                            "state": "queued",
                            "attempt": 0,
                            "configurationRevision": entry.get("configurationRevision") or 1,
                        }
                        for entry in plan["actions"]
                    }
                    now = _utc_now()
                    conn.execute(
                        """
                        INSERT INTO creative_canvas_graph_runs(
                            graph_run_id, graph_id, session_id, chat_run_id, canvas_operation_id,
                            graph_revision, target_node_ids_json, plan_json, node_states_json,
                            status, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                        """,
                        (
                            graph_run_id,
                            graph_id,
                            session_id,
                            chat_run_id or None,
                            canvas_operation_id,
                            graph_revision,
                            json.dumps(plan["targetNodeIds"], ensure_ascii=False),
                            json.dumps(plan, ensure_ascii=False),
                            json.dumps(node_states, ensure_ascii=False),
                            now,
                            now,
                        ),
                    )
                    conn.commit()
                    created_graph_run = True
                else:
                    conn.commit()
                    graph_run_id = str(existing["graph_run_id"])
                    node_states = _record(_json(existing.get("node_states_json"), {}))
                    stored_plan = _record(_json(existing.get("plan_json"), {}))
                    if _list(stored_plan.get("actions")):
                        plan = stored_plan
                    canvas_operation_id = str(existing.get("canvas_operation_id") or canvas_operation_id)

            if created_graph_run:
                created_row = self._run_row(
                    session_id=session_id,
                    canvas_operation_id=canvas_operation_id,
                )
                if not created_row:
                    raise CreativeCanvasGraphError("Canvas graph run disappeared before queued state publication")
                self._emit_graph_run_state_event(row=created_row, status="queued")
                if not self._write_run_state(
                    graph_run_id=graph_run_id,
                    status="running",
                    node_states=node_states,
                    expected_statuses={"queued"},
                ):
                    raise CreativeCanvasGraphCancelled("Canvas graph run was cancelled before execution started")
            elif self._run_status(graph_run_id=graph_run_id) != "running":
                raise CreativeCanvasGraphCancelled("Canvas graph retry was cancelled before execution started")
            current_task = asyncio.current_task()
            if current_task:
                self._active_tasks[graph_run_id] = current_task
            all_artifacts: list[dict[str, Any]] = []
            for entry in plan["actions"]:
                node_id = str(entry["actionNodeId"])
                current_state = _record(node_states.get(node_id))
                if current_state.get("state") == "succeeded":
                    output = self._run_result_artifact(
                        graph_run_id=graph_run_id,
                        result_node_id=str(entry["resultNodeId"]),
                    )
                    artifact = db.get_runtime_artifact(str((output or {}).get("artifact_id") or "")) if output else None
                    if artifact:
                        all_artifacts.append(artifact)
                        continue
                    raise CreativeCanvasGraphError(
                        f"Succeeded Canvas ancestor artifact is unavailable; refusing a paid rerun: {node_id}"
                    )
                for stale_key in (
                    "error",
                    "errorCode",
                    "completedAt",
                    "interruptedAt",
                    "priorState",
                    "providerCancellation",
                    "providerCleanup",
                ):
                    current_state.pop(stale_key, None)
                current_state.update({
                    "state": "running",
                    "attempt": int(current_state.get("attempt") or 0) + 1,
                    "startedAt": _utc_now(),
                    "recoverable": False,
                })
                node_states[node_id] = current_state
                if not self._write_run_state(
                    graph_run_id=graph_run_id,
                    status="running",
                    node_states=node_states,
                    current_node_id=node_id,
                    expected_statuses={"running"},
                ):
                    raise CreativeCanvasGraphCancelled("Canvas graph run was cancelled before provider submission")
                inner_request = self._request_for_entry(
                    entry=entry,
                    graph_id=graph_id,
                    graph_run_id=graph_run_id,
                    session_id=session_id,
                    chat_run_id=chat_run_id,
                    canvas_operation_id=canvas_operation_id,
                    project_id=str(request.get("projectId") or request.get("project_id") or "").strip(),
                    workspace_id=str(request.get("workspaceId") or request.get("workspace_id") or "").strip(),
                    workspace_path=str(request.get("workspacePath") or request.get("workspace_path") or "").strip(),
                )
                reserve_job_id = getattr(runtime, "_reserve_job_id", None)
                reserved_job_id = str(reserve_job_id() or "").strip() if callable(reserve_job_id) else ""
                if reserved_job_id:
                    current_state["jobId"] = reserved_job_id
                    node_states[node_id] = current_state
                    if not self._write_run_state(
                        graph_run_id=graph_run_id,
                        status="running",
                        node_states=node_states,
                        current_node_id=node_id,
                        expected_statuses={"running"},
                    ):
                        raise CreativeCanvasGraphCancelled("Canvas graph run was cancelled before provider reservation")
                    active_inner_job = {"jobId": reserved_job_id}
                if self._run_status(graph_run_id=graph_run_id) != "running":
                    raise CreativeCanvasGraphCancelled("Canvas graph run was cancelled before provider submission")
                inner_job = await (
                    runtime.create_job(inner_request, reserved_job_id=reserved_job_id)
                    if reserved_job_id
                    else runtime.create_job(inner_request)
                )
                active_inner_job = dict(inner_job)
                current_state["jobId"] = inner_job.get("jobId") or inner_job.get("id")
                node_states[node_id] = current_state
                if not self._write_run_state(
                    graph_run_id=graph_run_id,
                    status="running",
                    node_states=node_states,
                    current_node_id=node_id,
                    expected_statuses={"running"},
                ):
                    raise CreativeCanvasGraphCancelled("Canvas graph run was cancelled during provider submission")
                inner_job = await self._wait_for_job(
                    runtime,
                    inner_job,
                    graph_run_id=graph_run_id,
                    timeout_seconds=max(30.0, min(float(request.get("timeoutSeconds") or 600), 1800.0)),
                )
                active_inner_job = dict(inner_job)
                inner_status = str(inner_job.get("status") or "").lower()
                if inner_status == "cancelled":
                    raise CreativeCanvasGraphCancelled("Canvas action provider job was cancelled")
                if inner_status != "succeeded":
                    raise CreativeCanvasGraphError(
                        str(inner_job.get("error") or f"Canvas action failed: {entry['actionDefinitionId']}")
                    )
                artifacts = [dict(item) for item in _list(inner_job.get("artifacts")) if isinstance(item, dict)]
                if not artifacts:
                    raise CreativeCanvasGraphError(f"Canvas action produced no governed artifact: {entry['actionDefinitionId']}")
                if self._run_status(graph_run_id=graph_run_id) != "running":
                    raise CreativeCanvasGraphCancelled("Canvas graph run was cancelled before output persistence")
                recorded = self._record_output(
                    graph_run_id=graph_run_id,
                    graph_id=graph_id,
                    session_id=session_id,
                    entry=entry,
                    job=inner_job,
                    artifact=artifacts[0],
                )
                current_state.update({
                    "state": "succeeded",
                    "completedAt": _utc_now(),
                    "resultNodeId": entry["resultNodeId"],
                    "artifactId": artifacts[0].get("artifactId") or artifacts[0].get("id"),
                    "outputVersionId": recorded["outputVersionId"],
                    "version": recorded["version"],
                    "recoverable": False,
                })
                node_states[node_id] = current_state
                all_artifacts.extend(artifacts)
                active_inner_job = {}
                if not self._write_run_state(
                    graph_run_id=graph_run_id,
                    status="running",
                    node_states=node_states,
                    current_node_id=node_id,
                    expected_statuses={"running"},
                ):
                    raise CreativeCanvasGraphCancelled("Canvas graph run was cancelled after output persistence")

            if not self._write_run_state(
                graph_run_id=graph_run_id,
                status="succeeded",
                node_states=node_states,
                completed=True,
                expected_statuses={"running"},
            ):
                raise CreativeCanvasGraphCancelled("Canvas graph run was cancelled before completion")
            outer_job["status"] = "succeeded"
            outer_job["qualityStatus"] = "passed"
            outer_job["artifacts"] = list({str(item.get("artifactId") or item.get("id")): item for item in all_artifacts}.values())
            outer_job["canvasGraphRunId"] = graph_run_id
            outer_job["completedAt"] = _utc_now()
        except CreativeCanvasGraphCancelled as exc:
            message = str(exc) or "Canvas graph run was cancelled"
            existing = self._run_row(session_id=session_id, canvas_operation_id=canvas_operation_id)
            explicit_cancel = bool(existing and str(existing.get("status") or "") in {"cancelling", "cancelled"})
            if existing:
                states = _record(_json(existing.get("node_states_json"), {}))
                current_node_id = str(existing.get("current_node_id") or "")
                state = _record(states.get(current_node_id))
                if active_inner_job and not explicit_cancel and state.get("providerCancellation") != "completed":
                    state.update(await self._cancel_and_cleanup_job(
                        runtime,
                        str(active_inner_job.get("jobId") or active_inner_job.get("id") or ""),
                    ))
                if current_node_id and not explicit_cancel:
                    states[current_node_id] = {
                        **state,
                        "state": "cancelled",
                        "errorCode": str(state.get("errorCode") or "user_cancelled"),
                        "error": message,
                        "recoverable": False,
                        "completedAt": _utc_now(),
                    }
                graph_run_id = str(existing["graph_run_id"])
                if not explicit_cancel:
                    self._write_run_state(
                        graph_run_id=graph_run_id,
                        status="cancelled",
                        node_states=states,
                        current_node_id=current_node_id,
                        error=message,
                        completed=True,
                        expected_statuses={"queued", "running", "cancelling"},
                    )
            outer_job["status"] = "cancelled"
            outer_job["error"] = message
            outer_job["canvasGraphRunId"] = graph_run_id or None
            outer_job["completedAt"] = _utc_now()
        except asyncio.CancelledError:
            existing = self._run_row(session_id=session_id, canvas_operation_id=canvas_operation_id)
            explicit_cancel = bool(existing and str(existing.get("status") or "") in {"cancelling", "cancelled"})
            if existing:
                states = _record(_json(existing.get("node_states_json"), {}))
                current_node_id = str(existing.get("current_node_id") or "")
                state = _record(states.get(current_node_id))
                if active_inner_job and not explicit_cancel and state.get("providerCancellation") != "completed":
                    state.update(await self._cancel_and_cleanup_job(
                        runtime,
                        str(active_inner_job.get("jobId") or active_inner_job.get("id") or ""),
                    ))
                if current_node_id and not explicit_cancel:
                    states[current_node_id] = {
                        **state,
                        "state": "cancelled",
                        "errorCode": str(state.get("errorCode") or "parent_graph_cancelled"),
                        "error": "Parent Canvas graph execution task was cancelled",
                        "recoverable": False,
                        "completedAt": _utc_now(),
                    }
                graph_run_id = str(existing["graph_run_id"])
                if not explicit_cancel:
                    self._write_run_state(
                        graph_run_id=graph_run_id,
                        status="cancelled",
                        node_states=states,
                        current_node_id=current_node_id,
                        error="Parent Canvas graph execution task was cancelled",
                        completed=True,
                        expected_statuses={"queued", "running", "cancelling"},
                    )
            outer_job["status"] = "cancelled"
            outer_job["error"] = "Parent Canvas graph execution task was cancelled"
            outer_job["canvasGraphRunId"] = graph_run_id or None
            outer_job["completedAt"] = _utc_now()
            saved_outer_job = runtime._save_job(outer_job)
            if explicit_cancel:
                return saved_outer_job
            raise
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            existing = self._run_row(session_id=session_id, canvas_operation_id=canvas_operation_id)
            if existing:
                states = _record(_json(existing.get("node_states_json"), {}))
                current_node_id = str(existing.get("current_node_id") or "")
                graph_run_id = str(existing["graph_run_id"])
                explicit_cancel = str(existing.get("status") or "") in {"cancelling", "cancelled"}
                lifecycle: dict[str, Any] = {}
                if active_inner_job and not explicit_cancel:
                    job_id = str(active_inner_job.get("jobId") or active_inner_job.get("id") or "")
                    try:
                        lifecycle = await self._cancel_and_cleanup_job(runtime, job_id)
                    except Exception as lifecycle_exc:
                        lifecycle = {
                            "providerCancellation": "failed",
                            "providerCancellationDetailCode": "graph_failure_cleanup_failed",
                            "providerCancellationRemoteTaskMayContinue": True,
                            "providerCancellationError": _exception_summary(lifecycle_exc),
                            "providerCleanup": "failed",
                            "providerCleanupDetailCode": "graph_failure_cleanup_failed",
                            "providerCleanupError": _exception_summary(lifecycle_exc),
                        }
                latest = self._run_row(session_id=session_id, canvas_operation_id=canvas_operation_id) or existing
                latest_status = str(latest.get("status") or "")
                if latest_status in {"cancelling", "cancelled"}:
                    outer_job["status"] = "cancelled"
                    outer_job["error"] = "Canvas graph run was cancelled"
                else:
                    state = _record(states.get(current_node_id))
                    state.update(lifecycle)
                    remote_task_may_continue = bool(
                        state.get("providerCancellationRemoteTaskMayContinue")
                    )
                    cleanup_status = str(state.get("providerCleanup") or "")
                    state.update({
                        "state": "failed",
                        "errorCode": "provider_job_failed",
                        "error": message,
                        "recoverable": not remote_task_may_continue and cleanup_status != "failed",
                        "completedAt": _utc_now(),
                    })
                    if current_node_id:
                        states[current_node_id] = state
                    self._write_run_state(
                        graph_run_id=graph_run_id,
                        status="failed",
                        node_states=states,
                        current_node_id=current_node_id,
                        error=message,
                        completed=True,
                        expected_statuses={"running"},
                    )
                    after_write = self._run_row(
                        session_id=session_id,
                        canvas_operation_id=canvas_operation_id,
                    ) or latest
                    if str(after_write.get("status") or "") in {"cancelling", "cancelled"}:
                        outer_job["status"] = "cancelled"
                        outer_job["error"] = "Canvas graph run was cancelled"
                    else:
                        outer_job["status"] = "failed"
                        outer_job["error"] = message
            else:
                outer_job["status"] = "failed"
                outer_job["error"] = message
            outer_job["canvasGraphRunId"] = graph_run_id or None
            outer_job["completedAt"] = _utc_now()
        finally:
            current_task = asyncio.current_task()
            if graph_run_id and self._active_tasks.get(graph_run_id) is current_task:
                self._active_tasks.pop(graph_run_id, None)
        return runtime._save_job(outer_job)

    @staticmethod
    def _template_title(value: Any) -> str:
        title = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", " ", str(value or "")).strip()
        if not title:
            raise CreativeCanvasGraphError("Workflow template title is required")
        if len(title) > MAX_TEMPLATE_TITLE:
            raise CreativeCanvasGraphError(f"Workflow template title is limited to {MAX_TEMPLATE_TITLE} characters")
        return title

    def _template_graph(self, graph: dict[str, Any]) -> dict[str, Any]:
        converted = json.loads(json.dumps(graph, ensure_ascii=False))
        for index, node in enumerate(converted.get("nodes") or []):
            if node.get("kind") != "resource":
                continue
            media_type = str(node.get("mediaType") or "unknown")
            node.clear()
            node.update({
                "nodeId": graph["nodes"][index]["nodeId"],
                "kind": "input",
                "x": graph["nodes"][index]["x"],
                "y": graph["nodes"][index]["y"],
                "width": graph["nodes"][index]["width"],
                "height": graph["nodes"][index]["height"],
                "title": graph["nodes"][index].get("title") or "素材输入",
                "acceptedMediaTypes": [media_type],
                "mediaType": media_type,
            })
        return {
            "schema": TEMPLATE_SCHEMA,
            "version": 1,
            "nodes": converted.get("nodes") or [],
            "edges": converted.get("edges") or [],
            "viewport": converted.get("viewport") or {"x": 24, "y": 24, "scale": 1},
        }

    def save_template(self, *, session_id: str, title: str, description: str = "") -> dict[str, Any]:
        authority = self._authority(session_id, require_write=True)
        row = self._graph_row(session_id=session_id)
        if not row:
            raise CreativeCanvasGraphError("Current session has no Canvas graph")
        graph = _record(_json(row.get("graph_json"), {}))
        if not any(_record(node).get("kind") == "action" for node in _list(graph.get("nodes"))):
            raise CreativeCanvasGraphError("Workflow templates require at least one action card")
        template_graph = self._template_graph(graph)
        self.validate_graph(
            session_id=session_id,
            graph={**template_graph, "schema": GRAPH_SCHEMA, "version": GRAPH_VERSION},
            allow_unbound_inputs=True,
            validate_resources=False,
        )
        template_id = f"canvas-template-{uuid.uuid4().hex}"
        normalized_title = self._template_title(title)
        now = _utc_now()
        with db.get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO workspace_canvas_templates(
                        template_id, workspace_key, workspace_id, project_id, title, description,
                        schema_version, revision, template_json, created_from_session_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, ?)
                    """,
                    (
                        template_id,
                        self._workspace_key(authority),
                        authority.workspace_id or None,
                        authority.project_id or None,
                        normalized_title,
                        _clean_text(description, limit=500),
                        json.dumps(template_graph, ensure_ascii=False),
                        session_id,
                        now,
                        now,
                    ),
                )
                conn.commit()
            except Exception as exc:
                if "unique" in str(exc).lower():
                    raise CreativeCanvasGraphConflict("A workflow template with this title already exists") from exc
                raise
        return self.get_template(session_id=session_id, template_id=template_id)

    def list_templates(self, *, session_id: str) -> list[dict[str, Any]]:
        authority = self._authority(session_id)
        with db.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT template_id, title, description, revision, created_at, updated_at, template_json
                FROM workspace_canvas_templates
                WHERE workspace_key = ? ORDER BY updated_at DESC
                """,
                (self._workspace_key(authority),),
            ).fetchall()
        return [{
            "templateId": row["template_id"],
            "title": row["title"],
            "description": row["description"],
            "revision": row["revision"],
            "inputCount": sum(1 for node in _list(_record(_json(row["template_json"], {})).get("nodes")) if _record(node).get("kind") == "input"),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        } for row in rows]

    def get_template(self, *, session_id: str, template_id: str) -> dict[str, Any]:
        authority = self._authority(session_id)
        with db.get_connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM workspace_canvas_templates
                WHERE template_id = ? AND workspace_key = ? LIMIT 1
                """,
                (template_id, self._workspace_key(authority)),
            ).fetchone()
        if not row:
            raise CreativeCanvasGraphError("Workflow template is not in the current workspace")
        item = dict(row)
        return {
            "templateId": item["template_id"],
            "title": item["title"],
            "description": item.get("description"),
            "revision": item["revision"],
            "graph": _record(_json(item.get("template_json"), {})),
            "createdAt": item["created_at"],
            "updatedAt": item["updated_at"],
        }

    def delete_template(self, *, session_id: str, template_id: str) -> None:
        authority = self._authority(session_id, require_write=True)
        with db.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM workspace_canvas_templates WHERE template_id = ? AND workspace_key = ?",
                (template_id, self._workspace_key(authority)),
            )
            conn.commit()
        if cursor.rowcount != 1:
            raise CreativeCanvasGraphError("Workflow template is not in the current workspace")

    def instantiate_template(
        self,
        *,
        session_id: str,
        template_id: str,
        expected_revision: int,
        mode: str = "append",
    ) -> dict[str, Any]:
        template = self.get_template(session_id=session_id, template_id=template_id)
        source = _record(template["graph"])
        current_payload = self.get_graph(session_id=session_id)
        current = _record(current_payload.get("graph")) or self.empty_graph()
        if mode not in {"append", "replace"}:
            raise CreativeCanvasGraphError("Workflow template mode must be append or replace")
        if mode == "replace":
            combined = {**source, "schema": GRAPH_SCHEMA, "version": GRAPH_VERSION, "graphId": current.get("graphId") or ""}
        else:
            id_map = {
                str(node["nodeId"]): f"canvas-node-{uuid.uuid4().hex}"
                for node in _list(source.get("nodes"))
                if isinstance(node, dict)
            }
            max_x = max([float(node.get("x") or 0) + float(node.get("width") or 280) for node in _list(current.get("nodes")) if isinstance(node, dict)] or [0])
            appended_nodes = []
            for raw_node in _list(source.get("nodes")):
                node = dict(raw_node)
                node["nodeId"] = id_map[str(node["nodeId"])]
                node["x"] = float(node.get("x") or 0) + max_x + 120
                if node.get("producerActionNodeId"):
                    node["producerActionNodeId"] = id_map[str(node["producerActionNodeId"])]
                appended_nodes.append(node)
            appended_edges = []
            for raw_edge in _list(source.get("edges")):
                edge = dict(raw_edge)
                edge["edgeId"] = f"canvas-edge-{uuid.uuid4().hex}"
                edge["from"] = id_map[str(edge["from"])]
                edge["to"] = id_map[str(edge["to"])]
                appended_edges.append(edge)
            combined = {
                **current,
                "schema": GRAPH_SCHEMA,
                "version": GRAPH_VERSION,
                "nodes": [*_list(current.get("nodes")), *appended_nodes],
                "edges": [*_list(current.get("edges")), *appended_edges],
            }
        return self.save_graph(
            session_id=session_id,
            graph=combined,
            expected_revision=expected_revision,
            allow_unbound_inputs=True,
        )


creative_canvas_graph_service = CreativeCanvasGraphService()
