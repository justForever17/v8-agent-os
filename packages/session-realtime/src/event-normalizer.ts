import type {
  NormalizedSessionRuntimeEvent,
  SessionRuntimeEventTarget,
  SessionRuntimeEventSource,
  SessionRuntimeId,
  SessionRuntimeScope,
  SessionRuntimeVisibility,
} from "./contract.js";
import { findRuntimeEventTaxonomyEntry } from "./event-taxonomy.js";
import { getRuntimeRegistryEntry, normalizeRuntimeId } from "./runtime-registry.js";

type JsonRecord = Record<string, unknown>;
type RuntimeEnvelope = JsonRecord & {
  kind?: string;
  topic?: string;
  payload?: unknown;
  source?: unknown;
};

export type NormalizeRuntimeEventOptions = {
  locale?: "zh-CN" | "en";
};

function isEnglish(locale: NormalizeRuntimeEventOptions["locale"]) {
  return locale === "en";
}

function tr(locale: NormalizeRuntimeEventOptions["locale"], zh: string, en: string) {
  return isEnglish(locale) ? en : zh;
}

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" ? (value as JsonRecord) : {};
}

const TODO_TOOL_NAMES = new Set(["write_todos", "update_todo"]);
const TODO_MUTATION_PATTERNS = [
  /command\s*\(\s*update\s*=\s*\{[^)]*todos/i,
  /\bpersistent task plan\b/i,
  /\btodo\s*#?\d+\b.*\b(marked|updated|done|in_progress|pending|skipped|created)\b/i,
  /\bcreated with\s+\d+\s+items\b/i,
];

function normalizeToolName(value: unknown) {
  return normalizeString(value);
}

function resolveToolName(value: unknown): string {
  const payload = asRecord(value);
  const nestedTool = asRecord(payload.tool);
  return normalizeToolName(
    payload.toolName
    || payload.tool_name
    || nestedTool.toolName
    || nestedTool.tool_name
    || payload.name,
  );
}

function resolveArtifactMetadata(value: unknown) {
  const payload = asRecord(value);
  const artifact = asRecord(payload.artifact);
  return asRecord(
    artifact.metadata
    || payload.metadata
    || payload.artifactMetadata
    || payload.artifact_metadata,
  );
}

function stringLooksLikeTodoMutation(value: unknown) {
  const normalized = String(value || "").trim();
  if (!normalized) {
    return false;
  }
  return TODO_MUTATION_PATTERNS.some((pattern) => pattern.test(normalized));
}

function containsTodoMutationHint(value: unknown, depth = 0): boolean {
  if (depth > 4 || value === null || value === undefined) {
    return false;
  }

  if (typeof value === "string") {
    return stringLooksLikeTodoMutation(value);
  }

  if (Array.isArray(value)) {
    return value.some((item) => containsTodoMutationHint(item, depth + 1));
  }

  if (typeof value !== "object") {
    return false;
  }

  const record = asRecord(value);
  if (isTodoToolName(resolveToolName(record))) {
    return true;
  }

  if (Array.isArray(record.todos) || typeof record.todos === "object" && record.todos !== null) {
    return true;
  }

  const update = asRecord(record.update);
  if (Array.isArray(update.todos) || typeof update.todos === "object" && update.todos !== null || "todo" in update) {
    return true;
  }

  const request = asRecord(record.request);
  if (Array.isArray(request.todos) || typeof request.todos === "object" && request.todos !== null || "todo" in request) {
    return true;
  }

  return Object.values(record).some((nested) => containsTodoMutationHint(nested, depth + 1));
}

export function isTodoToolName(value: unknown) {
  return TODO_TOOL_NAMES.has(normalizeToolName(value));
}

export function isTodoToolRuntimePayload(value: unknown) {
  return isTodoToolName(resolveToolName(value)) || containsTodoMutationHint(value);
}

export function isSurfaceVisibleArtifactPayload(value: unknown) {
  const metadata = resolveArtifactMetadata(value);
  if (typeof metadata.surfaceVisible === "boolean") {
    return metadata.surfaceVisible;
  }
  if (typeof metadata.surface_visible === "boolean") {
    return metadata.surface_visible;
  }
  return true;
}

function applySurfaceVisibilityOverrides(
  event: NormalizedSessionRuntimeEvent,
  payload: JsonRecord,
): NormalizedSessionRuntimeEvent {
  if ((event.type === "tool_start" || event.type === "tool_result") && isTodoToolRuntimePayload(payload)) {
    return {
      ...event,
      visibility: "hidden",
      targets: ["todos_hud", "runtime_timeline"],
    };
  }

  if (event.type === "custom_event" && event.name === "artifact_recorded" && !isSurfaceVisibleArtifactPayload(payload)) {
    return {
      ...event,
      visibility: "excluded",
      targets: ["artifact"],
    };
  }

  return event;
}

function withTranscriptTargetFields(
  event: NormalizedSessionRuntimeEvent,
  payload: JsonRecord,
): NormalizedSessionRuntimeEvent {
  const messageId = typeof payload.message_id === "string"
    ? payload.message_id
    : typeof payload.messageId === "string"
      ? payload.messageId
      : undefined;
  const nodeId = typeof payload.node_id === "string"
    ? payload.node_id
    : typeof payload.nodeId === "string"
      ? payload.nodeId
      : undefined;
  const transcriptVersionRaw = payload.transcript_version ?? payload.transcriptVersion;
  const transcriptVersion = typeof transcriptVersionRaw === "number"
    ? transcriptVersionRaw
    : typeof transcriptVersionRaw === "string" && transcriptVersionRaw.trim()
      ? Number.parseInt(transcriptVersionRaw, 10)
      : undefined;

  return applyOwnerFields({
    ...event,
    message_id: messageId || event.message_id,
    node_id: nodeId || event.node_id,
    transcript_version: Number.isFinite(transcriptVersion || NaN)
      ? transcriptVersion
      : event.transcript_version,
  }, payload);
}

function withEnvelopeFields(event: NormalizedSessionRuntimeEvent, envelope: RuntimeEnvelope): NormalizedSessionRuntimeEvent {
  const source = resolveSource(envelope, event.data);
  return {
    ...event,
    seq: typeof envelope.seq === "number" ? envelope.seq : event.seq,
    session_id: typeof envelope.session_id === "string" ? envelope.session_id : event.session_id,
    conversation_id: typeof envelope.conversation_id === "string" ? envelope.conversation_id : event.conversation_id,
    run_id: typeof envelope.run_id === "string" ? envelope.run_id : event.run_id,
    event_id: typeof envelope.event_id === "string" ? envelope.event_id : event.event_id,
    ts: typeof envelope.ts === "string" ? envelope.ts : event.ts,
    topic: typeof envelope.topic === "string" ? envelope.topic : event.topic,
    source: source || event.source,
    raw: asRecord(envelope),
  };
}

function normalizeString(value: unknown) {
  return String(value || "").trim().toLowerCase().replace(/[^a-z0-9._:-]+/g, "_");
}

function normalizeTargets(value: unknown): SessionRuntimeEventTarget[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const allowed = new Set<SessionRuntimeEventTarget>([
    "message",
    "runtime_card",
    "runtime_timeline",
    "hud",
    "todos_hud",
    "approval",
    "artifact",
    "terminal",
    "process",
    "context",
    "history",
  ]);
  const normalized = value
    .map((item) => String(item || "").trim() as SessionRuntimeEventTarget)
    .filter((item) => allowed.has(item));
  return normalized.length ? normalized : null;
}

function resolveExplicitTargets(payload: JsonRecord, fallback?: unknown): SessionRuntimeEventTarget[] | null {
  return normalizeTargets(payload.targets)
    || normalizeTargets(payload.surfaceTargets)
    || normalizeTargets(payload.surface_targets)
    || normalizeTargets(fallback);
}

function resolveExplicitRuntimeId(payload: JsonRecord): SessionRuntimeId | null {
  return normalizeRuntimeId(
    typeof payload.runtimeId === "string"
      ? payload.runtimeId
      : typeof payload.runtime_id === "string"
        ? payload.runtime_id
        : typeof payload.ownerRuntimeId === "string"
          ? payload.ownerRuntimeId
          : typeof payload.owner_runtime_id === "string"
            ? payload.owner_runtime_id
            : typeof payload.runtime === "string"
              ? payload.runtime
              : typeof payload.runtimeKind === "string"
                ? payload.runtimeKind
                : typeof payload.runtime_kind === "string"
                  ? payload.runtime_kind
                  : "",
  );
}

function applyOwnerFields(
  event: NormalizedSessionRuntimeEvent,
  payload: JsonRecord,
): NormalizedSessionRuntimeEvent {
  const ownerRuntimeId = resolveExplicitRuntimeId({
    runtimeId: payload.ownerRuntimeId,
    runtime_id: payload.owner_runtime_id,
  });
  const surfaceTargets = resolveExplicitTargets({
    targets: payload.surfaceTargets,
    surface_targets: payload.surface_targets,
  });
  const nextData = asRecord(event.data);
  const traceGroupId =
    (typeof payload.traceGroupId === "string" && payload.traceGroupId)
    || (typeof payload.trace_group_id === "string" && payload.trace_group_id)
    || event.traceGroupId;
  return {
    ...event,
    ownerRuntimeId: ownerRuntimeId || event.ownerRuntimeId,
    ownerAgentKind:
      (typeof payload.ownerAgentKind === "string" && payload.ownerAgentKind)
      || (typeof payload.owner_agent_kind === "string" && payload.owner_agent_kind)
      || event.ownerAgentKind,
    ownerAgentId:
      (typeof payload.ownerAgentId === "string" && payload.ownerAgentId)
      || (typeof payload.owner_agent_id === "string" && payload.owner_agent_id)
      || event.ownerAgentId,
    ownerStreamKey:
      (typeof payload.ownerStreamKey === "string" && payload.ownerStreamKey)
      || (typeof payload.owner_stream_key === "string" && payload.owner_stream_key)
      || event.ownerStreamKey,
    traceGroupId,
    surfaceTargets: surfaceTargets || event.surfaceTargets,
    data: {
      ...nextData,
      ownerRuntimeId: ownerRuntimeId || event.ownerRuntimeId || nextData.ownerRuntimeId,
      ownerAgentKind:
        (typeof payload.ownerAgentKind === "string" && payload.ownerAgentKind)
        || (typeof payload.owner_agent_kind === "string" && payload.owner_agent_kind)
        || event.ownerAgentKind
        || nextData.ownerAgentKind,
      ownerAgentId:
        (typeof payload.ownerAgentId === "string" && payload.ownerAgentId)
        || (typeof payload.owner_agent_id === "string" && payload.owner_agent_id)
        || event.ownerAgentId
        || nextData.ownerAgentId,
      ownerStreamKey:
        (typeof payload.ownerStreamKey === "string" && payload.ownerStreamKey)
        || (typeof payload.owner_stream_key === "string" && payload.owner_stream_key)
        || event.ownerStreamKey
        || nextData.ownerStreamKey,
      traceGroupId: traceGroupId || nextData.traceGroupId,
      surfaceTargets: surfaceTargets || event.surfaceTargets || nextData.surfaceTargets,
    },
  };
}

function resolveSource(envelope: RuntimeEnvelope, payload?: Record<string, unknown> | null): SessionRuntimeEventSource | undefined {
  const rawSource = asRecord(envelope.source);
  const payloadSource = asRecord(payload?.source);
  const source: SessionRuntimeEventSource = {
    plane: typeof rawSource.plane === "string"
      ? rawSource.plane
      : typeof payloadSource.plane === "string"
        ? payloadSource.plane
        : undefined,
    component: typeof rawSource.component === "string"
      ? rawSource.component
      : typeof payloadSource.component === "string"
        ? payloadSource.component
        : typeof payload?.source_component === "string"
          ? payload.source_component
          : typeof payload?.sourceComponent === "string"
            ? payload.sourceComponent
            : undefined,
    node: typeof rawSource.node === "string"
      ? rawSource.node
      : typeof payloadSource.node === "string"
        ? payloadSource.node
        : typeof payload?.source_node === "string"
          ? payload.source_node
          : typeof payload?.sourceNode === "string"
            ? payload.sourceNode
            : undefined,
    agent_id: typeof rawSource.agent_id === "string"
      ? rawSource.agent_id
      : typeof payloadSource.agent_id === "string"
        ? payloadSource.agent_id
        : typeof payload?.agent_id === "string"
          ? payload.agent_id
          : typeof payload?.agentId === "string"
            ? payload.agentId
            : undefined,
  };

  if (!source.plane && !source.component && !source.node && !source.agent_id) {
    return undefined;
  }
  return source;
}

function resolveChannelType(payload: JsonRecord, source?: SessionRuntimeEventSource) {
  return normalizeString(
    payload.channel_type
    || payload.channelType
    || payload.channel_name
    || payload.channelName
    || payload.transport_managed_by
    || payload.transportManagedBy
    || payload.handoff_source
    || payload.handoffSource
    || source?.node
    || source?.component,
  );
}

function resolveRuntimeKind(payload: JsonRecord) {
  const episode = asRecord(payload.episode);
  const need = asRecord(payload.need);
  const handoffRef = asRecord(payload.handoffRef);
  const handoff = asRecord(payload.handoff);
  const childDelegation = asRecord(payload.childDelegation);
  const tool = asRecord(payload.tool);
  return normalizeString(
    payload.runtimeId
    || payload.runtime_id
    || payload.ownerRuntimeId
    || payload.owner_runtime_id
    || payload.runtime
    || payload.runtimeKind
    || payload.runtime_kind
    || payload.runtimeFamily
    || payload.runtime_family
    || episode.runtimeId
    || episode.runtime_id
    || episode.runtimeKind
    || episode.runtime_kind
    || episode.kind
    || need.runtimeId
    || need.runtime_id
    || need.runtimeKind
    || need.runtime_kind
    || need.kind
    || handoffRef.runtimeId
    || handoffRef.runtime_id
    || handoffRef.runtimeKind
    || handoffRef.runtime_kind
    || handoffRef.kind
    || handoff.runtimeId
    || handoff.runtime_id
    || handoff.runtimeKind
    || handoff.runtime_kind
    || handoff.kind
    || childDelegation.runtimeId
    || childDelegation.runtime_id
    || childDelegation.runtimeKind
    || childDelegation.runtime_kind
    || childDelegation.kind
    || tool.runtimeId
    || tool.runtime_id
    || tool.runtimeKind
    || tool.runtime_kind
    || payload.kind
    || payload.family,
  );
}

function normalizeArtifactRuntimeId(hint: string): SessionRuntimeId | null {
  const normalized = normalizeString(hint);
  if (!normalized) {
    return null;
  }
  if (normalized.includes("research_evidence") || normalized.includes("source_matrix")) {
    return "research";
  }
  if (normalized.includes("engineering_patch") || normalized.includes("verification_report") || normalized.includes("patch_bundle")) {
    return "engineering";
  }
  if (normalized.includes("asset_bundle") || normalized.includes("media_asset") || normalized.includes("recipe_ref")) {
    return "creative_media";
  }
  if (normalized.includes("computer_observation") || normalized.includes("observation_bundle") || normalized.includes("screenshot_ref")) {
    return "computer_use";
  }
  if (normalized.includes("rpa_trace") || normalized.includes("trace_bundle")) {
    return "rpa";
  }
  if (normalized.includes("subagent_result") || normalized.includes("delegation")) {
    return "subagent_swarm";
  }
  return normalizeRuntimeId(normalized);
}

function resolveSourceHints(source?: SessionRuntimeEventSource) {
  return {
    component: normalizeString(source?.component),
    node: normalizeString(source?.node),
    agentId: normalizeString(source?.agent_id),
  };
}

function resolvePluginHostRuntimeId(
  topic: string,
  payload: JsonRecord,
  source?: SessionRuntimeEventSource,
): SessionRuntimeId | null {
  const normalizedTopic = normalizeString(topic);
  const runtimeKind = resolveRuntimeKind(payload);
  const channelType = resolveChannelType(payload, source);
  const { component, node, agentId } = resolveSourceHints(source);
  const joinedHints = [component, node, agentId, runtimeKind, channelType, normalizedTopic].filter(Boolean).join("¦");

  const isPluginHostRuntime = joinedHints.includes("plugin_host");
  if (!isPluginHostRuntime) {
    return null;
  }

  if (
    normalizedTopic.startsWith("gateway.")
    || normalizedTopic.startsWith("plugin_tool.")
    || runtimeKind.includes("plugin_host_tool")
    || runtimeKind.includes("gateway")
    || runtimeKind.includes("plugin_tool")
    || channelType.includes("tool")
    || channelType.includes("gateway")
  ) {
    return "plugin_host_tool";
  }

  if (
    normalizedTopic.startsWith("plugin_host.")
    || normalizedTopic.startsWith("channel.")
    || runtimeKind.includes("plugin_host_channel")
    || runtimeKind.includes("channel")
    || channelType.includes("channel")
    || channelType.includes("openclaw")
    || channelType.includes("wechat")
    || channelType.includes("feishu")
    || channelType.includes("telegram")
    || channelType.includes("discord")
    || joinedHints.includes("inbound")
    || joinedHints.includes("push")
    || joinedHints.includes("dispatch")
    || joinedHints.includes("delivery")
  ) {
    return "plugin_host_channel";
  }

  return "plugin_host_tool";
}

function resolveRuntimeId(topic: string, payload: JsonRecord, source?: SessionRuntimeEventSource): SessionRuntimeId | null {
  const pluginHostRuntimeId = resolvePluginHostRuntimeId(topic, payload, source);
  if (pluginHostRuntimeId) {
    return pluginHostRuntimeId;
  }

  const nestedRuntimeId = normalizeArtifactRuntimeId(resolveRuntimeKind(payload));
  if (nestedRuntimeId) {
    return nestedRuntimeId;
  }

  const hints = [
    resolveRuntimeKind(payload),
    normalizeString(source?.component),
    normalizeString(source?.node),
    normalizeString(source?.agent_id),
    normalizeString(payload.source_component),
    normalizeString(payload.sourceComponent),
    normalizeString(payload.source_node),
    normalizeString(payload.sourceNode),
    normalizeString(payload.channel_type),
    normalizeString(payload.channelType),
    normalizeString(payload.transport),
    normalizeString(topic),
    normalizeString(payload.name),
    normalizeString(payload.type),
  ];

  for (const hint of hints) {
    const runtimeId = normalizeRuntimeId(hint);
    if (runtimeId) {
      return runtimeId;
    }
  }

  return null;
}

function resolveMatrixEntry(topic: string, payload: JsonRecord) {
  return findRuntimeEventTaxonomyEntry({
    topic,
    type: typeof payload.type === "string" ? payload.type : "",
    name: typeof payload.name === "string" ? payload.name : "",
  });
}

function resolveScope(topic: string, payload: JsonRecord): SessionRuntimeScope {
  return resolveMatrixEntry(topic, payload)?.scope || "active_run";
}

function resolveVisibility(topic: string, payload: JsonRecord): SessionRuntimeVisibility {
  return resolveMatrixEntry(topic, payload)?.visibility || "visible";
}

function resolveTargets(topic: string, payload: JsonRecord) {
  return resolveMatrixEntry(topic, payload)?.targets || ["runtime_card"];
}

function normalizeTypedEventType(type: string) {
  if (type === "tool_call") {
    return "tool_start";
  }
  return type;
}

function resolveTypedEventRuntimeId(type: string, name: string, payload: JsonRecord): SessionRuntimeId | null {
  const normalizedType = normalizeTypedEventType(type);
  if (normalizedType === "agent_start" || normalizedType === "text_chunk" || normalizedType === "reasoning_chunk" || normalizedType === "tool_start" || normalizedType === "tool_result" || normalizedType === "done" || normalizedType === "error") {
    return "chat";
  }

  if (normalizedType !== "custom_event") {
    return null;
  }

  if (name === "ask_user") {
    return "chat";
  }
  if (name === "approval_requested" || name === "approval_resolved") {
    return "automation";
  }
  if (name === "artifact_recorded" || name === "runtime_progress" || name === "runtime_event" || name === "run_controlled" || name === "safety_blocked" || name === "lane_updated" || name === "context_governance_changed") {
    return "chat";
  }
  if (name === "human_guidance") {
    return "chat";
  }
  return null;
}

function resolveTypedEventTargets(type: string, name: string): SessionRuntimeEventTarget[] | null {
  const normalizedType = normalizeTypedEventType(type);
  if (normalizedType === "text_chunk") {
    return ["message"];
  }
  if (normalizedType === "reasoning_chunk") {
    return ["message", "runtime_card"];
  }
  if (normalizedType === "agent_start") {
    return ["runtime_card", "hud"];
  }
  if (normalizedType === "tool_start" || normalizedType === "tool_result") {
    return ["message", "process"];
  }
  if (normalizedType === "done" || normalizedType === "error") {
    return ["message", "runtime_card", "hud"];
  }
  if (normalizedType !== "custom_event") {
    return null;
  }
  if (name === "ask_user") {
    return ["hud", "runtime_card"];
  }
  if (name === "approval_requested") {
    return ["approval", "hud", "runtime_card"];
  }
  if (name === "approval_resolved") {
    return ["approval", "hud", "runtime_card"];
  }
  if (name === "artifact_recorded") {
    return ["artifact", "message", "hud"];
  }
  if (name === "runtime_progress" || name === "runtime_event") {
    return ["runtime_card", "hud", "process", "terminal"];
  }
  if (name === "run_controlled" || name === "lane_updated") {
    return ["runtime_card", "hud"];
  }
  if (name === "human_guidance") {
    return ["runtime_card", "hud"];
  }
  if (name === "safety_blocked") {
    return ["runtime_card", "hud"];
  }
  if (name === "context_governance_changed") {
    return ["runtime_card", "hud", "context"];
  }
  return null;
}

function resolveTypedEventVisibility(type: string, name: string): SessionRuntimeVisibility | null {
  const normalizedType = normalizeTypedEventType(type);
  if (normalizedType === "agent_start" || normalizedType === "text_chunk" || normalizedType === "reasoning_chunk" || normalizedType === "tool_start" || normalizedType === "tool_result" || normalizedType === "done" || normalizedType === "error") {
    return "visible";
  }
  if (normalizedType !== "custom_event") {
    return null;
  }
  if (name === "approval_resolved" || name === "run_controlled" || name === "lane_updated" || name === "context_governance_changed") {
    return "hidden";
  }
  return "visible";
}

export function isRuntimeEventVisibleInRealtimeSurface(
  event: Pick<NormalizedSessionRuntimeEvent, "visibility"> | SessionRuntimeVisibility | null | undefined,
) {
  const visibility = typeof event === "string" ? event : event?.visibility;
  return visibility === "visible" || visibility === "hidden";
}

export function isRuntimeEventHistoryOnly(
  event: Pick<NormalizedSessionRuntimeEvent, "visibility"> | SessionRuntimeVisibility | null | undefined,
) {
  const visibility = typeof event === "string" ? event : event?.visibility;
  return visibility === "history_only";
}

export function isRuntimeEventExcludedFromRealtimeSurface(
  event: Pick<NormalizedSessionRuntimeEvent, "visibility"> | SessionRuntimeVisibility | null | undefined,
) {
  const visibility = typeof event === "string" ? event : event?.visibility;
  return visibility === "excluded";
}

function toFiniteNumber(value: unknown) {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function readContextGovernanceData(value: unknown): JsonRecord {
  const record = asRecord(value);
  const nestedPayload = asRecord(record.payload);
  return Object.keys(nestedPayload).length > 0 ? nestedPayload : record;
}

function hasContextGovernanceRecallSignal(value: unknown) {
  const recall = asRecord(value);
  return Boolean(recall.injection_allowed || recall.injectionAllowed);
}

function hasTruthyField(payload: JsonRecord, keys: string[]) {
  return keys.some((key) => Boolean(payload[key]));
}

function hasPositiveNumberField(payload: JsonRecord, keys: string[]) {
  return keys.some((key) => toFiniteNumber(payload[key]) > 0);
}

export function isEffectiveContextGovernancePayload(value: unknown) {
  const payload = readContextGovernanceData(value);
  const durableFlush = asRecord(payload.durable_flush || payload.durableFlush);
  const durableReason = normalizeString(durableFlush.reason || durableFlush.status);
  const durableSkipReasons = new Set(["", "compaction_not_needed", "none", "prepared", "context_prepared", "skipped", "unchanged"]);

  if (Boolean(payload.compaction_applied || payload.compactionApplied)) {
    return true;
  }
  if (toFiniteNumber(payload.estimated_saved_tokens || payload.estimatedSavedTokens) > 0) {
    return true;
  }
  if (hasTruthyField(payload, [
    "approval_required",
    "approvalRequired",
    "pendingApproval",
    "requiresApproval",
    "approvalRequested",
    "approval_request",
  ])) {
    return true;
  }
  if (hasTruthyField(payload, [
    "truncation_risk",
    "truncationRisk",
    "truncated",
    "context_truncated",
    "contextTruncated",
    "budget_exceeded",
    "budgetExceeded",
    "overflow",
  ])) {
    return true;
  }
  if (hasPositiveNumberField(payload, [
    "truncated_tokens",
    "truncatedTokens",
    "overflow_tokens",
    "overflowTokens",
    "tokens_over_budget",
    "tokensOverBudget",
  ])) {
    return true;
  }
  if (!durableSkipReasons.has(durableReason) && /compact|flush|truncate|approval|recall|inject|budget|overflow/.test(durableReason)) {
    return true;
  }
  return hasContextGovernanceRecallSignal(payload.recall_audit || payload.recallAudit);
}

export function shouldForwardRuntimeEventToRealtimeSurface(
  event: (Pick<NormalizedSessionRuntimeEvent, "visibility" | "name" | "topic" | "data"> & Record<string, unknown>) | SessionRuntimeVisibility | null | undefined,
) {
  if (event && typeof event === "object") {
    const name = normalizeString(event.name);
    const topic = normalizeString(event.topic);
    if (name === "context_governance_changed" || topic === "context.prepared") {
      return isRuntimeEventVisibleInRealtimeSurface(event) && isEffectiveContextGovernancePayload(event.data || event);
    }
  }
  return isRuntimeEventVisibleInRealtimeSurface(event);
}

function resolveActorLabel(runtimeId: SessionRuntimeId | null, locale: NormalizeRuntimeEventOptions["locale"]) {
  if (!runtimeId) return undefined;
  return getRuntimeRegistryEntry(runtimeId, locale).label;
}

function buildProgressLabel(topic: string, payload: JsonRecord, locale: NormalizeRuntimeEventOptions["locale"]) {
  if (typeof payload.label === "string" && payload.label.trim()) {
    return payload.label.trim();
  }
  if (typeof payload.summary === "string" && payload.summary.trim()) {
    return payload.summary.trim();
  }
  if (typeof payload.message === "string" && payload.message.trim()) {
    return payload.message.trim();
  }

  const action = String(payload.action || payload.actionType || payload.appName || payload.expectedTitle || "").trim();
  const index = typeof payload.index === "number" ? payload.index : undefined;
  const runtimeId = resolveRuntimeId(topic, payload);
  const runtimeLabel = runtimeId ? getRuntimeRegistryEntry(runtimeId, locale).shortLabel : tr(locale, "运行", "Runtime");

  if (topic === "computer_use.step.started") {
    return isEnglish(locale)
      ? `${runtimeLabel} step ${index ?? 0} started: ${action || "Running"}`
      : `${runtimeLabel} 第 ${index ?? 0} 步开始：${action || "处理中"}`;
  }
  if (topic === "computer_use.step.completed") {
    return isEnglish(locale)
      ? `${runtimeLabel} step ${index ?? 0} completed: ${action || "Done"}`
      : `${runtimeLabel} 第 ${index ?? 0} 步完成：${action || "已完成"}`;
  }
  if (topic === "computer_use.step.failed") {
    return isEnglish(locale)
      ? `${runtimeLabel} step ${index ?? 0} failed: ${action || "Failed"}`
      : `${runtimeLabel} 第 ${index ?? 0} 步失败：${action || "执行失败"}`;
  }
  if (topic === "approval.requested") {
    return typeof payload.question === "string" && payload.question.trim()
      ? payload.question.trim()
      : tr(locale, "等待用户确认", "Waiting for approval");
  }
  if (topic === "ask_user.requested") {
    return typeof payload.question === "string" && payload.question.trim()
      ? payload.question.trim()
      : tr(locale, "等待你的输入", "Waiting for your answer");
  }
  if (topic === "ask_user.resolved") {
    return tr(locale, "已收到你的输入，继续执行", "Answer received, continuing");
  }
  if (topic === "approval.approved") {
    return tr(locale, "审批已通过，继续执行", "Approval granted, continuing");
  }
  if (topic === "approval.rejected") {
    return tr(locale, "审批被拒绝，等待进一步处理", "Approval rejected, waiting for next action");
  }
  if (topic === "approval.auto_approved") {
    return tr(locale, "审批已自动放行", "Approval auto-approved");
  }
  if (topic === "artifact.recorded") {
    return String(payload.title || payload.kind || payload.workspacePath || tr(locale, "记录新的产物", "Recorded a new artifact"));
  }
  if (topic === "engineering_lane.trigger.decided") {
    const matched = Boolean(payload.matched);
    const active = Boolean(payload.active);
    const mode = String(payload.mode || "").trim();
    const workspaceMode = String(payload.workspaceMode || payload.workspace_mode || "").trim();
    const reason = pickFirstString(payload.reason, payload.triggerReason);
    if (active && workspaceMode === "project_creation_workspace") {
      return tr(locale, "Engineering 已进入项目创建工作区模式", "Engineering entered project-creation workspace mode");
    }
    if (active) {
      return mode === "force"
        ? tr(locale, "Engineering 已按用户要求强制进入主链", "Engineering was forced into the main lane by user request")
        : tr(locale, "Engineering 已进入主链", "Engineering entered the main lane");
    }
    if (matched) {
      return reason
        ? tr(locale, `Engineering 已匹配但暂未进入主链：${reason}`, `Engineering matched but is not active: ${reason}`)
        : tr(locale, "Engineering 已匹配但暂未进入主链", "Engineering matched but is not active");
    }
    return reason
      ? tr(locale, `Engineering 未命中：${reason}`, `Engineering did not match: ${reason}`)
      : tr(locale, "Engineering 未命中", "Engineering did not match");
  }
  if (topic === "run.state.changed") {
    const fromStatus = normalizeDisplayStatus(payload.from_status) || normalizeDisplayStatus(payload.fromStatus);
    const toStatus = normalizeDisplayStatus(payload.to_status) || normalizeDisplayStatus(payload.toStatus) || normalizeDisplayStatus(payload.status);
    const reason = pickFirstString(payload.reason);
    const statusPart = fromStatus && toStatus
      ? tr(locale, `运行状态：${fromStatus} -> ${toStatus}`, `Run state: ${fromStatus} -> ${toStatus}`)
      : toStatus
        ? tr(locale, `运行状态更新为 ${toStatus}`, `Run state changed to ${toStatus}`)
        : tr(locale, "运行状态已更新", "Run state updated");
    return reason ? `${statusPart} (${reason})` : statusPart;
  }
  if (topic === "run.paused") {
    return tr(locale, "运行已暂停", "Run paused");
  }
  if (topic === "run.resumed") {
    return tr(locale, "运行已恢复", "Run resumed");
  }
  if (topic === "run.interrupted") {
    return tr(locale, "运行已中断", "Run interrupted");
  }
  if (topic === "run.cancelled") {
    return tr(locale, "运行已取消", "Run cancelled");
  }
  if (topic === "run.retry.requested") {
    return tr(locale, "已请求重试当前运行", "Retry requested for current run");
  }
  if (topic === "run.lane.queued") {
    return tr(locale, "当前会话正在排队等待执行", "Session queued for execution");
  }
  if (topic === "run.lane.acquired") {
    return tr(locale, "已获得当前会话执行权", "Session execution lane acquired");
  }
  if (topic === "run.lane.released") {
    return tr(locale, "已释放当前会话执行权", "Session execution lane released");
  }
  if (topic === "run.lane.rejected") {
    return tr(locale, "当前会话忙碌，本次请求未进入执行", "Session busy, request was rejected");
  }
  if (topic === "safety.preflight.blocked") {
    return pickFirstString(payload.message, payload.summary, payload.reason)
      || tr(locale, "安全预检阻止了本次执行", "Safety preflight blocked this execution");
  }
  if (topic === "context.prepared") {
    const savedTokens = Number(payload.estimated_saved_tokens || 0) || 0;
    const blockCount = Number(payload.block_count || 0) || 0;
    if (savedTokens > 0 || blockCount > 0) {
      return tr(
        locale,
        `上下文治理已更新：压缩 ${blockCount} 个块，节省约 ${savedTokens} tokens`,
        `Context governance updated: compacted ${blockCount} blocks and saved about ${savedTokens} tokens`,
      );
    }
    return tr(locale, "上下文治理已更新", "Context governance updated");
  }

  return topic;
}

function pickFirstString(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return undefined;
}

function normalizeDisplayStatus(value: unknown) {
  const normalized = pickFirstString(value);
  if (!normalized) {
    return undefined;
  }
  return normalized.toLowerCase() === "unknown" ? undefined : normalized;
}

function extractMcpAppRef(...values: unknown[]) {
  for (const value of values) {
    const record = asRecord(value);
    const appInstanceId = pickFirstString(record.appInstanceId, record.app_instance_id);
    const resourceUri = pickFirstString(record.resourceUri, record.resource_uri);
    if (!appInstanceId || !resourceUri) {
      continue;
    }
    return {
      appInstanceId,
      serverName: pickFirstString(record.serverName, record.server_name),
      resourceUri,
      toolInvocationId: pickFirstString(record.toolInvocationId, record.tool_invocation_id),
      initialToolResultRef: pickFirstString(record.initialToolResultRef, record.initial_tool_result_ref) || null,
      csp: asRecord(record.csp),
      permissions: asRecord(record.permissions),
      status: pickFirstString(record.status),
    };
  }
  return undefined;
}

function extractToolPayload(payload: JsonRecord) {
  const nestedTool = asRecord(payload.tool);
  const payloadMcpApp = extractMcpAppRef(payload.mcpApp, payload.mcp_app, nestedTool.mcpApp, nestedTool.mcp_app);
  const toolCallId = pickFirstString(
    payload.toolCallId,
    payload.tool_call_id,
    nestedTool.toolCallId,
    nestedTool.tool_call_id,
  );
  const toolInvocationId = pickFirstString(
    payload.toolInvocationId,
    payload.tool_invocation_id,
    payload.canonicalToolCallId,
    payload.canonical_tool_call_id,
    nestedTool.toolInvocationId,
    nestedTool.tool_invocation_id,
    nestedTool.canonicalToolCallId,
    nestedTool.canonical_tool_call_id,
    toolCallId,
  );
  const toolName = pickFirstString(
    payload.toolName,
    payload.tool_name,
    payload.name,
    nestedTool.toolName,
    nestedTool.tool_name,
  );
  const args = payload.args ?? payload.request ?? nestedTool.args ?? nestedTool.request;
  const result = payload.result ?? payload.response ?? payload.result_preview ?? nestedTool.result ?? nestedTool.response ?? nestedTool.result_preview;
  const agentVisibleResult = payload.agentVisibleResult
    ?? payload.agent_visible_result
    ?? payload.agentVisibleOutput
    ?? payload.agent_visible_output
    ?? nestedTool.agentVisibleResult
    ?? nestedTool.agent_visible_result
    ?? nestedTool.agentVisibleOutput
    ?? nestedTool.agent_visible_output;
  const agentVisibleChars = typeof payload.agentVisibleChars === "number"
    ? payload.agentVisibleChars
    : typeof payload.agent_visible_chars === "number"
      ? payload.agent_visible_chars
      : typeof nestedTool.agentVisibleChars === "number"
        ? nestedTool.agentVisibleChars
        : typeof nestedTool.agent_visible_chars === "number"
          ? nestedTool.agent_visible_chars
          : undefined;

  if (!toolCallId && !toolName && args === undefined && result === undefined && agentVisibleResult === undefined) {
    return undefined;
  }

  return {
    toolCallId: toolCallId || toolInvocationId || undefined,
    toolInvocationId: toolInvocationId || toolCallId || undefined,
    toolName: toolName || undefined,
    args,
    result,
    agentVisibleResult,
    agentVisibleChars,
    mcpApp: payloadMcpApp,
  };
}

function buildTypedEventFromMatrixEntry(
  topic: string,
  payload: JsonRecord,
  {
    matrixEntry,
    runtimeId,
    scope,
    visibility,
    targets,
    actorLabel,
    source,
    locale,
  }: {
    matrixEntry: NonNullable<ReturnType<typeof resolveMatrixEntry>>;
    runtimeId: SessionRuntimeId | null;
    scope: SessionRuntimeScope;
    visibility: SessionRuntimeVisibility;
    targets: ReturnType<typeof resolveTargets>;
    actorLabel: string | undefined;
    source: SessionRuntimeEventSource | undefined;
    locale: NormalizeRuntimeEventOptions["locale"];
  },
): NormalizedSessionRuntimeEvent {
  const runtime = runtimeId || matrixEntry.runtimeId;
  const sharedBase = {
    runtimeId: runtime || undefined,
    scope,
    visibility,
    targets,
    actorLabel,
    tool: extractToolPayload(payload),
    data: {
      ...payload,
      topic,
      runtimeId: runtime || undefined,
      actorLabel,
    },
    source,
  } satisfies Partial<NormalizedSessionRuntimeEvent>;

  if (matrixEntry.eventType === "agent_start") {
    return {
      type: "agent_start",
      ...sharedBase,
    };
  }

  if (matrixEntry.eventType === "text_chunk") {
    return {
      type: "text_chunk",
      content: pickFirstString(payload.content, payload.delta, payload.text, payload.message, payload.summary) || "",
      ...sharedBase,
    };
  }

  if (matrixEntry.eventType === "reasoning_chunk") {
    return {
      type: "reasoning_chunk",
      content: pickFirstString(payload.content, payload.delta, payload.text, payload.message, payload.summary) || "",
      reasoningKind: pickFirstString(payload.reasoningKind, payload.reasoning_kind, payload.displayKind, payload.display_kind),
      ...sharedBase,
    };
  }

  if (matrixEntry.eventType === "tool_start") {
    return {
      type: "tool_start",
      content: pickFirstString(payload.message, payload.summary),
      ...sharedBase,
    };
  }

  if (matrixEntry.eventType === "tool_result") {
    return {
      type: "tool_result",
      content: pickFirstString(payload.message, payload.summary),
      ...sharedBase,
    };
  }

  if (matrixEntry.eventType === "done") {
    return {
      type: "done",
      content: pickFirstString(payload.message, payload.summary, payload.result),
      ...sharedBase,
    };
  }

  if (matrixEntry.eventType === "error") {
    return {
      type: "error",
      error: pickFirstString(payload.error, payload.message, payload.summary, payload.reason),
      content: pickFirstString(payload.message, payload.summary, payload.reason),
      ...sharedBase,
    };
  }

  const label = buildProgressLabel(topic, payload, locale);
  return {
    type: matrixEntry.eventType || "custom_event",
    name: matrixEntry.eventName,
    content: pickFirstString(payload.message, payload.summary, payload.reason, payload.label),
    status: pickFirstString(payload.status, payload.to_status, payload.toStatus, payload.approval_status, payload.approvalStatus),
    ...sharedBase,
    data: {
      ...payload,
      topic,
      label,
      runtimeId: runtime || undefined,
      actorLabel,
    },
  };
}

export function normalizeSessionRuntimeEvent(raw: unknown, options: NormalizeRuntimeEventOptions = {}): NormalizedSessionRuntimeEvent | null {
  if (!raw || typeof raw !== "object") {
    return null;
  }

  const direct = raw as NormalizedSessionRuntimeEvent;
  if (typeof direct.type === "string" && typeof direct.scope === "string" && typeof direct.visibility === "string") {
    return direct;
  }

  if (typeof direct.type === "string") {
    const source = resolveSource(direct as RuntimeEnvelope, direct.data);
    const directPayload = direct as unknown as JsonRecord;
    const normalizedType = normalizeTypedEventType(direct.type);
    const directEventName = typeof direct.name === "string" ? direct.name : "";
    const explicitRuntimeId = resolveExplicitRuntimeId(directPayload);
    const directMatrixEntry = typeof direct.topic === "string" ? resolveMatrixEntry(direct.topic, directPayload) : null;
    const typedRuntimeId =
      explicitRuntimeId
      || (typeof direct.topic === "string" ? resolveRuntimeId(direct.topic, directPayload, source) : null)
      || directMatrixEntry?.runtimeId
      || resolveTypedEventRuntimeId(normalizedType, directEventName, directPayload);
    const normalizedRuntimeId = normalizeRuntimeId(
      typedRuntimeId
      || (typeof direct.topic === "string"
        ? direct.topic
        : typeof direct.name === "string"
          ? direct.name
          : ""),
    );
    const typedTargets = resolveTypedEventTargets(normalizedType, directEventName);
    const explicitTargets = resolveExplicitTargets(directPayload, direct.targets);
    const typedVisibility = resolveTypedEventVisibility(normalizedType, directEventName);
    return applySurfaceVisibilityOverrides(withTranscriptTargetFields({
      ...direct,
      type: normalizedType,
      runtimeId: normalizedRuntimeId || undefined,
      tool: direct.tool || extractToolPayload(directPayload),
      source: source || direct.source,
      scope: direct.scope || (typeof direct.topic === "string" ? resolveScope(direct.topic, direct as unknown as JsonRecord) : "active_run"),
      visibility: direct.visibility || typedVisibility || (typeof direct.topic === "string" ? resolveVisibility(direct.topic, direct as unknown as JsonRecord) : "visible"),
      targets: explicitTargets || typedTargets || (typeof direct.topic === "string" ? resolveTargets(direct.topic, direct as unknown as JsonRecord) : ["runtime_card"]),
      actorLabel: direct.actorLabel || (normalizedRuntimeId ? resolveActorLabel(normalizedRuntimeId, options.locale) : undefined),
      raw: asRecord(direct.raw || direct),
    }, directPayload), directPayload);
  }

  const envelope = raw as RuntimeEnvelope;
  const topic = typeof envelope.topic === "string" ? envelope.topic : "";
  const payload = asRecord(envelope.payload);
  const source = resolveSource(envelope, payload);
  if (!topic && typeof payload.type !== "string") {
    return null;
  }

  if (typeof payload.type === "string") {
    const matrixEntry = resolveMatrixEntry(topic, payload);
    const normalizedType = normalizeTypedEventType(payload.type);
    const typedEventName = typeof payload.name === "string" ? payload.name : "";
    const explicitRuntimeId = resolveExplicitRuntimeId(payload);
    const baseRuntimeId =
      explicitRuntimeId
      || resolveRuntimeId(topic, payload, source)
      || matrixEntry?.runtimeId
      || resolveTypedEventRuntimeId(normalizedType, typedEventName, payload)
      || null;
    const typedTargets = resolveTypedEventTargets(normalizedType, typedEventName);
    const explicitTargets = resolveExplicitTargets(payload);
    const typedVisibility = resolveTypedEventVisibility(normalizedType, typedEventName);
    return applySurfaceVisibilityOverrides(withTranscriptTargetFields(withEnvelopeFields({
      ...(payload as unknown as NormalizedSessionRuntimeEvent),
      type: normalizedType,
      runtimeId: baseRuntimeId || undefined,
      tool: extractToolPayload(payload),
      scope: resolveScope(topic, payload),
      visibility: typedVisibility || (topic ? resolveVisibility(topic, payload) : "visible"),
      targets: explicitTargets || typedTargets || (topic ? resolveTargets(topic, payload) : ["runtime_card"]),
      actorLabel: resolveActorLabel(baseRuntimeId, options.locale),
      source,
      raw: asRecord(raw),
    }, envelope), payload), payload);
  }

  const matrixEntry = resolveMatrixEntry(topic, payload);
  const runtimeId = resolveExplicitRuntimeId(payload) || resolveRuntimeId(topic, payload, source) || matrixEntry?.runtimeId || null;
  const scope = resolveScope(topic, payload);
  const visibility = resolveVisibility(topic, payload);
  const targets = resolveExplicitTargets(payload) || resolveTargets(topic, payload);
  const actorLabel = resolveActorLabel(runtimeId, options.locale);

  if (topic === "ask_user.requested") {
    const request = asRecord(payload.request);
    return applySurfaceVisibilityOverrides(withTranscriptTargetFields(withEnvelopeFields({
      type: "custom_event",
      name: "ask_user",
      runtimeId: runtimeId || "chat",
      scope,
      visibility,
      targets: ["hud", "runtime_card"],
      actorLabel,
      data: {
        question:
          (typeof request.question === "string" && request.question)
          || (typeof request.prompt === "string" && request.prompt)
          || tr(options.locale, "我需要您的输入以继续执行任务。", "I need your input to continue the task."),
        toolCallId:
          (typeof request.toolCallId === "string" && request.toolCallId)
          || (typeof payload.toolCallId === "string" && payload.toolCallId)
          || "",
        interactionId:
          (typeof payload.interactionId === "string" && payload.interactionId)
          || (typeof payload.id === "string" && payload.id)
          || undefined,
        interactionKind:
          (typeof request.interactionKind === "string" && request.interactionKind)
          || (typeof payload.interactionKind === "string" && payload.interactionKind)
          || "ask_user",
        request,
        runtimeId: runtimeId || "chat",
        actorLabel,
      },
      source,
    }, envelope), payload), payload);
  }

  if (topic === "ask_user.resolved") {
    return applySurfaceVisibilityOverrides(withTranscriptTargetFields(withEnvelopeFields({
      type: "custom_event",
      name: "ask_user",
      runtimeId: runtimeId || "chat",
      scope,
      visibility,
      targets: ["hud", "runtime_card"],
      actorLabel,
      status: typeof payload.status === "string" ? payload.status : "resolved",
      data: {
        ...payload,
        topic,
        runtimeId: runtimeId || "chat",
        actorLabel,
      },
      source,
    }, envelope), payload), payload);
  }

  if (topic === "approval.requested") {
    const request = asRecord(payload.request);
    return applySurfaceVisibilityOverrides(withTranscriptTargetFields(withEnvelopeFields({
      type: "custom_event",
      name: "approval_requested",
      runtimeId: runtimeId || "automation",
      scope,
      visibility,
      targets: ["approval", "hud", "runtime_card"],
      actorLabel,
      data: {
        question:
          (typeof request.question === "string" && request.question)
          || (typeof request.prompt === "string" && request.prompt)
          || tr(options.locale, "我需要您的输入以继续执行任务。", "I need your input to continue the task."),
        toolCallId:
          (typeof request.toolCallId === "string" && request.toolCallId)
          || (typeof payload.approval_id === "string" && payload.approval_id)
          || "",
        approvalId: typeof payload.approval_id === "string" ? payload.approval_id : undefined,
        approvalKind: typeof payload.approval_kind === "string" ? payload.approval_kind : undefined,
        interactionKind: typeof request.interactionKind === "string" ? request.interactionKind : undefined,
        request,
        runtimeId: runtimeId || "automation",
        actorLabel,
      },
      source,
    }, envelope), payload), payload);
  }

  if (matrixEntry?.explicit && (matrixEntry.eventType || matrixEntry.eventName)) {
    return applySurfaceVisibilityOverrides(withTranscriptTargetFields(withEnvelopeFields(
      buildTypedEventFromMatrixEntry(topic, payload, {
        matrixEntry,
        runtimeId,
        scope,
        visibility,
        targets,
        actorLabel,
        source,
        locale: options.locale,
      }),
      envelope,
    ), payload), payload);
  }

  if (["run.paused", "run.cancelled", "run.interrupted", "run.resumed", "run.retry.requested", "run.state.changed"].includes(topic)) {
    return applySurfaceVisibilityOverrides(withTranscriptTargetFields(withEnvelopeFields({
      type: "custom_event",
      name: "run_controlled",
      runtimeId: runtimeId || "chat",
      scope,
      visibility,
      targets,
      actorLabel,
      status: typeof payload.status === "string" ? payload.status : undefined,
      data: {
        ...payload,
        topic,
        runtimeId: runtimeId || "chat",
        actorLabel,
      },
      source,
    }, envelope), payload), payload);
  }

  if (topic === "artifact.recorded") {
    return applySurfaceVisibilityOverrides(withTranscriptTargetFields(withEnvelopeFields({
      type: "custom_event",
      name: "artifact_recorded",
      runtimeId: runtimeId || "chat",
      scope,
      visibility,
      targets,
      actorLabel,
      data: {
        artifact: payload,
        runtimeId: runtimeId || "chat",
        actorLabel,
      },
      artifact: payload,
      source,
    }, envelope), payload), payload);
  }

  if (topic === "context.prepared") {
    return applySurfaceVisibilityOverrides(withTranscriptTargetFields(withEnvelopeFields({
      type: "custom_event",
      name: "context_governance_changed",
      runtimeId: "chat",
      scope,
      visibility,
      targets,
      actorLabel,
      data: {
        ...payload,
        topic,
        label: buildProgressLabel(topic, payload, options.locale),
        runtimeId: "chat",
        actorLabel,
      },
      source,
    }, envelope), payload), payload);
  }

  const label = buildProgressLabel(topic, payload, options.locale);
  return applySurfaceVisibilityOverrides(withTranscriptTargetFields(withEnvelopeFields({
    type: "custom_event",
    name: label && label !== topic ? "runtime_progress" : "runtime_event",
    runtimeId: runtimeId || undefined,
    scope,
    visibility,
    targets,
    actorLabel,
    status: typeof payload.status === "string" ? payload.status : undefined,
    data: {
      ...payload,
      topic,
      label,
      runtimeId: runtimeId || undefined,
      actorLabel,
    },
    source,
  }, envelope), payload), payload);
}
