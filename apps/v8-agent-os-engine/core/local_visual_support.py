from __future__ import annotations

import base64
import math
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests
from PIL import Image


LOCAL_IMAGE_MAX_LONG_EDGE = 1344
LOCAL_IMAGE_MAX_TOTAL_PIXELS = 1_800_000
LOCAL_REMOTE_IMAGE_MAX_BYTES = 12 * 1024 * 1024
_RESAMPLE_LANCZOS = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
_PROBE_CACHE_TTL_SECONDS = 60.0
_probe_cache: dict[tuple[str, str, str], tuple[float, Dict[str, Any]]] = {}


def is_local_provider(provider: Optional[Dict[str, Any]]) -> bool:
    return str((provider or {}).get("type") or "").strip().upper() == "LOCAL"


def resolve_lm_studio_models_endpoint(base_url: str) -> str | None:
    normalized = str(base_url or "").strip()
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        return None

    path = (parsed.path or "").rstrip("/")
    if path.endswith("/api/v1"):
        models_path = f"{path}/models"
    else:
        models_path = "/api/v1/models"

    return parsed._replace(path=models_path, params="", query="", fragment="").geturl()


def _normalize_probe_result(
    *,
    status: str,
    message: str,
    model_id: str,
    endpoint: str | None = None,
    vision_supported: bool | None = None,
    context_length: int | None = None,
    max_context_length: int | None = None,
    params: str | None = None,
) -> Dict[str, Any]:
    return {
        "status": status,
        "message": message,
        "modelId": str(model_id or "").strip() or None,
        "endpoint": endpoint,
        "visionSupported": vision_supported,
        "contextLength": context_length,
        "maxContextLength": max_context_length,
        "params": params,
    }


def probe_local_multimodal_capability(
    *,
    model_id: str,
    provider_type: str,
    base_url: str,
    api_key: str,
    timeout: float = 3.0,
) -> Dict[str, Any]:
    normalized_model_id = str(model_id or "").strip()
    normalized_provider_type = str(provider_type or "").strip().upper()
    if normalized_provider_type != "LOCAL" or not normalized_model_id:
        return _normalize_probe_result(
            status="not_applicable",
            message="当前 provider 不是本地多模态探针目标。",
            model_id=normalized_model_id,
        )

    endpoint = resolve_lm_studio_models_endpoint(base_url)
    if not endpoint:
        return _normalize_probe_result(
            status="unknown",
            message="未配置本地 provider 的基础地址，无法探测视觉能力。",
            model_id=normalized_model_id,
        )

    cache_key = (endpoint, normalized_model_id, str(hash(str(api_key or "").strip())))
    cached = _probe_cache.get(cache_key)
    now = time.time()
    if cached and now - cached[0] < _PROBE_CACHE_TTL_SECONDS:
        return dict(cached[1])

    headers = {"Accept": "application/json"}
    if str(api_key or "").strip():
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = requests.get(endpoint, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        models = None
        if isinstance(payload, dict):
            if isinstance(payload.get("data"), list):
                models = payload.get("data")
            elif isinstance(payload.get("models"), list):
                models = payload.get("models")
        if not isinstance(models, list):
            raise ValueError("LM Studio 探针响应格式无效。")
        model_entry = next(
            (
                item
                for item in models
                if isinstance(item, dict)
                and str(item.get("id") or item.get("key") or "").strip() == normalized_model_id
            ),
            None,
        )
        if not isinstance(model_entry, dict):
            result = _normalize_probe_result(
                status="unknown",
                message="LM Studio 未返回该模型的能力信息。",
                model_id=normalized_model_id,
                endpoint=endpoint,
            )
        else:
            capabilities = model_entry.get("capabilities") or {}
            loaded_instances = model_entry.get("loaded_instances") or []
            context_length = None
            if loaded_instances and isinstance(loaded_instances[0], dict):
                context_length = int(
                    (((loaded_instances[0].get("config") or {}).get("context_length")) or 0) or 0
                ) or None
            max_context_length = int(model_entry.get("max_context_length") or 0) or None
            vision_supported = capabilities.get("vision")
            params = str(model_entry.get("params_string") or "").strip() or None
            if vision_supported is True:
                result = _normalize_probe_result(
                    status="supported",
                    message="本地视觉能力可用。",
                    model_id=normalized_model_id,
                    endpoint=endpoint,
                    vision_supported=True,
                    context_length=context_length,
                    max_context_length=max_context_length,
                    params=params,
                )
            elif vision_supported is False:
                result = _normalize_probe_result(
                    status="unsupported",
                    message="当前本地模型未启用图像输入能力，视觉调用会失败。",
                    model_id=normalized_model_id,
                    endpoint=endpoint,
                    vision_supported=False,
                    context_length=context_length,
                    max_context_length=max_context_length,
                    params=params,
                )
            else:
                result = _normalize_probe_result(
                    status="unknown",
                    message="未探测到明确的本地视觉能力标记。",
                    model_id=normalized_model_id,
                    endpoint=endpoint,
                    vision_supported=None,
                    context_length=context_length,
                    max_context_length=max_context_length,
                    params=params,
                )
    except Exception as exc:
        result = _normalize_probe_result(
            status="unknown",
            message=f"本地视觉能力探针不可用：{exc}",
            model_id=normalized_model_id,
            endpoint=endpoint,
        )

    _probe_cache[cache_key] = (now, dict(result))
    return result


def _scale_size(width: int, height: int) -> tuple[int, int]:
    width = max(1, int(width))
    height = max(1, int(height))
    scale_by_edge = min(1.0, LOCAL_IMAGE_MAX_LONG_EDGE / float(max(width, height)))
    scale_by_pixels = min(1.0, math.sqrt(LOCAL_IMAGE_MAX_TOTAL_PIXELS / float(width * height)))
    scale = min(scale_by_edge, scale_by_pixels)
    if scale >= 0.999:
        return width, height
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def _normalize_image(image: Image.Image) -> bytes:
    normalized = image
    if normalized.mode not in {"RGB", "RGBA"}:
        normalized = normalized.convert("RGBA" if "A" in normalized.getbands() else "RGB")
    if normalized.mode == "RGBA":
        canvas = Image.new("RGB", normalized.size, (255, 255, 255))
        canvas.paste(normalized, mask=normalized.getchannel("A"))
        normalized = canvas
    elif normalized.mode != "RGB":
        normalized = normalized.convert("RGB")

    target_size = _scale_size(*normalized.size)
    if tuple(normalized.size) != target_size:
        normalized = normalized.resize(target_size, _RESAMPLE_LANCZOS)

    buffer = BytesIO()
    normalized.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def build_inline_image_data_from_bytes(raw_bytes: bytes) -> Dict[str, Any]:
    if not raw_bytes:
        raise ValueError("图片内容为空，无法构造本地视觉请求。")
    with Image.open(BytesIO(raw_bytes)) as image:
        encoded_bytes = _normalize_image(image)
    encoded = base64.b64encode(encoded_bytes).decode("ascii")
    return {
        "dataUrl": f"data:image/png;base64,{encoded}",
        "mimeType": "image/png",
        "byteSize": len(encoded_bytes),
        "transportMode": "inline_base64_image",
    }


def build_inline_image_data_from_file(file_path: str | Path) -> Dict[str, Any]:
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        raise ValueError(f"图片文件不存在：{path}")
    return build_inline_image_data_from_bytes(path.read_bytes())


def download_remote_image_bytes(url: str, *, max_bytes: int = LOCAL_REMOTE_IMAGE_MAX_BYTES, timeout: float = 20.0) -> bytes:
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        content_type = str(response.headers.get("Content-Type") or "").strip().lower()
        if content_type and not content_type.startswith("image/"):
            raise ValueError(f"远程资源不是图片：{content_type}")
        collected = bytearray()
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if not chunk:
                continue
            collected.extend(chunk)
            if len(collected) > max_bytes:
                raise ValueError("远程图片超过本地视觉接入上限。")
        return bytes(collected)
