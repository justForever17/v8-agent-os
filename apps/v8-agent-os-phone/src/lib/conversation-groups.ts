import type { ConversationSummary } from "@/src/types/admin";
import type { LocaleCode } from "@/src/providers/ui-prefs";
import { createTranslator, type TranslationKey } from "@/src/lib/locale";
import {
    groupSessionHistoryByWorkspace,
    type SessionHistoryWorkspaceGroup,
} from "@v8/session-realtime";

export type ConversationGroupKey = "channels" | "cron" | "hooks" | "web";
export type ConversationActivityState = "active" | "failed" | null;
export type ConversationWorkspaceGroup = SessionHistoryWorkspaceGroup<ConversationSummary>;

export const conversationGroupOrder: ConversationGroupKey[] = [
    "channels",
    "cron",
    "hooks",
    "web",
];

export const conversationGroupLabels: Record<ConversationGroupKey, TranslationKey> = {
    channels: "shared.history.group.channels",
    cron: "shared.history.group.cron",
    hooks: "shared.history.group.hooks",
    web: "shared.history.group.web",
};

export function getConversationGroupLabel(key: ConversationGroupKey, locale: LocaleCode = "zh-CN") {
    return createTranslator(locale)(conversationGroupLabels[key]);
}

export function groupConversations(items: ConversationSummary[]) {
    const grouped: Record<ConversationGroupKey, ConversationSummary[]> = {
        channels: [],
        cron: [],
        hooks: [],
        web: [],
    };

    for (const item of items) {
        const key = String(item.sourceGroup || "").trim().toLowerCase();
        if (key === "channels" || key === "cron" || key === "hooks") {
            grouped[key].push(item);
        } else {
            grouped.web.push(item);
        }
    }

    return grouped;
}

export function groupConversationsByWorkspace(items: ConversationSummary[], locale: LocaleCode = "zh-CN"): ConversationWorkspaceGroup[] {
    const t = createTranslator(locale);
    return groupSessionHistoryByWorkspace(items, {
        mainWorkspace: t("shared.history.workspace.main"),
        externalWorkspace: t("shared.history.workspace.external"),
        unbound: t("shared.history.workspace.unbound"),
        workspace: t("shared.history.workspace.generic"),
    });
}

export function getConversationActivityState(item: ConversationSummary): ConversationActivityState {
    const status = String(item.workflowStatus || item.status || "").trim().toLowerCase();
    if (["running", "queued", "pending", "starting", "streaming", "waiting_input", "waiting_approval"].includes(status)) {
        return "active";
    }
    if (["failed", "cancelled", "degraded"].includes(status)) {
        return "failed";
    }
    return null;
}
