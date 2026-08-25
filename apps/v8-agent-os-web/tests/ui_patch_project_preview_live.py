from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


def _wait_for_page(page) -> None:
    page.wait_for_load_state("domcontentloaded", timeout=30_000)
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except PlaywrightTimeoutError:
        # The chat shell keeps realtime channels open after the document is ready.
        pass


def _authenticate_if_needed(page) -> None:
    password = page.locator('input[type="password"]')
    if password.count() == 0:
        return
    username = page.locator('input[type="text"], input[type="email"]').first
    username.fill(input("Username: ").strip())
    password.first.fill(getpass.getpass("Password: "))
    submit = page.locator('button[type="submit"]').first
    if submit.count() == 0:
        raise RuntimeError("Authentication form has no submit button")
    submit.click()
    page.wait_for_timeout(500)
    _wait_for_page(page)
    if page.locator('input[type="password"]').count():
        raise RuntimeError("Web authentication did not complete")


def run(base_url: str, output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    console_errors: list[str] = []
    page_errors: list[str] = []
    with sync_playwright() as playwright:
        edge = Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")
        browser = playwright.chromium.launch(
            executable_path=str(edge) if edge.is_file() else None,
            headless=True,
            args=["--no-proxy-server"],
        )
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.goto(f"{base_url.rstrip('/')}/chat", wait_until="domcontentloaded", timeout=30_000)
        _wait_for_page(page)
        _authenticate_if_needed(page)

        page.goto(
            f"{base_url.rstrip('/')}/ui-patch?sessionId=ui-patch-visual-dry-run",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        _wait_for_page(page)
        body_text = page.locator("body").inner_text()
        if not ({"项目工作区", "Project workspace"} & set(body_text.splitlines())):
            raise RuntimeError("Project workspace mode is not visible")
        project_tab = page.get_by_role("button", name="项目工作区")
        if project_tab.count() == 0:
            project_tab = page.get_by_role("button", name="Project workspace")
        project_tab.click()
        page.wait_for_timeout(150)
        body_text = page.locator("body").inner_text()
        if not ({"项目目录或源码文件", "Project path or source file"} & set(body_text.splitlines())):
            raise RuntimeError("Project path field is not visible")
        if not ({"打开工作台", "Open workbench"} & set(body_text.splitlines())):
            raise RuntimeError("Workbench start action is not visible")
        desktop_overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        page.screenshot(path=str(output_dir / "ui-patch-project-desktop.png"), full_page=True)

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(250)
        mobile_overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
        page.screenshot(path=str(output_dir / "ui-patch-project-mobile.png"), full_page=True)
        browser.close()

    return {
        "ok": not desktop_overflow and not mobile_overflow and not page_errors,
        "desktopHorizontalOverflow": desktop_overflow,
        "mobileHorizontalOverflow": mobile_overflow,
        "consoleErrors": console_errors,
        "pageErrors": page_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the live V8OS project UI workbench visual smoke")
    parser.add_argument("--live", action="store_true", help="Explicitly use the running local V8OS Web product")
    parser.add_argument("--base-url", default="http://127.0.0.1:9527")
    parser.add_argument("--output-dir", type=Path, default=Path.cwd() / ".tmp" / "ui-patch-live")
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("Refusing to connect to the local product without --live")
    result = run(args.base_url, args.output_dir.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
