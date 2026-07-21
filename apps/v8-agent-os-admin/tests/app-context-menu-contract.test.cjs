const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.resolve(__dirname, "..");
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), "utf8");

test("admin mounts the semantic desktop context menu", () => {
  const layout = read("src/app/layout.tsx");
  const menu = read("src/components/ui/AppContextMenu.tsx");
  assert.match(layout, /<AppContextMenu\s*\/>/);
  assert.match(menu, /event\.defaultPrevented/);
  assert.match(menu, /admin\.contextMenu\.cut/);
  assert.match(menu, /admin\.contextMenu\.copy/);
  assert.match(menu, /admin\.contextMenu\.paste/);
  assert.match(menu, /admin\.contextMenu\.selectAll/);
  assert.match(menu, /admin\.contextMenu\.copyLink/);
});

test("admin does not advertise the Web-only workbench action", () => {
  const menu = read("src/components/ui/AppContextMenu.tsx");
  assert.doesNotMatch(menu, /openInWorkbench|data-v8-context-open-workbench/);
});

test("admin input edits dispatch an input event for controlled React fields", () => {
  const menu = read("src/components/ui/AppContextMenu.tsx");
  assert.match(menu, /setRangeText/);
  assert.match(menu, /new InputEvent\("input"/);
});
