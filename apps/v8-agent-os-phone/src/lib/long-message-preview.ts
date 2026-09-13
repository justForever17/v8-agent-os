import type { ChatMessage } from "@/src/types/admin";

export function shouldCollapseLongMessage(message: ChatMessage, active: boolean) {
    return !active && message.role === "assistant"
        && (String(message.content || "").length > 32_000 || (message.nodes?.length || 0) > 80);
}

export function completeMessageText(message: ChatMessage) {
    if (message.content) return String(message.content);
    return (message.nodes || []).map((node) => "content" in node && typeof node.content === "string" ? node.content : "").filter(Boolean).join("\n\n");
}
