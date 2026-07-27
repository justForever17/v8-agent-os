const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const root = path.resolve(__dirname, "..");

test("web desktop sidebar uses an unframed icon collapse control", () => {
  const source = fs.readFileSync(path.join(root, "src/components/layout/Sidebar.tsx"), "utf8");

  assert.match(source, /<button[\s\S]*aria-label=\{isCollapsed/);
  assert.match(source, /hover:text-foreground hover:opacity-100/);
  assert.doesNotMatch(source, /h-7 w-7 rounded-full border-border bg-background shadow-md/);
});
