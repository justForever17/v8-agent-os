import assert from "node:assert/strict";

import { isEffectiveContextGovernancePayload } from "../dist/event-normalizer.js";
import { VISIBLE_SESSION_RUNTIME_ORDER } from "../dist/runtime-registry.js";
import { buildRuntimeEpisodeGraph } from "../dist/runtime-episode-graph.js";

const baseTs = Date.parse("2026-05-19T12:00:00.000Z");

assert.ok(!VISIBLE_SESSION_RUNTIME_ORDER.includes("planner_lane"), "planner lane must not be a persistent runtime dock entry");
assert.ok(!VISIBLE_SESSION_RUNTIME_ORDER.includes("engineering_lane"), "legacy engineering lane must not be a persistent runtime dock entry");
assert.ok(!VISIBLE_SESSION_RUNTIME_ORDER.includes("subagent_swarm"), "subagent swarm must be merged into the chat execution map entry");
assert.equal(isEffectiveContextGovernancePayload({ block_count: 3, durable_flush: { reason: "compaction_not_needed" } }), false);
assert.equal(isEffectiveContextGovernancePayload({ recall_audit: { injection_allowed: true } }), true);
assert.equal(isEffectiveContextGovernancePayload({ truncation_risk: true }), true);
assert.equal(isEffectiveContextGovernancePayload({ approvalRequired: true }), true);

function episodeEvent(seq, topic, episode, summary = "") {
  return {
    id: `evt-${seq}`,
    topic,
    summary: summary || episode.reason || topic,
    timestamp: baseTs + seq * 1000,
    data: { episode },
  };
}

function handoffEvent(seq, handoffRef, key = "handoffRef") {
  return {
    id: `evt-${seq}`,
    topic: "handoff.ref.created",
    summary: handoffRef.compactSummary,
    timestamp: baseTs + seq * 1000,
    data: { [key]: handoffRef },
  };
}

const research = {
  episodeId: "episode_research",
  kind: "research",
  state: "queued",
  reason: "查证中国象棋规则和 AI 接口资料",
};
const engineering = {
  episodeId: "episode_engineering",
  kind: "engineering",
  state: "active",
  reason: "实现 Web 项目",
};
const subagent = {
  episodeId: "episode_subagent",
  kind: "delegation",
  state: "active",
  parentEpisodeId: "episode_engineering",
  reason: "审计棋盘和状态管理",
};
const child = {
  episodeId: "episode_child",
  kind: "delegation",
  state: "queued",
  parentEpisodeId: "episode_subagent",
  reason: "并行检查走法生成器",
};

const liveEvents = [
  episodeEvent(1, "capability.need.detected", research),
  episodeEvent(2, "runtime.episode.queued", research),
  episodeEvent(3, "runtime.episode.active", { ...research, state: "active" }),
  handoffEvent(4, {
    handoffRefId: "handoff_research",
    producerEpisodeId: "episode_research",
    kind: "research",
    status: "ready",
    compactSummary: "调研证据包 ready：规则、API、UI 参考已整理。",
  }),
  episodeEvent(5, "runtime.episode.queued", { ...engineering, state: "queued" }),
  episodeEvent(6, "runtime.episode.active", engineering),
  episodeEvent(7, "runtime.episode.active", subagent),
  episodeEvent(8, "delegation.child.requested", child),
  episodeEvent(9, "runtime.episode.queued", child),
  episodeEvent(10, "runtime.episode.active", { ...child, state: "active" }),
  handoffEvent(11, {
    handoffRefId: "handoff_child",
    producerEpisodeId: "episode_child",
    kind: "subagent_result",
    status: "ready",
    compactSummary: "孙 agent 已确认走法生成器边界。",
  }),
  handoffEvent(12, {
    handoffRefId: "handoff_subagent",
    producerEpisodeId: "episode_subagent",
    kind: "subagent_result",
    status: "ready",
    compactSummary: "子 agent 汇总：棋盘状态管理可继续。",
  }),
  handoffEvent(13, {
    handoffRefId: "handoff_engineering",
    producerEpisodeId: "episode_engineering",
    kind: "engineering",
    status: "ready",
    compactSummary: "工程 episode 完成实现和验证。",
  }, "handoff"),
];

const kindLabels = {
  runtime: "运行时",
  research: "调研运行时",
  engineering: "工程运行时",
  delegation: "子代理",
  handoff: "交接",
};

function simplify(nodes) {
  return nodes.map(({ id, parentId, label, subtitle, status, depth }) => ({
    id,
    parentId,
    label,
    subtitle,
    status,
    depth,
  }));
}

const liveGraph = buildRuntimeEpisodeGraph(liveEvents, {
  rootLabel: "Supervisor",
  kindLabels,
});
const historyGraph = buildRuntimeEpisodeGraph([...liveEvents].reverse(), {
  rootLabel: "Supervisor",
  kindLabels,
});

assert.deepEqual(simplify(liveGraph), simplify(historyGraph), "live and history replay graphs must be identical");

const byId = new Map(liveGraph.map((node) => [node.id, node]));
assert.equal(byId.get("episode_research")?.status, "completed");
assert.match(byId.get("episode_research")?.subtitle || "", /调研证据包/);
assert.equal(byId.get("episode_engineering")?.status, "completed");
assert.equal(byId.get("episode_subagent")?.parentId, "episode_engineering");
assert.equal(byId.get("episode_child")?.parentId, "episode_subagent");
assert.equal(byId.get("episode_child")?.depth, 3);
assert.match(byId.get("episode_child")?.subtitle || "", /走法生成器/);
assert.equal(byId.get("handoff:handoff_research")?.parentId, "episode_research");
assert.equal(byId.get("handoff:handoff_research")?.label, "交接");
assert.equal(byId.get("handoff:handoff_engineering")?.parentId, "episode_engineering");

const activeGraph = buildRuntimeEpisodeGraph(liveEvents.slice(0, 10), {
  rootLabel: "Supervisor",
  kindLabels,
});
const activeIds = new Set(activeGraph.filter((node) => node.status === "active").map((node) => node.id));
assert.ok(activeIds.has("episode_engineering"), "engineering edge should be active before handoff");
assert.ok(activeIds.has("episode_subagent"), "subagent edge should be active before handoff");
assert.ok(activeIds.has("episode_child"), "child delegation edge should be active before handoff");

console.log("runtime episode graph fixture verified");
