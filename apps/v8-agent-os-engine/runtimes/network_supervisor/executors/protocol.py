"""Small, strict v1 control protocol shared by transports and simulators."""
from __future__ import annotations

import hashlib
import json
import re

VERSION = 1
MAX_FRAME = 16_384
LEASE_MS = 30_000
MAX_TTL_MS = 30_000
TERMINAL = frozenset({"succeeded", "failed", "rejected", "cancelled", "expired", "unknown_outcome"})
CAPABILITIES = frozenset({"device.health", "sensor.read", "actuator.set", "android.observe", "android.capture", "android.action"})
MUTATING = frozenset({"actuator.set", "android.action"})
GESTURES = frozenset({"tap", "swipe"})
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}\Z")


class ExecutorError(ValueError):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


def require(condition, code: str, status: int = 409):
    if not condition:
        raise ExecutorError(code, status)


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "duplicate_json_key", 400)
        result[key] = value
    return result


def _finite(value):
    raise ExecutorError("invalid_json_number", 400)


def parse(raw: str | bytes) -> dict:
    require(len(raw.encode("utf-8") if isinstance(raw, str) else raw) <= MAX_FRAME, "frame_too_large", 413)
    try:
        result = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_finite)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ExecutorError("invalid_json", 400) from exc
    require(isinstance(result, dict), "object_required", 400)
    def check(value, depth=0):
        require(depth <= 12, "json_too_deep", 400)
        if isinstance(value, dict):
            for item in value.values():
                check(item, depth + 1)
        elif isinstance(value, list):
            require(len(value) <= 256, "array_too_large", 400)
            for item in value:
                check(item, depth + 1)
        elif isinstance(value, float):
            import math
            require(math.isfinite(value), "invalid_json_number", 400)
    check(result)
    return result


def canonical(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(command: dict) -> str:
    return hashlib.sha256(canonical({k: v for k, v in command.items() if k != "commandDigest"}).encode()).hexdigest()


def identifier(value, code="identifier_invalid") -> str:
    require(isinstance(value, str) and ID.fullmatch(value), code, 400)
    return value


def integer(value, minimum: int, maximum: int, code: str) -> int:
    require(type(value) is int and minimum <= value <= maximum, code, 400)
    return value


def grants(value) -> list[dict]:
    require(isinstance(value, list) and len(value) <= 64, "grants_invalid", 400)
    result = []
    for item in value:
        require(isinstance(item, dict) and set(item) == {"capability", "resourceId"}, "grant_invalid", 400)
        require(isinstance(item["capability"], str) and item["capability"] in CAPABILITIES, "capability_unsupported", 400)
        identifier(item["resourceId"], "resource_invalid")
        require(item not in result, "duplicate_grant", 400)
        result.append(item)
    return result


def action(capability: str, resource: str, arguments: dict, precondition: dict):
    require(isinstance(capability, str) and capability in CAPABILITIES, "capability_unsupported", 400)
    identifier(resource, "resource_invalid")
    require(isinstance(arguments, dict) and isinstance(precondition, dict), "action_invalid", 400)
    if capability.startswith("android."):
        require(resource not in {"display", "android", "com.v8agentos.phone", "com.android.settings"}
                and "permissioncontroller" not in resource.lower(), "protected_android_resource", 403)
    if capability in {"device.health", "android.observe"}:
        require(not arguments, "arguments_invalid", 400)
    elif capability == "sensor.read":
        require(set(arguments) <= {"maxAgeMs"}, "arguments_invalid", 400)
        integer(arguments.get("maxAgeMs", 0), 0, 60_000, "max_age_invalid")
    elif capability == "android.capture":
        require(set(arguments) <= {"scope"} and arguments.get("scope", "window") in ("window", "display"), "capture_scope_invalid", 400)
    elif capability == "actuator.set":
        require(set(arguments) == {"level", "maxHoldMs"} and type(arguments["level"]) is bool, "arguments_invalid", 400)
        integer(arguments["maxHoldMs"], 1, MAX_TTL_MS, "hold_invalid")
        integer(precondition.get("resourceRevision"), 0, 2**53 - 1, "resource_revision_required")
    elif capability == "android.action":
        operation = arguments.get("action")
        require(isinstance(operation, str), "android_action_unsupported", 400)
        if operation in GESTURES:
            expected = {"action", "x", "y"} | ({"endX", "endY", "durationMs"} if operation == "swipe" else set())
            require(set(arguments) == expected, "arguments_invalid", 400)
            frame_geometry(precondition)
            identifier(precondition.get("frameId"), "frame_required")
            for key in ("x", "y", "endX", "endY"):
                if key in arguments:
                    integer(arguments[key], 0, precondition["width" if key in ("x", "endX") else "height"] - 1, "gesture_outside_frame")
            if operation == "swipe":
                integer(arguments["durationMs"], 100, 1000, "gesture_duration_invalid")
        else:
            require(set(arguments) <= {"action", "nodeId", "text"}, "arguments_invalid", 400)
            require(operation in {"click", "long_click", "set_text", "scroll_forward", "scroll_backward"}, "android_action_unsupported", 400)
            identifier(arguments.get("nodeId"), "node_required")
            if operation == "set_text":
                require(isinstance(arguments.get("text"), str) and len(arguments["text"]) <= 1000, "text_invalid", 400)
            else:
                require("text" not in arguments, "text_invalid", 400)
            require(isinstance(precondition.get("nodeMapRevision"), (str, int)), "observation_anchor_required", 400)
        for key in ("observationId", "appId", "windowId"):
            require(key in precondition and isinstance(precondition[key], (str, int)), "observation_anchor_required", 400)
        require(precondition["appId"] == resource, "observation_resource_mismatch", 400)
    # Reuse the wire budget/depth parser for trusted callers too.
    parse(canonical({"arguments": arguments, "precondition": precondition}))


def frame_geometry(value: dict):
    for key in ("width", "height"):
        integer(value.get(key), 1, 4096, "frame_dimensions_invalid")
    require(value["width"] * value["height"] <= 8 * 1024 * 1024, "frame_dimensions_invalid", 400)
    integer(value.get("rotation"), 0, 3, "frame_rotation_invalid")
    require(isinstance(value.get("geometryRevision"), str) and 0 < len(value["geometryRevision"]) <= 128, "geometry_revision_required", 400)
    viewport = value.get("viewport")
    require(isinstance(viewport, dict) and set(viewport) == {"left", "top", "width", "height"}, "viewport_required", 400)
    for key in ("left", "top"):
        integer(viewport[key], 0, 16384, "viewport_invalid")
    for key in ("width", "height"):
        integer(viewport[key], 1, 16384, "viewport_invalid")
