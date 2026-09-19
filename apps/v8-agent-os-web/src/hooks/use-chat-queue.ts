import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { readCompleteQueue, reconcileQueueSnapshot } from "@/lib/queue-snapshot";
import { isVisibleQueuedMessage, normalizeQueuedMessage, sortQueuedMessages, type QueuedChatMessage } from "@/lib/chat-queue";

type QueueScope = { ownerKey: string; sessionId: string | null };
type QueueDraft = { item: QueuedChatMessage; text: string };
type QueueMutation = {
    kind: "promote" | "cancel" | "edit";
    item: QueuedChatMessage;
    scope: QueueScope;
    controller: AbortController;
};

type UseChatQueueOptions = {
    activeConversationId: string | null;
    activeConversationIdRef: MutableRefObject<string | null>;
    latestRealtimeSeqRef: MutableRefObject<number>;
    ownerKey: string;
    translate: (key: string) => string;
};

/** Own queue cache, synchronization and mutation lifetimes for one signed-in instance. */
export function useChatQueue({ activeConversationId, activeConversationIdRef, latestRealtimeSeqRef, ownerKey, translate }: UseChatQueueOptions) {
    const scopeRef = useRef<QueueScope>({ ownerKey, sessionId: activeConversationId });
    scopeRef.current = { ownerKey, sessionId: activeConversationId };
    const cacheOwnerRef = useRef(ownerKey);
    const cacheRef = useRef(new Map<string, QueuedChatMessage[]>());
    const sequenceRef = useRef(new Map<string, number>());
    const syncRequestRef = useRef<AbortController | null>(null);
    const mutationRef = useRef<QueueMutation | null>(null);
    const draftRef = useRef<QueueDraft | null>(null);
    const [messages, setMessages] = useState<QueuedChatMessage[]>([]);
    const [collapsed, setCollapsed] = useState(false);
    const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
    const [draft, setDraft] = useState<QueueDraft | null>(null);
    const [mutation, setMutation] = useState<QueueMutation | null>(null);
    const [error, setError] = useState("");

    const isCurrentScope = useCallback((scope: QueueScope) => (
        scopeRef.current.ownerKey === scope.ownerKey
        && scopeRef.current.sessionId === scope.sessionId
        && activeConversationIdRef.current === scope.sessionId
    ), [activeConversationIdRef]);

    const updateDraft = useCallback((next: QueueDraft | null) => {
        draftRef.current = next;
        setDraft(next);
    }, []);

    const releaseMutation = useCallback((request: QueueMutation) => {
        if (mutationRef.current !== request) return;
        mutationRef.current = null;
        setMutation(null);
    }, []);

    const abortRequests = useCallback(() => {
        syncRequestRef.current?.abort();
        syncRequestRef.current = null;
        mutationRef.current?.controller.abort();
        mutationRef.current = null;
    }, []);

    const reset = useCallback(() => {
        abortRequests();
        setMutation(null);
        updateDraft(null);
        setCollapsed(false);
        setMenuOpenId(null);
        setError("");
    }, [abortRequests, updateDraft]);

    useEffect(() => {
        if (cacheOwnerRef.current !== ownerKey) {
            cacheOwnerRef.current = ownerKey;
            cacheRef.current.clear();
            sequenceRef.current.clear();
        }
        reset();
        setMessages(cacheRef.current.get(activeConversationId || "") || []);
        return abortRequests;
    }, [abortRequests, activeConversationId, ownerKey, reset]);

    const publish = useCallback((sessionId: string, next: QueuedChatMessage[]) => {
        cacheRef.current.set(sessionId, next);
        if (scopeRef.current.sessionId === sessionId) setMessages(next);
    }, []);

    const upsert = useCallback((incoming: QueuedChatMessage | null) => {
        if (!incoming?.sessionId) return;
        const current = cacheRef.current.get(incoming.sessionId) || [];
        publish(incoming.sessionId, sortQueuedMessages([
            ...current.filter((item) => item.id !== incoming.id), incoming,
        ]));
    }, [publish]);

    const removeMessage = useCallback((id: string) => {
        const sessionId = scopeRef.current.sessionId;
        if (!sessionId) return;
        publish(sessionId, (cacheRef.current.get(sessionId) || []).filter((item) => item.id !== id));
    }, [publish]);

    const applySnapshot = useCallback((incoming: QueuedChatMessage[] | null, expectedSessionId?: string | null, sequence = 0, complete = false) => {
        const sessionId = expectedSessionId || scopeRef.current.sessionId;
        if (!incoming || !sessionId || sessionId !== scopeRef.current.sessionId) return;
        const knownSequence = Math.max(sequenceRef.current.get(sessionId) || 0, latestRealtimeSeqRef.current);
        if (sequence < knownSequence) return;
        const next = reconcileQueueSnapshot(
            cacheRef.current.get(sessionId) || [],
            incoming.filter((item) => item.sessionId === sessionId),
            sequence,
            knownSequence,
            complete,
        );
        sequenceRef.current.set(sessionId, sequence);
        publish(sessionId, sortQueuedMessages(next));
        setError("");
    }, [latestRealtimeSeqRef, publish]);

    const synchronize = useCallback(async (sessionId: string) => {
        const scope = scopeRef.current;
        if (!sessionId || sessionId !== scope.sessionId || !isCurrentScope(scope) || syncRequestRef.current) return;
        const controller = new AbortController();
        syncRequestRef.current = controller;
        try {
            const result = await readCompleteQueue<QueuedChatMessage>(sessionId, async (cursor) => {
                const query = new URLSearchParams({ session_id: sessionId });
                if (cursor !== null) query.set("after_ordinal", String(cursor));
                const response = await fetch(`/api/chat-queue?${query}`, { cache: "no-store", signal: controller.signal });
                if (!response.ok) throw new Error("Queue sync failed");
                return await response.json();
            }, (sequence) => !controller.signal.aborted && isCurrentScope(scope) && sequence >= latestRealtimeSeqRef.current);
            if (!controller.signal.aborted && isCurrentScope(scope)) applySnapshot(result.items, sessionId, result.sequence, true);
        } catch {
            if (!controller.signal.aborted && isCurrentScope(scope)) setError("队列同步未完成，保留上次状态。");
        } finally {
            if (syncRequestRef.current === controller) syncRequestRef.current = null;
        }
    }, [applySnapshot, isCurrentScope, latestRealtimeSeqRef]);

    const closeEditor = useCallback(() => {
        const request = mutationRef.current;
        if (request?.kind === "edit") {
            request.controller.abort();
            releaseMutation(request);
        }
        updateDraft(null);
    }, [releaseMutation, updateDraft]);

    const openEditor = useCallback((item: QueuedChatMessage) => {
        if (item.sessionId !== scopeRef.current.sessionId || String(item.state || "pending").trim().toLowerCase() !== "pending") return;
        closeEditor();
        setMenuOpenId(null);
        updateDraft({ item, text: item.content || "" });
    }, [closeEditor, updateDraft]);

    const setEditText = useCallback((text: string) => {
        if (draftRef.current) updateDraft({ ...draftRef.current, text });
    }, [updateDraft]);

    const mutate = useCallback(async (kind: QueueMutation["kind"], item: QueuedChatMessage, submittedDraft: QueueDraft | null = null) => {
        const scope = scopeRef.current;
        const content = submittedDraft?.text.trim();
        if (!item.id || !item.sessionId || item.sessionId !== scope.sessionId || !isCurrentScope(scope)
            || mutationRef.current || (kind === "edit" && !content)) return;
        const request: QueueMutation = { kind, item, scope, controller: new AbortController() };
        mutationRef.current = request;
        setMutation(request);
        setError("");
        setMenuOpenId(null);
        const current = () => mutationRef.current === request && isCurrentScope(scope) && !request.controller.signal.aborted;
        try {
            const response = await fetch(`/api/chat-queue/${encodeURIComponent(item.id)}${kind === "promote" ? "/promote" : ""}`, {
                method: kind === "edit" ? "PATCH" : kind === "cancel" ? "DELETE" : "POST",
                ...(kind === "edit" ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content }) } : {}),
                signal: request.controller.signal,
            });
            const payload = await response.json();
            if (!current()) return;
            if (!response.ok || payload?.ok === false) {
                throw new Error(typeof payload?.error === "string" ? payload.error : typeof payload?.detail === "string" ? payload.detail : "");
            }
            // All three Engine routes return the persisted queue record. A
            // missing/wrong receipt must not manufacture a successful mutation.
            const receipt = normalizeQueuedMessage(payload?.queuedMessage);
            if (!receipt || receipt.id !== item.id || receipt.sessionId !== item.sessionId) {
                throw new Error("Queue response does not match the requested message.");
            }
            upsert(receipt);
            if (kind === "edit" && draftRef.current === submittedDraft) updateDraft(null);
        } catch (cause) {
            if (current()) {
                const fallback = kind === "promote" ? "web.generated.8f1e4072ac" : kind === "cancel" ? "web.generated.5c2e41d9a8" : "web.generated.76ac182bf4";
                setError(cause instanceof Error && cause.message ? cause.message : translate(fallback));
            }
        } finally {
            releaseMutation(request);
        }
    }, [isCurrentScope, releaseMutation, translate, updateDraft, upsert]);

    const promote = useCallback((item: QueuedChatMessage) => mutate("promote", item), [mutate]);
    const cancel = useCallback((item: QueuedChatMessage) => mutate("cancel", item), [mutate]);
    const saveEdit = useCallback(async () => {
        const current = draftRef.current;
        if (current) await mutate("edit", current.item, current);
    }, [mutate]);
    const expand = useCallback(() => setCollapsed(false), []);
    const toggleCollapsed = useCallback(() => setCollapsed((current) => !current), []);
    const visibleMessages = useMemo(() => messages.filter((item) => (
        cacheOwnerRef.current === ownerKey && item.sessionId === activeConversationId && isVisibleQueuedMessage(item)
    )), [activeConversationId, messages, ownerKey]);

    return {
        view: {
            visibleMessages, collapsed, menuOpenId, error,
            busyId: mutation?.item.id || "",
            editingItem: draft?.item || null,
            editText: draft?.text || "",
            editBusy: mutation?.kind === "edit",
        },
        commands: {
            upsert, applySnapshot, synchronize, removeMessage,
            promote, cancel, openEditor, closeEditor, setEditText, saveEdit,
            expand, toggleCollapsed, reset, openMenu: setMenuOpenId, setError,
        },
    };
}
