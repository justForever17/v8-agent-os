from __future__ import annotations

from typing import Any


_CANONICAL_DELIVERY_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "openclaw-weixin": {
        "profileId": "weixin_audio_attachment",
        "mode": "audio_attachment",
        "container": "file_attachment",
        "codec": "mp3",
        "extension": ".mp3",
        "mimeType": "audio/mpeg",
        "asVoice": False,
        "requirementsKnown": "complete",
        "verificationSource": "mixed",
        "encodePresetId": "audio_attachment_mp3",
        "fallbackMode": "audio_attachment",
        "notes": "Weixin is intentionally downgraded to MP3 attachment delivery until native voice handling is stable enough for the mainline.",
    },
    "qq": {
        "profileId": "qq_native_voice",
        "mode": "native_voice",
        "container": "tencent_silk_v3",
        "codec": "silk_v3",
        "extension": ".silk",
        "mimeType": "audio/silk",
        "asVoice": True,
        "channels": 1,
        "channelLayout": "mono",
        "sampleFormat": "s16le",
        "sampleRate": 16000,
        "bitsPerSample": 16,
        "frameDurationMs": 20,
        "maxDurationSeconds": 60,
        "header": "0x02+#!SILK_V3",
        "stripTrailingSilkTerminator": True,
        "encodeType": 6,
        "requiresPlaytime": True,
        "requirementsKnown": "complete",
        "verificationSource": "mixed",
        "requiresExternalEncoder": True,
        "toolchain": "silk_v3",
        "encoderCommand": "v8_agent_os_silk_wrapper",
        "encodePresetId": "tencent_silk_16k_vbr",
        "fallbackMode": "audio_attachment",
        "fallbackProfileId": "slack_audio_attachment",
        "notes": "QQ voice shares the same Tencent Silk V3 wire contract as Weixin voice bubbles.",
    },
    "feishu": {
        "profileId": "feishu_voice_message",
        "mode": "native_voice",
        "container": "ogg",
        "codec": "opus",
        "extension": ".ogg",
        "mimeType": "audio/ogg; codecs=opus",
        "asVoice": True,
        "requirementsKnown": "partial",
        "verificationSource": "official_doc",
        "encodePresetId": "ogg_opus_voice_default",
        "fallbackMode": "audio_attachment",
        "fallbackProfileId": "slack_audio_attachment",
        "notes": "Feishu voice delivery is restored once runtime-generated media is staged into OpenClaw 4.8 allowed outbound roots.",
    },
    "whatsapp": {
        "profileId": "whatsapp_voice_note",
        "mode": "native_voice",
        "container": "ogg",
        "codec": "opus",
        "extension": ".ogg",
        "mimeType": "audio/ogg; codecs=opus",
        "asVoice": True,
        "requirementsKnown": "partial",
        "verificationSource": "official_doc",
        "encodePresetId": "ogg_opus_voice_default",
        "fallbackMode": "audio_attachment",
        "fallbackProfileId": "slack_audio_attachment",
        "notes": "Canonical hard requirement is OGG/Opus voice delivery; sample rate and bitrate remain platform-unspecified.",
    },
    "telegram": {
        "profileId": "telegram_voice_note",
        "mode": "native_voice",
        "container": "ogg",
        "codec": "opus",
        "extension": ".ogg",
        "mimeType": "audio/ogg; codecs=opus",
        "asVoice": True,
        "requirementsKnown": "partial",
        "verificationSource": "official_doc",
        "encodePresetId": "ogg_opus_voice_default",
        "fallbackMode": "audio_attachment",
        "fallbackProfileId": "slack_audio_attachment",
        "notes": "Telegram hard-requires voice-note semantics plus OGG/Opus packaging.",
    },
    "matrix": {
        "profileId": "matrix_voice_note",
        "mode": "native_voice",
        "container": "ogg",
        "codec": "opus",
        "extension": ".ogg",
        "mimeType": "audio/ogg; codecs=opus",
        "asVoice": True,
        "requirementsKnown": "partial",
        "verificationSource": "official_doc",
        "encodePresetId": "ogg_opus_voice_default",
        "fallbackMode": "audio_attachment",
        "fallbackProfileId": "slack_audio_attachment",
        "notes": "OpenClaw 官方 TTS 文档将 Matrix 列为原生 voice message 渠道，Engine 默认按 OGG/Opus 收口。",
    },
    "discord": {
        "profileId": "discord_voice_message",
        "mode": "native_voice",
        "container": "ogg",
        "codec": "opus",
        "extension": ".ogg",
        "mimeType": "audio/ogg; codecs=opus",
        "asVoice": True,
        "requiresWaveformMetadata": True,
        "requiresTextOmitted": True,
        "requirementsKnown": "partial",
        "verificationSource": "official_doc",
        "encodePresetId": "ogg_opus_voice_default",
        "fallbackMode": "audio_attachment",
        "fallbackProfileId": "slack_audio_attachment",
        "notes": "Discord voice messages require OGG/Opus plus gateway-generated waveform metadata.",
    },
    "slack": {
        "profileId": "slack_audio_attachment",
        "mode": "audio_attachment",
        "container": "mp3",
        "codec": "mp3",
        "extension": ".mp3",
        "mimeType": "audio/mpeg",
        "asVoice": False,
        "requirementsKnown": "complete",
        "verificationSource": "official_doc",
        "encodePresetId": "audio_attachment_mp3",
        "fallbackMode": "audio_attachment",
        "notes": "Slack currently receives audio as a standard file attachment.",
    },
}

_ENGINE_ENCODE_PRESETS: dict[str, dict[str, Any]] = {
    "tencent_silk_16k_vbr": {
        "sampleRate": 16000,
        "channels": 1,
        "bitrate": 18000,
        "sampleFormat": "s16le",
        "bitsPerSample": 16,
        "frameDurationMs": 20,
        "vbr": True,
    },
    "ogg_opus_voice_default": {
        "sampleRate": 48000,
        "channels": 1,
        "bitrate": 24000,
        "sampleFormat": "s16le",
        "bitsPerSample": 16,
    },
    "audio_attachment_mp3": {
        "sampleRate": 24000,
        "channels": 1,
        "bitrate": 64000,
        "sampleFormat": "s16le",
        "bitsPerSample": 16,
    },
}

_VOICE_PROFILE_ALIASES: dict[str, str] = {
    "openclaw-weixin": "openclaw-weixin",
    "weixin": "openclaw-weixin",
    "wechat": "openclaw-weixin",
    "qq": "qq",
    "feishu": "feishu",
    "lark": "feishu",
    "@openclaw/feishu": "feishu",
    "openclaw-lark": "feishu",
    "@larksuite/openclaw-lark": "feishu",
    "matrix": "matrix",
    "@openclaw/matrix": "matrix",
    "whatsapp": "whatsapp",
    "telegram": "telegram",
    "discord": "discord",
    "slack": "slack",
}

_DEFAULT_PROFILE_ID = "slack"
_TEXT_ONLY_MODES = {"", "none", "disabled", "text_only", "unsupported"}


def resolve_voice_delivery_profile(channel_type: str | None) -> dict[str, Any]:
    normalized = str(channel_type or "").strip().lower()
    resolved_key = _VOICE_PROFILE_ALIASES.get(normalized, _DEFAULT_PROFILE_ID)
    profile = dict(
        _CANONICAL_DELIVERY_REQUIREMENTS.get(
            resolved_key,
            _CANONICAL_DELIVERY_REQUIREMENTS[_DEFAULT_PROFILE_ID],
        )
    )
    encode_preset_id = str(profile.get("encodePresetId") or "").strip()
    profile["deliveryRequirements"] = dict(profile)
    profile["encodePreset"] = dict(_ENGINE_ENCODE_PRESETS.get(encode_preset_id, {}))
    return profile


def resolve_voice_encode_preset(profile: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(profile or {})
    encode_preset = payload.get("encodePreset")
    if isinstance(encode_preset, dict):
        return dict(encode_preset)
    encode_preset_id = str(payload.get("encodePresetId") or "").strip()
    return dict(_ENGINE_ENCODE_PRESETS.get(encode_preset_id, {}))


def voice_profile_requires_external_encoder(profile: dict[str, Any] | None) -> bool:
    return bool((profile or {}).get("requiresExternalEncoder"))


def voice_profile_allows_audio_delivery(profile: dict[str, Any] | None) -> bool:
    payload = dict(profile or {})
    mode = str(payload.get("mode") or "").strip().lower()
    return mode not in _TEXT_ONLY_MODES
