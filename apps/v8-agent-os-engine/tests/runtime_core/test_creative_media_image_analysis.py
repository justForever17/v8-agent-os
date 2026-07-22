from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from runtimes.creative_media import image_analysis


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
