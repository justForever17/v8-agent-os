import assert from "node:assert/strict";
import { buildCollaborationMicroStages, selectCollaborationMicroStageLayout } from "../dist/collaboration-micro-stage.js";

const runId = "run_micro_stage_demo";
const stages = buildCollaborationMicroStages([
  {
    id: "evt_delegate",
    topic: "delegation_broker.dispatch",
    summary: "派遣 Research 子代理复核资料。",
    timestamp: 100,
    runtimeId: "subagent_swarm",
    data: {
      runId,
      dispatchGroup: "dg_research",
      subagentName: "Research 子代理",
      taskGoal: "复核资料来源",
    },
  },
  {
    id: "evt_child",
    topic: "delegation.child.started",
    summary: "孙代理读取官方文档。",
    timestamp: 200,
    runtimeId: "subagent_swarm",
    data: {
      runId,
      dispatchGroup: "dg_research",
      workerType: "Docs reader",
      compactSummary: "读取官方文档",
    },
  },
  {
    id: "evt_runtime",
    topic: "runtime.episode.active",
    summary: "Engineering 正在生成 work plan。",
    timestamp: 300,
    runtimeId: "engineering",
    data: {
      runId,
      episodeId: "ep_engineering",
      kind: "engineering",
      state: "active",
      compactSummary: "生成 work plan",
      detailRef: "raw://engineering/active",
    },
  },
  {
    id: "evt_handoff",
    topic: "handoff.ref.ready",
    summary: "work_plan_ready",
    timestamp: 400,
    runtimeId: "engineering",
    data: {
      runId,
      producerEpisodeId: "ep_engineering",
      kind: "engineering",
      status: "ready",
      compactSummary: "work_plan_ready",
    },
  },
], { runId, locale: "zh-CN" });

assert.equal(stages.length, 2);

const subagentStage = stages.find((stage) => stage.kind === "subagent");
assert.ok(subagentStage);
assert.equal(subagentStage.title, "子代理微舞台");
assert.equal(subagentStage.steps.length, 2);
assert.equal(subagentStage.actors.length, 2);
assert.deepEqual(subagentStage.steps.map((step) => step.cue), ["summon", "child_agent"]);
assert.deepEqual(subagentStage.actors.map((actor) => actor.cue), ["summon", "child_agent"]);

const runtimeStage = stages.find((stage) => stage.kind === "runtime");
assert.ok(runtimeStage);
assert.equal(runtimeStage.runtimeId, "engineering");
assert.equal(runtimeStage.status, "completed");
assert.equal(runtimeStage.actors.length, 1);
assert.deepEqual(runtimeStage.steps.map((step) => step.cue), ["engineering", "completed"]);
assert.equal(runtimeStage.steps[0].detailRef, "raw://engineering/active");
assert.equal(selectCollaborationMicroStageLayout(stages), "singleRow");

const unrelated = buildCollaborationMicroStages([
  {
    id: "evt_routing_noise",
    topic: "runtime.episode.queued",
    summary: "Runtime routing metadata changed.",
    timestamp: 450,
    runtimeId: "chat",
    data: { runId },
  },
  {
    id: "evt_chat_noise",
    topic: "runtime.episode.active",
    summary: "Chat runtime is active.",
    timestamp: 451,
    runtimeId: "chat",
    data: { runId, episodeId: "ep_chat", kind: "chat" },
  },
], { runId, locale: "zh-CN" });

assert.equal(unrelated.length, 0);

const degraded = buildCollaborationMicroStages([
  {
    id: "evt_missing",
    topic: "delegation_broker.dispatch",
    summary: "没有形成有效子任务。",
    timestamp: 500,
    runtimeId: "subagent_swarm",
    data: {
      runId,
      dispatchGroup: "dg_missing",
      dispatchStatus: "missing_tasks",
      missingTasks: true,
    },
  },
], { runId, locale: "zh-CN" });

assert.equal(degraded.length, 1);
assert.equal(degraded[0].status, "degraded");
assert.equal(degraded[0].steps[0].cue, "degraded");

const aliasedDelegation = buildCollaborationMicroStages([
  {
    id: "evt_alias_planned",
    topic: "delegation_broker.dispatch",
    summary: "子任务已规划。",
    timestamp: 510,
    runtimeId: "subagent_swarm",
    data: { runId, taskBriefId: "task_alias", state: "pending" },
  },
  {
    id: "evt_alias_started",
    topic: "subagent.task.started",
    summary: "子代理开始执行。",
    timestamp: 520,
    runtimeId: "subagent_swarm",
    data: {
      runId,
      taskBriefId: "task_alias",
      delegationId: "delegation_alias",
      state: "active",
    },
  },
  {
    id: "evt_alias_completed",
    topic: "delegation.completed",
    summary: "子代理回流完成。",
    timestamp: 530,
    runtimeId: "subagent_swarm",
    data: { runId, delegationId: "delegation_alias", status: "completed" },
  },
], { runId, locale: "zh-CN" });

assert.equal(aliasedDelegation.length, 1);
assert.equal(aliasedDelegation[0].id, "subagent:task_alias");
assert.equal(aliasedDelegation[0].status, "completed");
assert.equal(aliasedDelegation[0].steps.length, 3);

const manyActors = buildCollaborationMicroStages(Array.from({ length: 10 }).map((_, index) => ({
  id: `evt_subagent_${index}`,
  topic: "subagent.task.started",
  summary: `子代理 ${index + 1} 接单。`,
  timestamp: 600 + index,
  runtimeId: "subagent_swarm",
  data: {
    runId,
    dispatchGroup: "dg_many",
    taskBriefId: `task_${index}`,
    subagentName: `Worker ${index + 1}`,
    state: "active",
  },
})), { runId, locale: "zh-CN", limit: 10 });

assert.equal(manyActors.length, 1);
assert.equal(manyActors[0].actors.length, 10);
assert.equal(selectCollaborationMicroStageLayout(manyActors), "clusteredGrid");

console.log("collaboration micro-stage verified");
