import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { readCompleteQueue, reconcileQueueSnapshot } from "@/lib/queue-snapshot";
import { isVisibleQueuedMessage, sortQueuedMessages, type QueuedChatMessage } from "@/lib/chat-queue";

type UseChatQueueOptions = {
    activeConversationId: string | null;
    activeConversationIdRef: MutableRefObject<string | null>;
    latestRealtimeSeqRef: MutableRefObject<number>;
    ownerKey: string;
};

export function useChatQueue({ activeConversationId, activeConversationIdRef, latestRealtimeSeqRef, ownerKey }: UseChatQueueOptions) {
    const [queuedMessages, setQueuedMessages] = useState<QueuedChatMessage[]>([]);
    const queueCacheRef = useRef(new Map<string, QueuedChatMessage[]>());
    const queueSequenceRef = useRef(new Map<string, number>());
    const queuedMessagesRef = useRef(queuedMessages);
    queuedMessagesRef.current = queuedMessages;
    const queuedMessagesSessionIdRef = useRef<string | null>(activeConversationId);
    queuedMessagesSessionIdRef.current = activeConversationId;
    const queueSyncControllerRef = useRef<AbortController | null>(null);
    const [queuedMessagesCollapsed, setQueuedMessagesCollapsed] = useState(false);
    const [queuedMessageMenuId, setQueuedMessageMenuId] = useState<string | null>(null);
    const [queuedMessageBusyId, setQueuedMessageBusyId] = useState("");
    const [editingQueuedMessage, setEditingQueuedMessage] = useState<QueuedChatMessage | null>(null);
    const [queuedMessageEditText, setQueuedMessageEditText] = useState("");
    const [queuedMessageEditBusy, setQueuedMessageEditBusy] = useState(false);
    const [queuedMessageError, setQueuedMessageError] = useState("");

    const upsertQueuedMessage = useCallback((incoming: QueuedChatMessage | null) => {
        const sessionId = incoming?.sessionId;
        if (!incoming || !sessionId) return;
        const current = sessionId === queuedMessagesSessionIdRef.current
            ? queuedMessagesRef.current
            : queueCacheRef.current.get(sessionId) || [];
        const next = sortQueuedMessages([...current.filter((item) => item.id !== incoming.id), incoming]);
        queueCacheRef.current.set(sessionId, next);
        if (sessionId === queuedMessagesSessionIdRef.current) {
            queuedMessagesRef.current = next;
            setQueuedMessages(next);
        }
    }, []);

    const applyQueuedMessagesSnapshot = useCallback((incoming: QueuedChatMessage[] | null, expectedSessionId?: string | null, sequence = 0, complete = false) => {
        const sessionId = expectedSessionId || queuedMessagesSessionIdRef.current;
        if (!incoming || !sessionId || sessionId !== queuedMessagesSessionIdRef.current) return;
        const knownSequence = Math.max(queueSequenceRef.current.get(sessionId) || 0, latestRealtimeSeqRef.current);
        if (sequence < knownSequence) return;
        const next = reconcileQueueSnapshot(queuedMessagesRef.current, incoming.filter((item) => item.sessionId === sessionId), sequence, knownSequence, complete);
        queueSequenceRef.current.set(sessionId, sequence);
        queueCacheRef.current.set(sessionId, next);
        queuedMessagesRef.current = next;
        setQueuedMessages(sortQueuedMessages(next));
        setQueuedMessageError("");
    }, [latestRealtimeSeqRef]);

    const visibleQueuedMessages = useMemo(
        () => sortQueuedMessages(queuedMessages.filter((item) => item.sessionId === activeConversationId && isVisibleQueuedMessage(item))),
        [activeConversationId, queuedMessages],
    );

    const synchronizeQueue = useCallback(async (sessionId: string) => {
        if (!sessionId || sessionId !== activeConversationIdRef.current || queueSyncControllerRef.current) return;
        const controller = new AbortController();
        queueSyncControllerRef.current = controller;
        try {
            const result = await readCompleteQueue<QueuedChatMessage>(sessionId, async (cursor) => {
                const query = new URLSearchParams({ session_id: sessionId });
                if (cursor !== null) query.set("after_ordinal", String(cursor));
                const response = await fetch(`/api/chat-queue?${query}`, { cache: "no-store", signal: controller.signal });
                if (!response.ok) throw new Error("Queue sync failed");
                return await response.json();
            }, (sequence) => !controller.signal.aborted
                && activeConversationIdRef.current === sessionId
                && sequence >= latestRealtimeSeqRef.current);
            if (!controller.signal.aborted) applyQueuedMessagesSnapshot(result.items, sessionId, result.sequence, true);
        } catch {
            if (!controller.signal.aborted && activeConversationIdRef.current === sessionId) setQueuedMessageError("队列同步未完成，保留上次状态。");
        } finally {
            if (queueSyncControllerRef.current === controller) queueSyncControllerRef.current = null;
        }
    }, [activeConversationIdRef, applyQueuedMessagesSnapshot, latestRealtimeSeqRef]);

    const resetQueueUi = useCallback(() => {
        setQueuedMessages([]);
        setQueuedMessagesCollapsed(false);
        setQueuedMessageMenuId(null);
        setEditingQueuedMessage(null);
        setQueuedMessageEditText("");
        setQueuedMessageEditBusy(false);
        setQueuedMessageBusyId("");
        setQueuedMessageError("");
    }, []);

    useEffect(() => () => {
        queueSyncControllerRef.current?.abort();
        queueSyncControllerRef.current = null;
    }, [ownerKey, activeConversationId]);

    return {
        queuedMessages,
        setQueuedMessages,
        queuedMessagesRef,
        queueCacheRef,
        queueSequenceRef,
        queuedMessagesSessionIdRef,
        queuedMessagesCollapsed,
        setQueuedMessagesCollapsed,
        queuedMessageMenuId,
        setQueuedMessageMenuId,
        queuedMessageBusyId,
        setQueuedMessageBusyId,
        editingQueuedMessage,
        setEditingQueuedMessage,
        queuedMessageEditText,
        setQueuedMessageEditText,
        queuedMessageEditBusy,
        setQueuedMessageEditBusy,
        queuedMessageError,
        setQueuedMessageError,
        visibleQueuedMessages,
        upsertQueuedMessage,
        applyQueuedMessagesSnapshot,
        synchronizeQueue,
        resetQueueUi,
    };
}

