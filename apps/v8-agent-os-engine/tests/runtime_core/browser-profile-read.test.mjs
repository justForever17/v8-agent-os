import assert from "node:assert/strict";
import test from "node:test";
import { readProfilePage } from "../../scripts/browser_profile_read.mjs";

function fixture({ hops = ["https://example.test/member"], gotoError = false } = {}) {
  let paused, enabled = false;
  const state = { closed: 0, detached: 0, sent: [], commands: [], failed: new Set() };
  const cdp = {
    on: (name, fn) => { assert.equal(name, "Fetch.requestPaused"); paused = fn; },
    send: async (name, params) => {
      state.commands.push({ name, params });
      if (name === "Page.getFrameTree") return { frameTree: { frame: { id: "main" } } };
      if (name === "Fetch.enable") {
        assert.deepEqual(params.patterns, [{ urlPattern: "*", resourceType: "Document", requestStage: "Request" }]);
        enabled = true;
      }
      if (name === "Fetch.failRequest") state.failed.add(params.requestId);
    },
    detach: async () => { assert.ok(state.closed); state.detached++; },
  };
  const page = {
    goto: async () => {
      assert.ok(enabled, "interception must precede initial navigation");
      for (const [index, url] of hops.entries()) {
        const requestId = `request-${index}`;
        await paused({ requestId, frameId: "main", resourceType: "Document", request: { url },
          ...(index ? { redirectedRequestId: `request-${index - 1}` } : {}) });
        if (state.failed.has(requestId) || state.closed) throw Error(`navigation failed: ${url}`);
        state.sent.push(url);
      }
      if (gotoError) throw Error("PRIVATE-SIGNED-URL");
      return { status: () => 200 };
    },
    url: () => hops.at(-1),
    evaluate: async () => ({ html: "<article>private body</article>", htmlTruncated: false }),
    close: async () => { state.closed++; },
  };
  const context = { newPage: async () => page, newCDPSession: async target => { assert.equal(target, page); return cdp; },
    close: () => assert.fail("must retain context") };
  const browser = { contexts: () => [context], newContext: () => assert.fail("would lose authentication"),
    close: () => assert.fail("must retain browser") };
  return { browser, page, context, state };
}

test("authenticated read uses persistent context and closes only its own tab", async () => {
  const { browser, state } = fixture();
  const result = await readProfilePage(browser, { url: "https://example.test/member" });
  assert.equal(result.status, 200);
  assert.equal(result.contextReused, true);
  assert.equal(state.closed, 1);
  assert.equal(state.detached, 1);
});

test("a late newPage is closed without navigating after the request deadline", async () => {
  let navigated = false, closed = false;
  const page = { goto: async () => { navigated = true; }, close: async () => { closed = true; } };
  const context = { newPage: () => new Promise(resolve => setTimeout(() => resolve(page), 1100)) };
  await assert.rejects(readProfilePage({ contexts: () => [context] }, { url: "https://example.test", timeoutMs: 1000 }), /creation_failed/);
  await new Promise(resolve => setTimeout(resolve, 150));
  assert.equal(navigated, false);
  assert.equal(closed, true);
});

test("missing context and navigation failure never fall back to a fresh anonymous context", async () => {
  await assert.rejects(readProfilePage({ contexts: () => [] }, { url: "https://example.test" }), /profile_context_missing/);
  const { browser, state } = fixture({ gotoError: true });
  await assert.rejects(readProfilePage(browser, { url: "https://example.test" }),
    error => error.message === "agent_browser_profile_read_navigation_failed");
  assert.equal(state.closed, 1);
});

test("every redirect hop is checked; a same-origin intermediate does not authorize another host or port", async () => {
  for (const target of ["https://other.test/private?signature=SECRET", "https://example.test:444/private",
    "https://127.0.0.1/private", "https://localhost/private", "https://example.test.evil.test/private",
    "http://example.test/private", "https://user:SECRET@example.test/private"]) {
    const hops = ["https://example.test/start", "https://www.example.test/intermediate", target];
    const { browser, state } = fixture({ hops });
    await assert.rejects(readProfilePage(browser, { url: hops[0] }), error => {
      assert.equal(error.message, `agent_browser_profile_redirect_requires_authorization:${new URL(target).host}`);
      assert.ok(!error.message.includes("SECRET"));
      return true;
    });
    assert.deepEqual(state.sent, hops.slice(0, 2));
    assert.equal(state.closed, 1);
  }
});

test("same service and normal HTTPS/root-www upgrades remain readable, without downgrade after upgrading", async () => {
  for (const hops of [["https://example.test/a", "https://example.test/b"],
    ["https://example.test:8443/a", "https://www.example.test:8443/b"],
    ["http://example.test/a", "https://www.example.test/b", "https://example.test/c"]]) {
    const { browser, state } = fixture({ hops });
    const result = await readProfilePage(browser, { url: hops[0] });
    assert.equal(result.html, "<article>private body</article>");
    assert.deepEqual(state.sent, hops);
  }
  const hops = ["http://example.test/a", "https://example.test/b", "http://example.test/c"];
  const { browser, state } = fixture({ hops });
  await assert.rejects(readProfilePage(browser, { url: hops[0] }), /redirect_requires_authorization:example.test$/);
  assert.deepEqual(state.sent, hops.slice(0, 2));
});

test("missing/failed interception fails closed before sending the initial request", async () => {
  const { browser, context, state } = fixture();
  context.newCDPSession = async () => { throw Error("CDP failed at PRIVATE-SIGNED-URL"); };
  await assert.rejects(readProfilePage(browser, { url: "https://example.test" }),
    error => error.message === "agent_browser_profile_read_navigation_guard_failed");
  assert.deepEqual(state.sent, []);
  assert.equal(state.closed, 1);
});

// Explicit --live owns only a new temporary profile and two local HTTP servers. No model/provider calls.
test("live Chromium blocks cross-port and chained redirects before contact; same-origin cookie reads succeed", {
  skip: !process.argv.includes("--live"), timeout: 45000,
}, async t => {
  const [{ createServer }, { mkdtemp, rm }, { tmpdir }, { join, resolve }, { createRequire }, { fileURLToPath }] = await Promise.all([
    import("node:http"), import("node:fs/promises"), import("node:os"), import("node:path"), import("node:module"), import("node:url"),
  ]);
  const require = createRequire(import.meta.url);
  const engineRoot = fileURLToPath(new URL("../../", import.meta.url));
  const { chromium } = require(process.env.PLAYWRIGHT_DRIVER_PACKAGE || resolve(engineRoot, ".venv/Lib/site-packages/playwright/driver/package"));
  const profile = await mkdtemp(join(tmpdir(), "v8-profile-redirect-"));
  let context;
  const servers = [];
  t.after(async () => {
    try { await context?.close(); }
    finally {
      for (const server of servers) {
        server.closeAllConnections();
        await new Promise(resolveClose => server.close(resolveClose));
      }
      assert.equal(resolve(profile).startsWith(resolve(tmpdir()) + (process.platform === "win32" ? "\\" : "/") + "v8-profile-redirect-"), true);
      await rm(profile, { recursive: true, force: true });
    }
  });
  let unauthorizedHits = 0, authorizedHits = 0;
  const unauthorized = createServer((_request, response) => { unauthorizedHits++; response.end("unauthorized body"); });
  await new Promise(resolveListen => unauthorized.listen(0, "127.0.0.1", resolveListen));
  servers.push(unauthorized);
  const deniedPort = unauthorized.address().port;
  const authorized = createServer((request, response) => {
    const pathname = new URL(request.url, "http://fixture.test").pathname;
    if (pathname === "/session") { response.setHeader("Set-Cookie", "fixture_session=owned; HttpOnly; SameSite=Lax; Path=/"); response.end("fixture session ready"); }
    else if (pathname === "/good") { response.writeHead(302, { Location: "/member" }); response.end(); }
    else if (pathname === "/member") { authorizedHits++; response.end(request.headers.cookie?.includes("fixture_session=owned") ? "<article>authenticated fixture body</article>" : "login required"); }
    else if (pathname === "/chain") { response.writeHead(302, { Location: "/bad" }); response.end(); }
    else if (pathname === "/bad") { response.writeHead(302, { Location: `http://127.0.0.1:${deniedPort}/never?signature=SECRET` }); response.end(); }
    else if (pathname === "/client-redirect") { response.end(`<script>setTimeout(() => location.href = 'http://127.0.0.1:${deniedPort}/never?signature=SECRET', 50)</script><article>starting redirect</article>`); }
    else { response.writeHead(404); response.end(); }
  });
  await new Promise(resolveListen => authorized.listen(0, "127.0.0.1", resolveListen));
  servers.push(authorized);
  const origin = `http://127.0.0.1:${authorized.address().port}`;
  const channel = process.argv.find(arg => arg.startsWith("--browser-channel="))?.split("=")[1] || process.env.V8_TEST_BROWSER_CHANNEL;
  context = await chromium.launchPersistentContext(profile, {
    headless: true, ...(channel ? { channel } : {}),
  });
  const originalTab = context.pages()[0];
  await originalTab.goto(`${origin}/session`);
  // Negative control: the old unguarded goto really contacts the second server.
  const unguarded = await context.newPage();
  try { await unguarded.goto(`${origin}/chain`); assert.equal(unauthorizedHits, 1); }
  finally { await unguarded.close(); }
  unauthorizedHits = 0;
  const browser = { contexts: () => [context] };
  const result = await readProfilePage(browser, { url: `${origin}/good`, timeoutMs: 5000 });
  assert.equal(result.status, 200);
  assert.equal(result.contextReused, true);
  assert.ok(result.html.includes("authenticated fixture body"));
  assert.equal(authorizedHits, 1);
  for (const path of ["/bad", "/chain", "/client-redirect"]) {
    await assert.rejects(readProfilePage(browser, { url: origin + path, timeoutMs: 5000, waitMs: 200 }),
      error => error.message === `agent_browser_profile_redirect_requires_authorization:127.0.0.1:${deniedPort}`);
  }
  assert.equal(unauthorizedHits, 0, "the redirect destination must receive no request, including intermediate chains");
  assert.deepEqual(context.pages(), [originalTab]);
  assert.equal(originalTab.isClosed(), false);
  t.diagnostic(`unguardedRedirectContact=1; authorizedBodyReads=${authorizedHits}; guardedUnauthorizedTargetRequests=${unauthorizedHits}; originalTabRetained=true`);
});
