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

test("oversized or corrupt cache rows force a full server read without truncating canonical content or touching B", async () => {
  const sqlite = sqliteMock();
  const load = loader({ "expo-sqlite": sqlite });
  const { createLocalDatabase, MAX_LOCAL_MESSAGE_JSON_CHARS } = load("@/src/services/LocalDatabaseService");
  const { phoneSessionKey } = load("@/src/lib/phone-identity");
  const a = createLocalDatabase("authority-A", "instance"), b = createLocalDatabase("authority-B", "instance");
  const message = { id: "message-1", content: "x".repeat(MAX_LOCAL_MESSAGE_JSON_CHARS + 1), turnId: "turn-1" };
  await b.upsertMessages("session-1", [{ id: "message-1", content: "keep-B", turnId: "turn-1" }]);
  await b.setSyncCursor("session-1", "B");
  await a.upsertMessages("session-1", [message]); await a.setSyncCursor("session-1", "cannot-cover-unpersisted-message");
  assert.equal(await a.getSyncCursor("session-1"), ""); assert.equal((await a.getMessages("session-1")).length, 0);
  assert.equal(message.content.length, MAX_LOCAL_MESSAGE_JSON_CHARS + 1);
  await a.deleteSessionData("session-1");
  await a.upsertMessages("session-1", [{ id: "message-1", content: "A", turnId: "turn-1" }]);
  await a.setSyncCursor("session-1", "A");
  sqlite.db.prepare("UPDATE messages SET raw_json = '{broken' WHERE session_key = ?").run(phoneSessionKey("authority-A", "instance", "session-1"));
  assert.deepEqual(await a.getLatestTurnMessages("session-1"), []); assert.equal(await a.getSyncCursor("session-1"), "");
  assert.equal((await b.getMessages("session-1"))[0].content, "keep-B"); assert.equal(await b.getSyncCursor("session-1"), "B");
  sqlite.db.close();
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

test("process restart turns an in-flight submission into an unknown receipt without changing its id", async () => {
  const storage = memoryStorage();
  const { PhoneDraftStore } = loader({ "@/src/lib/mobile-storage": {} })("@/src/lib/phone-drafts");
  const first = new PhoneDraftStore(storage);
  await first.hydrate("A"); first.set("A", "input", "unsent text");
  first.set("A", "pendingIntent", { clientMessageId: "intent-unique", state: "submitting", fingerprint: "same-intent" });
  await first.flush("A");
  const restarted = new PhoneDraftStore(storage); await restarted.hydrate("A");
  assert.deepEqual(restarted.get("A").values.pendingIntent, { clientMessageId: "intent-unique", state: "acceptance_unknown", fingerprint: "same-intent" });
  assert.equal(restarted.get("A").values.input, "unsent text");
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
  await profiles.updateAdminConnectionProfiles(() => old); await profiles.writeActiveAdminConnectionProfileId("A");
  const directory = storage.values.get("v8.phone.profiles.v2");
  assert.ok(!directory.includes("synthetic")); assert.ok(old[0].credentialRef); assert.equal(old[0].accessToken, undefined);
  fail = true;
  await assert.rejects(profiles.updateAdminConnectionProfiles((current) => [...current, { id: "B", adminBaseUrl: "https://b.invalid", accessToken: "synthetic-B", refreshToken: "synthetic-rB" }]), /not saved/);
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

test("delayed refresh A and add/remove B serialize read-modify-write; replaced and removed slots are collected", async () => {
  const storage = memoryStorage(), secure = new Map();
  let releaseWrite, delay = false;
  const load = loader({ "@/src/lib/mobile-storage": {
    getStoredValue: storage.read, readMetadata: storage.read,
    writeMetadata: async (key, value) => {
      if (delay && key === "v8.phone.profiles.v2") { delay = false; await new Promise((resolve) => { releaseWrite = resolve; }); }
      await storage.write(key, value);
    },
    readSecureItem: async (key) => secure.get(key), writeSecureItem: async (key, value) => secure.set(key, value), deleteSecureItem: async (key) => secure.delete(key),
  }, "@/src/lib/admin-client": { normalizeAdminBaseUrl: (url) => url } });
  const profiles = load("@/src/lib/admin-connection-profiles");
  const make = (id) => ({ id, label: id, instanceId: id, adminBaseUrl: `https://${id}.invalid`, accessToken: `synthetic-${id}`, refreshToken: `synthetic-r${id}`, lastUsedAt: "1" });
  await profiles.updateAdminConnectionProfiles(() => [make("A")]);
  const oldRef = (await profiles.readAdminConnectionProfiles())[0].credentialRef;
  delay = true;
  const refresh = profiles.updateAdminConnectionProfiles((current) => current.map((profile) => profile.id === "A" ? { ...profile, accessToken: "synthetic-new-A", refreshToken: "synthetic-new-rA" } : profile));
  while (!releaseWrite) await tick();
  const add = profiles.updateAdminConnectionProfiles((current) => [...current, make("B")]);
  releaseWrite(); await Promise.all([refresh, add]);
  let directory = await profiles.readAdminConnectionProfiles();
  assert.deepEqual(directory.map((profile) => profile.id).sort(), ["A", "B"]);
  assert.equal(secure.has(oldRef), false); assert.equal(secure.size, 2);
  assert.equal((await profiles.readProfileCredentials(directory.find((profile) => profile.id === "A"))).accessToken, "synthetic-new-A");
  const b = directory.find((profile) => profile.id === "B");
  await Promise.all([
    profiles.updateAdminConnectionProfiles((current) => current.map((profile) => profile.id === "A" ? { ...profile, label: "renamed-A" } : profile)),
    profiles.updateAdminConnectionProfiles((current) => current.filter((profile) => profile.id !== "B")),
  ]);
  directory = await profiles.readAdminConnectionProfiles();
  assert.equal(directory.length, 1); assert.equal(directory[0].label, "renamed-A"); assert.equal(secure.has(b.credentialRef), false);
  let published = false;
  await assert.rejects(profiles.commitActiveAdminConnectionProfile("B", b.credentialRef, () => { published = true; }), /changed/);
  assert.equal(published, false); assert.notEqual(await profiles.readActiveAdminConnectionProfileId(), "B");
});

test("activation pointer publishes in order and rejects a removed or rotated credential target", async () => {
  const storage = memoryStorage(), secure = new Map(); let releasePointer, block = false;
  const load = loader({ "@/src/lib/mobile-storage": {
    getStoredValue: async (key) => storage.read(key === "activeAdminConnectionProfileId" ? "v8.phone.activeAdminConnectionProfileId" : key), readMetadata: storage.read,
    writeMetadata: async (key, value) => {
      if (block && key === "v8.phone.activeAdminConnectionProfileId") { block = false; await new Promise((resolve) => { releasePointer = resolve; }); }
      await storage.write(key, value);
    }, readSecureItem: async (key) => secure.get(key), writeSecureItem: async (key, value) => secure.set(key, value), deleteSecureItem: async (key) => secure.delete(key),
  }, "@/src/lib/admin-client": { normalizeAdminBaseUrl: (url) => url } });
  const profiles = load("@/src/lib/admin-connection-profiles");
  const initial = await profiles.updateAdminConnectionProfiles(() => ["A", "B"].map((id) => ({ id, label: id, adminBaseUrl: `https://${id}.invalid`, accessToken: `synthetic-${id}`, refreshToken: `synthetic-r${id}`, lastUsedAt: "1" })));
  const a = initial[0], b = initial[1], activated = [];
  block = true;
  const first = profiles.commitActiveAdminConnectionProfile("A", a.credentialRef, () => activated.push("A"));
  while (!releasePointer) await tick();
  const second = profiles.commitActiveAdminConnectionProfile("B", b.credentialRef, () => activated.push("B"));
  assert.equal(activated.length, 0); releasePointer(); await Promise.all([first, second]);
  assert.deepEqual(activated, ["A", "B"]); assert.equal(await profiles.readActiveAdminConnectionProfileId(), "B");
  await assert.rejects(profiles.updateAdminConnectionProfiles((current) => current.filter((profile) => profile.id !== "B")), /active connection/);
});

test("late response body remains abort-aware through parseJsonSafe rather than turning into an empty success", async () => {
  const { PhoneTransport } = transportLoader();
  const { parseJsonSafe } = loader({ "@/src/lib/locale": { translateCurrent: (value) => value } })("@/src/lib/admin-client");
  const oldFetch = global.fetch; let finish;
  global.fetch = async () => {
    const response = Response.json({});
    response.json = () => new Promise((resolve) => { finish = resolve; });
    return response;
  };
  const a = transport(PhoneTransport);
  try {
    const response = await a.authorizedFetch("/read");
    const result = parseJsonSafe(response); a.dispose(); finish({ owner: "A" });
    await assert.rejects(result, { name: "AbortError" });
  } finally { a.dispose(); global.fetch = oldFetch; }
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

test("terminal UTF-8 byte cursor never repeats a pipe prefix and generation changes restart at zero", () => {
  const { initialTerminalOutputCursor, mergeTerminalOutput } = loader()("@/src/lib/terminal-output-cursor");
  let state = initialTerminalOutputCursor();
  state = mergeTerminalOutput(state, 0, { output: "中文", outputCursor: 6, outputGeneration: "g1" });
  state = mergeTerminalOutput(state, 6, { output: "next", outputCursor: 10, outputGeneration: "g1" });
  assert.equal(state.output, "中文next"); assert.equal(state.cursor, 10);
  assert.equal(mergeTerminalOutput(state, 0, { output: "中文", outputCursor: 6, outputGeneration: "g1" }), state);
  state = mergeTerminalOutput(state, 10, { output: "wrong tail", outputCursor: 20, outputGeneration: "g2" });
  assert.equal(state.cursor, 0); assert.equal(state.output, ""); assert.equal(state.reset, true);
  state = mergeTerminalOutput(state, 0, { output: "new-generation", outputCursor: 14, outputGeneration: "g2" });
  assert.equal(state.output, "new-generation"); assert.equal(state.cursor, 14);
  state = mergeTerminalOutput(state, 14, { output: "reset tail", outputCursor: 2, outputGeneration: "g2", outputReset: true });
  assert.equal(state.output, ""); assert.equal(state.cursor, 0); assert.equal(state.reset, true);
  state = mergeTerminalOutput(state, 0, { output: "reset-start", outputCursor: 11, outputGeneration: "g2" });
  assert.equal(state.output, "reset-start");
});

test("actual terminal polling effect ignores late A 400 after switching to B even when abort is ineffective", async () => {
  const filename = path.join(root, "src/components/chat/InteractiveTerminalCard.tsx");
  const source = ts.createSourceFile(filename, fs.readFileSync(filename, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let callback;
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(source) === "useEffect" && node.arguments[0]?.getText(source).includes("const requestedCursor")) callback = node.arguments[0].getText(source);
    ts.forEachChild(node, visit);
  }
  visit(source); assert.ok(callback);
  const effects = []; let resolve;
  const targetRef = { current: "A" }, outputCursorRef = { current: { cursor: 5, generation: "A", output: "A", reset: false } };
  const context = vm.createContext({ pollingEnabled: true, process: { processId: "shared-process" }, outputPath: "/fixture", focused: true, appVisible: true, isCollapsed: false,
    targetKey: "A", targetRef, outputCursorRef, AbortController: class { signal = { aborted: false }; abort() {} },
    authorizedFetch: () => new Promise((done) => { resolve = done; }), setTimeout: () => { effects.push("scheduled"); }, clearTimeout() {},
    initialTerminalOutputCursor: () => { effects.push("reset"); return { cursor: 0 }; }, setPollingEnabled: () => effects.push("polling"), setIsRunning: () => effects.push("running"),
    setConnectionNote: () => effects.push("note"), t: (key) => key,
  });
  const code = ts.transpileModule(`this.callback = ${callback}`, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInContext(code, context);
  const cleanup = context.callback();
  cleanup(); targetRef.current = "B"; outputCursorRef.current = { cursor: 99, generation: "B", output: "B", reset: false };
  resolve({ status: 400, ok: false }); await tick();
  assert.equal(outputCursorRef.current.cursor, 99); assert.equal(outputCursorRef.current.output, "B"); assert.deepEqual(effects, []);
});

test("full resource identities map to distinct short native directory names and same-key calls share storage", async () => {
  const storage = memoryStorage();
  const { resourceCacheDirectory } = loader({ "@/src/lib/mobile-storage": { readMetadata: storage.read, writeMetadata: storage.write } })("@/src/lib/resource-cache-directory");
  const longIdentity = JSON.stringify(["authority".repeat(60), "serving", "session", "resource", "version"]);
  const [a, again, b] = await Promise.all([
    resourceCacheDirectory("file:///cache/", "artifact", longIdentity),
    resourceCacheDirectory("file:///cache/", "artifact", longIdentity),
    resourceCacheDirectory("file:///cache/", "artifact", longIdentity + "B"),
  ]);
  assert.equal(a, again); assert.notEqual(a, b); assert.ok(a.length < 100); assert.equal(storage.values.size, 2);
  assert.equal(await resourceCacheDirectory("file:///cache/", "artifact", longIdentity), a);
});
