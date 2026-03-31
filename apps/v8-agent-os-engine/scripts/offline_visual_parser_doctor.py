from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[1]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from runtimes.computer_use.visual_parser_adapter import (
    NullVisualParserAdapter,
    PrecomputedVisualParserAdapter,
    RPADesktopVisualLocatorAdapter,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check generic offline visual parser availability.")
    parser.add_argument(
        "--parser",
        type=str,
        default="rpa_desktop",
        choices=["precomputed", "rpa_desktop", "null"],
        help="Offline parser adapter.",
    )
    parser.add_argument("--predictions-dir", type=str, help="Optional prediction directory for precomputed or RPA desktop replay.")
    parser.add_argument("--image", type=str, help="Optional screenshot path for a sample parse attempt.")
    args = parser.parse_args()

    if args.parser == "precomputed":
        if not args.predictions_dir:
            raise ValueError("使用 precomputed doctor 时必须提供 --predictions-dir。")
        adapter = PrecomputedVisualParserAdapter(
            predictions_dir=Path(args.predictions_dir),
            parser_id="offline_precomputed",
        )
    elif args.parser == "rpa_desktop":
        adapter = RPADesktopVisualLocatorAdapter(
            predictions_dir=Path(args.predictions_dir).expanduser() if args.predictions_dir else None
        )
    else:
        adapter = NullVisualParserAdapter()

    payload = {
        "status": "ok",
        "availability": adapter.is_available(),
        "capability": adapter.capability_summary(),
    }
    if args.image and adapter.is_available():
        result = adapter.parse_image(image_path=args.image, context={})
        payload["sampleParse"] = {
            "pageIdentityCandidates": list(result.page_identity_candidates),
            "blockerCandidates": list(result.blocker_candidates),
            "elementCount": len(result.element_candidates),
            "hitZoneCount": len(result.candidate_hit_zones),
            "latencyMs": result.latency_ms,
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
