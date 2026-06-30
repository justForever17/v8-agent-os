import type { Conversation } from "@/context/ConversationContext";

export type ConversationActivityState = "active" | "failed" | null;

export type ConversationWorkspaceGroup = {
    key: string;
    label: string;
    kind: "project" | "workspace" | "unbound";
    items: Conversation[];
};

export type ConversationWorkspaceGroupLabels = {
    mainWorkspace: string;
    externalWorkspace: string;
    unbound: string;
    workspace: string;
};

function readMetadata(item: Conversation): Record<string, unknown> {
    if (item.parsedMetadata && typeof item.parsedMetadata === "object" && !Array.isArray(item.parsedMetadata)) {
        return item.parsedMetadata as Record<string, unknown>;
    }
    if (item.metadata && typeof item.metadata === "object" && !Array.isArray(item.metadata)) {
        return item.metadata as Record<string, unknown>;
    }
    if (typeof item.metadata === "string") {
        try {
            const parsed = JSON.parse(item.metadata);
            return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
        } catch {
            return {};
        }
    }
    return {};
}

function readString(item: Conversation, keys: string[]): string {
    const record = item as unknown as Record<string, unknown>;
    const metadata = readMetadata(item);
    for (const key of keys) {
        const value = record[key] ?? metadata[key];
        const normalized = String(value || "").trim();
        if (normalized) return normalized;
    }
    return "";
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

function groupIdentity(item: Conversation, labels: ConversationWorkspaceGroupLabels): Omit<ConversationWorkspaceGroup, "items"> {
    const projectId = readString(item, ["projectId", "project_id"]);
    const workspacePath = readString(item, ["workspacePath", "workspace_path"]);
    const workspaceId = readString(item, ["workspaceId", "workspace_id"]);
    const resolvedScope = readString(item, ["resolvedScope", "resolved_scope"]);
    const scopeTag = (Array.isArray(item.scopeTags) ? item.scopeTags : [])
        .map((tag) => String(tag || "").trim())
        .find((tag) => tag.startsWith("project:") || tag.startsWith("workspace:")) || "";
    const scope = resolvedScope || scopeTag;

    if (projectId || scope.startsWith("project:")) {
        const id = projectId || scope;
        return { key: `project:${id}`, label: labelFromProjectId(id), kind: "project" };
    }

    if (workspacePath) {
        const key = normalizePathKey(workspacePath);
        if (scope === "workspace:main") {
            return { key: `workspace:path:${key}`, label: labels.mainWorkspace, kind: "workspace" };
        }
        const fallback = scope.startsWith("workspace:external:") ? labels.externalWorkspace : labels.workspace;
        return { key: `workspace:path:${key}`, label: basenameFromPath(workspacePath) || fallback, kind: "workspace" };
    }

    if (workspaceId || scope.startsWith("workspace:")) {
        const id = workspaceId || scope.replace(/^workspace:/i, "");
        const label = id === "main" || scope === "workspace:main" ? labels.mainWorkspace : id || labels.workspace;
        return { key: `workspace:${id || scope}`, label, kind: "workspace" };
    }

    return { key: "unbound", label: labels.unbound, kind: "unbound" };
}

export function groupConversationsByWorkspace(
    conversations: Conversation[],
    labels: ConversationWorkspaceGroupLabels,
): ConversationWorkspaceGroup[] {
    const groups = new Map<string, ConversationWorkspaceGroup>();
    for (const item of conversations) {
        const identity = groupIdentity(item, labels);
        const existing = groups.get(identity.key);
        if (existing) {
            existing.items.push(item);
        } else {
            groups.set(identity.key, { ...identity, items: [item] });
        }
    }
    return Array.from(groups.values()).sort((left, right) => {
        const leftTime = left.items[0]?.historySortAt || left.items[0]?.createdAt || "";
        const rightTime = right.items[0]?.historySortAt || right.items[0]?.createdAt || "";
        return rightTime.localeCompare(leftTime);
    });
}

export function getConversationActivityState(item: Conversation): ConversationActivityState {
    const status = String(item.workflowStatus || item.status || "").trim().toLowerCase();
    if (["running", "queued", "pending", "starting", "streaming", "waiting_input", "waiting_approval"].includes(status)) {
        return "active";
    }
    if (["failed", "cancelled", "degraded"].includes(status)) {
        return "failed";
    }
    return null;
}
