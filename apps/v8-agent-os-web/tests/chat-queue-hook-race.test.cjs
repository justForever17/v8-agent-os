const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");
const test = require("node:test");

// Only React scheduling and HTTP are controlled. Every queue operation and its
// normalization/reconciliation dependency executes from the production module.
function createHarness(transform = source => source) {
    const slots = [], effects = [], pending = [], cache = new Map();
    let cursor = 0, dirty = false;
    const equalDeps = (left, right) => left && right
        && left.length === right.length && left.every((value, index) => Object.is(value, right[index]));
    const react = {
        useState(initial) {
            const index = cursor++;
            if (!slots[index]) {
                slots[index] = { value: typeof initial === "function" ? initial() : initial };
                slots[index].set = next => {
                    const value = typeof next === "function" ? next(slots[index].value) : next;
                    if (!Object.is(value, slots[index].value)) dirty = true;
                    slots[index].value = value;
                };
            }
            return [slots[index].value, slots[index].set];
        },
        useRef(initial) { return slots[cursor++] ??= { current: initial }; },
        useMemo(fn, deps) {
            const index = cursor++;
            if (!equalDeps(slots[index]?.deps, deps)) slots[index] = { deps, value: fn() };
            return slots[index].value;
        },
        useCallback(fn, deps) { return react.useMemo(() => fn, deps); },
        useEffect(fn, deps) {
            const index = cursor++, old = slots[index];
            if (!equalDeps(old?.deps, deps)) {
                slots[index] = { deps };
                effects.push(() => { old?.cleanup?.(); slots[index].cleanup = fn(); });
            }
        },
    };
    function load(file) {
        if (cache.has(file)) return cache.get(file);
        const exports = {};
        cache.set(file, exports);
        const source = fs.readFileSync(file, "utf8");
        const code = ts.transpileModule(file.endsWith("use-chat-queue.ts") ? transform(source) : source, {
            fileName: file,
            compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
        }).outputText;
        const requireLocal = name => {
            if (name === "react") return react;
            if (name.startsWith("@/")) return load(path.join(__dirname, "../src", `${name.slice(2)}.ts`));
            return require(name);
        };
        const fetch = (url, options) => new Promise((resolve, reject) => pending.push({ url, options, resolve, reject }));
        new Function("require", "exports", "fetch", code)(requireLocal, exports, fetch);
        return exports;
    }
    const { useChatQueue } = load(path.join(__dirname, "../src/hooks/use-chat-queue.ts"));
    const options = {
        activeConversationId: "A", activeConversationIdRef: { current: "A" },
        latestRealtimeSeqRef: { current: 0 }, ownerKey: "owner-A", translate: value => value,
    };
    function render() {
        for (let attempt = 0; attempt < 20; attempt += 1) {
            cursor = 0; dirty = false;
            const value = useChatQueue(options);
            effects.splice(0).forEach(fn => fn());
            if (!dirty) return value;
        }
        throw new Error("hook did not settle");
    }
    function switchTo(sessionId, ownerKey = options.ownerKey) {
        options.activeConversationId = options.activeConversationIdRef.current = sessionId;
        options.ownerKey = ownerKey;
        options.latestRealtimeSeqRef.current = 0;
        return render();
    }
    function edit(item, text) {
        render().commands.openEditor(item);
        render().commands.setEditText(text);
        return render();
    }
    return { options, pending, render, switchTo, edit, unmount: () => slots.forEach(slot => slot?.cleanup?.()) };
}

const item = (id, sessionId = "A", content = id) => ({ id, sessionId, content, state: "pending" });
const ok = queuedMessage => ({ ok: true, json: async () => ({ ok: true, queuedMessage }) });
const fail = error => ({ ok: false, json: async () => ({ error }) });

test("successful edits release their request so the next message can be edited", async () => {
    const h = createHarness();
    for (const id of ["q-A", "q-C"]) {
        const saving = h.edit(item(id), `edited ${id}`).commands.saveEdit();
        const request = h.pending.at(-1);
        assert.equal(request.options.method, "PATCH");
        assert.equal(JSON.parse(request.options.body).content, `edited ${id}`);
        request.resolve(ok(item(id, "A", `edited ${id}`)));
        await saving;
        assert.equal(h.render().view.editBusy, false);
        assert.equal(h.render().view.editingItem, null);
    }
    assert.equal(h.pending.length, 2);
});

test("a late success cannot close another session's editor or clear its pending request", async () => {
    const h = createHarness();
    const first = h.edit(item("q-A"), "save A").commands.saveEdit();
    h.switchTo("B");
    assert.equal(h.pending[0].options.signal.aborted, true);
    const second = h.edit(item("q-B", "B"), "unsaved B").commands.saveEdit();
    h.pending[0].resolve(ok(item("q-A")));
    await first;
    const view = h.render().view;
    assert.equal(view.editingItem.id, "q-B");
    assert.equal(view.editText, "unsaved B");
    assert.equal(view.editBusy, true);
    h.pending[1].resolve(ok(item("q-B", "B", "unsaved B")));
    await second;
    assert.equal(h.render().view.editBusy, false);
});

test("changing principal on the same session rejects a late error and clears cached records", async () => {
    const h = createHarness();
    h.render().commands.upsert(item("old-owner"));
    const first = h.edit(item("q-A"), "A").commands.saveEdit();
    h.switchTo("A", "owner-B");
    h.edit(item("q-B"), "B draft");
    h.pending[0].resolve(fail("old owner failed"));
    await first;
    assert.equal(h.render().view.error, "");
    assert.equal(h.render().view.editText, "B draft");
    assert.deepEqual(h.render().view.visibleMessages, []);
});

test("same-batch duplicate edits share one request; failure keeps draft and retry succeeds", async () => {
    const h = createHarness();
    const ui = h.edit(item("q-A"), "retry me");
    const first = ui.commands.saveEdit(), duplicate = ui.commands.saveEdit();
    assert.equal(h.pending.length, 1);
    h.pending[0].resolve(fail("access denied"));
    await Promise.all([first, duplicate]);
    assert.equal(h.render().view.editText, "retry me");
    assert.equal(h.render().view.editBusy, false);
    assert.equal(h.render().view.error, "access denied");
    const retry = h.render().commands.saveEdit();
    assert.equal(h.pending.length, 2);
    h.pending[1].resolve(ok(item("q-A", "A", "retry me")));
    await retry;
    assert.equal(h.render().view.editingItem, null);
});

test("editing during save preserves the newer draft while settling the earlier receipt", async () => {
    const h = createHarness();
    const first = h.edit(item("q-A"), "first").commands.saveEdit();
    h.render().commands.setEditText("second");
    h.pending[0].resolve(ok(item("q-A", "A", "first")));
    await first;
    assert.equal(h.render().view.editText, "second");
    assert.equal(h.render().view.editBusy, false);
    assert.equal(h.render().view.visibleMessages[0].content, "first");
    const second = h.render().commands.saveEdit();
    assert.equal(JSON.parse(h.pending[1].options.body).content, "second");
    h.pending[1].resolve(ok(item("q-A", "A", "second")));
    await second;
});

test("closing and reopening even the same item invalidates the old receipt", async () => {
    const h = createHarness();
    const first = h.edit(item("q-A"), "same").commands.saveEdit();
    h.render().commands.closeEditor();
    const second = h.edit(item("q-A"), "same").commands.saveEdit();
    h.pending[0].resolve(ok(item("q-A", "A", "same")));
    await first;
    assert.equal(h.render().view.editingItem.id, "q-A");
    assert.equal(h.render().view.editBusy, true);
    h.pending[1].resolve(ok(item("q-A", "A", "same")));
    await second;
    assert.equal(h.render().view.editingItem, null);
});

for (const kind of ["promote", "cancel"]) {
    test(`${kind} rejects duplicate submission and ignores late failures across sessions`, async () => {
        const h = createHarness();
        const ui = h.render();
        const first = ui.commands[kind](item("same-id"));
        await ui.commands[kind](item("same-id"));
        assert.equal(h.pending.length, 1);
        h.switchTo("B");
        const second = h.render().commands[kind](item("same-id", "B"));
        h.pending[0].resolve(fail("A failed"));
        await first;
        assert.equal(h.render().view.error, "");
        assert.equal(h.render().view.busyId, "same-id");
        h.pending[1].resolve(ok({ ...item("same-id", "B"), state: kind === "cancel" ? "cancelled" : "promoted" }));
        await second;
        assert.equal(h.render().view.busyId, "");
        assert.equal(h.render().view.visibleMessages.length, kind === "cancel" ? 0 : 1);
    });
}

test("missing or wrong mutation receipts do not invent a successful queue state", async () => {
    const h = createHarness();
    for (const receipt of [undefined, item("wrong-id"), item("q-A", "B")]) {
        const saving = h.edit(item("q-A"), "keep draft").commands.saveEdit();
        h.pending.at(-1).resolve(ok(receipt));
        await saving;
        assert.equal(h.render().view.editText, "keep draft");
        assert.equal(h.render().view.editBusy, false);
        assert.match(h.render().view.error, /does not match/);
        assert.deepEqual(h.render().view.visibleMessages, []);
    }
});

test("sequence checks and session filtering execute inside the hook; removal persists across navigation", () => {
    const h = createHarness();
    h.render().commands.applySnapshot([item("q-A"), item("q-B", "B")], "A", 10, true);
    assert.deepEqual(h.render().view.visibleMessages.map(value => value.id), ["q-A"]);
    h.render().commands.applySnapshot([], "A", 9, true);
    h.options.latestRealtimeSeqRef.current = 12;
    h.render().commands.applySnapshot([], "A", 11, true);
    assert.deepEqual(h.render().view.visibleMessages.map(value => value.id), ["q-A"]);
    h.switchTo("B");
    h.render().commands.applySnapshot([item("q-B", "B")], "B", 1, true);
    h.render().commands.applySnapshot([item("late-A")], "A", 99, true);
    assert.deepEqual(h.render().view.visibleMessages.map(value => value.id), ["q-B"]);
    h.switchTo("A");
    assert.deepEqual(h.render().view.visibleMessages.map(value => value.id), ["q-A"]);
    h.render().commands.removeMessage("q-A");
    h.switchTo("B"); h.switchTo("A");
    assert.deepEqual(h.render().view.visibleMessages, []);
});

test("cancelled synchronization cannot replace another session or clear its new sync request", async () => {
    const h = createHarness();
    const page = (sessionId, id) => ({ sessionId, latestSeq: 1, queuedMessages: [item(id, sessionId)], queuedMessagesWindow: { hasMore: false, nextOrdinal: null } });
    const first = h.render().commands.synchronize("A");
    h.switchTo("B");
    const second = h.render().commands.synchronize("B");
    h.pending[0].resolve({ ok: true, json: async () => page("A", "q-A") });
    await first;
    await h.render().commands.synchronize("B");
    assert.equal(h.pending.length, 2);
    h.pending[1].resolve({ ok: true, json: async () => page("B", "q-B") });
    await second;
    assert.deepEqual(h.render().view.visibleMessages.map(value => value.id), ["q-B"]);
});

test("unmount aborts the active request and ignores late completion", async () => {
    const h = createHarness();
    const first = h.edit(item("q-A"), "A").commands.saveEdit();
    h.unmount();
    assert.equal(h.pending[0].options.signal.aborted, true);
    h.pending[0].resolve(ok(item("q-A")));
    await first;
});

test("late-request lock test detects removal of the request identity guard", async () => {
    const h = createHarness(source => {
        const guard = "if (mutationRef.current !== request) return;";
        assert.ok(source.includes(guard));
        return source.replace(guard, "");
    });
    const first = h.edit(item("q-A"), "A").commands.saveEdit();
    h.switchTo("B");
    const second = h.edit(item("q-B", "B"), "B").commands.saveEdit();
    h.pending[0].resolve(ok(item("q-A")));
    await first;
    assert.equal(h.render().view.editBusy, false, "mutant incorrectly released B's request");
    h.pending[1].resolve(ok(item("q-B", "B")));
    await second;
});
