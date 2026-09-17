const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");
const vm = require("node:vm");
const web = path.resolve(__dirname, "..");
const phone = path.resolve(web, "../v8-agent-os-phone");
function load(filename, shared) {
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded.require = (name) => {
    if (name === "@v8/session-realtime") return shared;
    if (name === "@/src/lib/locale") return { translateCurrent: () => "Supervisor" };
    if (name.startsWith("@/")) return load(path.join(web, "src", name.slice(2) + ".ts"), shared);
    return require(name);
  };
  loaded._compile(ts.transpileModule(fs.readFileSync(filename, "utf8"), { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, esModuleInterop: true,
  } }).outputText, filename);
  return loaded.exports;
}
test("both clients replace revised narrative nodes and reject late lower message versions", async () => {
  const shared = await import("@v8/session-realtime");
  for (const filename of [path.join(web, "src/lib/chat-stream-state.ts"), path.join(phone, "src/lib/chat-state.ts")]) {
    const { normalizeMessagesForState } = load(filename, shared);
    const proof = { id: "proof", kind: "execution", executionType: "tool_result", toolCallId: "call", result: { written: true }, timestamp: 1 };
    const before = { id: "m", role: "assistant", version: 7, content: "old", timestamp: 1,
      nodes: [{ id: "old", kind: "narrative", role: "assistant", content: "old", timestamp: 1 }, proof] };
    const revised = { ...before, version: 8, editedBy: "user", content: "new",
      nodes: [{ id: "revision", kind: "narrative", role: "assistant", content: "new", timestamp: 1 }, proof] };
    const [visible] = normalizeMessagesForState([before, revised]);
    assert.deepEqual(visible.nodes.map(node => node.id), ["revision", "proof"], filename);
    assert.deepEqual(visible.nodes[1], proof);
    assert.equal(visible.content, "new");
    assert.equal(visible.nodes[0].editedBy, "user");
    const [late] = normalizeMessagesForState([revised, before]);
    assert.equal(late.content, "new");
    assert.equal(late.version, 8);
    assert.equal(late.editedBy, "user");
  }
});

test("user revisions keep literal protocol tags as text on both clients", async () => {
  const shared = await import("@v8/session-realtime");
  const content = '示例 <think>visible words</think>\n<tool-call id="fake" /><artifact>plain prose</artifact><voice>not audio</voice>';
  const webParser = load(path.join(web, "src/lib/chat/content-detector.ts"), shared).parseContentToBlocks;
  const phoneParser = load(path.join(phone, "src/lib/content-detector.ts"), shared).parsePhoneContentBlocks;
  for (const parse of [webParser, phoneParser]) {
    const blocks = parse(content, false, 0, false, true);
    assert.deepEqual(blocks.map(block => block.type), ["text"]);
    assert.equal(blocks[0].content, content);
    assert.notEqual(parse(content, false, 0, false).map(block => block.content).join(""), content);
  }
});
test("user revision cannot be masked by stale composer presentation text", () => {
  for (const filename of [path.join(web, "src/components/chat/ChatMessage.tsx"), path.join(phone, "src/components/chat/MessageBubble.tsx")]) {
    const source = ts.createSourceFile(filename, fs.readFileSync(filename, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    let found;
    function visit(node) {
      if (ts.isFunctionDeclaration(node) && node.name?.text === "extractComposerPresentation") found = node.getText(source);
      ts.forEachChild(node, visit);
    }
    visit(source);
    assert.ok(found);
    const sandbox = {};
    vm.runInNewContext(ts.transpileModule("globalThis.extract = " + found, { compilerOptions: { target: ts.ScriptTarget.ES2022 } }).outputText, sandbox);
    const message = { content: "corrected text", metadata: { composerPresentation: { text: "obsolete text", references: [{ kind: "skill", id: "s", label: "skill" }] } } };
    assert.equal(sandbox.extract(message).text, "obsolete text");
    assert.equal(sandbox.extract({ ...message, editedBy: "user" }), null);
  }
});

function callbackSource(filename, name) {
  const source = ts.createSourceFile(filename, fs.readFileSync(filename, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let found;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === name) found = node.initializer.arguments[0].getText(source);
    ts.forEachChild(node, visit);
  }
  visit(source);
  assert.ok(found, name);
  return ts.transpileModule("globalThis.callback = " + found, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
}
test("actual Web/Phone event handlers deduplicate mutation refresh before the first reload settles", async () => {
  const shared = await import("@v8/session-realtime");
  for (const [filename, name, isPhone] of [
    [path.join(web, "src/app/chat/ChatClient.tsx"), "applyRemoteRuntimeEvent", false],
    [path.join(phone, "src/screens/ChatScreen.tsx"), "handleRealtimeEvent", true],
  ]) {
    let refreshes = 0;
    const ledger = { seenIdentities: new Set(), remember(identity) { this.seenIdentities.add(identity); } };
    const refresh = async () => { refreshes++; };
    const sandbox = {
      conversationEventDisposition: shared.conversationEventDisposition,
      evaluateSessionRuntimeEvent: shared.evaluateSessionRuntimeEvent,
      normalizeRealtimeEvent: shared.normalizeSessionRuntimeEvent,
      normalizePhoneRealtimeEvent: shared.normalizeSessionRuntimeEvent,
      transcriptIdentitiesRef: { current: new Map([["s", { transcriptRevision: 9, contextEpoch: 2 }]]) },
      activeConversationIdRef: { current: "s" },
      seenRealtimeEventIdentitiesRef: { current: ledger }, seenRealtimeEventKeysRef: { current: ledger },
      snapshotCoveredRealtimeSeqRef: { current: 0 }, lastAppliedSnapshotSeqRef: { current: 0 },
      latestRealtimeSeqRef: { current: 0 }, latestSeqRef: { current: 0 },
      loadConversationHistory: refresh, loadConversationRef: { current: refresh },
      readRealtimeDiagnostics: () => ({}), getPerfNowMs: () => 0, locale: "en",
    };
    vm.runInNewContext(callbackSource(filename, name), sandbox);
    const event = { topic: "session.branch.created", event_id: "duplicate-branch", session_id: "s", seq: 10, payload: { contextEpoch: 2, transcriptRevision: 9 } };
    const dispatch = () => isPhone ? sandbox.callback("runtime", event) : sandbox.callback(event);
    dispatch(); dispatch();
    assert.equal(refreshes, 1, filename);
    assert.equal(ledger.seenIdentities.size, 1);
    // Fault mutant removes the identity write; the same observable oracle catches it.
    refreshes = 0; ledger.seenIdentities.clear();
    const mutant = callbackSource(filename, name).replace(/seenRealtimeEvent(?:Identities|Keys)Ref.current.remember\(acceptance.identity, (?:eventSeq|normalized.seq)\);/, "");
    vm.runInNewContext(mutant, sandbox);
    dispatch(); dispatch();
    assert.equal(refreshes, 2, "without deduplication the old duplicate-refresh defect reappears");
  }
});
