from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from runtimes.computer_use.visual_benchmark import (
    BenchmarkElementExpectation,
    OfflineBenchmarkCase,
    evaluate_offline_benchmark_case,
    parse_benchmark_case,
    summarize_offline_benchmark,
)
from runtimes.computer_use.visual_parser_adapter import (
    NullVisualParserAdapter,
    PrecomputedVisualParserAdapter,
    RPADesktopVisualLocatorAdapter,
)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _load_manifest(path: Path) -> List[OfflineBenchmarkCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for item in list(payload.get("cases") or []):
        if not isinstance(item, dict):
            continue
        cases.append(parse_benchmark_case(item))
    return cases


def _run_self_check() -> Dict[str, Any]:
    case = OfflineBenchmarkCase(
        case_id="media_results_overlay",
        app_id="generic_media_app",
        scene_id="media.results_overlay",
        image_path="fixtures/media_results_overlay.png",
        page_identity_candidates=["media.results_overlay"],
        required_elements=[
            BenchmarkElementExpectation(
                role="primary_input",
                bbox=[0.32, 0.03, 0.53, 0.09],
                min_iou=0.3,
            )
        ],
        required_hit_zones=[
            BenchmarkElementExpectation(
                role="primary_result",
                bbox=[0.33, 0.23, 0.73, 0.47],
                min_iou=0.2,
            )
        ],
        forbidden_blockers=["login_dialog"],
    )
    from runtimes.computer_use.visual_benchmark import parse_visual_result

    result = parse_visual_result(
        {
            "parserId": "offline_precomputed",
            "pageIdentityCandidates": ["media.results_overlay"],
            "blockerCandidates": [],
            "elementCandidates": [
                {
                    "role": "primary_input",
                    "label": "主输入区",
                    "bbox": [0.325, 0.031, 0.528, 0.089],
                    "confidence": 0.97,
                }
            ],
            "candidateHitZones": [
                {
                    "role": "primary_result",
                    "bbox": [0.34, 0.25, 0.72, 0.46],
                    "gesture": "click",
                    "confidence": 0.91,
                }
            ],
            "visualConfidence": 0.93,
            "latencyMs": 380,
        },
        parser_id="offline_precomputed",
    )
    case_result = evaluate_offline_benchmark_case(case, result)
    summary = summarize_offline_benchmark([case_result])
    _assert(case_result.passed is True, "self-check 样例应通过")
    _assert(summary.get("passRate") == 1.0, "self-check 汇总应为 100%")
    return {
        "status": "ok",
        "mode": "self_check",
        "case": case.as_dict(),
        "result": case_result.as_dict(),
        "summary": summary,
    }


def _build_case_context(case: OfflineBenchmarkCase) -> Dict[str, Any]:
    metadata = dict(case.metadata or {})
    visual_hints = dict(metadata.get("visualHints") or {})
    role_hints = list(visual_hints.get("roleHints") or [])
    if not role_hints:
        for expectation in list(case.required_elements) + list(case.required_hit_zones):
            role_hints.append(
                {
                    "role": expectation.role,
                    "bbox": list(expectation.bbox),
                    "minIou": max(0.1, float(expectation.min_iou) * 0.5),
                    "keywords": list((visual_hints.get("roleKeywords") or {}).get(expectation.role, [])),
                }
            )
    page_identity_hints = list(visual_hints.get("pageIdentityHints") or [])
    if not page_identity_hints:
        page_identity_hints = [
            {
                "sceneId": scene_id,
                "keywords": list((visual_hints.get("pageIdentityKeywords") or {}).get(scene_id, [])),
            }
            for scene_id in list(case.page_identity_candidates)
        ]
    blocker_hints = list(visual_hints.get("blockerHints") or [])
    if not blocker_hints:
        blocker_hints = [
            {
                "blockerId": blocker_id,
                "keywords": list((visual_hints.get("blockerKeywords") or {}).get(blocker_id, [])),
            }
            for blocker_id in list(case.forbidden_blockers)
        ]
    return {
        "appId": case.app_id,
        "sceneId": case.scene_id,
        "tags": list(case.tags),
        "roleHints": role_hints,
        "pageIdentityHints": page_identity_hints,
        "blockerHints": blocker_hints,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run generic offline visual benchmark without touching the main runtime path.")
    parser.add_argument("--manifest", type=str, help="Benchmark manifest JSON path.")
    parser.add_argument("--predictions-dir", type=str, help="Directory containing precomputed prediction JSON files.")
    parser.add_argument("--output", type=str, help="Optional output JSON path.")
    parser.add_argument(
        "--parser",
        type=str,
        default="precomputed",
        choices=["precomputed", "rpa_desktop", "null"],
        help="Offline parser adapter.",
    )
    parser.add_argument("--self-check", action="store_true", help="Run built-in self-check without external files.")
    args = parser.parse_args()

    if args.self_check:
        payload = _run_self_check()
    else:
        if not args.manifest:
            raise ValueError("未提供 --manifest，无法执行离线 benchmark。")
        cases = _load_manifest(Path(args.manifest))
        if args.parser == "precomputed":
            if not args.predictions_dir:
                raise ValueError("使用 precomputed parser 时必须提供 --predictions-dir。")
            adapter = PrecomputedVisualParserAdapter(
                predictions_dir=Path(args.predictions_dir),
                parser_id="offline_precomputed",
            )
        elif args.parser == "rpa_desktop":
            if not args.predictions_dir:
                raise ValueError("使用 rpa_desktop parser 时必须提供 --predictions-dir。")
            adapter = RPADesktopVisualLocatorAdapter(predictions_dir=Path(args.predictions_dir))
        else:
            adapter = NullVisualParserAdapter()
        _assert(adapter.is_available(), "离线视觉解析适配器当前不可用。")
        results = []
        for case in cases:
            parsed = adapter.parse_image(
                image_path=case.image_path,
                context=_build_case_context(case),
            )
            results.append(evaluate_offline_benchmark_case(case, parsed))
        payload = {
            "status": "ok",
            "mode": "offline_benchmark",
            "parser": adapter.capability_summary(),
            "cases": [item.as_dict() for item in cases],
            "results": [item.as_dict() for item in results],
            "summary": summarize_offline_benchmark(results),
        }

    output = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
