import type {
    AuthoritativeSessionHistoryRecord,
    SessionHistoryControls,
    SessionHistorySourceGroup,
} from "@v8/session-realtime/history";
import {
    mergeAuthoritativeSessionHistoryRecord,
    normalizeAuthoritativeSessionHistoryList,
    normalizeAuthoritativeSessionHistoryRecord,
    sortAuthoritativeSessionHistory,
} from "@v8/session-realtime/history";

export type SessionHistoryItem = AuthoritativeSessionHistoryRecord;
export type { SessionHistoryControls, SessionHistorySourceGroup };

export const mergeSessionHistoryOverlay = mergeAuthoritativeSessionHistoryRecord;
export const normalizeSessionHistoryList = normalizeAuthoritativeSessionHistoryList;
export const normalizeSessionHistoryItem = normalizeAuthoritativeSessionHistoryRecord;
export const sortSessionHistory = sortAuthoritativeSessionHistory;

function sessionIdOf(item: SessionHistoryItem) {
    return String(item.sessionId || item.id || "").trim();
}

function isPlaceholderSessionTitle(value: unknown) {
    const title = String(value || "").trim();
    return !title || title === "New Chat" || title === "新对话";
}

export function preserveLiveSessionTitles(
    current: SessionHistoryItem[],
    incoming: SessionHistoryItem[],
) {
    const currentById = new Map(current.map((item) => [sessionIdOf(item), item]));
    return incoming.map((item) => {
        const previous = currentById.get(sessionIdOf(item));
        if (
            previous
            && isPlaceholderSessionTitle(item.title)
            && !isPlaceholderSessionTitle(previous.title)
        ) {
            return mergeAuthoritativeSessionHistoryRecord(item, { title: previous.title });
        }
        return item;
    });
}
