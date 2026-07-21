const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.resolve(__dirname, "..");
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), "utf8");

test("web mounts the semantic desktop context menu without replacing custom menus", () => {
  const layout = read("src/app/layout.tsx");
  const menu = read("src/components/ui/AppContextMenu.tsx");
  assert.match(layout, /<AppContextMenu\s*\/>/);
  assert.match(menu, /event\.defaultPrevented/);
  assert.match(menu, /contextmenu/);
  assert.match(menu, /web\.contextMenu\.cut/);
  assert.match(menu, /web\.contextMenu\.copy/);
  assert.match(menu, /web\.contextMenu\.paste/);
  assert.match(menu, /web\.contextMenu\.selectAll/);
  assert.match(menu, /web\.contextMenu\.copyLink/);
});

test("web workbench action is opt-in and limited to supported resource surfaces", () => {
  const menu = read("src/components/ui/AppContextMenu.tsx");
  const artifact = read("src/components/chat/ArtifactCard.tsx");
  const markdown = read("src/components/chat/MarkdownRenderer.tsx");
  const subagents = read("src/components/chat/WorkspaceWorkbenchPanel.tsx");
  assert.match(menu, /data-v8-context-open-workbench/);
  assert.match(menu, /web\.contextMenu\.openInWorkbench/);
  assert.match(artifact, /data-v8-context-resource/);
  assert.match(artifact, /data-v8-context-open-workbench/);
  assert.match(markdown, /data-v8-context-open-workbench/);
  assert.match(subagents, /data-v8-context-open-workbench/);
});

test("web context menu copy remains useful when Clipboard API is unavailable", () => {
  const menu = read("src/components/ui/AppContextMenu.tsx");
  assert.match(menu, /document\.execCommand\("copy"\)/);
  assert.match(menu, /setRangeText/);
  assert.match(menu, /new InputEvent\("input"/);
});
