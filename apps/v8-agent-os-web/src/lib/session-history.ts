export type SessionHistorySourceGroup = "web" | "channels" | "cron" | "hooks";

export interface SessionHistoryControls {
    canResume?: boolean;
    canRetry?: boolean;
    canInterrupt?: boolean;
    canOpenApproval?: boolean;
}

export interface SessionHistoryItem {
    id: string;
    title: string;
    updatedAt?: string;
    updated_at?: string;
    lastActivityAt?: string;
    metadata?: string | Record<string, unknown>;
    parsedMetadata?: Record<string, unknown>;
    sourceGroup: SessionHistorySourceGroup;
    channelType?: string;
    channelName?: string;
    channelDomain?: string;
    chatType?: string;
    accountId?: string;
    defaultAccount?: string;
    workflowStatus?: string;
    statusLabel?: string;
    stepStatus?: string;
    recoverable?: boolean;
    ownerRuntime?: string;
    ownerAgentId?: string;
    currentStepId?: string;
    currentStepKey?: string;
    currentStepTitle?: string;
    previewExcerpt?: string;
    lastNarrativeExcerpt?: string;
    lastRuntimeSummary?: string;
    hasDurablePreview?: boolean;
    pendingApprovalCount?: number;
    hasPendingApproval?: boolean;
    controls?: SessionHistoryControls;
    scopeTags: string[];
}

function parseMetadata(metadata: SessionHistoryItem["metadata"]): Record<string, unknown> {
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

function normalizeSourceGroup(value: unknown): SessionHistorySourceGroup | "" {
    const normalized = String(value || "").trim().toLowerCase();
    if (!normalized) return "";
    if (normalized === "cron") return "cron";
    if (normalized === "hooks") return "hooks";
    if (normalized === "channels") return "channels";
    if (normalized === "web") return "web";
    return "";
}

function readServerSourceGroup(record: Record<string, unknown>): SessionHistorySourceGroup {
    const summary = record.summary && typeof record.summary === "object"
        ? record.summary as Record<string, unknown>
        : {};
    return normalizeSourceGroup(
        record.sourceGroup
        || record.source_group
        || summary.sourceGroup
        || summary.source_group,
    ) || "web";
}

function deriveChannelValue(
    record: Record<string, unknown>,
    parsedMetadata: Record<string, unknown>,
    keys: string[],
) {
    for (const key of keys) {
        const value = record[key];
        if (typeof value === "string" && value.trim()) {
            return value.trim();
        }
    }
    for (const key of keys) {
        const value = parsedMetadata[key];
        if (typeof value === "string" && value.trim()) {
            return value.trim();
        }
    }
    return undefined;
}

function deriveScopeTags(parsedMetadata: Record<string, unknown>, record: Record<string, unknown>): string[] {
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

function coerceString(value: unknown): string | undefined {
    const normalized = String(value || "").trim();
    return normalized || undefined;
}

function sortHistoryItems(items: SessionHistoryItem[]): SessionHistoryItem[] {
    return [...items].sort((left, right) => {
        const leftTs = left.lastActivityAt || left.updatedAt || left.updated_at || "";
        const rightTs = right.lastActivityAt || right.updatedAt || right.updated_at || "";
        return rightTs.localeCompare(leftTs);
    });
}

export function normalizeSessionHistoryItem(raw: unknown): SessionHistoryItem {
    const record = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
    const parsedMetadata = parseMetadata(record.metadata as SessionHistoryItem["metadata"]);
    const previewExcerpt = coerceString(record.previewExcerpt) || coerceString(record.lastNarrativeExcerpt);

    return {
        id: coerceString(record.id) || "",
        title: coerceString(record.title) || "新对话",
        updatedAt: coerceString(record.updatedAt),
        updated_at: coerceString(record.updated_at),
        lastActivityAt: coerceString(record.lastActivityAt) || coerceString(record.updatedAt) || coerceString(record.updated_at),
        metadata: (record.metadata as SessionHistoryItem["metadata"]) || parsedMetadata,
        parsedMetadata,
        sourceGroup: readServerSourceGroup(record),
        channelType: deriveChannelValue(record, parsedMetadata, ["channelType", "channel_type"]),
        channelName: deriveChannelValue(record, parsedMetadata, ["channelName", "channel_name"]),
        channelDomain: deriveChannelValue(record, parsedMetadata, ["channelDomain", "channel_domain"]),
        chatType: deriveChannelValue(record, parsedMetadata, ["chatType", "chat_type"]),
        accountId: deriveChannelValue(record, parsedMetadata, ["accountId", "account_id"]),
        defaultAccount: deriveChannelValue(record, parsedMetadata, ["defaultAccount", "default_account"]),
        workflowStatus: coerceString(record.workflowStatus),
        statusLabel: coerceString(record.statusLabel),
        stepStatus: coerceString(record.stepStatus),
        recoverable: Boolean(record.recoverable),
        ownerRuntime: coerceString(record.ownerRuntime),
        ownerAgentId: coerceString(record.ownerAgentId),
        currentStepId: coerceString(record.currentStepId),
        currentStepKey: coerceString(record.currentStepKey),
        currentStepTitle: coerceString(record.currentStepTitle),
        previewExcerpt,
        lastNarrativeExcerpt: coerceString(record.lastNarrativeExcerpt) || previewExcerpt,
        lastRuntimeSummary: coerceString(record.lastRuntimeSummary),
        hasDurablePreview: Boolean(record.hasDurablePreview),
        pendingApprovalCount: Number(record.pendingApprovalCount || 0) || 0,
        hasPendingApproval: Boolean(record.hasPendingApproval),
        controls: (record.controls as SessionHistoryControls) || undefined,
        scopeTags: deriveScopeTags(parsedMetadata, record),
    };
}

export function normalizeSessionHistoryList(raw: unknown[]): SessionHistoryItem[] {
    return sortHistoryItems(
        raw
            .map((item) => normalizeSessionHistoryItem(item))
            .filter((item) => item.parsedMetadata?.hiddenFromHistory !== true),
    );
}

export function mergeSessionHistoryOverlay(
    current: SessionHistoryItem,
    patch: Partial<SessionHistoryItem>,
): SessionHistoryItem {
    const mergedMetadata = patch.parsedMetadata
        ? { ...(current.parsedMetadata || {}), ...(patch.parsedMetadata || {}) }
        : current.parsedMetadata;
    const next = normalizeSessionHistoryItem({
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

export function sortSessionHistory(items: SessionHistoryItem[]): SessionHistoryItem[] {
    return sortHistoryItems(items);
}
