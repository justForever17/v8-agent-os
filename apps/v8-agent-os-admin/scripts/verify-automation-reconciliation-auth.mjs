import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import ts from "typescript";

let session = null;
let resultStatus = 200;
let failTransport = false;
const sent = [];
function load(relativePath) {
    const source = fs.readFileSync(path.resolve(relativePath), "utf8");
    const compiled = ts.transpileModule(source, {
        compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
        reportDiagnostics: true,
    });
    assert.equal(compiled.diagnostics?.length || 0, 0);
    const loadedModule = { exports: {} };
    vm.runInNewContext(compiled.outputText, {
        module: loadedModule, exports: loadedModule.exports, TextEncoder, URLSearchParams,
        require(name) {
            if (name === "next/server") return { NextResponse: { json(data, init) { return new Response(JSON.stringify(data), init); } } };
            if (name === "@/lib/auth") return { auth: async () => session };
            if (name === "@/lib/server/runtime-config") return { resolveInternalSecret: () => "synthetic-server-relay" };
            if (name === "@/lib/server/engine-proxy") return {
                proxyEngineJson: async (url, init) => {
                    sent.push({ url, init });
                    if (failTransport) throw new Error("private-transport-canary");
                    return { response: { status: resultStatus }, data: { status: resultStatus === 200 ? "success" : "conflict" } };
                },
            };
            throw new Error("unexpected import: " + name);
        },
    });
    return loadedModule.exports;
}
const get = load("src/app/api/automation/deliveries/route.ts").GET;
const post = load("src/app/api/automation/deliveries/[deliveryId]/reconcile/route.ts").POST;
const req = {
    nextUrl: new URL("https://fixture.invalid/api/automation/deliveries?limit=2&after=opaque&ownership=system,unresolved&user_id=system"),
    headers: new Headers({ "x-v8-admin-role": "ADMIN", "x-v8-agent-os-user-email": "forged-owner", "x-v8-agent-os-secret": "forged-relay" }),
    text: async () => JSON.stringify({ outcome: "completed", evidence: { observation: "fixture observation" } }),
};
const context = { params: Promise.resolve({ deliveryId: "delivery-fixture" }) };

assert.equal((await get(req)).status, 401);
assert.equal((await post(req, context)).status, 401);
assert.equal(sent.length, 0, "caller ADMIN header cannot create an authenticated session");
session = { user: { role: "USER", email: "non-admin" } };
assert.equal((await get(req)).status, 403);
assert.equal((await post(req, context)).status, 403);
assert.equal(sent.length, 0, "authenticated non-admin cannot relay");
session = { user: { role: "ADMIN", login: "real-owner" } };
assert.equal((await get(req)).status, 200);
assert.equal(sent.length, 1);
assert.match(sent[0].url, /after=opaque/);
assert.match(sent[0].url, /ownership=system%2Cunresolved/);
assert.ok(!sent[0].url.includes("user_id"));
assert.equal(sent[0].init.headers["x-v8-agent-os-user-email"], "real-owner");
assert.equal(sent[0].init.headers["x-v8-admin-role"], "ADMIN");
assert.equal(sent[0].init.headers["x-v8-agent-os-secret"], "synthetic-server-relay");
assert.equal((await post(req, context)).status, 200);
assert.equal(sent.length, 2);
assert.equal(sent[1].init.method, "POST");
assert.equal(sent[1].init.headers["x-v8-agent-os-user-email"], "real-owner");
assert.deepEqual(JSON.parse(sent[1].init.body), { outcome: "completed", evidence: { observation: "fixture observation" } });

for (const status of [404, 409, 422, 500]) {
    resultStatus = status;
    const before = sent.length;
    assert.equal((await post(req, context)).status, status);
    assert.equal(sent.length, before + 1, "POST must not retry itself");
}
const beforeOversize = sent.length;
assert.equal((await post({ ...req, text: async () => "x".repeat(16385) }, context)).status, 422);
assert.equal(sent.length, beforeOversize);
failTransport = true;
for (const response of [await get(req), await post(req, context)]) {
    assert.equal(response.status, 502);
    assert.ok(!(await response.text()).includes("private-transport-canary"));
}
console.log("Automation reconciliation Admin route auth/relay/pagination/error checks passed.");

