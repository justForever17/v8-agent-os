import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import { randomUUID } from "node:crypto";
import { createRequire } from "node:module";
import path from "node:path";
import { pathToFileURL } from "node:url";

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
  if (!request.oneTimeToken || !request.generation) throw new Error("Inspector token and generation are required");
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

export function postJson(url, payload) {
  return new Promise((resolve) => {
    const body = JSON.stringify(payload ?? {});
    const parsed = new URL(url);
    const transport = parsed.protocol === "https:" ? https : http;
    const req = transport.request(
      {
        method: "POST",
        hostname: parsed.hostname,
        port: parsed.port || (parsed.protocol === "https:" ? 443 : 80),
        path: `${parsed.pathname}${parsed.search}`,
        headers: {
          "content-type": "application/json; charset=utf-8",
          "content-length": Buffer.byteLength(body),
        },
        timeout: 5000,
      },
      (res) => {
        let acknowledgement = "";
        res.setEncoding("utf8");
        res.on("data", (chunk) => { if (acknowledgement.length < 1_000_000) acknowledgement += chunk; });
        res.on("end", () => {
          let confirmed = false;
          try { confirmed = JSON.parse(acknowledgement).ok === true; } catch {}
          resolve({ ok: res.statusCode >= 200 && res.statusCode < 300 && confirmed, statusCode: res.statusCode });
        });
      },
    );
    req.on("error", () => resolve({ ok: false, error: "Inspector callback transport failed" }));
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

export function createEventSender(request, send = postJson) {
  let sequence = 0;
  let active = Promise.resolve();
  let stopped = false;
  const queue = [];
  const shouldStop = () => request.__requestFile && (!fs.existsSync(request.__requestFile) || JSON.parse(fs.readFileSync(request.__requestFile, "utf8")).stopRequested);
  return (type, payload = {}) => {
    const action = async () => {
      if (stopped || shouldStop()) return { ok: false, statusCode: 410 };
      let requested = queue.find(item => item.payload === payload || payload.eventId && item.eventId === payload.eventId || type !== "candidate" && item.type === type);
      if (!requested) {
        requested = { type, payload, eventId: payload.eventId || randomUUID(), body: null };
        queue.push(requested);
      }
      while (queue.length) {
        if (stopped || shouldStop()) return { ok: false, statusCode: 410 };
        const item = queue[0];
        item.body ||= structuredClone({ ...item.payload, type: item.type, oneTimeToken: request.oneTimeToken, generation: request.generation, seq: sequence + 1, eventId: item.eventId });
        const result = await send(callbackUrl(request), item.body);
        if (!result.ok) {
          if ([401, 403, 404, 410].includes(result.statusCode)) stopped = true;
          return result;
        }
        queue.shift();
        sequence++;
        if (item === requested) return result;
      }
      return { ok: true };
    };
    const result = active.then(action);
    active = result.catch(() => {});
    return result;
  };
}

const eventSenders = new WeakMap();
async function postEvent(request, type, payload = {}) {
  if (!eventSenders.has(request)) eventSenders.set(request, createEventSender(request));
  return eventSenders.get(request)(type, payload);
}

export async function resolvePage(browser, attach) {
  const targetId = String(attach.targetId || attach.proxyTargetId || "").trim();
  const urlHint = String(attach.url || attach.currentUrl || "").trim();
  const titleHint = String(attach.title || "").trim();
  const matches = [];
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      if (targetId) {
        const cdp = await context.newCDPSession(page);
        try {
          const { targetInfo } = await cdp.send("Target.getTargetInfo");
          if (targetInfo.targetId === targetId) matches.push(page);
        } finally { await cdp.detach(); }
      } else if (urlHint ? page.url() === urlHint : titleHint && await page.title() === titleHint) {
        matches.push(page);
      }
    }
  }
  if (matches.length !== 1) throw new Error(matches.length ? "browser_target_ambiguous" : "browser_target_lost");
  return matches[0];
}

export function inspectorInstallScript(options = {}) {
  const configJson = JSON.stringify({
    captureMode: options.captureMode || "modifier_click",
    instruction: options.captureMode === "next_click" ? "V8 RPA: click the target element to capture it once." : "V8 RPA: hold Alt / Ctrl / Cmd and click to capture.",
  });
  return `
(() => {
  const config = ${configJson};
  if (window.__v8RpaInspector && window.__v8RpaInspector.installed) {
    if (typeof window.__v8RpaInspector.configure === "function") {
      window.__v8RpaInspector.configure(config);
      return { installed: true, reused: true, captureMode: window.__v8RpaInspector.captureMode };
    }
    try {
      window.__v8RpaInspector.enabled = false;
      document.querySelectorAll("[data-v8-rpa-inspector-overlay], [data-v8-rpa-inspector-banner]").forEach((node) => node.remove());
    } catch {}
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
  const banner = document.createElement("div");
  banner.setAttribute("data-v8-rpa-inspector-banner", "true");
  Object.assign(banner.style, {
    position: "fixed",
    zIndex: "2147483647",
    left: "50%",
    top: "18px",
    transform: "translateX(-50%)",
    maxWidth: "min(560px, calc(100vw - 32px))",
    padding: "9px 13px",
    borderRadius: "999px",
    background: "rgba(8,13,24,.92)",
    color: "#e5e7eb",
    border: "1px solid rgba(34,211,238,.45)",
    boxShadow: "0 12px 34px rgba(0,0,0,.35), 0 0 18px rgba(34,211,238,.25)",
    font: "13px/1.35 Arial, sans-serif",
    pointerEvents: "none",
    display: "none",
  });
  document.documentElement.appendChild(banner);
  const queue = [];
  const updateBanner = (text, visible = true) => {
    banner.textContent = text || "";
    banner.style.display = visible && text ? "block" : "none";
  };
  const inspector = window.__v8RpaInspector = {
    installed: true,
    enabled: true,
    captureMode: "modifier_click",
    armed: false,
    capturedCount: 0,
    configure(nextConfig = {}) {
      this.captureMode = String(nextConfig.captureMode || "modifier_click");
      this.enabled = true;
      this.armed = this.captureMode === "next_click";
      this.capturedCount = 0;
      updateBanner(nextConfig.instruction || "", this.armed);
    },
    drain() {
      const items = queue.splice(0, queue.length);
      return { installed: true, events: items };
    },
    dispose() {
      this.enabled = false;
      this.armed = false;
      this.installed = false;
      listenerController.abort();
      overlay.remove();
      banner.remove();
    },
  };
  const listenerController = new AbortController();
  inspector.configure(config);
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
    const elementMarker = "capture_" + Date.now() + "_" + Math.random().toString(16).slice(2);
    el.setAttribute("data-v8-rpa-capture-id", elementMarker);
    return {
      label: safeText(el && (el.getAttribute("aria-label") || el.innerText || el.textContent || el.value || el.getAttribute("title")), 120) || "browser element",
      source: "rpa_playwright_node_sidecar",
      platform: "browser",
      action: "click",
      selectorCandidates,
      elementMarker,
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
  window.addEventListener("mousemove", (ev) => {
    if (!inspector.enabled) return;
    const el = ev.target && ev.target.nodeType === 1 ? ev.target : null;
    if (el && el !== overlay) highlight(el);
  }, { capture: true, signal: listenerController.signal });
  for (const eventName of ["pointerdown", "mousedown", "pointerup", "mouseup"]) {
    window.addEventListener(eventName, (ev) => {
      if (!inspector.enabled || !(inspector.armed || ev.altKey || ev.ctrlKey || ev.metaKey)) return;
      ev.preventDefault();
      ev.stopImmediatePropagation();
    }, { capture: true, signal: listenerController.signal });
  }
  window.addEventListener("click", (ev) => {
    if (!inspector.enabled) return;
    const modifierClick = ev.altKey || ev.ctrlKey || ev.metaKey;
    const nextClick = inspector.captureMode === "next_click" && inspector.armed;
    if (!(nextClick || modifierClick)) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    const candidate = build(ev);
    candidate.metadata = { ...(candidate.metadata || {}), captureMode: inspector.captureMode, nextClickCapture: nextClick };
    queue.push({ eventId: "browser_" + Date.now() + "_" + Math.random().toString(16).slice(2), recordedAt: new Date().toISOString(), candidate });
    inspector.capturedCount += 1;
    if (nextClick) {
      inspector.armed = false;
      inspector.enabled = false;
      updateBanner("V8 RPA: target captured. Return to Studio to review proof.", true);
      window.setTimeout(() => updateBanner("", false), 1400);
    }
  }, { capture: true, signal: listenerController.signal });
  return { installed: true, reused: false, captureMode: inspector.captureMode };
})()
`;
}

function locatorFor(page, selector) {
  if (selector.kind === "role" && selector.role) return page.getByRole(selector.role, { name: selector.name, exact: true });
  if (selector.kind === "label") return page.getByLabel(selector.name, { exact: true });
  if (selector.kind === "text") return page.getByText(selector.text, { exact: true });
  if (selector.kind === "css" && selector.css) return page.locator(selector.css);
  if (selector.kind === "xpath" && selector.xpath) return page.locator(`xpath=${selector.xpath}`);
  return null;
}

export async function countForCandidate(page, candidate) {
  const selectors = [...(candidate.selectorCandidates || [])].sort((a, b) => Number(b.kind === "css") - Number(a.kind === "css"));
  let last = { selector: {}, count: 0, source: "playwright_locator_unresolved" };
  for (const selector of selectors) {
    const locator = locatorFor(page, selector);
    if (!locator) continue;
    const count = await locator.count();
    last = { selector, count, source: "playwright_live_locator" };
    if (count === 1 && candidate.elementMarker && await locator.evaluate((el, marker) => el.getAttribute("data-v8-rpa-capture-id") === marker, candidate.elementMarker)) return last;
  }
  return { ...last, sameElement: false };
}

async function highlightAndScreenshot(page, request, selector) {
  const locator = locatorFor(page, selector);
  if (!locator || await locator.count() !== 1) return { ok: false };
  let previous;
  try {
    previous = await locator.evaluate((el) => {
      const style = el.getAttribute("style");
      el.style.outline = "3px solid #22d3ee";
      el.style.outlineOffset = "3px";
      return style;
    });
    const file = path.join(request.__requestDir, `${request.sessionId}-browser-proof-${Date.now()}.png`);
    await page.screenshot({ path: file, fullPage: false });
    return { ok: true, screenshotRef: file, highlightRef: file };
  } catch (error) {
    return { ok: false, warning: error?.message || String(error) };
  } finally {
    if (previous !== undefined) await locator.evaluate((el, style) => style === null ? el.removeAttribute("style") : el.setAttribute("style", style), previous).catch(() => {});
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
      status: countResult.count === 1 && countResult.sameElement !== false && proofShot.ok ? "captured" : countResult.count > 1 ? "locator_ambiguous" : "locator_unresolved",
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
  const captureMode = String(request.captureMode || "modifier_click");
  await page.evaluate(inspectorInstallScript({ captureMode }));
  await postEvent(request, "ready", {
    sidecar: { kind: "rpa_playwright_node_sidecar", status: "attached", targetId: attach.targetId, url: page.url(), captureMode },
  });
  const pending = [];
  let heartbeatAt = Date.now();
  try {
    while (!page.isClosed()) {
      if (!fs.existsSync(request.__requestFile) || JSON.parse(fs.readFileSync(request.__requestFile, "utf8")).stopRequested) break;
      // Navigation replaces the document; reinstall only this page's observer.
      if (!await page.evaluate(() => Boolean(window.__v8RpaInspector?.installed))) {
        await page.evaluate(inspectorInstallScript({ captureMode }));
      }
      if (!pending.length) {
        const drained = await page.evaluate(() => window.__v8RpaInspector?.drain?.() || { events: [] });
        for (const raw of drained.events || []) {
          const candidate = raw.candidate || raw;
          const countResult = await countForCandidate(page, candidate);
          const proofShot = countResult.sameElement === false ? { ok: false } : await highlightAndScreenshot(page, request, countResult.selector || {});
          pending.push({ eventId: raw.eventId, candidate: candidateWithProof(candidate, countResult, proofShot) });
        }
      }
      if (pending.length) {
        const result = await postEvent(request, "candidate", pending[0]);
        if (result.ok) pending.shift();
        else if ([403, 404, 410].includes(result.statusCode)) break;
      } else if (Date.now() - heartbeatAt >= 3000) {
        const result = await postEvent(request, "heartbeat");
        if ([403, 404, 410].includes(result.statusCode)) break;
        if (result.ok) heartbeatAt = Date.now();
      }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  } finally {
    await page.evaluate(() => window.__v8RpaInspector?.dispose?.()).catch(() => {});
    // Exit closes this CDP connection; never close the user's browser or page.
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) main().then(() => process.exit(0)).catch(() => {
  console.error("Browser inspector stopped after an unconfirmed error; inspect its runtime session before restarting.");
  process.exitCode = 1;
});
