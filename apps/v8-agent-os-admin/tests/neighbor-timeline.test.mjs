import assert from "node:assert/strict";
import test from "node:test";
import { mergeNeighborTimeline } from "../src/components/network-supervisor/neighbor-timeline.ts";

test("latest page updates delivery in place and retains older pages beyond 100 messages", () => {
    const make = seq => ({ messageId: `m${seq}`, seq, status: "pending" });
    const latest = Array.from({ length: 100 }, (_, index) => make(index + 31));
    const older = Array.from({ length: 30 }, (_, index) => make(index + 1));
    const loaded = mergeNeighborTimeline(latest, older);
    assert.equal(loaded.length, 130);
    const updated = mergeNeighborTimeline(loaded, [{ ...make(130), status: "delivered" }, make(131)]);
    assert.equal(updated.length, 131);
    assert.equal(updated[0].messageId, "m1");
    assert.equal(updated.at(-1).messageId, "m131");
    assert.equal(updated.find(item => item.messageId === "m130").status, "delivered");
    assert.equal(updated.filter(item => item.messageId === "m130").length, 1);
});
