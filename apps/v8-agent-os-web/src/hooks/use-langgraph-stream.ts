/* eslint-disable @typescript-eslint/no-explicit-any */
import { useRef, useCallback, useEffect, useState } from 'react';

import {
    buildAssistantMessage,
    cloneMessages,
    normalizeMessagesForState,
    WEB_STREAM_LIFECYCLE_OPTIONS,
} from '@/lib/chat-stream-state';
import { createClientId } from '@/lib/id';
import { normalizeRealtimeEvent } from '@/lib/realtime';
import { shouldSettleSubmittedRun } from '@/lib/chat/run-activity';
import { readChatStream } from '@/lib/chat/read-chat-stream';
import { specError, type SpecReviewDecision } from '@/lib/spec-review';
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
    isClientVisualAttachment,
    queueSessionRealtimeRuntimeEvent,
    syncSessionRealtimeMessageState,
} from '@v8/session-realtime';

type AbortableTransport = {
    abort: () => void;
};

interface UseLangGraphStreamOptions {
    apiEndpoint: string;
    submitEndpoint?: string;
    conversationId: string | null;
    onResync: (sessionId: string) => Promise<void>;
    onError?: (error: Error) => void;
    onFinish?: (messages: Message[]) => void;
    onConnect?: (conversationId: string, transport: 'stream' | 'submit') => void;
    onCustomEvent?: (event: any) => void;
    acceptsRuntimeEvent?: (event: unknown) => boolean;
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

function isVisualUrl(value: string) {
    return isClientVisualAttachment({ url: value });
}

export function useLangGraphStream({ apiEndpoint, submitEndpoint, conversationId, onResync, onError, onFinish, onConnect, onCustomEvent, acceptsRuntimeEvent }: UseLangGraphStreamOptions) {
    const { messages, setMessages, isLoading, setIsLoading } = useChatStore();
    const [submittedRunId, setSubmittedRunId] = useState<string | null>(null);
    const abortControllerRef = useRef<AbortableTransport | null>(null);
    const submittedRunIdRef = useRef<string | null>(null);
    const durableSubmitPendingRef = useRef(false);
    const requestGenerationRef = useRef(0);
    const activeConversationRef = useRef(conversationId);
    activeConversationRef.current = conversationId;
    const beginRequest = useCallback(() => {
        const generation = ++requestGenerationRef.current;
        const owner = activeConversationRef.current;
        return () => generation === requestGenerationRef.current && owner === activeConversationRef.current;
    }, []);
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
    const handlersRef = useRef({ onError, onFinish, onConnect, onCustomEvent, onResync, acceptsRuntimeEvent });
    handlersRef.current = { onError, onFinish, onConnect, onCustomEvent, onResync, acceptsRuntimeEvent };

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

    const scheduleMessagesCommit = useCallback((nextMessages: Message[], isCurrent: () => boolean) => {
        pendingMessagesRef.current = nextMessages;
        if (commitFrameRef.current !== null || commitTimerRef.current) {
            return;
        }

        const commit = () => {
            commitFrameRef.current = null;
            commitTimerRef.current = null;
            if (!isCurrent()) pendingMessagesRef.current = null;
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
            requestGenerationRef.current += 1;
            abortControllerRef.current?.abort();
            pendingMessagesRef.current = null;
            flushPendingMessages();
        };
    }, [flushPendingMessages]);

    const applyStreamEvent = useCallback((event: any) => {
        if (event.type === 'protocol_connected') {
            const connectedSessionId = event.sessionId || event.conversationId;
            if (connectedSessionId && handlersRef.current.onConnect) {
                handlersRef.current.onConnect(connectedSessionId, 'stream');
            }
            return false;
        }

        if (handlersRef.current.acceptsRuntimeEvent?.(event) === false) return false;
        if (event.type === 'error') {
            console.error('Stream Error Event:', event.error);
        } else if (event.type === 'custom_event') {
            if (handlersRef.current.onCustomEvent) {
                handlersRef.current.onCustomEvent(event);
            }
        }

        return queueSessionRealtimeRuntimeEvent(realtimeMessageStateRef.current, event);
    }, []);

    const streamNdjson = useCallback(async (requestBody: any, initialMessages: Message[], isCurrent: () => boolean) => {
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
        if (!isCurrent()) throw new DOMException('Detached conversation', 'AbortError');

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
        if (convId && requestBody.session_id && convId !== requestBody.session_id) {
            throw new Error('Stream conversation does not match the submitted task.');
        }
        if (convId && handlersRef.current.onConnect) {
            handlersRef.current.onConnect(convId, 'stream');
        }

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
            scheduleMessagesCommit(nextState.messages, isCurrent);
        };
        await readChatStream(response.body, (rawEvent) => {
            const event = normalizeRealtimeEvent(rawEvent);
            if (!event) return;
            const pendingDiagnostic = recordReceivedStreamDelta({
                surface: 'web/local-ndjson', event,
                diagnostics: readStreamDiagnostics(rawEvent), receivedAtMs: Date.now(),
                statsByKey: streamLatencyStatsRef.current,
            });
            if (pendingDiagnostic) pendingStreamDiagnosticRef.current = pendingDiagnostic;
            applyStreamEvent(event);
            flushRuntimeEvents();
        }, isCurrent);

        flushPendingMessages();
        return localMessages;
    }, [apiEndpoint, applyStreamEvent, flushPendingMessages, scheduleMessagesCommit]);

    const submitDurableRun = useCallback(async (requestBody: any, isCurrent: () => boolean) => {
        if (!submitEndpoint) {
            return null;
        }
        const abortController = new AbortController();
        abortControllerRef.current = { abort: () => abortController.abort() };
        const response = await fetch(submitEndpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(requestBody),
            signal: abortController.signal,
        });
        const payload = await response.json().catch(() => ({})) as Record<string, unknown>;
        if (!isCurrent()) throw new DOMException('Detached conversation', 'AbortError');
        abortControllerRef.current = null;
        if (!response.ok) {
            const detail = payload.detail && typeof payload.detail === 'object'
                ? payload.detail as Record<string, unknown>
                : {};
            throw new Error(String(
                detail.summary
                || detail.error
                || payload.error
                || payload.message
                || `HTTP error! status: ${response.status}`,
            ));
        }
        if (payload.accepted !== true) {
            throw new Error('Engine did not accept the chat run.');
        }
        return payload;
    }, [submitEndpoint]);

    const tryResyncConversation = useCallback(async (sessionId: string | undefined, label: string, isCurrent: () => boolean) => {
        if (!sessionId || !isCurrent()) {
            return false;
        }
        try {
            // Flush before the canonical reload, never over its recovered state.
            flushPendingMessages();
            await handlersRef.current.onResync(sessionId);
            return isCurrent();
        } catch (syncError) {
            console.warn(`[useLangGraphStream] ${label} resync failed:`, syncError);
            return false;
        }
    }, [flushPendingMessages]);

    const sendMessage = useCallback(async (userMessage: string, data?: any) => {
        const isCurrent = beginRequest();
        setIsLoading(true);
        const currentMessages = cloneMessages(messages);
        const commandPresetName = typeof data?.commandPreset?.name === 'string'
            ? String(data.commandPreset.name).trim()
            : '';
        const optimisticMetadata: Record<string, unknown> = {};
        if (commandPresetName) {
            optimisticMetadata.commandPreset = { name: commandPresetName };
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
        if (data?.composerPresentation && typeof data.composerPresentation === "object") {
            const presentationText = typeof data.composerPresentation.text === "string"
                ? data.composerPresentation.text
                : "";
            const presentationReferences = Array.isArray(data.composerPresentation.references)
                ? data.composerPresentation.references.filter((item: unknown) => Boolean(item) && typeof item === "object")
                : [];
            if (presentationText) {
                optimisticMetadata.composerPresentation = {
                    text: presentationText,
                    references: presentationReferences,
                };
            }
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
                .filter(isClientVisualAttachment)
                .map(attachmentUrl)
                .filter((item): item is string => Boolean(item))
            : allFileUrls.filter(isVisualUrl);
        const effectiveUserMessage = userMessage.trim();
        const nextNodes = effectiveUserMessage.trim()
            ? [{ id: createClientId('node'), kind: 'narrative' as const, role: 'user' as const, content: effectiveUserMessage, timestamp: Date.now() }]
            : [];

        // Optimistic User Message
        const tempUserMsg: Message = {
            id: typeof data?.clientMessageId === "string" && data.clientMessageId ? data.clientMessageId : createClientId('message'),
            role: 'user',
            content: effectiveUserMessage,
            nodes: nextNodes,
            timestamp: Date.now(),
            images: nextImages,
            metadata: Object.keys(optimisticMetadata).length > 0 ? optimisticMetadata : undefined,
        };

        const submissionMessages = normalizeMessagesForState([...currentMessages, tempUserMsg]);
        const newHistory = appendAssistantPlaceholderIfNeeded(submissionMessages);
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
                messages: submissionMessages.map(m => ({ id: m.id, role: m.role, content: m.content })), // Stable ids let the Engine reconcile persistent history.
                data: data, // Keep passing the whole object just in case backend expects it
                fileUrls: allFileUrls, // Explicitly pass all uploaded refs, including audio
                attachments: dataAttachments,
            };
            applyScopeRequestFields(requestBody, data);
            if (submitEndpoint) {
                requestBody.clientMessageId = tempUserMsg.id;
                requestBody.data = {
                    ...(requestBody.data || {}),
                    clientMessageId: tempUserMsg.id,
                };
                durableSubmitPendingRef.current = true;
                const payload = await submitDurableRun(requestBody, isCurrent);
                const conversationId = String(
                    payload?.conversationId
                    || payload?.session_id
                    || data?.conversationId
                    || '',
                ).trim();
                if (data?.conversationId && conversationId !== data.conversationId) {
                    throw new Error('Accepted conversation does not match the submitted task.');
                }
                const runId = String(payload?.runId || payload?.run_id || '').trim();
                const queued = payload?.queued === true;
                if (!queued && !runId) {
                    throw new Error('Engine accepted the chat run without returning a run id.');
                }
                submittedRunIdRef.current = queued ? null : runId;
                setSubmittedRunId(queued ? null : runId);
                durableSubmitPendingRef.current = false;
                if (conversationId && handlersRef.current.onConnect) {
                    handlersRef.current.onConnect(conversationId, 'submit');
                }
                if (queued) {
                    await tryResyncConversation(conversationId || data?.conversationId, 'queued submit', isCurrent);
                    if (!isCurrent()) return true;
                    setIsLoading(false);
                    if (handlersRef.current.onFinish) handlersRef.current.onFinish(messagesRef.current);
                }
                return true;
            }

            const finalMessages = await streamNdjson(requestBody, newHistory, isCurrent);
            if (handlersRef.current.onFinish) handlersRef.current.onFinish(finalMessages);
            return true;

        } catch (error) {
            if (!isCurrent()) return false;
            durableSubmitPendingRef.current = false;
            console.error("Stream failed:", error);
            await tryResyncConversation(data?.conversationId, "HTTP stream", isCurrent);
            if (isCurrent()) handlersRef.current.onError?.(error as Error);
            return false;
        } finally {
            if (isCurrent()) {
                flushPendingMessages();
                if (!submitEndpoint || !submittedRunIdRef.current) {
                    setIsLoading(false);
                }
                if (!submittedRunIdRef.current) {
                    abortControllerRef.current = null;
                }
            }
        }

    }, [beginRequest, flushPendingMessages, messages, setIsLoading, setMessages, streamNdjson, submitDurableRun, submitEndpoint, tryResyncConversation]);

    const stop = useCallback(() => {
        requestGenerationRef.current += 1;
        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
            abortControllerRef.current = null;
        }
        submittedRunIdRef.current = null;
        durableSubmitPendingRef.current = false;
        setSubmittedRunId(null);
        pendingMessagesRef.current = null;
        flushPendingMessages();
        setIsLoading(false);
    }, [flushPendingMessages, setIsLoading]);

    const settleTerminalStream = useCallback((runId?: string | null) => {
        // The session realtime channel is authoritative for run completion. A
        // provider HTTP stream can close late (or omit its final delimiter), so
        // do not keep the composer busy after Engine has published a terminal
        // run event. We deliberately leave the transport alive long enough to
        // accept any already-buffered final message nodes.
        const normalizedRunId = String(runId || '').trim();
        const activeSubmittedRunId = String(submittedRunIdRef.current || '').trim();
        if (!shouldSettleSubmittedRun({
            submittedRunId: activeSubmittedRunId,
            terminalRunId: normalizedRunId,
            acceptancePending: durableSubmitPendingRef.current,
        })) {
            return false;
        }
        flushPendingMessages();
        submittedRunIdRef.current = null;
        setSubmittedRunId(null);
        setIsLoading(false);
        if (handlersRef.current.onFinish) handlersRef.current.onFinish(messagesRef.current);
        return true;
    }, [flushPendingMessages, setIsLoading]);

    const isRunAcceptancePending = useCallback(() => durableSubmitPendingRef.current, []);
    const getSubmittedRunId = useCallback(() => submittedRunIdRef.current, []);

    const sendToolOutput = useCallback(async (toolCallId: string, output: string, data?: any) => {
        const isCurrent = beginRequest();
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
                messages: currentMessages.map(m => ({ id: m.id, role: m.role, content: m.content })),
                data: data,
                tool_outputs: [{ tool_call_id: toolCallId, output: output }]
            };
            applyScopeRequestFields(requestBody, data);

            const finalMessages = await streamNdjson(requestBody, nextMessages, isCurrent);

            if (handlersRef.current.onFinish) handlersRef.current.onFinish(finalMessages);

        } catch (error) {
            if (!isCurrent()) return;
            console.error("Tool output stream failed:", error);
            await tryResyncConversation(data?.conversationId, "Tool output", isCurrent);
            if (isCurrent()) handlersRef.current.onError?.(error as Error);
        } finally {
            if (isCurrent()) {
                flushPendingMessages();
                setIsLoading(false);
                abortControllerRef.current = null;
            }
        }
    }, [beginRequest, flushPendingMessages, messages, setIsLoading, setMessages, streamNdjson, tryResyncConversation]);

    const resolveApproval = useCallback(async (approvalId: string, answer: string, approve = true, review?: SpecReviewDecision) => {
        const endpoint = approve ? `/api/approvals/${approvalId}/approve` : `/api/approvals/${approvalId}/reject`;
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                response: {
                    answer,
                    approved: approve,
                    ...(approve && review ? { documentSha256: review.documentSha256, replaceSpecReview: review.replaceSpecReview === true } : {}),
                }
            }),
        });

        const payload = await response.json().catch(() => ({}));
        if (!response.ok || payload.ok === false) throw specError(payload, `Approval request failed: ${response.status}`);
        if (review && (payload.spec_stage_approval?.ok === false || payload.approval?.status !== 'approved')) {
            throw specError(payload.spec_stage_approval || payload, 'Spec approval was not accepted. Refresh its state before continuing.');
        }
        return payload;
    }, []);

    const dispatchRunCommand = useCallback(async (runId: string, command: string, reason?: string) => {
        const response = await fetch(`/api/runs/${runId}/commands/${command}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason }),
            signal: AbortSignal.timeout(10_000),
        });

        if (!response.ok) {
            const detail = await response.text().catch(() => '');
            throw new Error(detail || `Run command failed: ${response.status}`);
        }

        return response.json().catch(() => ({}));
    }, []);

    return {
        messages,
        isLoading,
        sendMessage,
        stop,
        settleTerminalStream,
        setMessages,
        sendToolOutput,
        resolveApproval,
        dispatchRunCommand,
        submittedRunId,
        isRunAcceptancePending,
        getSubmittedRunId,
    };
}
