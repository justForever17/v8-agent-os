"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, ReactNode } from 'react';
import { useSession } from "next-auth/react";
import type { SupervisorRuntimeMode } from "@v8/session-realtime";
import {
    SessionHistoryItem,
    mergeSessionHistoryOverlay,
    normalizeSessionHistoryItem,
    normalizeSessionHistoryList,
    preserveLiveSessionTitles,
    sortSessionHistory,
} from "@/lib/session-history";
import {
    clearWebSessionIndexCache,
    readWebSessionIndexCache,
    writeWebSessionIndexCache,
} from "@/lib/web-conversation-cache";

export type Conversation = SessionHistoryItem;

export interface ConversationPresentationPatch {
    title?: string;
    pinned?: boolean;
    supervisorWorkMode?: "daily" | "engineering";
    supervisorRuntimeMode?: SupervisorRuntimeMode;
}

export interface ConversationPresentationOptions {
    applyResponse?: boolean;
}

export interface CreateConversationPayload {
    title?: string;
    projectId?: string;
    workspaceId?: string;
    workspacePath?: string;
    threadId?: string;
    scopeHint?: string;
    scopeMode?: string;
}

interface ConversationContextType {
    conversations: Conversation[];
    isLoading: boolean;
    refreshConversations: () => Promise<void>;
    createConversation: (payload?: CreateConversationPayload) => Promise<Conversation | null>;
    updateConversationPresentation: (
        id: string,
        patch: ConversationPresentationPatch,
        options?: ConversationPresentationOptions,
    ) => Promise<Conversation | null>;
    patchConversationSummary: (id: string, patch: Partial<Conversation>) => void;
    deleteConversation: (id: string) => Promise<boolean>;
    clearConversations: () => Promise<boolean>;
}

const ConversationContext = createContext<ConversationContextType | undefined>(undefined);

function isSameConversation(left: Conversation, right: Conversation): boolean {
    return JSON.stringify(left) === JSON.stringify(right);
}

function isSameConversationList(left: Conversation[], right: Conversation[]): boolean {
    if (left.length !== right.length) return false;
    for (let index = 0; index < left.length; index += 1) {
        if (!isSameConversation(left[index], right[index])) {
            return false;
        }
    }
    return true;
}

function getConversationSessionId(item: Pick<Conversation, "id" | "sessionId">): string {
    return item.sessionId || item.id;
}

export function ConversationProvider({ children }: { children: ReactNode }) {
    const { data: session, status } = useSession();
    const [conversations, setConversations] = useState<Conversation[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const authenticatedRef = useRef(false);
    const hasLoadedRef = useRef(false);
    const refreshInFlightRef = useRef<Promise<void> | null>(null);
    const sessionIndexHydratedRef = useRef(false);
    const lastOwnerKeyRef = useRef("");
    const ownerKey = String(session?.user?.id || session?.user?.email || "").trim().toLowerCase();

    const fetchConversations = useCallback((): Promise<void> => {
        if (refreshInFlightRef.current) {
            return refreshInFlightRef.current;
        }

        const showInitialLoading = !hasLoadedRef.current;
        if (showInitialLoading) {
            setIsLoading(true);
        }

        const request = (async () => {
            try {
                const res = await fetch(`/api/conversations`, { cache: "no-store" });
                if (res.ok && authenticatedRef.current) {
                    const data = await res.json();
                    const sessionList = Array.isArray(data) ? data : (data.sessions || []);
                    const normalized = normalizeSessionHistoryList(sessionList);
                    setConversations((prev) => {
                        const reconciled = preserveLiveSessionTitles(prev, normalized);
                        return isSameConversationList(prev, reconciled) ? prev : reconciled;
                    });
                }
            } catch (error) {
                console.error("Failed to fetch conversations", error);
            } finally {
                hasLoadedRef.current = true;
                refreshInFlightRef.current = null;
                if (authenticatedRef.current) {
                    setIsLoading(false);
                }
            }
        })();
        refreshInFlightRef.current = request;
        return request;
    }, []);

    const createConversation = useCallback(async (payload?: CreateConversationPayload) => {
        try {
                const res = await fetch(`/api/conversations`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(payload || {}),
            });
            if (res.ok) {
                const newConv = normalizeSessionHistoryItem(await res.json());
                const newSessionId = getConversationSessionId(newConv);
                setConversations((prev) => {
                    const next = sortSessionHistory([newConv, ...prev.filter(item => getConversationSessionId(item) !== newSessionId)]);
                    return isSameConversationList(prev, next) ? prev : next;
                });
                return newConv;
            }
        } catch (error) {
            console.error("Failed to create conversation", error);
        }
        return null;
    }, []);

    const patchConversationSummary = useCallback((id: string, patch: Partial<Conversation>) => {
        if (!id) return;
        setConversations((prev) => {
            const index = prev.findIndex((item) => getConversationSessionId(item) === id);
            if (index < 0) {
                return prev;
            }
            const current = prev[index];
            const merged = mergeSessionHistoryOverlay(current, patch);
            if (isSameConversation(current, merged)) {
                return prev;
            }
            const next = [...prev];
            next[index] = merged;
            const sorted = sortSessionHistory(next);
            return isSameConversationList(prev, sorted) ? prev : sorted;
        });
    }, []);

    const updateConversationPresentation = useCallback(async (
        id: string,
        patch: ConversationPresentationPatch,
        options: ConversationPresentationOptions = {},
    ) => {
        try {
            const response = await fetch(`/api/conversations/${encodeURIComponent(id)}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(patch),
            });
            if (!response.ok) {
                return null;
            }
            const updated = normalizeSessionHistoryItem(await response.json().catch(() => ({})));
            const sessionId = getConversationSessionId(updated);
            if (options.applyResponse !== false) {
                setConversations((current) => {
                    const next = sortSessionHistory([
                        updated,
                        ...current.filter((item) => getConversationSessionId(item) !== sessionId),
                    ]);
                    return isSameConversationList(current, next) ? current : next;
                });
            }
            return updated;
        } catch (error) {
            console.error("Failed to update conversation presentation", error);
            return null;
        }
    }, []);

    const deleteConversation = useCallback(async (id: string) => {
        try {
            const res = await fetch(`/api/conversations/${id}`, {
                method: 'DELETE',
            });
            if (res.ok) {
                setConversations(prev => prev.filter(c => getConversationSessionId(c) !== id));
                return true;
            }
        } catch (error) {
            console.error("Failed to delete conversation", error);
        }
        return false;
    }, []);

    const clearConversations = useCallback(async () => {
        try {
            const res = await fetch(`/api/conversations`, {
                method: 'DELETE',
            });
            if (res.ok) {
                setConversations([]);
                return true;
            }
        } catch (error) {
            console.error("Failed to clear conversations", error);
        }
        return false;
    }, []);

    useEffect(() => {
        authenticatedRef.current = status === "authenticated";
        if (status === "unauthenticated") {
            if (lastOwnerKeyRef.current) {
                clearWebSessionIndexCache(lastOwnerKeyRef.current);
            }
            lastOwnerKeyRef.current = "";
            sessionIndexHydratedRef.current = false;
            hasLoadedRef.current = false;
            setConversations([]);
            setIsLoading(false);
            return;
        }
        if (status === "authenticated" && ownerKey && lastOwnerKeyRef.current !== ownerKey) {
            const cached = normalizeSessionHistoryList(readWebSessionIndexCache<Conversation>(ownerKey));
            lastOwnerKeyRef.current = ownerKey;
            sessionIndexHydratedRef.current = true;
            if (cached.length > 0) {
                hasLoadedRef.current = true;
                setConversations(cached);
                setIsLoading(false);
            }
        }
    }, [ownerKey, status]);

    useEffect(() => {
        if (status !== "authenticated" || !ownerKey || !sessionIndexHydratedRef.current) {
            return;
        }
        writeWebSessionIndexCache(ownerKey, conversations);
    }, [conversations, ownerKey, status]);

    useEffect(() => {
        if (status !== "authenticated") {
            return;
        }

        let stopped = false;
        let activityStream: EventSource | null = null;
        let reconnectTimer: number | null = null;
        let refreshTimer: number | null = null;
        let reconnectDelayMs = 500;

        const scheduleRefresh = (delayMs = 80) => {
            if (refreshTimer !== null) {
                window.clearTimeout(refreshTimer);
            }
            refreshTimer = window.setTimeout(() => {
                refreshTimer = null;
                if (!stopped) {
                    void fetchConversations();
                }
            }, delayMs);
        };

        const refreshWhenVisible = () => {
            if (document.visibilityState === "visible") {
                scheduleRefresh(0);
            }
        };

        const connectActivityStream = () => {
            if (stopped) return;
            activityStream?.close();
            activityStream = new EventSource("/api/realtime/session-activity/stream");
            const handleActivity = () => scheduleRefresh();
            activityStream.addEventListener("ready", handleActivity);
            activityStream.addEventListener("activity", handleActivity);
            activityStream.onopen = () => {
                reconnectDelayMs = 500;
            };
            activityStream.onerror = () => {
                activityStream?.close();
                activityStream = null;
                if (stopped || reconnectTimer !== null) return;
                const delay = reconnectDelayMs;
                reconnectDelayMs = Math.min(8_000, reconnectDelayMs * 2);
                reconnectTimer = window.setTimeout(() => {
                    reconnectTimer = null;
                    connectActivityStream();
                }, delay);
            };
        };

        void fetchConversations();
        connectActivityStream();
        window.addEventListener("focus", refreshWhenVisible);
        document.addEventListener("visibilitychange", refreshWhenVisible);

        return () => {
            stopped = true;
            activityStream?.close();
            if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
            if (refreshTimer !== null) window.clearTimeout(refreshTimer);
            window.removeEventListener("focus", refreshWhenVisible);
            document.removeEventListener("visibilitychange", refreshWhenVisible);
        };
    }, [fetchConversations, status]);

    const contextValue = useMemo(() => ({
        conversations,
        isLoading,
        refreshConversations: fetchConversations,
        createConversation,
        updateConversationPresentation,
        patchConversationSummary,
        deleteConversation,
        clearConversations,
    }), [
        clearConversations,
        conversations,
        createConversation,
        deleteConversation,
        fetchConversations,
        isLoading,
        patchConversationSummary,
        updateConversationPresentation,
    ]);

    return (
        <ConversationContext.Provider value={contextValue}>
            {children}
        </ConversationContext.Provider>
    );
}

export function useConversationContext() {
    const context = useContext(ConversationContext);
    if (context === undefined) {
        throw new Error('useConversationContext must be used within a ConversationProvider');
    }
    return context;
}
