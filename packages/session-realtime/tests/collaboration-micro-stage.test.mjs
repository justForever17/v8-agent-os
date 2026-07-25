import assert from "node:assert/strict";
import test from "node:test";

import { buildCollaborationMicroStages } from "../dist/collaboration-micro-stage.js";

test("nested progress events preserve their envelope identity and form one subagent", () => {
  const runId = "run_nested_delegation";
  const episodeId = "subagent::delegation_one::0::heading-audit::reviewer";
  const activities = [
    {
      id: "event_progress_1",
      topic: "runtime.episode.progress",
      summary: "子代理正在核对标题。",
      timestamp: 100,
      runtimeId: "engineering",
      data: {
        runId,
        delegationId: "delegation_one",
        agentName: "Code Review Architect",
        episode: {
          episodeId,
          kind: "delegation",
          state: "active",
          parentEpisodeId: "episode_engineering",
        },
      },
    },
    {
      id: "event_progress_2",
      topic: "runtime.episode.progress",
      summary: "blocked: README-ZH.md 不存在，无法完成核对。",
      timestamp: 200,
      runtimeId: "engineering",
      data: {
        runId,
        delegationId: "delegation_one",
        agentName: "Code Review Architect",
        status: "completed",
        episode: {
          episodeId,
          kind: "delegation",
          state: "completed",
          parentEpisodeId: "episode_engineering",
        },
      },
    },
  ];

  const stages = buildCollaborationMicroStages(activities, { runId, locale: "zh-CN" });

  assert.equal(stages.length, 1);
  assert.equal(stages[0].kind, "subagent");
  assert.equal(stages[0].actors.length, 1);
  assert.equal(stages[0].actors[0].label, "Code Review Architect");
  assert.equal(stages[0].status, "failed");
  assert.equal(stages[0].actors[0].status, "failed");
  assert.equal(stages[0].steps.length, 2);
});

test("a message-bound stage never adopts runless or other-run collaboration activity", () => {
  const expectedRunId = "run_current";
  const stages = buildCollaborationMicroStages([
    {
      id: "event_runless",
      topic: "runtime.episode.progress",
      summary: "旧事件没有 run 血缘。",
      timestamp: 100,
      runtimeId: "subagent_swarm",
      data: { episodeId: "episode_old", kind: "delegation", state: "active" },
    },
    {
      id: "event_other_run",
      topic: "runtime.episode.progress",
      summary: "另一个 run 的事件。",
      timestamp: 200,
      runtimeId: "subagent_swarm",
      data: { runId: "run_other", episodeId: "episode_other", kind: "delegation", state: "active" },
    },
    {
      id: "event_current",
      topic: "runtime.episode.progress",
      summary: "当前 run 的事件。",
      timestamp: 300,
      runtimeId: "subagent_swarm",
      data: { runId: expectedRunId, episodeId: "episode_current", kind: "delegation", state: "active" },
    },
  ], { runId: expectedRunId, locale: "zh-CN" });

  assert.equal(stages.length, 1);
  assert.deepEqual(stages[0].sourceActivityIds, ["event_current"]);
});

test("one real delegation is not duplicated by capability noise or evolving lineage", () => {
  const runId = "run_real_delegation";
  const episodeId = "subagent::delegation_one::0::heading-review::docs-writer";
  const activities = [
    {
      id: "evt_tool_started",
      topic: "tool.started",
      summary: "Delegation broker 已启动",
      timestamp: 10,
      runtimeId: "subagent_swarm",
      data: { runId, episodeId: "42", dispatchStatus: "dispatch_attempted" },
    },
    {
      id: "evt_capability_need",
      topic: "capability.need.detected",
      summary: "检测到 subagent_swarm 能力需求",
      timestamp: 20,
      runtimeId: "subagent_swarm",
      data: { runId, episodeId: "44" },
    },
    {
      id: "evt_waiting",
      topic: "runtime.episode.waiting",
      summary: "独立复核 README。",
      timestamp: 30,
      runtimeId: "subagent_swarm",
      data: { runId, episodeId, episodeKind: "delegation" },
    },
    {
      id: "evt_text",
      topic: "subagent.text.delta",
      summary: "协作回复已更新",
      timestamp: 40,
      runtimeId: "subagent_swarm",
      data: {
        runId,
        ownerAgentId: "docs-writer",
        delegationId: episodeId,
      },
    },
    {
      id: "evt_progress",
      topic: "runtime.episode.progress",
      summary: "正在使用 read_native_file。",
      timestamp: 50,
      runtimeId: "subagent_swarm",
      data: {
        runId,
        episodeId,
        episodeKind: "delegation",
        progress: {
          agentId: "docs-writer",
          agentName: "Docs Delivery Writer",
          delegationId: episodeId,
          taskBriefId: "heading-review",
          status: "running",
        },
      },
    },
    {
      id: "evt_completed",
      topic: "runtime.episode.completed",
      summary: "独立复核 README。",
      timestamp: 60,
      runtimeId: "subagent_swarm",
      data: { runId, episodeId, episodeKind: "delegation", status: "completed" },
    },
  ];

  const stages = buildCollaborationMicroStages(activities, { runId, locale: "zh-CN" });

  assert.equal(stages.length, 1);
  assert.equal(stages[0].actors.length, 1);
  assert.equal(stages[0].actors[0].label, "Docs Delivery Writer");
  assert.equal(stages[0].actors[0].status, "completed");
  assert.deepEqual(stages[0].sourceActivityIds, ["evt_waiting", "evt_text", "evt_progress", "evt_completed"]);
  assert.ok(!stages[0].sourceActivityIds.includes("evt_tool_started"));
  assert.ok(!stages[0].sourceActivityIds.includes("evt_capability_need"));
});

test("an actor keeps one identity while task brief lineage resolves into an episode", () => {
  const runId = "run_incremental_lineage";
  const episodeId = "subagent::delegation_two::0::package-review::docs-writer";
  const early = {
    id: "evt_dispatched",
    topic: "subagent.task.dispatched",
    summary: "核对 package.json。",
    timestamp: 10,
    runtimeId: "subagent_swarm",
    data: {
      runId,
      taskBriefId: "package-review",
      childEpisodeId: episodeId,
      targetLabel: "Docs Delivery Writer",
    },
  };
  const later = {
    id: "evt_progress",
    topic: "runtime.episode.progress",
    summary: "正在读取 package.json。",
    timestamp: 20,
    runtimeId: "subagent_swarm",
    data: {
      runId,
      episodeId,
      episodeKind: "delegation",
      progress: {
        delegationId: episodeId,
        agentId: "docs-writer",
        agentName: "Docs Delivery Writer",
        status: "running",
      },
    },
  };

  const earlyStage = buildCollaborationMicroStages([early], { runId, locale: "zh-CN" })[0];
  const resolvedStage = buildCollaborationMicroStages([early, later], { runId, locale: "zh-CN" })[0];

  assert.equal(earlyStage.id, `subagent:${episodeId}`);
  assert.equal(resolvedStage.id, earlyStage.id);
  assert.equal(resolvedStage.actors.length, 1);
  assert.equal(earlyStage.actors[0].id, `subagent:${episodeId}`);
  assert.equal(resolvedStage.actors[0].id, earlyStage.actors[0].id);
});
