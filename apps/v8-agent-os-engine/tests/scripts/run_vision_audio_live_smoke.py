from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.model_control_plane import model_control_plane  # noqa: E402
from core.multimodal_payload_adapter import (  # noqa: E402
    build_multimodal_content,
    describe_multimodal_payload_shape,
)
from core.tools import vision_media_analyzer as vision_module  # noqa: E402


DEFAULT_PROMPT = "请完整转录这段音频中用户说出的所有文字内容，逐字逐句地还原，不要遗漏任何部分。"


def _role_resolution(role: str) -> dict:
    return dict(model_control_plane.resolve_model_for_role(role) or {})


def _payload_preview(
    *,
    provider_id: str,
    model_id: str,
    api_standard: str,
    media_ref: str,
    transport_mode: str,
) -> dict:
    content = build_multimodal_content(
        prompt=DEFAULT_PROMPT,
        media_url=media_ref,
        mime_type="audio/mpeg",
        api_standard=api_standard,
        transport_mode=transport_mode,
        provider_id=provider_id,
        model_id=model_id,
    )
    audio_item = next((item for item in content if item.get("type") in {"input_audio", "media"}), {})
    redacted_audio_item = dict(audio_item)
    if isinstance(redacted_audio_item.get("audio_url"), str):
        value = str(redacted_audio_item["audio_url"])
        redacted_audio_item["audio_url"] = value[:40] + "...<redacted>" if value.startswith("data:") else value
    if isinstance(redacted_audio_item.get("input_audio"), dict):
        nested = dict(redacted_audio_item["input_audio"])
        if nested.get("data"):
            nested["data"] = "<base64 redacted>"
        redacted_audio_item["input_audio"] = nested
    if redacted_audio_item.get("data"):
        redacted_audio_item["data"] = "<base64 redacted>"
    return {
        "payloadShape": describe_multimodal_payload_shape(
            mime_type="audio/mpeg",
            api_standard=api_standard,
            provider_id=provider_id,
            model_id=model_id,
            transport_mode=transport_mode,
        ),
        "audioItem": redacted_audio_item,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Live smoke for vision_media_analyzer audio input.")
    parser.add_argument("--role", default="vision", help="Model role to test. Default: vision.")
    parser.add_argument("--mp3", default="", help="Path to an mp3 sample. Required for --live.")
    parser.add_argument("--live", action="store_true", help="Actually call the configured provider.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    args = parser.parse_args()

    stage = "preflight"
    resolution = _role_resolution(args.role)
    provider = dict(resolution.get("resolvedProvider") or {})
    provider_id = str(resolution.get("resolvedProviderId") or "")
    model_id = str(resolution.get("resolvedModelId") or "")
    api_standard = str(provider.get("api_standard") or "openai")

    report: dict[str, object] = {
        "providerId": provider_id,
        "modelId": model_id,
        "api_standard": api_standard,
        "transport": "inline_base64",
        "stage": stage,
        "resultText": "",
        "error": "",
    }

    mp3_path = Path(args.mp3).expanduser() if args.mp3 else None
    if mp3_path and mp3_path.exists():
        try:
            stage = "prepare_audio"
            media_ref, byte_size, metadata, transport_mode = vision_module._prepare_audio_payload_from_file(
                mp3_path,
                "audio/mpeg",
            )
            report.update(
                {
                    "transport": "base64" if transport_mode == "inline_base64_audio" else "url",
                    "byteSize": byte_size,
                    "prepareMetadata": metadata,
                    "stage": stage,
                }
            )
        except Exception as exc:
            report.update({"stage": stage, "error": str(exc)})
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
    else:
        media_ref = "AAEC"
        transport_mode = "inline_base64_audio"
        if args.live:
            report.update(
                {
                    "stage": "input_missing",
                    "error": "Live mode requires --mp3 pointing to a real mp3 sample.",
                }
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2

    report["payload"] = _payload_preview(
        provider_id=provider_id,
        model_id=model_id,
        api_standard=api_standard,
        media_ref=media_ref,
        transport_mode=transport_mode,
    )

    if not args.live:
        report["stage"] = "dry_run"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    try:
        stage = "invoke_tool"
        output = vision_module.vision_media_analyzer.func(file_path=str(mp3_path), prompt=args.prompt)
        report.update(
            {
                "stage": "completed" if "--- Vision Analysis Complete ---" in output else "tool_returned_failure",
                "resultText": output[:2000],
            }
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["stage"] == "completed" else 3
    except Exception as exc:
        report.update({"stage": stage, "error": str(exc)})
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
