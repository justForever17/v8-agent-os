import fs from "node:fs";
import http from "node:http";
import https from "node:https";
import path from "node:path";
import process from "node:process";
import { URL } from "node:url";


const argv = process.argv.slice(2);
const configFlag = argv.indexOf("--config");
if (configFlag < 0 || !argv[configFlag + 1]) {
  throw new Error("ui patch preview proxy requires --config <path>");
}

const configPath = path.resolve(argv[configFlag + 1]);
const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
const mode = String(config.mode || "").trim();
const workspaceRoot = fs.realpathSync(path.resolve(String(config.workspaceRoot || "")));
const sessionId = String(config.patchSessionId || "").trim();
const parentOrigin = String(config.parentOrigin || "").trim();
const authToken = String(config.authToken || "");
const bootstrapTicket = String(config.bootstrapTicket || "");
const descriptorPath = path.resolve(String(config.descriptorPath || ""));
const entryPath = String(config.entryPath || "").replaceAll("\\", "/").replace(/^\/+/, "");
const targetUrl = mode === "dev" ? new URL(String(config.targetUrl || "")) : null;
const cookieName = `v8_ui_patch_${sessionId.replace(/[^a-zA-Z0-9]/g, "").slice(-18)}`;
let ticketConsumed = false;
const STATIC_ASSET_EXTENSIONS = new Set([
  ".css", ".gif", ".htm", ".html", ".ico", ".jpeg", ".jpg", ".js", ".mjs",
  ".png", ".svg", ".webp", ".woff", ".woff2",
]);
const STATIC_CONTENT_SECURITY_POLICY = [
  "default-src 'self' data: blob:",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "frame-src 'none'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'none'",
].join("; ");

if (!sessionId || !parentOrigin || !authToken || !bootstrapTicket || !descriptorPath) {
  throw new Error("ui patch preview proxy config is incomplete");
}
if (!new Set(["static", "dev"]).has(mode)) {
  throw new Error(`unsupported ui patch preview mode: ${mode}`);
}

function atomicWriteJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  const temporary = `${filePath}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { encoding: "utf8", mode: 0o600 });
  fs.renameSync(temporary, filePath);
}

function parseCookies(header) {
  const result = new Map();
  for (const item of String(header || "").split(";")) {
    const separator = item.indexOf("=");
    if (separator <= 0) continue;
    try {
      result.set(item.slice(0, separator).trim(), decodeURIComponent(item.slice(separator + 1).trim()));
    } catch {}
  }
  return result;
}

function isAuthorized(request) {
  return parseCookies(request.headers.cookie).get(cookieName) === authToken;
}

function contentType(filePath) {
  const extension = path.extname(filePath).toLowerCase();
  return ({
    ".css": "text/css; charset=utf-8",
    ".gif": "image/gif",
    ".htm": "text/html; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".webp": "image/webp",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
  })[extension] || "application/octet-stream";
}

function bridgeBootstrap() {
  const bootstrap = `<script>window.__V8_UI_PATCH_CONFIG__=${JSON.stringify({
    sessionId,
    parentOrigin,
    entryPath: entryPath || null,
  }).replaceAll("<", "\\u003c")};</script><script src="/__v8_ui_patch__/bridge.js"></script>`;
  return bootstrap;
}

function injectBridge(html) {
  if (html.includes("/__v8_ui_patch__/bridge.js")) return html;
  const bootstrap = bridgeBootstrap();
  if (/<\/head\s*>/i.test(html)) return html.replace(/<\/head\s*>/i, `${bootstrap}</head>`);
  if (/<body[\s>]/i.test(html)) return html.replace(/<body([^>]*)>/i, `<body$1>${bootstrap}`);
  return `<!doctype html><html><head>${bootstrap}</head><body>${html}</body></html>`;
}

function uiPatchBridgeRuntime() {
  const config = window.__V8_UI_PATCH_CONFIG__ || {};
  if (!config.sessionId || !config.parentOrigin || window.__v8UiPatchBridgeInstalled) return;
  window.__v8UiPatchBridgeInstalled = true;

  const ALLOWED = new Set([
    "width", "height", "min-width", "min-height", "max-width", "max-height",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "display", "gap", "row-gap", "column-gap", "align-items", "align-content",
    "justify-items", "justify-content", "flex", "flex-direction", "flex-wrap",
    "flex-grow", "flex-shrink", "grid-template-columns", "grid-template-rows",
    "position", "top", "right", "bottom", "left", "z-index", "overflow",
    "border", "border-width", "border-style", "border-color", "border-radius",
    "box-shadow", "background", "background-color", "color", "opacity",
    "font-size", "font-weight", "line-height", "letter-spacing", "text-align"
  ]);

  const escapeCss = (value) => window.CSS && typeof window.CSS.escape === "function"
    ? window.CSS.escape(String(value))
    : String(value).replace(/[^a-zA-Z0-9_-]/g, (char) => "\\" + char);
  const post = (payload) => window.parent.postMessage({ ...payload, patchSessionId: config.sessionId }, config.parentOrigin);
  const safeText = (value, limit = 140) => String(value || "").replace(/\s+/g, " ").trim().slice(0, limit);
  let inspectMode = true;
  let hovered = null;
  let selected = null;
  let frame = 0;
  let previewStyle = null;
  let selectedNodeId = "";

  const overlay = document.createElement("div");
  overlay.dataset.v8UiPatchOverlay = "true";
  Object.assign(overlay.style, {
    position: "fixed",
    zIndex: "2147483646",
    left: "0",
    top: "0",
    width: "1px",
    height: "1px",
    pointerEvents: "none",
    opacity: "0",
    transformOrigin: "center",
    border: "1px solid rgba(124, 58, 237, .88)",
    borderRadius: "4px",
    background: "rgba(124, 58, 237, .035)",
    boxShadow: "0 0 0 3px rgba(124, 58, 237, .10), 0 8px 24px rgba(15, 23, 42, .10)",
    transition: "transform 130ms cubic-bezier(.2,.8,.2,1), width 130ms cubic-bezier(.2,.8,.2,1), height 130ms cubic-bezier(.2,.8,.2,1), opacity 100ms ease-out, box-shadow 130ms ease-out",
    willChange: "transform,width,height,opacity",
  });
  const badge = document.createElement("div");
  badge.dataset.v8UiPatchOverlay = "true";
  Object.assign(badge.style, {
    position: "fixed",
    zIndex: "2147483647",
    pointerEvents: "none",
    opacity: "0",
    maxWidth: "320px",
    padding: "3px 7px",
    borderRadius: "4px",
    background: "rgba(15, 23, 42, .94)",
    color: "#f8fafc",
    font: "500 11px/1.35 ui-monospace, SFMono-Regular, Menlo, monospace",
    boxShadow: "0 6px 20px rgba(15, 23, 42, .20)",
    transition: "transform 130ms cubic-bezier(.2,.8,.2,1), opacity 100ms ease-out",
    willChange: "transform,opacity",
  });
  document.documentElement.append(overlay, badge);

  const reducedMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reducedMotion) {
    overlay.style.transition = "opacity 80ms linear";
    badge.style.transition = "opacity 80ms linear";
  }

  function updateOverlay(element) {
    if (!inspectMode || !element || !element.isConnected) {
      overlay.style.opacity = "0";
      badge.style.opacity = "0";
      return;
    }
    const rect = element.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1) return;
    const lift = reducedMotion ? "" : " scale(1.003)";
    overlay.style.width = Math.max(1, rect.width) + "px";
    overlay.style.height = Math.max(1, rect.height) + "px";
    overlay.style.transform = `translate3d(${rect.left}px, ${rect.top}px, 0)${lift}`;
    overlay.style.opacity = "1";
    const badgeTop = rect.top > 28 ? rect.top - 25 : Math.min(window.innerHeight - 24, rect.bottom + 5);
    badge.textContent = elementLabel(element, rect);
    badge.style.transform = `translate3d(${Math.max(4, Math.min(window.innerWidth - 324, rect.left))}px, ${badgeTop}px, 0)`;
    badge.style.opacity = "1";
  }

  function elementLabel(element, knownRect = null) {
    const id = element.id ? "#" + element.id : "";
    const classes = Array.from(element.classList || []).slice(0, 2).map((item) => "." + item).join("");
    const size = knownRect || element.getBoundingClientRect();
    return `${element.tagName.toLowerCase()}${id}${classes}  ${Math.round(size.width)}×${Math.round(size.height)}`;
  }

  function uniqueSelector(element) {
    if (!(element instanceof Element)) return "";
    if (element.id) {
      const selector = "#" + escapeCss(element.id);
      if (document.querySelectorAll(selector).length === 1) return selector;
    }
    for (const name of ["data-testid", "data-test-id", "data-v8-id", "name", "aria-label"]) {
      const value = element.getAttribute(name);
      if (!value) continue;
      const selector = `${element.tagName.toLowerCase()}[${name}="${escapeCss(value)}"]`;
      try { if (document.querySelectorAll(selector).length === 1) return selector; } catch {}
    }
    const parts = [];
    let current = element;
    while (current && current !== document.documentElement && parts.length < 6) {
      let part = current.tagName.toLowerCase();
      const classes = Array.from(current.classList || []).filter(Boolean).slice(0, 3);
      if (classes.length) part += classes.map((item) => "." + escapeCss(item)).join("");
      let candidate = [part, ...parts].join(" > ");
      try { if (document.querySelectorAll(candidate).length === 1) return candidate; } catch {}
      const parent = current.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((item) => item.tagName === current.tagName);
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
      }
      parts.unshift(part);
      current = parent;
    }
    return ["html", ...parts].join(" > ");
  }

  function declarationsOf(style) {
    const result = {};
    for (const property of ALLOWED) {
      const value = style.getPropertyValue(property);
      if (value) result[property] = value.trim() + (style.getPropertyPriority(property) ? " !important" : "");
    }
    return result;
  }

  function sourceHintForSheet(sheet) {
    const owner = sheet.ownerNode;
    if (owner instanceof HTMLLinkElement) return { kind: "href", value: owner.href };
    if (owner instanceof HTMLStyleElement) {
      const viteId = owner.getAttribute("data-vite-dev-id");
      if (viteId) return { kind: "vite", value: viteId };
      const nextHref = owner.getAttribute("data-n-href") || owner.getAttribute("data-next-href");
      if (nextHref) return { kind: "next", value: nextHref };
      const styles = Array.from(document.querySelectorAll("style"));
      return { kind: "inline", value: String(styles.indexOf(owner)) };
    }
    return sheet.href ? { kind: "href", value: sheet.href } : { kind: "unknown", value: "" };
  }

  function collectRules(element) {
    const result = [];
    let order = 0;
    const visit = (rules, sheet) => {
      for (const rule of Array.from(rules || [])) {
        if (rule instanceof CSSStyleRule) {
          let matched = false;
          try { matched = element.matches(rule.selectorText); } catch {}
          if (matched) {
            result.push({
              selector: rule.selectorText,
              declarations: declarationsOf(rule.style),
              ruleText: safeText(rule.cssText, 1800),
              sourceHint: sourceHintForSheet(sheet),
              order: order++,
            });
          }
        } else if (rule.cssRules) {
          visit(rule.cssRules, sheet);
        }
      }
    };
    for (const sheet of Array.from(document.styleSheets || [])) {
      try { visit(sheet.cssRules, sheet); } catch {}
    }
    return result;
  }

  function selectionPayload(element) {
    const style = getComputedStyle(element);
    const computedStyles = {};
    for (const property of ALLOWED) computedStyles[property] = style.getPropertyValue(property).trim();
    const rect = element.getBoundingClientRect();
    return {
      selector: uniqueSelector(element),
      tagName: element.tagName.toLowerCase(),
      label: safeText(element.getAttribute("aria-label") || element.getAttribute("title") || element.textContent, 100) || elementLabel(element),
      rect: { left: rect.left, top: rect.top, width: rect.width, height: rect.height },
      computedStyles,
      rules: collectRules(element),
    };
  }

  function setSelected(element, notifyParent = true) {
    if (selected && selected !== element && selectedNodeId) selected.removeAttribute("data-v8-ui-patch-node");
    selected = element;
    selectedNodeId = "node_" + Math.random().toString(16).slice(2);
    selected.setAttribute("data-v8-ui-patch-node", selectedNodeId);
    updateOverlay(selected);
    if (notifyParent) post({ type: "v8-ui-patch:selected", selection: selectionPayload(selected) });
  }

  function applyPreview(changes) {
    if (!selected || !selectedNodeId) return;
    if (previewStyle) previewStyle.remove();
    previewStyle = document.createElement("style");
    previewStyle.dataset.v8UiPatchPreview = "true";
    const declarations = Object.entries(changes || {})
      .filter(([property, value]) => ALLOWED.has(property) && String(value || "").trim())
      .map(([property, value]) => `${property}:${String(value).trim()} !important`)
      .join(";");
    previewStyle.textContent = `[data-v8-ui-patch-node="${escapeCss(selectedNodeId)}"]{${declarations}}`;
    document.documentElement.appendChild(previewStyle);
    requestAnimationFrame(() => {
      updateOverlay(selected);
      const style = getComputedStyle(selected);
      const computedStyles = {};
      for (const property of Object.keys(changes || {})) computedStyles[property] = style.getPropertyValue(property).trim();
      post({ type: "v8-ui-patch:preview-applied", computedStyles });
    });
  }

  function verify(selector, expectedStyles, requestId) {
    let element = null;
    try { element = document.querySelector(selector); } catch {}
    if (!element) {
      post({ type: "v8-ui-patch:verification", requestId, ok: false, reason: "element_not_found", observedStyles: {} });
      return;
    }
    const style = getComputedStyle(element);
    const observedStyles = {};
    let ok = true;
    for (const [property, expected] of Object.entries(expectedStyles || {})) {
      const observed = style.getPropertyValue(property).trim();
      observedStyles[property] = observed;
      if (observed !== String(expected || "").trim()) ok = false;
    }
    post({ type: "v8-ui-patch:verification", requestId, ok, reason: ok ? null : "computed_style_mismatch", observedStyles });
  }

  document.addEventListener("mousemove", (event) => {
    if (!inspectMode) return;
    if (frame) cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      const target = event.target instanceof Element ? event.target : null;
      if (!target || target.closest("[data-v8-ui-patch-overlay]")) return;
      if (target !== hovered) {
        hovered = target;
        updateOverlay(target);
      }
    });
  }, true);

  document.addEventListener("mouseleave", () => {
    if (!selected) updateOverlay(null);
  }, true);

  document.addEventListener("click", (event) => {
    if (!inspectMode) return;
    const target = event.target instanceof Element ? event.target : null;
    if (!target || target.closest("[data-v8-ui-patch-overlay]")) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    setSelected(target);
  }, true);

  const refreshOverlay = () => updateOverlay(selected || hovered);
  window.addEventListener("scroll", refreshOverlay, true);
  window.addEventListener("resize", refreshOverlay, { passive: true });

  window.addEventListener("message", (event) => {
    if (event.origin !== config.parentOrigin || event.source !== window.parent) return;
    const message = event.data || {};
    if (message.patchSessionId !== config.sessionId) return;
    if (message.type === "v8-ui-patch:set-mode") {
      inspectMode = message.mode !== "interact";
      document.documentElement.style.cursor = inspectMode ? "crosshair" : "";
      if (!inspectMode) updateOverlay(null);
      else refreshOverlay();
    } else if (message.type === "v8-ui-patch:apply-preview") {
      applyPreview(message.changes || {});
    } else if (message.type === "v8-ui-patch:clear-preview") {
      if (previewStyle) previewStyle.remove();
      previewStyle = null;
      requestAnimationFrame(refreshOverlay);
    } else if (message.type === "v8-ui-patch:reload") {
      window.location.reload();
    } else if (message.type === "v8-ui-patch:verify") {
      verify(String(message.selector || ""), message.expectedStyles || {}, message.requestId);
    } else if (message.type === "v8-ui-patch:restore-selection") {
      let element = null;
      try { element = document.querySelector(String(message.selector || "")); } catch {}
      if (element) setSelected(element, false);
    }
  });

  document.documentElement.style.cursor = "crosshair";
  post({ type: "v8-ui-patch:ready", href: window.location.href, title: document.title });
}

const BRIDGE_SOURCE = `(${uiPatchBridgeRuntime.toString()})();`;

function writeUnauthorized(response) {
  response.writeHead(401, { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" });
  response.end("UI Patch preview authorization required.");
}

function staticFileForUrl(urlPath) {
  let decoded;
  try {
    decoded = decodeURIComponent(urlPath || "/");
  } catch {
    return null;
  }
  if (decoded === "/") decoded = `/${entryPath}`;
  const relative = decoded.replace(/^\/+/, "");
  const candidate = path.resolve(workspaceRoot, relative);
  const relativeCheck = path.relative(workspaceRoot, candidate);
  if (!relativeCheck || relativeCheck === ".") return path.resolve(workspaceRoot, entryPath);
  if (relativeCheck.startsWith("..") || path.isAbsolute(relativeCheck)) return null;
  if (!fs.existsSync(candidate)) return null;
  const real = fs.realpathSync(candidate);
  const realCheck = path.relative(workspaceRoot, real);
  if (realCheck.startsWith("..") || path.isAbsolute(realCheck)) return null;
  if (fs.statSync(real).isDirectory()) {
    const indexPath = path.join(real, "index.html");
    return fs.existsSync(indexPath) ? fs.realpathSync(indexPath) : null;
  }
  if (!STATIC_ASSET_EXTENSIONS.has(path.extname(real).toLowerCase())) return null;
  return real;
}

function serveStatic(request, response, requestUrl) {
  const filePath = staticFileForUrl(requestUrl.pathname);
  if (!filePath) {
    response.writeHead(404, { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" });
    response.end("Not found");
    return;
  }
  const type = contentType(filePath);
  if (type.startsWith("text/html")) {
    const html = injectBridge(fs.readFileSync(filePath, "utf8"));
    response.writeHead(200, {
      "content-type": type,
      "cache-control": "no-store",
      "content-security-policy": STATIC_CONTENT_SECURITY_POLICY,
      "content-length": Buffer.byteLength(html),
    });
    response.end(html);
    return;
  }
  const stat = fs.statSync(filePath);
  response.writeHead(200, { "content-type": type, "cache-control": "no-store", "content-length": stat.size });
  fs.createReadStream(filePath).pipe(response);
}

function proxyRequest(request, response, requestUrl) {
  const client = targetUrl.protocol === "https:" ? https : http;
  const headers = { ...request.headers, host: targetUrl.host, "accept-encoding": "identity" };
  delete headers.cookie;
  const upstream = client.request({
    protocol: targetUrl.protocol,
    hostname: targetUrl.hostname,
    port: targetUrl.port || undefined,
    method: request.method,
    path: requestUrl.pathname + requestUrl.search,
    headers,
  }, (upstreamResponse) => {
    const responseHeaders = { ...upstreamResponse.headers };
    delete responseHeaders["x-frame-options"];
    delete responseHeaders["content-security-policy"];
    delete responseHeaders["content-security-policy-report-only"];
    if (responseHeaders.location) {
      let redirected;
      try {
        const upstreamRequestUrl = new URL(requestUrl.pathname + requestUrl.search, targetUrl.origin);
        redirected = new URL(String(responseHeaders.location), upstreamRequestUrl);
      } catch {
        redirected = null;
      }
      if (!redirected || redirected.origin !== targetUrl.origin) {
        upstreamResponse.resume();
        response.writeHead(502, { "content-type": "text/plain; charset=utf-8", "cache-control": "no-store" });
        response.end("UI Patch blocked a redirect outside the selected local development origin.");
        return;
      }
      responseHeaders.location = `${redirected.pathname}${redirected.search}${redirected.hash}`;
    }
    const type = String(upstreamResponse.headers["content-type"] || "");
    if (!type.includes("text/html")) {
      response.writeHead(upstreamResponse.statusCode || 502, responseHeaders);
      upstreamResponse.pipe(response);
      return;
    }
    const chunks = [];
    let size = 0;
    upstreamResponse.on("data", (chunk) => {
      size += chunk.length;
      if (size <= 5 * 1024 * 1024) chunks.push(chunk);
    });
    upstreamResponse.on("end", () => {
      if (size > 5 * 1024 * 1024) {
        response.writeHead(413, { "content-type": "text/plain; charset=utf-8" });
        response.end("Preview HTML is too large.");
        return;
      }
      const html = injectBridge(Buffer.concat(chunks).toString("utf8"));
      delete responseHeaders["content-length"];
      delete responseHeaders["content-encoding"];
      responseHeaders["cache-control"] = "no-store";
      responseHeaders["content-type"] = "text/html; charset=utf-8";
      response.writeHead(upstreamResponse.statusCode || 200, responseHeaders);
      response.end(html);
    });
  });
  upstream.on("error", (error) => {
    if (!response.headersSent) response.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
    response.end(`Local development server unavailable: ${error.message}`);
  });
  request.pipe(upstream);
}

const server = http.createServer((request, response) => {
  const requestUrl = new URL(request.url || "/", "http://127.0.0.1");
  if (requestUrl.pathname === "/__v8_ui_patch__/bootstrap") {
    const ticketValid = !ticketConsumed && requestUrl.searchParams.get("ticket") === bootstrapTicket;
    if (!ticketValid && !isAuthorized(request)) return writeUnauthorized(response);
    ticketConsumed = true;
    const staticLocation = `/${entryPath.split("/").map((part) => encodeURIComponent(part)).join("/")}`;
    response.writeHead(302, {
      location: mode === "static" ? staticLocation : `${targetUrl.pathname || "/"}${targetUrl.search || ""}`,
      "set-cookie": `${cookieName}=${encodeURIComponent(authToken)}; HttpOnly; SameSite=Lax; Path=/; Max-Age=14400`,
      "cache-control": "no-store",
    });
    response.end();
    return;
  }
  if (!isAuthorized(request)) return writeUnauthorized(response);
  if (requestUrl.pathname === "/__v8_ui_patch__/bridge.js") {
    response.writeHead(200, { "content-type": "text/javascript; charset=utf-8", "cache-control": "no-store" });
    response.end(BRIDGE_SOURCE);
    return;
  }
  if (requestUrl.pathname === "/__v8_ui_patch__/health") {
    response.writeHead(200, { "content-type": "application/json", "cache-control": "no-store" });
    response.end(JSON.stringify({ ok: true, mode, sessionId }));
    return;
  }
  if (mode === "static") serveStatic(request, response, requestUrl);
  else proxyRequest(request, response, requestUrl);
});

server.on("upgrade", (request, socket, head) => {
  if (mode !== "dev" || !isAuthorized(request)) {
    socket.destroy();
    return;
  }
  const client = targetUrl.protocol === "https:" ? https : http;
  const headers = { ...request.headers, host: targetUrl.host };
  delete headers.cookie;
  const upstream = client.request({
    protocol: targetUrl.protocol,
    hostname: targetUrl.hostname,
    port: targetUrl.port || undefined,
    method: request.method,
    path: request.url,
    headers,
  });
  upstream.on("upgrade", (upstreamResponse, upstreamSocket, upstreamHead) => {
    const statusLine = `HTTP/1.1 ${upstreamResponse.statusCode || 101} ${upstreamResponse.statusMessage || "Switching Protocols"}\r\n`;
    const headers = Object.entries(upstreamResponse.headers).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : value}\r\n`).join("");
    socket.write(statusLine + headers + "\r\n");
    if (upstreamHead.length) socket.write(upstreamHead);
    if (head.length) upstreamSocket.write(head);
    upstreamSocket.pipe(socket).pipe(upstreamSocket);
  });
  upstream.on("error", () => socket.destroy());
  upstream.end();
});

server.listen(0, "127.0.0.1", () => {
  const address = server.address();
  atomicWriteJson(descriptorPath, {
    version: 1,
    patchSessionId: sessionId,
    pid: process.pid,
    port: typeof address === "object" && address ? address.port : 0,
    mode,
    readyAt: new Date().toISOString(),
  });
});

const shutdown = () => server.close(() => process.exit(0));
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
