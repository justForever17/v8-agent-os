const assert = require("node:assert/strict");
const os = require("node:os");
const path = require("node:path");
const { execFileSync } = require("node:child_process");
const test = require("node:test");

const phoneRoot = path.resolve(__dirname, "..");
const validatorPath = path.join(phoneRoot, "scripts", "validate-i18n.mjs");

test("Phone i18n validation resolves its source tree independently of the caller cwd", () => {
  const output = execFileSync(process.execPath, [validatorPath], {
    cwd: os.tmpdir(),
    encoding: "utf8",
  });

  assert.match(output, /phone i18n validation passed/);
});
