import type {
  NormalizedSessionRuntimeEvent,
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
  return normalizeString(
    payload.runtimeId
    || payload.runtime
    || payload.runtimeKind
    || payload.runtime_kind
    || payload.runtimeFamily
    || payload.runtime_family
    || payload.kind
    || payload.family,
  );
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

export function shouldForwardRuntimeEventToRealtimeSurface(
  event: Pick<NormalizedSessionRuntimeEvent, "visibility"> | SessionRuntimeVisibility | null | undefined,
) {
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
  if (topic === "run.state.changed") {
    const fromStatus = pickFirstString(payload.from_status, payload.fromStatus);
    const toStatus = pickFirstString(payload.to_status, payload.toStatus, payload.status);
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
    const normalizedRuntimeId = normalizeRuntimeId(
      typeof direct.runtimeId === "string"
        ? direct.runtimeId
        : typeof direct.topic === "string"
          ? direct.topic
          : typeof direct.name === "string"
            ? direct.name
            : "",
    );
    return {
      ...direct,
      runtimeId: normalizedRuntimeId || undefined,
      source: source || direct.source,
      scope: direct.scope || (typeof direct.topic === "string" ? resolveScope(direct.topic, direct as unknown as JsonRecord) : "active_run"),
      visibility: direct.visibility || (typeof direct.topic === "string" ? resolveVisibility(direct.topic, direct as unknown as JsonRecord) : "visible"),
      targets: Array.isArray(direct.targets) ? direct.targets : (typeof direct.topic === "string" ? resolveTargets(direct.topic, direct as unknown as JsonRecord) : ["runtime_card"]),
      actorLabel: direct.actorLabel || (normalizedRuntimeId ? resolveActorLabel(normalizedRuntimeId, options.locale) : undefined),
      raw: asRecord(direct.raw || direct),
    };
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
    const baseRuntimeId = resolveRuntimeId(topic, payload, source) || matrixEntry?.runtimeId || null;
    return withEnvelopeFields({
      ...(payload as unknown as NormalizedSessionRuntimeEvent),
      runtimeId: baseRuntimeId || undefined,
      scope: resolveScope(topic, payload),
      visibility: resolveVisibility(topic, payload),
      targets: resolveTargets(topic, payload),
      actorLabel: resolveActorLabel(baseRuntimeId, options.locale),
      source,
      raw: asRecord(raw),
    }, envelope);
  }

  const matrixEntry = resolveMatrixEntry(topic, payload);
  const runtimeId = resolveRuntimeId(topic, payload, source) || matrixEntry?.runtimeId || null;
  const scope = resolveScope(topic, payload);
  const visibility = resolveVisibility(topic, payload);
  const targets = resolveTargets(topic, payload);
  const actorLabel = resolveActorLabel(runtimeId, options.locale);

  if (matrixEntry?.explicit && (matrixEntry.eventType || matrixEntry.eventName)) {
    return withEnvelopeFields(
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
    );
  }

  if (topic === "approval.requested") {
    const request = asRecord(payload.request);
    return withEnvelopeFields({
      type: "custom_event",
      name: "ask_user",
      runtimeId: runtimeId || "automation",
      scope,
      visibility,
      targets,
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
    }, envelope);
  }

  if (["run.paused", "run.cancelled", "run.interrupted", "run.resumed", "run.retry.requested", "run.state.changed"].includes(topic)) {
    return withEnvelopeFields({
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
    }, envelope);
  }

  if (topic === "artifact.recorded") {
    return withEnvelopeFields({
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
    }, envelope);
  }

  if (topic === "context.prepared") {
    return withEnvelopeFields({
      type: "custom_event",
      name: "context_governance_changed",
      runtimeId: runtimeId || "chat",
      scope,
      visibility,
      targets,
      actorLabel,
      data: {
        ...payload,
        topic,
        label: buildProgressLabel(topic, payload, options.locale),
        runtimeId: runtimeId || "chat",
        actorLabel,
      },
      source,
    }, envelope);
  }

  const label = buildProgressLabel(topic, payload, options.locale);
  return withEnvelopeFields({
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
  }, envelope);
}
