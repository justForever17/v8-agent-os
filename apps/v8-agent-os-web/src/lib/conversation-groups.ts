import type { Conversation } from "@/context/ConversationContext";
import {
    groupSessionHistoryByWorkspace,
    type SessionHistoryWorkspaceGroup,
    type SessionHistoryWorkspaceGroupLabels,
} from "@v8/session-realtime/history";

export type ConversationActivityState = "active" | "failed" | null;

export type ConversationWorkspaceGroup = SessionHistoryWorkspaceGroup<Conversation>;
export type ConversationWorkspaceGroupLabels = SessionHistoryWorkspaceGroupLabels;

export function groupConversationsByWorkspace(
    conversations: Conversation[],
    labels: ConversationWorkspaceGroupLabels,
): ConversationWorkspaceGroup[] {
    return groupSessionHistoryByWorkspace(conversations, labels);
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
