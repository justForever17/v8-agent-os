"""Browser contract for the real review component, using synthetic HTTP; no server or Engine."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright


def build_fixture(admin: Path, output: Path) -> None:
    builder = output / "build.cjs"
    builder.write_text(r'''
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const {execFileSync} = require('node:child_process');
const [admin, output] = process.argv.slice(2);
const local = createRequire(path.join(admin, 'package.json'));
const {webpack} = local('next/dist/compiled/webpack/webpack');
const loader = path.join(output, 'typescript-loader.cjs');
fs.writeFileSync(loader, `const ts = require(${JSON.stringify(local.resolve('typescript'))}); module.exports = function(source) { return ts.transpileModule(source, {compilerOptions: {jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext, esModuleInterop: true}}).outputText; };`);
const entry = path.join(output, 'entry.tsx');
fs.writeFileSync(entry, `import React from 'react'; import {createRoot} from 'react-dom/client'; import {LocaleProvider} from '@/components/providers/LocaleProvider'; import {AutomationDeliveryReview} from '@/components/automation/AutomationDeliveryReview'; createRoot(document.getElementById('root')!).render(<LocaleProvider initialLocale={new URLSearchParams(location.search).get('locale') === 'en' ? 'en' : 'zh-CN'}><AutomationDeliveryReview/></LocaleProvider>);`);
webpack({mode:'production', context:admin, entry, output:{path:output,filename:'component.js'}, resolve:{extensions:['.tsx','.ts','.js','.json'],alias:{'@':path.join(admin,'src')},modules:[path.join(admin,'node_modules'),'node_modules']}, module:{rules:[{test:/\.tsx?$/,use:loader}]}, optimization:{minimize:false}, performance:{hints:false}}, (error, stats) => {
  if(error || stats.hasErrors()) { console.error(error || stats.toString({all:false,errors:true})); process.exitCode=1; return; }
  execFileSync(process.execPath, [local.resolve('tailwindcss/lib/cli.js'), '-c',path.join(admin,'tailwind.config.ts'),'-i',path.join(admin,'src/app/globals.css'),'-o',path.join(output,'component.css'),'--minify'], {cwd:admin,stdio:'pipe'});
  fs.appendFileSync(path.join(output,'component.css'), fs.readFileSync(local.resolve('@v8/product-ui/styles.css')));
});
''', "utf-8")
    subprocess.run(["node", str(builder), str(admin), str(output)], check=True, cwd=admin)


def delivery(identity: str, *, phase: str = "unknown", ownership: str = "system") -> dict:
    return {"deliveryId": identity, "definitionId": "fixture-definition", "definitionName": "系统定时任务 " + identity,
        "kind": "cron", "phase": phase, "ownership": ownership, "createdAt": "2026-09-14T05:00:00Z", "updatedAt": "2026-09-14T05:01:00Z"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--channel", choices=("chrome", "msedge"))
    args = parser.parse_args()
    admin = Path(__file__).resolve().parents[1]
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="v8-review-ui-") as temporary:
        assets = Path(temporary)
        build_fixture(admin, assets)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, channel=args.channel)
            checks = []

            def mount(scenario: dict, *, locale="zh-CN", dark=False, width=1440, height=900):
                page = browser.new_page(viewport={"width": width, "height": height}, reduced_motion="reduce")
                scenario.setdefault("posts", [])
                scenario.setdefault("gets", [])
                failures = []
                page.on("pageerror", lambda error: failures.append(str(error)))
                page.on("dialog", lambda dialog: dialog.accept())

                def route_request(route):
                    request = route.request
                    parsed = urlparse(request.url)
                    if parsed.path == "/api/automation/deliveries":
                        query = parse_qs(parsed.query)
                        assert query["ownership"] == ["system,unresolved"]
                        assert query["limit"] == ["25"]
                        scenario["gets"].append(query)
                        if scenario.get("get_failure"):
                            route.fulfill(status=502, body="raw-secret-sentinel")
                            return
                        cursor = query.get("after", [""])[0]
                        items = scenario.get("pages", {}).get(cursor, scenario.get("items", []))
                        next_cursor = scenario.get("cursor") if not cursor else None
                        route.fulfill(json={"items": items, "limit": 25, "hasMore": bool(next_cursor), "nextCursor": next_cursor})
                    elif parsed.path.endswith("/reconcile"):
                        body = request.post_data_json
                        scenario["posts"].append({"url": parsed.path, "body": body})
                        if scenario.get("network_failure"):
                            route.abort("failed")
                            return
                        status = scenario.get("post_status", 200)
                        if status != 200:
                            route.fulfill(status=status, json={"detail": {"code": "fixture_error", "message": "raw-secret-sentinel"}})
                        else:
                            identity = parsed.path.split("/")[-2]
                            scenario["items"] = []
                            route.fulfill(json={"status": "success", "deliveryId": identity if not scenario.get("wrong_receipt") else "other-target", "phase": body["outcome"], "summary": "raw-secret-sentinel"})
                    elif parsed.path == "/component.js":
                        route.fulfill(content_type="text/javascript", body=(assets / "component.js").read_bytes())
                    elif parsed.path == "/component.css":
                        route.fulfill(content_type="text/css", body=(assets / "component.css").read_bytes())
                    elif request.resource_type == "document":
                        route.fulfill(content_type="text/html; charset=utf-8", body=f'<!doctype html><html class="{"dark" if dark else ""}"><head><meta charset="utf-8"><link rel="stylesheet" href="/component.css"></head><body class="admin-app"><main style="padding:24px"><div id="root"></div></main><script src="/component.js"></script></body></html>')
                    else:
                        route.abort()

                page.route("**/*", route_request)
                page.goto("https://automation-fixture.invalid/?locale=" + locale)
                page.wait_for_load_state("networkidle")
                assert not failures, failures
                assert scenario["gets"], "component must mount and perform its initial GET"
                (args.out / "last-rendered.txt").write_text(page.locator("body").inner_text(), "utf-8")
                return page, failures

            scenario = {"items": [delivery("completed", phase="completed"), delivery("user", ownership="user")]}
            page, _ = mount(scenario)
            expect(page.get_by_role("region")).to_have_count(0)
            assert not scenario["posts"]
            checks.append("empty/ordinary/completed records hide the review entrance")
            page.close()

            for outcome in ("completed", "failed"):
                scenario = {"items": [delivery("system-one"), delivery("unresolved-two", ownership="unresolved"), delivery("ordinary", ownership="user")]}
                page, failures = mount(scenario)
                expect(page.get_by_role("button", name="核对结果", exact=True)).to_have_count(2)
                page.get_by_role("button", name="核对结果", exact=True).first.click()
                save = page.get_by_role("button", name="保存核对记录", exact=True)
                expect(save).to_be_disabled()
                page.get_by_role("radio", name="已完成" if outcome == "completed" else "已失败", exact=True).check()
                page.get_by_label("核对证据", exact=True).fill("  public observation  ")
                page.get_by_label("证据位置或记录编号（选填）", exact=True).fill("  public-record-7  ")
                page.locator("form").evaluate("form => {form.requestSubmit(); form.requestSubmit();}")
                expect(page.get_by_role("dialog")).to_have_count(0)
                expect(page.get_by_role("status")).to_contain_text("已保存你的核对记录")
                assert len(scenario["posts"]) == 1
                assert scenario["posts"][0] == {"url": "/api/automation/deliveries/system-one/reconcile", "body": {"outcome": outcome, "evidence": {"observation": "public observation", "reference": "public-record-7"}}}
                assert "raw-secret-sentinel" not in page.locator("body").inner_text()
                assert len(scenario["gets"]) == 2
                assert not failures, failures
                page.close()
            checks.append("both outcomes require evidence, keep exact target, prevent duplicate submission, then refresh")

            for status in (401, 403, 404, 409, 422, 502, 200):
                scenario = {"items": [delivery("failure")], "post_status": status, "wrong_receipt": status == 200}
                page, failures = mount(scenario)
                page.get_by_role("button", name="核对结果", exact=True).click()
                page.get_by_role("radio", name="已失败", exact=True).check()
                page.get_by_label("核对证据", exact=True).fill("retain this public draft")
                page.get_by_role("button", name="保存核对记录", exact=True).click()
                expect(page.get_by_role("dialog").get_by_role("alert")).to_be_visible()
                expect(page.get_by_label("核对证据", exact=True)).to_have_value("retain this public draft")
                assert len(scenario["posts"]) == 1
                assert "raw-secret-sentinel" not in page.locator("body").inner_text()
                if status != 422:
                    expect(page.get_by_role("button", name="保存核对记录", exact=True)).to_be_disabled()
                    scenario["items"] = []
                    page.get_by_role("dialog").get_by_role("button", name="刷新状态", exact=True).click()
                    expect(page.get_by_role("dialog").get_by_role("alert")).to_contain_text("当前列表中没有这条记录")
                assert len(scenario["posts"]) == 1
                assert not failures, failures
                page.close()
            checks.append("auth/conflict/validation/unavailable/wrong receipt retain drafts without retry or raw error disclosure")

            scenario = {"items": [delivery("network")], "network_failure": True}
            page, _ = mount(scenario)
            page.get_by_role("button", name="核对结果", exact=True).click()
            page.get_by_role("radio", name="已完成", exact=True).check()
            page.get_by_label("核对证据", exact=True).fill("public network test")
            page.get_by_role("button", name="保存核对记录", exact=True).click()
            expect(page.get_by_role("dialog").get_by_role("alert")).to_contain_text("保存结果尚未确认")
            expect(page.get_by_label("核对证据", exact=True)).to_have_value("public network test")
            assert len(scenario["posts"]) == 1
            page.close()
            scenario = {"get_failure": True}
            page, _ = mount(scenario)
            expect(page.get_by_role("alert")).to_contain_text("暂时无法读取")
            assert "raw-secret-sentinel" not in page.locator("body").inner_text()
            expect(page.get_by_role("button", name="核对结果", exact=True)).to_have_count(0)
            checks.append("network failures remain unconfirmed, retain drafts and reveal no raw response")
            page.close()

            cursor = "opaque+/= cursor"
            scenario = {"pages": {"": [delivery("page-one")], cursor: [delivery("page-one"), delivery("page-two")]}, "cursor": cursor}
            page, _ = mount(scenario)
            page.get_by_role("button", name="加载更多", exact=True).click()
            expect(page.get_by_role("button", name="核对结果", exact=True)).to_have_count(2)
            assert scenario["gets"][-1]["after"] == [cursor]
            page.get_by_role("button", name="刷新状态", exact=True).click()
            expect(page.get_by_role("button", name="核对结果", exact=True)).to_have_count(1)
            assert "after" not in scenario["gets"][-1]
            checks.append("opaque keyset pagination, deduplication and first-page refresh")
            page.close()

            for width, dark, locale in ((1440, False, "zh-CN"), (390, False, "zh-CN"), (390, True, "en")):
                scenario = {"items": [delivery("visual-one"), delivery("visual-two", ownership="unresolved")]}
                page, failures = mount(scenario, width=width, height=640 if width == 390 else 900, dark=dark, locale=locale)
                review = "Review result" if locale == "en" else "核对结果"
                page.get_by_role("button", name=review, exact=True).first.focus()
                page.keyboard.press("Enter")
                expect(page.get_by_role("dialog")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "horizontal overflow"
                save_box = page.get_by_role("button", name="Save review record" if locale == "en" else "保存核对记录", exact=True).bounding_box()
                assert save_box and save_box["y"] >= 0 and save_box["y"] + save_box["height"] <= page.viewport_size["height"], "save footer must stay visible"
                page.screenshot(path=str(args.out / f"review-{width}-{'dark' if dark else 'light'}-{locale}.png"))
                page.keyboard.press("Escape")
                expect(page.get_by_role("dialog")).to_have_count(0)
                assert not scenario["posts"]
                assert not failures, failures
                page.close()
            checks.append("desktop/mobile, Chinese/English, light/dark, keyboard open/Escape, no horizontal overflow")
            browser.close()
    (args.out / "interaction-evidence.json").write_text(json.dumps({"checks": checks, "backend": "synthetic HTTP boundary; no Engine, Admin server or provider invoked"}, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"passed": len(checks), "evidence": str(args.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
