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

function deriveSourceGroup(parsedMetadata: Record<string, unknown>, record: Record<string, unknown>): SessionHistorySourceGroup {
    const source = String(
        parsedMetadata.source
        || parsedMetadata.trigger_source
        || parsedMetadata.triggerSource
        || record.source
        || "web"
    ).trim().toLowerCase();
    if (source === "cron") return "cron";
    if (source.startsWith("hook")) return "hooks";
    if (source && source !== "web" && source !== "session_list") return "channels";
    return "web";
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
        sourceGroup: deriveSourceGroup(parsedMetadata, record),
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
