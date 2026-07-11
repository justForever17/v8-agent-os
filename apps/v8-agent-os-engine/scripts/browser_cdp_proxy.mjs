import http from "node:http";
import { createRequire } from "node:module";
import { URL } from "node:url";

const require = createRequire(import.meta.url);
const proxyPort = Number.parseInt(process.env.CDP_PROXY_PORT || "3456", 10);
const targetPort = Number.parseInt(process.env.CDP_TARGET_PORT || "9222", 10);
const targetEndpoint = process.env.CDP_TARGET_ENDPOINT || `http://127.0.0.1:${targetPort}`;

let playwrightModule = null;
let playwrightError = null;
try {
  const explicitPackage = process.env.PLAYWRIGHT_DRIVER_PACKAGE || "";
  if (explicitPackage) {
    playwrightModule = require(explicitPackage);
  }
  if (!playwrightModule) {
    try {
      playwrightModule = require("playwright");
    } catch {
      playwrightModule = require("playwright-core");
    }
  }
} catch (error) {
  playwrightError = error;
}

let browser = null;
let browserError = null;
let pageCounter = 0;
const pages = new Map();
const pageIds = new WeakMap();
const cdpSessions = new Map();
const screencasts = new Map();

function sendJson(res, status, payload) {
  const body = JSON.stringify(payload ?? {}, null, 2);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  res.end(body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

async function pageTargetInfo(page) {
  const session = await cdpSessionForPage(page);
  const result = await session.send("Target.getTargetInfo").catch(() => null);
  return result?.targetInfo || null;
}

async function pageId(page) {
  const cached = pageIds.get(page);
  if (cached) return cached;
  const targetInfo = await pageTargetInfo(page).catch(() => null);
  const id = String(targetInfo?.targetId || `page-fallback-${++pageCounter}`);
  pageIds.set(page, id);
  pages.set(id, page);
  page.once("close", () => {
    pages.delete(id);
    const session = cdpSessions.get(id);
    cdpSessions.delete(id);
    screencasts.delete(id);
    void session?.detach?.().catch(() => {});
  });
  return id;
}

async function cdpSessionForPage(page) {
  const cachedId = pageIds.get(page);
  if (cachedId && cdpSessions.has(cachedId)) return cdpSessions.get(cachedId);
  const session = await page.context().newCDPSession(page);
  const result = await session.send("Target.getTargetInfo").catch(() => null);
  const targetId = String(result?.targetInfo?.targetId || cachedId || `page-fallback-${++pageCounter}`);
  pageIds.set(page, targetId);
  pages.set(targetId, page);
  cdpSessions.set(targetId, session);
  return session;
}

async function ensureBrowser() {
  if (!playwrightModule) {
    throw new Error(`Playwright dependency is not available: ${playwrightError?.message || "missing module"}`);
  }
  if (browser && browser.isConnected()) return browser;
  try {
    browser = await playwrightModule.chromium.connectOverCDP(targetEndpoint);
    browserError = null;
    for (const context of browser.contexts()) {
      for (const page of context.pages()) await pageId(page);
    }
    browser.on("disconnected", () => {
      browser = null;
    });
    return browser;
  } catch (error) {
    browser = null;
    browserError = error;
    throw error;
  }
}

async function listPages() {
  const connected = await ensureBrowser();
  const result = [];
  for (const context of connected.contexts()) {
    for (const page of context.pages()) {
      const id = await pageId(page);
      const targetInfo = await pageTargetInfo(page).catch(() => null);
      result.push({
        targetId: id,
        id,
        title: await page.title().catch(() => ""),
        url: page.url(),
        openerId: targetInfo?.openerId || null,
      });
    }
  }
  return result;
}

async function getPage(targetId) {
  const explicit = String(targetId || "").trim();
  await ensureBrowser();
  if (explicit && pages.has(explicit)) return pages.get(explicit);
  const targets = await listPages();
  if (explicit) {
    const found = targets.find((item) => item.targetId === explicit || item.id === explicit);
    if (found && pages.has(found.targetId)) return pages.get(found.targetId);
    throw new Error(`target_not_found: ${explicit}`);
  }
  if (targets.length && pages.has(targets[0].targetId)) return pages.get(targets[0].targetId);
  return createPage("about:blank");
}

async function createPage(url) {
  const connected = await ensureBrowser();
  const context = connected.contexts()[0] || await connected.newContext();
  const nextUrl = String(url || "about:blank").trim() || "about:blank";
  let page = null;
  if (nextUrl !== "about:blank") {
    const blank = context.pages().find((candidate) => {
      const current = String(candidate.url() || "").trim();
      return current === "" || current === "about:blank";
    });
    if (blank) page = blank;
  }
  if (!page) page = await context.newPage();
  const id = await pageId(page);
  if (nextUrl !== "about:blank") {
    await page.goto(nextUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
  }
  return page;
}

async function maximizePageWindow(page) {
  await page.bringToFront().catch(() => {});
  try {
    const session = await page.context().newCDPSession(page);
    const info = await session.send("Browser.getWindowForTarget").catch(() => null);
    if (info?.windowId !== undefined) {
      await session.send("Browser.setWindowBounds", {
        windowId: info.windowId,
        bounds: { windowState: "maximized" },
      });
      return { maximized: true, method: "Browser.setWindowBounds", windowId: info.windowId };
    }
  } catch {}
  await page.setViewportSize({ width: 1600, height: 1000 }).catch(() => {});
  return { maximized: false, method: "viewport_fallback", viewport: { width: 1600, height: 1000 } };
}

function summarizeElement(element, index) {
  return {
    index,
    tag: element.tagName,
    text: (element.innerText || element.textContent || element.value || "").trim().slice(0, 200),
    ariaLabel: element.getAttribute("aria-label"),
    title: element.getAttribute("title"),
    href: element.href || null,
    id: element.id || null,
    name: element.name || null,
    role: element.getAttribute("role"),
  };
}

async function pageInfo(page) {
  return page.evaluate((summarizeSource) => {
    const summarize = eval(`(${summarizeSource})`);
    const buttons = Array.from(document.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit']")).slice(0, 80).map(summarize);
    const links = Array.from(document.querySelectorAll("a[href]")).slice(0, 120).map(summarize);
    const inputs = Array.from(document.querySelectorAll("input, textarea, select, [contenteditable='true']")).slice(0, 80).map(summarize);
    return {
      url: location.href,
      title: document.title,
      text: (document.body?.innerText || "").slice(0, 12000),
      buttons,
      links,
      inputs,
    };
  }, summarizeElement.toString());
}

function modifierMask(value) {
  const modifiers = Array.isArray(value) ? value.map((item) => String(item || "").toLowerCase()) : [];
  let mask = 0;
  if (modifiers.includes("alt")) mask |= 1;
  if (modifiers.includes("control") || modifiers.includes("ctrl")) mask |= 2;
  if (modifiers.includes("meta") || modifiers.includes("command")) mask |= 4;
  if (modifiers.includes("shift")) mask |= 8;
  return mask;
}

async function startScreencast(page, options = {}) {
  const targetId = await pageId(page);
  if (screencasts.has(targetId)) return screencasts.get(targetId);
  const session = await cdpSessionForPage(page);
  const state = { targetId, seq: 0, latest: null, session, active: true, frameHandler: null };
  const frameHandler = async (event) => {
    if (!state.active) return;
    state.seq += 1;
    state.latest = {
      seq: state.seq,
      data: event.data,
      metadata: event.metadata || {},
      receivedAt: Date.now(),
    };
    await session.send("Page.screencastFrameAck", { sessionId: event.sessionId }).catch(() => {});
  };
  state.frameHandler = frameHandler;
  session.on("Page.screencastFrame", frameHandler);
  await session.send("Page.enable").catch(() => {});
  await session.send("Page.startScreencast", {
    format: "jpeg",
    quality: Math.max(30, Math.min(90, Number(options.quality || 70))),
    maxWidth: Math.max(640, Math.min(2560, Number(options.maxWidth || 1920))),
    maxHeight: Math.max(480, Math.min(1800, Number(options.maxHeight || 1200))),
    everyNthFrame: 1,
  });
  screencasts.set(targetId, state);
  return state;
}

async function stopScreencast(targetId) {
  const normalized = String(targetId || "").trim();
  const state = screencasts.get(normalized);
  if (!state) return { targetId: normalized, stopped: false };
  screencasts.delete(normalized);
  state.active = false;
  await state.session.send("Page.stopScreencast").catch(() => {});
  if (state.frameHandler) state.session.off("Page.screencastFrame", state.frameHandler);
  return { targetId: normalized, stopped: true };
}

async function dispatchStructuredCommand(page, body) {
  const action = String(body.action || "").trim();
  const targetId = await pageId(page);
  const session = await cdpSessionForPage(page);
  if (action === "navigate") {
    const nextUrl = String(body.url || "").trim();
    if (!nextUrl) throw new Error("url is required");
    await page.goto(nextUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
  } else if (action === "back") {
    await page.goBack({ waitUntil: "domcontentloaded", timeout: 30000 }).catch(() => null);
  } else if (action === "forward") {
    await page.goForward({ waitUntil: "domcontentloaded", timeout: 30000 }).catch(() => null);
  } else if (action === "reload") {
    await page.reload({ waitUntil: "domcontentloaded", timeout: 30000 });
  } else if (action === "activate") {
    await page.bringToFront();
  } else if (["mouseMoved", "mousePressed", "mouseReleased"].includes(action)) {
    await session.send("Input.dispatchMouseEvent", {
      type: action,
      x: Number(body.x || 0),
      y: Number(body.y || 0),
      button: String(body.button || "none"),
      buttons: Number(body.buttons || 0),
      clickCount: Math.max(0, Math.min(3, Number(body.clickCount || 0))),
      modifiers: modifierMask(body.modifiers),
    });
  } else if (action === "mouseWheel") {
    await session.send("Input.dispatchMouseEvent", {
      type: "mouseWheel",
      x: Number(body.x || 0),
      y: Number(body.y || 0),
      deltaX: Number(body.deltaX || 0),
      deltaY: Number(body.deltaY || 0),
      modifiers: modifierMask(body.modifiers),
    });
  } else if (["keyDown", "rawKeyDown", "keyUp", "char"].includes(action)) {
    await session.send("Input.dispatchKeyEvent", {
      type: action,
      key: String(body.key || ""),
      code: String(body.code || ""),
      text: String(body.text || ""),
      unmodifiedText: String(body.unmodifiedText || body.text || ""),
      modifiers: modifierMask(body.modifiers),
      autoRepeat: Boolean(body.autoRepeat),
      isKeypad: Boolean(body.isKeypad),
    });
  } else if (action === "insertText") {
    await session.send("Input.insertText", { text: String(body.text || "") });
  } else {
    throw new Error(`unsupported dispatch action: ${action}`);
  }
  return { targetId, action, url: page.url(), title: await page.title().catch(() => "") };
}

async function route(req, res) {
  const url = new URL(req.url || "/", `http://127.0.0.1:${proxyPort}`);
  try {
    if (url.pathname === "/health") {
      let connected = false;
      let error = browserError?.message || null;
      if (playwrightModule) {
        try {
          const connectedBrowser = await ensureBrowser();
          connected = Boolean(connectedBrowser?.isConnected?.());
        } catch (healthError) {
          error = healthError?.message || String(healthError);
        }
      }
      return sendJson(res, 200, {
        ok: Boolean(playwrightModule),
        playwrightAvailable: Boolean(playwrightModule),
        connected,
        targetEndpoint,
        targetPort,
        proxyPort,
        pageCount: pages.size,
        error,
      });
    }

    if (url.pathname === "/targets" && req.method === "GET") {
      return sendJson(res, 200, { targets: await listPages() });
    }

    if (url.pathname === "/new" && req.method === "GET") {
      const page = await createPage(url.searchParams.get("url") || "about:blank");
      const id = await pageId(page);
      return sendJson(res, 200, { targetId: id, id, url: page.url(), title: await page.title().catch(() => "") });
    }

    if (url.pathname === "/info" && req.method === "GET") {
      const page = await getPage(url.searchParams.get("target"));
      return sendJson(res, 200, await pageInfo(page));
    }

    if (url.pathname === "/close" && req.method === "POST") {
      const targetId = String(url.searchParams.get("target") || "").trim();
      if (!targetId || !pages.has(targetId)) return sendJson(res, 404, { error: "target_not_found", targetId });
      const page = pages.get(targetId);
      await page.close({ runBeforeUnload: false });
      pages.delete(targetId);
      return sendJson(res, 200, { targetId, closed: true });
    }

    if (url.pathname === "/bringToFront" && req.method === "POST") {
      const page = await getPage(url.searchParams.get("target"));
      await page.bringToFront();
      return sendJson(res, 200, { targetId: await pageId(page), broughtToFront: true, url: page.url() });
    }

    if (url.pathname === "/maximize" && req.method === "POST") {
      const page = await getPage(url.searchParams.get("target"));
      const result = await maximizePageWindow(page);
      return sendJson(res, 200, { targetId: await pageId(page), ...result, url: page.url() });
    }

    if (url.pathname === "/navigate" && req.method === "GET") {
      const page = await getPage(url.searchParams.get("target"));
      const nextUrl = String(url.searchParams.get("url") || "").trim();
      if (!nextUrl) return sendJson(res, 400, { error: "url is required" });
      await page.goto(nextUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
      return sendJson(res, 200, { targetId: await pageId(page), url: page.url(), title: await page.title().catch(() => "") });
    }

    if (url.pathname === "/eval" && req.method === "POST") {
      const page = await getPage(url.searchParams.get("target"));
      const expression = await readBody(req);
      const value = await page.evaluate(expression);
      return sendJson(res, 200, { targetId: await pageId(page), value });
    }

    if (url.pathname === "/scroll" && req.method === "GET") {
      const page = await getPage(url.searchParams.get("target"));
      const amount = Math.max(1, Number.parseInt(url.searchParams.get("y") || "1200", 10));
      const direction = String(url.searchParams.get("direction") || "down").toLowerCase();
      const signed = direction === "up" ? -amount : amount;
      await page.evaluate((deltaY) => window.scrollBy({ top: deltaY, behavior: "instant" }), signed);
      return sendJson(res, 200, { targetId: await pageId(page), scrollY: await page.evaluate(() => window.scrollY) });
    }

    if (url.pathname === "/setFiles" && req.method === "POST") {
      const page = await getPage(url.searchParams.get("target"));
      const raw = await readBody(req);
      const body = raw ? JSON.parse(raw) : {};
      const selector = String(body.selector || "").trim();
      const files = Array.isArray(body.files) ? body.files.map(String) : [];
      if (!selector) return sendJson(res, 400, { error: "selector is required" });
      await page.setInputFiles(selector, files);
      return sendJson(res, 200, { targetId: await pageId(page), selector, fileCount: files.length });
    }

    if (url.pathname === "/screencast/start" && req.method === "POST") {
      const page = await getPage(url.searchParams.get("target"));
      const raw = await readBody(req);
      const body = raw ? JSON.parse(raw) : {};
      const state = await startScreencast(page, body);
      return sendJson(res, 200, { targetId: state.targetId, started: true, mode: "screencast", seq: state.seq });
    }

    if (url.pathname === "/screencast/frame" && req.method === "GET") {
      const targetId = String(url.searchParams.get("target") || "").trim();
      const after = Number(url.searchParams.get("after") || 0);
      const state = screencasts.get(targetId);
      if (!state) return sendJson(res, 404, { error: "screencast_not_started", targetId });
      return sendJson(res, 200, { targetId, frame: state.latest && state.latest.seq > after ? state.latest : null, seq: state.seq });
    }

    if (url.pathname === "/screencast/stop" && req.method === "POST") {
      return sendJson(res, 200, await stopScreencast(url.searchParams.get("target")));
    }

    if (url.pathname === "/screenshot" && req.method === "GET") {
      const page = await getPage(url.searchParams.get("target"));
      const session = await cdpSessionForPage(page);
      const result = await session.send("Page.captureScreenshot", { format: "jpeg", quality: 65, fromSurface: true });
      return sendJson(res, 200, { targetId: await pageId(page), data: result.data, capturedAt: Date.now() });
    }

    if (url.pathname === "/dispatch" && req.method === "POST") {
      const page = await getPage(url.searchParams.get("target"));
      const raw = await readBody(req);
      const body = raw ? JSON.parse(raw) : {};
      return sendJson(res, 200, await dispatchStructuredCommand(page, body));
    }

    return sendJson(res, 404, { error: "not_found", path: url.pathname });
  } catch (error) {
    return sendJson(res, 503, {
      error: error?.message || String(error),
      targetEndpoint,
      playwrightAvailable: Boolean(playwrightModule),
    });
  }
}

const server = http.createServer((req, res) => {
  void route(req, res);
});

server.listen(proxyPort, "127.0.0.1", () => {
  console.log(`browser_cdp_proxy listening on 127.0.0.1:${proxyPort}, target=${targetEndpoint}`);
});

process.on("SIGTERM", () => {
  server.close(() => process.exit(0));
});
