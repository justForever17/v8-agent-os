"""One real vision invocation after the isolated executor HTTPS/WSS media chain.

Only a freshly generated geometric JPEG is sent. Model configuration is read in
memory; CredentialRefStore remains the sole credential resolver. This local
fixture uses Pillow and is not the separate minimal/no-Pillow Server acceptance.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import time
from unittest.mock import patch


PROMPT = (
    "Inspect this image directly. Ignore the plain background. Group the visible filled geometric shapes "
    "by color and shape. Return only one JSON object with a groups array. Each group must have color "
    "and shape as lowercase English names, count as an integer, and horizontal_position as left, middle, "
    "right, or distributed. Describe only visible evidence. Do not infer objects from filenames or labels. "
    "Do not include Markdown fences, commentary, or an explanation."
)


def geometric_jpeg() -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (640, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((450, 60, 550, 160), fill="#155eef")
    draw.polygon(((80, 160), (140, 60), (200, 160)), fill="#16a34a")
    for left in (100, 230, 360, 490):
        draw.rectangle((left, 270, left + 50, 320), fill="#ff8c00")
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=85, progressive=False, optimize=False)
    image.close()
    return output.getvalue()


def parse_summary(content) -> dict:
    text = content if isinstance(content, str) else "\n".join(
        str(item.get("text", "")) for item in content if isinstance(item, dict) and item.get("type") == "text"
    )
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, count=1)
        stripped = re.sub(r"\s*```$", "", stripped, count=1)
    answer = json.loads(stripped)  # No repair, truncation completion, or inferred answer.
    if not isinstance(answer, dict) or not isinstance(answer.get("groups"), list) or len(answer["groups"]) > 12:
        raise ValueError("invalid_geometric_answer")
    groups = []
    for row in answer["groups"]:
        color = str(row.get("color", "")).strip().lower()
        shape = str(row.get("shape", "")).strip().lower()
        position = str(row.get("horizontal_position", "")).strip().lower()
        count = row.get("count")
        if (color not in {"blue", "green", "orange", "red", "yellow", "purple", "black", "white"}
                or shape not in {"circle", "ellipse", "triangle", "square", "rectangle"}
                or position not in {"left", "middle", "right", "distributed"}
                or isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 20):
            raise ValueError("invalid_geometric_answer")
        groups.append({"color": color, "shape": shape, "count": count, "horizontal_position": position})
    return {"groups": groups}


def matches_oracle(summary: dict) -> bool:
    groups = summary.get("groups", [])
    if len(groups) != 3:
        return False
    by_color = {row["color"]: row for row in groups}
    return set(by_color) == {"blue", "green", "orange"} and all(
        by_color[color]["shape"] in shapes and by_color[color]["count"] == count
        and by_color[color]["horizontal_position"] == position
        for color, shapes, count, position in (
            ("blue", {"circle", "ellipse"}, 1, "right"),
            ("green", {"triangle"}, 1, "left"),
            ("orange", {"square", "rectangle"}, 4, "distributed"),
        )
    )


def safe_usage(usage) -> dict:
    if not isinstance(usage, dict):
        return {}
    return {key: value for key, value in usage.items()
            if key in {"input_tokens", "output_tokens", "total_tokens"}
            and isinstance(value, int) and not isinstance(value, bool) and value >= 0}


def run_once(output: Path, config_path: Path, report: dict) -> None:
    jpeg = geometric_jpeg()
    image_hash = hashlib.sha256(jpeg).hexdigest()
    (output / "geometric-fixture.jpg").write_bytes(jpeg)
    report["imageSha256"] = image_hash
    report["fixtureUsesPillow"] = True
    report["minimalServerClaim"] = False
    source_models = json.loads(config_path.read_text(encoding="utf-8"))["models"]
    # Never import or migrate a plaintext configuration secret. Only the existing
    # managed credential reference may be materialized by ModelControlPlane.
    for provider in dict(source_models.get("providers") or {}).values():
        if isinstance(provider, dict) and isinstance(provider.get("provider"), dict):
            provider["provider"].pop("api_key", None)
            provider["provider"].pop("apiKey", None)

    smoke_path = Path(__file__).with_name("run_executor_media_server_smoke.py")
    spec = importlib.util.spec_from_file_location("executor_media_loopback_smoke", smoke_path)
    smoke = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(smoke)
    smoke.JPEG = jpeg
    with patch.object(sys, "argv", [str(smoke_path), "--live", "--output", str(output / "transport")]):
        smoke.main()

    from core.database import db
    from core.model_capability_matrix import build_effective_capability_matrix
    from core.model_control_plane import model_control_plane
    from core.storage import storage
    from core.tools import vision_media_analyzer as vision
    from core.tool_surface import apply_tool_surface_budget
    from erc.runtime_context import bind_runtime_context
    from langchain_core.messages import ToolMessage
    from runtimes.network_supervisor.executors.service import get_executor_service

    service = get_executor_service()
    with service.identity.database() as connection:
        row = connection.execute("SELECT command_id,owner_id FROM executor_commands ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row is not None
    command = service.status(row["owner_id"], row["command_id"])
    with bind_runtime_context(session_id="bench-session", run_id="bench-run", runtime_kind="chat", agent_id="supervisor", user_id=row["owner_id"]):
        visible_message = apply_tool_surface_budget(ToolMessage(name="device_broker", tool_call_id="executor-status-projection",
            content=json.dumps(command)), {"agentVisibleBudget": 6000}, runtime_kind="native")
    visible_text = str(visible_message.content)
    assert visible_text.startswith("Device executor result\n")
    data_lines = [line.removeprefix("Data: ") for line in visible_text.splitlines() if line.startswith("Data: ")]
    assert len(data_lines) == 1
    visible = json.loads(data_lines[0])
    # Follow the actual Agent-visible pointer. Raw runtime state is evidence for
    # equality only, never a bypass when the rendered tool result loses inputs.
    artifact_id = visible["screenshotRef"]["artifactId"]
    artifact_path = Path(visible["screenshotRef"]["filePath"])
    assert artifact_id == command["screenshotRef"]["artifactId"]
    anchors = visible["precondition"]
    assert all(key in anchors for key in ("observationId", "deviceId", "bootId", "controlSessionId", "resourceId", "appId", "windowId", "frameId", "geometryRevision", "rotation", "viewport", "width", "height"))
    report["agentProjection"] = {"visibleChars": len(visible_text), "budget": 6000, "screenshotRefPreserved": True, "frameAnchorsPreserved": True}
    artifact = db.get_runtime_artifact(artifact_id)
    assert artifact and artifact["metadata"]["executorCapture"] is True
    assert hashlib.sha256(artifact_path.read_bytes()).hexdigest() == image_hash
    episode_id = command["command"]["traceRef"]["episodeId"]
    episode = db.get_runtime_episode(episode_id)
    report["chain"] = {
        "commandId": command["commandId"], "commandStatus": command["status"],
        "receiptStatus": command["receipt"]["status"], "receiptSeq": command["receipt"]["receiptSeq"],
        "episodeId": episode_id, "episodeState": episode["state"], "artifactId": artifact_id,
        "persistedArtifactSha256": image_hash, "mediaStatus": command["mediaStatus"],
        "deviceBusinessVerification": command["businessVerification"],
    }
    report["chainPassed"] = command["status"] == "succeeded" and episode["state"] == "completed" and command["mediaStatus"] == "available"
    with patch.object(storage, "get_models_config", side_effect=lambda: deepcopy(source_models)):
        resolution = model_control_plane.resolve_model_for_role("vision")
        provider = dict(resolution.get("resolvedProvider") or {})
        model = dict(resolution.get("resolvedModel") or {})
        report["model"] = {"providerId": resolution.get("resolvedProviderId"), "modelId": resolution.get("resolvedModelId")}
        capability = build_effective_capability_matrix(capability_class=str(model.get("capabilityClass") or ""),
            capabilities=model.get("capabilities") or {}, api_standard=str(provider.get("api_standard") or "openai"))
        if not all(report["model"].values()) or not capability.get("supports_multimodal"):
            report["reason"] = "configured_vision_model_missing_or_not_multimodal"
            return
        if not (provider.get("credentialRef") or provider.get("credential_ref")) or provider.get("credentialStatus") != "configured":
            report["reason"] = "managed_vision_credential_unavailable"
            return
        original_factory = vision.llm_factory.create_for_role
        captured = []

        class ObservedModel:
            def __init__(self, actual):
                self.actual = actual

            def __getattr__(self, name):
                return getattr(self.actual, name)

            def invoke(self, messages, config=None, **kwargs):
                if report["providerInvocations"] != 0:
                    raise RuntimeError("one_provider_invocation_budget_exhausted")
                blocks = [block for message in messages if isinstance(message.content, list)
                          for block in message.content if isinstance(block, dict) and block.get("type") == "image_url"]
                hashes = [hashlib.sha256(base64.b64decode(block["image_url"]["url"].split(",", 1)[1], validate=True)).hexdigest() for block in blocks]
                assert hashes == [image_hash]
                source_hashes = [item["sourceSha256"] for item in (config or {}).get("metadata", {}).get("images", [])]
                assert source_hashes == [image_hash]
                report["payload"] = {"imageCount": len(hashes), "sentHashes": hashes, "sourceHashes": source_hashes}
                report["providerInvocations"] += 1
                started = time.monotonic()
                response = self.actual.invoke(messages, config, **kwargs)
                report["providerElapsedMs"] = round((time.monotonic() - started) * 1000)
                report["usage"] = safe_usage(getattr(response, "usage_metadata", None))
                captured.append(response)
                return response

        def observed_factory(*args, **kwargs):
            # Factory and adapter remain real; explicit zero retries enforces
            # this audit's single-call budget without changing source settings.
            kwargs["max_retries"] = 0
            kwargs["timeout"] = 90
            return ObservedModel(original_factory(*args, **kwargs))

        with patch.object(vision.llm_factory, "create_for_role", side_effect=observed_factory), bind_runtime_context(
            session_id="bench-session", run_id="bench-run", runtime_kind="chat", actor_role="supervisor",
            agent_id="supervisor", user_id=row["owner_id"],
        ):
            result = vision.vision_media_analyzer.invoke({"type": "tool_call", "id": "executor-real-vision", "name": "vision_media_analyzer",
                "args": {"file_path": str(artifact_path), "prompt": PROMPT}})
        if len(captured) != 1:
            report["reason"] = "vision_tool_did_not_return_one_real_provider_result"
            return
        report["analysis"] = parse_summary(captured[0].content)
        report["oracleMatches"] = matches_oracle(report["analysis"])
        report["toolCompleted"] = "--- Vision Analysis Complete ---" in str(result.content)
        report["result"] = "passed" if report["chainPassed"] and report["oracleMatches"] and report["toolCompleted"] else "failed"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True, help="New isolated directory, never an existing Engine state root")
    parser.add_argument("--config", type=Path, default=Path.home() / ".v8-agent-os" / "config.json", help="Read models only; no mutation or credential copying")
    args = parser.parse_args()
    if not args.live:
        parser.error("Explicit --live is required before configuration reads or a provider invocation")
    output = args.output.resolve()
    if output.exists():
        parser.error("Use a new output directory to prevent accidental replay of a paid audit")
    output.mkdir(parents=True)
    report = {"kind": "executor_media_real_vision_provider", "result": "unverified", "physicalDeviceUsed": False,
              "providerInvocations": 0, "providerRetries": 0}
    # Provider/SDK diagnostics can include private endpoints. Keep both streams
    # in memory and never copy them, raw exceptions, or raw model output to disk.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        try:
            run_once(output, args.config, report)
        except Exception as error:
            report["errorType"] = type(error).__name__
            report["result"] = "failed" if report["providerInvocations"] else "unverified"
    (output / "provider-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
