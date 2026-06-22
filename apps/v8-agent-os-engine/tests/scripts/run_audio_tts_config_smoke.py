from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from core.audio.audio_config import AudioConfigManager  # noqa: E402
from core.audio.tts_provider import TTSManager  # noqa: E402


def _redacted_tts_config() -> dict[str, object]:
    config = AudioConfigManager.get_config()
    tts = config.get("tts", {}) if isinstance(config, dict) else {}
    custom = tts.get("custom", {}) if isinstance(tts.get("custom"), dict) else {}
    return {
        "activeProvider": tts.get("active_provider"),
        "custom": {
            "endpoint": custom.get("endpoint"),
            "protocol": custom.get("protocol"),
            "model": custom.get("model"),
            "voice": custom.get("voice"),
            "format": custom.get("format"),
            "speed": custom.get("speed"),
            "responseAudioPath": custom.get("responseAudioPath") or custom.get("response_audio_path"),
            "hasApiKey": bool(custom.get("api_key")),
            "headerKeys": sorted(custom.get("headers", {}).keys()) if isinstance(custom.get("headers"), dict) else [],
        },
    }


async def _collect_tts_bytes(text: str, max_bytes: int) -> bytes:
    provider = TTSManager.get_provider()
    chunks: list[bytes] = []
    total = 0
    async for chunk in provider.synthesize_stream(text):
        if not chunk:
            continue
        remaining = max_bytes - total
        if remaining <= 0:
            break
        chunks.append(chunk[:remaining])
        total += len(chunks[-1])
        if total >= max_bytes:
            break
    return b"".join(chunks)


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-check the configured V8OS TTS provider.")
    parser.add_argument("--live", action="store_true", help="Actually call the configured TTS provider.")
    parser.add_argument("--text", default="你好，V8OS 语音合成测试。")
    parser.add_argument("--output", default="", help="Optional output audio path for --live.")
    parser.add_argument("--max-bytes", type=int, default=2_000_000)
    args = parser.parse_args()

    summary = _redacted_tts_config()
    active_provider = summary.get("activeProvider")
    custom = summary.get("custom") if isinstance(summary.get("custom"), dict) else {}
    is_minimax = active_provider == "custom" and custom.get("protocol") == "minimax_t2a_v2"

    result: dict[str, object] = {
        "ok": False,
        "kind": "audio_tts_config_smoke",
        "config": summary,
        "live": bool(args.live),
    }

    if not is_minimax:
        result["reason"] = "minimax_tts_not_configured"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if not custom.get("endpoint") or not custom.get("hasApiKey"):
        result["reason"] = "minimax_tts_missing_endpoint_or_key"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if not args.live:
        result["ok"] = True
        result["reason"] = "minimax_tts_configured_live_not_requested"
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    audio_bytes = asyncio.run(_collect_tts_bytes(args.text, args.max_bytes))
    result["ok"] = len(audio_bytes) > 0
    result["audioBytes"] = len(audio_bytes)
    if audio_bytes and args.output:
        output_path = Path(args.output).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio_bytes)
        result["output"] = str(output_path)
    if not audio_bytes:
        result["reason"] = "tts_provider_returned_no_audio"
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if audio_bytes else 1


if __name__ == "__main__":
    raise SystemExit(main())
