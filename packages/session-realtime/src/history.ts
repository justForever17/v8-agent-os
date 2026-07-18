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
  supervisorWorkMode?: "daily" | "engineering";
  pinned?: boolean;
  pinnedAt?: string;
  projectId?: string;
  projectName?: string;
  workspaceId?: string;
  workspacePath?: string;
  workspaceDisplayName?: string;
  workspacePinned?: boolean;
  workspacePinnedAt?: string;
  scopeHint?: string;
  scopeMode?: string;
  resolvedScope?: string;
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
  historySortAt?: string;
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

export type SessionHistoryCreationBinding = {
  projectId?: string;
  workspaceId?: string;
  workspacePath?: string;
  scopeHint?: string;
  scopeMode: "explicit";
};

export type SessionHistoryWorkspaceGroupLabels = {
  mainWorkspace: string;
  externalWorkspace: string;
  unbound: string;
  workspace: string;
};

export type SessionHistoryWorkspaceGroup<T extends AuthoritativeSessionHistoryRecord = AuthoritativeSessionHistoryRecord> = {
  key: string;
  label: string;
  kind: "project" | "workspace" | "unbound";
  items: T[];
  creationBinding: SessionHistoryCreationBinding | null;
  workspacePath?: string;
  pinned: boolean;
  pinnedAt?: string;
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

export type CanonicalTurnIndexEntry = {
  turnId: string;
  position: number;
  firstOrdinal: number;
  lastOrdinal: number;
  messageCount: number;
  preview: string;
  state: string;
  createdAt?: string | null;
  updatedAt?: string | null;
};

export type CanonicalTurnPageInfo = {
  hasMore: boolean;
  hasOlder: boolean;
  hasNewer: boolean;
  beforeCursor?: string | null;
  afterCursor?: string | null;
  loadedTurnCount: number;
  totalTurnCount?: number | null;
  firstTurnId?: string | null;
  lastTurnId?: string | null;
  anchorTurnId?: string | null;
  anchorPosition?: number | null;
  windowStartPosition?: number | null;
  windowEndPosition?: number | null;
  firstPosition?: number | null;
  lastPosition?: number | null;
};

export type CanonicalTurnIndexPayload = {
  sessionId: string;
  turns: CanonicalTurnIndexEntry[];
  pageInfo: CanonicalTurnPageInfo;
};

export type CanonicalTurnWindowPayload<TMessage = Record<string, unknown>> = {
  sessionId: string;
  syncCursor?: string | null;
  messages: TMessage[];
  pageInfo: CanonicalTurnPageInfo;
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

function normalizeUtcTimestamp(value: unknown): string | undefined {
  if (value === null || value === undefined) {
    return undefined;
  }
  if (typeof value === "number" && Number.isFinite(value)) {
    const timestamp = Math.abs(value) > 1_000_000_000_000 ? value : value * 1000;
    return new Date(timestamp).toISOString();
  }
  const text = String(value || "").trim();
  if (!text) {
    return undefined;
  }
  if (/^\d+(\.\d+)?$/.test(text)) {
    const numeric = Number(text);
    if (!Number.isFinite(numeric)) {
      return undefined;
    }
    const timestamp = Math.abs(numeric) > 1_000_000_000_000 ? numeric : numeric * 1000;
    return new Date(timestamp).toISOString();
  }
  const sqliteMatch = text.match(/^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}(?:\.\d+)?)$/);
  if (sqliteMatch) {
    const parsed = Date.parse(`${sqliteMatch[1]}T${sqliteMatch[2]}Z`);
    return Number.isNaN(parsed) ? undefined : new Date(parsed).toISOString();
  }
  const naiveIsoMatch = text.match(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
  if (naiveIsoMatch && !/[zZ]$|[+-]\d{2}:\d{2}$/.test(text)) {
    const parsed = Date.parse(`${text}Z`);
    return Number.isNaN(parsed) ? undefined : new Date(parsed).toISOString();
  }
  const parsed = Date.parse(text);
  return Number.isNaN(parsed) ? undefined : new Date(parsed).toISOString();
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
    if (Boolean(left.pinned) !== Boolean(right.pinned)) {
      return left.pinned ? -1 : 1;
    }
    const pinnedOrder = String(right.pinnedAt || "").localeCompare(String(left.pinnedAt || ""));
    if (pinnedOrder !== 0) {
      return pinnedOrder;
    }
    const leftTs = left.historySortAt || left.createdAt || "";
    const rightTs = right.historySortAt || right.createdAt || "";
    return rightTs.localeCompare(leftTs);
  });
}

function readBindingString(record: Record<string, unknown>, parsedMetadata: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const normalized = coerceString(record[key]) || coerceString(parsedMetadata[key]);
    if (normalized) return normalized;
  }
  return undefined;
}

function shouldHideHistoryRecord(item: AuthoritativeSessionHistoryRecord): boolean {
  const metadata = item.parsedMetadata || {};
  if (
    metadata.hiddenFromHistory === true ||
    metadata.nonChatRun === true ||
    metadata.manualRpaRun === true ||
    metadata.internalProbe === true ||
    metadata.governanceOnly === true
  ) {
    return true;
  }

  const runtimeOwner = String(item.ownerRuntime || item.runtimeOwner || "").trim().toLowerCase();
  if (runtimeOwner === "memory") {
    return true;
  }

  const id = String(item.id || "").trim().toLowerCase();
  if (
    id.startsWith("hook:on_chat_end:memory:") ||
    id.startsWith("memory:summary:") ||
    id.startsWith("memory:") ||
    id.startsWith("computer_use:") ||
    id.startsWith("computer-use:") ||
    id.startsWith("rpa:")
  ) {
    return true;
  }

  return false;
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
    supervisorWorkMode: (
      coerceString(record.supervisorWorkMode || record.supervisor_work_mode || parsedMetadata.supervisorWorkMode || parsedMetadata.supervisor_work_mode) === "engineering"
        ? "engineering"
        : "daily"
    ),
    pinned: Boolean(record.pinned ?? parsedMetadata.pinned),
    pinnedAt: normalizeUtcTimestamp(record.pinnedAt || record.pinned_at || parsedMetadata.pinnedAt || parsedMetadata.pinned_at),
    projectId: readBindingString(record, parsedMetadata, "projectId", "project_id"),
    projectName: readBindingString(record, parsedMetadata, "projectName", "project_name"),
    workspaceId: readBindingString(record, parsedMetadata, "workspaceId", "workspace_id"),
    workspacePath: readBindingString(record, parsedMetadata, "workspacePath", "workspace_path"),
    workspaceDisplayName: readBindingString(record, parsedMetadata, "workspaceDisplayName", "workspace_display_name"),
    workspacePinned: Boolean(record.workspacePinned ?? record.workspace_pinned ?? parsedMetadata.workspacePinned ?? parsedMetadata.workspace_pinned),
    workspacePinnedAt: normalizeUtcTimestamp(record.workspacePinnedAt || record.workspace_pinned_at || parsedMetadata.workspacePinnedAt || parsedMetadata.workspace_pinned_at),
    scopeHint: readBindingString(record, parsedMetadata, "scopeHint", "scope_hint"),
    scopeMode: readBindingString(record, parsedMetadata, "scopeMode", "scope_mode"),
    resolvedScope: readBindingString(record, parsedMetadata, "resolvedScope", "resolved_scope"),
    source: coerceString(record.source),
    sourceGroup,
    runtimeOwner: coerceString(record.runtimeOwner) || coerceString(record.ownerRuntime),
    ownerRuntime: coerceString(record.ownerRuntime) || coerceString(record.runtimeOwner),
    ownerAgentId: coerceString(record.ownerAgentId),
    createdAt: normalizeUtcTimestamp(record.createdAt || record.created_at || record.startedAt),
    updatedAt: normalizeUtcTimestamp(record.updatedAt || record.updated_at),
    updated_at: normalizeUtcTimestamp(record.updated_at || record.updatedAt),
    startedAt: normalizeUtcTimestamp(record.startedAt || record.createdAt || record.created_at),
    lastActivityAt: normalizeUtcTimestamp(record.lastActivityAt || record.updatedAt || record.updated_at),
    historySortAt: normalizeUtcTimestamp(record.historySortAt || record.createdAt || record.created_at || record.startedAt),
    endedAt: normalizeUtcTimestamp(record.endedAt),
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
        if (shouldHideHistoryRecord(item)) return false;
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

function normalizePathKey(value: string): string {
  return value.trim().replace(/\\/g, "/").replace(/\/+$/g, "").toLowerCase();
}

function basenameFromPath(value: string): string {
  const normalized = value.trim().replace(/\\/g, "/").replace(/\/+$/g, "");
  return normalized.split("/").filter(Boolean).pop() || normalized;
}

function labelFromProjectId(value: string): string {
  return value.replace(/^project:/i, "").trim() || value;
}

function explicitCreationBinding(item: AuthoritativeSessionHistoryRecord): SessionHistoryCreationBinding | null {
  if (!item.projectId && !item.workspaceId && !item.workspacePath) {
    return null;
  }
  return {
    ...(item.projectId ? { projectId: item.projectId } : {}),
    ...(item.workspaceId ? { workspaceId: item.workspaceId } : {}),
    ...(item.workspacePath ? { workspacePath: item.workspacePath } : {}),
    ...(item.scopeHint ? { scopeHint: item.scopeHint } : {}),
    scopeMode: "explicit",
  };
}

function workspaceGroupIdentity(
  item: AuthoritativeSessionHistoryRecord,
  labels: SessionHistoryWorkspaceGroupLabels,
): Omit<SessionHistoryWorkspaceGroup, "items"> {
  const scopeTag = item.scopeTags
    .map((tag) => String(tag || "").trim())
    .find((tag) => tag.startsWith("project:") || tag.startsWith("workspace:")) || "";
  const scope = item.resolvedScope || scopeTag;
  const creationBinding = explicitCreationBinding(item);
  const displayLabel = item.workspaceDisplayName || item.projectName;
  const workspacePinned = Boolean(item.workspacePinned);
  const workspacePinnedAt = item.workspacePinnedAt;

  if (item.projectId || scope.startsWith("project:")) {
    const id = item.projectId || scope;
    return {
      key: `project:${id}`,
      label: displayLabel || labelFromProjectId(id),
      kind: "project",
      creationBinding: item.projectId ? creationBinding : null,
      workspacePath: item.workspacePath,
      pinned: workspacePinned,
      pinnedAt: workspacePinnedAt,
    };
  }

  if (item.workspacePath) {
    const key = normalizePathKey(item.workspacePath);
    const fallback = scope.startsWith("workspace:external:") ? labels.externalWorkspace : labels.workspace;
    return {
      key: `workspace:path:${key}`,
      label: displayLabel || (scope === "workspace:main" ? labels.mainWorkspace : basenameFromPath(item.workspacePath) || fallback),
      kind: "workspace",
      creationBinding,
      workspacePath: item.workspacePath,
      pinned: workspacePinned,
      pinnedAt: workspacePinnedAt,
    };
  }

  if (item.workspaceId || scope.startsWith("workspace:")) {
    const id = item.workspaceId || scope.replace(/^workspace:/i, "");
    return {
      key: `workspace:${id || scope}`,
      label: displayLabel || (id === "main" || scope === "workspace:main" ? labels.mainWorkspace : id || labels.workspace),
      kind: "workspace",
      creationBinding: item.workspaceId ? creationBinding : null,
      workspacePath: item.workspacePath,
      pinned: workspacePinned,
      pinnedAt: workspacePinnedAt,
    };
  }

  return {
    key: "unbound",
    label: labels.unbound,
    kind: "unbound",
    creationBinding: null,
    pinned: false,
  };
}

export function groupSessionHistoryByWorkspace<T extends AuthoritativeSessionHistoryRecord>(
  items: T[],
  labels: SessionHistoryWorkspaceGroupLabels,
): SessionHistoryWorkspaceGroup<T>[] {
  const groups = new Map<string, SessionHistoryWorkspaceGroup<T>>();
  for (const item of items) {
    const identity = workspaceGroupIdentity(item, labels);
    const existing = groups.get(identity.key);
    if (existing) {
      existing.items.push(item);
      if (identity.creationBinding) {
        existing.creationBinding = existing.creationBinding
          ? {
              ...identity.creationBinding,
              ...existing.creationBinding,
              scopeMode: "explicit",
            }
          : identity.creationBinding;
      }
      if (!existing.workspacePath && identity.workspacePath) {
        existing.workspacePath = identity.workspacePath;
      }
      if (identity.pinned && !existing.pinned) {
        existing.pinned = true;
        existing.pinnedAt = identity.pinnedAt;
      }
      if (!existing.label && identity.label) {
        existing.label = identity.label;
      }
    } else {
      groups.set(identity.key, { ...identity, items: [item] });
    }
  }
  return Array.from(groups.values()).map((group) => ({
    ...group,
    items: sortHistoryItems(group.items) as T[],
  })).sort((left, right) => {
    if (left.pinned !== right.pinned) {
      return left.pinned ? -1 : 1;
    }
    const pinnedOrder = String(right.pinnedAt || "").localeCompare(String(left.pinnedAt || ""));
    if (pinnedOrder !== 0) {
      return pinnedOrder;
    }
    const leftTime = left.items[0]?.historySortAt || left.items[0]?.createdAt || "";
    const rightTime = right.items[0]?.historySortAt || right.items[0]?.createdAt || "";
    return rightTime.localeCompare(leftTime);
  });
}
