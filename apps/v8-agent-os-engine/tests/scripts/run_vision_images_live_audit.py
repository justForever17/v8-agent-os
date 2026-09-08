"""Paid, isolated audit of the real vision tool: ordered and repeated images.

Only the configuration read is adapted to use an existing ModelHub in memory.
Tools, access checks, image preparation, provider and artifact writes are real;
all writes go to a new isolated state root. No source credentials are copied.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import time
from unittest.mock import patch


def parse_answer(text: str) -> dict:
    # Parse one complete JSON answer; never repair incomplete model output.
    text = text.split("--- Vision Analysis Complete ---", 1)[-1]
    start = text.find('{"images"')
    if start < 0:
        start = text.find("{\n")
    if start < 0:
        raise ValueError("missing_json_answer")
    answer, _ = json.JSONDecoder().raw_decode(text[start:])
    return answer


def answer_matches(answer: dict, order: list[int]) -> bool:
    rows = answer.get("images")
    return isinstance(rows, list) and len(rows) == len(order) and all(
        isinstance(row, dict)
        and row.get("index") == i + 1
        and row.get("blue_position") == ("left", "middle", "right")[source]
        and row.get("orange_count") == source + 1
        for i, (row, source) in enumerate(zip(rows, order))
    )


def observation_matches(record: dict, calls: list[dict], order: list[int]) -> bool:
    if len(calls) != 1 or calls[0].get("imageCount") != len(order):
        return False
    sources = (record.get("metadata") or {}).get("images") or []
    if [source.get("inputSha256") for source in sources] != calls[0]["sentHashes"]:
        return False
    if [source.get("index") for source in sources] != list(range(1, len(order) + 1)):
        return False
    try:
        return answer_matches(parse_answer(record.get("raw_body_text") or ""), order)
    except (ValueError, TypeError):
        return False


def generate_images(workspace: Path) -> list[Path]:
    from PIL import Image, ImageDraw

    paths = []
    for n, x in enumerate((80, 250, 420)):
        picture = Image.new("RGB", (500, 300), "white")
        draw = ImageDraw.Draw(picture)
        draw.rectangle((10, 10, 489, 289), outline="#364152", width=3)
        draw.ellipse((x - 30, 90, x + 30, 150), fill="#155eef")
        for tile in range(n + 1):
            draw.rectangle((100 + tile * 90, 210, 150 + tile * 90, 250), fill="#ff8c00")
        path = workspace / f"frame-{n}.png"
        picture.save(path)
        paths.append(path)
    return paths


def run_audit(isolated: Path, source_models: dict) -> dict:
    from core.storage import storage

    # Keep config access read-only; do not migrate/save the source profile.
    with patch.object(storage, "get_models_config", side_effect=lambda: deepcopy(source_models)):
        from core.database import db
        from core.model_control_plane import model_control_plane
        from core.observability_db import observability_db
        from core.tools.vision_media_analyzer import vision_media_analyzer, llm_factory
        from erc.runtime_context import bind_runtime_context

        resolution = model_control_plane.resolve_model_for_role("vision")
        identity = {
            "providerId": resolution.get("resolvedProviderId"),
            "modelId": resolution.get("resolvedModelId"),
        }
        if not all(identity.values()):
            raise ValueError("configured_vision_model_missing")
        workspace = isolated / "fixture-workspace"
        workspace.mkdir()
        paths = generate_images(workspace)
        original_factory = llm_factory.create_for_role
        cells = []
        prompt = (
            '请联合查看这些图片，严格按输入编号回答，仅输出一个JSON对象：'
            '{"images":[{"index":1,"blue_position":"left|middle|right",'
            '"orange_count":0}],"changes":"中文说明可见变化与不能断定的事"}。'
            '每张图都要记录：蓝色圆形在画面左、中或右的位置，橙色矩形的准确数量。'
            '不要根据标签推测；标签仅代表给定顺序。只能描述可见状态，不能证明操作真的执行过。'
        )
        for case, order in (("forward", [0, 1, 2]), ("reverse", [2, 1, 0]), ("repeat", [2, 0, 2])):
            session = f"vision-images-{case}"
            run = f"run-{session}"
            db.create_or_update_session(session, title=session, user_id="vision-audit")
            db.create_run_record(run, session, user_id="vision-audit")
            captured: list[dict] = []

            class ObservedModel:
                def __init__(self, model):
                    self.model = model

                def invoke(self, messages, config=None, **kwargs):
                    blocks = [block for message in messages for block in message.content if isinstance(block, dict)]
                    hashes = [hashlib.sha256(base64.b64decode(block["image_url"]["url"].split(",", 1)[1])).hexdigest()
                              for block in blocks if block.get("type") == "image_url"]
                    observation = {"imageCount": len(hashes), "sentHashes": hashes}
                    captured.append(observation)
                    response = self.model.invoke(messages, config, **kwargs)
                    observation["usage"] = getattr(response, "usage_metadata", None)
                    observation["finishReason"] = (getattr(response, "response_metadata", {}) or {}).get("finish_reason")
                    return response

            def create_observed(*args, **kwargs):
                return ObservedModel(original_factory(*args, **kwargs))

            started = time.perf_counter()
            with patch.object(llm_factory, "create_for_role", side_effect=create_observed), bind_runtime_context(
                runtime_kind="chat", workspace_path=str(workspace), session_id=session,
                run_id=run, actor_role="supervisor", agent_id="supervisor", user_id="vision-audit",
            ):
                result = vision_media_analyzer.invoke({
                    "type": "tool_call", "id": f"vision-{case}", "name": "vision_media_analyzer",
                    "args": {"images": [
                        {"file_path": str(paths[n]), "label": ("之前", "过程中", "之后")[i]}
                        for i, n in enumerate(order)
                    ], "prompt": prompt},
                })
            text = str(getattr(result, "content", result))
            try:
                answer = parse_answer(text)
                matched = answer_matches(answer, order)
            except (ValueError, TypeError):
                answer, matched = {}, False
            calls_ok = len(captured) == 1 and captured[0]["imageCount"] == 3
            reference = re.search(r"detailRef: (toolobs://[^\s]+)", text)
            record = observability_db.get_tool_observation_record(reference[1]) if reference else None
            stored_ok = observation_matches(record or {}, captured, order)
            cells.append({"case": case, "order": order, "elapsedMs": round((time.perf_counter() - started) * 1000),
                          "passed": bool(matched and calls_ok and stored_ok), "answer": answer,
                          "observationMatches": stored_ok, "calls": captured,
                          "failure": "" if matched and calls_ok and stored_ok else text})
            db.update_run_record(run, status="completed" if cells[-1]["passed"] else "failed")
        return {"kind": "real_provider_tool_live", **identity, "cells": cells,
                "passed": all(cell["passed"] for cell in cells)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--config", help="Read-only existing config.json; credentials never copied to report/state.")
    parser.add_argument("--isolated-root", required=True, help="Must not exist; all state/fixtures/reports go here.")
    args = parser.parse_args(argv)
    if not args.live:
        print("Refusing configuration reads, state creation and provider calls without --live.")
        return 2
    configured_root = Path(os.environ.get("V8_AGENT_OS_HOME") or Path.home() / ".v8-agent-os")
    source_config = Path(args.config) if args.config else configured_root / "config.json"
    isolated = Path(args.isolated_root).resolve()
    if isolated.exists():
        parser.error("--isolated-root must be a new directory")
    source_models = json.loads(source_config.read_text(encoding="utf-8"))["models"]
    isolated.mkdir(parents=True)
    os.environ["V8_AGENT_OS_HOME"] = str(isolated)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    # Provider/debug logs can contain private endpoints; never forward to stdout/report.
    with redirect_stdout(io.StringIO()):
        try:
            report = run_audit(isolated, source_models)
        except Exception as exc:
            report = {"passed": False, "errorType": type(exc).__name__}
    (isolated / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "modelId": report.get("modelId"),
                      "cases": [{"case": cell["case"], "passed": cell["passed"], "elapsedMs": cell["elapsedMs"]}
                                for cell in report.get("cells", [])], "errorType": report.get("errorType")}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
