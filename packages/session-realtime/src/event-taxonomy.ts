import type {
  SessionRuntimeEventTarget,
  SessionRuntimeId,
  SessionRuntimeScope,
  SessionRuntimeVisibility,
} from "./contract.js";

export type RuntimeEventTaxonomyEntry = {
  key: string;
  topicPattern?: string;
  eventType?: string;
  eventName?: string;
  runtimeId: SessionRuntimeId;
  scope: SessionRuntimeScope;
  visibility: SessionRuntimeVisibility;
  targets: SessionRuntimeEventTarget[];
  explicit: boolean;
};

export type RuntimeEventMatrixEntry = RuntimeEventTaxonomyEntry;

export const RUNTIME_EVENT_TAXONOMY: RuntimeEventTaxonomyEntry[] = [
  { key: "session.connected", topicPattern: "session.connected", runtimeId: "chat", scope: "active_run", visibility: "excluded", targets: [], explicit: true },
  { key: "session.subscribed", topicPattern: "session.subscribed", runtimeId: "chat", scope: "active_run", visibility: "excluded", targets: [], explicit: true },
  { key: "chat.agent_start", topicPattern: "agent.started", eventType: "agent_start", runtimeId: "chat", scope: "active_run", visibility: "visible", targets: ["runtime_card", "hud"], explicit: true },
  { key: "chat.text_chunk", topicPattern: "run.text.delta", eventType: "text_chunk", runtimeId: "chat", scope: "active_run", visibility: "visible", targets: ["message"], explicit: true },
  { key: "chat.reasoning_chunk", topicPattern: "run.reasoning.delta", eventType: "reasoning_chunk", runtimeId: "chat", scope: "active_run", visibility: "visible", targets: ["message", "runtime_card"], explicit: true },
  { key: "chat.tool_start", topicPattern: "tool.started", eventType: "tool_start", runtimeId: "chat", scope: "active_run", visibility: "visible", targets: ["message", "runtime_card", "process"], explicit: true },
  { key: "chat.tool_result", topicPattern: "tool.finished", eventType: "tool_result", runtimeId: "chat", scope: "active_run", visibility: "visible", targets: ["message", "runtime_card", "process"], explicit: true },
  { key: "chat.ask_user_requested", topicPattern: "ask_user.requested", eventType: "custom_event", eventName: "ask_user", runtimeId: "chat", scope: "active_run", visibility: "visible", targets: ["hud", "runtime_card"], explicit: true },
  { key: "chat.ask_user_resolved", topicPattern: "ask_user.resolved", eventType: "custom_event", eventName: "ask_user", runtimeId: "chat", scope: "active_run", visibility: "hidden", targets: ["hud", "runtime_card"], explicit: true },
  { key: "chat.approval_requested", topicPattern: "approval.requested", eventType: "custom_event", eventName: "approval_requested", runtimeId: "automation", scope: "active_run", visibility: "visible", targets: ["approval", "hud", "runtime_card"], explicit: true },
  { key: "chat.approval_resolved.approved", topicPattern: "approval.approved", eventType: "custom_event", eventName: "approval_resolved", runtimeId: "automation", scope: "active_run", visibility: "hidden", targets: ["approval", "hud", "runtime_card"], explicit: true },
  { key: "chat.approval_resolved.rejected", topicPattern: "approval.rejected", eventType: "custom_event", eventName: "approval_resolved", runtimeId: "automation", scope: "active_run", visibility: "hidden", targets: ["approval", "hud", "runtime_card"], explicit: true },
  { key: "chat.approval_resolved.auto", topicPattern: "approval.auto_approved", eventType: "custom_event", eventName: "approval_resolved", runtimeId: "automation", scope: "active_run", visibility: "hidden", targets: ["approval", "hud", "runtime_card"], explicit: true },
  { key: "chat.artifact_recorded", topicPattern: "artifact.recorded", eventType: "custom_event", eventName: "artifact_recorded", runtimeId: "chat", scope: "active_run", visibility: "visible", targets: ["artifact", "message", "hud"], explicit: true },
  { key: "chat.run_state_changed", topicPattern: "run.state.changed", eventType: "custom_event", eventName: "run_controlled", runtimeId: "chat", scope: "active_run", visibility: "hidden", targets: ["runtime_card", "hud"], explicit: true },
  { key: "chat.run_paused", topicPattern: "run.paused", eventType: "custom_event", eventName: "run_controlled", runtimeId: "chat", scope: "active_run", visibility: "hidden", targets: ["runtime_card", "hud"], explicit: true },
  { key: "chat.run_resumed", topicPattern: "run.resumed", eventType: "custom_event", eventName: "run_controlled", runtimeId: "chat", scope: "active_run", visibility: "hidden", targets: ["runtime_card", "hud"], explicit: true },
  { key: "chat.run_interrupted", topicPattern: "run.interrupted", eventType: "custom_event", eventName: "run_controlled", runtimeId: "chat", scope: "active_run", visibility: "hidden", targets: ["runtime_card", "hud"], explicit: true },
  { key: "chat.run_cancelled", topicPattern: "run.cancelled", eventType: "custom_event", eventName: "run_controlled", runtimeId: "chat", scope: "active_run", visibility: "hidden", targets: ["runtime_card", "hud"], explicit: true },
  { key: "chat.run_retry_requested", topicPattern: "run.retry.requested", eventType: "custom_event", eventName: "run_controlled", runtimeId: "chat", scope: "active_run", visibility: "hidden", targets: ["runtime_card", "hud"], explicit: true },
  { key: "chat.run_controlled", topicPattern: "run.", eventType: "custom_event", eventName: "run_controlled", runtimeId: "chat", scope: "active_run", visibility: "hidden", targets: ["runtime_card", "hud"], explicit: true },
  { key: "chat.lane_queued", topicPattern: "run.lane.queued", eventType: "custom_event", eventName: "lane_updated", runtimeId: "chat", scope: "active_run", visibility: "hidden", targets: ["runtime_card", "hud"], explicit: true },
  { key: "chat.lane_acquired", topicPattern: "run.lane.acquired", eventType: "custom_event", eventName: "lane_updated", runtimeId: "chat", scope: "active_run", visibility: "hidden", targets: ["runtime_card", "hud"], explicit: true },
  { key: "chat.lane_released", topicPattern: "run.lane.released", eventType: "custom_event", eventName: "lane_updated", runtimeId: "chat", scope: "active_run", visibility: "hidden", targets: ["runtime_card", "hud"], explicit: true },
  { key: "chat.lane_rejected", topicPattern: "run.lane.rejected", eventType: "custom_event", eventName: "lane_updated", runtimeId: "chat", scope: "active_run", visibility: "hidden", targets: ["runtime_card", "hud"], explicit: true },
  { key: "chat.safety_preflight_blocked", topicPattern: "safety.preflight.blocked", eventType: "custom_event", eventName: "safety_blocked", runtimeId: "chat", scope: "active_run", visibility: "visible", targets: ["runtime_card", "hud"], explicit: true },
  { key: "chat.context_governance", topicPattern: "context.prepared", eventType: "custom_event", eventName: "context_governance_changed", runtimeId: "chat", scope: "active_run", visibility: "hidden", targets: ["runtime_card", "hud", "context"], explicit: true },
  { key: "chat.done", topicPattern: "run.completed", eventType: "done", runtimeId: "chat", scope: "active_run", visibility: "visible", targets: ["message", "runtime_card", "hud"], explicit: true },
  { key: "chat.error", topicPattern: "run.failed", eventType: "error", runtimeId: "chat", scope: "active_run", visibility: "visible", targets: ["message", "runtime_card", "hud"], explicit: true },
  { key: "chat.lifecycle", topicPattern: "chat.", runtimeId: "chat", scope: "active_run", visibility: "visible", targets: ["message", "runtime_card"], explicit: true },
  { key: "memory.lifecycle", topicPattern: "memory.", runtimeId: "memory", scope: "active_run", visibility: "hidden", targets: ["runtime_card"], explicit: true },
  { key: "automation.lifecycle", topicPattern: "automation.", runtimeId: "automation", scope: "active_run", visibility: "visible", targets: ["runtime_card", "hud", "process"], explicit: true },
  { key: "extensions.lifecycle", topicPattern: "extension.", runtimeId: "extensions", scope: "active_run", visibility: "visible", targets: ["message", "runtime_card", "artifact"], explicit: true },
  { key: "network.lifecycle", topicPattern: "network_supervisor.", runtimeId: "network_supervisor", scope: "active_run", visibility: "visible", targets: ["runtime_card", "process"], explicit: true },
  { key: "plugin.tool.gateway", topicPattern: "gateway.", runtimeId: "plugin_host_tool", scope: "active_run", visibility: "visible", targets: ["runtime_card", "process"], explicit: true },
  { key: "plugin.tool.host", topicPattern: "plugin_tool.", runtimeId: "plugin_host_tool", scope: "active_run", visibility: "visible", targets: ["runtime_card", "process"], explicit: true },
  { key: "plugin.channels", topicPattern: "plugin_host.", runtimeId: "plugin_host_channel", scope: "session", visibility: "history_only", targets: ["history"], explicit: true },
  { key: "channel.lifecycle", topicPattern: "channel.", runtimeId: "plugin_host_channel", scope: "session", visibility: "history_only", targets: ["history"], explicit: true },
  { key: "computer_use.lifecycle", topicPattern: "computer_use.", runtimeId: "computer_use", scope: "active_run", visibility: "visible", targets: ["message", "runtime_card", "terminal", "process"], explicit: true },
  { key: "rpa.lifecycle", topicPattern: "rpa.", runtimeId: "rpa", scope: "active_run", visibility: "visible", targets: ["runtime_card", "process"], explicit: true },
  { key: "desktop_live.lifecycle", topicPattern: "desktop_live.", runtimeId: "desktop_live", scope: "active_run", visibility: "excluded", targets: [], explicit: true },
  { key: "supervisor.graph", topicPattern: "supervisor.graph.", runtimeId: "chat", scope: "active_run", visibility: "hidden", targets: ["runtime_card"], explicit: true },
];

function topicMatches(entryTopicPattern: string | undefined, normalizedTopic: string) {
  if (!entryTopicPattern) {
    return true;
  }
  return entryTopicPattern.endsWith(".")
    ? normalizedTopic.startsWith(entryTopicPattern)
    : normalizedTopic === entryTopicPattern;
}

export function findRuntimeEventTaxonomyEntry(input?: {
  topic?: string | null;
  type?: string | null;
  name?: string | null;
}) {
  const normalizedTopic = String(input?.topic || "").trim().toLowerCase();
  const normalizedType = String(input?.type || "").trim().toLowerCase();
  const normalizedName = String(input?.name || "").trim().toLowerCase();
  if (!normalizedTopic && !normalizedType && !normalizedName) {
    return null;
  }
  return (
    RUNTIME_EVENT_TAXONOMY.find((entry) => {
      if (entry.eventType && normalizedType !== entry.eventType) {
        return false;
      }
      if (entry.eventName && normalizedName !== entry.eventName) {
        return false;
      }
      return topicMatches(entry.topicPattern, normalizedTopic);
    }) || null
  );
}

export function findRuntimeEventTaxonomyEntryByTopic(topic?: string | null) {
  const normalizedTopic = String(topic || "").trim().toLowerCase();
  if (!normalizedTopic) {
    return null;
  }
  return (
    RUNTIME_EVENT_TAXONOMY.find((entry) => topicMatches(entry.topicPattern, normalizedTopic)) || null
  );
}
