import assert from "node:assert/strict";
import test from "node:test";

import {
    nextMotionFrameIndex as nextWebFrame,
    retainDepartingMotionItem as retainWebDepartingItem,
    shouldRunContinuousMotion as shouldRunWebMotion,
    shouldRunTransitionMotion as shouldRunWebTransition,
} from "../src/lib/motion-policy.ts";
import {
    nextMotionFrameIndex as nextPhoneFrame,
    retainDepartingMotionItem as retainPhoneDepartingItem,
    shouldRunContinuousMotion as shouldRunPhoneMotion,
    shouldRunTransitionMotion as shouldRunPhoneTransition,
    voiceWavePhase,
} from "../../v8-agent-os-phone/src/lib/motion-policy.ts";

const clients = [
    { name: "web", continuous: shouldRunWebMotion, transition: shouldRunWebTransition },
    { name: "phone", continuous: shouldRunPhoneMotion, transition: shouldRunPhoneTransition },
];

for (const client of clients) {
    test(`${client.name} continuous motion runs only for an active visible execution`, () => {
        assert.equal(client.continuous({ reducedMotion: false, executionActive: true }), true);
        assert.equal(client.continuous({ reducedMotion: true, executionActive: true }), false);
        assert.equal(client.continuous({ reducedMotion: false, executionActive: false }), false);
        assert.equal(client.continuous({ reducedMotion: false, executionActive: true, surfaceVisible: false }), false);
        assert.equal(client.continuous({ reducedMotion: false, executionActive: true, appVisible: false }), false);
    });

    test(`${client.name} transition motion honors reduced motion and visibility`, () => {
        assert.equal(client.transition({ reducedMotion: false }), true);
        assert.equal(client.transition({ reducedMotion: true }), false);
        assert.equal(client.transition({ reducedMotion: false, surfaceVisible: false }), false);
        assert.equal(client.transition({ reducedMotion: false, appVisible: false }), false);
    });
}

test("sprite clocks stop at a finite action and wrap only for explicit loops", () => {
    for (const nextFrame of [nextWebFrame, nextPhoneFrame]) {
        assert.deepEqual([0, 1, 2, 3].map((index) => nextFrame(index, 4, false)), [1, 2, 3, null]);
        assert.deepEqual([0, 1, 2, 3].map((index) => nextFrame(index, 4, true)), [1, 2, 3, 0]);
        assert.equal(nextFrame(0, 1, true), null);
    }
});

test("departing stages survive unrelated updates until their original exit deadline", () => {
    for (const retainDepartingItem of [retainWebDepartingItem, retainPhoneDepartingItem]) {
        const active = { id: "stage-a", renderPhase: "active", phaseUntil: undefined };
        const exiting = retainDepartingItem(active, new Set(["stage-b"]), (stage) => ({
            ...stage,
            renderPhase: "exiting",
            phaseUntil: 1_700,
        }));
        assert.deepEqual(exiting, { id: "stage-a", renderPhase: "exiting", phaseUntil: 1_700 });

        const retained = retainDepartingItem(exiting, new Set(["stage-c"]), () => {
            throw new Error("an existing exit must not restart");
        });
        assert.equal(retained, exiting);
        assert.equal(retainDepartingItem(exiting, new Set(["stage-a"]), () => exiting), null);
    }
});

test("the shared phone voice clock gives every bar a stable bounded phase", () => {
    const phases = Array.from({ length: 8 }, (_, index) => voiceWavePhase(index));
    assert.equal(new Set(phases).size, 8);
    assert.ok(phases.every((phase) => phase >= 0 && phase < 1));
    assert.equal(voiceWavePhase(8), phases[0]);
    assert.equal(voiceWavePhase(-1), phases[7]);
});
