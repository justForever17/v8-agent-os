import type { ConversationSummary } from "@/src/types/admin";

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
