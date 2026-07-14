import assert from "node:assert/strict";
import {
  buildCollaborationMicroStagesFromMessageBoundNodes,
  buildMessageBoundCollaborationMicroStagePlacement,
  buildMessageBoundExecutionNodes,
  getMessageBoundExecutionTimelineNodeIdentityCandidates,
} from "../dist/message-bound-execution-node.js";

const messages = [
  {
    id: "msg_supervisor_1",
    runId: "run_message_bound",
    timestamp: 100,
    nodes: [
      {
        id: "node_runtime_route",
        kind: "execution",
        executionType: "runtime_progress",
        topic: "runtime.episode.active",
        label: "Engineering Runtime",
        content: "Engineering 正在生成 proof。",
        runtimeId: "engineering",
        data: {
          runId: "run_message_bound",
          episodeId: "ep_engineering",
          kind: "engineering",
          state: "active",
          compactSummary: "生成 proof",
          detailRef: "raw://engineering/proof",
        },
      },
      {
        id: "node_delegation",
        kind: "execution",
        executionType: "tool_result",
        toolCallId: "call_delegate",
        toolName: "delegation_broker",
        topic: "delegation_broker.dispatch",
        content: "派遣两个子代理。",
        data: {
          runId: "run_message_bound",
          dispatchGroup: "dg_quality",
          subagentName: "Quality 子代理",
          taskGoal: "复核交付质量",
        },
      },
    ],
  },
];

const nodes = buildMessageBoundExecutionNodes(messages);
assert.equal(nodes.length, 2);
assert.equal(nodes[0].messageId, "msg_supervisor_1");
assert.equal(nodes[0].kind, "runtime");
assert.equal(nodes[0].episodeId, "ep_engineering");
assert.equal(nodes[0].detailRef, "raw://engineering/proof");
assert.equal(nodes[1].kind, "subagent");
assert.equal(nodes[1].toolCallId, "call_delegate");
assert.equal(nodes[1].dispatchGroup, "dg_quality");

const stages = buildCollaborationMicroStagesFromMessageBoundNodes(nodes, {
  runId: "run_message_bound",
  locale: "zh-CN",
});
assert.equal(stages.length, 2);
assert.ok(stages.some((stage) => stage.kind === "runtime" && stage.runtimeId === "engineering"));
assert.ok(stages.some((stage) => stage.kind === "subagent" && stage.dispatchGroup === "dg_quality"));

const placement = buildMessageBoundCollaborationMicroStagePlacement(nodes, {
  runId: "run_message_bound",
  locale: "zh-CN",
});
assert.ok(placement);
assert.equal(placement.anchorNodeId, "node_runtime_route");
assert.equal(placement.anchorSequence, 0);
assert.deepEqual(placement.sourceNodeIds, ["node_runtime_route", "node_delegation"]);
assert.equal(placement.stages.length, 2);
assert.deepEqual(
  getMessageBoundExecutionTimelineNodeIdentityCandidates(messages[0].nodes[1]),
  ["node_delegation", "call_delegate"],
);

console.log("message-bound execution nodes verified");
