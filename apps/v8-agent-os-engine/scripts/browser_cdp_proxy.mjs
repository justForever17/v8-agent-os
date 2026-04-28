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

function pageId(page) {
  for (const [id, existing] of pages.entries()) {
    if (existing === page) return id;
  }
  const id = `page-${++pageCounter}`;
  pages.set(id, page);
  page.once("close", () => pages.delete(id));
  return id;
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
      for (const page of context.pages()) pageId(page);
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
      const id = pageId(page);
      result.push({
        targetId: id,
        id,
        title: await page.title().catch(() => ""),
        url: page.url(),
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
  }
  if (targets.length && pages.has(targets[0].targetId)) return pages.get(targets[0].targetId);
  return createPage("about:blank");
}

async function createPage(url) {
  const connected = await ensureBrowser();
  const context = connected.contexts()[0] || await connected.newContext();
  const page = await context.newPage();
  const id = pageId(page);
  const nextUrl = String(url || "about:blank").trim() || "about:blank";
  if (nextUrl !== "about:blank") {
    await page.goto(nextUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
  }
  return page;
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
      const id = pageId(page);
      return sendJson(res, 200, { targetId: id, id, url: page.url(), title: await page.title().catch(() => "") });
    }

    if (url.pathname === "/info" && req.method === "GET") {
      const page = await getPage(url.searchParams.get("target"));
      return sendJson(res, 200, await pageInfo(page));
    }

    if (url.pathname === "/navigate" && req.method === "GET") {
      const page = await getPage(url.searchParams.get("target"));
      const nextUrl = String(url.searchParams.get("url") || "").trim();
      if (!nextUrl) return sendJson(res, 400, { error: "url is required" });
      await page.goto(nextUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
      return sendJson(res, 200, { targetId: pageId(page), url: page.url(), title: await page.title().catch(() => "") });
    }

    if (url.pathname === "/eval" && req.method === "POST") {
      const page = await getPage(url.searchParams.get("target"));
      const expression = await readBody(req);
      const value = await page.evaluate(expression);
      return sendJson(res, 200, { targetId: pageId(page), value });
    }

    if (url.pathname === "/scroll" && req.method === "GET") {
      const page = await getPage(url.searchParams.get("target"));
      const amount = Math.max(1, Number.parseInt(url.searchParams.get("y") || "1200", 10));
      const direction = String(url.searchParams.get("direction") || "down").toLowerCase();
      const signed = direction === "up" ? -amount : amount;
      await page.evaluate((deltaY) => window.scrollBy({ top: deltaY, behavior: "instant" }), signed);
      return sendJson(res, 200, { targetId: pageId(page), scrollY: await page.evaluate(() => window.scrollY) });
    }

    if (url.pathname === "/setFiles" && req.method === "POST") {
      const page = await getPage(url.searchParams.get("target"));
      const raw = await readBody(req);
      const body = raw ? JSON.parse(raw) : {};
      const selector = String(body.selector || "").trim();
      const files = Array.isArray(body.files) ? body.files.map(String) : [];
      if (!selector) return sendJson(res, 400, { error: "selector is required" });
      await page.setInputFiles(selector, files);
      return sendJson(res, 200, { targetId: pageId(page), selector, fileCount: files.length });
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
