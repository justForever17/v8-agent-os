/* eslint-disable @typescript-eslint/no-require-imports */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const repoRoot = path.resolve(__dirname, "../../..");
const card = fs.readFileSync(path.join(repoRoot, "apps/v8-agent-os-web/src/components/chat/ApprovalCard.tsx"), "utf8");

test("generic governance cards keep the action summary compact and expose the full issue on hover", () => {
  assert.match(card, /data-approval-card=\"compact\"/);
  assert.match(card, /title=\{fullMessage \|\| title\}/);
  assert.match(card, /className=\"mt-0\.5 truncate text-xs/);
  assert.match(card, /title=\{fullMessage \|\| undefined\}/);
  assert.match(card, /className=\"mt-1\.5 flex min-w-0 flex-wrap gap-1\"/);
  assert.doesNotMatch(card, /showHint \? <div/);
});

test("full problem text is reduced to a readable first-line summary before truncation", () => {
  assert.match(card, /function shortMessage\(value: string\)/);
  assert.match(card, /value\.split\(\/\\r\?\\n\/, 1\)/);
});
