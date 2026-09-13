const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");
const ts = require("typescript");
const { DatabaseSync } = require("node:sqlite");
const root = path.resolve(__dirname, "..");

function loader(mocks = {}) {
  const cache = new Map();
  return function load(name) {
    if (Object.hasOwn(mocks, name)) return mocks[name];
    if (!name.startsWith("@/")) return require(name);
    if (cache.has(name)) return cache.get(name);
    const file = path.join(root, name.slice(2) + ".ts");
    const module = { exports: {} };
    cache.set(name, module.exports);
    const output = ts.transpileModule(fs.readFileSync(file, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
    vm.runInThisContext(`(function(require,module,exports){${output}\n})`, { filename: file })(load, module, module.exports);
    cache.set(name, module.exports);
    return module.exports;
  };
}
function memoryStorage() {
  const values = new Map();
  return { values, read: async (key) => values.get(key) ?? null, write: async (key, value) => values.set(key, value) };
}
function sqliteMock() {
  const db = new DatabaseSync(":memory:");
  const adapter = {
    execAsync: async (sql) => db.exec(sql),
    getFirstAsync: async (sql, args = []) => db.prepare(sql).get(...args),
    getAllAsync: async (sql, args = []) => db.prepare(sql).all(...args),
    runAsync: async (sql, args = []) => db.prepare(sql).run(...args),
    prepareAsync: async (sql) => { const statement = db.prepare(sql); return { executeAsync: async (args) => statement.run(...args), finalizeAsync: async () => {} }; },
    withTransactionAsync: async (action) => { db.exec("BEGIN"); try { await action(); db.exec("COMMIT"); } catch (error) { db.exec("ROLLBACK"); throw error; } },
  };
  return { openDatabaseAsync: async () => adapter, db };
}
const tick = () => new Promise((resolve) => setImmediate(resolve));

test("same IDs in independent profiles / serving instances isolate SQL messages, cursors and tombstones", async () => {
  const sqlite = sqliteMock();
  const load = loader({ "expo-sqlite": sqlite });
  const { phoneAuthorityKey, phoneSessionKey, phoneResourceKey } = load("@/src/lib/phone-identity");
  const { createLocalDatabase } = load("@/src/services/LocalDatabaseService");
  const authorityA = phoneAuthorityKey({ instanceId: "Instance", principalId: "User", profileId: "A" });
  const authorityB = phoneAuthorityKey({ instanceId: "Instance", principalId: "User", profileId: "B" });
  const a = createLocalDatabase(authorityA, "serving-A"), b = createLocalDatabase(authorityB, "serving-A"), c = createLocalDatabase(authorityA, "serving-B");
  for (const [database, content] of [[a, "A"], [b, "B"], [c, "C"]]) {
    await database.upsertMessages("session-1", [{ id: "message-1", ordinal: 1, content, turnId: "turn-1" }]);
    await database.setSyncCursor("session-1", content);
  }
  await a.deleteMessages("session-1", ["message-1"]);
  await a.upsertMessages("session-1", [{ id: "message-1", content: "late-A" }]);
  assert.deepEqual(await a.getMessages("session-1"), []);
  assert.equal((await b.getLatestTurnMessages("session-1"))[0].content, "B");
  assert.equal((await c.getMessages("session-1"))[0].content, "C");
  assert.equal(await b.getSyncCursor("session-1"), "B");
  await a.deleteSessionData("session-1");
  assert.equal(await c.getSyncCursor("session-1"), "C");
  assert.notEqual(phoneResourceKey(phoneSessionKey(authorityA, "serving-A", "session-1"), "file", "same", "1"), phoneResourceKey(phoneSessionKey(authorityB, "serving-A", "session-1"), "file", "same", "1"));
  assert.notEqual(phoneAuthorityKey({ instanceId: "instance", principalId: "User", profileId: "A" }), authorityA);
  assert.throws(() => phoneAuthorityKey({ instanceId: "", principalId: "User", profileId: "A" }));
  sqlite.db.close();
});

test("draft A → B → A restores text, selection, attachment, references and queue after process restart", async () => {
  const storage = memoryStorage();
  const { PhoneDraftStore } = loader({ "@/src/lib/mobile-storage": {} })("@/src/lib/phone-drafts");
  const store = new PhoneDraftStore(storage);
  for (const [key, text] of [["A", "中文 A"], ["B", "中文 B"]]) {
    await store.hydrate(key);
    store.set(key, "input", text); store.set(key, "selection", { start: 1, end: 2 });
    store.set(key, "files", [{ id: "file-1", uri: `file:///${key}` }]);
    store.set(key, "plugins", [{ pluginId: key }]);
    store.set(key, "queue", [{ id: "queue-1", content: text }]);
  }
  await store.flushAll();
  const restored = new PhoneDraftStore(storage);
  await restored.hydrate("A"); await restored.hydrate("B");
  assert.equal(restored.get("A").values.input, "中文 A");
  assert.equal(restored.get("B").values.files[0].uri, "file:///B");
  assert.deepEqual(restored.get("A").values.selection, { start: 1, end: 2 });
  assert.equal(restored.get("B").values.plugins[0].pluginId, "B");
  assert.equal(restored.get("A").values.queue[0].content, "中文 A");
});

test("slow acceptance never erases v2; unrelated queue updates do not prevent v1 acknowledgement", async () => {
  const storage = memoryStorage();
  const { PhoneDraftStore } = loader({ "@/src/lib/mobile-storage": {} })("@/src/lib/phone-drafts");
  const store = new PhoneDraftStore(storage);
  await store.hydrate("A"); store.set("A", "input", "v1");
  const v1 = store.get("A").composerRevision;
  store.set("A", "queue", [{ id: "queue" }]);
  assert.equal(store.compareAndSet("A", v1, { input: "" }), true);
  store.set("A", "input", "v1"); const submitted = store.get("A").composerRevision;
  store.set("A", "input", "v2");
  assert.equal(store.compareAndSet("A", submitted, { input: "" }), false);
  assert.equal(store.get("A").values.input, "v2");
  await store.flushAll();
});

test("SQLite write rejection is visible, keeps the in-memory draft, and recovers on explicit flush", async () => {
  const storage = memoryStorage(); let fail = true;
  const { PhoneDraftStore } = loader({ "@/src/lib/mobile-storage": {} })("@/src/lib/phone-drafts");
  const store = new PhoneDraftStore({ ...storage, write: async (key, value) => { if (fail) throw new Error("disk full"); await storage.write(key, value); } });
  await store.hydrate("A"); store.set("A", "input", "keep");
  await assert.rejects(store.flush("A"), /not saved/);
  assert.equal(store.get("A").values.input, "keep"); assert.match(store.get("A").error, /not saved/);
  fail = false; await store.flush("A"); assert.equal(store.get("A").error, "");
});

test("SecureStore rejection cannot resolve as success or publish a metadata pointer", async () => {
  const storage = memoryStorage();
  const secure = new Map(); let fail = false;
  const load = loader({ "react-native": { Platform: { OS: "android" } }, "expo-sqlite/kv-store": {
    getItem: storage.read, setItem: storage.write, removeItem: async (key) => storage.values.delete(key),
  }, "expo-secure-store": {
    getItemAsync: async (key) => secure.get(key) || null,
    setItemAsync: async (key, value) => { if (fail) throw new Error("native rejected value"); secure.set(key, value); },
    deleteItemAsync: async (key) => secure.delete(key),
  }, "@/src/lib/admin-client": { normalizeAdminBaseUrl: (url) => url.replace(/\/+$/, "") } });
  const profiles = load("@/src/lib/admin-connection-profiles");
  const old = [{ id: "A", label: "A", adminBaseUrl: "https://a.invalid", instanceId: "A", accessToken: "synthetic-A", refreshToken: "synthetic-rA", lastUsedAt: "1" }];
  await profiles.writeAdminConnectionProfiles(old); await profiles.writeActiveAdminConnectionProfileId("A");
  const directory = storage.values.get("v8.phone.profiles.v2");
  assert.ok(!directory.includes("synthetic")); assert.ok(old[0].credentialRef); assert.equal(old[0].accessToken, undefined);
  fail = true;
  await assert.rejects(profiles.writeAdminConnectionProfiles([...old, { id: "B", adminBaseUrl: "https://b.invalid", accessToken: "synthetic-B", refreshToken: "synthetic-rB" }]), /not saved/);
  assert.equal(storage.values.get("v8.phone.profiles.v2"), directory);
  assert.equal(await profiles.readActiveAdminConnectionProfileId(), "A");
  assert.equal((await profiles.readProfileCredentials(old[0])).accessToken, "synthetic-A");
});

test("same-session selection executes the real callback without touching composer, attachment or selection", async () => {
  const filename = path.join(root, "src/screens/ChatScreen.tsx");
  const source = ts.createSourceFile(filename, fs.readFileSync(filename, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let callback;
  function visit(node) { if (ts.isVariableDeclaration(node) && node.name.getText(source) === "handleSelectConversation") callback = node.initializer.arguments[0].getText(source); ts.forEachChild(node, visit); }
  visit(source); assert.ok(callback);
  const effects = [];
  const code = ts.transpileModule(`const callback = ${callback};`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  const context = vm.createContext({ activeConversationIdRef: { current: "session-1" }, setHistoryOpen: (value) => effects.push(["drawer", value]) });
  vm.runInContext(code + "\nthis.run = callback;", context);
  await context.run({ sessionId: "session-1", id: "different-index-id" });
  assert.deepEqual(effects, [["drawer", false]]);
});

function transportLoader() {
  return loader({ "@/src/lib/admin-client": { buildAdminApiUrl: (base, route) => base + route,
    parseJsonSafe: (response) => response.json(), streamSse: async () => {}, streamSseWithXmlHttpRequest: async () => {} } })("@/src/lib/phone-transport");
}
function transport(PhoneTransport, overrides = {}) {
  return new PhoneTransport({ endpoints: ["https://a.invalid", "https://alias-a.invalid"], credentials: { accessToken: "synthetic-old", refreshToken: "synthetic-refresh" },
    principalId: "User", native: false, persistRefresh: async () => {}, onEndpoint() {}, onClock() {}, ...overrides });
}
test("ten 401 reads share refresh and bound concurrency, while other profile remains usable", async () => {
  const { PhoneTransport } = transportLoader(); const oldFetch = global.fetch;
  let refreshes = 0, inFlight = 0, max = 0;
  global.fetch = async (url, init) => {
    if (url.endsWith("/auth/refresh")) { refreshes++; await tick(); return Response.json({ accessToken: "synthetic-new", refreshToken: "synthetic-rnew", user: { id: "User" } }); }
    inFlight++; max = Math.max(max, inFlight); await tick(); inFlight--;
    return new Headers(init.headers).get("Authorization") === "Bearer synthetic-old" ? Response.json({}, { status: 401 }) : Response.json({ ok: true });
  };
  const a = transport(PhoneTransport); const b = transport(PhoneTransport, { endpoints: ["https://b.invalid"], credentials: { accessToken: "synthetic-B", refreshToken: "synthetic-rB" } });
  try {
    const results = await Promise.all(Array.from({ length: 10 }, () => a.authorizedFetch("/read").then((response) => response.json())));
    assert.ok(results.every((result) => result.ok)); assert.equal(refreshes, 1); assert.ok(max <= 2);
    assert.equal((await (await b.authorizedFetch("/read")).json()).ok, true);
  } finally { a.dispose(); b.dispose(); global.fetch = oldFetch; }
});

test("disposing A rejects late results even when fetch ignores abort; mutations never replay to aliases", async () => {
  const { PhoneTransport } = transportLoader(); const oldFetch = global.fetch;
  let resolve; let requests = 0;
  global.fetch = () => { requests++; return new Promise((done) => { resolve = done; }); };
  const a = transport(PhoneTransport);
  try {
    const request = a.authorizedFetch("/read"); await tick(); a.dispose(); resolve(Response.json({ from: "A" }));
    await assert.rejects(request, { name: "AbortError" });
    const mutation = transport(PhoneTransport);
    global.fetch = async () => { requests++; throw new Error("network acceptance unknown"); };
    await assert.rejects(mutation.authorizedFetch("/write", { method: "POST", body: "intent" }), /unknown/);
    assert.equal(requests, 2); mutation.dispose();
  } finally { a.dispose(); global.fetch = oldFetch; }
});
