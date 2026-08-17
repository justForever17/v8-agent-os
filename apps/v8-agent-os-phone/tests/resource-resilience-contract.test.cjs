const assert = require("node:assert/strict");
const fs = require("node:fs");
const Module = require("node:module");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");
const { evaluateSessionRuntimeEvent } = require("@v8/session-realtime");

const phoneRoot = path.resolve(__dirname, "..");

function read(relativePath) {
  return fs.readFileSync(path.join(phoneRoot, relativePath), "utf8");
}

function loadTsModule(relativePath) {
  const filename = path.join(phoneRoot, relativePath);
  const source = fs.readFileSync(filename, "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020,
      esModuleInterop: true,
    },
    fileName: filename,
  }).outputText;
  const loaded = new Module(filename, module);
  loaded.filename = filename;
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  loaded._compile(output, filename);
  return loaded.exports;
}

test("artifact list and selected detail remain independent failure domains", () => {
  const screen = read("src/screens/ArtifactsScreen.tsx");

  assert.match(screen, /Promise\.allSettled\(\[/);
  assert.match(screen, /listResult\.status === "fulfilled"/);
  assert.match(screen, /detailResult\.status === "fulfilled"/);
  assert.match(screen, /setListError\(/);
  assert.match(screen, /setDetailError\(/);
  assert.match(screen, /list\.unshift\(detailResult\.value\)/);
  assert.match(screen, /accessibilityRole="button" onPress=\{\(\) => void load\(\)\}/);
});

test("artifact routes fail closed unless artifact, conversation, and workspace scopes agree", () => {
  const screen = read("src/screens/ArtifactsScreen.tsx");

  assert.match(screen, /getSessionScope\(authorizedFetch, conversationId\)/);
  assert.match(screen, /if \(!expectedSessionId \|\| !expectedWorkspaceId\) return false/);
  assert.match(screen, /artifactWorkspaceId\(artifact\) === expectedWorkspaceId/);
  assert.match(screen, /rawList\.filter\(\(item\) => artifactBelongsToAuthority\(item, conversationId, expectedWorkspaceId\)\)/);
  assert.match(screen, /if \(artifactBelongsToAuthority\(detailResult\.value, conversationId, expectedWorkspaceId\)\)/);
  assert.match(screen, /setDetailError\(t\("src\.screens\.artifactsscreen\.artifact_binding_mismatch"\)\)/);
  assert.match(screen, /workspaceReadAllowed && workspacePath/);
  assert.match(screen, /sessionId=\{conversationId\}/);
  assert.match(screen, /getArtifact\(authorizedFetch, artifactId, conversationId\)/);
  assert.match(screen, /fetchArtifactContentResponse\(authorizedFetch, selectedArtifact\.id, conversationId\)/);

  const api = read("src/lib/phone-api.ts");
  assert.match(api, /\/api\/client\/artifacts\/\$\{encodeURIComponent\(id\)\}\?sessionId=\$\{encodeURIComponent\(sessionId\)\}/);
  assert.match(api, /\/content\?sessionId=\$\{encodeURIComponent\(sessionId\)\}/);
});

test("ask_user media stays bound to the active session authority", () => {
  const askUser = read("src/components/chat/AskUserModal.tsx");

  assert.match(askUser, /declaredSessionId && declaredSessionId !== expectedSessionId/);
  assert.match(askUser, /rawResourceRef && typeof rawResourceRef === "object" && !Array\.isArray\(rawResourceRef\)/);
  assert.match(askUser, /coerceAdminResourceRef\(rawResourceRef\)/);
  assert.match(askUser, /resourceRef\.kind === "external_url"/);
  assert.match(askUser, /new URLSearchParams\(\{ sessionId: resourceRef\.sessionId \}\)/);
  assert.match(askUser, /\/api\/client\/artifacts\/\$\{encodeURIComponent\(resourceRef\.artifactId\)\}\/content\?\$\{query\.toString\(\)\}/);
  assert.doesNotMatch(askUser, /const direct = asText\(item\.contentUrl\)/);
});

test("artifact Human Surface omits raw lineage, paths, and metadata", () => {
  const screen = read("src/screens/ArtifactsScreen.tsx");
  const listStart = screen.indexOf("artifacts.map((artifact) =>");
  const listTextStart = screen.indexOf("<View style={styles.itemBody}>", listStart);
  const listTextEnd = screen.indexOf("</View>", listTextStart);
  const detailStart = screen.indexOf("<View style={styles.detailList}>");
  const previewStart = screen.indexOf("{selectedArtifact ? (", detailStart);
  const listSurface = screen.slice(listTextStart, listTextEnd);
  const detailSurface = screen.slice(detailStart, previewStart);

  assert.ok(listStart > 0 && listTextStart > listStart && listTextEnd > listTextStart && detailStart > listTextEnd && previewStart > detailStart);
  assert.doesNotMatch(listSurface, /artifact\.(?:id|sessionId|runId|messageId|workspacePath|sourcePath|metadata)/);
  assert.match(listSurface, /artifactTypeLabel\(artifact, t\)/);
  assert.match(listSurface, /artifactOriginLabel\(artifact, t\)/);
  assert.doesNotMatch(detailSurface, /selectedArtifact\.(?:sessionId|runId|messageId|workspacePath|sourcePath|metadata)/);
  assert.doesNotMatch(detailSurface, /JSON\.stringify/);
  assert.match(detailSurface, /artifactOriginLabel\(selectedArtifact, t\)/);
  assert.match(detailSurface, /artifactTypeLabel\(selectedArtifact, t\)/);
});

test("session overview exposes independent source, artifact, and file retries", () => {
  const overview = read("src/components/chat/SessionOverviewPanel.tsx");

  assert.match(overview, /const \[artifactLoadError, setArtifactLoadError\]/);
  assert.match(overview, /const \[sourceLoadError, setSourceLoadError\]/);
  assert.match(overview, /setArtifactReloadToken\(\(value\) => value \+ 1\)/);
  assert.match(overview, /setSourceReloadToken\(\(value\) => value \+ 1\)/);
  assert.match(overview, /onRetry=\{\(\) => void loadPage\(startLine\)\}/);
  assert.match(overview, /<SourceItemRow key=\{item\.id\} item=\{item\} \/>/);
  assert.match(overview, /<NodeRenderBoundary[\s\S]*?<MessageBlockItem node=\{node\} \/>/);
  assert.match(overview, /buildSessionSourceProjection\(messages, sessionSources, \{ sessionId, workspaceId: scopedWorkspaceId \}\)/);
  assert.match(overview, /setSessionArtifacts\(\[\]\);[\s\S]*setSessionSources\(\[\]\);[\s\S]*\}, \[sessionId, scopedWorkspaceId\]\)/);
  assert.match(overview, /buildSessionOutputProjection\(messages, sessionArtifacts, \{[\s\S]*?sessionId,[\s\S]*?workspaceId: scopedWorkspaceId/);
  assert.match(overview, /key=\{`\$\{sessionId\}:\$\{scopedWorkspaceId\}:\$\{file\.source\}:\$\{file\.path\}`\}/);
  assert.match(overview, /const authorityKey = `\$\{sessionId\}\\u0000\$\{workspaceId\}\\u0000\$\{file\.path\}`/);
  assert.match(overview, /requestId !== loadRequestRef\.current/);
  assert.doesNotMatch(overview, /\.catch\(\(\) => \{\s*if \(!disposed\) setSession(?:Artifacts|Sources)\(\[\]\)/);
});

test("scope binding ignores stale responses after a conversation switch", () => {
  const screen = read("src/screens/ChatScreen.tsx");

  assert.match(screen, /scopeBindingState\?\.sessionId === activeConversationId/);
  assert.match(screen, /const requestSeq = \+\+scopeRequestSeqRef\.current/);
  assert.match(screen, /requestSeq !== scopeRequestSeqRef\.current \|\| activeConversationIdRef\.current !== conversationId/);
  assert.match(screen, /setScopeBindingState\(binding \? \{ sessionId: conversationId, binding \} : null\)/);
  assert.match(screen, /scopeRequestSeqRef\.current \+= 1;[\s\S]*setScopeBindingState\(null\)/);
});

test("Phone previews media and text artifacts without replacing the canonical renderer", () => {
  const screen = read("src/screens/ArtifactsScreen.tsx");
  const block = read("src/components/chat/MessageBlockItem.tsx");

  assert.match(screen, /probe\.includes\("application\/json"\)/);
  assert.match(screen, /probe\.includes\("text\/markdown"\)/);
  assert.match(screen, /readSessionWorkbenchFile\(authorizedFetch, sessionId, workspacePath, 1, 300\)/);
  assert.match(screen, /<MarkdownRenderer content=\{textContent\} \/>/);
  assert.match(screen, /<CodeBlock language="json" value=\{jsonContent\} \/>/);
  assert.match(screen, /<MessageBlockItem node=\{node\} \/>/);

  assert.match(block, /if \(mediaKind === "image"\)/);
  assert.match(block, /mediaKind === "video" \|\| mediaKind === "audio"/);
  assert.match(block, /return "model" as const/);
});

test("Phone document resources never enter third-party viewers without authoritative public provenance", () => {
  const block = read("src/components/chat/MessageBlockItem.tsx");
  const pdf = read("src/components/chat/PDFFileCard.tsx");
  const ppt = read("src/components/chat/PPTCard.tsx");
  const model = read("src/components/chat/ModelViewer.tsx");

  assert.doesNotMatch(block, /PDFFileCard|PPTCard|thirdPartyPreviewAllowed|isPublicThirdPartyPreviewUrl/);
  assert.match(block, /viewerKind === "pdf"[\s\S]*?<DownloadFileCard[\s\S]*?application\/pdf/);
  assert.match(block, /viewerKind === "ppt"[\s\S]*?<DownloadFileCard[\s\S]*?presentation/);
  assert.match(pdf, /https:\/\/docs\.google\.com\/gview/);
  assert.match(ppt, /https:\/\/view\.xdocin\.com\/view/);
  assert.match(model, /source=\{\{ html \}\}/);
  assert.doesNotMatch(model, /source=\{\{ uri: src/);
});

test("text artifact previews enforce byte and character limits without unbounded fallback reads", async () => {
  const {
    BoundedResponseTextError,
    readBoundedResponseText,
  } = loadTsModule("src/lib/bounded-response-text.ts");

  assert.deepEqual(
    await readBoundedResponseText(new Response("small"), { maxBytes: 32, maxChars: 32 }),
    { text: "small", truncated: false },
  );
  assert.deepEqual(
    await readBoundedResponseText(new Response("abcdef"), { maxBytes: 32, maxChars: 3 }),
    { text: "abc", truncated: true },
  );
  await assert.rejects(
    readBoundedResponseText(new Response("abcdef"), { maxBytes: 3, maxChars: 32 }),
    (error) => error instanceof BoundedResponseTextError && error.code === "too_large",
  );

  let consumedUnboundedBody = false;
  const nonStreamingResponse = {
    headers: new Headers({ "Content-Length": "1" }),
    body: null,
    text: async () => {
      consumedUnboundedBody = true;
      return "x".repeat(1_000_000);
    },
  };
  await assert.rejects(
    readBoundedResponseText(nonStreamingResponse, { maxBytes: 32, maxChars: 32 }),
    (error) => error instanceof BoundedResponseTextError && error.code === "stream_unavailable",
  );
  assert.equal(consumedUnboundedBody, false);
});

test("artifact open caches authenticated content before any external fallback", () => {
  const screen = read("src/screens/ArtifactsScreen.tsx");
  const openStart = screen.indexOf("const openSelectedArtifact");
  const openEnd = screen.indexOf("if (status === \"booting\")", openStart);
  const openFlow = screen.slice(openStart, openEnd);

  assert.ok(openStart > 0 && openEnd > openStart);
  assert.ok(openFlow.indexOf("fetchArtifactContentResponse") < openFlow.indexOf("Linking.openURL"));
  assert.doesNotMatch(openFlow, /fetchWorkspaceFileResponse/);
  assert.match(openFlow, /resourceRef\?\.kind === "external_url"/);
});

test("resource recovery labels remain bilingual", () => {
  const zh = JSON.parse(read("src/i18n/locales/zh-CN.json"));
  const en = JSON.parse(read("src/i18n/locales/en.json"));

  assert.equal(zh["src.screens.artifactsscreen.retry"], "重试");
  assert.equal(en["src.screens.artifactsscreen.retry"], "Retry");
  assert.equal(zh["src.components.chat.sessionoverviewpanel.retry"], "重试");
  assert.equal(en["src.components.chat.sessionoverviewpanel.retry"], "Retry");
  assert.match(zh["src.screens.artifactsscreen.text_preview_stream_unavailable"], /安全/);
  assert.match(en["src.screens.artifactsscreen.text_preview_stream_unavailable"], /safely stream/);
});

test("Phone prunes only realtime identities covered by an advancing snapshot", () => {
  const screen = read("src/screens/ChatScreen.tsx");

  assert.match(
    screen,
    /const snapshotWatermarkAdvanced = snapshotSeq > lastAppliedSnapshotSeqRef\.current;[\s\S]*?if \(snapshotWatermarkAdvanced\) \{\s*seenRealtimeEventKeysRef\.current\.pruneSnapshotCovered\(snapshotSeq\);\s*\}/,
  );
});

test("Phone keeps a gap event identity beyond a partial snapshot watermark", () => {
  const { BoundedRuntimeEventIdentityLedger } = loadTsModule("src/lib/runtime-event-identity-ledger.ts");
  const ledger = new BoundedRuntimeEventIdentityLedger(8);
  const liveEvent = {
    type: "custom_event",
    topic: "subagent.tool.finished",
    seq: 15,
    event_id: "evt-phone-15",
  };
  let sideEffects = 0;
  const first = evaluateSessionRuntimeEvent(liveEvent, {
    snapshotCoveredSeq: 10,
    contiguousSeq: 10,
    seenEventIdentities: ledger.seenIdentities,
  });
  assert.equal(first.accept, true);
  assert.deepEqual(first.gap, {
    expectedSeq: 11,
    observedSeq: 15,
    missingFromSeq: 11,
    missingToSeq: 14,
  });
  if (first.accept) {
    ledger.remember(first.identity, liveEvent.seq);
    sideEffects += 1;
  }

  ledger.pruneSnapshotCovered(12);
  const duplicate = evaluateSessionRuntimeEvent(liveEvent, {
    snapshotCoveredSeq: 12,
    seenEventIdentities: ledger.seenIdentities,
  });
  if (duplicate.accept) sideEffects += 1;

  assert.equal(duplicate.reason, "duplicate");
  assert.equal(sideEffects, 1);
  assert.equal(ledger.has(first.identity), true);
});

test("Phone realtime identity retention stays bounded without guessing legacy snapshot coverage", () => {
  const { BoundedRuntimeEventIdentityLedger } = loadTsModule("src/lib/runtime-event-identity-ledger.ts");
  const ledger = new BoundedRuntimeEventIdentityLedger(2);
  ledger.remember("legacy-without-sequence", 0);
  ledger.pruneSnapshotCovered(99);
  assert.equal(ledger.has("legacy-without-sequence"), true);

  ledger.remember("event:100", 100);
  ledger.remember("event:101", 101);
  assert.equal(ledger.size, 2);
  assert.equal(ledger.has("legacy-without-sequence"), false);
  assert.equal(ledger.has("event:100"), true);
  assert.equal(ledger.has("event:101"), true);
});
