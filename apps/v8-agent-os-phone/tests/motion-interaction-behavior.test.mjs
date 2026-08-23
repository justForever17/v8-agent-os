import assert from "node:assert/strict";
import test from "node:test";

import {
    createFrameTaskScheduler,
    createTerminalScrollState,
    reduceTerminalScrollState,
    resolveHiddenControlBehavior,
} from "../src/lib/motion-behavior.ts";

test("hidden controls cannot receive touch or accessibility focus", () => {
    assert.deepEqual(resolveHiddenControlBehavior(false), {
        pointerEvents: "none",
        accessible: false,
        accessibilityElementsHidden: true,
        importantForAccessibility: "no-hide-descendants",
        disabled: true,
    });
    assert.deepEqual(resolveHiddenControlBehavior(true), {
        pointerEvents: "auto",
        accessible: true,
        accessibilityElementsHidden: false,
        importantForAccessibility: "auto",
        disabled: false,
    });
});

test("terminal follows new output only while it was pinned and the user is not dragging", () => {
    let state = createTerminalScrollState();
    state = reduceTerminalScrollState(state, {
        type: "scroll",
        contentHeight: 500,
        viewportHeight: 200,
        offsetY: 300,
    }).state;

    let transition = reduceTerminalScrollState(state, {
        type: "content_resize",
        contentHeight: 540,
    });
    assert.equal(transition.shouldScrollToEnd, true);
    assert.equal(transition.state.isPinnedToBottom, true);

    state = reduceTerminalScrollState(transition.state, { type: "drag_start" }).state;
    transition = reduceTerminalScrollState(state, {
        type: "content_resize",
        contentHeight: 580,
    });
    assert.equal(transition.shouldScrollToEnd, false);

    state = reduceTerminalScrollState(transition.state, {
        type: "scroll",
        contentHeight: 580,
        viewportHeight: 200,
        offsetY: 120,
    }).state;
    state = reduceTerminalScrollState(state, { type: "drag_end" }).state;
    transition = reduceTerminalScrollState(state, {
        type: "content_resize",
        contentHeight: 620,
    });
    assert.equal(transition.shouldScrollToEnd, false);
    assert.equal(transition.state.isPinnedToBottom, false);

    state = reduceTerminalScrollState(transition.state, {
        type: "scroll",
        contentHeight: 620,
        viewportHeight: 200,
        offsetY: 404,
    }).state;
    transition = reduceTerminalScrollState(state, {
        type: "content_resize",
        contentHeight: 640,
    });
    assert.equal(transition.shouldScrollToEnd, true);
});

test("terminal output bursts schedule at most one scroll per frame", () => {
    const frames = [];
    const cancelled = [];
    const executed = [];
    let nextHandle = 0;
    const scheduler = createFrameTaskScheduler(
        (callback) => {
            frames.push(callback);
            nextHandle += 1;
            return nextHandle;
        },
        (handle) => cancelled.push(handle),
    );

    assert.equal(scheduler.request(() => executed.push("first")), true);
    assert.equal(scheduler.request(() => executed.push("latest")), false);
    assert.equal(frames.length, 1);
    assert.equal(scheduler.isPending(), true);

    frames.shift()();
    assert.deepEqual(executed, ["latest"]);
    assert.equal(scheduler.isPending(), false);

    scheduler.request(() => executed.push("cancelled"));
    scheduler.cancel();
    assert.deepEqual(cancelled, [2]);
    assert.equal(scheduler.isPending(), false);
});
