import mimetypes
import asyncio
import base64
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

import requests
from langchain_core.messages import HumanMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import interrupt

from core.artifact_store import artifact_store
from core.background_model_output import sanitize_background_model_output
from core.llm_factory import llm_factory
from core.local_visual_support import (
    build_inline_image_data_from_bytes,
    build_inline_image_data_from_file,
    download_remote_image_bytes,
    is_local_provider,
    probe_local_multimodal_capability,
)
from core.model_budget_service import model_budget_service
from core.model_control_plane import model_control_plane
from core.model_governance_exceptions import ModelGovernanceInterventionRequired
from core.multimodal_payload_adapter import (
    build_multimodal_content,
    build_multimodal_payload_metadata,
    describe_multimodal_payload_shape,
    infer_media_kind,
)
from core.tools.native.tool_governance import log_safety_review_auto_approved, should_auto_approve_safety_review
from core.workspace_resolution import workspace_resolution_service
from erc.runtime_context import get_runtime_context
from erc.safety_guardian import safety_guardian
from core.system_base import get_engine_origin
from core.workspace_guard import ensure_workspace_auto_create_allowed

_LARGE_MEDIA_S3_THRESHOLD = 25 * 1024 * 1024
_MAX_INLINE_AUDIO_BYTES = 10 * 1024 * 1024
_MAX_REMOTE_AUDIO_BYTES = 50 * 1024 * 1024
_SUPPORTED_AUDIO_MIME_TYPES = {
    "audio/mp3",
    "audio/mpeg",
}
_TRANSCODABLE_AUDIO_SUFFIXES = {
    ".aac",
    ".aiff",
    ".amr",
    ".flac",
    ".m4a",
    ".mp4",
    ".oga",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}
_AUDIO_SUFFIX_BY_MIME = {
    "audio/aac": ".aac",
    "audio/aiff": ".aiff",
    "audio/amr": ".amr",
    "audio/flac": ".flac",
    "audio/m4a": ".m4a",
    "audio/mp4": ".m4a",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/wav": ".wav",
    "audio/wave": ".wav",
    "audio/webm": ".webm",
    "audio/x-m4a": ".m4a",
    "audio/x-wav": ".wav",
}


def _upload_to_temp_s3_sync(file_path: Path) -> str:
    from core.tools.s3_tools import upload_file_to_s3

    try:
        return str(upload_file_to_s3(file_path, prefix="v8chat").get("url") or "")
    except Exception as e:
        raise ValueError(f"S3 Upload failed: Failed to upload {file_path}: {e}")


async def _upload_to_temp_s3(file_path: Path) -> str:
    return _upload_to_temp_s3_sync(file_path)


async def _mount_in_workspace(file_path: Path) -> str:
    import shutil
    runtime_context = get_runtime_context()
    workspace_dir = Path(
        workspace_resolution_service.resolve_workspace_path(
            runtime_kind=str(runtime_context.get("runtime_kind") or "") or None,
            session_id=str(runtime_context.get("session_id") or "") or None,
            explicit_workspace_id=str(runtime_context.get("workspace_id") or "") or None,
            explicit_project_id=str(runtime_context.get("project_id") or "") or None,
            explicit_workspace_path=str(runtime_context.get("workspace_path") or "") or None,
        )
    ).expanduser()
    workspace_dir = ensure_workspace_auto_create_allowed(
        workspace_dir,
        source="vision_media_analyzer.mount_in_workspace",
        allow_missing=True,
    )
    workspace_dir.mkdir(parents=True, exist_ok=True)
    target_path = workspace_dir / file_path.name
    if str(file_path.absolute()) != str(target_path.absolute()):
        shutil.copy2(file_path, target_path)
    return f"{get_engine_origin().rstrip('/')}/workspace/{file_path.name}"


def _try_upload_media_to_s3(file_path: Path) -> str:
    url = _upload_to_temp_s3_sync(file_path)
    if not url:
        raise ValueError("S3 上传未返回可用 URL。")
    return url


def _route_local_media_file_to_url(file_path: Path, *, allow_workspace_fallback: bool = False) -> tuple[str, dict[str, object]]:
    try:
        return _try_upload_media_to_s3(file_path), {"mediaRoutedToS3": True}
    except Exception as exc:
        if not allow_workspace_fallback:
            raise
        try:
            return asyncio.run(_mount_in_workspace(file_path)), {
                "mediaRoutedToS3": False,
                "mediaRouteFallback": "workspace_url",
                "mediaRouteFallbackReason": str(exc)[:240],
            }
        except Exception:
            raise exc


def _enforce_remote_media_guard(url: str, *, tool_call_id: str) -> tuple[bool, str | None]:
    runtime_context = get_runtime_context()
    decision = safety_guardian.assess_http_request("GET", url, body=None, runtime_context=runtime_context)
    safety_guardian.log_decision_event(
        action="vision_media_safety",
        decision=decision,
        subject=f"GET {url}",
        metadata={"toolCallId": tool_call_id},
    )
    if decision.is_allow():
        return True, None

    if should_auto_approve_safety_review(decision):
        log_safety_review_auto_approved(
            decision,
            action="vision_media_safety",
            subject=f"GET {url}",
            tool_call_id=tool_call_id,
        )
        return True, None

    response = interrupt(
        decision.to_interrupt_request(
            question=f"Safety Guardian 检测到远程媒体分析需要确认，是否继续？\n\nGET {url}",
            tool_call_id=tool_call_id,
        )
    )
    approved = True
    if isinstance(response, dict):
        approved = bool(response.get("approved", True))

    if decision.is_block() or not decision.allow_override:
        return False, f"Safety Guardian 已阻止远程媒体分析：{decision.reason}"
    if not approved:
        return False, f"Safety Guardian 未获得批准，远程媒体分析已取消：{decision.reason}"
    return True, None


def _non_image_error_for_local(media_kind: str) -> str:
    if media_kind == "video":
        return "当前本地模型不支持视频识别。"
    if media_kind == "audio":
        return "当前本地模型不支持音频识别。"
    if media_kind in {"document", "file"}:
        return "当前本地模型不支持文档直读。"
    return "当前本地模型只支持图片识别。"


def _document_redirect_message(media_kind: str) -> str:
    if media_kind == "video":
        return "当前视觉分析只保留视频 URL/S3 通道。"
    if media_kind == "audio":
        return "当前 vision_media_analyzer 支持音频 URL 或本地音频文件，并会在进入模型前统一转换为 MP3。"
    if media_kind in {"document", "file"}:
        return "当前 vision_media_analyzer 不再承担文档直读，请改用 web_fetch 或文本抽取链路。"
    return "当前 vision_media_analyzer 只处理图片、视频和音频。"


def _detail_ref_for_failure(tool_call_id: str = "") -> str:
    normalized = str(tool_call_id or "").strip()
    return f"tool_call:{normalized}" if normalized else "tool_call:vision_media_analyzer"


def _provider_rejected_audio(reason: str) -> bool:
    lowered = str(reason or "").lower()
    return (
        "input_audio" in lowered
        and (
            "not supported" in lowered
            or "invalidparameter" in lowered
            or "badrequest" in lowered
            or "capability_mismatch" in lowered
        )
    )


def _vision_failure_markdown(
    *,
    media_kind: str,
    reason: str,
    tool_call_id: str = "",
    raw_ref: str = "",
) -> str:
    if media_kind == "audio" and _provider_rejected_audio(reason):
        result = "音频识别失败"
        clean_reason = "当前模型接口拒绝音频输入，模型名可听不等于当前 provider 请求协议已打通。"
        next_action = "切换到已验证支持音频输入的视觉/多模态模型，或改用系统 STT。"
    elif media_kind == "audio":
        result = "音频识别失败"
        clean_reason = str(reason or "音频链路执行失败。").splitlines()[0][:220]
        next_action = "确认音频已是 MP3，或先通过受治理的媒体处理流程转码后重试。"
    else:
        result = "视觉分析失败"
        clean_reason = str(reason or "多模态分析失败。").splitlines()[0][:220]
        next_action = "确认模型支持当前媒体类型，或换用兼容的视觉/多模态模型。"
    lines = [
        f"结果：{result}",
        f"原因：{clean_reason}",
        f"下一步：{next_action}",
        f"detailRef：{_detail_ref_for_failure(tool_call_id)}",
    ]
    if raw_ref:
        lines.append(f"rawRef：{raw_ref}")
    else:
        lines.append("rawRef：运行时工具详情")
    return "\n".join(lines)


def _audio_requires_mp3_message(mime_type: str) -> str:
    return (
        "当前 vision_media_analyzer 会把音频统一交给模型为 MP3。"
        f"收到的音频类型是 {mime_type or 'unknown'}。"
        "但本次未能完成自动转换，请先用受治理的命令或媒体处理流程把音频转换为 mp3，再重新调用本工具。"
    )


def _is_supported_mp3_audio(mime_type: str, source_name: str = "") -> bool:
    normalized_mime = (mime_type or "").split(";", 1)[0].strip().lower()
    if normalized_mime in _SUPPORTED_AUDIO_MIME_TYPES:
        return True
    return str(source_name or "").strip().lower().split("?", 1)[0].endswith(".mp3")


def _looks_like_audio_source(source_name: str) -> bool:
    suffix = Path(str(source_name or "").split("?", 1)[0]).suffix.lower()
    return suffix in _TRANSCODABLE_AUDIO_SUFFIXES or suffix == ".mp3"


def _normalize_audio_mime(mime_type: str, source_name: str = "") -> str:
    normalized_mime = (mime_type or "").split(";", 1)[0].strip().lower()
    if normalized_mime:
        return normalized_mime
    guessed_mime, _ = mimetypes.guess_type(str(source_name or ""))
    return (guessed_mime or "").split(";", 1)[0].strip().lower()


def _suffix_for_audio_temp(mime_type: str, source_name: str = "") -> str:
    normalized_mime = _normalize_audio_mime(mime_type, source_name)
    if normalized_mime in _AUDIO_SUFFIX_BY_MIME:
        return _AUDIO_SUFFIX_BY_MIME[normalized_mime]
    suffix = Path(str(source_name or "").split("?", 1)[0]).suffix.lower()
    if suffix in _TRANSCODABLE_AUDIO_SUFFIXES or suffix == ".mp3":
        return suffix
    return ".audio"


def _transcode_audio_file_to_mp3(source_path: Path, target_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise ValueError("系统未检测到 ffmpeg，无法把当前音频自动转换为 MP3。请安装 ffmpeg 或先转换后重试。")

    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "64k",
        str(target_path),
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0 or not target_path.exists() or target_path.stat().st_size <= 0:
        stderr = (result.stderr or "").strip()
        if len(stderr) > 360:
            stderr = stderr[-360:]
        raise ValueError(f"ffmpeg 未能把音频转换为 MP3。{stderr or '没有可用的错误详情。'}")


def _download_remote_audio_to_file(url: str, mime_type: str) -> tuple[Path, str, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="v8-vision-audio-"))
    suffix = _suffix_for_audio_temp(mime_type, url)
    target_path = temp_dir / f"remote-audio{suffix}"
    try:
        with requests.get(url, stream=True, timeout=45) as response:
            response.raise_for_status()
            response_mime = _normalize_audio_mime(
                response.headers.get("Content-Type", "") if response.headers else "",
                url,
            )
            total = 0
            with target_path.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > _MAX_REMOTE_AUDIO_BYTES:
                        raise ValueError(
                            f"远程音频过大，下载上限为 {_MAX_REMOTE_AUDIO_BYTES} bytes。请先裁剪或压缩后重试。"
                        )
                    fh.write(chunk)
        return target_path, response_mime or mime_type, temp_dir
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def _build_inline_audio_data_from_file(file_path: Path, mime_type: str) -> tuple[str, int, dict[str, object]]:
    payload_ref, byte_size, metadata, transport_mode = _prepare_audio_payload_from_file(file_path, mime_type)
    if transport_mode != "inline_base64_audio":
        raise ValueError("音频已按大文件路由上传为 URL，无法作为 inline payload 返回。")
    return payload_ref, byte_size, metadata


def _prepare_audio_payload_from_file(file_path: Path, mime_type: str) -> tuple[str, int, dict[str, object], str]:
    normalized_mime = (mime_type or "").split(";", 1)[0].strip().lower()
    temp_dir: Path | None = None
    read_path = file_path
    original_byte_size = file_path.stat().st_size
    metadata: dict[str, object] = {
        "audioInputMimeType": normalized_mime or mime_type or "unknown",
        "audioOriginalByteSize": original_byte_size,
    }
    try:
        if not _is_supported_mp3_audio(normalized_mime, str(file_path)):
            temp_dir = Path(tempfile.mkdtemp(prefix="v8-vision-audio-mp3-"))
            read_path = temp_dir / f"{file_path.stem[:80] or 'audio'}.mp3"
            _transcode_audio_file_to_mp3(file_path, read_path)
            metadata.update(
                {
                    "audioTranscoded": True,
                    "audioTranscodeTarget": "audio/mpeg",
                }
            )
        else:
            metadata["audioTranscoded"] = False

        byte_size = read_path.stat().st_size
        should_use_url = byte_size > _MAX_INLINE_AUDIO_BYTES or original_byte_size > _LARGE_MEDIA_S3_THRESHOLD
        if should_use_url:
            try:
                url = _try_upload_media_to_s3(read_path)
            except Exception as exc:
                raise ValueError(
                    f"音频文件已超过内联上限或大媒体阈值，但无法上传到 S3：{exc}"
                ) from exc
            metadata.update(
                {
                    "audioRoutedToUrl": True,
                    "mediaRoutedToS3": True,
                    "audioRoutedByteSize": byte_size,
                }
            )
            return url, byte_size, metadata, "url_reference"
        metadata["audioRoutedToUrl"] = False
        return base64.b64encode(read_path.read_bytes()).decode("ascii"), byte_size, metadata, "inline_base64_audio"
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _build_inline_audio_data_from_url(url: str, mime_type: str) -> tuple[str, int, dict[str, object]]:
    payload_ref, byte_size, metadata, transport_mode = _prepare_audio_payload_from_url(url, mime_type)
    if transport_mode != "inline_base64_audio":
        raise ValueError("音频已按大文件路由上传为 URL，无法作为 inline payload 返回。")
    return payload_ref, byte_size, metadata


def _prepare_audio_payload_from_url(url: str, mime_type: str) -> tuple[str, int, dict[str, object], str]:
    downloaded_path, downloaded_mime, temp_dir = _download_remote_audio_to_file(url, mime_type)
    try:
        data, byte_size, metadata, transport_mode = _prepare_audio_payload_from_file(downloaded_path, downloaded_mime or mime_type)
        metadata["audioRemoteDownloaded"] = True
        return data, byte_size, metadata, transport_mode
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@tool
def vision_media_analyzer(
    file_path: str = "",
    source_url: str = "",
    mime_type_hint: str = "",
    prompt: str = "详细描述这个文件里的内容。如果包含文字请提取出来。如果是视频，请总结视频的剧情和关键帧变化。",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Analyze images, videos, and audio directly using a multimodal LLM.
    
    When a user uploads a media file (image/video/audio) via the Web UI or a Channel (Feishu), 
    their message will explicitly contain a system injected path: `[User uploaded file: /path/to/media.mp4]`.
    
    Extract that local path, and pass it immediately to this tool along with your analytical requirements in `prompt`.
    This tool returns textual analysis which you can incorporate into your reasoning.
    
    Arguments:
        file_path (str): The absolute local filesystem path to the uploaded image, video, or audio file. Non-MP3 audio is converted to MP3 before model input.
        source_url (str): 远程图片/视频/音频 URL，可直接消费 web_fetch 返回的 visionCandidates；非 MP3 音频会先下载并转换为 MP3。
        mime_type_hint (str): 远程媒体的 MIME 类型提示，可选。
        prompt (str): Your specific instructions to the Vision LLM (e.g., "Extract the error code from this screenshot").
    """
    try:
        resolved_url = str(source_url or "").strip()
        local_path_value = str(file_path or "").strip()
        path: Path | None = None
        display_source = resolved_url or local_path_value
        transport_mode = "url_reference"
        payload_media_ref = resolved_url
        inline_image_payload: dict[str, object] | None = None
        inline_media_byte_size: int | None = None
        audio_metadata: dict[str, object] = {}
        media_route_metadata: dict[str, object] = {}

        if resolved_url:
            print(f"[VisionMediaAnalyzer] Starting remote analysis for: {resolved_url}")
        else:
            path = Path(local_path_value)
            if not path.exists() or not path.is_file():
                return f"Error: The file '{local_path_value}' does not exist on the local filesystem."
            print(f"[VisionMediaAnalyzer] Starting analysis for: {local_path_value}")

        runtime_context = get_runtime_context()
        resolved_role = str(runtime_context.get("vision_role_override") or "vision").strip() or "vision"
        role_resolution = model_control_plane.resolve_model_for_role(resolved_role)
        resolved_provider = dict(role_resolution.get("resolvedProvider") or {})
        resolved_model = dict(role_resolution.get("resolvedModel") or {})
        provider_type = str(resolved_provider.get("type") or "API").upper()
        api_standard = str(resolved_provider.get("api_standard") or "openai")
        resolved_model_id = str(role_resolution.get("resolvedModelId") or "")

        if path is not None:
            mime, _ = mimetypes.guess_type(str(path))
            mime = mime or "application/octet-stream"
        else:
            guessed_mime, _ = mimetypes.guess_type(urlparse(resolved_url).path)
            mime = str(mime_type_hint or guessed_mime or "application/octet-stream").strip()
        media_kind = infer_media_kind(mime)
        if media_kind == "file" and _looks_like_audio_source(resolved_url or str(path or "")):
            mime = _normalize_audio_mime(mime if mime != "application/octet-stream" else "", resolved_url or str(path or "")) or "audio/mpeg"
            media_kind = "audio"

        if is_local_provider(resolved_provider):
            if media_kind == "video":
                return _non_image_error_for_local(media_kind)

            probe = probe_local_multimodal_capability(
                model_id=resolved_model_id,
                provider_type=provider_type,
                base_url=str(resolved_provider.get("base_url") or ""),
                api_key=str(resolved_provider.get("api_key") or ""),
            )
            if probe.get("status") == "unsupported":
                return str(probe.get("message") or "当前本地模型未启用图像输入能力，视觉调用会失败。")

            if path is not None:
                try:
                    inline_image_payload = build_inline_image_data_from_file(path)
                except Exception:
                    return _non_image_error_for_local(media_kind)
            else:
                allowed, error_message = _enforce_remote_media_guard(resolved_url, tool_call_id=tool_call_id)
                if not allowed:
                    return error_message or "Safety Guardian 已阻止远程媒体分析。"
                try:
                    remote_bytes = download_remote_image_bytes(resolved_url)
                    inline_image_payload = build_inline_image_data_from_bytes(remote_bytes)
                except Exception:
                    return _non_image_error_for_local(media_kind)

            transport_mode = "inline_base64_image"
            payload_media_ref = str((inline_image_payload or {}).get("dataUrl") or "")
            mime = str((inline_image_payload or {}).get("mimeType") or "image/png")
            media_kind = "image"
            inline_media_byte_size = int((inline_image_payload.get("byteSize") or 0) or 0)
        else:
            if media_kind == "video":
                if resolved_url:
                    allowed, error_message = _enforce_remote_media_guard(resolved_url, tool_call_id=tool_call_id)
                    if not allowed:
                        return error_message or "Safety Guardian 已阻止远程媒体分析。"
                elif path is not None:
                    try:
                        resolved_url, media_route_metadata = _route_local_media_file_to_url(
                            path,
                            allow_workspace_fallback=True,
                        )
                    except Exception:
                        resolved_url = asyncio.run(_mount_in_workspace(path))
                    print(f"[VisionMediaAnalyzer] Media temporarily mapped to URL: {resolved_url}")
                payload_media_ref = resolved_url
            elif media_kind == "audio":
                if resolved_url:
                    allowed, error_message = _enforce_remote_media_guard(resolved_url, tool_call_id=tool_call_id)
                    if not allowed:
                        return error_message or "Safety Guardian 已阻止远程媒体分析。"
                    if _is_supported_mp3_audio(mime, resolved_url):
                        payload_media_ref = resolved_url
                    else:
                        try:
                            payload_media_ref, inline_media_byte_size, audio_metadata, transport_mode = _prepare_audio_payload_from_url(
                                resolved_url,
                                mime,
                            )
                        except Exception as exc:
                            return f"当前音频 URL 无法转换为 MP3 后交给多模态模型分析：{exc}"
                        mime = "audio/mpeg"
                elif path is not None:
                    try:
                        payload_media_ref, inline_media_byte_size, audio_metadata, transport_mode = _prepare_audio_payload_from_file(path, mime)
                    except Exception as exc:
                        return f"当前音频文件无法转换为 MP3 后交给多模态模型分析：{exc}"
                    mime = "audio/mpeg"
            else:
                try:
                    if path is not None:
                        if path.stat().st_size > _LARGE_MEDIA_S3_THRESHOLD:
                            try:
                                resolved_url, media_route_metadata = _route_local_media_file_to_url(path)
                                transport_mode = "url_reference"
                                payload_media_ref = resolved_url
                                media_kind = "image"
                                inline_media_byte_size = path.stat().st_size
                            except Exception:
                                inline_image_payload = build_inline_image_data_from_file(path)
                        else:
                            inline_image_payload = build_inline_image_data_from_file(path)
                    else:
                        allowed, error_message = _enforce_remote_media_guard(resolved_url, tool_call_id=tool_call_id)
                        if not allowed:
                            return error_message or "Safety Guardian 已阻止远程媒体分析。"
                        remote_bytes = download_remote_image_bytes(resolved_url)
                        inline_image_payload = build_inline_image_data_from_bytes(remote_bytes)
                    if inline_image_payload is not None:
                        transport_mode = "inline_base64_image"
                        payload_media_ref = str((inline_image_payload or {}).get("dataUrl") or "")
                        mime = str((inline_image_payload or {}).get("mimeType") or "image/png")
                        media_kind = "image"
                        inline_media_byte_size = int((inline_image_payload.get("byteSize") or 0) or 0)
                except Exception:
                    return _document_redirect_message(media_kind)

        # 2. Get the Vision model via llm_factory's role-based resolution (reads models.json → roles.vision)
        try:
            vision_llm = llm_factory.create_for_role(resolved_role, temperature=0.1)
        except Exception as e:
            print(f"[VisionMediaAnalyzer] Vision model initialization failed: {e}")
            raise e

        ctx = runtime_context
        metadata = {
            "source": "vision_media_analyzer",
            "apiStandard": api_standard,
            "prompt": prompt[:200],
            "transportMode": transport_mode,
            "providerId": str(role_resolution.get("resolvedProviderId") or ""),
            "modelId": resolved_model_id,
            "role": resolved_role,
            **media_route_metadata,
            **audio_metadata,
        }
        payload_provider_id = str(metadata.get("providerId") or "")
        payload_shape = describe_multimodal_payload_shape(
            mime_type=mime,
            api_standard=api_standard,
            provider_id=payload_provider_id,
            model_id=resolved_model_id,
            transport_mode=transport_mode,
        )
        metadata["payloadShape"] = payload_shape
        if inline_image_payload is not None:
            metadata["byteSize"] = int((inline_image_payload.get("byteSize") or 0) or 0)
        if inline_media_byte_size is not None:
            metadata["byteSize"] = int(inline_media_byte_size)

        if path is not None:
            artifact_store.record_local_file(
                file_path=path,
                session_id=ctx.get("session_id"),
                run_id=ctx.get("run_id"),
                workspace_path=str(path),
                external_url=resolved_url if transport_mode == "url_reference" else None,
                preview_url=resolved_url if transport_mode == "url_reference" else None,
                metadata=metadata,
                source_component="vision_media_analyzer",
                node="vision_media_analyzer",
            )
        else:
            artifact_store.record_artifact(
                artifact_kind=infer_media_kind(mime),
                mime_type=mime,
                session_id=ctx.get("session_id"),
                run_id=ctx.get("run_id"),
                title=display_source,
                external_url=resolved_url,
                preview_url=resolved_url,
                metadata={**metadata, "remoteSource": True},
                source_component="vision_media_analyzer",
                node="vision_media_analyzer",
            )
        model_budget_service.enforce_or_raise(
            config=model_control_plane.get_config(),
            run_id=ctx.get("run_id"),
            project_id=ctx.get("project_id"),
            role=resolved_role,
            capability_class=str(resolved_model.get("capabilityClass") or "vision_multimodal"),
            model_id=resolved_model_id,
        )

        # 3. Construct standardized multimodal message payload and keep provider-specific
        # divergence inside the adapter layer.
        content_components = build_multimodal_content(
            prompt=prompt,
            media_url=payload_media_ref,
            mime_type=mime,
            api_standard=api_standard,
            transport_mode=transport_mode,
            provider_id=payload_provider_id,
            model_id=resolved_model_id,
        )
             
        # 4. Invoke LLM
        msg = HumanMessage(content=content_components)
        print(f"[VisionMediaAnalyzer] Invoking Vision LLM...")
        payload_metadata = build_multimodal_payload_metadata(
            mime_type=mime,
            media_url=payload_media_ref if transport_mode == "url_reference" else "",
            api_standard=api_standard,
            transport_mode=transport_mode,
            source_ref=display_source,
            byte_size=inline_media_byte_size,
            provider_id=payload_provider_id,
            model_id=resolved_model_id,
        )
        payload_metadata["providerId"] = payload_provider_id
        payload_metadata["modelId"] = resolved_model_id
        payload_metadata["role"] = resolved_role
        for key in (
            "audioInputMimeType",
            "audioOriginalByteSize",
            "audioRemoteDownloaded",
            "audioRoutedByteSize",
            "audioRoutedToUrl",
            "audioTranscodeTarget",
            "audioTranscoded",
            "mediaRouteFallback",
            "mediaRouteFallbackReason",
            "mediaRoutedToS3",
        ):
            if key in metadata:
                payload_metadata[key] = metadata[key]
        
        # Actually in standard Langchain format for videos, it might crash some adapters if `video_url` isn't fully supported.
        # But since the user explicitly confirmed doubao-seed-2.0-pro supports it, we'll send it and let the provider adapter handle serialization.
        response = vision_llm.invoke(
            [msg],
            {"metadata": payload_metadata},
        )

        sanitized = sanitize_background_model_output(response)
        if not sanitized.text:
            return _vision_failure_markdown(
                media_kind=media_kind,
                reason="background_output_no_visible_text",
                tool_call_id=tool_call_id,
            )
        return f"--- Vision Analysis Complete ---\nSource: {display_source}\n{sanitized.text}"
        
    except ModelGovernanceInterventionRequired:
        raise
    except Exception as e:
        print(f"[VisionMediaAnalyzer] Raw provider/runtime failure: {repr(e)}")
        try:
            failed_kind = media_kind
        except Exception:
            failed_kind = "file"
        return _vision_failure_markdown(
            media_kind=failed_kind,
            reason=str(e),
            tool_call_id=tool_call_id,
        )
