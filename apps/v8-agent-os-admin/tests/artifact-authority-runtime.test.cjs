const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const { NextRequest, NextResponse } = require("next/server");

const adminRoot = path.resolve(__dirname, "..");
const internalSecret = "artifact-authority-test-secret";
const internalSurfaceUser = "surface-resource@internal";

function loadTypeScriptModule(relativePath, options = {}) {
  const filename = path.join(adminRoot, relativePath);
  const source = fs.readFileSync(filename, "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: {
      esModuleInterop: true,
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: filename,
  }).outputText;
  const moduleRecord = { exports: {} };
  const localRequire = (specifier) => Object.hasOwn(options.requireOverrides || {}, specifier)
    ? options.requireOverrides[specifier]
    : require(specifier);
  const execute = new Function("require", "module", "exports", "fetch", output);
  execute(localRequire, moduleRecord, moduleRecord.exports, options.fetchImpl || global.fetch);
  return moduleRecord.exports;
}

function loadRequestAuth() {
  const serviceAuth = loadTypeScriptModule("src/lib/service-auth.ts", {
    requireOverrides: {
      "@/i18n/internal-readable": { INTERNAL_READABLE: { k4b0c4c45f3: "missing secret" } },
      "@/lib/server/runtime-config": { resolveInternalSecret: () => internalSecret },
    },
  });
  return loadTypeScriptModule("src/lib/server/request-auth.ts", {
    requireOverrides: {
      "next/server": { NextRequest, NextResponse },
      "@/lib/auth": { auth: async () => null },
      "@/lib/service-auth": serviceAuth,
    },
  });
}

function memoryRouteOverrides(requestAuth) {
  return {
    "next/server": { NextRequest, NextResponse },
    "@/lib/server/artifact-surface": {
      normalizeArtifactForAdminSurface: (value) => value,
      normalizeArtifactsForAdminSurface: (value) => value,
    },
    "@/lib/server/request-auth": requestAuth,
    "@/lib/server/runtime-config": { resolveEngineOrigin: () => "http://engine.test" },
  };
}

function serviceHeaders() {
  return {
    "x-v8-agent-os-secret": internalSecret,
    "x-v8-agent-os-user-email": internalSurfaceUser,
  };
}

test("memory artifact list, detail, and content routes return 401 before Engine fetch", async () => {
  const requestAuth = loadRequestAuth();
  let fetchCount = 0;
  const fetchImpl = async () => {
    fetchCount += 1;
    throw new Error("unauthorized route must not reach Engine");
  };
  const options = {
    fetchImpl,
    requireOverrides: memoryRouteOverrides(requestAuth),
  };
  const listRoute = loadTypeScriptModule("src/app/api/memory/artifacts/route.ts", options);
  const detailRoute = loadTypeScriptModule("src/app/api/memory/artifacts/[id]/route.ts", options);
  const contentRoute = loadTypeScriptModule("src/app/api/memory/artifacts/[id]/content/route.ts", options);

  const responses = await Promise.all([
    listRoute.GET(new NextRequest("http://admin.test/api/memory/artifacts?sessionId=session-a")),
    detailRoute.GET(
      new NextRequest("http://admin.test/api/memory/artifacts/artifact-a?sessionId=session-a"),
      { params: Promise.resolve({ id: "artifact-a" }) },
    ),
    contentRoute.GET(
      new NextRequest("http://admin.test/api/memory/artifacts/artifact-a/content?sessionId=session-a"),
      { params: Promise.resolve({ id: "artifact-a" }) },
    ),
  ]);

  assert.deepEqual(responses.map((response) => response.status), [401, 401, 401]);
  assert.equal(fetchCount, 0);
});

test("valid service headers authorize memory content origin fetch with exact session scope", async () => {
  const requestAuth = loadRequestAuth();
  let originRequest = null;
  const contentRoute = loadTypeScriptModule("src/app/api/memory/artifacts/[id]/content/route.ts", {
    requireOverrides: memoryRouteOverrides(requestAuth),
    fetchImpl: async (url, init) => {
      originRequest = { url: String(url), init };
      return new Response("artifact-body", {
        status: 206,
        headers: {
          "Accept-Ranges": "bytes",
          "Content-Range": "bytes 0-12/13",
          "Content-Type": "image/png",
        },
      });
    },
  });
  const req = new NextRequest(
    "http://admin.test/api/memory/artifacts/artifact-a/content?sessionId=session-a&download=1",
    { headers: { ...serviceHeaders(), Range: "bytes=0-12" } },
  );

  assert.equal(await requestAuth.resolveAuthorizedUserEmail(req), internalSurfaceUser);
  const response = await contentRoute.GET(req, { params: Promise.resolve({ id: "artifact-a" }) });

  assert.equal(response.status, 206);
  assert.equal(await response.text(), "artifact-body");
  assert.equal(originRequest.url, "http://engine.test/v1/artifacts/artifact-a/content?sessionId=session-a&download=true");
  assert.equal(originRequest.init.headers.get("Range"), "bytes=0-12");
});

test("signed content URL covers session and internal signed fetch remains service-authorized", async () => {
  let internalRequest = null;
  const runtimeConfig = {
    resolveAdminApiBaseUrl: () => "http://admin.internal/api",
    resolveInternalSecret: () => internalSecret,
    resolveReachableAdminPublicBaseUrl: () => "http://admin.test",
    resolveReachableClientSurfaceOrigin: (value) => String(value || "http://admin.test").replace(/\/+$/, ""),
  };
  const signing = loadTypeScriptModule("src/lib/server/client-surface-resource.ts", {
    requireOverrides: { "@/lib/server/runtime-config": runtimeConfig },
    fetchImpl: async (url, init) => {
      internalRequest = { url: String(url), init };
      return new Response("ok");
    },
  });
  const signedUrl = signing.buildSignedClientSurfaceUrl(
    "/api/client/artifacts/artifact-a/content?sessionId=session-a",
    { publicBaseUrl: "http://admin.test" },
  );

  assert.equal(signing.verifySignedClientSurfaceRequest(new NextRequest(signedUrl)), true);
  const tampered = new URL(signedUrl);
  tampered.searchParams.set("sessionId", "session-b");
  assert.equal(signing.verifySignedClientSurfaceRequest(new NextRequest(tampered)), false);
  const missingSession = new URL(signedUrl);
  missingSession.searchParams.delete("sessionId");
  assert.equal(signing.verifySignedClientSurfaceRequest(new NextRequest(missingSession)), false);

  await signing.fetchSignedClientAdminPath(
    "/memory/artifacts/artifact-a/content?sessionId=session-a",
    { headers: { Range: "bytes=0-7" } },
  );
  assert.equal(internalRequest.url, "http://admin.internal/api/memory/artifacts/artifact-a/content?sessionId=session-a");
  assert.equal(internalRequest.init.headers.get("x-v8-agent-os-secret"), internalSecret);
  assert.equal(internalRequest.init.headers.get("x-v8-agent-os-user-email"), internalSurfaceUser);
  assert.equal(internalRequest.init.headers.get("Range"), "bytes=0-7");

  const requestAuth = loadRequestAuth();
  const internalAuthProbe = new NextRequest("http://admin.test/api/memory/artifacts", {
    headers: internalRequest.init.headers,
  });
  assert.equal(await requestAuth.resolveAuthorizedUserEmail(internalAuthProbe), internalSurfaceUser);
});
