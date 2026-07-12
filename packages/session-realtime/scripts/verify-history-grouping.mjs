import assert from "node:assert/strict";

import {
  groupSessionHistoryByWorkspace,
  normalizeAuthoritativeSessionHistoryList,
} from "../dist/history.js";

const labels = {
  mainWorkspace: "Main workspace",
  externalWorkspace: "External workspace",
  unbound: "Unbound",
  workspace: "Workspace",
};

const records = normalizeAuthoritativeSessionHistoryList([
  {
    id: "session-project-explicit",
    title: "Project",
    createdAt: "2026-07-10T10:00:00Z",
    workspaceDisplayName: "Alpha custom",
    workspacePinned: true,
    workspacePinnedAt: "2026-07-10T11:00:00Z",
    metadata: {
      project_id: "project-alpha",
      workspace_id: "workspace-alpha",
      workspace_path: "E:\\Projects\\alpha",
      scope_hint: "project",
      resolved_scope: "project:project-alpha",
    },
  },
  {
    id: "session-project-pinned-task",
    title: "Pinned task",
    pinned: true,
    pinnedAt: "2026-07-10T12:00:00Z",
    createdAt: "2026-07-10T08:30:00Z",
    projectId: "project-alpha",
    workspaceId: "workspace-alpha",
    workspacePath: "E:\\Projects\\alpha",
    workspaceDisplayName: "Alpha custom",
    workspacePinned: true,
    workspacePinnedAt: "2026-07-10T11:00:00Z",
    resolvedScope: "project:project-alpha",
  },
  {
    id: "session-project-legacy",
    title: "Legacy project",
    createdAt: "2026-07-10T09:00:00Z",
    scopeTags: ["project:legacy-only"],
  },
  {
    id: "session-main-workspace",
    title: "Main",
    createdAt: "2026-07-10T08:00:00Z",
    workspacePath: "E:\\Projects\\v8chat",
    resolvedScope: "workspace:main",
  },
  {
    id: "session-unbound",
    title: "Unbound",
    createdAt: "2026-07-10T07:00:00Z",
  },
]);

const explicitRecord = records.find((record) => record.id === "session-project-explicit");
assert.equal(explicitRecord?.projectId, "project-alpha");
assert.equal(explicitRecord?.workspaceId, "workspace-alpha");
assert.equal(explicitRecord?.workspacePath, "E:\\Projects\\alpha");
assert.equal(explicitRecord?.scopeHint, "project");

const groups = groupSessionHistoryByWorkspace(records, labels);
const explicitProject = groups.find((group) => group.key === "project:project-alpha");
const legacyProject = groups.find((group) => group.key === "project:project:legacy-only");
const mainWorkspace = groups.find((group) => group.label === labels.mainWorkspace);
const unbound = groups.find((group) => group.kind === "unbound");

assert.equal(groups[0]?.key, "project:project-alpha");
assert.equal(explicitProject?.label, "Alpha custom");
assert.equal(explicitProject?.pinned, true);
assert.equal(explicitProject?.items[0]?.id, "session-project-pinned-task");
assert.deepEqual(explicitProject?.creationBinding, {
  projectId: "project-alpha",
  workspaceId: "workspace-alpha",
  workspacePath: "E:\\Projects\\alpha",
  scopeHint: "project",
  scopeMode: "explicit",
});
assert.equal(legacyProject?.creationBinding, null);
assert.deepEqual(mainWorkspace?.creationBinding, {
  workspacePath: "E:\\Projects\\v8chat",
  scopeMode: "explicit",
});
assert.equal(unbound?.creationBinding, null);

console.log("history grouping contract verified");
