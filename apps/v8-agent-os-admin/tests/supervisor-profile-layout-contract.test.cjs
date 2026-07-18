const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const source = fs.readFileSync(
    path.join(__dirname, "../src/app/admin/(dashboard)/supervisor/page.tsx"),
    "utf8",
);

test("supervisor identity fields and avatar controls use two orderly layout bands", () => {
    assert.match(source, /grid grid-cols-1 gap-4 sm:grid-cols-2/);
    assert.match(source, /grid grid-cols-\[6rem_minmax\(0,1fr\)\]/);
    assert.match(source, /htmlFor="supervisor-avatar-url"/);
    assert.match(source, /id="supervisor-avatar-url"/);
    assert.doesNotMatch(source, /grid grid-cols-1 md:grid-cols-2 gap-6 pb-6 border-b/);
});
