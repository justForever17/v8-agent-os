const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const source = fs.readFileSync(
    path.join(__dirname, "../src/app/chat/ChatClient.tsx"),
    "utf8",
);
const queueComponent = fs.readFileSync(
    path.join(__dirname, "../src/components/chat/QueuedMessagesDock.tsx"),
    "utf8",
);

test("queued message actions use a portal menu above clipped composer surfaces", () => {
    assert.match(queueComponent, /function QueuedMessagesStrip/);
    assert.match(queueComponent, /<DropdownMenu\b/);
    assert.match(queueComponent, /<DropdownMenuContent[\s\S]*side="top"/);
    assert.match(queueComponent, /collisionPadding=\{12\}/);
    assert.doesNotMatch(queueComponent, /absolute bottom-full/);
});

test("ask-user and queued messages float above the composer without shrinking chat history", () => {
    assert.match(source, /data-testid="chat-transient-dock"/);
    assert.match(source, /pointer-events-none absolute inset-x-0 bottom-full/);
    assert.match(source, /max-h-\[min\(62vh,560px\)\]/);
});
