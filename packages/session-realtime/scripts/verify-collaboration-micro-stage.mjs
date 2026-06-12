import assert from "node:assert/strict";
import { buildCollaborationMicroStages } from "../dist/collaboration-micro-stage.js";

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
assert.deepEqual(subagentStage.steps.map((step) => step.cue), ["summon", "child_agent"]);

const runtimeStage = stages.find((stage) => stage.kind === "runtime");
assert.ok(runtimeStage);
assert.equal(runtimeStage.runtimeId, "engineering");
assert.equal(runtimeStage.status, "completed");
assert.deepEqual(runtimeStage.steps.map((step) => step.cue), ["engineering", "completed"]);
assert.equal(runtimeStage.steps[0].detailRef, "raw://engineering/active");

const unrelated = buildCollaborationMicroStages([
  {
    id: "evt_planner_noise",
    topic: "planner.plan.created",
    summary: "Planner generated a draft.",
    timestamp: 450,
    runtimeId: "planner_lane",
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

console.log("collaboration micro-stage verified");
