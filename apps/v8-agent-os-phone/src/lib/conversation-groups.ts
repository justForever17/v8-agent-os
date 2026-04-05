import type { ConversationSummary } from "@/src/types/admin";
import type { LocaleCode } from "@/src/providers/ui-prefs";

export type ConversationGroupKey = "channels" | "cron" | "hooks" | "web";

export const conversationGroupOrder: ConversationGroupKey[] = [
    "channels",
    "cron",
    "hooks",
    "web",
];

export const conversationGroupLabels: Record<ConversationGroupKey, string> = {
    channels: "第三方渠道",
    cron: "定时任务",
    hooks: "触发器",
    web: "网页对话",
};

const conversationGroupLabelMap: Record<ConversationGroupKey, { zh: string; en: string }> = {
    channels: { zh: "第三方渠道", en: "Channels" },
    cron: { zh: "定时任务", en: "Cron" },
    hooks: { zh: "触发器", en: "Hooks" },
    web: { zh: "网页对话", en: "Web chat" },
};

export function getConversationGroupLabel(key: ConversationGroupKey, locale: LocaleCode = "zh-CN") {
    return locale === "en" ? conversationGroupLabelMap[key].en : conversationGroupLabelMap[key].zh;
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
