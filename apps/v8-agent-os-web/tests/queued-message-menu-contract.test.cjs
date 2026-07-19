const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const source = fs.readFileSync(
    path.join(__dirname, "../src/app/chat/ChatClient.tsx"),
    "utf8",
);

test("queued message actions use a portal menu above clipped composer surfaces", () => {
    const start = source.indexOf("function QueuedMessagesStrip");
    const end = source.indexOf("function QueuedMessageEditDialog");
    assert.ok(start >= 0 && end > start, "queued message component should exist");

    const component = source.slice(start, end);
    assert.match(component, /<DropdownMenu\b/);
    assert.match(component, /<DropdownMenuContent[\s\S]*side="top"/);
    assert.match(component, /collisionPadding=\{12\}/);
    assert.doesNotMatch(component, /absolute bottom-full/);
});

test("ask-user and queued messages float above the composer without shrinking chat history", () => {
    assert.match(source, /data-testid="chat-transient-dock"/);
    assert.match(source, /pointer-events-none absolute inset-x-0 bottom-full/);
    assert.match(source, /max-h-\[min\(62vh,560px\)\]/);
});
