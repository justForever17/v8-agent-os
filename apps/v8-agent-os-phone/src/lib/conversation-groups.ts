import type { ConversationSummary } from "@/src/types/admin";
import type { LocaleCode } from "@/src/providers/ui-prefs";
import { createTranslator, type TranslationKey } from "@/src/lib/locale";

export type ConversationGroupKey = "channels" | "cron" | "hooks" | "web";

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
