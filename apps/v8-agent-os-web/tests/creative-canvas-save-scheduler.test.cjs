/* eslint-disable @typescript-eslint/no-require-imports */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const ts = require("typescript");

function loadScheduler() {
    const sourcePath = path.resolve(
        __dirname,
        "../src/components/workbench/creative-canvas/save-scheduler.ts",
    );
    const source = fs.readFileSync(sourcePath, "utf8");
    const output = ts.transpileModule(source, {
        compilerOptions: {
            module: ts.ModuleKind.CommonJS,
            target: ts.ScriptTarget.ES2020,
            strict: true,
        },
        fileName: sourcePath,
    }).outputText;
    const moduleRecord = { exports: {} };
    Function("module", "exports", "require", output)(moduleRecord, moduleRecord.exports, require);
    return moduleRecord.exports;
}

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

function tick() {
    return new Promise((resolve) => setImmediate(resolve));
}

function graph(sessionId, revision) {
    return { sessionId, revision, value: `${sessionId}-${revision}` };
}

function keyOf(value) {
    return value.value;
}

function seed(revision = 0) {
    return { revision, lastSavedKey: "", persisted: false, migrationPending: false };
}

test("A in-flight does not block or clear B saves, including B's next pending graph", async () => {
    const { createCanvasGraphSaveScheduler } = loadScheduler();
    const requests = [];
    const controls = [];
    const scheduler = createCanvasGraphSaveScheduler({
        persistenceKeyOf: keyOf,
        save: (request) => {
            const control = deferred();
            requests.push(request);
            controls.push(control);
            return control.promise;
        },
    });
    scheduler.ensureSession("A", seed());
    scheduler.ensureSession("B", seed());

    const saveA = scheduler.enqueue("A", graph("A", 1));
    const saveB1 = scheduler.enqueue("B", graph("B", 1));
    const saveB2 = scheduler.enqueue("B", graph("B", 2));

    assert.equal(requests.length, 2, "B starts in its own lane while A is still in flight");
    assert.equal(requests[0].sessionId, "A");
    assert.equal(requests[1].sessionId, "B");
    assert.equal(scheduler.getState("B").pendingKey, "B-2");

    controls[0].resolve({ accepted: true, revision: 1, meta: {} });
    assert.equal(await saveA, true);
    await tick();
    assert.equal(scheduler.getState("B").pendingKey, "B-2", "A response cannot clear B pending state");
    assert.equal(scheduler.getState("B").inFlightKey, "B-1");

    controls[1].resolve({ accepted: true, revision: 1, meta: {} });
    assert.equal(await saveB1, true);
    await tick();
    assert.equal(requests.length, 3);
    assert.equal(requests[2].sessionId, "B");
    assert.equal(requests[2].graph.value, "B-2");
    assert.equal(requests[2].expectedRevision, 1);
    controls[2].resolve({ accepted: true, revision: 2, meta: {} });
    assert.equal(await saveB2, true);
    scheduler.dispose();
});

test("rapid A-B-A keeps endpoint, storage identity, payload, and revision in one session lane", async () => {
    const { createCanvasGraphSaveScheduler } = loadScheduler();
    const requests = [];
    const controls = [];
    const scheduler = createCanvasGraphSaveScheduler({
        persistenceKeyOf: keyOf,
        save: (request) => {
            const control = deferred();
            requests.push(request);
            controls.push(control);
            return control.promise;
        },
    });
    scheduler.ensureSession("A/one", seed(4));
    scheduler.ensureSession("B two", seed(8));

    const saveA1 = scheduler.enqueue("A/one", graph("A/one", 1));
    const saveB = scheduler.enqueue("B two", graph("B two", 1));
    const saveA2 = scheduler.enqueue("A/one", graph("A/one", 2));

    assert.equal(requests.length, 2);
    for (const request of requests) {
        assert.equal(request.graph.sessionId, request.sessionId);
        assert.equal(request.storageKey, `v8-web-creative-canvas:v3:${request.sessionId}`);
        assert.equal(request.legacyStorageKey, `v8-web-creative-canvas:v2:${request.sessionId}`);
        assert.equal(
            request.endpoint,
            `/api/workbench/sessions/${encodeURIComponent(request.sessionId)}/canvas/graph`,
        );
    }
    assert.equal(requests[0].expectedRevision, 4);
    assert.equal(requests[1].expectedRevision, 8);

    controls[1].resolve({ accepted: true, revision: 9, meta: {} });
    controls[0].resolve({ accepted: true, revision: 5, meta: {} });
    assert.equal(await saveB, true);
    assert.equal(await saveA1, true);
    await tick();
    assert.equal(requests.length, 3);
    assert.equal(requests[2].sessionId, "A/one");
    assert.equal(requests[2].graph.sessionId, "A/one");
    assert.equal(requests[2].expectedRevision, 5);
    controls[2].resolve({ accepted: true, revision: 6, meta: {} });
    assert.equal(await saveA2, true);
    assert.equal(scheduler.getState("A/one").revision, 6);
    assert.equal(scheduler.getState("B two").revision, 9);
    scheduler.dispose();
});

test("returning to the in-flight graph cancels a stale pending save", async () => {
    const { createCanvasGraphSaveScheduler } = loadScheduler();
    const requests = [];
    const controls = [];
    const scheduler = createCanvasGraphSaveScheduler({
        persistenceKeyOf: keyOf,
        save: (request) => {
            const control = deferred();
            requests.push(request);
            controls.push(control);
            return control.promise;
        },
    });
    scheduler.ensureSession("A", seed());

    const saveA1 = scheduler.enqueue("A", { value: "A" });
    const saveB = scheduler.enqueue("A", { value: "B" });
    const saveA2 = scheduler.enqueue("A", { value: "A" });

    assert.equal(requests.length, 1);
    assert.equal(scheduler.getState("A").inFlightKey, "A");
    assert.equal(scheduler.getState("A").pendingKey, "", "latest A cancels stale pending B");
    assert.equal(await saveB, false, "superseded B is not reported as saved");

    controls[0].resolve({ accepted: true, revision: 1, meta: {} });
    assert.equal(await saveA1, true);
    assert.equal(await saveA2, true);
    await tick();

    assert.deepEqual(requests.map((request) => request.graph.value), ["A"]);
    const finalState = scheduler.getState("A");
    assert.equal(finalState.lastSavedKey, "A");
    assert.equal(finalState.dirty, false);
    assert.equal(finalState.pendingKey, "");
    assert.equal(finalState.inFlightKey, "");
    scheduler.dispose();
});

test("close-before-debounce flush survives callback detach and immediate reopen", async () => {
    const { createCanvasGraphSaveScheduler } = loadScheduler();
    const requests = [];
    const control = deferred();
    const firstMountResults = [];
    const reopenedResults = [];
    const scheduler = createCanvasGraphSaveScheduler({
        persistenceKeyOf: keyOf,
        save: (request) => {
            requests.push(request);
            return control.promise;
        },
    });
    scheduler.ensureSession("A", {
        revision: 4,
        lastSavedKey: "A-old",
        persisted: true,
        migrationPending: false,
    });
    const detachFirstMount = scheduler.setCallbacks({
        onResult: (_identity, savedGraph) => firstMountResults.push(savedGraph.value),
    });

    const detachedState = scheduler.flush("A", { value: "A-latest" });
    detachFirstMount();

    assert.equal(requests.length, 1, "unmount flush starts without waiting for the debounce timer");
    assert.equal(detachedState.dirty, true);
    assert.equal(detachedState.inFlightKey, "A-latest");
    assert.equal(scheduler.getState("A").dirty, true, "the lane survives while no UI owns callbacks");
    assert.deepEqual(scheduler.getDesired("A").graph, { value: "A-latest" });

    scheduler.setCallbacks({
        onResult: (_identity, savedGraph) => reopenedResults.push(savedGraph.value),
    });
    control.resolve({ accepted: true, revision: 5, meta: {} });
    await tick();

    assert.deepEqual(firstMountResults, []);
    assert.deepEqual(reopenedResults, ["A-latest"], "the reopened owner receives the settled save");
    assert.equal(scheduler.getState("A").lastSavedKey, "A-latest");
    assert.equal(scheduler.getState("A").dirty, false);
    scheduler.dispose();
});

test("a stale conflict cannot replace a newer pending graph", async () => {
    const { createCanvasGraphSaveScheduler } = loadScheduler();
    const requests = [];
    const controls = [];
    const projectedRecoveries = [];
    const scheduler = createCanvasGraphSaveScheduler({
        persistenceKeyOf: keyOf,
        save: (request) => {
            const control = deferred();
            requests.push(request);
            controls.push(control);
            return control.promise;
        },
    });
    scheduler.ensureSession("A", {
        revision: 7,
        lastSavedKey: "engine-old",
        persisted: true,
        migrationPending: false,
    });
    scheduler.setCallbacks({
        onResult: (identity, savedGraph, result) => {
            const desired = scheduler.getDesired(identity.sessionId);
            if (result.meta.recoveredGraph && (
                !desired || desired.persistenceKey === keyOf(savedGraph)
            )) {
                projectedRecoveries.push(result.meta.recoveredGraph.value);
            }
        },
    });

    const saveB = scheduler.enqueue("A", { value: "B" });
    const saveC = scheduler.enqueue("A", { value: "C" });
    assert.equal(scheduler.getDesired("A").persistenceKey, "C");

    controls[0].resolve({
        accepted: false,
        revision: 8,
        persistenceKey: "engine-D",
        meta: { recoveredGraph: { value: "engine-D" } },
    });
    assert.equal(await saveB, false);
    await tick();

    assert.deepEqual(projectedRecoveries, [], "B's recovery cannot overwrite the newer C UI state");
    assert.deepEqual(requests.map((request) => request.graph.value), ["B", "C"]);
    controls[1].resolve({ accepted: true, revision: 9, meta: {} });
    assert.equal(await saveC, true);
    await tick();

    assert.equal(scheduler.getState("A").lastSavedKey, "C");
    assert.equal(scheduler.getState("A").dirty, false);
    assert.equal(scheduler.getDesired("A"), null);
    scheduler.dispose();
});

test("same semantic graph keeps the latest presentation through conflict recovery", async () => {
    const { createCanvasGraphSaveScheduler } = loadScheduler();
    const control = deferred();
    const projectedGraphs = [];
    const scheduler = createCanvasGraphSaveScheduler({
        persistenceKeyOf: keyOf,
        save: () => control.promise,
    });
    scheduler.ensureSession("A", {
        revision: 3,
        lastSavedKey: "engine-old",
        persisted: true,
        migrationPending: false,
    });
    scheduler.setCallbacks({
        onResult: (_identity, savedGraph) => projectedGraphs.push(savedGraph),
    });

    const firstSave = scheduler.enqueue("A", { value: "same-graph", viewport: "old" });
    const latestPresentation = scheduler.enqueue("A", { value: "same-graph", viewport: "latest" });
    assert.equal(scheduler.getState("A").pendingKey, "");
    assert.equal(scheduler.getDesired("A").graph.viewport, "latest");

    control.resolve({
        accepted: false,
        revision: 4,
        persistenceKey: "engine-recovered",
        meta: { recoveredGraph: { value: "engine-recovered", viewport: "engine" } },
    });
    assert.equal(await firstSave, false);
    assert.equal(await latestPresentation, false);
    await tick();

    assert.equal(projectedGraphs[0].viewport, "latest");
    assert.equal(scheduler.getSettled("A").graph.viewport, "latest");
    assert.equal(scheduler.getDesired("A"), null);
    scheduler.dispose();
});

test("a late save result cannot regress a newer hydrated Engine revision", async () => {
    const { createCanvasGraphSaveScheduler } = loadScheduler();
    const control = deferred();
    const scheduler = createCanvasGraphSaveScheduler({
        persistenceKeyOf: keyOf,
        save: () => control.promise,
    });
    scheduler.ensureSession("A", {
        revision: 4,
        lastSavedKey: "engine-4",
        persisted: true,
        migrationPending: false,
    });

    const lateSave = scheduler.enqueue("A", { value: "local-5" });
    scheduler.configureSession("A", {
        revision: 6,
        lastSavedKey: "engine-6",
        persisted: true,
        migrationPending: false,
    });
    assert.equal(scheduler.getDesired("A"), null, "the newer Engine revision supersedes the old in-flight intent");

    control.resolve({ accepted: true, revision: 5, meta: {} });
    assert.equal(await lateSave, false);
    await tick();

    assert.equal(scheduler.getState("A").revision, 6);
    assert.equal(scheduler.getState("A").lastSavedKey, "engine-6");
    assert.equal(scheduler.getSettled("A"), null);
    scheduler.dispose();
});

test("a detached conflict keeps the recovered Engine graph for hydration", async () => {
    const { createCanvasGraphSaveScheduler } = loadScheduler();
    const control = deferred();
    const scheduler = createCanvasGraphSaveScheduler({
        persistenceKeyOf: keyOf,
        save: () => control.promise,
    });
    scheduler.ensureSession("A", {
        revision: 7,
        lastSavedKey: "engine-old",
        persisted: true,
        migrationPending: false,
    });

    const detach = scheduler.setCallbacks({});
    scheduler.flush("A", { value: "local-edit" });
    detach();
    control.resolve({
        accepted: false,
        revision: 8,
        persistenceKey: "engine-recovered",
        meta: { recoveredGraph: { value: "engine-recovered" } },
    });
    await tick();

    const settled = scheduler.getSettled("A");
    assert.equal(scheduler.getState("A").dirty, false);
    assert.equal(scheduler.getState("A").lastSavedKey, "engine-recovered");
    assert.equal(settled.result.accepted, false);
    assert.equal(settled.result.revision, 8);
    assert.deepEqual(settled.result.meta.recoveredGraph, { value: "engine-recovered" });
    assert.deepEqual(settled.graph, { value: "local-edit" }, "the rejected local graph remains distinguishable");
    scheduler.dispose();
});

test("failed save remains dirty and retries without claiming the candidate was saved", async () => {
    const { createCanvasGraphSaveScheduler } = loadScheduler();
    const timers = [];
    const requests = [];
    const retryControl = deferred();
    const scheduler = createCanvasGraphSaveScheduler({
        persistenceKeyOf: keyOf,
        retryDelaysMs: [10],
        setTimer: (callback, delayMs) => {
            const timer = { callback, delayMs, cleared: false };
            timers.push(timer);
            return timer;
        },
        clearTimer: (timer) => {
            timer.cleared = true;
        },
        save: (request) => {
            requests.push(request);
            return requests.length === 1
                ? Promise.reject(new Error("offline"))
                : retryControl.promise;
        },
    });
    scheduler.ensureSession("A", {
        revision: 2,
        lastSavedKey: "A-old",
        persisted: true,
        migrationPending: false,
    });

    await assert.rejects(scheduler.enqueue("A", graph("A", 3)), /offline/);
    await tick();
    const failed = scheduler.getState("A");
    assert.equal(failed.dirty, true);
    assert.equal(failed.persisted, true);
    assert.equal(failed.lastSavedKey, "A-old", "failed candidate must not become the saved truth");
    assert.equal(failed.pendingKey, "A-3");
    assert.equal(timers.length, 1);
    assert.equal(timers[0].delayMs, 10);

    timers[0].callback();
    assert.equal(requests.length, 2);
    assert.equal(requests[1].expectedRevision, 2);
    retryControl.resolve({ accepted: true, revision: 3, meta: {} });
    await tick();
    const recovered = scheduler.getState("A");
    assert.equal(recovered.dirty, false);
    assert.equal(recovered.lastSavedKey, "A-3");
    assert.equal(recovered.revision, 3);
    scheduler.dispose();
});
