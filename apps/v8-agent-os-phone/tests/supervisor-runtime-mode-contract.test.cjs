const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const phoneRoot = path.resolve(__dirname, "..");

function read(relativePath) {
  return fs.readFileSync(path.join(phoneRoot, relativePath), "utf8");
}

test("Phone composer exposes every supported Supervisor runtime mode", () => {
  const composer = read("src/components/chat/Composer.tsx");
  for (const mode of ["auto", "engineering", "research", "creative_media", "computer_use", "rpa"]) {
    assert.match(composer, new RegExp(`mode: ["']${mode}["']`));
  }
  assert.doesNotMatch(composer, /supervisorWorkMode|onChangeSupervisorWorkMode/);
});

test("Phone snapshots the selected mode for queued and immediate submissions", () => {
  const chatScreen = read("src/screens/ChatScreen.tsx");
  assert.match(chatScreen, /supervisorRuntimeModeRef\.current = nextMode/);
  assert.match(chatScreen, /const pendingSupervisorRuntimeMode = supervisorRuntimeModeRef\.current/);
  assert.equal(
    (chatScreen.match(/supervisorRuntimeMode: pendingSupervisorRuntimeMode/g) || []).length,
    2,
  );
  assert.match(chatScreen, /updateConversationPresentation[\s\S]*?supervisorRuntimeMode: nextMode/);
  assert.match(chatScreen, /supervisorRuntimeModeRef\.current = rollbackMode/);
  assert.match(chatScreen, /shared\.conversation\.runtime_mode_sync_failed/);
});

test("Phone serializes mode persistence per session and isolates stale failures", () => {
  const chatScreen = read("src/screens/ChatScreen.tsx");
  assert.match(chatScreen, /useLayoutEffect\(\(\) => \{\s*activeConversationIdRef\.current = activeConversationId/);
  assert.match(chatScreen, /supervisorRuntimeModeRequestSeqRef = useRef<Record<string, number>>/);
  assert.match(chatScreen, /supervisorRuntimeModePersistChainRef = useRef<Record<string, Promise<void>>>/);
  assert.match(chatScreen, /const requestSeq = \(supervisorRuntimeModeRequestSeqRef\.current\[sessionId\] \|\| 0\) \+ 1/);
  assert.match(chatScreen, /const previousWrite = supervisorRuntimeModePersistChainRef\.current\[sessionId\] \|\| Promise\.resolve\(\)/);
  assert.match(chatScreen, /const persistPromise = previousWrite\.catch\(\(\) => undefined\)\.then/);
  assert.match(chatScreen, /supervisorRuntimeModeRequestSeqRef\.current\[sessionId\] !== requestSeq/);
  assert.match(chatScreen, /const refreshed = await listConversations\(authorizedFetch\)/);
  assert.match(chatScreen, /if \(activeConversationIdRef\.current !== sessionId\) return/);
  assert.match(chatScreen, /supervisorRuntimeModePersistChainRef\.current\[sessionId\] === persistPromise/);
});

test("Phone API forwards the selected mode through submit and stream payloads", () => {
  const phoneApi = read("src/lib/phone-api.ts");
  assert.equal(
    (phoneApi.match(/supervisorRuntimeMode: options\.supervisorRuntimeMode \|\| undefined/g) || []).length,
    2,
  );
  assert.match(phoneApi, /supervisorRuntimeMode\?: SupervisorRuntimeMode/);
});

test("Phone runtime mode labels stay bilingual and keep RPA explicit", () => {
  const zh = JSON.parse(read("src/i18n/locales/zh-CN.json"));
  const en = JSON.parse(read("src/i18n/locales/en.json"));
  assert.equal(zh["src.components.chat.composer.runtime_mode_auto_title"], "智能模式");
  assert.equal(zh["src.components.chat.composer.runtime_mode_rpa_title"], "RPA模式");
  assert.equal(en["src.components.chat.composer.runtime_mode_auto_title"], "Smart mode");
  assert.equal(en["src.components.chat.composer.runtime_mode_rpa_title"], "RPA mode");
});
