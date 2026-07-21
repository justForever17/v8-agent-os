import os
from urllib.parse import urlsplit

from playwright.sync_api import Page, sync_playwright


WEB_BASE_URL = os.environ.get("V8_WEB_BASE_URL", "http://127.0.0.1:9627/chat")
ADMIN_BASE_URL = os.environ.get("V8_ADMIN_BASE_URL", "http://127.0.0.1:9628/admin")


def wait_for_app(page: Page, url: str) -> None:
    page.goto(url, wait_until="commit", timeout=120_000)
    page.wait_for_selector("body", timeout=120_000)
    parsed_url = urlsplit(page.url)
    page.context.grant_permissions(
        ["clipboard-read", "clipboard-write"],
        origin=f"{parsed_url.scheme}://{parsed_url.netloc}",
    )
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        # Chat surfaces may keep a realtime request open after the UI is ready.
        pass
    page.wait_for_function("document.documentElement.dataset.v8ContextMenu === 'ready'", timeout=30_000)


def inject_surface_fixture(page: Page, include_workbench: bool) -> None:
    page.evaluate(
        """
        ({ includeWorkbench }) => {
          document.querySelector('[data-context-menu-e2e]')?.remove();
          const root = document.createElement('section');
          root.dataset.contextMenuE2e = 'true';
          root.style.cssText = 'position:fixed;left:320px;top:100px;z-index:50;padding:24px;background:white;color:black';
          root.innerHTML = `
            <p id="context-copy">Selectable context menu text</p>
            <textarea id="context-input">alpha beta</textarea>
            <a id="context-link" href="https://example.com/context">Context link</a>
            <div id="context-owned">Existing custom menu</div>
          `;
          const owned = root.querySelector('#context-owned');
          owned.addEventListener('contextmenu', (event) => {
            event.preventDefault();
            owned.dataset.opened = 'true';
          });
          if (includeWorkbench) {
            const resource = document.createElement('div');
            resource.dataset.v8ContextResource = '';
            resource.innerHTML = '<span id="context-resource">Workspace resource</span><button data-v8-context-open-workbench type="button">Open</button>';
            resource.querySelector('button').addEventListener('click', () => { resource.dataset.opened = 'true'; });
            root.appendChild(resource);
          }
          document.body.appendChild(root);
        }
        """,
        {"includeWorkbench": include_workbench},
    )


def select_text(page: Page, selector: str) -> None:
    page.evaluate(
        """
        (selector) => {
          const node = document.querySelector(selector);
          const range = document.createRange();
          range.selectNodeContents(node);
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
        }
        """,
        selector,
    )


def open_context_menu(page: Page, selector: str) -> None:
    page.evaluate(
        """
        (selector) => {
          const event = new MouseEvent('contextmenu', {
            bubbles: true,
            cancelable: true,
            button: 2,
            buttons: 2,
            clientX: 360,
            clientY: 130,
          });
          document.querySelector(selector).dispatchEvent(event);
        }
        """,
        selector,
    )


def menu_labels(page: Page) -> list[str]:
    return [label.strip() for label in page.get_by_role("menuitem").all_inner_texts()]


def menu_item(page: Page, *labels: str):
    for label in labels:
        item = page.get_by_role("menuitem", name=label, exact=True)
        if item.count():
            return item
    raise AssertionError(f"Missing menu item: {labels}; got {menu_labels(page)}")


def assert_common_surface(page: Page, include_workbench: bool) -> None:
    inject_surface_fixture(page, include_workbench)
    select_text(page, "#context-copy")
    prevented = page.evaluate(
        """
        () => {
          const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 360, clientY: 130 });
          document.querySelector('#context-copy').dispatchEvent(event);
          return event.defaultPrevented;
        }
        """
    )
    assert prevented, "AppContextMenu did not claim selectable text"
    page.get_by_role("menu").wait_for(state="visible")
    labels = menu_labels(page)
    assert any(label in labels for label in ("Copy", "复制")), labels
    assert any(label in labels for label in ("Select all", "全选")), labels
    menu_item(page, "Copy", "复制").click()
    assert page.evaluate("navigator.clipboard.readText()") == "Selectable context menu text"

    page.locator("#context-input").evaluate("node => node.setSelectionRange(0, 5)")
    open_context_menu(page, "#context-input")
    page.get_by_role("menu").wait_for(state="visible")
    labels = menu_labels(page)
    for expected in (("Cut", "剪切"), ("Copy", "复制"), ("Paste", "粘贴"), ("Select all", "全选")):
        assert any(label in labels for label in expected), labels
    menu_item(page, "Cut", "剪切").click()
    page.wait_for_function("document.querySelector('#context-input').value === ' beta'")
    assert page.locator("#context-input").input_value() == " beta"
    assert page.evaluate("navigator.clipboard.readText()") == "alpha"

    page.evaluate("navigator.clipboard.writeText('gamma')")
    page.locator("#context-input").evaluate("node => node.setSelectionRange(0, 0)")
    open_context_menu(page, "#context-input")
    page.get_by_role("menu").wait_for(state="visible")
    menu_item(page, "Paste", "粘贴").click()
    page.wait_for_function("document.querySelector('#context-input').value === 'gamma beta'")
    pasted_value = page.locator("#context-input").input_value()
    assert pasted_value == "gamma beta", pasted_value

    page.locator("#context-link").click(button="right")
    page.get_by_role("menu").wait_for(state="visible")
    labels = menu_labels(page)
    assert any(label in labels for label in ("Copy link", "复制链接")), labels
    menu_item(page, "Copy link", "复制链接").click()
    assert page.evaluate("navigator.clipboard.readText()") == "https://example.com/context"

    page.locator("#context-owned").click(button="right")
    assert page.locator("#context-owned").get_attribute("data-opened") == "true"
    assert page.get_by_role("menu").count() == 0

    if include_workbench:
        page.locator("#context-resource").click(button="right")
        page.get_by_role("menu").wait_for(state="visible")
        labels = menu_labels(page)
        workbench_label = next(label for label in labels if label in ("View in workbench", "在工作台查看"))
        page.get_by_role("menuitem", name=workbench_label).click()
        assert page.locator("[data-v8-context-resource]").get_attribute("data-opened") == "true"


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True, args=["--no-proxy-server"])
        context = browser.new_context(locale="zh-CN")
        for origin in ("http://127.0.0.1:9627", "http://127.0.0.1:9628"):
            context.grant_permissions(["clipboard-read", "clipboard-write"], origin=origin)
        web_page = context.new_page()
        wait_for_app(web_page, WEB_BASE_URL)
        assert_common_surface(web_page, include_workbench=True)

        admin_page = context.new_page()
        wait_for_app(admin_page, ADMIN_BASE_URL)
        assert_common_surface(admin_page, include_workbench=False)
        browser.close()


if __name__ == "__main__":
    main()
