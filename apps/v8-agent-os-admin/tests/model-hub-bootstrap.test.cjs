const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

const sourcePath = path.join(__dirname, "..", "src", "hooks", "use-model-hub-bootstrap.ts");
const source = ts.transpileModule(fs.readFileSync(sourcePath, "utf8"), {
  fileName: sourcePath,
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

function mount() {
  const slots = [];
  const effects = [];
  const requests = [];
  let cursor = 0;
  const React = {
    useState(initial) {
      const index = cursor++;
      slots[index] ??= { value: typeof initial === "function" ? initial() : initial };
      return [slots[index].value, next => {
        slots[index].value = typeof next === "function" ? next(slots[index].value) : next;
      }];
    },
    useRef(value) {
      const index = cursor++;
      return slots[index] ??= { current: value };
    },
    useCallback(fn) { cursor++; return fn; },
    useEffect(fn) { cursor++; effects.push(fn); },
  };
  const imports = name => {
    if (name === "react") return React;
    if (name.endsWith("admin-client-cache")) {
      return {
        peekAdminJsonCache: () => undefined,
        fetchAdminJson: () => {
          const request = deferred();
          requests.push(request);
          return request.promise;
        },
      };
    }
    if (name.endsWith("model-hub-domain")) {
      return {
        MODEL_HUB_BOOTSTRAP_URL: "/api/model-hub/bootstrap",
        mergeAudioConfig: value => value || { draft: false },
        preserveModelOrder: (_current, incoming) => incoming,
      };
    }
    return {};
  };
  const moduleRecord = { exports: {} };
  new Function("require", "module", "exports", source)(imports, moduleRecord, moduleRecord.exports);
  function render() {
    cursor = 0;
    effects.length = 0;
    const value = moduleRecord.exports.useModelHubBootstrap();
    const pendingEffects = effects.splice(0);
    pendingEffects.forEach(effect => effect());
    return value;
  }
  return { requests, render };
}

function payload(id, audioConfig = { id }) {
  return {
    providers: [{ id, code: id, name: id, models: [] }],
    models: [{ id, providerId: id, modelId: id, type: "TEXT", isEnabled: true }],
    hubEnvelope: { data: { models: [], providersOverview: [] } },
    audioConfig,
    defaultModel: { modelRef: `${id}::default` },
    catalog: { providers: [] },
  };
}

test("a late bootstrap response cannot replace the newest refresh snapshot", async () => {
  const ui = mount();
  let state = ui.render();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(ui.requests.length, 1);
  const first = ui.requests[0];
  const newer = state.fetchData(true);
  assert.equal(ui.requests.length, 2);
  ui.requests[1].resolve(payload("new"));
  await newer;
  state = ui.render();
  first.resolve(payload("old"));
  await Promise.resolve();
  state = ui.render();
  assert.equal(state.providers[0].id, "new");
  assert.equal(state.models[0].id, "new");
  assert.equal(state.defaultModelRef, "new::default");
});

test("a bootstrap refresh preserves an audio draft and exposes request errors", async () => {
  const ui = mount();
  let state = ui.render();
  await new Promise(resolve => setImmediate(resolve));
  const request = ui.requests[0];
  state.setAudioConfig({ draft: true });
  state = ui.render();
  request.resolve(payload("server", { draft: false, server: true }));
  await Promise.resolve();
  state = ui.render();
  assert.deepEqual(state.audioConfig, { draft: true });

  const failed = mount();
  state = failed.render();
  await new Promise(resolve => setImmediate(resolve));
  failed.requests[0].reject(new Error("bootstrap unavailable"));
  await Promise.resolve();
  state = failed.render();
  assert.equal(state.bootstrapError, "bootstrap unavailable");
  assert.equal(state.isLoading, false);
});
