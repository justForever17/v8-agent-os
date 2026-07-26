from __future__ import annotations

from types import SimpleNamespace

from core import dependency_registry


def _completed(first_line: str, *, returncode: int = 0) -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=f"{first_line}\n", stderr="")


def test_ffmpeg_dependency_requires_paired_release_7_or_newer(monkeypatch):
    paths = {
        "ffmpeg": "C:/ffmpeg/bin/ffmpeg.exe",
        "ffprobe": "C:/ffmpeg/bin/ffprobe.exe",
    }
    monkeypatch.setattr(dependency_registry.shutil, "which", lambda name: paths.get(name))
    monkeypatch.setattr(
        dependency_registry.subprocess,
        "run",
        lambda command, **_kwargs: _completed(f"{command[0].split('/')[-1].removesuffix('.exe')} version 8.1.2-full_build"),
    )

    status = dependency_registry._detect_ffmpeg_pair()

    assert status["detected"] is True
    assert status["installed"] is True
    assert status["meetsMinimumVersion"] is True
    assert status["pairedInstallation"] is True
    assert status["minimumVersion"] == "7.0"
    assert status["version"] == "8.1.2"
    assert status["ffprobeVersion"] == "8.1.2"


def test_ffmpeg_dependency_rejects_old_or_mixed_installation(monkeypatch):
    paths = {
        "ffmpeg": "D:/ImageMagick/ffmpeg.exe",
        "ffprobe": "C:/ffmpeg/bin/ffprobe.exe",
    }
    monkeypatch.setattr(dependency_registry.shutil, "which", lambda name: paths.get(name))

    def fake_run(command, **_kwargs):
        binary = command[0].split("/")[-1].removesuffix(".exe")
        version = "4.2.3" if binary == "ffmpeg" else "8.1.2"
        return _completed(f"{binary} version {version}")

    monkeypatch.setattr(dependency_registry.subprocess, "run", fake_run)

    status = dependency_registry._detect_ffmpeg_pair()

    assert status["detected"] is False
    assert status["installed"] is True
    assert status["meetsMinimumVersion"] is False
    assert status["pairedInstallation"] is False


def test_dependency_registry_exposes_ffmpeg_minimum_version(monkeypatch):
    monkeypatch.setattr(
        dependency_registry,
        "_detect_ffmpeg_pair",
        lambda: {
            "detected": True,
            "minimumVersion": "7.0",
            "detail": "ok",
        },
    )
    monkeypatch.setattr(dependency_registry, "detect_desktop_tools_readiness", lambda: {})

    ffmpeg = next(item for item in dependency_registry.build_dependency_status() if item["id"] == "ffmpeg")

    assert ffmpeg["minimumVersion"] == "7.0"
    assert ffmpeg["detection"]["detected"] is True
