export type SessionHistorySourceGroup = "web" | "channels" | "cron" | "hooks";

export type SessionHistoryControls = {
  canResume?: boolean;
  canRetry?: boolean;
  canInterrupt?: boolean;
  canOpenApproval?: boolean;
};

export type ChannelHistorySubdocument = {
  channelType?: string;
  channelName?: string;
  channelDomain?: string;
  accountId?: string;
  chatType?: string;
  defaultAccount?: string;
  externalMessageId?: string;
  deliveryStatus?: string;
  channelState?: string;
};

export type SessionHistoryWorkflowSummary = {
  workflowStatus?: string;
  statusLabel?: string;
  stepStatus?: string;
  ownerRuntime?: string;
  ownerAgentId?: string;
  currentStepId?: string;
  currentStepKey?: string;
  currentStepTitle?: string;
};

export type AuthoritativeSessionHistoryRecord = {
  id: string;
  sessionId?: string;
  title: string;
  source?: string;
  sourceGroup: SessionHistorySourceGroup;
  runtimeOwner?: string;
  ownerRuntime?: string;
  ownerAgentId?: string;
  createdAt?: string;
  updatedAt?: string;
  updated_at?: string;
  startedAt?: string;
  lastActivityAt?: string;
  endedAt?: string;
  status?: string;
  workflowStatus?: string;
  statusLabel?: string;
  stepStatus?: string;
  currentRunId?: string;
  lastRunId?: string;
  currentStepId?: string;
  currentStepKey?: string;
  currentStepTitle?: string;
  previewExcerpt?: string;
  lastNarrativeExcerpt?: string;
  lastRuntimeSummary?: string;
  pendingApprovalCount?: number;
  hasPendingApproval?: boolean;
  recoverable?: boolean;
  scopeTags: string[];
  controls?: SessionHistoryControls;
  metadata?: string | Record<string, unknown>;
  parsedMetadata?: Record<string, unknown>;
  workflowSummary?: SessionHistoryWorkflowSummary;
  channel?: ChannelHistorySubdocument | null;
  channelType?: string;
  channelName?: string;
  channelDomain?: string;
  accountId?: string;
  chatType?: string;
  defaultAccount?: string;
};

export type SessionHistoryLedgerEntry = {
  eventId: string;
  seq: number;
  sessionId: string;
  runId?: string | null;
  ts: string;
  runtimeFamily: string;
  eventName: string;
  scope: "session" | "active_run";
  visibility: "visible" | "hidden" | "history_only" | "excluded";
  targets: string[];
  messageRef?: string | null;
  toolCallId?: string | null;
  processRef?: string | null;
  resourceRef?: string | null;
  payload: Record<string, unknown>;
};

export type SessionHistoryMaterializedView = {
  record: AuthoritativeSessionHistoryRecord;
  updatedAt?: string;
};

function parseMetadata(metadata: AuthoritativeSessionHistoryRecord["metadata"]): Record<string, unknown> {
  if (!metadata) return {};
  if (typeof metadata === "string") {
    try {
      return JSON.parse(metadata) as Record<string, unknown>;
    } catch {
      return {};
    }
  }
  if (typeof metadata === "object") {
    return metadata;
  }
  return {};
}

function coerceString(value: unknown): string | undefined {
  const normalized = String(value || "").trim();
  return normalized || undefined;
}

function normalizeSourceGroup(value: unknown): SessionHistorySourceGroup | "" {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "web" || normalized === "channels" || normalized === "cron" || normalized === "hooks") {
    return normalized;
  }
  return "";
}

function deriveScopeTags(parsedMetadata: Record<string, unknown>, record: Record<string, unknown>): string[] {
  const explicitScopeTags = [
    ...(Array.isArray(parsedMetadata.scopeTags) ? parsedMetadata.scopeTags : []),
    ...(Array.isArray(parsedMetadata.scope_tags) ? parsedMetadata.scope_tags : []),
    ...(Array.isArray(record.scopeTags) ? record.scopeTags : []),
    ...(Array.isArray(record.scope_tags) ? record.scope_tags : []),
  ]
    .map((value) => String(value || "").trim())
    .filter(Boolean);

  if (explicitScopeTags.length > 0) {
    return Array.from(new Set(explicitScopeTags));
  }

  const tags: string[] = [];
  const projectId = parsedMetadata.project_id || parsedMetadata.projectId || record.projectId || record.project_id;
  const resolvedScope = parsedMetadata.resolved_scope || parsedMetadata.resolvedScope || record.resolvedScope || record.resolved_scope;
  for (const value of [projectId, resolvedScope]) {
    const normalized = String(value || "").trim();
    if (normalized && !tags.includes(normalized)) {
      tags.push(normalized);
    }
  }
  return tags;
}

function deriveChannelSubdocument(record: Record<string, unknown>, parsedMetadata: Record<string, unknown>) {
  const rawChannel = record.channel && typeof record.channel === "object"
    ? record.channel as Record<string, unknown>
    : {};
  const next = {
    channelType: coerceString(rawChannel.channelType || rawChannel.channel_type || record.channelType || record.channel_type || parsedMetadata.channelType || parsedMetadata.channel_type),
    channelName: coerceString(rawChannel.channelName || rawChannel.channel_name || record.channelName || record.channel_name || parsedMetadata.channelName || parsedMetadata.channel_name),
    channelDomain: coerceString(rawChannel.channelDomain || rawChannel.channel_domain || record.channelDomain || record.channel_domain || parsedMetadata.channelDomain || parsedMetadata.channel_domain),
    accountId: coerceString(rawChannel.accountId || rawChannel.account_id || record.accountId || record.account_id || parsedMetadata.accountId || parsedMetadata.account_id),
    chatType: coerceString(rawChannel.chatType || rawChannel.chat_type || record.chatType || record.chat_type || parsedMetadata.chatType || parsedMetadata.chat_type),
    defaultAccount: coerceString(rawChannel.defaultAccount || rawChannel.default_account || record.defaultAccount || record.default_account || parsedMetadata.defaultAccount || parsedMetadata.default_account),
    externalMessageId: coerceString(rawChannel.externalMessageId || rawChannel.external_message_id || record.externalMessageId || record.external_message_id),
    deliveryStatus: coerceString(rawChannel.deliveryStatus || rawChannel.delivery_status || record.deliveryStatus || record.delivery_status),
    channelState: coerceString(rawChannel.channelState || rawChannel.channel_state || record.channelState || record.channel_state),
  } satisfies ChannelHistorySubdocument;

  return Object.values(next).some(Boolean) ? next : null;
}

function sortHistoryItems(items: AuthoritativeSessionHistoryRecord[]) {
  return [...items].sort((left, right) => {
    const leftTs = left.lastActivityAt || left.updatedAt || left.updated_at || left.startedAt || left.createdAt || "";
    const rightTs = right.lastActivityAt || right.updatedAt || right.updated_at || right.startedAt || right.createdAt || "";
    return rightTs.localeCompare(leftTs);
  });
}

export function normalizeAuthoritativeSessionHistoryRecord(raw: unknown): AuthoritativeSessionHistoryRecord {
  const record = raw && typeof raw === "object" ? raw as Record<string, unknown> : {};
  const parsedMetadata = parseMetadata(record.metadata as AuthoritativeSessionHistoryRecord["metadata"]);
  const channel = deriveChannelSubdocument(record, parsedMetadata);
  const sourceGroup = normalizeSourceGroup(record.sourceGroup || record.source_group) || "web";
  const previewExcerpt = coerceString(record.previewExcerpt) || coerceString(record.lastNarrativeExcerpt);
  const canonicalSessionId = coerceString(record.sessionId || record.session_id || record.id) || "";
  const workflowSummary = record.workflowSummary && typeof record.workflowSummary === "object"
    ? record.workflowSummary as SessionHistoryWorkflowSummary
    : undefined;

  return {
    id: canonicalSessionId,
    sessionId: canonicalSessionId,
    title: coerceString(record.title) || "新对话",
    source: coerceString(record.source),
    sourceGroup,
    runtimeOwner: coerceString(record.runtimeOwner) || coerceString(record.ownerRuntime),
    ownerRuntime: coerceString(record.ownerRuntime) || coerceString(record.runtimeOwner),
    ownerAgentId: coerceString(record.ownerAgentId),
    createdAt: coerceString(record.createdAt || record.created_at || record.startedAt),
    updatedAt: coerceString(record.updatedAt || record.updated_at || record.lastActivityAt),
    updated_at: coerceString(record.updated_at || record.updatedAt || record.lastActivityAt),
    startedAt: coerceString(record.startedAt || record.createdAt || record.created_at),
    lastActivityAt: coerceString(record.lastActivityAt || record.updatedAt || record.updated_at),
    endedAt: coerceString(record.endedAt),
    status: coerceString(record.status) || coerceString(record.workflowStatus),
    workflowStatus: coerceString(record.workflowStatus) || coerceString(record.status),
    statusLabel: coerceString(record.statusLabel),
    stepStatus: coerceString(record.stepStatus),
    currentRunId: coerceString(record.currentRunId),
    lastRunId: coerceString(record.lastRunId),
    currentStepId: coerceString(record.currentStepId),
    currentStepKey: coerceString(record.currentStepKey),
    currentStepTitle: coerceString(record.currentStepTitle),
    previewExcerpt,
    lastNarrativeExcerpt: coerceString(record.lastNarrativeExcerpt) || previewExcerpt,
    lastRuntimeSummary: coerceString(record.lastRuntimeSummary),
    pendingApprovalCount: Number(record.pendingApprovalCount || 0) || 0,
    hasPendingApproval: Boolean(record.hasPendingApproval),
    recoverable: Boolean(record.recoverable),
    scopeTags: deriveScopeTags(parsedMetadata, record),
    controls: (record.controls as SessionHistoryControls) || undefined,
    metadata: (record.metadata as AuthoritativeSessionHistoryRecord["metadata"]) || parsedMetadata,
    parsedMetadata,
    workflowSummary,
    channel,
    channelType: channel?.channelType,
    channelName: channel?.channelName,
    channelDomain: channel?.channelDomain,
    accountId: channel?.accountId,
    chatType: channel?.chatType,
    defaultAccount: channel?.defaultAccount,
  };
}

export function normalizeAuthoritativeSessionHistoryList(raw: unknown[]): AuthoritativeSessionHistoryRecord[] {
  return sortHistoryItems(
    raw
      .map((item) => normalizeAuthoritativeSessionHistoryRecord(item))
      .filter((item) => {
        if (!item.id) return false;
        if (item.parsedMetadata?.hiddenFromHistory === true) return false;
        if (item.ownerRuntime === "memory" || item.runtimeOwner === "memory") return false;
        if (item.id.startsWith("hook:on_chat_end:memory:") || item.id.startsWith("memory:summary:")) return false;
        return true;
      }),
  );
}

export function mergeAuthoritativeSessionHistoryRecord(
  current: AuthoritativeSessionHistoryRecord,
  patch: Partial<AuthoritativeSessionHistoryRecord>,
): AuthoritativeSessionHistoryRecord {
  const mergedMetadata = patch.parsedMetadata
    ? { ...(current.parsedMetadata || {}), ...(patch.parsedMetadata || {}) }
    : current.parsedMetadata;
  const next = normalizeAuthoritativeSessionHistoryRecord({
    ...current,
    ...patch,
    metadata: patch.metadata ?? current.metadata,
    parsedMetadata: mergedMetadata,
  });
  return {
    ...next,
    parsedMetadata: mergedMetadata || next.parsedMetadata,
    scopeTags: patch.scopeTags && patch.scopeTags.length > 0 ? patch.scopeTags : next.scopeTags,
  };
}

export function sortAuthoritativeSessionHistory(
  items: AuthoritativeSessionHistoryRecord[],
): AuthoritativeSessionHistoryRecord[] {
  return sortHistoryItems(items);
}
