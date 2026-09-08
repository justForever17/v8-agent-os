from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).with_name("run_vision_images_live_audit.py")
SPEC = importlib.util.spec_from_file_location("vision_images_live_audit", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def test_without_live_never_reads_config_or_creates_state(tmp_path, monkeypatch):
    target = tmp_path / "untouched"
    monkeypatch.setattr(audit, "run_audit", lambda *_: (_ for _ in ()).throw(AssertionError("provider called")))
    assert audit.main(["--config", str(tmp_path / "missing.json"), "--isolated-root", str(target)]) == 2
    assert not target.exists()


def test_oracle_rejects_order_loss_dropped_middle_and_duplicate_dedup():
    answer = {"images": [
        {"index": 1, "blue_position": "left", "orange_count": 1},
        {"index": 2, "blue_position": "middle", "orange_count": 2},
        {"index": 3, "blue_position": "right", "orange_count": 3},
    ]}
    assert audit.answer_matches(answer, [0, 1, 2])
    assert not audit.answer_matches(answer, [2, 1, 0])
    assert not audit.answer_matches(answer, [2, 0, 2])
    assert not audit.answer_matches({"images": [answer["images"][0], answer["images"][2]]}, [0, 1, 2])


def test_parse_does_not_repair_truncated_json():
    import pytest
    with pytest.raises(ValueError):
        audit.parse_answer('--- Vision Analysis Complete ---\n{"images":[{"index":1}')


def test_persisted_observation_must_match_actual_payload_and_answer():
    import json
    from copy import deepcopy
    answer = {"images": [{"index": 1, "blue_position": "left", "orange_count": 1}]}
    record = {"metadata": {"images": [{"index": 1, "inputSha256": "actual-image"}]},
              "raw_body_text": json.dumps(answer)}
    calls = [{"imageCount": 1, "sentHashes": ["actual-image"]}]
    assert audit.observation_matches(record, calls, [0])
    wrong = deepcopy(record)
    wrong["metadata"]["images"][0]["inputSha256"] = "other-image"
    assert not audit.observation_matches(wrong, calls, [0])
    wrong = deepcopy(record)
    wrong["raw_body_text"] = "已完成"
    assert not audit.observation_matches(wrong, calls, [0])
    assert not audit.observation_matches({}, calls, [0])
