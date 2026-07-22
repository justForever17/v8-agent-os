import assert from "node:assert/strict";
import test from "node:test";

import {
  buildReasoningEffortFillCells,
  resolveNearestReasoningEffortIndex,
  resolveReasoningEffortStop,
} from "../dist/ReasoningEffortControl.js";

test("reasoning effort stops use the full usable track without dead edge segments", () => {
  assert.equal(resolveReasoningEffortStop(0, 5), 0);
  assert.equal(resolveReasoningEffortStop(4, 5), 1);
  assert.equal(resolveReasoningEffortStop(-4, 5), 0);
  assert.equal(resolveReasoningEffortStop(99, 5), 1);
});

test("continuous drag settles on the nearest declared level", () => {
  const second = resolveReasoningEffortStop(1, 5);
  const third = resolveReasoningEffortStop(2, 5);
  const midpoint = (second + third) / 2;
  assert.equal(resolveNearestReasoningEffortIndex(midpoint - 0.001, 5), 1);
  assert.equal(resolveNearestReasoningEffortIndex(midpoint + 0.001, 5), 2);
});

test("dynamic ModelHub level counts share the same geometry", () => {
  assert.equal(resolveNearestReasoningEffortIndex(resolveReasoningEffortStop(2, 3), 3), 2);
  assert.equal(resolveNearestReasoningEffortIndex(resolveReasoningEffortStop(5, 6), 6), 5);
});

test("max effort uses a deterministic finite grid that fills from right to left", () => {
  const cells = buildReasoningEffortFillCells(312, 28, 1);
  assert.ok(cells.length > 250);
  assert.deepEqual(cells, buildReasoningEffortFillCells(312, 28, 1));

  const ordered = cells.toSorted((left, right) => left.x - right.x);
  const midpoint = Math.floor(ordered.length / 2);
  const leftAverage = ordered.slice(0, midpoint).reduce((sum, cell) => sum + cell.revealAt, 0) / midpoint;
  const rightAverage = ordered.slice(midpoint).reduce((sum, cell) => sum + cell.revealAt, 0) / (ordered.length - midpoint);
  assert.ok(leftAverage > rightAverage, "left-side cells must settle later than right-side cells");
  assert.ok(Math.min(...cells.map((cell) => cell.x)) > 60, "the finite fill must not flood the entire rail");
  assert.ok(Math.max(...cells.map((cell) => cell.x)) < 295, "the fill must stop before the thumb center");
});
