const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const adminRoot = path.resolve(__dirname, "..");

function loadTypeScriptModule(relativePath, options = {}) {
  const source = fs.readFileSync(path.join(adminRoot, relativePath), "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: relativePath,
  }).outputText;
  const moduleRecord = { exports: {} };
  const localRequire = (specifier) => {
    if (Object.hasOwn(options.requireOverrides || {}, specifier)) {
      return options.requireOverrides[specifier];
    }
    return require(specifier);
  };
  const execute = new Function("require", "module", "exports", "fetch", "window", output);
  execute(localRequire, moduleRecord, moduleRecord.exports, options.fetchImpl || global.fetch, options.browser);
  return moduleRecord.exports;
}

function response(data) {
  return {
    ok: true,
    status: 200,
    json: async () => data,
  };
}

function systemBaseEnvelope(engineBaseUrl) {
  return {
    domain: "system-base",
    title: "System Base",
    summary: "",
    data: { bridge: { engineBaseUrl } },
    source: "config",
    savePath: "config.json",
    reloadRequired: false,
    warnings: [],
    advancedFields: [],
  };
}

function fakeBrowser() {
  const events = [];
  let reloads = 0;
  return {
    browser: {
      dispatchEvent(event) {
        events.push(event.type);
        return true;
      },
      location: {
        reload() {
          reloads += 1;
        },
      },
    },
    events,
    reloads: () => reloads,
  };
}

test("a normalized same-origin System Base save preserves the Admin cache and does not reload", async () => {
  const host = fakeBrowser();
  const nextEnvelope = systemBaseEnvelope("http://127.0.0.1:9530/v1");
  const cache = loadTypeScriptModule("src/lib/admin-client-cache.ts", { browser: host.browser });
  const config = loadTypeScriptModule("src/lib/config-registry.ts", {
    browser: host.browser,
    fetchImpl: async () => response(nextEnvelope),
    requireOverrides: {
      "@/i18n/admin-legacy": { ik: (key) => key },
      "@/lib/admin-client-cache": cache,
      "@/lib/locale": { translateCurrentClient: (value) => value },
    },
  });

  cache.primeAdminJsonCache("/api/config-registry/system-base", systemBaseEnvelope(" HTTP://127.0.0.1:9530/v1/ "));
  cache.primeAdminJsonCache("/api/models", { provider: "engine-a" });

  await config.saveConfigDomain("system-base", { data: nextEnvelope.data });

  assert.deepEqual(cache.peekAdminJsonCache("/api/models"), { provider: "engine-a" });
  assert.equal(cache.peekAdminJsonCache("/api/config-registry/system-base").data.bridge.engineBaseUrl, "http://127.0.0.1:9530/v1");
  assert.equal(host.reloads(), 0);
  assert.deepEqual(host.events, []);
});

test("a real Engine origin change clears all data, notifies subscribers, and reloads once", async () => {
  const host = fakeBrowser();
  const nextEnvelope = systemBaseEnvelope("http://127.0.0.1:19530/v1");
  const cache = loadTypeScriptModule("src/lib/admin-client-cache.ts", { browser: host.browser });
  const config = loadTypeScriptModule("src/lib/config-registry.ts", {
    browser: host.browser,
    fetchImpl: async () => response(nextEnvelope),
    requireOverrides: {
      "@/i18n/admin-legacy": { ik: (key) => key },
      "@/lib/admin-client-cache": cache,
      "@/lib/locale": { translateCurrentClient: (value) => value },
    },
  });
  let notifications = 0;
  cache.primeAdminJsonCache("/api/config-registry/system-base", systemBaseEnvelope("http://127.0.0.1:9530/v1"));
  cache.primeAdminJsonCache("/api/models", { provider: "engine-a" });
  cache.subscribeAdminJsonCache("/api/models", () => { notifications += 1; });

  await config.saveConfigDomain("system-base", { data: nextEnvelope.data });

  assert.equal(cache.peekAdminJsonCache("/api/models"), undefined);
  assert.equal(cache.peekAdminJsonCache("/api/config-registry/system-base"), undefined);
  assert.equal(notifications, 1);
  assert.deepEqual(host.events, [cache.ADMIN_ENGINE_ORIGIN_CHANGED_EVENT]);
  assert.equal(host.reloads(), 1);

  assert.equal(cache.applyAdminEngineOriginChange(
    "http://127.0.0.1:9530/v1",
    "http://127.0.0.1:19530/v1",
    { browser: host.browser, reload: true },
  ), false);
  assert.deepEqual(host.events, [cache.ADMIN_ENGINE_ORIGIN_CHANGED_EVENT]);
  assert.equal(host.reloads(), 1);
});

test("an Engine origin change aborts and rejects an old deferred request without refilling the cache", async () => {
  let resolveFetch;
  let requestSignal;
  const cache = loadTypeScriptModule("src/lib/admin-client-cache.ts", {
    fetchImpl: (_url, init) => {
      requestSignal = init.signal;
      return new Promise((resolve) => { resolveFetch = resolve; });
    },
  });

  const pending = cache.fetchAdminJson("/api/models");
  assert.equal(cache.getAdminJsonSnapshot("/api/models").isFetching, true);

  cache.applyAdminEngineOriginChange(
    "http://127.0.0.1:9530/v1",
    "http://127.0.0.1:19530/v1",
  );
  assert.equal(requestSignal.aborted, true);
  resolveFetch(response({ provider: "engine-a" }));

  await assert.rejects(pending, (error) => error?.code === "admin_engine_origin_changed");
  assert.equal(cache.peekAdminJsonCache("/api/models"), undefined);
  assert.equal(cache.getAdminJsonSnapshot("/api/models").isFetching, false);
});

test("the Engine origin boundary is SSR-safe even when reload is requested", () => {
  const cache = loadTypeScriptModule("src/lib/admin-client-cache.ts");
  cache.primeAdminJsonCache("/api/models", { provider: "engine-a" });

  assert.equal(cache.applyAdminEngineOriginChange(
    "http://127.0.0.1:9530/v1",
    "http://127.0.0.1:19530/v1",
    { reload: true },
  ), true);
  assert.equal(cache.peekAdminJsonCache("/api/models"), undefined);
});
