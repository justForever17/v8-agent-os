import os
import tempfile
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


WEB_BASE_URL = os.environ.get("V8_WEB_BASE_URL", "http://127.0.0.1:9527/chat")
ARTIFACT_DIR = Path(os.environ.get(
    "V8_MOTION_ARTIFACT_DIR",
    Path(tempfile.gettempdir()) / "v8os-motion-e2e",
))


def read_motion_styles(page, label):
    page.goto(WEB_BASE_URL, wait_until="domcontentloaded", timeout=120_000)
    page.wait_for_load_state("load", timeout=120_000)
    try:
        page.wait_for_load_state("networkidle", timeout=5_000)
        network_idle_observed = True
    except PlaywrightTimeoutError:
        # The chat route keeps realtime requests open, so global network-idle is not a valid readiness gate.
        network_idle_observed = False
    page.wait_for_selector("body", timeout=120_000)
    page.wait_for_function("document.styleSheets.length > 0", timeout=30_000)
    page.evaluate("document.fonts.ready")
    page.evaluate("() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    styles = page.evaluate(
        """
        () => {
          const fixture = document.createElement("div");
          fixture.id = "motion-policy-e2e";
          fixture.className = "animate-spin";
          fixture.style.transition = "transform 2s ease";
          fixture.style.scrollBehavior = "smooth";
          document.body.appendChild(fixture);
          const style = getComputedStyle(fixture);
          return {
            mediaMatches: matchMedia("(prefers-reduced-motion: reduce)").matches,
            animationName: style.animationName,
            animationDuration: style.animationDuration,
            animationIterationCount: style.animationIterationCount,
            transitionDuration: style.transitionDuration,
            scrollBehavior: style.scrollBehavior,
          };
        }
        """
    )
    styles["networkIdleObserved"] = network_idle_observed
    page.screenshot(path=ARTIFACT_DIR / f"{label}.png", full_page=True)
    return styles


def duration_ms(value: str) -> float:
    first = value.split(",", 1)[0].strip()
    if first.endswith("ms"):
        return float(first[:-2])
    if first.endswith("s"):
        return float(first[:-1]) * 1000
    raise AssertionError(f"Unexpected CSS duration: {value}")


def main() -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True, args=["--no-proxy-server"])

        reduced_context = browser.new_context(reduced_motion="reduce")
        reduced_page = reduced_context.new_page()
        reduced_console_errors = []
        reduced_page_errors = []
        reduced_page.on("console", lambda message: reduced_console_errors.append(message.text) if message.type == "error" else None)
        reduced_page.on("pageerror", lambda error: reduced_page_errors.append(str(error)))
        reduced = read_motion_styles(reduced_page, "reduced-motion")
        assert reduced["mediaMatches"] is True, reduced
        assert duration_ms(reduced["animationDuration"]) <= 0.01, reduced
        assert reduced["animationIterationCount"] == "1", reduced
        assert duration_ms(reduced["transitionDuration"]) <= 0.01, reduced
        assert reduced["scrollBehavior"] == "auto", reduced
        assert not reduced_page_errors, reduced_page_errors
        reduced_context.close()

        standard_context = browser.new_context(reduced_motion="no-preference")
        standard_page = standard_context.new_page()
        standard_console_errors = []
        standard_page_errors = []
        standard_page.on("console", lambda message: standard_console_errors.append(message.text) if message.type == "error" else None)
        standard_page.on("pageerror", lambda error: standard_page_errors.append(str(error)))
        standard = read_motion_styles(standard_page, "standard-motion")
        assert standard["mediaMatches"] is False, standard
        assert standard["animationName"] != "none", standard
        assert duration_ms(standard["animationDuration"]) >= 500, standard
        assert not standard_page_errors, standard_page_errors
        standard_context.close()

        browser.close()

    print({
        "reduced": reduced,
        "standard": standard,
        "consoleErrors": reduced_console_errors + standard_console_errors,
        "screenshots": str(ARTIFACT_DIR),
    })


if __name__ == "__main__":
    main()
