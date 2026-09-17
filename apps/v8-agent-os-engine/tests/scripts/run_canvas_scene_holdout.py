"""Explicit local HTTP holdout: abstract porcelain and ice, two source PNGs.

Uses run_canvas_scene_local_server.py's isolated session-other. No external model.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:19533/v1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    session_id = "session-other"
    base = args.base_url.rstrip("/")

    def call(method, route, **kwargs):
        response = requests.request(method, base + route, timeout=60, **kwargs)
        response.raise_for_status()
        return response.json()

    inputs = []
    for index in range(2):
        image = Image.new("RGB", (512, 512), "#151c30")
        draw = ImageDraw.Draw(image)
        if index == 0:
            draw.ellipse((85, 85, 425, 425), fill="#d9d9d1")
            draw.arc((100, 210, 410, 300), 0, 360, fill="#4c6aa3", width=12)
            draw.ellipse((150, 145, 195, 195), fill="#f4f4ed")
        else:
            draw.polygon([(110, 180), (330, 110), (410, 195), (200, 270)], fill="#a6cae2")
            draw.polygon([(110, 180), (200, 270), (200, 425), (110, 340)], fill="#5184ad")
            draw.polygon([(200, 270), (410, 195), (410, 355), (200, 425)], fill="#79a9c5")
            draw.line((240, 270, 285, 330, 340, 300, 390, 345), fill="#daf0fa", width=5)
        path = args.output / ("porcelain.png" if index == 0 else "ice.png")
        image.save(path)
        with path.open("rb") as handle:
            source = call("POST", "/chat/upload", files={"file": (path.name, handle, "image/png")}, data={"sessionId": session_id, "sourceKind": "canvas_upload"})
        inputs.append({"id": source.get("sourceId") or source.get("id"), "name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    entities = [{"entityId": "porcelain", "name": "瓷质行星", "kind": "object", "shape": "sphere", "proxyColor": "#eeaa33", "size": [1.2, 1.2, 1.2], "appearance": "Ivory sphere with a cobalt equatorial ring", "material": "Glazed porcelain", "motion": [{"time": 0, "position": [-1.2, .1, 0]}, {"time": 4, "position": [.2, 1.4, -.2]}]},
                {"entityId": "ice", "name": "晶蓝冰块", "kind": "object", "shape": "box", "proxyColor": "#ab55db", "size": [.8, .8, .8], "appearance": "Faceted blue cube with fine internal cracks", "material": "Translucent ice", "motion": [{"time": 0, "position": [1.2, .3, .2], "rotation": [0, 20, 0]}, {"time": 4, "position": [-.8, .2, 1], "rotation": [0, 110, 0]}]}]
    scene = {"schema": "v8.proxy_scene.v1", "durationSeconds": 4, "fps": 24, "width": 640, "height": 360, "background": "#151c30", "style": "Abstract product photography, midnight blue background, crisp cold rim lighting; no characters", "entities": entities, "camera": {"projection": "perspective", "fov": 40, "keyframes": [{"time": 0, "position": [0, 3, 8], "target": [0, 1, 0]}, {"time": 4, "position": [-3, 2.5, 6], "target": [0, 1, 0]}]}}
    nodes = [{"nodeId": "scene", "kind": "action", "origin": "placeholder", "x": 500, "y": 200, "width": 350, "height": 300, "actionDefinitionId": "creative_media.render_proxy_scene_control_pack", "mediaType": "document", "parameters": {"scene": scene}, "configurationRevision": 1}, {"nodeId": "result", "kind": "result", "origin": "placeholder", "x": 950, "y": 200, "width": 360, "height": 280, "mediaType": "document", "producerActionNodeId": "scene", "outputSlot": "control_pack"}]
    edges = [{"edgeId": "output", "from": "scene", "to": "result", "fromPort": "right", "toPort": "left", "fromPortId": "output", "toPortId": "input", "dataType": "document", "role": "data", "order": 0}]
    for i, source in enumerate(inputs):
        nodes.append({"nodeId": f"source-{i}", "kind": "resource", "origin": "source", "resourceId": source["id"], "title": source["name"], "mediaType": "image", "x": 80, "y": 80+i*330, "width": 280, "height": 280})
        edges.append({"edgeId": f"reference-{i}", "from": f"source-{i}", "to": "scene", "fromPort": "right", "toPort": "left", "fromPortId": "output", "toPortId": "references", "dataType": "image", "role": "data", "order": i, "entityId": entities[i]["entityId"], "bindingKey": f"material-{i}", "semanticRole": "material", "purpose": "Use only this entity's material, surface detail and appearance; motion comes from the proxy.", "resourceDigest": source["sha256"]})
    route = f"/sessions/{session_id}/canvas/graph"
    prior = call("GET", route)
    graph = {"schema": "v8.creative_canvas_graph.v1", "version": 3, "graphId": "holdout-scene", "nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "scale": 1}}
    saved = call("POST", route, json={"graph": graph, "expectedRevision": prior.get("revision", 0)})
    started = call("POST", route + "/runs", json={"graphId": graph["graphId"], "graphRevision": saved["revision"], "targetNodeIds": ["result"]})
    for _ in range(90):
        result = call("GET", route)
        if result.get("runtime", {}).get("status") in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(1)
    assert result["runtime"]["status"] == "succeeded", result["runtime"]
    version = result["runtime"]["outputs"]["result"][0]
    manifest = call("GET", f"/artifacts/{version['artifactId']}/content?sessionId={session_id}")
    for field, suffix in (("videoArtifactId", "mp4"), ("imageArtifactId", "png")):
        response = requests.get(base + f"/artifacts/{manifest['preview'][field]}/content?sessionId={session_id}", timeout=60)
        response.raise_for_status()
        (args.output / ("holdout." + suffix)).write_bytes(response.content)
    assert len(manifest["references"]) == 2
    assert manifest["scene"]["style"] == scene["style"]
    (args.output / "evidence.json").write_text(json.dumps({"level": "real-local-http", "externalProvider": False, "started": started, "graph": result, "manifest": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "output": str(args.output), "artifactId": version["artifactId"]}))


if __name__ == "__main__":
    main()
