const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");

function readText(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

test("Spec confirmation uses a document review surface instead of generic approval details", () => {
  const governance = readText("apps/v8-agent-os-web/src/components/chat/GovernanceApprovalModal.tsx");
  const documentDialog = readText("apps/v8-agent-os-web/src/components/chat/SpecDocumentConfirmationDialog.tsx");
  assert.match(governance, /SpecDocumentConfirmationDialog/);
  assert.match(documentDialog, /rewrite_stage/);
  assert.match(documentDialog, /同意并继续/);
  assert.match(documentDialog, /需要修改/);
  assert.doesNotMatch(documentDialog, />specId</);
  assert.doesNotMatch(documentDialog, />workspace</);
  const dialog = readText("apps/v8-agent-os-web/src/components/ui/dialog.tsx");
  assert.match(dialog, /z-\[110\]/);
  assert.doesNotMatch(dialog, /z-50/);
});

test("ask_user reserves a stable composer surface", () => {
  const askUser = readText("apps/v8-agent-os-web/src/components/chat/AskUserModal.tsx");
  assert.match(askUser, /h-\[clamp\(220px,34dvh,320px\)\]/);
  assert.match(askUser, /min-h-0 flex-1 overflow-y-auto/);
});

test("Workbench has one compact tab row and browser prewarm", () => {
  const shell = readText("apps/v8-agent-os-web/src/components/workbench/WorkbenchShell.tsx");
  assert.match(shell, /browser\/prepare/);
  assert.match(shell, /浏览器就绪后会自动进入可控制页面/);
  assert.doesNotMatch(shell, /PanelRight/);
  assert.doesNotMatch(shell, /aria-label="关闭工作台"/);
});

test("Workbench browser fallback follows the panel and preserves real pointer semantics", () => {
  const renderer = readText("apps/v8-agent-os-web/src/components/workbench/BrowserRenderer.tsx");
  const service = readText("apps/v8-agent-os-engine/runtimes/computer_use/browser_session_service.py");
  const proxy = readText("apps/v8-agent-os-engine/scripts/browser_cdp_proxy.mjs");
  assert.match(renderer, /sendCommand\("set_viewport"/);
  assert.match(renderer, /new ResizeObserver\(syncViewport\)/);
  assert.match(renderer, /frameMetadata\.deviceWidth/);
  assert.match(renderer, /frameMetadata\.pageScaleFactor/);
  assert.match(renderer, /aria-label="浏览器右键菜单"/);
  assert.match(renderer, /onContextMenu=\{openContextMenu\}/);
  assert.match(service, /action == "set_viewport"/);
  assert.match(proxy, /Emulation\.setDeviceMetricsOverride/);
  assert.match(proxy, /portraitPrimary/);
  assert.match(proxy, /V8_ENGINE_PARENT_PID/);
});

test("Web and Phone summaries hide engineering counters and raw payload bodies", () => {
  const webSummary = readText("apps/v8-agent-os-web/src/components/chat/WorkspaceWorkbenchPanel.tsx");
  const phoneDock = readText("apps/v8-agent-os-phone/src/components/chat/RuntimeDock.tsx");
  const phoneTimeline = readText("apps/v8-agent-os-phone/src/components/chat/RuntimeTimelinePanel.tsx");
  assert.doesNotMatch(webSummary, /本会话已参与/);
  assert.doesNotMatch(webSummary, /commandPreview/);
  assert.doesNotMatch(phoneDock, /eventCount > 0/);
  assert.doesNotMatch(phoneTimeline, /<ContentDispatcher node=\{activity\.node\}/);
});
