from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

from langchain_core.tools import tool


def _s3_config() -> dict[str, Any]:
    from core.storage import storage

    system_base = storage.get_system_base_config()
    config = system_base.get("s3") if isinstance(system_base.get("s3"), dict) else {}
    if not config or not config.get("accessKeyId") or not config.get("secretAccessKey") or not config.get("bucket"):
        raise ValueError("S3 未配置完整。请先在 Admin 的 systemBase.s3 中配置 endpoint、bucket、accessKeyId 和 secretAccessKey。")
    return dict(config)


def _client(config: dict[str, Any]):
    import boto3
    from botocore.config import Config as BotoConfig

    return boto3.client(
        "s3",
        endpoint_url=config.get("endpoint"),
        region_name=config.get("region"),
        aws_access_key_id=config.get("accessKeyId"),
        aws_secret_access_key=config.get("secretAccessKey"),
        config=BotoConfig(s3={"addressing_style": "path"}, signature_version="s3"),
    )


def _public_url(config: dict[str, Any], key: str) -> str:
    endpoint = str(config.get("endpoint") or "").rstrip("/")
    if endpoint and not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"
    return f"{endpoint}/{config.get('bucket')}/{key.lstrip('/')}"


def upload_file_to_s3(file_path: str | Path, *, key: str | None = None, prefix: str = "v8chat") -> dict[str, Any]:
    path = Path(file_path).expanduser()
    if not path.exists() or not path.is_file():
        raise ValueError(f"文件不存在或不是普通文件：{path}")
    config = _s3_config()
    object_key = (key or f"{prefix.strip('/').rstrip('/')}/media_{path.name}").lstrip("/")
    mime_type, _ = mimetypes.guess_type(str(path))
    content_type = mime_type or "application/octet-stream"
    _client(config).upload_file(
        Filename=str(path),
        Bucket=str(config.get("bucket")),
        Key=object_key,
        ExtraArgs={"ContentType": content_type},
    )
    return {
        "bucket": config.get("bucket"),
        "key": object_key,
        "url": _public_url(config, object_key),
        "contentType": content_type,
        "size": path.stat().st_size,
    }


def _render_s3_broker_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _render_s3_broker_error(mode: str, error: str, *, code: str | None = None) -> str:
    payload: dict[str, Any] = {
        "ok": False,
        "mode": mode,
        "summary": error,
        "error": error,
    }
    if code:
        payload["code"] = code
    return _render_s3_broker_payload(payload)


@tool
def s3_upload_file(file_path: str, key: str = "", prefix: str = "v8chat") -> str:
    """Upload a local workspace file to the configured S3-compatible bucket and return its public URL."""
    result = upload_file_to_s3(file_path, key=key or None, prefix=prefix or "v8chat")
    return (
        "S3 上传完成：\n"
        f"- bucket: {result['bucket']}\n"
        f"- key: {result['key']}\n"
        f"- url: {result['url']}\n"
        f"- contentType: {result['contentType']}\n"
        f"- size: {result['size']}"
    )


@tool
def s3_list_objects(prefix: str = "v8chat", limit: int = 50) -> str:
    """List objects from the configured S3-compatible bucket by prefix."""
    config = _s3_config()
    max_keys = max(1, min(int(limit or 50), 200))
    response = _client(config).list_objects_v2(
        Bucket=str(config.get("bucket")),
        Prefix=str(prefix or ""),
        MaxKeys=max_keys,
    )
    objects = list(response.get("Contents") or [])
    if not objects:
        return f"S3 中没有找到 prefix={prefix!r} 的对象。"
    lines = [f"S3 对象列表（bucket={config.get('bucket')}, prefix={prefix}, count={len(objects)}）："]
    for item in objects:
        key = str(item.get("Key") or "")
        lines.append(f"- {key} ({int(item.get('Size') or 0)} bytes) { _public_url(config, key) }")
    return "\n".join(lines)


@tool
def s3_download_file(key: str, destination_path: str) -> str:
    """Download an object from the configured S3-compatible bucket to a local file path."""
    config = _s3_config()
    target = Path(destination_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    _client(config).download_file(str(config.get("bucket")), key.lstrip("/"), str(target))
    return f"S3 下载完成：{key} -> {target}"


@tool
def s3_broker(
    mode: str = "list",
    file_path: str = "",
    key: str = "",
    prefix: str = "v8chat",
    destination_path: str = "",
    limit: int = 50,
) -> str:
    """Unified S3 broker for upload, list, and download operations with a compact JSON contract."""
    normalized_mode = str(mode or "list").strip().lower()
    try:
        if normalized_mode == "upload":
            normalized_file_path = str(file_path or "").strip()
            if not normalized_file_path:
                return _render_s3_broker_error(normalized_mode, "s3_broker(mode=upload) 需要提供 file_path。", code="missing_file_path")
            resolved_prefix = str(prefix or "v8chat").strip() or "v8chat"
            result = upload_file_to_s3(
                normalized_file_path,
                key=str(key or "").strip() or None,
                prefix=resolved_prefix,
            )
            return _render_s3_broker_payload(
                {
                    "ok": True,
                    "mode": normalized_mode,
                    "summary": f"已上传 {Path(normalized_file_path).name} 到 S3。",
                    "bucket": result.get("bucket"),
                    "key": result.get("key"),
                    "url": result.get("url"),
                    "contentType": result.get("contentType"),
                    "size": result.get("size"),
                }
            )

        if normalized_mode == "list":
            config = _s3_config()
            resolved_prefix = str(prefix or "").strip()
            max_keys = max(1, min(int(limit or 50), 200))
            response = _client(config).list_objects_v2(
                Bucket=str(config.get("bucket")),
                Prefix=resolved_prefix,
                MaxKeys=max_keys,
            )
            objects = list(response.get("Contents") or [])
            return _render_s3_broker_payload(
                {
                    "ok": True,
                    "mode": normalized_mode,
                    "summary": (
                        f"prefix={resolved_prefix or '/'} 下找到 {len(objects)} 个对象。"
                        if objects
                        else f"prefix={resolved_prefix or '/'} 下没有对象。"
                    ),
                    "bucket": config.get("bucket"),
                    "prefix": resolved_prefix,
                    "count": len(objects),
                    "objects": [
                        {
                            "key": str(item.get("Key") or ""),
                            "size": int(item.get("Size") or 0),
                            "url": _public_url(config, str(item.get("Key") or "")),
                        }
                        for item in objects
                    ],
                }
            )

        if normalized_mode == "download":
            normalized_key = str(key or "").strip()
            normalized_destination = str(destination_path or "").strip()
            if not normalized_key:
                return _render_s3_broker_error(normalized_mode, "s3_broker(mode=download) 需要提供 key。", code="missing_key")
            if not normalized_destination:
                return _render_s3_broker_error(
                    normalized_mode,
                    "s3_broker(mode=download) 需要提供 destination_path。",
                    code="missing_destination_path",
                )
            config = _s3_config()
            target = Path(normalized_destination).expanduser()
            target.parent.mkdir(parents=True, exist_ok=True)
            _client(config).download_file(str(config.get("bucket")), normalized_key.lstrip("/"), str(target))
            return _render_s3_broker_payload(
                {
                    "ok": True,
                    "mode": normalized_mode,
                    "summary": f"已下载 {normalized_key}。",
                    "bucket": config.get("bucket"),
                    "key": normalized_key,
                    "destinationPath": str(target),
                    "downloaded": True,
                }
            )
        return _render_s3_broker_error(
            normalized_mode,
            f"Unsupported s3_broker mode: {normalized_mode}",
            code="unsupported_mode",
        )
    except Exception as exc:
        return _render_s3_broker_error(normalized_mode, str(exc), code="s3_operation_failed")
