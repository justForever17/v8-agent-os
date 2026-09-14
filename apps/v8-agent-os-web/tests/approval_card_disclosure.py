"""Exercise the real ApprovalCard markup and native disclosure without a service/model."""
import argparse
import json
import subprocess
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]
URL = "https://approval-card.test/"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline-ref", help="Render an old card without changing the checkout")
    parser.add_argument("--expect-old-failure", action="store_true")
    args = parser.parse_args()
    command = ["node", "tests/approval-card-fixture.cjs"]
    if args.baseline_ref:
        command.append(args.baseline_ref)
    fixture = json.loads(subprocess.check_output(command, cwd=APP).decode("utf-8"))
    output = REPO / "tmp/approval-card-disclosure"
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        try:
            for touch, dark in [(False, False), (True, True)]:
                context = browser.new_context(
                    viewport={"width": 390 if touch else 900, "height": 820},
                    has_touch=touch, is_mobile=touch,
                    permissions=["clipboard-read", "clipboard-write"],
                )
                unexpected_requests = []
                def route(request):
                    if request.request.url == URL:
                        request.fulfill(status=200, content_type="text/html", body=fixture["html"])
                    else:
                        unexpected_requests.append(request.request.url)
                        request.abort()
                context.route("**/*", route)
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(URL, wait_until="networkidle")
                light_background = page.locator("body").evaluate("element => getComputedStyle(element).backgroundColor")
                if dark:
                    page.locator("html").evaluate("element => element.classList.add('dark')")
                    assert page.locator("body").evaluate("element => getComputedStyle(element).backgroundColor") != light_background
                card = page.locator('[data-approval-card="compact"]')
                summary = card.locator("summary")
                if args.expect_old_failure:
                    assert summary.count() == 0, "The old inaccessible card must lack a disclosure"
                    assert "FULL_PROBLEM_END" not in card.inner_text()
                    results.append({"baseline": args.baseline_ref, "expectedFailure": "Full problem unavailable to click/keyboard"})
                    context.close()
                    break
                expect(summary).to_be_visible()
                expect(card).not_to_have_attribute("open", "")
                full = card.locator("p")
                expect(full).to_be_hidden()
                collapsed_height = card.bounding_box()["height"]
                assert collapsed_height < 100, "Default card must remain compact even for long issues"
                if touch:
                    summary.tap()
                else:
                    summary.click()
                expect(card).to_have_attribute("open", "")
                expect(full).to_be_visible()
                assert full.text_content() == fixture["body"]
                for key, value in fixture["eventSummary"].items():
                    row = card.locator("dl > div").filter(has=page.locator("dt", has_text=key))
                    assert row.locator("dd").text_content() == value
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Long issue and path must wrap"
                assert full.evaluate("element => getComputedStyle(element).userSelect") != "none"
                # The browser's real copy command must receive the exact selected
                # full text, not the summary/title or a separately rebuilt string.
                full.evaluate("element => {const range=document.createRange();range.selectNodeContents(element);const selection=getSelection();selection.removeAllRanges();selection.addRange(range)}")
                assert page.evaluate("getSelection().toString()") == fixture["body"]
                page.keyboard.press("Control+c")
                copied = page.evaluate("navigator.clipboard.readText()")
                assert copied.replace("\r\n", "\n") == fixture["body"], repr(copied[:120])
                page.evaluate("getSelection().removeAllRanges()")
                summary.scroll_into_view_if_needed()
                if touch:
                    summary.tap()
                else:
                    summary.click()
                expect(full).to_be_hidden()
                expect(card).not_to_have_attribute("open", "")
                summary.focus()
                expect(summary).to_be_focused()
                for key in ["Enter", "Space"]:
                    summary.press(key)
                    expect(full).to_be_visible()
                    summary.press(key)
                    expect(full).to_be_hidden()
                assert abs(card.bounding_box()["height"] - collapsed_height) < 1
                assert errors == [] and unexpected_requests == []
                results.append({"touch": touch, "dark": dark, "collapsedHeight": collapsed_height, "fullTextCopied": True, "mouseOrTouchToggle": True, "keyboardEnterAndSpaceToggle": True, "errors": errors})
                page.screenshot(path=str(output / ("touch-dark.png" if touch else "desktop-light.png")))
                context.close()
        finally:
            browser.close()
    label = "baseline-result.json" if args.expect_old_failure else "result.json"
    (output / label).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
