"""Real React Native Web navigation tests; synthetic router/storage/native boundaries.

Build before/after with build-phone-navigation-ui.cjs, then run:
python tests/phone-navigation-ui.py <evidence-dir> --browser msedge
No device, real account, Engine, or automation is contacted.
"""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser()
parser.add_argument("output", type=Path)
parser.add_argument("--browser", default="msedge")
args = parser.parse_args()
output = args.output.resolve()


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(output)))
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"
results = {"boundary": "Real React Native Web components; synthetic routing, persistence, API, safe area and font scaling. Not native Android acceptance.", "checks": [], "matrix": []}


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel=args.browser, headless=True)
    page = browser.new_page(viewport={"width": 320, "height": 640})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def fixture(expression):
        return page.evaluate("() => {const f=window.phoneNavigationFixture; " + expression + "}")

    def settle():
        # Wait for RN Web's 300ms Modal fade, including its focus trap.
        page.wait_for_timeout(350)

    def load(version="after", query="", width=320, height=640):
        page.set_viewport_size({"width": width, "height": height})
        page.goto(f"{base}/{version}/?{query}")
        page.wait_for_load_state("networkidle")
        page.wait_for_function("window.phoneNavigationFixture?.ready")

    def button(label):
        return page.get_by_role("button", name=label, exact=True)

    def open_menu(label="导航"):
        button(label).click()
        settle()

    def close_menu(label="关闭"):
        button(label).last.click()
        settle()

    def snap(name):
        page.screenshot(path=str(output / name))

    def check(name):
        results["checks"].append(name)

    def bounds(locator, width, height, target=False):
        box = locator.bounding_box()
        assert box, "missing visible bounds"
        assert box["x"] >= -1 and box["y"] >= -1, box
        assert box["x"] + box["width"] <= width + 1, box
        assert box["y"] + box["height"] <= height + 1, box
        if target:
            assert box["width"] >= 44 and box["height"] >= 44, box
        return box

    # Same baseline fixture proves both the reported overflow and stale jump.
    load("before")
    old_profile = button("用户资料").bounding_box()
    assert old_profile["x"] + old_profile["width"] > 320
    results["baselineOverflow"] = old_profile
    snap("before-chat-320.png")
    open_menu()
    snap("before-menu-320.png")
    fixture("f.setDraftMode('hold');")
    button("设置").click()
    page.keyboard.press("Escape")
    fixture("f.resolveFlush();")
    page.wait_for_function("window.phoneNavigationFixture.records.routes.length===1")
    assert fixture("return f.records.routes[0].target;") == "/settings"
    check("baseline reproduces offscreen right control and late navigation after close")

    configurations = [
        (320, 640, "zh-CN", "light", 1, 0, 0),
        (360, 740, "en", "dark", 1, 24, 16),
        (320, 640, "en", "light", 1.8, 28, 24),
        (640, 320, "zh-CN", "dark", 1.8, 12, 16),
    ]
    for screen in ["chat", "sessions", "connect", "settings", "rpa"]:
        for width, height, locale, theme, scale, top, bottom in configurations:
            nav = "导航" if locale == "zh-CN" else "Navigation"
            close = "关闭" if locale == "zh-CN" else "Close"
            history = "历史会话" if locale == "zh-CN" else "Conversation history"
            artifact = "产物" if locale == "zh-CN" else "Artifacts"
            query = f"page={screen}&locale={locale}&theme={theme}&fontScale={scale}&top={top}&bottom={bottom}"
            load(query=query, width=width, height=height)
            bounds(button(nav), width, height, True)
            bounds(button("切换语言" if locale == "zh-CN" else "Switch language"), width, height, True)
            if screen == "chat" and scale == 1:
                for label in ["桌面实时预览" if locale == "zh-CN" else "Desktop Live", "切换主题" if locale == "zh-CN" else "Switch theme"]:
                    bounds(button(label), width, height, True)
                snap(f"after-chat-{width}-{locale}-{theme}.png")
            open_menu(nav)
            bounds(button(close).last, width, height, True)
            expect(button(history)).to_be_visible()
            # In a short landscape the list must scroll, keeping close reachable.
            button(artifact).scroll_into_view_if_needed()
            bounds(button(artifact), width, height, True)
            bounds(button(close).last, width, height, True)
            if screen == "chat":
                snap(f"after-menu-{width}x{height}-{locale}-{scale}.png")
            close_menu(close)
            assert fixture("return f.getState().page;") == screen
            results["matrix"].append({"page": screen, "size": [width, height], "locale": locale, "theme": theme, "fontScale": scale, "safeInsets": [top, bottom], "result": "pass"})
    check("20 actual screen/layout variants: menu reachable, scrollable, close reachable, original page restored")

    load(query="theme=dark&left=24&right=24", width=360)
    # The available width, including safe-area padding, selects the compact brand.
    expect(page.get_by_text("V8 Agent OS", exact=True)).to_have_count(0)
    bounds(button("导航"), 360, 640, True)
    snap("after-notch-360.png")
    check("dark wordmark respects left/right safe areas without overlapping actions")

    load()
    button("切换语言").click()
    page.get_by_text("English", exact=True).click()
    expect(button("Navigation")).to_be_visible()
    button("Switch theme").click()
    assert fixture("return f.getState().themeMode;") == "dark"
    open_menu("Navigation")
    close_menu("Close")
    check("language and theme controls still operate and menu follows the selected locale")

    load()
    draft = "草稿保持 · Same session input"
    page.get_by_label("Fixture chat draft").fill(draft)
    open_menu()
    # Clicking inert heading must not invoke backdrop; item clicks also cannot do so.
    page.get_by_role("heading", name="导航", exact=True).click()
    expect(button("设置")).to_be_visible()
    fixture("f.setDraftMode('hold');f.resetRecords();")
    page.evaluate("() => {const b=[...document.querySelectorAll('[role=button]')].find(e=>e.textContent==='设置'); b.click(); b.click();}")
    assert fixture("return f.records.flushCalls;") == 1
    expect(button("设置")).to_be_disabled()
    expect(page.get_by_text("正在保存草稿…", exact=True)).to_be_visible()
    assert fixture("return f.records.routes.length;") == 0
    close_menu()
    fixture("f.resolveFlush();")
    settle()
    assert fixture("return f.records.routes.length;") == 0
    expect(page.get_by_label("Fixture chat draft")).to_have_value(draft)
    check("double click is single-flight; close cancels late navigation; body/draft retained")

    for boundary in ["escape", "focus", "background", "authority"]:
        load()
        fixture("f.setDraftMode('hold');")
        open_menu()
        button("设置").click()
        if boundary == "escape":
            page.keyboard.press("Escape")
        elif boundary == "focus":
            fixture("f.setFocus(false);")
        elif boundary == "background":
            fixture("f.patch({visible:false});")
        else:
            fixture("f.setAuthority('B');")
        fixture("f.resolveFlush();")
        settle()
        assert fixture("return f.records.routes.length;") == 0
        expect(button("设置")).to_have_count(0)
        check(boundary + " invalidates delayed save navigation")

    load()
    fixture("f.setDraftMode('reject');")
    open_menu()
    button("设置").click()
    expect(page.get_by_role("alert")).to_have_text("Fixture draft write failed")
    snap("after-menu-save-failure.png")
    fixture("f.setDraftMode('immediate');")
    button("设置").click()
    page.wait_for_function("window.phoneNavigationFixture.getState().page==='settings'")
    open_menu()
    expect(page.get_by_role("alert")).to_have_count(0)
    button("返回聊天").click()
    settle()
    assert fixture("return f.records.routes;") == [{"method": "navigate", "target": "/settings"}, {"method": "dismissTo", "target": "/chat"}]
    check("failed save keeps menu and error; successful retry navigates; chat return uses dismissTo")

    for screen, label in [("sessions", "历史会话"), ("connect", "连接与设备"), ("settings", "设置")]:
        load()
        page.get_by_label("Fixture chat draft").fill(draft)
        open_menu()
        button(label).click()
        page.wait_for_function("s=>window.phoneNavigationFixture.getState().page===s", arg=screen)
        open_menu()
        button("返回聊天").click()
        expect(page.get_by_label("Fixture chat draft")).to_have_value(draft)
    check("history/devices/settings menu navigation round trips keep fixture chat draft")

    for width, height, scale, theme in [(320, 640, 1, "light"), (640, 320, 1.8, "dark")]:
        load(query=f"fontScale={scale}&theme={theme}&top=24&bottom=16", width=width, height=height)
        open_menu()
        button("启动自动流程").click()
        expect(page.get_by_text("选择模板", exact=True)).to_be_visible()
        settle()
        bounds(button("关闭").last, width, height, True)
        # Keep the real embedded template UI reachable by scrolling its own content.
        # Query the template field from the observed form, excluding hidden chat input.
        field = page.locator('input').last
        field.scroll_into_view_if_needed()
        field.fill("Fixture device B")
        expect(button("关闭").last).to_be_visible()
        snap(f"after-rpa-{width}x{height}.png")
        page.keyboard.press("Escape")
        settle()
        assert fixture("return f.records.routes.length;") == 0
        expect(page.get_by_label("Fixture chat draft")).to_be_visible()
        expect(button("导航")).to_be_focused()
    check("RPA remains a real embedded overlay; field edit does not dismiss; landscape scroll and Escape restore chat")

    load()
    open_menu()
    button("个人中心").click()
    settle()
    expect(page.get_by_text("个人中心", exact=True)).to_be_visible()
    bounds(button("关闭").last, 320, 640, True)
    snap("after-profile-320.png")
    page.keyboard.press("Escape")
    settle()
    assert fixture("return f.records.routes.length;") == 0
    expect(page.get_by_label("Fixture chat draft")).to_be_visible()
    expect(button("导航")).to_be_focused()
    check("personal center with avatar/background controls remains reachable and closes via Modal")

    load()
    fixture("f.patch({desktopDisabled:true});")
    expect(button("桌面实时预览")).to_be_disabled()
    open_menu()
    close_menu()
    assert fixture("return f.records.desktop;") == 0
    check("disabled desktop preview keeps its 44px target and menu remains enabled")
    assert not errors, errors
    results["pageErrors"] = errors
    (output / "ui-results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    browser.close()
server.shutdown()
print(json.dumps({"checks": len(results["checks"]), "matrix": len(results["matrix"]), "pageErrors": 0}))
