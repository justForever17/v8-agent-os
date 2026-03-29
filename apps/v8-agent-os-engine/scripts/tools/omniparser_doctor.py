from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[2]
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from runtimes.computer_use.visual_parser_adapter import OmniParserVisualParserAdapter
from scripts.stdout_utf8 import emit_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Check OmniParser installation and optional offline parse availability.")
    parser.add_argument("--omniparser-root", type=str, help="Official OmniParser repository root.")
    parser.add_argument("--omniparser-som-model", type=str, help="SOM/icon detect model path.")
    parser.add_argument("--omniparser-caption-model-path", type=str, help="Caption model path or HF cache path.")
    parser.add_argument("--omniparser-caption-model-name", type=str, default="florence2", help="Caption model name.")
    parser.add_argument("--omniparser-device", type=str, default=None, help="Device for OmniParser, e.g. cuda/cpu.")
    parser.add_argument("--image", type=str, help="Optional screenshot path for a real parse attempt.")
    args = parser.parse_args()

    adapter = OmniParserVisualParserAdapter(
        repo_root=args.omniparser_root,
        som_model_path=args.omniparser_som_model,
        caption_model_path=args.omniparser_caption_model_path,
        caption_model_name=args.omniparser_caption_model_name,
        device=args.omniparser_device,
    )
    payload = {
        "status": "ok",
        "availability": adapter.is_available(),
        "capability": adapter.capability_summary(),
        "installation": adapter.installation_status(),
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
    emit_json(payload)


if __name__ == "__main__":
    main()
