import fs from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);

function argValue(name) {
  const index = process.argv.indexOf(name);
  if (index < 0 || index + 1 >= process.argv.length) return "";
  return process.argv[index + 1];
}

function loadRequest() {
  const requestFile = argValue("--request-file");
  if (!requestFile) throw new Error("--request-file is required");
  const request = JSON.parse(fs.readFileSync(requestFile, "utf8"));
  request.__requestFile = requestFile;
  request.__requestDir = path.dirname(requestFile);
  return request;
}

function loadPlaywright(request) {
  const explicitPackage = process.env.PLAYWRIGHT_DRIVER_PACKAGE || request?.browserAttach?.playwrightDriverPackage || "";
  if (explicitPackage) return require(explicitPackage);
  try {
    return require("playwright");
  } catch {
    return require("playwright-core");
  }
}

function postJson(url, payload) {
  return new Promise((resolve) => {
    const body = JSON.stringify(payload ?? {});
    const parsed = new URL(url);
    const req = http.request(
      {
        method: "POST",
        hostname: parsed.hostname,
        port: parsed.port || 80,
        path: `${parsed.pathname}${parsed.search}`,
        headers: {
          "content-type": "application/json; charset=utf-8",
          "content-length": Buffer.byteLength(body),
        },
        timeout: 5000,
      },
      (res) => {
        res.resume();
        res.on("end", () => resolve({ ok: res.statusCode >= 200 && res.statusCode < 300, statusCode: res.statusCode }));
      },
    );
    req.on("error", (error) => resolve({ ok: false, error: error.message }));
    req.on("timeout", () => {
      req.destroy(new Error("request timeout"));
    });
    req.end(body);
  });
}

function callbackUrl(request) {
  const explicit = request?.callback?.url;
  if (explicit) return explicit;
  const base = String(request.engineUrl || "http://127.0.0.1:9530").replace(/\/+$/, "");
  return `${base}${request?.callback?.path || ""}`;
}

async function postEvent(request, type, payload = {}) {
  const url = callbackUrl(request);
  const body = {
    type,
    oneTimeToken: request.oneTimeToken,
    ...payload,
  };
  const result = await postJson(url, body);
  if (!result.ok) {
    console.error(`failed to post ${type}: ${result.error || result.statusCode || "unknown"}`);
  }
  return result;
}

async function resolvePage(browser, attach) {
  const targetId = String(attach.targetId || attach.proxyTargetId || "").trim();
  const urlHint = String(attach.url || attach.currentUrl || "").trim();
  const titleHint = String(attach.title || "").trim();
  const pages = browser.contexts().flatMap((context) => context.pages());
  if (!pages.length) throw new Error("Agent Browser has no open page to inspect.");
  if (urlHint) {
    const exact = pages.find((page) => page.url() === urlHint);
    if (exact) return exact;
    const contains = pages.find((page) => page.url() && urlHint.includes(page.url()));
    if (contains) return contains;
  }
  if (titleHint) {
    for (const page of pages) {
      const title = await page.title().catch(() => "");
      if (title && (title === titleHint || titleHint.includes(title) || title.includes(titleHint))) return page;
    }
  }
  if (targetId) {
    const byUrl = pages.find((page) => page.url().includes(targetId));
    if (byUrl) return byUrl;
  }
  return pages[0];
}

function inspectorInstallScript() {
  return `
(() => {
  if (window.__v8RpaInspector && window.__v8RpaInspector.installed) {
    window.__v8RpaInspector.enabled = true;
    return { installed: true, reused: true };
  }
  const cssEscape = (value) => {
    if (window.CSS && CSS.escape) return CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, (ch) => "\\\\" + ch);
  };
  const safeText = (value, max = 120) => String(value || "").replace(/\\s+/g, " ").trim().slice(0, max);
  const cssPath = (el) => {
    if (!el || el.nodeType !== 1) return "";
    if (el.id) return "#" + cssEscape(el.id);
    const testId = el.getAttribute("data-testid") || el.getAttribute("data-test") || el.getAttribute("data-cy");
    if (testId) return el.tagName.toLowerCase() + "[data-testid=\\"" + testId.replace(/"/g, "\\\\\\"") + "\\"]";
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 5) {
      let part = node.tagName.toLowerCase();
      const name = node.getAttribute("name");
      if (name) part += "[name=\\"" + name.replace(/"/g, "\\\\\\"") + "\\"]";
      else {
        let index = 1;
        let sib = node;
        while ((sib = sib.previousElementSibling)) {
          if (sib.tagName === node.tagName) index += 1;
        }
        part += ":nth-of-type(" + index + ")";
      }
      parts.unshift(part);
      node = node.parentElement;
    }
    return parts.join(" > ");
  };
  const xpath = (el) => {
    if (!el || el.nodeType !== 1) return "";
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 8) {
      let index = 1;
      let sib = node;
      while ((sib = sib.previousElementSibling)) {
        if (sib.tagName === node.tagName) index += 1;
      }
      parts.unshift(node.tagName.toLowerCase() + "[" + index + "]");
      node = node.parentElement;
    }
    return "/" + parts.join("/");
  };
  const roleFor = (el) => {
    const explicit = el.getAttribute && (el.getAttribute("role") || el.getAttribute("aria-role"));
    if (explicit) return explicit;
    const tag = String(el.tagName || "").toLowerCase();
    if (tag === "button") return "button";
    if (tag === "a" && el.getAttribute("href")) return "link";
    if (tag === "input") {
      const type = String(el.getAttribute("type") || "text").toLowerCase();
      if (["button", "submit", "reset"].includes(type)) return "button";
      if (["checkbox"].includes(type)) return "checkbox";
      if (["radio"].includes(type)) return "radio";
      return "textbox";
    }
    if (tag === "textarea") return "textbox";
    if (tag === "select") return "combobox";
    return "";
  };
  const selectors = (el) => {
    const result = [];
    const label = el.getAttribute && (el.getAttribute("aria-label") || el.getAttribute("title"));
    const text = safeText(label || el.innerText || el.textContent || el.value, 80);
    const role = roleFor(el);
    if (role && text) result.push({ kind: "role", role, name: text, playwright: "getByRole" });
    if (label) result.push({ kind: "label", name: safeText(label, 80), playwright: "getByLabel" });
    if (text) result.push({ kind: "text", text, playwright: "getByText" });
    const css = cssPath(el);
    const xp = xpath(el);
    if (css) result.push({ kind: "css", css, selector: css });
    if (xp) result.push({ kind: "xpath", xpath: xp });
    return result;
  };
  const overlay = document.createElement("div");
  overlay.setAttribute("data-v8-rpa-inspector-overlay", "true");
  Object.assign(overlay.style, {
    position: "fixed",
    zIndex: "2147483647",
    pointerEvents: "none",
    border: "2px solid #22d3ee",
    boxShadow: "0 0 0 9999px rgba(8,13,24,.12), 0 0 18px rgba(34,211,238,.65)",
    borderRadius: "6px",
    display: "none",
  });
  document.documentElement.appendChild(overlay);
  const queue = [];
  const inspector = window.__v8RpaInspector = {
    installed: true,
    enabled: true,
    drain() {
      const items = queue.splice(0, queue.length);
      return { installed: true, events: items };
    },
  };
  const highlight = (el) => {
    if (!el || !el.getBoundingClientRect) return;
    const rect = el.getBoundingClientRect();
    overlay.style.display = "block";
    overlay.style.left = Math.round(rect.left) + "px";
    overlay.style.top = Math.round(rect.top) + "px";
    overlay.style.width = Math.max(1, Math.round(rect.width)) + "px";
    overlay.style.height = Math.max(1, Math.round(rect.height)) + "px";
  };
  const build = (ev) => {
    const el = ev.target && ev.target.nodeType === 1 ? ev.target : document.activeElement;
    const rect = el && el.getBoundingClientRect ? el.getBoundingClientRect() : { left: 0, top: 0, width: 0, height: 0 };
    const selectorCandidates = selectors(el);
    return {
      label: safeText(el && (el.getAttribute("aria-label") || el.innerText || el.textContent || el.value || el.getAttribute("title")), 120) || "browser element",
      source: "rpa_playwright_node_sidecar",
      platform: "browser",
      action: "click",
      selectorCandidates,
      targetWindow: { title: document.title, url: location.href },
      anchorBundle: {
        window: { title: document.title, url: location.href },
        viewport: { width: window.innerWidth, height: window.innerHeight, devicePixelRatio: window.devicePixelRatio || 1 },
        rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height },
      },
      coordinate: { x: Math.round(ev.clientX || rect.left + rect.width / 2), y: Math.round(ev.clientY || rect.top + rect.height / 2) },
      metadata: {
        tag: el ? el.tagName : "",
        id: el && el.id || "",
        name: el && el.getAttribute && el.getAttribute("name") || "",
        textPreview: safeText(el && (el.innerText || el.textContent || el.value), 160),
      },
    };
  };
  document.addEventListener("mousemove", (ev) => {
    if (!inspector.enabled) return;
    const el = ev.target && ev.target.nodeType === 1 ? ev.target : null;
    if (el && el !== overlay) highlight(el);
  }, true);
  document.addEventListener("click", (ev) => {
    if (!inspector.enabled) return;
    if (!(ev.altKey || ev.ctrlKey || ev.metaKey)) return;
    ev.preventDefault();
    ev.stopPropagation();
    const candidate = build(ev);
    queue.push({ eventId: "browser_" + Date.now() + "_" + Math.random().toString(16).slice(2), recordedAt: new Date().toISOString(), candidate });
  }, true);
  return { installed: true, reused: false };
})()
`;
}

async function countForCandidate(page, candidate) {
  const selectors = Array.isArray(candidate.selectorCandidates) ? candidate.selectorCandidates : [];
  for (const selector of selectors) {
    try {
      if (selector.kind === "role" && selector.role && selector.name) {
        const count = await page.getByRole(selector.role, { name: selector.name }).count();
        return { selector, count, source: "playwright_get_by_role" };
      }
      if (selector.kind === "label" && selector.name) {
        const count = await page.getByLabel(selector.name).count();
        return { selector, count, source: "playwright_get_by_label" };
      }
      if (selector.kind === "text" && selector.text) {
        const count = await page.getByText(selector.text).count();
        return { selector, count, source: "playwright_get_by_text" };
      }
      if (selector.kind === "css" && selector.css) {
        const count = await page.locator(selector.css).count();
        return { selector, count, source: "playwright_css" };
      }
      if (selector.kind === "xpath" && selector.xpath) {
        const count = await page.locator(`xpath=${selector.xpath}`).count();
        return { selector, count, source: "playwright_xpath" };
      }
    } catch {}
  }
  return { selector: selectors[0] || {}, count: 0, source: "playwright_locator_unresolved" };
}

async function highlightAndScreenshot(page, request, selector) {
  try {
    if (selector.kind === "css" && selector.css) {
      await page.locator(selector.css).first().evaluate((el) => {
        el.setAttribute("data-v8-rpa-proof-highlight", "true");
        el.style.outline = "3px solid #22d3ee";
        el.style.outlineOffset = "3px";
      });
    }
    const file = path.join(request.__requestDir, `${request.sessionId}-browser-proof-${Date.now()}.png`);
    await page.screenshot({ path: file, fullPage: false });
    return { ok: true, screenshotRef: file, highlightRef: file };
  } catch (error) {
    return { ok: false, warning: error?.message || String(error) };
  }
}

function candidateWithProof(candidate, countResult, proofShot) {
  const primary = countResult.selector || {};
  const alternates = (candidate.selectorCandidates || []).filter((item) => item !== primary);
  return {
    ...candidate,
    locatorBundle: {
      platform: "browser",
      primaryLocator: primary,
      alternateLocators: alternates,
      searchScope: candidate.targetWindow || {},
      uniqueness: { count: countResult.count, source: countResult.source },
      confidence: countResult.count === 1 ? 0.9 : 0.55,
      source: "rpa_playwright_node_sidecar",
    },
    proof: {
      status: countResult.count === 1 && proofShot.ok ? "verified" : countResult.count > 1 ? "locator_ambiguous" : "locator_unresolved",
      findCount: countResult.count,
      highlightRef: proofShot.highlightRef,
      screenshotRef: proofShot.screenshotRef,
      warnings: proofShot.warning ? [proofShot.warning] : [],
      verifiedAt: new Date().toISOString(),
      verifier: "rpa_playwright_node_sidecar",
    },
  };
}

async function main() {
  const request = loadRequest();
  const attach = request.browserAttach || {};
  if (!attach.cdpEndpoint) throw new Error("browserAttach.cdpEndpoint is required");
  const playwright = loadPlaywright(request);
  const browser = await playwright.chromium.connectOverCDP(attach.cdpEndpoint);
  const page = await resolvePage(browser, attach);
  await page.bringToFront().catch(() => {});
  await page.evaluate(inspectorInstallScript());
  await postEvent(request, "ready", {
    sidecar: { kind: "rpa_playwright_node_sidecar", status: "attached", targetId: attach.targetId, url: page.url() },
  });
  setInterval(() => {
    void postEvent(request, "heartbeat", {
      sidecar: { kind: "rpa_playwright_node_sidecar", status: "attached", targetId: attach.targetId, url: page.url() },
    });
  }, 3000);
  setInterval(async () => {
    try {
      const drained = await page.evaluate(() => window.__v8RpaInspector?.drain?.() || { events: [] });
      for (const raw of drained.events || []) {
        const candidate = raw.candidate || raw;
        const countResult = await countForCandidate(page, candidate);
        const proofShot = await highlightAndScreenshot(page, request, countResult.selector || {});
        await postEvent(request, "candidate", { candidate: candidateWithProof(candidate, countResult, proofShot) });
      }
    } catch (error) {
      await postEvent(request, "error", { error: error?.message || String(error), sidecar: { kind: "rpa_playwright_node_sidecar", status: "poll_failed" } });
    }
  }, 500);
}

main().catch(async (error) => {
  const request = (() => {
    try {
      return loadRequest();
    } catch {
      return {};
    }
  })();
  if (request.callback) await postEvent(request, "error", { error: error?.message || String(error), sidecar: { kind: "rpa_playwright_node_sidecar", status: "failed" } });
  console.error(error?.stack || error?.message || String(error));
  process.exitCode = 1;
});
