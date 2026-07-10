/* eslint-disable @typescript-eslint/no-explicit-any */
import { useRef, useCallback, useEffect } from 'react';

import {
    buildAssistantMessage,
    cloneMessages,
    normalizeMessagesForState,
    normalizeProjectedMessages,
    WEB_STREAM_LIFECYCLE_OPTIONS,
} from '@/lib/chat-stream-state';
import { createClientId } from '@/lib/id';
import { normalizeRealtimeEvent } from '@/lib/realtime';
import {
    markStreamClientCommit,
    markStreamClientRender,
    readStreamDiagnostics,
    recordReceivedStreamDelta,
    type PendingStreamDiagnostic,
    type StreamLatencyStats,
} from '@/lib/streaming-diagnostics';
import { Message } from '@/store/chat-types';
import { useChatStore } from '@/store/chat-store';
import {
    createInitialSessionRealtimeMessageState,
    flushQueuedSessionRealtimeRuntimeEvents,
    queueSessionRealtimeRuntimeEvent,
    syncSessionRealtimeMessageState,
} from '@v8/session-realtime';

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

function appendAssistantPlaceholderIfNeeded(messages: Message[]) {
    const lastMessage = messages[messages.length - 1] as (Message & {
        uiEphemeral?: boolean;
        uiStreamPhase?: string | null;
    }) | undefined;
    if (
        lastMessage?.role === 'assistant'
        && (lastMessage.uiEphemeral || isActiveAssistantStreamPhase(lastMessage.uiStreamPhase))
    ) {
        return normalizeMessagesForState(messages);
    }
    return normalizeMessagesForState([
        ...messages,
        buildAssistantMessage({}),
    ]);
}

function isActiveAssistantStreamPhase(phase?: string | null) {
    return phase === 'placeholder' || phase === 'agent_started' || phase === 'streaming' || phase === 'settling';
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

function attachmentUrl(item: Record<string, unknown>) {
    return String(item.publicUrl || item.url || item.workspacePath || "").trim();
}

function attachmentMime(item: Record<string, unknown>) {
    return String(item.mimeType || item.mime_type || item.type || "").toLowerCase();
}

function isAudioAttachment(item: Record<string, unknown>) {
    const url = attachmentUrl(item).toLowerCase();
    const kind = String(item.mediaKind || item.previewKind || "").toLowerCase();
    return kind === "audio" || attachmentMime(item).startsWith("audio/") || /\.(mp3|m4a|wav|ogg|opus|aac|flac|webm)$/i.test(url);
}

function isAudioUrl(value: string) {
    return /\.(mp3|m4a|wav|ogg|opus|aac|flac|webm)(?:[?#].*)?$/i.test(String(value || "").trim());
}

export function useLangGraphStream({ apiEndpoint, onError, onFinish, onConnect, onCustomEvent }: UseLangGraphStreamOptions) {
    const { messages, setMessages, isLoading, setIsLoading } = useChatStore();
    const abortControllerRef = useRef<AbortableTransport | null>(null);
    const pendingMessagesRef = useRef<Message[] | null>(null);
    const commitFrameRef = useRef<number | null>(null);
    const commitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const streamLatencyStatsRef = useRef(new Map<string, StreamLatencyStats>());
    const pendingStreamDiagnosticRef = useRef<PendingStreamDiagnostic | null>(null);
    const messagesRef = useRef<Message[]>(messages);
    const realtimeMessageStateRef = useRef(
        createInitialSessionRealtimeMessageState<Message>(messages, WEB_STREAM_LIFECYCLE_OPTIONS),
    );

    // Use a ref for callbacks to avoid stale closures in the long-running stream loop
    const handlersRef = useRef({ onError, onFinish, onConnect, onCustomEvent });
    handlersRef.current = { onError, onFinish, onConnect, onCustomEvent };

    useEffect(() => {
        messagesRef.current = messages;
        realtimeMessageStateRef.current = syncSessionRealtimeMessageState(messages, WEB_STREAM_LIFECYCLE_OPTIONS);
    }, [messages]);

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
        messagesRef.current = snapshot;
        realtimeMessageStateRef.current = syncSessionRealtimeMessageState(snapshot, WEB_STREAM_LIFECYCLE_OPTIONS);
        setMessages(snapshot);
        const pendingStreamDiagnostic = pendingStreamDiagnosticRef.current;
        pendingStreamDiagnosticRef.current = null;
        if (pendingStreamDiagnostic) {
            const committedAtMs = Date.now();
            markStreamClientCommit(streamLatencyStatsRef.current, pendingStreamDiagnostic, committedAtMs);
            const markRendered = () => {
                markStreamClientRender(streamLatencyStatsRef.current, pendingStreamDiagnostic, Date.now());
            };
            if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
                window.requestAnimationFrame(markRendered);
            } else {
                setTimeout(markRendered, 0);
            }
        }
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

    const applyStreamEvent = useCallback((event: any) => {
        if (event.type === 'protocol_connected') {
            const connectedSessionId = event.sessionId || event.conversationId;
            if (connectedSessionId && handlersRef.current.onConnect) {
                handlersRef.current.onConnect(connectedSessionId);
            }
            return false;
        }

        if (event.type === 'error') {
            console.error('Stream Error Event:', event.error);
        } else if (event.type === 'custom_event') {
            if (handlersRef.current.onCustomEvent) {
                handlersRef.current.onCustomEvent(event);
            }
        }

        return queueSessionRealtimeRuntimeEvent(realtimeMessageStateRef.current, event);
    }, []);

    const streamNdjson = useCallback(async (requestBody: any, initialMessages: Message[]) => {
        const abortController = new AbortController();
        abortControllerRef.current = { abort: () => abortController.abort() };
        streamLatencyStatsRef.current.clear();
        pendingStreamDiagnosticRef.current = null;

        const response = await fetch(apiEndpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody),
            signal: abortController.signal
        });

        if (!response.ok) {
            const errorText = await response.text().catch(() => "");
            let detail = "";
            if (errorText.trim()) {
                try {
                    const payload = JSON.parse(errorText) as Record<string, unknown>;
                    const nested = payload.detail && typeof payload.detail === "object"
                        ? payload.detail as Record<string, unknown>
                        : {};
                    detail = String(
                        nested.error
                        || nested.summary
                        || payload.error
                        || payload.detail
                        || payload.message
                        || "",
                    ).trim();
                } catch {
                    detail = errorText.trim();
                }
            }
            throw new Error(detail || `HTTP error! status: ${response.status}`);
        }
        if (!response.body) throw new Error('Response body is null');

        const convId = response.headers.get('x-v8-agent-os-conversation-id');
        if (convId && handlersRef.current.onConnect) {
            handlersRef.current.onConnect(convId);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let localMessages = cloneMessages(initialMessages);
        realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
            localMessages,
            WEB_STREAM_LIFECYCLE_OPTIONS,
        );

        const flushRuntimeEvents = () => {
            const nextState = flushQueuedSessionRealtimeRuntimeEvents(
                localMessages,
                realtimeMessageStateRef.current,
                {
                    cloneMessages,
                    normalizeMessages: normalizeMessagesForState,
                    lifecycleOptions: WEB_STREAM_LIFECYCLE_OPTIONS,
                },
            );
            realtimeMessageStateRef.current = nextState.state;
            if (!nextState.changed) {
                return;
            }
            localMessages = nextState.messages;
            messagesRef.current = nextState.messages;
            scheduleMessagesCommit(nextState.messages);
        };

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
                    const pendingDiagnostic = recordReceivedStreamDelta({
                        surface: 'web/local-ndjson',
                        event,
                        diagnostics: readStreamDiagnostics(rawEvent),
                        receivedAtMs: Date.now(),
                        statsByKey: streamLatencyStatsRef.current,
                    });
                    if (pendingDiagnostic) {
                        pendingStreamDiagnosticRef.current = pendingDiagnostic;
                    }

                    applyStreamEvent(event);
                } catch (e) {
                    console.warn('Failed to parse NDJSON line:', line, e);
                }
            }

            flushRuntimeEvents();
        }

        if (buffer.trim()) {
            try {
                const rawEvent = JSON.parse(buffer);
                const event = normalizeRealtimeEvent(rawEvent);
                if (event) {
                    const pendingDiagnostic = recordReceivedStreamDelta({
                        surface: 'web/local-ndjson',
                        event,
                        diagnostics: readStreamDiagnostics(rawEvent),
                        receivedAtMs: Date.now(),
                        statsByKey: streamLatencyStatsRef.current,
                    });
                    if (pendingDiagnostic) {
                        pendingStreamDiagnosticRef.current = pendingDiagnostic;
                    }
                    applyStreamEvent(event);
                    flushRuntimeEvents();
                }
            } catch (e) {
                console.warn('Failed to parse trailing NDJSON buffer:', buffer, e);
            }
        }

        flushPendingMessages();
        return localMessages;
    }, [apiEndpoint, applyStreamEvent, flushPendingMessages, scheduleMessagesCommit]);

    const hydrateFromSnapshot = useCallback(async (sessionId: string) => {
        const snapshotRes = await fetch(`/api/realtime/sessions/${sessionId}/snapshot`, { cache: 'no-store' });
        if (!snapshotRes.ok) {
            return false;
        }

        const snapshotData = await snapshotRes.json();
        const snapshotMessages = snapshotData?.snapshot?.messages;
        if (!Array.isArray(snapshotMessages)) {
            return false;
        }

        const localMessages = normalizeMessagesForState(normalizeProjectedMessages(snapshotMessages));
        messagesRef.current = localMessages;
        realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
            localMessages,
            WEB_STREAM_LIFECYCLE_OPTIONS,
        );
        setMessages([...localMessages]);
        return true;
    }, [setMessages]);

    const tryResyncConversation = useCallback(async (sessionId: string | undefined, label: string) => {
        if (!sessionId) {
            return false;
        }
        try {
            return await hydrateFromSnapshot(sessionId);
        } catch (syncError) {
            console.warn(`[useLangGraphStream] ${label} resync failed:`, syncError);
            return false;
        }
    }, [hydrateFromSnapshot]);

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
        if (data?.specMode) {
            optimisticMetadata.specMode = true;
        }
        if (data?.specCommand && typeof data.specCommand === 'object') {
            optimisticMetadata.specMode = true;
            optimisticMetadata.specCommand = data.specCommand;
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
        if (Array.isArray(data?.contextSessionRefs) && data.contextSessionRefs.length > 0) {
            optimisticMetadata.contextSessionRefs = data.contextSessionRefs
                .filter((item: unknown) => item && typeof item === "object")
                .map((item: Record<string, unknown>) => ({
                    sessionId: typeof item.sessionId === "string" ? item.sessionId.trim() : "",
                    source: item.source === "history_menu" ? "history_menu" : "",
                }))
                .filter((item: { sessionId: string; source: string }) => item.sessionId && item.source);
        }
        const dataAttachments: Record<string, unknown>[] = Array.isArray(data?.attachments)
            ? data.attachments.filter((item: unknown): item is Record<string, unknown> => Boolean(item) && typeof item === 'object')
            : [];
        if (dataAttachments.length > 0) {
            optimisticMetadata.attachments = dataAttachments;
        }
        const allFileUrls: string[] = Array.isArray(data?.fileUrls)
            ? data.fileUrls.filter((item: unknown): item is string => typeof item === 'string' && item.trim().length > 0)
            : dataAttachments.map(attachmentUrl).filter(Boolean);
        const nextImages: string[] = dataAttachments.length > 0
            ? dataAttachments
                .filter((item: Record<string, unknown>) => !isAudioAttachment(item))
                .map(attachmentUrl)
                .filter((item): item is string => Boolean(item))
            : allFileUrls.filter((url: string) => !isAudioUrl(url));
        const effectiveUserMessage = userMessage.trim();
        const nextNodes = effectiveUserMessage.trim()
            ? [{ id: createClientId('node'), kind: 'narrative' as const, role: 'user' as const, content: effectiveUserMessage, timestamp: Date.now() }]
            : [];

        // Optimistic User Message
        const tempUserMsg: Message = {
            id: createClientId('message'),
            role: 'user',
            content: effectiveUserMessage,
            nodes: nextNodes,
            timestamp: Date.now(),
            images: nextImages,
            metadata: Object.keys(optimisticMetadata).length > 0 ? optimisticMetadata : undefined,
        };

        const newHistory = appendAssistantPlaceholderIfNeeded([...currentMessages, tempUserMsg]);
        messagesRef.current = newHistory;
        realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
            newHistory,
            WEB_STREAM_LIFECYCLE_OPTIONS,
        );
        setMessages(newHistory);

        // Prepare Request
        try {
            if (abortControllerRef.current) abortControllerRef.current.abort();

            const requestBody: any = {
                messages: [...currentMessages, tempUserMsg].map(m => ({ role: m.role, content: m.content })), // Send only history up to user msg
                data: data, // Keep passing the whole object just in case backend expects it
                fileUrls: allFileUrls, // Explicitly pass all uploaded refs, including audio
                attachments: dataAttachments,
            };
            applyScopeRequestFields(requestBody, data);
            const finalMessages = await streamNdjson(requestBody, newHistory);

            if (handlersRef.current.onFinish) handlersRef.current.onFinish(finalMessages);
            return true;

        } catch (error) {
            console.error("Stream failed:", error);
            const recovered = await tryResyncConversation(data?.conversationId, "HTTP stream");
            if (!recovered && handlersRef.current.onError) handlersRef.current.onError(error as Error);
            return false;
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
            const nextMessages = appendAssistantPlaceholderIfNeeded(currentMessages);
            messagesRef.current = nextMessages;
            realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
                nextMessages,
                WEB_STREAM_LIFECYCLE_OPTIONS,
            );
            setMessages(nextMessages);
            const requestBody: any = {
                messages: currentMessages.map(m => ({ role: m.role, content: m.content })),
                data: data,
                tool_outputs: [{ tool_call_id: toolCallId, output: output }]
            };
            applyScopeRequestFields(requestBody, data);

            const finalMessages = await streamNdjson(requestBody, nextMessages);

            if (handlersRef.current.onFinish) handlersRef.current.onFinish(finalMessages);

        } catch (error) {
            console.error("Tool output stream failed:", error);
            const recovered = await tryResyncConversation(data?.conversationId, "Tool output");
            if (!recovered && handlersRef.current.onError) handlersRef.current.onError(error as Error);
        } finally {
            flushPendingMessages();
            setIsLoading(false);
            abortControllerRef.current = null;
        }
    }, [flushPendingMessages, messages, setIsLoading, setMessages, streamNdjson, tryResyncConversation]);

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

    return { messages, isLoading, sendMessage, stop, setMessages, sendToolOutput, resolveApproval, dispatchRunCommand };
}
