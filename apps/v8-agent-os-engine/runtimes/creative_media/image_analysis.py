from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from core.process_launch import popen_windowless
from core.runtime.feature_packs import (
    build_feature_pack_statuses,
    load_feature_pack_asset_manifest,
    resolve_feature_pack_asset,
)


ANALYZER_VERSION = "1.0.1"
FEATURE_PACK_ID = "creative_media_image_analysis"
MODEL_ASSET_ID = "isnet_general_use"
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".psd"}
_ONNX_IMAGE_SIZE = 1024
_ONNX_PROBE_TIMEOUT_SECONDS = 15
_ONNX_INFERENCE_TIMEOUT_SECONDS = 45
_ONNX_WORKER_IDLE_SECONDS = 90
_ONNX_WORKER_STOP_SECONDS = 2
_ONNX_IPC_MAX_MESSAGE_BYTES = 8 * 1024
_ONNX_CHILD_MARKER = "V8OS_ONNX_RESULT="
_ONNX_CHILD_ENV_ALLOWLIST = frozenset(
    {
        "LANG",
        "LANGUAGE",
        "NUMBER_OF_PROCESSORS",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "WINDIR",
    }
)
_ONNX_WORKER_LAUNCHER = r"""
import hashlib
import json
import pathlib
import re
import site
import sys

MARKER = "V8OS_ONNX_RESULT="
MAX_REQUEST_BYTES = 8 * 1024

def emit(request_id, ok, error=None, **detail):
    print(
        MARKER
        + json.dumps(
            {"requestId": request_id, "ok": bool(ok), "error": error, **detail},
            separators=(",", ":"),
        ),
        flush=True,
    )

def fail_start(error):
    emit("startup", False, error)
    raise SystemExit(1)

def module_is_in_target(module, target):
    spec = getattr(module, "__spec__", None)
    candidates = []
    for value in (getattr(module, "__file__", None), getattr(spec, "origin", None)):
        if value and value not in {"built-in", "frozen"}:
            candidates.append(pathlib.Path(str(value)).resolve())
    candidates.extend(
        pathlib.Path(str(value)).resolve()
        for value in list(getattr(spec, "submodule_search_locations", None) or [])
    )
    return any(candidate == target or target in candidate.parents for candidate in candidates)

target_value, model_value, expected_sha256, ipc_root_value = sys.argv[1:5]
target = pathlib.Path(target_value).resolve()
model = pathlib.Path(model_value).resolve()
ipc_root = pathlib.Path(ipc_root_value).resolve()
input_path = (ipc_root / "input.rgb").resolve()
output_path = (ipc_root / "mask.f32").resolve()
if not target.is_dir() or not model.is_file() or not ipc_root.is_dir():
    fail_start("governed_runtime_unavailable")
if input_path.parent != ipc_root or output_path.parent != ipc_root:
    fail_start("ipc_root_invalid")

digest = hashlib.sha256()
try:
    with model.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
except OSError:
    fail_start("model_read_failed")
if not expected_sha256 or digest.hexdigest().lower() != expected_sha256.lower():
    fail_start("model_sha256_mismatch")

site.addsitedir(str(target))
if str(target) in sys.path:
    sys.path.remove(str(target))
sys.path.insert(0, str(target))
try:
    import numpy as np
    import onnxruntime as ort
except Exception:
    fail_start("runtime_import_failed")
if not module_is_in_target(np, target) or not module_is_in_target(ort, target):
    fail_start("module_origin_outside_feature_pack")

try:
    session = ort.InferenceSession(str(model), providers=["CPUExecutionProvider"])
    providers = list(session.get_providers())
except Exception:
    fail_start("onnx_session_failed")
if "CPUExecutionProvider" not in providers:
    fail_start("onnx_cpu_provider_unavailable")

verified = {
    "isolated": True,
    "moduleOriginsVerified": True,
    "modelShaVerified": True,
    "cpuSessionLoaded": True,
}
emit(
    "startup",
    True,
    **verified,
)

while True:
    raw_request = sys.stdin.readline(MAX_REQUEST_BYTES + 1)
    if raw_request == "":
        break
    oversized = len(raw_request.encode("utf-8", errors="ignore")) > MAX_REQUEST_BYTES
    if oversized and not raw_request.endswith("\n"):
        while raw_request and not raw_request.endswith("\n"):
            raw_request = sys.stdin.readline(MAX_REQUEST_BYTES + 1)
    if oversized:
        emit("invalid", False, "ipc_request_too_large")
        continue
    try:
        request = json.loads(raw_request)
    except (TypeError, ValueError):
        emit("invalid", False, "ipc_request_invalid")
        continue
    request_id = str(request.get("requestId") or "") if isinstance(request, dict) else ""
    if not re.fullmatch(r"[0-9a-f]{32}", request_id):
        emit("invalid", False, "ipc_request_id_invalid")
        continue
    action = str(request.get("action") or "")
    if action == "shutdown":
        emit(request_id, True)
        break
    if action == "probe":
        emit(request_id, True, **verified)
        continue
    if action != "infer":
        emit(request_id, False, "ipc_action_invalid")
        continue
    try:
        values = np.fromfile(str(input_path), dtype=np.uint8)
        expected_values = 1024 * 1024 * 3
        if int(values.size) != expected_values:
            emit(request_id, False, "invalid_input")
            continue
        values = values.reshape((1024, 1024, 3)).astype(np.float32)
        values = values / max(float(values.max()), 1e-6)
        values = (values - np.asarray((0.5, 0.5, 0.5), dtype=np.float32)).transpose((2, 0, 1))
        input_name = session.get_inputs()[0].name
        prediction = session.run(None, {input_name: np.expand_dims(values, 0).astype(np.float32)})[0]
        mask = np.asarray(prediction, dtype=np.float32).squeeze()
        if tuple(mask.shape) != (1024, 1024):
            emit(request_id, False, "invalid_model_output")
            continue
        minimum = float(mask.min())
        maximum = float(mask.max())
        if maximum - minimum <= 1e-8:
            emit(request_id, False, "model_returned_constant_mask")
            continue
        mask = (mask - minimum) / (maximum - minimum)
        np.clip(mask * 255.0, 0, 255).astype(np.uint8).tofile(str(output_path))
    except Exception:
        emit(request_id, False, "onnx_inference_failed")
        continue
    emit(request_id, True, **verified, outputWritten=True)
"""

QUALITY_PROFILES: dict[str, dict[str, Any]] = {
    "transparent_cutout": {
        "requireAlpha": True,
        "areaRatio": (0.03, 0.92),
        "maxTouchedEdges": 0,
        "maxComponents": 6,
        "minMaskConfidence": 0.52,
    },
    "character_reference": {
        "requireAlpha": False,
        "areaRatio": (0.18, 0.88),
        "maxTouchedEdges": 1,
        "maxComponents": 8,
        "minMaskConfidence": 0.48,
        "maxReferenceAreaDelta": 0.18,
        "maxReferenceCenterShift": 0.12,
    },
    "ui_icon": {
        "requireAlpha": True,
        "areaRatio": (0.10, 0.82),
        "maxTouchedEdges": 0,
        "maxComponents": 8,
        "minMaskConfidence": 0.58,
    },
    "product_packshot": {
        "requireAlpha": False,
        "areaRatio": (0.12, 0.82),
        "maxTouchedEdges": 0,
        "maxComponents": 5,
        "minMaskConfidence": 0.52,
        "maxReferenceAreaDelta": 0.15,
        "maxReferenceCenterShift": 0.10,
    },
    "storyboard_frame": {
        "requireAlpha": False,
        "areaRatio": (0.01, 0.99),
        "maxTouchedEdges": 4,
        "maxComponents": 64,
        "minMaskConfidence": 0.0,
    },
}


def _open_image(path: Path) -> Image.Image:
    if path.suffix.lower() == ".psd":
        try:
            from psd_tools import PSDImage  # type: ignore
        except Exception as exc:
            raise RuntimeError(f"psd-tools is required to inspect PSD files: {exc}") from exc
        return PSDImage.open(str(path)).composite().convert("RGBA")
    return Image.open(path).convert("RGBA")


def _source_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resize_mask(mask: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    image = Image.fromarray(np.clip(mask * 255.0, 0, 255).astype(np.uint8), mode="L")
    return np.asarray(image.resize(size, Image.Resampling.LANCZOS), dtype=np.float32) / 255.0


def _edge_values(rgb: np.ndarray) -> np.ndarray:
    if rgb.shape[0] == 1 or rgb.shape[1] == 1:
        return rgb.reshape(-1, 3)
    return np.concatenate((rgb[0], rgb[-1], rgb[1:-1, 0], rgb[1:-1, -1]), axis=0)


def _connected_background(candidate: np.ndarray) -> np.ndarray:
    height, width = candidate.shape
    connected = np.zeros((height, width), dtype=bool)
    queue: deque[tuple[int, int]] = deque()
    for x in range(width):
        if candidate[0, x]:
            connected[0, x] = True
            queue.append((0, x))
        if height > 1 and candidate[height - 1, x] and not connected[height - 1, x]:
            connected[height - 1, x] = True
            queue.append((height - 1, x))
    for y in range(1, height - 1):
        if candidate[y, 0]:
            connected[y, 0] = True
            queue.append((y, 0))
        if width > 1 and candidate[y, width - 1] and not connected[y, width - 1]:
            connected[y, width - 1] = True
            queue.append((y, width - 1))
    while queue:
        y, x = queue.popleft()
        for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
            if 0 <= ny < height and 0 <= nx < width and candidate[ny, nx] and not connected[ny, nx]:
                connected[ny, nx] = True
                queue.append((ny, nx))
    return connected


def _border_subject_mask(image: Image.Image) -> tuple[np.ndarray | None, float, dict[str, Any]]:
    rgb_image = image.convert("RGB")
    scale = min(1.0, 512.0 / max(rgb_image.size))
    if scale < 1.0:
        rgb_image = rgb_image.resize(
            (max(1, round(rgb_image.width * scale)), max(1, round(rgb_image.height * scale))),
            Image.Resampling.LANCZOS,
        )
    rgb = np.asarray(rgb_image, dtype=np.float32)
    edges = _edge_values(rgb)
    median = np.median(edges, axis=0)
    distances = np.linalg.norm(edges - median, axis=1)
    edge_spread = float(np.percentile(distances, 90))
    threshold = max(16.0, min(52.0, edge_spread * 1.7 + 8.0))
    candidate = np.linalg.norm(rgb - median, axis=2) <= threshold
    background = _connected_background(candidate)
    subject = (~background).astype(np.float32)
    area_ratio = float(subject.mean())
    usable = edge_spread <= 34.0 and 0.01 <= area_ratio <= 0.97
    confidence = max(0.0, min(1.0, 1.0 - edge_spread / 68.0)) if usable else 0.0
    diagnostics = {
        "edgeSpread": round(edge_spread, 4),
        "backgroundColor": [int(round(value)) for value in median.tolist()],
        "backgroundThreshold": round(threshold, 4),
        "candidateAreaRatio": round(area_ratio, 6),
    }
    if not usable:
        return None, confidence, diagnostics
    return _resize_mask(subject, image.size), confidence, diagnostics


def _expected_model_digest() -> str:
    manifest = load_feature_pack_asset_manifest(FEATURE_PACK_ID) or {}
    for asset in list(manifest.get("assets") or []):
        if str(asset.get("id") or "") == MODEL_ASSET_ID:
            return str(asset.get("sha256") or "").lower()
    return ""


@lru_cache(maxsize=2)
def _verify_onnx_model(model_path: str, modified_ns: int, size: int, expected: str) -> None:
    del modified_ns, size
    if not expected or _source_fingerprint(Path(model_path)).lower() != expected.lower():
        raise RuntimeError("onnx_model_sha256_mismatch")


def _onnx_child_environment() -> dict[str, str]:
    environment: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = str(key or "").upper()
        if upper in _ONNX_CHILD_ENV_ALLOWLIST or upper.startswith("LC_"):
            environment[str(key)] = str(value)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _receipt_governed_onnx_target() -> tuple[Path | None, str | None]:
    try:
        from core.storage import storage

        status = next(
            item
            for item in build_feature_pack_statuses(storage.get_runtime_registry_config())
            if item.get("id") == FEATURE_PACK_ID
        )
    except Exception:
        return None, "feature_pack_status_unavailable"
    if status.get("status") != "installed":
        return None, "feature_pack_not_installed"
    if status.get("restartRequired"):
        return None, "feature_pack_restart_required"
    target_value = str(status.get("targetDir") or "").strip()
    if not target_value:
        return None, "feature_pack_target_unavailable"
    target = Path(target_value).expanduser().resolve(strict=False)
    if not target.is_dir():
        return None, "feature_pack_target_unavailable"
    return target, None


class _OnnxWorkerFailure(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False, discard: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.discard = discard


def _verified_onnx_worker_identity(
    target: Path,
    model_path: Path,
    expected_sha256: str,
) -> tuple[str, str, int, int, str]:
    try:
        resolved_target = target.expanduser().resolve(strict=True)
        resolved_model = model_path.expanduser().resolve(strict=True)
        stat = resolved_model.stat()
        _verify_onnx_model(str(resolved_model), stat.st_mtime_ns, stat.st_size, expected_sha256)
    except RuntimeError:
        raise
    except OSError as exc:
        raise RuntimeError("onnx_model_unavailable") from exc
    if not resolved_target.is_dir():
        raise RuntimeError("feature_pack_target_unavailable")
    return (
        str(resolved_target),
        str(resolved_model),
        int(stat.st_mtime_ns),
        int(stat.st_size),
        expected_sha256,
    )


class _ManagedOnnxWorker:
    def __init__(self, identity: tuple[str, str, int, int, str]) -> None:
        self.identity = identity
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=4)
        self._temporary = tempfile.TemporaryDirectory(prefix="v8os-onnx-worker-")
        self._ipc_root = Path(self._temporary.name)
        self._input_path = self._ipc_root / "input.rgb"
        self._output_path = self._ipc_root / "mask.f32"
        self.process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        target, model_path, _modified_ns, _size, expected_sha256 = identity
        command = [
            sys.executable,
            "-I",
            "-S",
            "-c",
            _ONNX_WORKER_LAUNCHER,
            target,
            model_path,
            expected_sha256,
            str(self._ipc_root),
        ]
        try:
            self.process = popen_windowless(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=str(Path(__file__).resolve().parents[2]),
                env=_onnx_child_environment(),
            )
            self._reader = threading.Thread(
                target=self._read_responses,
                name="v8os-onnx-worker-reader",
                daemon=True,
            )
            self._reader.start()
            startup = self._wait_for_response("startup", _ONNX_PROBE_TIMEOUT_SECONDS)
            self._require_verified_success(startup)
        except Exception:
            self.stop()
            raise

    @property
    def pid(self) -> int | None:
        process = self.process
        return int(process.pid) if process is not None and process.poll() is None else None

    def _offer_response(self, payload: dict[str, Any]) -> None:
        try:
            self._responses.put_nowait(payload)
            return
        except queue.Full:
            pass
        try:
            while True:
                self._responses.get_nowait()
        except queue.Empty:
            pass
        try:
            self._responses.put_nowait({"_protocolError": "onnx_worker_response_overflow"})
        except queue.Full:
            pass

    def _read_responses(self) -> None:
        process = self.process
        stream = process.stdout if process is not None else None
        if stream is None:
            self._offer_response({"_eof": True})
            return
        while True:
            try:
                raw = stream.readline(_ONNX_IPC_MAX_MESSAGE_BYTES + 1)
            except (OSError, ValueError):
                break
            if raw == "":
                break
            starts_with_marker = raw.startswith(_ONNX_CHILD_MARKER)
            oversized = len(raw.encode("utf-8", errors="ignore")) > _ONNX_IPC_MAX_MESSAGE_BYTES
            if oversized and not raw.endswith("\n"):
                while raw and not raw.endswith("\n"):
                    try:
                        raw = stream.readline(_ONNX_IPC_MAX_MESSAGE_BYTES + 1)
                    except (OSError, ValueError):
                        raw = ""
                        break
            if oversized:
                if starts_with_marker:
                    self._offer_response({"_protocolError": "onnx_worker_response_too_large"})
                continue
            if not raw.startswith(_ONNX_CHILD_MARKER):
                continue
            try:
                payload = json.loads(raw[len(_ONNX_CHILD_MARKER) :])
            except json.JSONDecodeError:
                self._offer_response({"_protocolError": "onnx_worker_response_invalid"})
                continue
            if not isinstance(payload, dict):
                self._offer_response({"_protocolError": "onnx_worker_response_invalid"})
                continue
            self._offer_response(dict(payload))
        self._offer_response({"_eof": True})

    def _wait_for_response(self, request_id: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _OnnxWorkerFailure("onnx_worker_timeout", discard=True)
            try:
                payload = self._responses.get(timeout=remaining)
            except queue.Empty as exc:
                raise _OnnxWorkerFailure("onnx_worker_timeout", discard=True) from exc
            if payload.get("_eof"):
                raise _OnnxWorkerFailure("onnx_worker_exited", retryable=True, discard=True)
            protocol_error = str(payload.get("_protocolError") or "")
            if protocol_error:
                raise _OnnxWorkerFailure(protocol_error, retryable=True, discard=True)
            if str(payload.get("requestId") or "") != request_id:
                raise _OnnxWorkerFailure("onnx_worker_response_mismatch", retryable=True, discard=True)
            return payload

    @staticmethod
    def _require_verified_success(payload: dict[str, Any]) -> None:
        if not payload.get("ok"):
            code = str(payload.get("error") or "onnx_worker_failed")
            raise _OnnxWorkerFailure(code)
        if not (
            payload.get("isolated") is True
            and payload.get("moduleOriginsVerified") is True
            and payload.get("modelShaVerified") is True
            and payload.get("cpuSessionLoaded") is True
        ):
            raise _OnnxWorkerFailure("onnx_worker_unverified", discard=True)

    def request(self, action: str, input_bytes: bytes | None = None) -> tuple[dict[str, Any], bytes | None]:
        process = self.process
        if process is None or process.poll() is not None or process.stdin is None:
            raise _OnnxWorkerFailure("onnx_worker_exited", retryable=True, discard=True)
        if action == "infer":
            if input_bytes is None or len(input_bytes) != _ONNX_IMAGE_SIZE * _ONNX_IMAGE_SIZE * 3:
                raise _OnnxWorkerFailure("onnx_worker_input_invalid")
            try:
                self._input_path.write_bytes(input_bytes)
            except OSError as exc:
                raise _OnnxWorkerFailure("onnx_worker_input_write_failed", discard=True) from exc
        request_id = uuid.uuid4().hex
        request_line = json.dumps(
            {"requestId": request_id, "action": action},
            separators=(",", ":"),
        )
        if len(request_line.encode("utf-8")) > _ONNX_IPC_MAX_MESSAGE_BYTES:
            raise _OnnxWorkerFailure("onnx_worker_request_too_large")
        try:
            process.stdin.write(request_line + "\n")
            process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise _OnnxWorkerFailure("onnx_worker_exited", retryable=True, discard=True) from exc
        timeout = _ONNX_INFERENCE_TIMEOUT_SECONDS if action == "infer" else _ONNX_PROBE_TIMEOUT_SECONDS
        payload = self._wait_for_response(request_id, timeout)
        self._require_verified_success(payload)
        if action != "infer":
            return payload, None
        try:
            raw = self._output_path.read_bytes()
        except OSError as exc:
            raise _OnnxWorkerFailure(
                "onnx_worker_output_read_failed",
                retryable=True,
                discard=True,
            ) from exc
        expected_bytes = _ONNX_IMAGE_SIZE * _ONNX_IMAGE_SIZE
        if len(raw) != expected_bytes:
            raise _OnnxWorkerFailure("onnx_worker_output_invalid", retryable=True, discard=True)
        return payload, raw

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is not None and process.poll() is None:
            try:
                if process.stdin is not None:
                    shutdown = json.dumps(
                        {"requestId": uuid.uuid4().hex, "action": "shutdown"},
                        separators=(",", ":"),
                    )
                    process.stdin.write(shutdown + "\n")
                    process.stdin.flush()
                process.wait(timeout=_ONNX_WORKER_STOP_SECONDS)
            except Exception:
                try:
                    process.terminate()
                    process.wait(timeout=_ONNX_WORKER_STOP_SECONDS)
                except Exception:
                    try:
                        process.kill()
                        process.wait(timeout=_ONNX_WORKER_STOP_SECONDS)
                    except Exception:
                        pass
        if process is not None:
            for stream in (process.stdin, process.stdout):
                if stream is None:
                    continue
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
        try:
            self._temporary.cleanup()
        except OSError:
            pass


class _OnnxWorkerManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._worker: _ManagedOnnxWorker | None = None
        self._last_used_at = 0.0
        self._idle_seconds = float(_ONNX_WORKER_IDLE_SECONDS)
        self._closed = threading.Event()
        self._idle_thread: threading.Thread | None = None

    def _start_idle_monitor(self) -> None:
        if self._idle_thread is not None and self._idle_thread.is_alive():
            return
        self._idle_thread = threading.Thread(
            target=self._idle_monitor,
            name="v8os-onnx-worker-idle",
            daemon=True,
        )
        self._idle_thread.start()

    def _idle_monitor(self) -> None:
        while not self._closed.wait(min(5.0, max(0.05, self._idle_seconds / 4.0))):
            with self._lock:
                if self._worker is None:
                    continue
                if time.monotonic() - self._last_used_at < self._idle_seconds:
                    continue
                self._discard_worker()

    def _discard_worker(self) -> None:
        worker = self._worker
        if worker is not None:
            worker.stop()
        if self._worker is worker:
            self._worker = None

    def _ensure_worker(self, identity: tuple[str, str, int, int, str]) -> _ManagedOnnxWorker:
        worker = self._worker
        if worker is not None and (worker.identity != identity or worker.pid is None):
            self._discard_worker()
            worker = None
        if worker is None:
            worker = _ManagedOnnxWorker(identity)
            self._worker = worker
            self._start_idle_monitor()
        return worker

    def request(
        self,
        identity: tuple[str, str, int, int, str],
        action: str,
        input_bytes: bytes | None = None,
    ) -> tuple[dict[str, Any], bytes | None]:
        with self._lock:
            self._last_used_at = time.monotonic()
            for attempt in range(2):
                try:
                    worker = self._ensure_worker(identity)
                    result = worker.request(action, input_bytes)
                    self._last_used_at = time.monotonic()
                    return result
                except _OnnxWorkerFailure as exc:
                    if exc.discard:
                        self._discard_worker()
                    if not exc.retryable or attempt > 0:
                        raise RuntimeError(exc.code) from exc
            raise RuntimeError("onnx_worker_failed")

    def shutdown_worker(self) -> None:
        with self._lock:
            self._discard_worker()

    def close(self) -> None:
        self._closed.set()
        self.shutdown_worker()


_ONNX_WORKER_MANAGER = _OnnxWorkerManager()


def _shutdown_onnx_worker() -> None:
    _ONNX_WORKER_MANAGER.shutdown_worker()


atexit.register(_ONNX_WORKER_MANAGER.close)


def _probe_onnx_runtime(model_path: str | Path, target_dir: str | Path) -> dict[str, Any]:
    model = Path(model_path).expanduser().resolve(strict=False)
    target = Path(target_dir).expanduser().resolve(strict=False)
    expected = _expected_model_digest()
    identity = _verified_onnx_worker_identity(target, model, expected)
    payload, _raw = _ONNX_WORKER_MANAGER.request(identity, "probe")
    return {
        "ok": True,
        "error": None,
        "isolated": payload.get("isolated") is True,
        "moduleOriginsVerified": payload.get("moduleOriginsVerified") is True,
        "modelShaVerified": payload.get("modelShaVerified") is True,
        "cpuSessionLoaded": payload.get("cpuSessionLoaded") is True,
    }


def _run_isolated_onnx_inference(image: Image.Image, model_path: Path, target: Path) -> np.ndarray:
    expected = _expected_model_digest()
    identity = _verified_onnx_worker_identity(target, model_path, expected)
    resized = image.convert("RGB").resize((_ONNX_IMAGE_SIZE, _ONNX_IMAGE_SIZE), Image.Resampling.LANCZOS)
    _payload, raw = _ONNX_WORKER_MANAGER.request(identity, "infer", resized.tobytes())
    if raw is None:
        raise RuntimeError("onnx_worker_output_invalid")
    return (
        np.frombuffer(raw, dtype=np.uint8)
        .reshape((_ONNX_IMAGE_SIZE, _ONNX_IMAGE_SIZE))
        .astype(np.float32)
        / 255.0
    )


def _safe_onnx_error_code(error: BaseException) -> str:
    value = str(error or "").strip()
    if value and len(value) <= 96 and all(character.isalnum() or character in {"_", ":"} for character in value):
        return value
    return "onnx_isolated_runtime_failed"


def _onnx_subject_mask(image: Image.Image) -> tuple[np.ndarray | None, float, str | None]:
    model_path = resolve_feature_pack_asset(FEATURE_PACK_ID, MODEL_ASSET_ID)
    if model_path is None:
        return None, 0.0, "feature_pack_not_installed"
    target, target_error = _receipt_governed_onnx_target()
    if target is None:
        return None, 0.0, target_error or "feature_pack_target_unavailable"
    try:
        mask = _run_isolated_onnx_inference(image, model_path, target)
        minimum = float(mask.min())
        maximum = float(mask.max())
        if maximum - minimum <= 1e-8:
            return None, 0.0, "model_returned_constant_mask"
        mask = (mask - minimum) / (maximum - minimum)
        confidence = float(np.mean(np.abs(mask - 0.5)) * 2.0)
        return _resize_mask(mask, image.size), max(0.0, min(1.0, confidence)), None
    except Exception as exc:
        return None, 0.0, _safe_onnx_error_code(exc)


def _component_count(mask: np.ndarray) -> int:
    binary_image = Image.fromarray((mask >= 0.5).astype(np.uint8) * 255, mode="L")
    scale = min(1.0, 320.0 / max(binary_image.size))
    if scale < 1.0:
        binary_image = binary_image.resize(
            (max(1, round(binary_image.width * scale)), max(1, round(binary_image.height * scale))),
            Image.Resampling.NEAREST,
        )
    binary = np.asarray(binary_image, dtype=np.uint8) > 0
    height, width = binary.shape
    visited = np.zeros_like(binary)
    minimum_component = max(2, int(binary.size * 0.0002))
    count = 0
    for y in range(height):
        for x in range(width):
            if not binary[y, x] or visited[y, x]:
                continue
            visited[y, x] = True
            queue = deque([(y, x)])
            size = 0
            while queue:
                cy, cx = queue.popleft()
                size += 1
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < height and 0 <= nx < width and binary[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        queue.append((ny, nx))
            if size >= minimum_component:
                count += 1
    return count


def _mask_metrics(mask: np.ndarray) -> dict[str, Any]:
    binary = mask >= 0.5
    height, width = binary.shape
    coordinates = np.argwhere(binary)
    if not coordinates.size:
        return {
            "areaRatio": 0.0,
            "bbox": None,
            "centroid": None,
            "margins": None,
            "touchesEdges": [],
            "componentCount": 0,
        }
    y0, x0 = coordinates.min(axis=0)
    y1, x1 = coordinates.max(axis=0)
    weights = np.clip(mask, 0.0, 1.0)
    total = float(weights.sum())
    x_weights = weights.sum(axis=0, dtype=np.float64)
    y_weights = weights.sum(axis=1, dtype=np.float64)
    centroid_x = float(np.dot(x_weights, np.arange(width, dtype=np.float64)) / max(total, 1e-8)) / max(width - 1, 1)
    centroid_y = float(np.dot(y_weights, np.arange(height, dtype=np.float64)) / max(total, 1e-8)) / max(height - 1, 1)
    touches = []
    if y0 == 0:
        touches.append("top")
    if y1 == height - 1:
        touches.append("bottom")
    if x0 == 0:
        touches.append("left")
    if x1 == width - 1:
        touches.append("right")
    return {
        "areaRatio": round(float(binary.mean()), 6),
        "bbox": {
            "x": round(float(x0) / max(width, 1), 6),
            "y": round(float(y0) / max(height, 1), 6),
            "width": round(float(x1 - x0 + 1) / max(width, 1), 6),
            "height": round(float(y1 - y0 + 1) / max(height, 1), 6),
        },
        "centroid": {"x": round(centroid_x, 6), "y": round(centroid_y, 6)},
        "margins": {
            "top": round(float(y0) / max(height, 1), 6),
            "right": round(float(width - x1 - 1) / max(width, 1), 6),
            "bottom": round(float(height - y1 - 1) / max(height, 1), 6),
            "left": round(float(x0) / max(width, 1), 6),
        },
        "touchesEdges": touches,
        "componentCount": _component_count(mask),
    }


def _analyze(path: str | Path, *, allow_onnx: bool = True) -> tuple[dict[str, Any], np.ndarray | None]:
    source = Path(path).expanduser().resolve(strict=False)
    if source.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
        raise ValueError(f"unsupported image format: {source.suffix or 'unknown'}")
    if not source.is_file():
        raise FileNotFoundError(str(source))
    image = _open_image(source)
    alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
    total = max(1, alpha.size)
    transparent = int(np.count_nonzero(alpha == 0))
    translucent = int(np.count_nonzero((alpha > 0) & (alpha < 255)))
    alpha_fraction = (transparent + translucent) / total
    meaningful_alpha = transparent + translucent >= max(16, int(total * 0.0005))
    false_alpha = (transparent + translucent) > 0 and not meaningful_alpha
    diagnostics: dict[str, Any] = {}
    required_feature_pack = None
    model_error = None
    if meaningful_alpha:
        mask = alpha.astype(np.float32) / 255.0
        mask_source = "alpha"
        confidence = 1.0
    else:
        mask, confidence, border_diagnostics = _border_subject_mask(image)
        diagnostics["border"] = border_diagnostics
        mask_source = "border_connected" if mask is not None else "none"
        if mask is None and allow_onnx:
            mask, confidence, model_error = _onnx_subject_mask(image)
            if mask is not None:
                mask_source = "onnx_isnet"
            elif model_error == "feature_pack_not_installed":
                required_feature_pack = FEATURE_PACK_ID
    metrics = _mask_metrics(mask) if mask is not None else _mask_metrics(np.zeros((image.height, image.width), dtype=np.float32))
    edge_alpha = alpha[(alpha > 0) & (alpha < 255)]
    contamination_ratio = float(edge_alpha.size) / max(1, int(np.count_nonzero(alpha > 0)))
    status = "analyzed" if mask is not None else "review_required"
    report = {
        "version": 1,
        "analyzerVersion": ANALYZER_VERSION,
        "status": status,
        "sourcePath": str(source),
        "sourceFingerprint": _source_fingerprint(source),
        "format": source.suffix.lower().lstrip("."),
        "width": image.width,
        "height": image.height,
        "alpha": {
            "status": "true_alpha" if meaningful_alpha else "false_alpha" if false_alpha else "opaque",
            "transparentPixels": transparent,
            "translucentPixels": translucent,
            "coverageRatio": round(alpha_fraction, 6),
            "edgeContaminationRatio": round(contamination_ratio, 6),
        },
        "subject": {
            "maskSource": mask_source,
            "maskConfidence": round(float(confidence), 6),
            **metrics,
        },
        "requiredFeaturePackId": required_feature_pack,
        "modelError": model_error if model_error not in {None, "feature_pack_not_installed"} else None,
        "diagnostics": diagnostics,
    }
    return report, mask


def analyze_image(path: str | Path, *, allow_onnx: bool = True) -> dict[str, Any]:
    report, _ = _analyze(path, allow_onnx=allow_onnx)
    return report


def _bbox_iou(left: dict[str, Any] | None, right: dict[str, Any] | None) -> float | None:
    if not left or not right:
        return None
    lx0, ly0 = float(left["x"]), float(left["y"])
    lx1, ly1 = lx0 + float(left["width"]), ly0 + float(left["height"])
    rx0, ry0 = float(right["x"]), float(right["y"])
    rx1, ry1 = rx0 + float(right["width"]), ry0 + float(right["height"])
    intersection = max(0.0, min(lx1, rx1) - max(lx0, rx0)) * max(0.0, min(ly1, ry1) - max(ly0, ry0))
    union = float(left["width"]) * float(left["height"]) + float(right["width"]) * float(right["height"]) - intersection
    return round(intersection / max(union, 1e-8), 6)


def compare_image_analyses(reference: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    left = dict(reference.get("subject") or {})
    right = dict(candidate.get("subject") or {})
    left_center = dict(left.get("centroid") or {})
    right_center = dict(right.get("centroid") or {})
    center_shift = None
    if left_center and right_center:
        center_shift = math.dist(
            (float(left_center.get("x") or 0), float(left_center.get("y") or 0)),
            (float(right_center.get("x") or 0), float(right_center.get("y") or 0)),
        )
    margin_delta = {}
    for key in ("top", "right", "bottom", "left"):
        left_value = (left.get("margins") or {}).get(key)
        right_value = (right.get("margins") or {}).get(key)
        if left_value is not None and right_value is not None:
            margin_delta[key] = round(float(right_value) - float(left_value), 6)
    return {
        "version": 1,
        "analyzerVersion": ANALYZER_VERSION,
        "referenceFingerprint": reference.get("sourceFingerprint"),
        "candidateFingerprint": candidate.get("sourceFingerprint"),
        "referenceStatus": reference.get("status"),
        "candidateStatus": candidate.get("status"),
        "requiredFeaturePackId": reference.get("requiredFeaturePackId") or candidate.get("requiredFeaturePackId"),
        "areaRatioDelta": round(float(right.get("areaRatio") or 0) - float(left.get("areaRatio") or 0), 6),
        "bboxIoU": _bbox_iou(left.get("bbox"), right.get("bbox")),
        "centerShift": round(center_shift, 6) if center_shift is not None else None,
        "marginDelta": margin_delta,
        "clippingChange": {
            "before": list(left.get("touchesEdges") or []),
            "after": list(right.get("touchesEdges") or []),
        },
        "alphaCoverageDelta": round(
            float((candidate.get("alpha") or {}).get("coverageRatio") or 0)
            - float((reference.get("alpha") or {}).get("coverageRatio") or 0),
            6,
        ),
    }


def compare_images(reference_path: str | Path, candidate_path: str | Path, *, allow_onnx: bool = True) -> dict[str, Any]:
    reference = analyze_image(reference_path, allow_onnx=allow_onnx)
    candidate = analyze_image(candidate_path, allow_onnx=allow_onnx)
    return {
        "reference": reference,
        "candidate": candidate,
        "comparison": compare_image_analyses(reference, candidate),
    }


def evaluate_quality_profile(
    report: dict[str, Any],
    profile: str,
    *,
    comparison: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile_id = profile if profile in QUALITY_PROFILES else "storyboard_frame"
    rules = QUALITY_PROFILES[profile_id]
    subject = dict(report.get("subject") or {})
    alpha = dict(report.get("alpha") or {})
    violations: list[str] = []
    warnings: list[str] = []
    if report.get("status") == "review_required":
        return {
            "status": "review_required",
            "profile": profile_id,
            "violations": ["subject_mask_unavailable"],
            "warnings": [],
            "requiredFeaturePackId": report.get("requiredFeaturePackId"),
        }
    if comparison and comparison.get("referenceStatus") == "review_required":
        return {
            "status": "review_required",
            "profile": profile_id,
            "violations": ["reference_subject_mask_unavailable"],
            "warnings": [],
            "requiredFeaturePackId": comparison.get("requiredFeaturePackId"),
        }
    if rules["requireAlpha"] and alpha.get("status") != "true_alpha":
        violations.append("real_alpha_required")
    area = float(subject.get("areaRatio") or 0)
    minimum_area, maximum_area = rules["areaRatio"]
    if area < minimum_area:
        violations.append("subject_too_small")
    if area > maximum_area:
        violations.append("subject_too_large")
    if len(list(subject.get("touchesEdges") or [])) > int(rules["maxTouchedEdges"]):
        violations.append("subject_clipped")
    if int(subject.get("componentCount") or 0) > int(rules["maxComponents"]):
        warnings.append("too_many_subject_components")
    if float(subject.get("maskConfidence") or 0) < float(rules["minMaskConfidence"]):
        warnings.append("low_mask_confidence")
    if comparison:
        if abs(float(comparison.get("areaRatioDelta") or 0)) > float(rules.get("maxReferenceAreaDelta", 1.0)):
            violations.append("reference_subject_scale_drift")
        if float(comparison.get("centerShift") or 0) > float(rules.get("maxReferenceCenterShift", 1.0)):
            violations.append("reference_subject_position_drift")
    repairable = violations and set(violations).issubset({"real_alpha_required"}) and subject.get("maskSource") != "none"
    return {
        "status": "repairable" if repairable else "failed" if violations else "review_required" if warnings else "passed",
        "profile": profile_id,
        "violations": violations,
        "warnings": warnings,
        "requiredFeaturePackId": report.get("requiredFeaturePackId"),
    }


def create_transparent_derivative(source_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    source = Path(source_path).expanduser().resolve(strict=False)
    report, mask = _analyze(source, allow_onnx=True)
    if mask is None:
        raise RuntimeError("subject mask is unavailable; install the image analysis feature pack or review manually")
    image = _open_image(source).convert("RGBA")
    alpha = Image.fromarray(np.clip(mask * 255.0, 0, 255).astype(np.uint8), mode="L")
    image.putalpha(alpha)
    target = Path(output_path).expanduser().resolve(strict=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG")
    return {"path": str(target), "sourceReport": report, "report": analyze_image(target, allow_onnx=False)}


__all__ = [
    "ANALYZER_VERSION",
    "FEATURE_PACK_ID",
    "QUALITY_PROFILES",
    "SUPPORTED_IMAGE_SUFFIXES",
    "analyze_image",
    "compare_image_analyses",
    "compare_images",
    "create_transparent_derivative",
    "evaluate_quality_profile",
]
