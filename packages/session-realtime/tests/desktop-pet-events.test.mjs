import assert from "node:assert/strict";
import test from "node:test";

import {
  DESKTOP_PET_EVENT_CATALOG,
  desktopPetEventLabel,
  expandLegacyDesktopPetEvents,
  normalizeDesktopPetEventId,
} from "../dist/desktop-pet-events.js";

test("desktop pet event catalog exposes translated structured events", () => {
  assert.ok(DESKTOP_PET_EVENT_CATALOG.length >= 10);
  assert.equal(normalizeDesktopPetEventId("tool.started"), "tool.started");
  assert.equal(normalizeDesktopPetEventId("tool_start"), "tool.started");
  assert.equal(normalizeDesktopPetEventId("tool_start|工具"), null);
  assert.equal(desktopPetEventLabel("approval.requested", "zh-CN"), "等待用户审批");
  assert.equal(desktopPetEventLabel("approval.requested", "en"), "Waiting for approval");
});

test("legacy trigger text is read-compatible but projected to exact event ids", () => {
  assert.deepEqual(
    expandLegacyDesktopPetEvents("tool_start|tool_result|工具"),
    ["tool.started", "tool.finished"],
  );
  assert.deepEqual(
    expandLegacyDesktopPetEvents("subagent.task.completed|完成"),
    ["subagent.task.completed"],
  );
  assert.deepEqual(expandLegacyDesktopPetEvents("random keyword"), []);
});
