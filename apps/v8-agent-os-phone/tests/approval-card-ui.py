"""Actual React Native Web component interaction on an isolated static fixture.
Build with build-approval-card-ui.cjs, then run this script with its output dir.
Does not connect to Phone, Admin or Engine services.
"""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from playwright.sync_api import sync_playwright

fixture = Path(sys.argv[1]).resolve()

class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(fixture)))
threading.Thread(target=server.serve_forever, daemon=True).start()
results = []
try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for theme in ('light', 'dark'):
            for lang in ('zh', 'en'):
                context = browser.new_context(viewport={'width': 390, 'height': 844}, has_touch=True)
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f'http://127.0.0.1:{server.server_port}/?theme={theme}&lang={lang}')
                compact = page.get_by_test_id('compact')
                spec = page.get_by_test_id('spec')
                button = compact.get_by_role('button')
                button.wait_for()
                assert button.get_attribute('aria-expanded') == 'false', button.evaluate('el => el.outerHTML')
                collapsed_height = compact.bounding_box()['height']
                assert collapsed_height < spec.bounding_box()['height']
                spec_height = spec.bounding_box()['height']
                assert compact.get_by_text('RULE_TAIL', exact=True).count() == 0
                assert compact.get_by_text('Safety Guardian', exact=False).count() == 0
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth,sections:[...document.querySelectorAll("section")].map(el=>({box:el.getBoundingClientRect().width,scroll:el.scrollWidth}))})')
                button.tap()
                assert button.get_attribute('aria-expanded') == 'true'
                tail = compact.get_by_text('RULE_TAIL', exact=True)
                tail.scroll_into_view_if_needed()
                assert tail.is_visible()
                next_step = compact.get_by_text('确认授权后继续 NEXT_TAIL', exact=True)
                next_step.scroll_into_view_if_needed()
                assert next_step.is_visible()
                assert next_step.evaluate("el => getComputedStyle(el).userSelect") == 'text'
                selected = next_step.evaluate("el => {const r = document.createRange(); r.selectNodeContents(el); const s = getSelection(); s.removeAllRanges(); s.addRange(r); return s.toString();}")
                assert selected == '确认授权后继续 NEXT_TAIL'
                assert button.get_attribute('aria-expanded') == 'true', 'selection collapsed details'
                body = compact.get_by_text('ISSUE_TAIL', exact=False)
                assert 'ISSUE_TAIL' in body.inner_text()
                assert body.evaluate("el => getComputedStyle(el).webkitLineClamp") in ('none', '')
                page.screenshot(path=str(fixture / f'{theme}-{lang}-expanded.png'), full_page=True)
                button.focus()
                page.keyboard.press('Enter')
                assert button.get_attribute('aria-expanded') == 'false'
                assert compact.get_by_text('RULE_TAIL', exact=True).count() == 0
                assert spec.get_by_text('RULE_TAIL', exact=True).count() == 1
                assert spec.locator('[aria-expanded]').count() == 0
                assert not errors, errors
                button.evaluate('el => el.blur()')
                page.screenshot(path=str(fixture / f'{theme}-{lang}-collapsed.png'), full_page=True)
                results.append({'theme': theme, 'lang': lang, 'collapsedHeight': collapsed_height, 'fullLayoutHeight': spec_height,
                                'touchExpand': True, 'tailFieldsSelectable': True, 'keyboardCollapse': True, 'pageErrors': errors})
                context.close()
        browser.close()
finally:
    server.shutdown()
(fixture / 'result.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
print(json.dumps(results, indent=2))
