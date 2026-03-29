from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from core.v8_agent_os_paths import V8_AGENT_OS_HOME

_TENCENT_SILK_MAGIC = b"#!SILK_V3"
_TENCENT_SILK_PREFIX = b"\x02"
_TENCENT_SILK_HEADER = _TENCENT_SILK_PREFIX + _TENCENT_SILK_MAGIC
_STANDARD_SILK_TERMINATOR = b"\xff\xff"


class SilkCodecError(RuntimeError):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _engine_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_tool_root() -> Path:
    return V8_AGENT_OS_HOME / "tools" / "silk-v3"


def resolve_silk_tool_root() -> Path:
    configured = str(os.environ.get("V8_AGENT_OS_SILK_TOOL_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return _default_tool_root()


def _wrapper_path() -> Path:
    return _engine_root() / "scripts" / "runtime" / "silk_v3_encode_wrapper.py"


def _manifest_path(tool_root: Path) -> Path:
    return tool_root / "toolchain.json"


def _candidate_entrypoints(tool_root: Path) -> list[Path]:
    return [
        tool_root / "bin" / "silk_v3_encoder.cmd",
        tool_root / "bin" / "silk_v3_encoder.bat",
        tool_root / "bin" / "silk_v3_encoder.py",
        tool_root / "bin" / "silk_v3_encoder.exe",
    ]


def _candidate_decoder_entrypoints(tool_root: Path) -> list[Path]:
    return [
        tool_root / "bin" / "silk_v3_decoder.cmd",
        tool_root / "bin" / "silk_v3_decoder.bat",
        tool_root / "bin" / "silk_v3_decoder.py",
        tool_root / "bin" / "silk_v3_decoder.exe",
    ]


def silk_toolchain_status() -> dict[str, Any]:
    tool_root = resolve_silk_tool_root()
    wrapper = _wrapper_path()
    manifest_path = _manifest_path(tool_root)
    manifest: dict[str, object] = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except Exception:
            manifest = {}
    entrypoint = next((candidate for candidate in _candidate_entrypoints(tool_root) if candidate.exists()), None)
    decoder_entrypoint = next((candidate for candidate in _candidate_decoder_entrypoints(tool_root) if candidate.exists()), None)
    manifest_entrypoints = manifest.get("entrypoints")
    if isinstance(manifest_entrypoints, list):
        entrypoints = [str(item).strip() for item in manifest_entrypoints if str(item).strip()]
    else:
        entrypoints = []
    manifest_encoder_entrypoints = manifest.get("encoderEntrypoints")
    if isinstance(manifest_encoder_entrypoints, list):
        encoder_entrypoints = [str(item).strip() for item in manifest_encoder_entrypoints if str(item).strip()]
    else:
        encoder_entrypoints = []
    manifest_decoder_entrypoints = manifest.get("decoderEntrypoints")
    if isinstance(manifest_decoder_entrypoints, list):
        decoder_entrypoints = [str(item).strip() for item in manifest_decoder_entrypoints if str(item).strip()]
    else:
        decoder_entrypoints = []
    return {
        "toolRoot": str(tool_root),
        "wrapperPath": str(wrapper),
        "wrapperExists": wrapper.exists(),
        "toolRootExists": tool_root.exists(),
        "manifestPath": str(manifest_path),
        "manifestExists": manifest_path.exists(),
        "entrypointPath": str(entrypoint) if entrypoint else "",
        "decoderEntrypointPath": str(decoder_entrypoint) if decoder_entrypoint else "",
        "entrypoints": entrypoints,
        "encoderEntrypoints": encoder_entrypoints,
        "decoderEntrypoints": decoder_entrypoints,
        "entrypointExists": bool(entrypoint),
        "decoderEntrypointExists": bool(decoder_entrypoint),
        "available": bool(entrypoint and wrapper.exists() and str(manifest.get("status") or "ready").strip().lower() == "ready"),
        "version": str(manifest.get("version") or "").strip() or None,
        "source": str(manifest.get("source") or "").strip() or None,
        "platform": str(manifest.get("platform") or "").strip() or None,
        "packageName": str(manifest.get("packageName") or "").strip() or None,
        "packageVersion": str(manifest.get("packageVersion") or "").strip() or None,
        "status": str(manifest.get("status") or "").strip() or ("ready" if entrypoint and wrapper.exists() else "missing"),
        "lastError": str(manifest.get("lastError") or "").strip() or None,
    }


def probe_audio_duration_ms(
    source_audio_path: str | Path,
    *,
    ffprobe_executable: str | None = None,
) -> int | None:
    source = Path(str(source_audio_path)).expanduser()
    if not source.exists():
        return None
    ffprobe_path = str(ffprobe_executable or shutil.which("ffprobe") or "").strip()
    if not ffprobe_path:
        return None
    command = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(source),
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    raw = str(result.stdout or "").strip()
    if not raw:
        return None
    try:
        duration_ms = int(round(float(raw) * 1000.0))
    except Exception:
        return None
    return max(duration_ms, 0)


def validate_and_normalize_tencent_silk(
    *,
    file_path: str | Path,
) -> dict[str, Any]:
    target = Path(str(file_path)).expanduser()
    if not target.exists():
        raise SilkCodecError("silk_missing", f"Silk 文件不存在：{target}")

    raw = target.read_bytes()
    if not raw:
        raise SilkCodecError("silk_empty", f"Silk 文件为空：{target}")

    header_normalized = False
    stripped_terminator = False
    normalized = raw

    if normalized.startswith(_TENCENT_SILK_HEADER):
        pass
    elif normalized.startswith(_TENCENT_SILK_MAGIC):
        normalized = _TENCENT_SILK_PREFIX + normalized
        header_normalized = True
    else:
        raise SilkCodecError(
            "silk_invalid_header",
            "Silk 文件缺少 Tencent/WeChat 所需的 0x02 + #!SILK_V3 头标记。",
        )

    if normalized.endswith(_STANDARD_SILK_TERMINATOR):
        normalized = normalized[: -len(_STANDARD_SILK_TERMINATOR)]
        stripped_terminator = True

    if len(normalized) <= len(_TENCENT_SILK_HEADER):
        raise SilkCodecError("silk_invalid_payload", "Silk 文件缺少有效音频载荷。")

    if normalized != raw:
        target.write_bytes(normalized)

    return {
        "filePath": str(target),
        "header": "0x02+#!SILK_V3",
        "headerNormalized": header_normalized,
        "strippedTrailingSilkTerminator": stripped_terminator,
        "payloadBytes": len(normalized) - len(_TENCENT_SILK_HEADER),
        "totalBytes": len(normalized),
    }


def encode_audio_to_silk(
    *,
    source_audio_path: str | Path,
    output_path: str | Path,
    sample_rate: int,
    bitrate: int,
    ffmpeg_executable: str | None = None,
) -> dict[str, str | int]:
    source = Path(str(source_audio_path)).expanduser()
    if not source.exists():
        raise SilkCodecError("source_missing", f"源音频不存在：{source}")

    ffmpeg_path = str(ffmpeg_executable or shutil.which("ffmpeg") or "").strip()
    if not ffmpeg_path:
        raise SilkCodecError("ffmpeg_missing", "当前系统未安装 ffmpeg，无法准备 Silk 编码输入。")

    wrapper = _wrapper_path()
    if not wrapper.exists():
        raise SilkCodecError("wrapper_missing", f"Silk 封装脚本不存在：{wrapper}")

    output = Path(str(output_path)).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="v8-agent-os-silk-") as temp_dir:
        pcm_path = Path(temp_dir) / "input.wav"
        ffmpeg_command = [
            ffmpeg_path,
            "-y",
            "-i",
            str(source),
            "-ar",
            str(sample_rate),
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(pcm_path),
        ]
        ffmpeg_result = subprocess.run(
            ffmpeg_command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if ffmpeg_result.returncode != 0 or not pcm_path.exists():
            stderr_text = str(ffmpeg_result.stderr or "").strip()
            raise SilkCodecError(
                "ffmpeg_failed",
                stderr_text or "ffmpeg 未能生成 Silk 编码所需的 PCM WAV 输入。",
            )

        wrapper_command = [
            sys.executable,
            str(wrapper),
            "--input",
            str(pcm_path),
            "--output",
            str(output),
            "--sample-rate",
            str(sample_rate),
            "--bitrate",
            str(bitrate),
            "--tool-root",
            str(resolve_silk_tool_root()),
        ]
        wrapper_result = subprocess.run(
            wrapper_command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if wrapper_result.returncode != 0 or not output.exists():
            stderr_text = str(wrapper_result.stderr or "").strip()
            lowered = stderr_text.lower()
            reason = "silk_encoder_failed"
            if "missing" in lowered or "not found" in lowered or "不存在" in stderr_text:
                reason = "silk_toolchain_missing"
            raise SilkCodecError(
                reason,
                stderr_text or "Silk 包装层未能生成目标文件。",
            )

    return {
        "filePath": str(output),
        "toolRoot": str(resolve_silk_tool_root()),
        "sampleRate": int(sample_rate),
        "bitrate": int(bitrate),
    }
