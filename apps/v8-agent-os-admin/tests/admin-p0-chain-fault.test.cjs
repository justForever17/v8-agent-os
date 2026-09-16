const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const http = require("node:http");
const { execFileSync } = require("node:child_process");
const test = require("node:test");
const ts = require("typescript");
const { NextRequest } = require("next/server");

const adminRoot = path.resolve(__dirname, "..");
function load(relativePath, { overrides = {}, globals = {}, source } = {}) {
    const filename = path.join(adminRoot, relativePath);
    const compiled = ts.transpileModule(source ?? fs.readFileSync(filename, "utf8"), {
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
        fileName: filename,
    }).outputText;
    const loadedModule = { exports: {} };
    new Function("require", "module", "exports", ...Object.keys(globals), compiled)(
        name => Object.hasOwn(overrides, name) ? overrides[name] : require(name),
        loadedModule, loadedModule.exports, ...Object.values(globals),
    );
    return loadedModule.exports;
}
function baseline(relativePath) {
    const ref = process.env.V8_P0_BASELINE_REF;
    return ref ? execFileSync("git", ["show", `${ref}:apps/v8-agent-os-admin/${relativePath}`], { cwd: adminRoot, encoding: "utf8" }) : null;
}
function gateway(source) {
    let now = 0, nextTimer = 0;
    const timers = new Map(), sockets = [], fanout = [];
    class Socket {
        sent = [];
        closeCount = 0;
        constructor() { sockets.push(this); }
        send(value) { this.sent.push(JSON.parse(value)); }
        close() { this.closeCount += 1; this.onclose?.({ code: 1000 }); }
        open() { this.onopen?.(); }
        message(value) { this.onmessage?.({ data: typeof value === "string" ? value : JSON.stringify(value) }); }
        disconnect() { this.onclose?.({ code: 1006 }); }
    }
    const advance = ms => {
        now += ms;
        for (const [id, timer] of [...timers]) {
            if (timer.at <= now) { timers.delete(id); timer.fn(); }
        }
    };
    const resource = load("src/lib/server/session-realtime-resource.ts", { overrides: {
        "@/lib/server/client-surface-resource": { buildSignedClientSurfaceUrl: () => assert.fail("no asset in this fixture") },
        "@/lib/server/runtime-event-delivery": load("src/lib/server/runtime-event-delivery.ts"),
    } });
    const gatewayModule = load("src/lib/realtime/engine-chat-gateway.ts", { source, overrides: {
        "@/lib/realtime/session-fanout": { sessionFanoutHub: { publish: (id, event) => fanout.push({ id, event }) } },
        "@/lib/server/runtime-config": { resolveEngineWsBaseUrl: () => "ws://fixture.test", resolveInternalSecret: () => "public-test-key" },
        "@/lib/server/session-realtime-resource": resource,
    }, globals: {
        WebSocket: Socket,
        setTimeout: (fn, ms) => { const id = ++nextTimer; timers.set(id, { fn, at: now + ms }); return id; },
        clearTimeout: id => timers.delete(id),
    } });
    return { ...gatewayModule, sockets, fanout, advance, timers };
}
async function frames(stream) {
    const text = await new Response(stream).text();
    return text.split("\n").filter(Boolean).map(JSON.parse);
}
const progress = { type: "text_chunk", content: "partial result", session_id: "session-a", run_id: "run-a" };

test("unexpected EOF retains partial data and exact identity without publishing a failed run", async () => {
    const fixed = gateway();
    const stream = fixed.createEngineChatGatewayStream({ session_id: "session-a" }, "fixture-owner");
    const socket = fixed.sockets[0];
    socket.open(); socket.message(progress); socket.disconnect();
    const result = await frames(stream);
    assert.equal(result[0].content, "partial result");
    assert.deepEqual(result.at(-1), {
        type: "transport_error", code: "engine_stream_disconnected",
        error: "Engine connection closed before completion; the task outcome is unknown. Resync this session before continuing.",
        sessionId: "session-a", runId: "run-a", unknownOutcome: true, retryable: false, recovery: "resync",
    });
    assert.equal(fixed.fanout.length, 1);
    assert.equal(socket.sent.filter(value => value.topic === "chat.start").length, 1);
    assert.equal(fixed.timers.size, 0);
    // Same observable oracle rejects the actual pre-fix source, not a copied implementation.
    const source = baseline("src/lib/realtime/engine-chat-gateway.ts");
    if (!source) return; // Optional historical comparison; CI may use a shallow checkout.
    const old = gateway(source);
    const oldStream = old.createEngineChatGatewayStream({ session_id: "session-a" }, "fixture-owner");
    old.sockets[0].open(); old.sockets[0].message(progress); old.sockets[0].disconnect();
    const oldFrames = await frames(oldStream);
    assert.throws(() => assert.equal(oldFrames.at(-1).type, "transport_error"));
});

test("handshake times out once; five minutes of legitimate silence after open never ends the run", async () => {
    const stalled = gateway();
    const stream = stalled.createEngineChatGatewayStream({ session_id: "session-a" }, "fixture-owner");
    stalled.advance(9_999);
    assert.equal(stalled.sockets[0].closeCount, 0);
    stalled.advance(1);
    assert.equal((await frames(stream))[0].code, "engine_connect_timeout");
    assert.equal(stalled.sockets[0].sent.length, 0);
    assert.equal(stalled.timers.size, 0);

    const working = gateway();
    const live = working.createEngineChatGatewayStream({ session_id: "session-a" }, "fixture-owner");
    working.sockets[0].open();
    working.advance(300_000);
    assert.equal(working.sockets[0].closeCount, 0);
    working.sockets[0].message({ type: "done", session_id: "session-a", run_id: "run-a" });
    assert.equal((await frames(live)).at(-1).type, "done");
    assert.equal(working.fanout.length, 1);
});

test("Engine request rejection and malformed transport end without forging a failed run", async () => {
    const rejected = gateway();
    const stream = rejected.createEngineChatGatewayStream({ session_id: "session-a" }, "fixture-owner");
    rejected.sockets[0].open();
    rejected.sockets[0].message({ v: 1, kind: "error", topic: "runtime.invalid_request", payload: { message: "invalid request" } });
    assert.equal((await frames(stream)).at(-1).code, "engine_request_rejected");
    assert.equal(rejected.fanout.length, 0);
    assert.equal(rejected.timers.size, 0);
    const broken = gateway();
    const malformed = broken.createEngineChatGatewayStream({ session_id: "session-a" }, "fixture-owner");
    broken.sockets[0].open(); broken.sockets[0].message("{partial-json");
    assert.equal((await frames(malformed))[0].code, "engine_stream_invalid");
    assert.equal(broken.fanout.length, 0);
});

test("reader cancellation and request abort detach callbacks and timers, including before connect", async () => {
    for (const opened of [false, true]) {
        const fixture = gateway();
        const stream = fixture.createEngineChatGatewayStream({ session_id: "session-a" }, "fixture-owner");
        const socket = fixture.sockets[0];
        const lateOpen = socket.onopen, lateMessage = socket.onmessage;
        if (opened) socket.open();
        await stream.cancel();
        assert.doesNotThrow(() => { lateOpen(); lateMessage({ data: JSON.stringify(progress) }); fixture.advance(300_000); });
        assert.equal(fixture.fanout.length, 0);
        assert.equal(fixture.timers.size, 0);
        assert.equal(socket.onclose, null);
    }
    const fixture = gateway(), controller = new AbortController();
    controller.abort();
    assert.deepEqual(await frames(fixture.createEngineChatGatewayStream({ session_id: "session-a" }, "fixture-owner", controller.signal)), []);
    assert.equal(fixture.sockets.length, 0);
});

async function engineServer(t) {
    const received = [];
    let mode = "ok";
    const server = http.createServer((req, res) => {
        req.resume();
        received.push({ url: req.url, headers: req.headers });
        if (mode === "headers-only") { res.writeHead(200, { "Content-Type": "application/json" }); res.write('{"decided":'); return; }
        if (mode === "hang") return;
        if (mode === "invalid") { res.writeHead(200); res.end("not-json"); return; }
        if (mode === "empty") { res.writeHead(200); res.end("{}"); return; }
        if (mode === "conflict") { res.writeHead(409, { "Content-Type": "application/json" }); res.end('{"detail":"already_decided","status":"rejected"}'); return; }
        res.writeHead(200, { "Content-Type": "application/json" }); res.end('{"approval_id":"approval/a","status":"approved","resume_scheduled":false}');
    });
    await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
    t.after(() => { server.closeAllConnections(); server.close(); });
    return { received, origin: () => `http://127.0.0.1:${server.address().port}/v1`, setMode: value => { mode = value; } };
}
function commandRoutes(engine, { authorized = true, fetchImpl = fetch } = {}) {
    const overrides = {
        "@/lib/server/runtime-config": { resolveEngineBaseUrl: engine.origin, resolveEngineOrigin: () => engine.origin().replace(/\/v1$/, ""), resolveInternalSecret: () => "public-test-key" },
        "@/lib/server/request-auth": { resolveAuthorizedUserEmail: async () => authorized ? "fixture-owner" : null, unauthorizedJson: () => Response.json({ error: "Unauthorized" }, { status: 401 }) },
        "@/lib/auth": { auth: async () => authorized ? { user: { email: "fixture-owner" } } : null },
        "@/lib/service-auth": { verifyServiceAuth: async () => authorized ? "fixture-owner" : null },
    };
    const globals = {
        fetch: fetchImpl,
        // Only the clock is accelerated; actual fetch, abort composition,
        // streaming body parser and route serialization remain production code.
        setTimeout: (fn, ms) => { assert.equal(ms, 15_000); return setTimeout(fn, 100); },
    };
    overrides["@/lib/server/engine-fetch"] = load("src/lib/server/engine-fetch.ts", {
        overrides: { "@/lib/server/runtime-config": overrides["@/lib/server/runtime-config"] },
        globals: { fetch: fetchImpl },
    });
    overrides["@/lib/server/engine-command-proxy"] = load("src/lib/server/engine-command-proxy.ts", { overrides, globals });
    return ["approvals/[id]/approve", "approvals/[id]/reject", "runs/[runId]/commands/[command]"].map(value =>
        load(`src/app/api/${value}/route.ts`, { overrides, globals }));
}
function request(signal) { return new NextRequest("http://fixture.test/api/command", { method: "POST", body: '{"response":{"approved":true}}', signal }); }
const params = { params: Promise.resolve({ id: "approval/a", runId: "run/a", command: "interrupt" }) };

test("actual HTTP header/body stalls end as unknown with one POST; explicit next read/command can recover", async t => {
    const engine = await engineServer(t), routes = commandRoutes(engine);
    for (const mode of ["hang", "headers-only"]) {
        engine.setMode(mode);
        for (const route of routes) {
            const before = engine.received.length;
            const response = await route.POST(request(), params), data = await response.json();
            assert.equal(response.status, 504);
            assert.equal(data.code, "engine_timeout");
            assert.equal(data.unknownOutcome, true);
            assert.equal(data.retryable, false);
            assert.equal(data.recovery, "resync");
            assert.ok(data.approvalId === "approval/a" || data.runId === "run/a");
            assert.equal(engine.received.length, before + 1);
        }
    }
    engine.setMode("ok");
    const recovery = await routes[0].POST(request(), params);
    assert.deepEqual(await recovery.json(), { approval_id: "approval/a", status: "approved", resume_scheduled: false });
    assert.match(engine.received.at(-1).url, /approval%2Fa/);
    assert.equal(engine.received.at(-1).headers["x-v8-agent-os-user-email"], "fixture-owner");
});

test("invalid successful receipts cannot become 200 empty success; Engine conflict remains authoritative", async t => {
    const engine = await engineServer(t), routes = commandRoutes(engine);
    for (const mode of ["invalid", "empty"]) {
        engine.setMode(mode);
        const response = await routes[0].POST(request(), params);
        assert.equal(response.status, 502);
        assert.equal((await response.json()).unknownOutcome, true);
    }
    engine.setMode("conflict");
    const response = await routes[1].POST(request(), params);
    assert.equal(response.status, 409);
    assert.deepEqual(await response.json(), { detail: "already_decided", status: "rejected" });

    const source = baseline("src/app/api/approvals/[id]/approve/route.ts");
    if (!source) return;
    const old = load("src/app/api/approvals/[id]/approve/route.ts", { source, overrides: {
        "@/lib/server/runtime-config": { resolveEngineBaseUrl: engine.origin, resolveInternalSecret: () => "public-test-key" },
        "@/lib/server/request-auth": { resolveAuthorizedUserEmail: async () => "fixture-owner" },
    } });
    engine.setMode("invalid");
    const badReceipt = await old.POST(request(), params);
    assert.equal(badReceipt.status, 200);
    assert.deepEqual(await badReceipt.json(), {});
});

test("unauthorized and already-aborted commands never fetch; post-dispatch abort is unknown", async t => {
    const engine = await engineServer(t);
    const denied = commandRoutes(engine, { authorized: false, fetchImpl: () => assert.fail("unauthorized fetch") });
    for (const route of denied) assert.equal((await route.POST(request(), params)).status, 401);
    const controller = new AbortController(); controller.abort();
    const cancelled = commandRoutes(engine, { fetchImpl: () => assert.fail("pre-aborted fetch") });
    const before = await cancelled[0].POST(request(controller.signal), params);
    assert.equal(before.status, 499);
    assert.equal((await before.json()).unknownOutcome, false);
    const abort = new AbortController();
    const waiting = commandRoutes(engine, { fetchImpl: async (_url, init) => {
        abort.abort();
        init.signal.throwIfAborted();
    } });
    const after = await waiting[0].POST(request(abort.signal), params);
    assert.equal(after.status, 499);
    assert.equal((await after.json()).unknownOutcome, true);
    const offline = commandRoutes(engine, { fetchImpl: async () => { throw new TypeError("fetch failed"); } });
    const failed = await offline[0].POST(request(), params);
    assert.equal(failed.status, 502);
    assert.equal((await failed.json()).code, "engine_unavailable");
});

test("trusted chat sends minimal even without runtime mode; absent authorization remains absent", async () => {
    const payloads = [];
    const route = load("src/app/api/chat/route.ts", { overrides: {
        "@/lib/volcengine": { generateImageWithDoubao: () => assert.fail("not an image request") },
        "@/lib/realtime/engine-chat-gateway": { createEngineChatGatewayStream: payload => { payloads.push(payload); return new ReadableStream({ start(c) { c.close(); } }); } },
        "@/lib/realtime/engine-chat-request": { validateSupervisorRuntimeMode: () => undefined },
        "@/lib/realtime/supervisor-runtime-mode": { SupervisorRuntimeModeValidationError: class extends Error {} },
        "@/lib/server/request-auth": { resolveAuthorizedUserEmail: async () => "fixture-owner" },
    } });
    for (const data of [{ safetyApprovalMode: "minimal" }, {}]) {
        const response = await route.POST(new NextRequest("http://fixture.test/api/chat", { method: "POST", body: JSON.stringify({ messages: [{ role: "user", content: "task" }], data }) }));
        assert.equal(response.status, 200);
    }
    assert.equal(payloads[0].data.safetyApprovalMode, "minimal");
    assert.equal(payloads[1].data?.safetyApprovalMode, undefined);
});

test("Engine fetch boundary injects only canonical secret and preserves external stream headers", async () => {
    const calls = [];
    const loadedModule = load("src/lib/server/engine-fetch.ts", {
        overrides: { "@/lib/server/runtime-config": {
            resolveEngineOrigin: () => "http://engine.fixture",
            resolveInternalSecret: () => "synthetic-service-secret",
        } },
        globals: { fetch: async (input, init) => {
            calls.push({ input: String(input), headers: new Headers(init?.headers) });
            return new Response("ok");
        } },
    });
    await loadedModule.engineFetch("http://engine.fixture/v1/health", { headers: { "x-v8-agent-os-user-email": "owner" } });
    assert.equal(calls[0].headers.get("x-v8-agent-os-secret"), "synthetic-service-secret");
    const external = new Request("https://provider.fixture/upload", { headers: { "x-v8-agent-os-secret": "copied-secret", "Authorization": "Bearer provider" } });
    await loadedModule.engineFetch(external);
    assert.equal(calls[1].headers.get("x-v8-agent-os-secret"), null);
    assert.equal(calls[1].headers.get("authorization"), "Bearer provider");
});
