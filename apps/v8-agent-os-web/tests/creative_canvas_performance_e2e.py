from __future__ import annotations

import argparse
import base64
import json
import statistics
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright
from PIL import Image


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2n3AAAAAASUVORK5CYII="
)


def _asset(session_id: str, index: int) -> dict[str, object]:
    asset_id = "stress-model" if index == 0 else f"stress-asset-{index:03d}"
    is_model = index == 0
    return {
        "assetId": asset_id,
        "id": asset_id,
        "sessionId": session_id,
        "workspaceId": "canvas-performance",
        "projectId": "canvas-performance",
        "workspaceRelativePath": f".v8/media/stress/{asset_id}.{'glb' if is_model else 'png'}",
        "title": "Stress model.glb" if is_model else f"Stress image {index:03d}.png",
        "name": "Stress model.glb" if is_model else f"Stress image {index:03d}.png",
        "mediaType": "model_3d" if is_model else "image",
        "mimeType": "model/gltf-binary" if is_model else "image/png",
        "size": 1,
        "originKind": "source",
        "originId": asset_id,
        "originSessionId": session_id,
        "folderId": f"folder-{index % 20:02d}",
        "adoptedByCurrentSession": True,
        "contentUrl": f"/api/workbench/sessions/{session_id}/media/assets/{asset_id}/content",
        "previewUrl": f"/api/workbench/sessions/{session_id}/media/assets/{asset_id}/content",
        "metadata": {},
    }


def _node(index: int) -> dict[str, object]:
    is_model = index == 0
    return {
        "nodeId": "node-model" if is_model else f"node-{index:03d}",
        "kind": "resource",
        "origin": "workspace_asset",
        "resourceId": "stress-model" if is_model else f"stress-asset-{index:03d}",
        "x": 80 + (index % 12) * 420,
        "y": 80 + (index // 12) * 360,
        "width": 280,
        "height": 190,
        "title": "Stress model.glb" if is_model else f"Stress image {index:03d}.png",
        "mediaType": "model_3d" if is_model else "image",
    }


def _edge(index: int, node_count: int) -> dict[str, object]:
    source = index % node_count
    target = (source + 1 + index // node_count) % node_count
    return {
        "edgeId": f"edge-{index:03d}",
        "from": "node-model" if source == 0 else f"node-{source:03d}",
        "to": "node-model" if target == 0 else f"node-{target:03d}",
        "fromPort": "right",
        "toPort": "left",
        "fromPortId": "output",
        "toPortId": "input",
        "dataType": "unknown",
        "role": "relation",
        "order": index,
        "note": "",
    }


def _next_paint(page: Page) -> None:
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")


def _slope(values: list[float]) -> float:
    count = len(values)
    mean_x = (count - 1) / 2
    mean_y = statistics.fmean(values)
    denominator = sum((index - mean_x) ** 2 for index in range(count))
    return sum((index - mean_x) * (value - mean_y) for index, value in enumerate(values)) / denominator


def _screenshot_pixels(path: Path) -> dict[str, object]:
    with Image.open(path) as source:
        image = source.convert("RGB")
        image.thumbnail((160, 160))
        flattened = getattr(image, "get_flattened_data", None)
        pixels = list(flattened() if callable(flattened) else image.getdata())
    buckets: dict[tuple[int, int, int], int] = {}
    for red, green, blue in pixels:
        bucket = (red >> 4, green >> 4, blue >> 4)
        buckets[bucket] = buckets.get(bucket, 0) + 1
    dominant = max(buckets.values(), default=0)
    return {
        "width": source.width,
        "height": source.height,
        "colorBuckets": len(buckets),
        "dominantColorRatio": round(dominant / max(1, len(pixels)), 4),
    }


def run(args: argparse.Namespace) -> dict[str, object]:
    session_url = f"{args.web_base.rstrip('/')}/chat?id={args.session_id}"
    glb = Path(args.glb).read_bytes()
    assets = [_asset(args.session_id, index) for index in range(500)]
    node_count = 120
    graph_payload = {
        "graph": {
            "schema": "v8.creative_canvas_graph.v1",
            "version": 3,
            "graphId": "stress-graph",
            "nodes": [_node(index) for index in range(node_count)],
            "edges": [_edge(index, node_count) for index in range(200)],
            "viewport": {"x": 24, "y": 24, "scale": 1},
        },
        "revision": 1,
        "runtime": {"status": "idle", "nodeStates": {}, "outputs": {}},
        "history": {"canUndo": False, "canRedo": False, "undoDepth": 0, "redoDepth": 0},
    }
    folders = [{
        "folderId": f"folder-{index:02d}",
        "folderKind": "episode" if index < 10 else "sources",
        "title": f"Folder {index:02d}",
        "assetCount": 25,
    } for index in range(20)]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--no-proxy-server", "--js-flags=--expose-gc"],
        )
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(session_url, wait_until="domcontentloaded", timeout=120_000)
        page.get_by_role("button", name="添加到工作台").wait_for(state="visible", timeout=30_000)

        def route_api(route) -> None:
            url = route.request.url
            method = route.request.method
            if "/canvas/graph" in url and method == "GET":
                route.fulfill(status=200, content_type="application/json", body=json.dumps(graph_payload))
            elif "/canvas/actions" in url:
                route.fulfill(status=200, content_type="application/json", body='{"actions":[]}')
            elif "/canvas/templates" in url:
                route.fulfill(status=200, content_type="application/json", body='{"templates":[]}')
            elif "/media/assets?" in url:
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"assets": assets}))
            elif url.endswith("/media/folders"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps({"folders": folders}))
            elif url.endswith("/media/reconcile"):
                route.fulfill(status=200, content_type="application/json", body='{"registeredAssetIds":[],"skipped":[]}')
            elif "/api/artifacts" in url:
                route.fulfill(status=200, content_type="application/json", body='{"artifacts":[]}')
            elif "/api/sources" in url:
                route.fulfill(status=200, content_type="application/json", body='{"sources":[]}')
            elif url.endswith("/media/assets/stress-model/content"):
                route.fulfill(status=200, content_type="model/gltf-binary", body=glb)
            elif "/media/assets/stress-asset-" in url:
                route.fulfill(status=200, content_type="image/png", body=PNG)
            else:
                route.continue_()

        page.route("**/api/**", route_api)
        page.get_by_role("button", name="添加到工作台").click()
        page.get_by_text("画布", exact=True).click()
        canvas = page.get_by_test_id("creative-artifact-canvas")
        canvas.wait_for(state="visible", timeout=30_000)
        page.wait_for_function("() => document.querySelectorAll('[data-canvas-node]').length > 0", timeout=30_000)
        _next_paint(page)

        tray_button = canvas.get_by_role("button").filter(has_text="素材").first
        tray_button.click()
        page.wait_for_timeout(250)
        if canvas.locator("[data-canvas-asset-window]").count():
            windowing_kind = "virtualized"
            drawer = canvas.locator("[data-canvas-asset-window]").locator("xpath=ancestor::div[contains(@class,'overflow-y-auto')][1]")
            initial_cards = canvas.locator("[data-canvas-asset-window] [draggable=true]").count()
            first_visible_start = int(canvas.locator("[data-canvas-asset-window-start]").get_attribute("data-canvas-asset-window-start") or 0)
            drawer.evaluate("element => { element.scrollTop = element.scrollHeight; element.dispatchEvent(new Event('scroll', { bubbles: true })); }")
            page.wait_for_function("() => Number(document.querySelector('[data-canvas-asset-window-start]')?.dataset.canvasAssetWindowStart || 0) > 0")
            _next_paint(page)
            bottom_cards = canvas.locator("[data-canvas-asset-window] [draggable=true]").count()
            bottom_visible_start = int(canvas.locator("[data-canvas-asset-window-start]").get_attribute("data-canvas-asset-window-start") or 0)
        else:
            windowing_kind = "legacy_incremental"
            tray = canvas.locator("[data-canvas-wheel-isolation]").last
            initial_cards = tray.locator("[draggable=true]").count()
            tray.evaluate("root => { const elements = [...root.querySelectorAll('*')]; const drawer = elements.find(element => element.scrollHeight > element.clientHeight + 20); if (drawer) { drawer.scrollTop = drawer.scrollHeight; drawer.dispatchEvent(new Event('scroll', { bubbles: true })); } }")
            page.wait_for_timeout(250)
            bottom_cards = tray.locator("[draggable=true]").count()
            first_visible_start = 0
            bottom_visible_start = 0
        tray_button.click()

        model_node = canvas.locator('[data-canvas-node="node-model"]')
        model_node.click()
        load_model = canvas.get_by_role("button", name="加载 3D")
        load_model.click()
        model_node.locator("canvas").wait_for(state="visible", timeout=30_000)
        _next_paint(page)

        client = context.new_cdp_session(page)
        samples: list[dict[str, float | int]] = []
        for cycle in range(30):
            cycle_started = time.perf_counter()
            page.get_by_role("tab", name="概览").click()
            _next_paint(page)
            reopen_started = time.perf_counter()
            page.get_by_role("tab", name="创意画布").click()
            canvas.wait_for(state="visible")
            _next_paint(page)
            reopen_ms = (time.perf_counter() - reopen_started) * 1000
            if cycle % 5 == 4:
                client.send("HeapProfiler.collectGarbage")
            heap = client.send("Runtime.getHeapUsage")
            dom = client.send("Memory.getDOMCounters")
            samples.append({
                "cycle": cycle + 1,
                "heapMiB": round(float(heap["usedSize"]) / 1024 / 1024, 3),
                "nodes": int(dom["nodes"]),
                "documents": int(dom["documents"]),
                "webglCanvases": page.locator("canvas").count(),
                "reopenMs": round(reopen_ms, 2),
                "roundTripMs": round((time.perf_counter() - cycle_started) * 1000, 2),
            })

        tail = samples[-15:]
        gc_samples = [item for item in samples if int(item["cycle"]) % 5 == 0]
        heap_values = [float(item["heapMiB"]) for item in gc_samples]
        node_values = [float(item["nodes"]) for item in gc_samples]
        heap_slope = _slope(heap_values) / 5
        node_slope = _slope(node_values) / 5
        warm_reopen_times = sorted(float(item["reopenMs"]) for item in samples[5:])
        reopen_p95 = warm_reopen_times[min(len(warm_reopen_times) - 1, int(len(warm_reopen_times) * 0.95))]
        windowing_passed = (
            0 < initial_cards <= 36
            and 0 < bottom_cards <= 36
            and first_visible_start == 0
            and bottom_visible_start > 0
        )
        baseline_p95 = None
        reopen_budget_ms = args.reopen_budget_ms
        if args.baseline_result:
            baseline_payload = json.loads(Path(args.baseline_result).read_text(encoding="utf-8"))
            baseline_p95 = float(dict(baseline_payload.get("plateau") or {}).get("warmReopenP95Ms") or 0)
            if baseline_p95 <= 0:
                raise ValueError("Canvas performance baseline does not contain warmReopenP95Ms")
            reopen_budget_ms = round(baseline_p95 * 1.10, 2)
        plateau_passed = (
            heap_slope <= 0.25
            and max(heap_values) - min(heap_values) <= max(12.0, min(heap_values) * 0.20)
            and node_slope <= 5
            and max(int(item["webglCanvases"]) for item in tail) <= 1
            and reopen_p95 <= reopen_budget_ms
        )
        output_path = Path(args.output)
        evidence_stem = output_path.stem
        desktop_screenshot = output_path.parent / f"{evidence_stem}-desktop.png"
        mobile_screenshot = output_path.parent / f"{evidence_stem}-mobile.png"
        desktop_model_screenshot = output_path.parent / f"{evidence_stem}-desktop-model.png"
        mobile_model_screenshot = output_path.parent / f"{evidence_stem}-mobile-model.png"
        page.screenshot(path=str(desktop_screenshot), full_page=True)
        page.locator("canvas").first.screenshot(path=str(desktop_model_screenshot))
        desktop_pixels = _screenshot_pixels(desktop_model_screenshot)
        page.set_viewport_size({"width": 390, "height": 844})
        _next_paint(page)
        page.screenshot(path=str(mobile_screenshot), full_page=True)
        page.locator("canvas").first.screenshot(path=str(mobile_model_screenshot))
        mobile_pixels = _screenshot_pixels(mobile_model_screenshot)
        visual_passed = all(
            int(probe.get("width") or 0) > 0
            and int(probe.get("height") or 0) > 0
            and int(probe.get("colorBuckets") or 0) >= 4
            and float(probe.get("dominantColorRatio") or 1) < 0.98
            for probe in (desktop_pixels, mobile_pixels)
        )
        result = {
            "schema": "v8.creative_canvas_performance_e2e.v1",
            "capturedAtEpochMs": int(time.time() * 1000),
            "fixture": {"nodes": node_count, "edges": 200, "assets": 500, "cycles": 30},
            "windowing": {
                "initialCards": initial_cards,
                "bottomCards": bottom_cards,
                "initialStart": first_visible_start,
                "bottomStart": bottom_visible_start,
                "implementation": windowing_kind,
                "passed": windowing_passed,
            },
            "plateau": {
                "heapSlopeMiBPerCycle": round(heap_slope, 4),
                "nodeSlopePerCycle": round(node_slope, 4),
                "warmReopenP95Ms": round(reopen_p95, 2),
                "warmReopenBudgetMs": reopen_budget_ms,
                "baselineP95Ms": baseline_p95,
                "regressionPercent": round(((reopen_p95 / baseline_p95) - 1) * 100, 2) if baseline_p95 else None,
                "gcSamples": len(gc_samples),
                "passed": plateau_passed,
            },
            "visual": {
                "desktop": {**desktop_pixels, "screenshot": str(desktop_screenshot), "modelScreenshot": str(desktop_model_screenshot)},
                "mobile": {**mobile_pixels, "screenshot": str(mobile_screenshot), "modelScreenshot": str(mobile_model_screenshot)},
                "passed": visual_passed,
            },
            "samples": samples,
            "pageErrors": errors,
            "passed": (windowing_passed or args.allow_legacy_windowing) and plateau_passed and visual_passed and not errors,
        }
        browser.close()
        return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--glb", required=True)
    parser.add_argument("--web-base", default="http://127.0.0.1:9527")
    parser.add_argument("--output", required=True)
    parser.add_argument("--reopen-budget-ms", type=float, default=152.0)
    parser.add_argument("--baseline-result")
    parser.add_argument("--allow-legacy-windowing", action="store_true")
    args = parser.parse_args()
    result = run(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
