import os
import mimetypes
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from langchain_core.messages import HumanMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.types import interrupt

from core.artifact_store import artifact_store
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
    infer_media_kind,
)
from erc.runtime_context import get_runtime_context
from erc.safety_guardian import safety_guardian
from core.system_base import get_engine_origin

async def _upload_to_temp_s3(file_path: Path) -> str:
    from core.storage import storage

    system_base = storage.get_system_base_config()
    s3_config = system_base.get("s3") if isinstance(system_base.get("s3"), dict) else None
    
    if not s3_config or not s3_config.get("accessKeyId"):
        raise ValueError("S3 is not configured. Vision models require a public URL to process local media. Please configure S3 credentials in the Admin UI first.")
    
    import boto3
    from botocore.config import Config as BotoConfig
    from botocore.exceptions import NoCredentialsError
    from urllib.parse import urlparse
    
    # Many S3-compatible services (MinIO, hi168, etc.) require path-style addressing + V2 signature
    s3_client = boto3.client('s3',
        endpoint_url=s3_config.get('endpoint'),
        region_name=s3_config.get('region'),
        aws_access_key_id=s3_config.get('accessKeyId'),
        aws_secret_access_key=s3_config.get('secretAccessKey'),
        config=BotoConfig(
            s3={'addressing_style': 'path'},
            signature_version='s3'
        )
    )
    bucket_name = s3_config.get('bucket')
    
    key = f"v8chat/media_{file_path.name}"
    
    mime_type, _ = mimetypes.guess_type(str(file_path))
    content_type = mime_type or 'application/octet-stream'
    
    try:
        s3_client.upload_file(
            Filename=str(file_path),
            Bucket=bucket_name,
            Key=key,
            ExtraArgs={'ContentType': content_type}
        )
        endpoint = s3_config.get('endpoint', '').rstrip('/')
        if not endpoint.startswith("http"):
            endpoint = "https://" + endpoint
        
        # Path-style URL: endpoint/bucket/key
        return f"{endpoint}/{bucket_name}/{key}"
    except Exception as e:
        raise ValueError(f"S3 Upload failed: Failed to upload {file_path} to {bucket_name}/{key}: {e}")

async def _mount_in_workspace(file_path: Path) -> str:
    import shutil
    from core.storage import storage
    config = storage.get_workspace_config()
    default_workspace = Path(__file__).resolve().parents[2] / "workspace"
    workspace_dir = Path(config.get("agent_workspace_path", str(default_workspace)))
    if not workspace_dir.is_absolute():
         base_dir = Path(os.getcwd())
         workspace_dir = base_dir / workspace_dir
    workspace_dir.mkdir(parents=True, exist_ok=True)
    target_path = workspace_dir / file_path.name
    if str(file_path.absolute()) != str(target_path.absolute()):
        shutil.copy2(file_path, target_path)
    return f"{get_engine_origin().rstrip('/')}/workspace/{file_path.name}"


def _enforce_remote_media_guard(url: str, *, tool_call_id: str) -> tuple[bool, str | None]:
    runtime_context = get_runtime_context()
    decision = safety_guardian.assess_http_request("GET", url, body=None, runtime_context=runtime_context)
    if decision.is_allow():
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
    if media_kind in {"document", "file"}:
        return "当前本地模型不支持文档直读。"
    return "当前本地模型只支持图片识别。"


def _document_redirect_message(media_kind: str) -> str:
    if media_kind == "video":
        return "当前视觉分析只保留视频 URL/S3 通道。"
    if media_kind in {"document", "file"}:
        return "当前 vision_media_analyzer 不再承担文档直读，请改用 web_fetch 或文本抽取链路。"
    return "当前 vision_media_analyzer 只处理图片和视频。"


@tool
def vision_media_analyzer(
    file_path: str = "",
    source_url: str = "",
    mime_type_hint: str = "",
    prompt: str = "详细描述这个文件里的内容。如果包含文字请提取出来。如果是视频，请总结视频的剧情和关键帧变化。",
    tool_call_id: Annotated[str, InjectedToolCallId] = "",
) -> str:
    """Analyze images and videos directly using a powerful Vision LLM.
    
    When a user uploads a media file (image/video) via the Web UI or a Channel (Feishu), 
    their message will explicitly contain a system injected path: `[User uploaded file: /path/to/media.mp4]`.
    
    Extract that local path, and pass it immediately to this tool along with your analytical requirements in `prompt`.
    This tool natively supports analyzing long video files without manual frame extraction, 
    and returns textual analysis which you can incorporate into your reasoning.
    
    Arguments:
        file_path (str): The absolute local filesystem path to the uploaded image or video.
        source_url (str): 远程图片/视频/文档 URL，可直接消费 web_fetch 返回的 visionCandidates。
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
        else:
            if media_kind == "video":
                if resolved_url:
                    allowed, error_message = _enforce_remote_media_guard(resolved_url, tool_call_id=tool_call_id)
                    if not allowed:
                        return error_message or "Safety Guardian 已阻止远程媒体分析。"
                elif path is not None:
                    import asyncio
                    try:
                        resolved_url = asyncio.run(_upload_to_temp_s3(path))
                    except Exception:
                        resolved_url = asyncio.run(_mount_in_workspace(path))
                    print(f"[VisionMediaAnalyzer] Media temporarily mapped to URL: {resolved_url}")
                payload_media_ref = resolved_url
            else:
                try:
                    if path is not None:
                        inline_image_payload = build_inline_image_data_from_file(path)
                    else:
                        allowed, error_message = _enforce_remote_media_guard(resolved_url, tool_call_id=tool_call_id)
                        if not allowed:
                            return error_message or "Safety Guardian 已阻止远程媒体分析。"
                        remote_bytes = download_remote_image_bytes(resolved_url)
                        inline_image_payload = build_inline_image_data_from_bytes(remote_bytes)
                    transport_mode = "inline_base64_image"
                    payload_media_ref = str((inline_image_payload or {}).get("dataUrl") or "")
                    mime = str((inline_image_payload or {}).get("mimeType") or "image/png")
                    media_kind = "image"
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
        }
        if inline_image_payload is not None:
            metadata["byteSize"] = int((inline_image_payload.get("byteSize") or 0) or 0)

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
        )
             
        # 4. Invoke LLM
        msg = HumanMessage(content=content_components)
        print(f"[VisionMediaAnalyzer] Invoking Vision LLM...")
        
        # Actually in standard Langchain format for videos, it might crash some adapters if `video_url` isn't fully supported.
        # But since the user explicitly confirmed doubao-seed-2.0-pro supports it, we'll send it and let the provider adapter handle serialization.
        response = vision_llm.invoke(
            [msg],
            {
                "metadata": build_multimodal_payload_metadata(
                    mime_type=mime,
                    media_url=payload_media_ref if transport_mode == "url_reference" else "",
                    api_standard=api_standard,
                    transport_mode=transport_mode,
                    source_ref=display_source,
                    byte_size=int((inline_image_payload or {}).get("byteSize") or 0) or None,
                )
            },
        )

        return f"--- Vision Analysis Complete ---\nSource: {display_source}\n{response.content}"
        
    except ModelGovernanceInterventionRequired:
        raise
    except Exception as e:
        return f"Vision Media Analysis Failed: {str(e)}"
