/* eslint-disable @typescript-eslint/no-explicit-any */
import { useRef, useCallback, useEffect } from 'react';

import {
    AgentProfile,
    applyRealtimeEventToMessages,
    cloneMessages,
    normalizeProjectedMessages,
} from '@/lib/chat-stream-state';
import { createClientId } from '@/lib/id';
import { normalizeRealtimeEvent } from '@/lib/realtime';
import { Message } from '@/store/chat-types';
import { useChatStore } from '@/store/chat-store';

type AbortableTransport = {
    abort: () => void;
};

interface UseLangGraphStreamOptions {
    apiEndpoint: string;
    onError?: (error: Error) => void;
    onFinish?: (messages: Message[]) => void;
    onConnect?: (conversationId: string) => void;
    onCustomEvent?: (event: any) => void;
}

interface PendingApprovalRecord {
    id?: string;
    approval_id?: string;
    run_id?: string;
    approval_kind?: string;
    request?: {
        question?: string;
        prompt?: string;
        toolCallId?: string;
        [key: string]: unknown;
    };
}

function applyScopeRequestFields(requestBody: Record<string, unknown>, data?: Record<string, unknown>) {
    if (!data) return;

    const projectId = data.projectId ?? data.project_id;
    const workspaceId = data.workspaceId ?? data.workspace_id;
    const workspacePath = data.workspacePath ?? data.workspace_path;
    const scopeHint = data.scopeHint ?? data.scope_hint;
    const scopeMode = data.scopeMode ?? data.scope_mode;
    const conversationId = data.conversationId ?? data.session_id;

    if (conversationId) {
        requestBody.session_id = conversationId;
        requestBody.conversationId = conversationId;
    }
    if (projectId) {
        requestBody.project_id = projectId;
    }
    if (workspaceId) {
        requestBody.workspace_id = workspaceId;
    }
    if (workspacePath) {
        requestBody.workspace_path = workspacePath;
    }
    if (scopeHint) {
        requestBody.scope_hint = scopeHint;
    }
    if (scopeMode) {
        requestBody.scope_mode = scopeMode;
    }
}

export function useLangGraphStream({ apiEndpoint, onError, onFinish, onConnect, onCustomEvent }: UseLangGraphStreamOptions) {
    const { messages, setMessages, isLoading, setIsLoading } = useChatStore();
    const abortControllerRef = useRef<AbortableTransport | null>(null);
    const pendingMessagesRef = useRef<Message[] | null>(null);
    const commitFrameRef = useRef<number | null>(null);
    const commitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    // Use a ref for callbacks to avoid stale closures in the long-running stream loop
    const handlersRef = useRef({ onError, onFinish, onConnect, onCustomEvent });
    handlersRef.current = { onError, onFinish, onConnect, onCustomEvent };

    const flushPendingMessages = useCallback(() => {
        if (commitFrameRef.current !== null && typeof window !== 'undefined') {
            window.cancelAnimationFrame(commitFrameRef.current);
            commitFrameRef.current = null;
        }
        if (commitTimerRef.current) {
            clearTimeout(commitTimerRef.current);
            commitTimerRef.current = null;
        }

        if (!pendingMessagesRef.current) {
            return;
        }

        const snapshot = cloneMessages(pendingMessagesRef.current);
        pendingMessagesRef.current = null;
        setMessages(snapshot);
    }, [setMessages]);

    const scheduleMessagesCommit = useCallback((nextMessages: Message[]) => {
        pendingMessagesRef.current = nextMessages;
        if (commitFrameRef.current !== null || commitTimerRef.current) {
            return;
        }

        const commit = () => {
            commitFrameRef.current = null;
            commitTimerRef.current = null;
            flushPendingMessages();
        };

        if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
            commitFrameRef.current = window.requestAnimationFrame(commit);
        } else {
            commitTimerRef.current = setTimeout(commit, 16);
        }
    }, [flushPendingMessages]);

    useEffect(() => {
        return () => {
            flushPendingMessages();
        };
    }, [flushPendingMessages]);

    const applyStreamEvent = useCallback((
        event: any,
        localMessages: Message[],
        currentAiMsg: Message | undefined,
        activeAgentProfile: AgentProfile
    ) => {
        let nextCurrentAiMsg = currentAiMsg;
        let nextActiveAgentProfile = activeAgentProfile;

        if (event.type === 'protocol_connected') {
            const connectedSessionId = event.sessionId || event.conversationId;
            if (connectedSessionId && handlersRef.current.onConnect) {
                handlersRef.current.onConnect(connectedSessionId);
            }
            return { currentAiMsg: nextCurrentAiMsg, activeAgentProfile: nextActiveAgentProfile };
        }

        if (event.type === 'error') {
            console.error('Stream Error Event:', event.error);
        } else if (event.type === 'custom_event') {
            if (handlersRef.current.onCustomEvent) {
                handlersRef.current.onCustomEvent(event);
            }
        }

        const stateResult = applyRealtimeEventToMessages(event, localMessages, nextCurrentAiMsg, nextActiveAgentProfile);
        nextCurrentAiMsg = stateResult.currentAiMsg;
        nextActiveAgentProfile = stateResult.activeAgentProfile;

        return { currentAiMsg: nextCurrentAiMsg, activeAgentProfile: nextActiveAgentProfile };
    }, []);

    const streamNdjson = useCallback(async (
        requestBody: any,
        localMessages: Message[],
        currentAiMsg: Message | undefined,
        activeAgentProfile: AgentProfile
    ) => {
        const abortController = new AbortController();
        abortControllerRef.current = { abort: () => abortController.abort() };

        const response = await fetch(apiEndpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody),
            signal: abortController.signal
        });

        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        if (!response.body) throw new Error('Response body is null');

        const convId = response.headers.get('x-v8-agent-os-conversation-id');
        if (convId && handlersRef.current.onConnect) {
            handlersRef.current.onConnect(convId);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let nextCurrentAiMsg = currentAiMsg;
        let nextActiveAgentProfile = activeAgentProfile;

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value, { stream: true });
            buffer += chunk;
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (!line.trim()) continue;

                try {
                    const rawEvent = JSON.parse(line);
                    const event = normalizeRealtimeEvent(rawEvent);
                    if (!event) continue;

                    const result = applyStreamEvent(event, localMessages, nextCurrentAiMsg, nextActiveAgentProfile);
                    nextCurrentAiMsg = result.currentAiMsg;
                    nextActiveAgentProfile = result.activeAgentProfile;
                } catch (e) {
                    console.warn('Failed to parse NDJSON line:', line, e);
                }
            }

            scheduleMessagesCommit(localMessages);
        }

        if (buffer.trim()) {
            try {
                const rawEvent = JSON.parse(buffer);
                const event = normalizeRealtimeEvent(rawEvent);
                if (event) {
                    const result = applyStreamEvent(event, localMessages, nextCurrentAiMsg, nextActiveAgentProfile);
                    nextCurrentAiMsg = result.currentAiMsg;
                    nextActiveAgentProfile = result.activeAgentProfile;
                    scheduleMessagesCommit(localMessages);
                }
            } catch (e) {
                console.warn('Failed to parse trailing NDJSON buffer:', buffer, e);
            }
        }

        flushPendingMessages();

        return { currentAiMsg: nextCurrentAiMsg, activeAgentProfile: nextActiveAgentProfile };
    }, [apiEndpoint, applyStreamEvent, flushPendingMessages, scheduleMessagesCommit]);

    const hydrateFromSnapshotAndReplay = useCallback(async (sessionId: string) => {
        const snapshotRes = await fetch(`/api/realtime/sessions/${sessionId}/snapshot`, { cache: 'no-store' });
        if (!snapshotRes.ok) {
            return false;
        }

        const snapshotData = await snapshotRes.json();
        const snapshotMessages = snapshotData?.snapshot?.messages;
        if (!Array.isArray(snapshotMessages)) {
            return false;
        }

        const localMessages = normalizeProjectedMessages(snapshotMessages);
        let nextCurrentAiMsg = localMessages.length > 0 && localMessages[localMessages.length - 1]?.role === 'assistant'
            ? localMessages[localMessages.length - 1]
            : undefined;
        let nextActiveAgentProfile: AgentProfile = nextCurrentAiMsg
            ? {
                agentName: nextCurrentAiMsg.agentName,
                agentAvatar: nextCurrentAiMsg.agentAvatar,
                agentRoleLabel: nextCurrentAiMsg.agentRoleLabel,
            }
            : {};

        const latestSeq = Number(snapshotData?.latestSeq || snapshotData?.snapshot?.latest_seq || 0);
        const eventsRes = await fetch(`/api/realtime/sessions/${sessionId}/events?after_seq=${latestSeq}`, { cache: 'no-store' });
        if (eventsRes.ok) {
            const eventsData = await eventsRes.json();
            const events = Array.isArray(eventsData?.events) ? eventsData.events : [];
            for (const runtimeEvent of events) {
                const event = normalizeRealtimeEvent(runtimeEvent);
                if (!event) continue;
                const result = applyStreamEvent(event, localMessages, nextCurrentAiMsg, nextActiveAgentProfile);
                nextCurrentAiMsg = result.currentAiMsg;
                nextActiveAgentProfile = result.activeAgentProfile;
            }
        }

        setMessages([...localMessages]);
        return true;
    }, [applyStreamEvent, setMessages]);

    const tryResyncConversation = useCallback(async (sessionId: string | undefined, label: string) => {
        if (!sessionId) {
            return false;
        }
        try {
            return await hydrateFromSnapshotAndReplay(sessionId);
        } catch (syncError) {
            console.warn(`[useLangGraphStream] ${label} resync failed:`, syncError);
            return false;
        }
    }, [hydrateFromSnapshotAndReplay]);

    const sendMessage = useCallback(async (userMessage: string, data?: any) => {
        setIsLoading(true);
        const currentMessages = cloneMessages(messages);
        const commandPresetName = typeof data?.commandPreset?.name === 'string'
            ? String(data.commandPreset.name).trim()
            : '';
        const optimisticMetadata: Record<string, unknown> = {};
        if (commandPresetName) {
            optimisticMetadata.commandPreset = { name: commandPresetName };
        }
        if (data?.taskPlanningMode) {
            optimisticMetadata.taskPlanningMode = true;
        }
        if (Array.isArray(data?.skillReferences) && data.skillReferences.length > 0) {
            optimisticMetadata.skillReferences = data.skillReferences
                .filter((item: unknown) => item && typeof item === 'object')
                .map((item: Record<string, unknown>) => ({
                    name: typeof item.name === 'string' ? item.name.trim() : '',
                    description: typeof item.description === 'string' ? item.description.trim() : '',
                    path: typeof item.path === 'string' ? item.path.trim() : '',
                }))
                .filter((item: { name: string; description: string; path: string }) => item.name || item.path);
        }
        const nextImages = Array.isArray(data?.fileUrls) ? data.fileUrls : [];
        const nextNodes = userMessage.trim()
            ? [{ id: createClientId('node'), kind: 'narrative' as const, role: 'user' as const, content: userMessage, timestamp: Date.now() }]
            : [];

        // Optimistic User Message
        const tempUserMsg: Message = {
            id: createClientId('message'),
            role: 'user',
            content: userMessage,
            nodes: nextNodes,
            timestamp: Date.now(),
            images: nextImages,
            metadata: Object.keys(optimisticMetadata).length > 0 ? optimisticMetadata : undefined,
        };

        let currentAiMsg: Message | undefined;
        let activeAgentProfile: { agentName?: string, agentAvatar?: string, agentRoleLabel?: string } = {};

        // Update state with BOTH User and AI placeholders immediately
        const newHistory = [...currentMessages, tempUserMsg];
        setMessages(newHistory);

        // Prepare Request
        try {
            if (abortControllerRef.current) abortControllerRef.current.abort();

            const requestBody: any = {
                messages: [...currentMessages, tempUserMsg].map(m => ({ role: m.role, content: m.content })), // Send only history up to user msg
                data: data, // Keep passing the whole object just in case backend expects it
                fileUrls: data?.fileUrls // Explicitly pass fileUrls
            };
            applyScopeRequestFields(requestBody, data);
            const localMessages = cloneMessages(newHistory);
            const httpResult = await streamNdjson(requestBody, localMessages, currentAiMsg, activeAgentProfile);
            currentAiMsg = httpResult.currentAiMsg;
            activeAgentProfile = httpResult.activeAgentProfile;

            if (handlersRef.current.onFinish) handlersRef.current.onFinish(localMessages);

        } catch (error) {
            console.error("Stream failed:", error);
            const recovered = await tryResyncConversation(data?.conversationId, "HTTP stream");
            if (!recovered && handlersRef.current.onError) handlersRef.current.onError(error as Error);
        } finally {
            flushPendingMessages();
            setIsLoading(false);
            abortControllerRef.current = null;
        }

    }, [flushPendingMessages, messages, streamNdjson, setIsLoading, setMessages, tryResyncConversation]);

    const stop = useCallback(() => {
        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
            abortControllerRef.current = null;
            flushPendingMessages();
            setIsLoading(false);
        }
    }, [flushPendingMessages, setIsLoading]);

    const sendToolOutput = useCallback(async (toolCallId: string, output: string, data?: any) => {
        setIsLoading(true);
        const currentMessages = cloneMessages(messages);
        try {
            if (abortControllerRef.current) abortControllerRef.current.abort();
            const localMessages = cloneMessages(currentMessages);
            let currentAiMsg: Message | undefined = undefined;
            let activeAgentProfile: AgentProfile = {};
            const requestBody: any = {
                messages: currentMessages.map(m => ({ role: m.role, content: m.content })),
                data: data,
                tool_outputs: [{ tool_call_id: toolCallId, output: output }]
            };
            applyScopeRequestFields(requestBody, data);

            const httpResult = await streamNdjson(requestBody, localMessages, currentAiMsg, activeAgentProfile);
            currentAiMsg = httpResult.currentAiMsg;
            activeAgentProfile = httpResult.activeAgentProfile;

            if (handlersRef.current.onFinish) handlersRef.current.onFinish(localMessages);

        } catch (error) {
            console.error("Tool output stream failed:", error);
            const recovered = await tryResyncConversation(data?.conversationId, "Tool output");
            if (!recovered && handlersRef.current.onError) handlersRef.current.onError(error as Error);
        } finally {
            flushPendingMessages();
            setIsLoading(false);
            abortControllerRef.current = null;
        }
    }, [flushPendingMessages, messages, setIsLoading, streamNdjson, tryResyncConversation]);

    const resolveApproval = useCallback(async (approvalId: string, answer: string, approve = true) => {
        const endpoint = approve ? `/api/approvals/${approvalId}/approve` : `/api/approvals/${approvalId}/reject`;
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                response: {
                    answer,
                    approved: approve,
                }
            }),
        });

        if (!response.ok) {
            const detail = await response.text().catch(() => '');
            throw new Error(detail || `Approval request failed: ${response.status}`);
        }

        return response.json().catch(() => ({}));
    }, []);

    const dispatchRunCommand = useCallback(async (runId: string, command: string, reason?: string) => {
        const response = await fetch(`/api/runs/${runId}/commands/${command}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason }),
        });

        if (!response.ok) {
            const detail = await response.text().catch(() => '');
            throw new Error(detail || `Run command failed: ${response.status}`);
        }

        return response.json().catch(() => ({}));
    }, []);

    const fetchPendingApprovals = useCallback(async (sessionId: string): Promise<PendingApprovalRecord[]> => {
        const response = await fetch(`/api/approvals?session_id=${encodeURIComponent(sessionId)}&status=pending`, {
            cache: 'no-store',
        });
        if (!response.ok) {
            throw new Error(`Pending approvals request failed: ${response.status}`);
        }
        const data = await response.json().catch(() => ({}));
        return Array.isArray(data?.approvals) ? data.approvals : [];
    }, []);

    return { messages, isLoading, sendMessage, stop, setMessages, sendToolOutput, resolveApproval, dispatchRunCommand, fetchPendingApprovals };
}
