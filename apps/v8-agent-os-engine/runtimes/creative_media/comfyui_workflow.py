from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any


COMFYUI_WORKFLOW_SCHEMA = "v8.comfyui.workflow.v1"
MAX_WORKFLOW_BYTES = 1_048_576
MAX_WORKFLOW_NODES = 512
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_SECRET_INPUT_NAMES = {
    "api_key",
    "apikey",
    "access_token",
    "authorization",
    "client_secret",
    "password",
    "secret",
    "token",
}


def _record(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _identifier(value: Any, *, label: str) -> str:
    normalized = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(normalized):
        raise ValueError(f"ComfyUI workflow {label} is missing or invalid")
    return normalized


def _reject_embedded_secrets(prompt: dict[str, Any]) -> None:
    for node in prompt.values():
        for key, value in _record(_record(node).get("inputs")).items():
            normalized_key = str(key or "").strip().lower().replace("-", "_")
            if normalized_key in _SECRET_INPUT_NAMES and str(value or "").strip():
                raise ValueError(
                    "ComfyUI API workflow contains an embedded credential; configure it inside ComfyUI instead"
                )


def validate_comfyui_workflow(value: Any) -> dict[str, Any]:
    workflow = _record(value)
    if workflow.get("schema") != COMFYUI_WORKFLOW_SCHEMA:
        raise ValueError(f"ComfyUI workflow schema must be {COMFYUI_WORKFLOW_SCHEMA}")
    operation_kind = str(workflow.get("operationKind") or "").strip()
    if operation_kind != "video.action_transfer":
        raise ValueError("ComfyUI workflow currently supports only video.action_transfer")

    prompt = _record(workflow.get("prompt"))
    if not prompt or len(prompt) > MAX_WORKFLOW_NODES:
        raise ValueError(f"ComfyUI API workflow must contain 1-{MAX_WORKFLOW_NODES} nodes")
    encoded = json.dumps(prompt, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_WORKFLOW_BYTES:
        raise ValueError("ComfyUI API workflow exceeds the 1 MiB configuration limit")
    for node_id, raw_node in prompt.items():
        _identifier(node_id, label="node id")
        node = _record(raw_node)
        _identifier(node.get("class_type"), label=f"node {node_id} class_type")
        if not isinstance(node.get("inputs"), dict):
            raise ValueError(f"ComfyUI workflow node {node_id} must contain an inputs object")
    _reject_embedded_secrets(prompt)

    bindings = _record(workflow.get("bindings"))
    normalized_bindings: dict[str, dict[str, str]] = {}
    for port_id in ("image", "video"):
        binding = _record(bindings.get(port_id))
        node_id = _identifier(binding.get("nodeId"), label=f"{port_id} binding nodeId")
        input_name = _identifier(binding.get("inputName"), label=f"{port_id} binding inputName")
        node = _record(prompt.get(node_id))
        if not node:
            raise ValueError(f"ComfyUI workflow {port_id} binding references an unknown node")
        if input_name not in _record(node.get("inputs")):
            raise ValueError(f"ComfyUI workflow {port_id} binding references an unknown node input")
        normalized_bindings[port_id] = {"nodeId": node_id, "inputName": input_name}

    output = _record(workflow.get("output"))
    output_node_id = _identifier(output.get("nodeId"), label="output nodeId")
    output_field = _identifier(output.get("field"), label="output field")
    if output_node_id not in prompt:
        raise ValueError("ComfyUI workflow output references an unknown node")
    output_index = int(output.get("index") or 0)
    if output_index < 0 or output_index > 99:
        raise ValueError("ComfyUI workflow output index must be between 0 and 99")

    return {
        "schema": COMFYUI_WORKFLOW_SCHEMA,
        "operationKind": operation_kind,
        "prompt": deepcopy(prompt),
        "bindings": normalized_bindings,
        "output": {
            "nodeId": output_node_id,
            "field": output_field,
            "index": output_index,
        },
        "digest": hashlib.sha256(encoded).hexdigest(),
    }


def bind_comfyui_inputs(workflow: dict[str, Any], uploaded_inputs: dict[str, str]) -> dict[str, Any]:
    normalized = validate_comfyui_workflow(workflow)
    prompt = deepcopy(normalized["prompt"])
    for port_id in ("image", "video"):
        uploaded_name = str(uploaded_inputs.get(port_id) or "").strip()
        if not uploaded_name:
            raise ValueError(f"ComfyUI workflow is missing the uploaded {port_id} input")
        binding = normalized["bindings"][port_id]
        prompt[binding["nodeId"]]["inputs"][binding["inputName"]] = uploaded_name
    return prompt


def select_comfyui_output(workflow: dict[str, Any], history_item: Any) -> dict[str, str] | None:
    normalized = validate_comfyui_workflow(workflow)
    output = normalized["output"]
    node_output = _record(_record(_record(history_item).get("outputs")).get(output["nodeId"]))
    values = node_output.get(output["field"])
    if not isinstance(values, list) or output["index"] >= len(values):
        return None
    selected = _record(values[output["index"]])
    filename = str(selected.get("filename") or "").strip()
    subfolder = str(selected.get("subfolder") or "").strip().replace("\\", "/")
    folder_type = str(selected.get("type") or "output").strip()
    if not filename or "/" in filename or "\\" in filename or ".." in subfolder.split("/"):
        raise ValueError("ComfyUI workflow returned an unsafe output file reference")
    if folder_type not in {"input", "output", "temp"}:
        raise ValueError("ComfyUI workflow returned an unsupported output folder type")
    return {"filename": filename, "subfolder": subfolder, "type": folder_type}


__all__ = [
    "COMFYUI_WORKFLOW_SCHEMA",
    "bind_comfyui_inputs",
    "select_comfyui_output",
    "validate_comfyui_workflow",
]
