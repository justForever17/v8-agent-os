const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const adminRoot = path.resolve(__dirname, "..");
const sourcePath = path.join(adminRoot, "src", "lib", "server", "engine-health-snapshot.ts");
const source = fs.readFileSync(sourcePath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
    esModuleInterop: true,
  },
  fileName: sourcePath,
}).outputText;

function loadHealthModule(resolveOrigin = () => "http://engine.test") {
  const target = { exports: {} };
  const localRequire = (specifier) => specifier === "@/lib/server/runtime-config"
    ? { resolveEngineOrigin: resolveOrigin }
    : require(specifier);
  new Function("require", "module", "exports", compiled)(localRequire, target, target.exports);
  return target.exports;
}

test("cold engine health reads stay local, then explicit refreshes coalesce and warm reads reuse data", async () => {
  const originalFetch = global.fetch;
  let fetchCalls = 0;
  let resolveFetch;
  global.fetch = async () => {
    fetchCalls += 1;
    return new Promise((resolve) => {
      resolveFetch = () => resolve({
        ok: true,
        json: async () => ({ status: "ok", featurePacks: [] }),
      });
    });
  };

  try {
    const { readEngineHealthSnapshot } = loadHealthModule();
    const first = await readEngineHealthSnapshot();
    const second = await readEngineHealthSnapshot();

    assert.equal(first.data, null);
    assert.equal(first.refreshing, true);
    assert.equal(second.refreshing, true);
    assert.equal(fetchCalls, 0, "ordinary reads must not start request-lifetime background fetches");

    const forcedPromise = readEngineHealthSnapshot({ force: true, waitForFresh: true });
    const joinedPromise = readEngineHealthSnapshot({ force: true, waitForFresh: true });
    let forcedSettled = false;
    void forcedPromise.then(() => { forcedSettled = true; });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(fetchCalls, 1, "force must join the in-flight health request");
    assert.equal(forcedSettled, false, "explicit refresh must wait for real Engine health");
    resolveFetch();

    const [forced, joined] = await Promise.all([forcedPromise, joinedPromise]);
    assert.equal(forced.refreshing, false);
    assert.equal(forced.available, true);
    assert.equal(forced.data.status, "ok");
    assert.equal(joined.data.status, "ok");
    assert.equal(fetchCalls, 1);

    const explicitRefresh = readEngineHealthSnapshot({ force: true, waitForFresh: true });
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(fetchCalls, 2, "explicit refresh must bypass a fresh snapshot");
    resolveFetch();
    await explicitRefresh;

    const fresh = await readEngineHealthSnapshot();
    assert.equal(fresh.refreshing, false);
    assert.equal(fetchCalls, 2, "fresh snapshots must not re-read Engine health");
  } finally {
    global.fetch = originalFetch;
  }
});

test("a synchronous explicit health failure clears the request and a forced retry can recover", async () => {
  const originalFetch = global.fetch;
  let fetchCalls = 0;
  global.fetch = () => {
    fetchCalls += 1;
    throw new Error("sync_health_failure");
  };

  try {
    const { readEngineHealthSnapshot } = loadHealthModule();
    const failed = await readEngineHealthSnapshot({ force: true, waitForFresh: true });
    assert.equal(failed.refreshing, false);
    assert.equal(failed.available, false);
    assert.match(failed.error, /sync_health_failure/);
    assert.equal(fetchCalls, 1);

    const local = await readEngineHealthSnapshot();
    assert.equal(local.refreshing, false);
    assert.equal(fetchCalls, 1, "ordinary reads stay local during retry backoff");

    global.fetch = async () => {
      fetchCalls += 1;
      return { ok: true, json: async () => ({ status: "recovered" }) };
    };
    const recovered = await readEngineHealthSnapshot({ force: true, waitForFresh: true });
    assert.equal(recovered.refreshing, false);
    assert.equal(recovered.available, true);
    assert.equal(recovered.data.status, "recovered");
    assert.equal(fetchCalls, 2);
  } finally {
    global.fetch = originalFetch;
  }
});

test("health snapshots are isolated by the current Engine origin", async () => {
  const originalFetch = global.fetch;
  let origin = "http://engine-a.test";
  const urls = [];
  global.fetch = async (url) => {
    urls.push(String(url));
    return { ok: true, json: async () => ({ origin }) };
  };

  try {
    const { readEngineHealthSnapshot } = loadHealthModule(() => origin);
    const first = await readEngineHealthSnapshot({ force: true, waitForFresh: true });
    assert.equal(first.data.origin, "http://engine-a.test");

    origin = "http://engine-b.test";
    const second = await readEngineHealthSnapshot({ force: true, waitForFresh: true });
    assert.equal(second.data.origin, "http://engine-b.test");
    assert.deepEqual(urls, ["http://engine-a.test/health", "http://engine-b.test/health"]);
  } finally {
    global.fetch = originalFetch;
  }
});
