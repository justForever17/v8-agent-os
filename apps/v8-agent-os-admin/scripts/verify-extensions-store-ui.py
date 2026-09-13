"""Actual production component, synthetic HTTP boundaries; no real installs."""
import argparse
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:22825")
    parser.add_argument("--output", default="tmp/store-ui-evidence")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, channel="chrome")
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        errors, requests, submitted = [], [], []
        jobs = {}
        theme_state = {"theme": "light"}
        exact_args = ["--label", "  spaced value  ", ""]
        config_saves = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        async def route_api(route):
            from urllib.parse import urlparse, parse_qs
            url = urlparse(route.request.url)
            if url.path.startswith("/api/auth"):
                await route.continue_()
                return
            q = {key: values[0] for key, values in parse_qs(url.query).items()}
            requests.append((url.path, q))
            data = {}
            if url.path == "/api/extensions/store/operations":
                data = {"operations": list(jobs.values())}
            elif url.path.endswith("/operations/mcp"):
                body = route.request.post_data_json
                submitted.append(body)
                target = f"{body['provider']}:mcp::{body['id']}"
                data = {"operationId": body["id"], "target": target, "provider": body["provider"], "kind": "mcp", "source": "",
                        "itemId": body["id"], "status": "completed", "phase": "completed", "canCancel": False,
                        "updatedAt": 1, "result": {"readiness": "configured"}}
                jobs[target] = data
            elif url.path.endswith("/operations/skills"):
                body = route.request.post_data_json
                submitted.append(body)
                await asyncio.sleep(0.6 if body["skillId"].endswith("alpha") else 1.0)
                source = body['skillId'] if body['provider'] == 'modelscope' else body['source']
                identity = body['skillId'] if body['provider'] == 'modelscope' else f"{source}@{body['skillId']}"
                target = f"{body['provider']}:skills:{source}:{identity}"
                data = {"operationId": body["skillId"], "target": target, "provider": body["provider"], "kind": "skills",
                        "source": body["source"], "itemId": body["id"], "skillId": body["skillId"], "status": "completed",
                        "phase": "completed", "canCancel": False, "updatedAt": 1,
                        "result": {"installed": [], "skipped": [{}], "conflicts": []}}
                jobs[target] = data
            elif url.path.endswith("/skills/detail"):
                await asyncio.sleep(0.45 if "alpha" in q["skillId"] else 0.02)
                data = {"description": q["skillId"], "markdown": "\n\n".join(f"## Section {i}\n\nComplete reference content {i}." for i in range(100))}
            elif url.path.endswith("/mcp/detail"):
                await asyncio.sleep(0.45 if "alpha" in q["id"] else 0.02)
                data = {"id": q["id"], "markdown": "MCP fixture documentation", "candidates": [{"id": q["id"] + "-candidate", "serverName": q["id"].split("/")[-1], "label": "HTTP", "transport": "http", "requirements": []}]}
            elif url.path.endswith("/store/skills") or url.path.endswith("/store/mcp"):
                source = q.get("provider", "international")
                query = q.get("query", "")
                await asyncio.sleep(0.8 if query == "old" else 0.01)
                if query == "offline":
                    await route.fulfill(status=502, json={"detail": {"message": "Synthetic source offline"}})
                    return
                items = [{"id": f"author/{name}", "skillId": f"author/{name}" if source == "modelscope" else name,
                          "source": "author/skills", "name": f"{source} {query} {name}".replace("  ", " "),
                          "description": "A complete extension for the fixture.", "detailUrl": "https://example.invalid/skill"}
                         for name in ("alpha", "beta")]
                data = {"items": items, "hasMore": False, "sourceCoverage": "catalog"}
            elif url.path == "/api/ui-preferences/theme":
                if route.request.method == "PUT":
                    theme_state.update(route.request.post_data_json)
                data = theme_state
            elif url.path == "/api/client/instance":
                data = {"instanceId": "isolated-ui-fixture", "name": "Fixture"}
            elif url.path == "/api/models":
                data = []
            elif url.path == "/api/extensions/health":
                await route.fulfill(status=503, json={"detail": "Synthetic health unavailable"})
                return
            elif url.path == "/api/extensions/catalog":
                data = {"summary": {"skillCount": 0, "mcpServerCount": 1, "connectedMcpServerCount": 0, "mcpToolCount": 0}, "skills": {"root": "fixture", "items": []}, "mcp": {"servers": [{"name": "arguments-fixture", "status": "disabled", "transport": "stdio", "toolCount": 0, "tools": []}]}}
            elif url.path == "/api/mcp/config":
                if route.request.method == "POST":
                    config_saves.append(route.request.post_data_json)
                    data = {"status": "success"}
                else:
                    data = {"mcpServers": {"arguments-fixture": {"type": "stdio", "command": "fixture", "args": exact_args, "env": {}, "disabled": True}}}
            elif url.path == "/api/config-registry/extensions":
                data = {"domain": "extensions", "data": {"prefilterPolicy": {"enabled": False, "futurePolicy": "keep", "skills": {"futureSkill": 7}}, "modelBindings": {}, "futureConfig": {"keep": True}}, "warnings": []}
            await route.fulfill(json=data)
        await page.route("**/api/**", route_api)
        await page.goto(args.url + "/login", wait_until="networkidle")
        if await page.locator("#login").count():
            await page.locator("#login").fill("fixture-owner")
            if await page.locator("#name").count():
                await page.locator("#name").fill("Fixture Owner")
                await page.locator("#confirmPassword").fill("synthetic-ui-password")
            await page.locator("#password").fill("synthetic-ui-password")
            await page.locator('button[type="submit"]').click()
            await page.wait_for_url("**/admin", timeout=30000)
        await page.goto(args.url + "/admin/extensions/store", wait_until="networkidle")
        await page.screenshot(path=str(output / "initial.png"))
        search = page.get_by_role("textbox", name="搜索扩展")
        await page.get_by_role("button", name="international alpha", exact=False).first.wait_for()
        await page.get_by_role("button", name="切换国内源", exact=True).click()
        await page.get_by_role("button", name="modelscope alpha", exact=False).first.wait_for()
        await page.reload(wait_until="networkidle")
        assert await page.get_by_text("当前：魔搭国内源", exact=True).is_visible()
        await search.fill("old")
        await page.wait_for_timeout(380)
        await search.fill("new")
        await page.wait_for_timeout(1300)
        assert await page.get_by_role("button", name="modelscope new alpha", exact=False).first.is_visible()
        assert not await page.get_by_role("button", name="modelscope old alpha", exact=False).count()
        await search.fill("offline")
        await page.wait_for_timeout(700)
        assert await page.get_by_text("当前：魔搭国内源", exact=True).is_visible()
        assert await page.get_by_role("alert").get_by_text("Synthetic source offline", exact=True).is_visible()
        assert "[object Object]" not in (await page.get_by_role("alert").inner_text())
        await page.get_by_role("button", name="切回国际源", exact=True).click()
        await search.fill("")
        await page.wait_for_timeout(700)
        await page.get_by_role("button", name="international alpha", exact=False).first.click()
        await page.keyboard.press("Escape")
        await page.get_by_role("button", name="international beta", exact=False).first.click()
        await page.wait_for_timeout(650)
        dialog = page.get_by_role("dialog")
        assert await dialog.get_by_role("heading", name="international beta").is_visible()
        await dialog.get_by_role("button", name="使用说明", exact=True).click()
        await dialog.get_by_role("heading", name="Section 99", exact=True).wait_for()
        install = dialog.get_by_role("button", name="安装", exact=True)
        for viewport, theme in [({"width": 1440, "height": 900}, "light"), ({"width": 390, "height": 844}, "dark")]:
            if theme == "dark":
                await page.keyboard.press("Escape")
                await page.get_by_role("button", name="切换明暗主题", exact=True).click()
                await page.get_by_role("button", name="international beta", exact=False).first.click()
                await page.get_by_role("dialog").get_by_role("button", name="使用说明", exact=True).click()
                await page.wait_for_timeout(100)
                assert "dark" in (await page.locator("html").get_attribute("class") or "")
            await page.set_viewport_size(viewport)
            await page.emulate_media(color_scheme=theme, reduced_motion="reduce")
            box = await install.bounding_box()
            assert box and box["y"] >= 0 and box["y"] + box["height"] <= viewport["height"]
            await page.screenshot(path=str(output / f"footer-{viewport['width']}.png"))
        await install.click()
        await page.wait_for_timeout(1200)
        assert submitted[-1]["skillId"] == "beta"
        assert await dialog.get_by_text("已是此版本", exact=True).first.is_visible()
        await page.keyboard.press("Escape")
        await page.set_viewport_size({"width": 1440, "height": 900})
        await page.get_by_role("button", name="international alpha", exact=False).first.click()
        await page.wait_for_timeout(600)
        await page.get_by_role("dialog").get_by_role("button", name="安装", exact=True).click()
        await page.keyboard.press("Escape")
        await page.get_by_role("button", name="international beta", exact=False).first.click()
        await page.wait_for_timeout(100)
        await page.get_by_role("dialog").get_by_role("button", name="继续操作", exact=True).click()
        await page.wait_for_timeout(650)
        assert await page.get_by_role("dialog").get_by_role("heading", name="international beta").is_visible()
        assert await page.get_by_role("dialog").get_by_role("button", name="继续操作", exact=True).is_disabled()
        await page.wait_for_timeout(600)
        assert not await page.get_by_role("dialog").get_by_role("button", name="继续操作", exact=True).is_disabled()
        await page.keyboard.press("Escape")
        await page.get_by_role("button", name="MCP", exact=True).click()
        await page.get_by_role("button", name="international alpha", exact=False).first.click()
        await page.keyboard.press("Escape")
        await page.get_by_role("button", name="international beta", exact=False).first.click()
        await page.wait_for_timeout(650)
        await page.get_by_role("dialog").get_by_role("button", name="保存并连接", exact=True).click()
        await page.wait_for_timeout(100)
        assert submitted[-1]["id"] == "author/beta"
        assert submitted[-1]["candidateId"] == "author/beta-candidate"
        assert await page.get_by_role("dialog").get_by_text("配置已保存，待连接", exact=True).first.is_visible()
        await page.goto(args.url + "/admin/extensions", wait_until="networkidle")
        await page.get_by_placeholder("npx --yes skills add https://github.com/vercel-labs/skills -g --skill find-skills").wait_for(timeout=10000)
        assert await page.get_by_text("部分数据加载失败，可重试；已加载数据仍可使用。", exact=True).is_visible()
        await page.get_by_title("编辑 MCP 配置", exact=True).click()
        await page.get_by_role("dialog").wait_for()
        arg_editor = page.get_by_role("dialog").locator("textarea").first
        assert json.loads(await arg_editor.input_value()) == exact_args
        await page.get_by_role("dialog").get_by_role("button", name="保存修改", exact=True).click()
        await page.get_by_role("dialog").wait_for(state="hidden")
        assert config_saves[-1]["mcpServers"]["arguments-fixture"]["args"] == exact_args
        await page.screenshot(path=str(output / "management-partial.png"), full_page=True)
        assert not errors, errors
        result = {"status": "passed", "boundary": "production UI with synthetic network; no Engine install",
                  "checks": ["manual source persistence and switch back", "offline never switches source and preserves detail.message", "late query ignored",
                             "late detail cannot change install target", "footer visible on desktop and narrow viewport",
                             "skipped result distinguished", "A completion preserves B dialog and B pending install", "MCP late detail cannot change submitted id or candidate", "health failure does not block management or import", "stdio edit submits exact whitespace and empty arguments"],
                  "requests": len(requests), "screenshots": str(output)}
        (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
