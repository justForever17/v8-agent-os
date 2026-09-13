const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");
const { NextRequest, NextResponse } = require("next/server");
const root = path.resolve(__dirname, "..");

function setup({ role = "ADMIN", authenticated = true, links = [], mutateStatus = 200 } = {}) {
  const calls = [];
  const mocks = {
    "next/server": { NextRequest, NextResponse },
    "@/lib/server/client-proxy": {
      requireClientContext: async () => authenticated ? { user: { id: "fixture-owner", role } } : NextResponse.json({}, { status: 401 }),
      fetchClientEngine: async (_req, target, init = {}) => {
        calls.push({ target, init });
        if (target.endsWith("/links")) return Response.json({ items: links });
        if (target.includes("/timeline")) return Response.json({ items: [{ id: "message-1", seq: 1, body: "fixture", metadata: { deliveryEnvelope: "must stay server-side" } }], previousCursor: null });
        return Response.json({ ok: mutateStatus === 200 }, { status: mutateStatus });
      },
    },
    "@/lib/server/runtime-config": { buildClientLinkManifest: () => ({ instanceId: "authority-A" }), resolveRequestOrigin: () => "http://fixture.invalid" },
  };
  function load(relative) {
    const source = fs.readFileSync(path.join(root, relative), "utf8");
    const module = { exports: {} };
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
    new Function("require", "module", "exports", code)((name) => mocks[name] || require(name), module, module.exports);
    return module.exports;
  }
  mocks["@/lib/server/client-supervisor-peers"] = load("src/lib/server/client-supervisor-peers.ts");
  return { calls, load };
}
const link = { linkId: "link-A", peerId: "peer-B", trustStatus: "trusted", remoteNickname: "Laptop", online: true, token: "synthetic-must-not-expose", metadata: { credentialRef: "synthetic-ref" } };
test("Phone peer list authenticates owner before any engine read", async () => {
  for (const options of [{ authenticated: false }, { role: "USER" }]) {
    const fixture = setup(options);
    const result = await fixture.load("src/app/api/client/supervisor-peers/route.ts").GET(new NextRequest("http://fixture.invalid/api/client/supervisor-peers"));
    assert.equal(result.status, options.authenticated === false ? 401 : 403); assert.equal(fixture.calls.length, 0);
  }
});
test("list contains only existing authorized links, minimal fields and explicit local session ownership", async () => {
  const fixture = setup({ links: [link, { ...link, linkId: "discovered", trustStatus: "discovered" }] });
  const result = await fixture.load("src/app/api/client/supervisor-peers/route.ts").GET(new NextRequest("http://fixture.invalid/api/client/supervisor-peers?limit=1"));
  const payload = await result.json(); assert.equal(payload.items.length, 1);
  assert.equal(payload.items[0].servingInstanceId, "authority-A"); assert.equal(payload.items[0].sessionId, "network_neighbor_link-A");
  assert.equal(payload.items[0].peerId, "peer-B"); assert.equal(payload.items[0].sessionKind, "local_neighbor");
  assert.ok(!JSON.stringify(payload).includes("synthetic"));
});
test("renaming binds the selected authorized link and cannot mutate endpoint or credential fields", async () => {
  const fixture = setup({ links: [link] });
  const result = await fixture.load("src/app/api/client/supervisor-peers/[linkId]/route.ts").PATCH(
    new NextRequest("http://fixture.invalid/api/client/supervisor-peers/link-A", { method: "PATCH", body: JSON.stringify({ remoteNickname: "Office", endpoint: "https://untrusted.invalid", credentialRef: "no" }) }),
    { params: Promise.resolve({ linkId: "link-A" }) });
  assert.equal(result.status, 200);
  const mutation = fixture.calls.at(-1); assert.equal(mutation.target, "/network-supervisor/neighbors/link-A");
  assert.deepEqual(JSON.parse(mutation.init.body), { remoteNickname: "Office" });
});
test("revoked / unknown links never reach timeline or mutation; errors cannot return success", async () => {
  const fixture = setup({ links: [link] });
  const unknown = await fixture.load("src/app/api/client/supervisor-peers/[linkId]/timeline/route.ts").GET(new NextRequest("http://fixture.invalid/"), { params: Promise.resolve({ linkId: "other" }) });
  assert.equal(unknown.status, 404); assert.equal(fixture.calls.length, 1);
  const failed = setup({ links: [link], mutateStatus: 503 });
  const result = await failed.load("src/app/api/client/supervisor-peers/[linkId]/route.ts").DELETE(new NextRequest("http://fixture.invalid/"), { params: Promise.resolve({ linkId: "link-A" }) });
  assert.equal(result.status, 503);
});
test("timeline preserves content but strips runtime envelopes and returns the actual serving location", async () => {
  const fixture = setup({ links: [link] });
  const result = await fixture.load("src/app/api/client/supervisor-peers/[linkId]/timeline/route.ts").GET(new NextRequest("http://fixture.invalid/?before=42"), { params: Promise.resolve({ linkId: "link-A" }) });
  const payload = await result.json(); assert.equal(payload.items[0].body, "fixture"); assert.equal(payload.items[0].metadata, undefined);
  assert.equal(payload.peer.servingInstanceId, "authority-A"); assert.match(fixture.calls.at(-1).target, /before=42/);
});
