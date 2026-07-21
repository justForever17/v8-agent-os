from pathlib import Path

from api import chat_realtime_routes


def test_only_explicit_voice_sources_are_normalized() -> None:
    assert chat_realtime_routes._VOICE_UPLOAD_SOURCE_KINDS == {
        "web_voice",
        "phone_voice",
        "desktop_pet_voice",
    }
    assert "web_upload" not in chat_realtime_routes._VOICE_UPLOAD_SOURCE_KINDS
    assert "phone_upload" not in chat_realtime_routes._VOICE_UPLOAD_SOURCE_KINDS
    assert "desktop_pet_upload" not in chat_realtime_routes._VOICE_UPLOAD_SOURCE_KINDS


def test_voice_filename_is_normalized_to_mp3() -> None:
    assert chat_realtime_routes._normalized_voice_filename("desktop-pet-voice.webm") == "desktop-pet-voice.mp3"
    assert chat_realtime_routes._normalized_voice_filename("phone memo.m4a") == "phone memo.mp3"


def test_voice_source_requires_audio_mime_or_extension() -> None:
    assert chat_realtime_routes._is_voice_upload("recording.bin", "audio/webm;codecs=opus") is True
    assert chat_realtime_routes._is_voice_upload("recording.m4a", "application/octet-stream") is True
    assert chat_realtime_routes._is_voice_upload("attachment.png", "image/png") is False


def test_transcode_voice_upload_uses_compatible_mp3_contract(tmp_path, monkeypatch) -> None:
    source = tmp_path / "voice.webm"
    target = tmp_path / "voice.mp3"
    source.write_bytes(b"webm")
    captured: dict[str, object] = {}

    monkeypatch.setattr(chat_realtime_routes.shutil, "which", lambda name: "ffmpeg.exe" if name == "ffmpeg" else None)

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        Path(command[-1]).write_bytes(b"mp3")
        return type("Completed", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(chat_realtime_routes.subprocess, "run", fake_run)
    chat_realtime_routes._transcode_voice_upload_to_mp3(source, target)

    command = captured["command"]
    assert "-codec:a" in command
    assert "libmp3lame" in command
    assert command[command.index("-ac") + 1] == "1"
    assert command[command.index("-ar") + 1] == "16000"
    assert command[command.index("-b:a") + 1] == "64k"
    assert target.read_bytes() == b"mp3"
