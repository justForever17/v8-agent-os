from __future__ import annotations

import hashlib
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from runtimes.creative_media import image_analysis


@pytest.fixture(autouse=True)
def _managed_onnx_worker_cleanup():
    image_analysis._shutdown_onnx_worker()
    yield
    image_analysis._shutdown_onnx_worker()


def _fake_probe_target(root: Path) -> Path:
    target = root / "python"
    target.mkdir(parents=True)
    (target / "numpy.py").write_text("__version__ = 'target-test'\n", encoding="utf-8")
    (target / "onnxruntime.py").write_text(
        "class InferenceSession:\n"
        "    def __init__(self, model, providers):\n"
        "        self.providers = providers\n"
        "    def get_providers(self):\n"
        "        return self.providers\n",
        encoding="utf-8",
    )
    return target


def _transparent_subject(path: Path, *, inset: int = 20) -> None:
    image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((inset, inset, 99 - inset, 99 - inset), fill=(40, 90, 180, 255))
    image.save(path)


def test_real_alpha_subject_metrics_are_deterministic(tmp_path):
    source = tmp_path / "subject.png"
    _transparent_subject(source)

    report = image_analysis.analyze_image(source, allow_onnx=False)

    assert report["status"] == "analyzed"
    assert report["alpha"]["status"] == "true_alpha"
    assert report["subject"]["maskSource"] == "alpha"
    assert report["subject"]["areaRatio"] == 0.36
    assert report["subject"]["bbox"] == {"x": 0.2, "y": 0.2, "width": 0.6, "height": 0.6}
    assert report["subject"]["touchesEdges"] == []
    assert report["subject"]["componentCount"] == 1


def test_single_transparent_pixel_is_not_treated_as_real_transparency(tmp_path):
    source = tmp_path / "false-alpha.png"
    image = Image.new("RGBA", (100, 100), (255, 255, 255, 255))
    ImageDraw.Draw(image).rectangle((25, 20, 74, 79), fill=(0, 0, 0, 255))
    image.putpixel((0, 0), (255, 255, 255, 0))
    image.save(source)

    report = image_analysis.analyze_image(source, allow_onnx=False)

    assert report["alpha"]["status"] == "false_alpha"
    assert report["subject"]["maskSource"] == "border_connected"
    assert 0.29 <= report["subject"]["areaRatio"] <= 0.31


def test_complex_background_requires_governed_feature_pack(tmp_path, monkeypatch):
    source = tmp_path / "complex.jpg"
    image = Image.new("RGB", (96, 96))
    pixels = image.load()
    for y in range(96):
        for x in range(96):
            pixels[x, y] = ((x * 17 + y * 23) % 255, (x * 7 + y * 31) % 255, (x * 29 + y * 11) % 255)
    image.save(source)
    monkeypatch.setattr(
        image_analysis,
        "_onnx_subject_mask",
        lambda _image: (None, 0.0, "feature_pack_not_installed"),
    )

    report = image_analysis.analyze_image(source)

    assert report["status"] == "review_required"
    assert report["requiredFeaturePackId"] == "creative_media_image_analysis"
    assert report["subject"]["maskSource"] == "none"


def test_onnx_probe_uses_only_target_modules_and_keeps_parent_ambient_module(tmp_path, monkeypatch):
    target = _fake_probe_target(tmp_path)
    model = tmp_path / "model.onnx"
    model.write_bytes(b"receipt-governed-model")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    ambient = types.ModuleType("onnxruntime")
    ambient.__file__ = str(tmp_path / "ambient" / "onnxruntime.py")
    monkeypatch.setitem(sys.modules, "onnxruntime", ambient)
    monkeypatch.setattr(image_analysis, "_expected_model_digest", lambda: digest)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-child")
    monkeypatch.setenv("DATABASE_URL", "must-not-reach-child")

    result = image_analysis._probe_onnx_runtime(model, target)

    assert result == {
        "ok": True,
        "error": None,
        "isolated": True,
        "moduleOriginsVerified": True,
        "modelShaVerified": True,
        "cpuSessionLoaded": True,
    }
    assert sys.modules["onnxruntime"] is ambient
    child_environment = {
        key.upper(): value for key, value in image_analysis._onnx_child_environment().items()
    }
    assert "OPENAI_API_KEY" not in child_environment
    assert "DATABASE_URL" not in child_environment
    assert "LD_LIBRARY_PATH" not in child_environment
    assert "PATH" not in child_environment


def test_onnx_worker_reuses_session_and_serializes_concurrent_requests(tmp_path, monkeypatch):
    target = _fake_probe_target(tmp_path)
    model = tmp_path / "model.onnx"
    model.write_bytes(b"receipt-governed-model")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    monkeypatch.setattr(image_analysis, "_expected_model_digest", lambda: digest)

    first = image_analysis._probe_onnx_runtime(model, target)
    first_pid = image_analysis._ONNX_WORKER_MANAGER._worker.pid
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _index: image_analysis._probe_onnx_runtime(model, target), range(12)))

    assert first["cpuSessionLoaded"] is True
    assert all(result == first for result in results)
    assert first_pid is not None
    assert image_analysis._ONNX_WORKER_MANAGER._worker.pid == first_pid


def test_onnx_worker_recovers_once_after_process_death(tmp_path, monkeypatch):
    target = _fake_probe_target(tmp_path)
    model = tmp_path / "model.onnx"
    model.write_bytes(b"receipt-governed-model")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    monkeypatch.setattr(image_analysis, "_expected_model_digest", lambda: digest)

    image_analysis._probe_onnx_runtime(model, target)
    worker = image_analysis._ONNX_WORKER_MANAGER._worker
    first_pid = worker.pid
    old_ipc_root = worker._ipc_root
    worker._output_path.write_bytes(b"stale-output-must-not-survive")
    worker.process.kill()
    worker.process.wait(timeout=2)

    recovered = image_analysis._probe_onnx_runtime(model, target)

    assert recovered["cpuSessionLoaded"] is True
    assert first_pid is not None
    assert image_analysis._ONNX_WORKER_MANAGER._worker.pid not in {None, first_pid}
    assert image_analysis._ONNX_WORKER_MANAGER._worker._ipc_root != old_ipc_root
    assert not old_ipc_root.exists()
    assert not image_analysis._ONNX_WORKER_MANAGER._worker._output_path.exists()


def test_onnx_worker_rebuilds_when_model_identity_changes(tmp_path, monkeypatch):
    target = _fake_probe_target(tmp_path / "first-target")
    second_target = _fake_probe_target(tmp_path / "second-target")
    first_model = tmp_path / "first.onnx"
    second_model = tmp_path / "second.onnx"
    first_model.write_bytes(b"first-model")
    second_model.write_bytes(b"second-model")
    expected = {"digest": hashlib.sha256(first_model.read_bytes()).hexdigest()}
    monkeypatch.setattr(image_analysis, "_expected_model_digest", lambda: expected["digest"])

    image_analysis._probe_onnx_runtime(first_model, target)
    first_worker = image_analysis._ONNX_WORKER_MANAGER._worker
    expected["digest"] = hashlib.sha256(second_model.read_bytes()).hexdigest()
    image_analysis._probe_onnx_runtime(second_model, target)
    second_worker = image_analysis._ONNX_WORKER_MANAGER._worker
    image_analysis._probe_onnx_runtime(second_model, second_target)
    third_worker = image_analysis._ONNX_WORKER_MANAGER._worker

    assert first_worker is not second_worker
    assert second_worker is not third_worker
    assert first_worker.process is None
    assert second_worker.process is None
    assert third_worker.pid is not None


def test_onnx_worker_idle_timeout_reclaims_process(tmp_path, monkeypatch):
    target = _fake_probe_target(tmp_path)
    model = tmp_path / "model.onnx"
    model.write_bytes(b"receipt-governed-model")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    monkeypatch.setattr(image_analysis, "_expected_model_digest", lambda: digest)
    manager = image_analysis._OnnxWorkerManager()
    monkeypatch.setattr(image_analysis, "_ONNX_WORKER_MANAGER", manager)
    manager._idle_seconds = 0.1
    try:
        image_analysis._probe_onnx_runtime(model, target)
        process = manager._worker.process
        deadline = time.monotonic() + 2
        while manager._worker is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert manager._worker is None
        assert process.poll() is not None
    finally:
        manager.close()


def test_onnx_worker_retry_is_capped_at_one_restart(monkeypatch):
    starts: list[object] = []

    class DeadWorker:
        def __init__(self, identity):
            self.identity = identity
            starts.append(identity)

        @property
        def pid(self):
            return 123

        def request(self, _action, _input_bytes=None):
            raise image_analysis._OnnxWorkerFailure(
                "onnx_worker_exited",
                retryable=True,
                discard=True,
            )

        def stop(self):
            return None

    monkeypatch.setattr(image_analysis, "_ManagedOnnxWorker", DeadWorker)
    manager = image_analysis._OnnxWorkerManager()
    identity = ("target", "model", 1, 1, "0" * 64)
    try:
        with pytest.raises(RuntimeError, match="onnx_worker_exited"):
            manager.request(identity, "probe")
    finally:
        manager.close()

    assert len(starts) == 2


def test_onnx_probe_does_not_fall_back_to_parent_ambient_numpy(tmp_path, monkeypatch):
    target = tmp_path / "python"
    target.mkdir()
    (target / "onnxruntime.py").write_text(
        "class InferenceSession:\n"
        "    def __init__(self, model, providers): pass\n"
        "    def get_providers(self): return ['CPUExecutionProvider']\n",
        encoding="utf-8",
    )
    model = tmp_path / "model.onnx"
    model.write_bytes(b"receipt-governed-model")
    digest = hashlib.sha256(model.read_bytes()).hexdigest()
    monkeypatch.setattr(image_analysis, "_expected_model_digest", lambda: digest)

    with pytest.raises(RuntimeError, match="runtime_import_failed"):
        image_analysis._probe_onnx_runtime(model, target)


def test_onnx_subject_mask_delegates_inference_to_governed_target(tmp_path, monkeypatch):
    model = tmp_path / "model.onnx"
    model.write_bytes(b"model")
    target = tmp_path / "python"
    target.mkdir()
    observed: dict[str, object] = {}

    monkeypatch.setattr(image_analysis, "resolve_feature_pack_asset", lambda *_args: model)
    monkeypatch.setattr(image_analysis, "_receipt_governed_onnx_target", lambda: (target, None))

    def isolated_inference(image, model_path, target_dir):
        observed.update({"size": image.size, "model": model_path, "target": target_dir})
        return np.tile(np.linspace(0.0, 1.0, 1024, dtype=np.float32), (1024, 1))

    monkeypatch.setattr(image_analysis, "_run_isolated_onnx_inference", isolated_inference)
    mask, confidence, error = image_analysis._onnx_subject_mask(Image.new("RGB", (64, 48), "white"))

    assert error is None
    assert mask is not None and mask.shape == (48, 64)
    assert confidence > 0
    assert observed == {"size": (64, 48), "model": model, "target": target}


def test_onnx_subject_mask_does_not_expose_local_paths(tmp_path, monkeypatch):
    model = tmp_path / "private" / "model.onnx"
    target = tmp_path / "python"
    target.mkdir()
    monkeypatch.setattr(image_analysis, "resolve_feature_pack_asset", lambda *_args: model)
    monkeypatch.setattr(image_analysis, "_receipt_governed_onnx_target", lambda: (target, None))

    _mask, _confidence, error = image_analysis._onnx_subject_mask(Image.new("RGB", (32, 32), "white"))

    assert error == "onnx_model_unavailable"
    assert str(tmp_path) not in error


def test_comparison_reports_scale_position_and_clipping_changes(tmp_path):
    reference_path = tmp_path / "reference.png"
    candidate_path = tmp_path / "candidate.png"
    _transparent_subject(reference_path, inset=20)
    image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((0, 10, 69, 89), fill=(40, 90, 180, 255))
    image.save(candidate_path)

    result = image_analysis.compare_images(reference_path, candidate_path, allow_onnx=False)
    comparison = result["comparison"]

    assert comparison["areaRatioDelta"] == 0.2
    assert comparison["centerShift"] > 0.1
    assert comparison["clippingChange"] == {"before": [], "after": ["left"]}
    assert 0 < comparison["bboxIoU"] < 1


def test_quality_profile_marks_mask_based_alpha_repair_as_repairable(tmp_path):
    source = tmp_path / "opaque.png"
    image = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(image).ellipse((20, 20, 79, 79), fill="navy")
    image.save(source)

    report = image_analysis.analyze_image(source, allow_onnx=False)
    evaluation = image_analysis.evaluate_quality_profile(report, "transparent_cutout")

    assert evaluation["status"] == "repairable"
    assert evaluation["violations"] == ["real_alpha_required"]


def test_reference_without_subject_mask_requires_review_instead_of_false_drift():
    reference = {
        "status": "review_required",
        "sourceFingerprint": "reference",
        "requiredFeaturePackId": "creative_media_image_analysis",
        "alpha": {"coverageRatio": 0.0},
        "subject": {"areaRatio": 0.0, "bbox": None, "centroid": None, "margins": None},
    }
    candidate = {
        "status": "analyzed",
        "sourceFingerprint": "candidate",
        "alpha": {"status": "opaque", "coverageRatio": 0.0},
        "subject": {
            "areaRatio": 0.4,
            "bbox": {"x": 0.2, "y": 0.2, "width": 0.6, "height": 0.6},
            "centroid": {"x": 0.5, "y": 0.5},
            "margins": {"top": 0.2, "right": 0.2, "bottom": 0.2, "left": 0.2},
            "touchesEdges": [],
            "componentCount": 1,
            "maskConfidence": 0.9,
        },
    }

    comparison = image_analysis.compare_image_analyses(reference, candidate)
    evaluation = image_analysis.evaluate_quality_profile(candidate, "character_reference", comparison=comparison)

    assert evaluation == {
        "status": "review_required",
        "profile": "character_reference",
        "violations": ["reference_subject_mask_unavailable"],
        "warnings": [],
        "requiredFeaturePackId": "creative_media_image_analysis",
    }


def test_transparent_derivative_is_non_destructive(tmp_path):
    source = tmp_path / "opaque.png"
    target = tmp_path / "derived.png"
    image = Image.new("RGB", (100, 100), "white")
    ImageDraw.Draw(image).rectangle((20, 20, 79, 79), fill="black")
    image.save(source)

    result = image_analysis.create_transparent_derivative(source, target)

    assert source.exists()
    assert target.exists()
    assert result["report"]["alpha"]["status"] == "true_alpha"
    assert Image.open(target).mode == "RGBA"
