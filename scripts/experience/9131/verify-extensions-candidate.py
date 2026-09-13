"""Independent production browser tests with deterministic external HTTP faults.

No real install, Engine request, credential export or private user state.
"""
import argparse
import asyncio
import json
import re
import subprocess
import statistics
import math
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.async_api import async_playwright

COMMIT = "09dfcc3cd231d1717d3e712ad2270750c4c1fe31"
BASE = "http://127.0.0.1:22825"


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", default="")
    parser.add_argument("--candidate", default=COMMIT)
    parser.add_argument("--build-id", default="unspecified")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[3]
    assert subprocess.check_output(["git", "rev-parse", args.candidate], cwd=repo, text=True).strip() == args.candidate
    out = args.out.resolve()
    assert out.is_relative_to(repo / "tmp")
    out.mkdir(parents=True, exist_ok=True)
    requests, submitted, evidence, errors, assets = [], [], [], [], []
    holds, started, finished, jobs = {}, {}, {}, {}
    theme = {"theme": "light"}
    mcp_config = None
    config_saves = []
    selected = [x for x in args.only.split(",") if x]
    report = {"candidateCommit": args.candidate, "runtime": "production standalone 22825, exact source/build as explicitly handed off by owner", "buildId": args.build_id, "qualification": "Independent real browser / stateful synthetic external HTTP boundary. No real install, Engine or business API acceptance.", "requests": requests, "submitted": submitted, "configSaves": config_saves, "evidence": evidence, "errors": errors}
    long_readme = "\n\n".join(f"## Independent section {i}\n\n合成长说明用于测试操作可达性 {i}。" for i in range(100))

    def operation(body, kind):
        provider, item_id = body["provider"], body["id"]
        source = "" if kind == "mcp" else body["skillId"] if provider == "modelscope" else body["source"]
        identity = item_id if kind == "mcp" else body["skillId"] if provider == "modelscope" else f"{source}@{body['skillId']}"
        target = f"{provider}:{kind}:{source}:{identity}"
        return {"operationId": kind + "-" + item_id, "target": target, "provider": provider, "kind": kind, "source": body.get("source", ""), "itemId": item_id, "skillId": body.get("skillId"), "candidateId": body.get("candidateId"), "status": "completed", "phase": "completed", "canCancel": False, "updatedAt": len(submitted), "result": {"readiness": "configured"} if kind == "mcp" else {"installed": [], "skipped": [{"name": item_id}], "conflicts": []}}

    async def barrier(key):
        started.setdefault(key, asyncio.Event()).set()
        if key in holds:
            await asyncio.wait_for(holds[key].wait(), 20)

    async def route_api(route):
        nonlocal mcp_config
        req = route.request
        u = urlparse(req.url)
        if f"{u.scheme}://{u.netloc}" != BASE:
            requests.append({"path": u.path, "action": "external-aborted"})
            await route.abort()
            return
        if not u.path.startswith("/api/"):
            await route.continue_()
            return
        if u.path in ["/api/auth/providers", "/api/auth/csrf", "/api/auth/callback/credentials", "/api/auth/session"]:
            await route.continue_()
            return
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        row = {"path": u.path, "query": q, "method": req.method}
        requests.append(row)
        data, status, key = {"error": "independent_unlisted_fixture"}, 503, None
        if u.path == "/api/extensions/store/operations":
            data, status = {"operations": list(jobs.values())}, 200
        elif u.path in ["/api/extensions/store/operations/skills", "/api/extensions/store/operations/mcp"]:
            body = req.post_data_json
            submitted.append(body)
            kind = u.path.split("/")[-1]
            key = "install:" + kind + ":" + body["id"]
            await barrier(key)
            data = operation(body, kind)
            jobs[data["target"]] = data
            status = 200
        elif u.path in ["/api/extensions/store/skills/detail", "/api/extensions/store/mcp/detail"]:
            kind = "skills" if "/skills/" in u.path else "mcp"
            item_id = q.get("id") or q.get("skillId")
            key = "detail:" + kind + ":" + item_id
            await barrier(key)
            data, status = {"id": item_id, "description": item_id, "markdown": long_readme, "revision": "fixture-revision"}, 200
            if kind == "mcp":
                data["candidates"] = [{"id": item_id + "-candidate", "serverName": item_id.split("/")[-1], "label": "HTTP", "transport": "http", "requirements": []}]
        elif u.path in ["/api/extensions/store/skills", "/api/extensions/store/mcp"]:
            provider, query = q.get("provider", "international"), q.get("query", "")
            key = "list:" + provider + ":" + query
            await barrier(key)
            if query == "offline":
                data, status = {"detail": {"message": "Independent fixture source offline"}}, 502
            else:
                data, status = {"items": [{"id": "author/" + name, "skillId": "author/" + name if provider == "modelscope" else name, "source": "same-source", "name": f"{provider} {query} {name}".replace("  ", " "), "description": "合成扩展：同ID跨来源，完整内容另在详情。", "detailUrl": "https://fixture.invalid/item"} for name in ["alpha", "beta"]], "hasMore": False, "sourceCoverage": "catalog"}, 200
        elif u.path == "/api/ui-preferences/theme":
            if req.method == "PUT":
                theme.update(req.post_data_json)
            data, status = theme, 200
        elif u.path == "/api/extensions/health":
            data, status = {"detail": "Independent fixture health unavailable"}, 503
        elif u.path == "/api/extensions/catalog":
            data, status = {"summary": {"skillCount": 0, "mcpServerCount": 0, "connectedMcpServerCount": 0, "mcpToolCount": 0}, "skills": {"root": "synthetic", "items": []}, "mcp": {"servers": []}}, 200
            if mcp_config is not None:
                data["mcp"]["servers"] = [{"name": name, "status": "disabled", "transport": "stdio", "toolCount": 0, "tools": []} for name in mcp_config["mcpServers"]]
                data["summary"]["mcpServerCount"] = len(data["mcp"]["servers"])
        elif u.path == "/api/mcp/config" and mcp_config is not None:
            if req.method == "POST":
                config_saves.append(req.post_data_json)
                mcp_config = req.post_data_json
                data, status = {"status": "success"}, 200
            else:
                data, status = mcp_config, 200
        elif u.path == "/api/config-registry/extensions":
            data, status = {"domain": "extensions", "data": {"prefilterPolicy": {"enabled": False, "futurePolicy": "keep", "skills": {"future": 0}}, "modelBindings": {}, "futureConfig": {"keep": False}}, "warnings": []}, 200
        elif u.path in ["/api/models", "/api/skills/safety/reviews", "/api/admin-inbox"]:
            data, status = ([] if u.path == "/api/models" else {"items": []}), 200
        row["status"] = status
        await route.fulfill(status=status, content_type="application/json", body=json.dumps(data, ensure_ascii=False))
        if key:
            finished.setdefault(key, asyncio.Event()).set()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome")
        context = await browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN", reduced_motion="reduce")
        await context.add_init_script("""(() => { const fetch = window.fetch; window.__ineffectiveAbort = false;
          window.fetch = function(input, options) { if (window.__ineffectiveAbort && String(input).includes('/extensions/store/') && String(input).includes('/detail')) { options={...options}; delete options.signal; } return fetch.call(this,input,options); }; })();""")
        await context.route("**/*", route_api)
        page = await context.new_page()
        page.set_default_timeout(12000)
        page.on("pageerror", lambda error: errors.append({"url": page.url, "error": str(error)[:300]}))
        report["browserVersion"] = browser.version

        async def close():
            if await page.get_by_role("dialog").count():
                await page.keyboard.press("Escape")
                await page.get_by_role("dialog").wait_for(state="hidden")

        async def visit(kind="skills", normalize_source=True):
            await close()
            await page.goto(BASE + "/admin/extensions/store?kind=" + kind, wait_until="networkidle")
            await page.locator("article button").first.wait_for()
            if normalize_source and await page.get_by_role("button", name="切回国际源", exact=True).count():
                await page.get_by_role("button", name="切回国际源", exact=True).click()
                await page.get_by_role("button", name="international alpha", exact=False).first.wait_for()

        async def item(name):
            await page.get_by_role("button", name=name, exact=False).first.click()
            await page.get_by_role("dialog").wait_for()

        async def bounds(locator):
            box = await locator.bounding_box()
            vp = page.viewport_size
            return {"box": box, "viewport": vp, "inside": bool(box and box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= vp["width"] + 1 and box["y"] + box["height"] <= vp["height"] + 1)}

        async def test(name, dimension, fn):
            if selected and not any(name.startswith(x) for x in selected):
                return
            row = {"id": name, "dimension": dimension, "layer": "production browser / synthetic HTTP boundary"}
            try:
                row["details"] = await fn()
                row["status"] = "PASS"
            except AssertionError as error:
                row.update(status="FAIL", error=str(error)[:1000])
            except Exception as error:
                row.update(status="HARNESS_ERROR", error=str(error)[:1000])
            row["url"] = page.url
            row["screenshot"] = name + ".png"
            await page.screenshot(path=str(out / row["screenshot"]))
            evidence.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            (out / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        try:
            await page.goto(BASE + "/login", wait_until="networkidle")
            await page.locator("#login").fill("fixture-owner")
            # Explicitly public isolated fixture account, not a user credential.
            await page.locator("#password").fill("synthetic-ui-password")
            await page.locator('button[type="submit"]').click()
            await page.wait_for_url("**/admin")

            async def source_contract():
                await visit(normalize_source=False)
                assert await page.get_by_text("当前：国际源", exact=True).is_visible()
                await page.get_by_role("button", name="切换国内源", exact=True).click()
                await page.get_by_role("button", name="modelscope alpha", exact=False).first.wait_for()
                await page.reload(wait_until="networkidle")
                assert await page.get_by_text("当前：魔搭国内源", exact=True).is_visible()
                await page.get_by_role("textbox", name="搜索扩展").fill("offline")
                await page.get_by_role("alert").filter(has_text=re.compile(r"\S")).first.wait_for()
                assert await page.get_by_text("当前：魔搭国内源", exact=True).is_visible()
                await page.get_by_role("button", name="切回国际源", exact=True).click()
                await page.get_by_role("textbox", name="搜索扩展").fill("")
                await page.get_by_role("button", name="international alpha", exact=False).first.wait_for()
                return {"freshDefaultInternational": True, "manualPersistReload": True, "offlineKeepsSource": True, "switchBack": True}
            await test("X01-manual-source", "function", source_contract)

            async def search_race():
                await visit()
                key = "list:international:old"
                holds[key] = asyncio.Event()
                await page.get_by_role("textbox", name="搜索扩展").fill("old")
                await asyncio.wait_for(started.setdefault(key, asyncio.Event()).wait(), 5)
                await page.get_by_role("textbox", name="搜索扩展").fill("new")
                await page.get_by_role("button", name="international new alpha", exact=False).first.wait_for()
                holds.pop(key).set()
                await asyncio.wait_for(finished.setdefault(key, asyncio.Event()).wait(), 5)
                await page.wait_for_timeout(150)
                assert await page.get_by_role("button", name="international new alpha", exact=False).first.is_visible()
                assert await page.get_by_role("button", name="international old alpha", exact=False).count() == 0
                return {"newSurvivesOldResponse": True, "order": "old started → new rendered → old fulfilled"}
            await test("X03-query-order", "function", search_race)

            async def mcp_race():
                await visit("mcp")
                await page.evaluate("window.__ineffectiveAbort=true")
                key = "detail:mcp:author/alpha"
                holds[key] = asyncio.Event()
                await item("international alpha")
                await asyncio.wait_for(started.setdefault(key, asyncio.Event()).wait(), 5)
                await close()
                await item("international beta")
                await page.locator("#store-candidate").wait_for()
                holds.pop(key).set()
                await asyncio.wait_for(finished.setdefault(key, asyncio.Event()).wait(), 5)
                await page.wait_for_timeout(150)
                await page.get_by_role("dialog").get_by_role("button", name="保存并连接", exact=True).click()
                await page.get_by_role("dialog").get_by_text("配置已保存，待连接", exact=True).first.wait_for()
                assert submitted[-1]["id"] == "author/beta", submitted[-1]
                assert submitted[-1]["candidateId"] == "author/beta-candidate", submitted[-1]
                return {"submittedId": submitted[-1]["id"], "candidateId": submitted[-1]["candidateId"], "abortDeliberatelyIneffective": True, "configuredNotHealthy": True}
            await test("X02-MCP-target-after-late-detail", "function", mcp_race)

            async def concurrent_installs():
                jobs.clear()
                before_submits = len(submitted)
                await visit()
                a, b = "install:skills:author/alpha", "install:skills:author/beta"
                holds[a], holds[b] = asyncio.Event(), asyncio.Event()
                await item("international alpha")
                await page.get_by_role("dialog").get_by_role("button", name="安装", exact=True).click()
                await asyncio.wait_for(started.setdefault(a, asyncio.Event()).wait(), 5)
                await close()
                await item("international beta")
                install = page.get_by_role("dialog").get_by_role("button", name="安装", exact=True)
                await install.click()
                await asyncio.wait_for(started.setdefault(b, asyncio.Event()).wait(), 5)
                holds.pop(a).set()
                await asyncio.wait_for(finished.setdefault(a, asyncio.Event()).wait(), 5)
                await page.wait_for_timeout(150)
                assert await install.is_disabled(), "A completion must not clear B pending state"
                assert await page.get_by_role("dialog").get_by_role("heading", name="international beta", exact=True).is_visible()
                holds.pop(b).set()
                await page.get_by_role("dialog").get_by_text("已是此版本", exact=True).first.wait_for()
                current_submits = submitted[before_submits:]
                assert len([x for x in current_submits if x["id"] == "author/beta" and x.get("skillId") == "beta"]) == 1, current_submits
                return {"BPendingAfterACompletion": True, "BDialogPreserved": True, "singleBSubmit": True, "skippedNotInstalled": True}
            await test("X04-concurrent-install-identity", "function", concurrent_installs)

            async def footer():
                await close()
                await visit()
                results = []
                for width, height, desired in [(1440, 900, "light"), (390, 844, "dark")]:
                    await close()
                    if theme["theme"] != desired:
                        await page.get_by_role("button", name="切换明暗主题", exact=True).click()
                    await page.set_viewport_size({"width": width, "height": height})
                    await item("international beta")
                    dialog = page.get_by_role("dialog")
                    await dialog.get_by_role("button", name="使用说明", exact=True).click()
                    await dialog.get_by_role("heading", name="Independent section 99", exact=True).wait_for(state="attached")
                    action = dialog.get_by_role("button", name="继续操作", exact=True)
                    box = await bounds(action)
                    status = dialog.get_by_role("status").filter(has_text="已是此版本")
                    status_box = await bounds(status)
                    assert box["inside"], box
                    assert status_box["inside"], status_box
                    assert desired in (await page.locator("html").get_attribute("class")), desired
                    results.append({"theme": desired, "action": box, "receiptStatus": status_box})
                    await page.screenshot(path=str(out / f"footer-{width}-{desired}.png"))
                return {"longReadme": 100, "variants": results, "footerFeedbackVisibleWithDocs": True}
            await test("X05-footer-and-receipt-feedback", "convenience-visual", footer)

            async def health_failure():
                await close()
                await page.set_viewport_size({"width": 1440, "height": 900})
                await page.goto(BASE + "/admin/extensions", wait_until="networkidle")
                await page.get_by_text("部分数据加载失败，可重试；已加载数据仍可使用。", exact=True).wait_for()
                command = page.get_by_placeholder("npx --yes skills add https://github.com/vercel-labs/skills -g --skill find-skills")
                await command.fill("synthetic non-executed install draft")
                assert await command.input_value() == "synthetic non-executed install draft"
                return {"health503Visible": True, "independentImportDraftEditable": True, "submittedRealCommand": False}
            await test("X09-health-partial-availability", "convenience", health_failure)

            async def readable_source_error():
                await visit()
                await page.get_by_role("textbox", name="搜索扩展").fill("offline")
                alert = page.get_by_role("alert").filter(has_text=re.compile(r"\S")).first
                await alert.wait_for()
                actual = await alert.inner_text()
                assert "Independent fixture source offline" in actual, f"structured error message lost: {actual!r}"
                return {"sourceErrorReadable": True}
            await test("X10-structured-error-readable", "convenience", readable_source_error)

            async def source_identity():
                await visit()
                await page.get_by_role("button", name="切换国内源", exact=True).click()
                await item("modelscope beta")
                dialog = page.get_by_role("dialog")
                await dialog.get_by_role("button", name="安装", exact=True).wait_for()
                assert await dialog.get_by_text("已是此版本", exact=True).count() == 0, "international receipt must not mark identical domestic item installed"
                await close()
                first = len(requests)
                await page.reload(wait_until="networkidle")
                await page.get_by_role("button", name="modelscope alpha", exact=False).first.wait_for()
                listings = [r for r in requests[first:] if r.get("path") in ["/api/extensions/store/skills", "/api/extensions/store/mcp"]]
                assert listings and all(r["query"].get("provider") == "modelscope" for r in listings), listings
                return {"sameIdReceiptIsolatedByProvider": True, "domesticReloadNoInternationalList": True, "reloadListRequests": len(listings)}
            await test("X11-provider-receipt-and-reload", "function", source_identity)

            async def detail_samples():
                jobs.clear()
                await visit()
                await page.evaluate("""() => document.addEventListener('click',e=>{
                  if(e.target.closest('button')?.textContent.includes('international beta')) window.__detailOpenAt=performance.now();
                },true)""")
                samples = []
                for index in range(31):
                    await item("international beta")
                    await page.wait_for_function("() => [...document.querySelectorAll('[role=dialog] button')].some(b=>b.textContent.trim()==='安装'&&!b.disabled)")
                    elapsed = await page.evaluate("() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(performance.now()-window.__detailOpenAt))))")
                    if index:
                        samples.append(round(elapsed, 2))
                    await close()
                ordered = sorted(samples)
                return {"n": len(samples), "samplesMs": samples, "medianMs": statistics.median(samples), "p95Ms": ordered[math.ceil(len(samples)*.95)-1], "qualification": "Production Chrome153 warm click→detail ready→two frames with synthetic response; documentation is lazy and not opened. No baseline comparison or native performance claim."}
            await test("X12-detail-warm-samples", "performance", detail_samples)

            async def stdio_ui_roundtrip():
                nonlocal mcp_config
                exact_args = ["--label", "  spaced value  ", ""]
                mcp_config = {"mcpServers": {"independent-arguments": {"type": "stdio", "command": "synthetic-command", "args": exact_args.copy(), "env": {}, "disabled": True, "future": {"keep": False, "zero": 0}}}}
                await close()
                await page.goto(BASE + "/admin/extensions", wait_until="networkidle")
                await page.get_by_title("编辑 MCP 配置", exact=True).click()
                dialog = page.get_by_role("dialog")
                await dialog.wait_for()
                editor = dialog.locator("textarea").first
                shown = json.loads(await editor.input_value())
                assert shown == exact_args, shown
                before = len(config_saves)
                await dialog.get_by_role("button", name="保存修改", exact=True).click()
                await dialog.wait_for(state="hidden")
                assert len(config_saves) == before + 1
                actual = config_saves[-1]["mcpServers"]["independent-arguments"]
                assert actual["args"] == exact_args, actual["args"]
                assert actual["future"] == {"keep": False, "zero": 0}
                return {"unmodifiedArgsShownExactly": True, "actualInterceptedPostArgs": actual["args"], "unknownRetained": True, "realEngineWrite": False}
            await test("X13-stdio-ui-unmodified-save", "function", stdio_ui_roundtrip)
        finally:
            for gate in holds.values():
                gate.set()
            report["summary"] = {status: sum(row["status"] == status for row in evidence) for status in ["PASS", "FAIL", "HARNESS_ERROR"]}
            (out / "evidence.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            await browser.close()
            print(json.dumps({"summary": report["summary"], "out": str(out)}, ensure_ascii=False))
    return 1 if report["summary"]["FAIL"] or report["summary"]["HARNESS_ERROR"] else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
