"use client";

import dynamic from "next/dynamic";
import { ChatWindow } from "@/components/chat/ChatWindow";
import { InputArea } from "@/components/chat/InputArea";
import { useLangGraphStream } from "@/hooks/use-langgraph-stream";
import {
    cloneMessages,
    normalizeMessagesForState,
    normalizeProjectedMessages,
    WEB_STREAM_LIFECYCLE_OPTIONS,
} from "@/lib/chat-stream-state";
import { normalizeRealtimeEvent } from "@/lib/realtime";
import {
    RuntimeId,
    RuntimeTimelineEntry,
    buildRuntimeStageModel,
    buildRuntimeTimelineEntryFromEvent,
    mergeRuntimeTimeline,
    normalizeRuntimeTimeline,
} from "@/lib/runtime-stage";
import { Message } from "@/store/chat-types";
import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { CreateConversationPayload, useConversationContext } from "@/context/ConversationContext";
import { useSession } from "next-auth/react";
import { LoginDialog } from "@/components/auth/LoginDialog";
import { Bot, FolderTree, RefreshCw } from "lucide-react";
import { TodosHUD } from "@/components/chat/TodosHUD";
import { ProcessesHUD } from "@/components/chat/ProcessesHUD";
import { RunControlBar } from "@/components/chat/RunControlBar";
import { Button } from "@/components/ui/button";
import { RuntimeDock } from "@/components/chat/RuntimeDock";
import { useLocale, useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";
import { cn } from "@/lib/utils";
import {
    createInitialSessionRealtimeMessageState,
    deriveAuthoritativeSessionView,
    flushQueuedSessionRealtimeRuntimeEvents,
    queueSessionRealtimeRuntimeEvent,
    syncSessionRealtimeMessageState,
    type AuthoritativeSessionView,
    type SessionApprovalView,
} from "@v8/session-realtime";

const AskUserModal = dynamic(
    () => import("@/components/chat/AskUserModal").then((mod) => mod.AskUserModal),
    { ssr: false }
);

const ArtifactsPanel = dynamic(
    () => import("@/components/chat/ArtifactsPanel").then((mod) => mod.ArtifactsPanel),
    { ssr: false }
);

const RuntimeTimelinePanel = dynamic(
    () => import("@/components/chat/RuntimeTimelinePanel").then((mod) => mod.RuntimeTimelinePanel),
    { ssr: false }
);

interface ProjectDescriptor {
    id: string;
    name: string;
    description?: string;
    workspaceId?: string;
    workspacePath?: string;
    defaultScope?: string;
    tags?: string[];
    active?: boolean;
}

interface ScopeBindingView {
    projectId?: string;
    workspaceId?: string;
    workspacePath?: string;
    resolvedScope: string;
    scopeSource?: string;
    scopeConfidence?: number;
}

interface RunRecordView {
    id: string;
    status: string;
    started_at?: string;
    finished_at?: string;
    metadata?: Record<string, unknown>;
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function asNullableString(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

function normalizeScopeBinding(raw: unknown): ScopeBindingView | null {
    if (!raw || typeof raw !== "object") {
        return null;
    }

    const record = raw as Record<string, unknown>;
    const resolvedScope = (record.resolved_scope || record.resolvedScope) as string | undefined;
    if (!resolvedScope) {
        return null;
    }

    return {
        projectId: (record.project_id || record.projectId) as string | undefined,
        workspaceId: (record.workspace_id || record.workspaceId) as string | undefined,
        workspacePath: (record.workspace_path || record.workspacePath) as string | undefined,
        resolvedScope,
        scopeSource: (record.scope_source || record.scopeSource) as string | undefined,
        scopeConfidence: Number(record.scope_confidence || record.scopeConfidence || 0) || undefined,
    };
}

function normalizeWorkflowStatusForRunBar(status?: string | null): string | undefined {
    if (!status) return undefined;
    if (status === "recoverable_failed") return "failed";
    return status;
}

function deriveHistoryPreview(
    messages: Message[],
    projectionSummary?: AuthoritativeSessionView["summary"] | null,
): string | undefined {
    const projectedPreview = String(
        projectionSummary?.previewExcerpt
        || (projectionSummary as Record<string, unknown> | null)?.lastNarrativeExcerpt
        || ""
    ).trim();
    if (projectedPreview) {
        return projectedPreview.slice(0, 120);
    }

    for (const message of [...messages].reverse()) {
        if (message.role !== "assistant" && message.role !== "user") {
            continue;
        }
        const content = String(message.content || "").trim();
        if (content) {
            return content.slice(0, 120);
        }
    }
    return undefined;
}



export default function ChatClient() {
    const t = useT();
    const { locale } = useLocale();
    const { status, data: session } = useSession();
    const searchParams = useSearchParams();
    const urlId = searchParams.get("id");
    const router = useRouter();

    // Use a true React state to track the active conversation ID.
    // This is crucial because window.history.replaceState does not trigger Next.js router updates,
    // which would cause `sendMessage` to send `conversationId: null` on subsequent messages 
    // and spawn duplicate history entries.
    const [activeConversationId, setActiveConversationId] = useState<string | null>(urlId);

    useEffect(() => {
        setActiveConversationId(urlId);
    }, [urlId]);

    // Track which conversation is currently streaming to prevent overwriting state
    const streamingConversationIdRef = useRef<string | null>(null);

    // Sound Effect Logic
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const lastMessageIdRef = useRef<string | null>(null);
    const lastMessageLengthRef = useRef<number>(0);

    // Initialize Audio
    useEffect(() => {
        audioRef.current = new Audio("/message-pop.mp3");
        audioRef.current.volume = 0.5;
    }, []);

    const [input, setInput] = useState("");
    const { refreshConversations, createConversation, patchConversationSummary } = useConversationContext();
    const [askUserModalOpen, setAskUserModalOpen] = useState(false);
    const [askUserQuestion, setAskUserQuestion] = useState("");
    const [askUserToolCallId, setAskUserToolCallId] = useState("");
    const [askUserApprovalId, setAskUserApprovalId] = useState("");
    const [askUserRunId, setAskUserRunId] = useState("");
    const [askUserInteractionKind, setAskUserInteractionKind] = useState<"ask_user" | "approval">("approval");
    const [projects, setProjects] = useState<ProjectDescriptor[]>([]);
    const [defaultProjectId, setDefaultProjectId] = useState<string | null>(null);
    const [selectedProjectId, setSelectedProjectId] = useState("");
    const [scopeBinding, setScopeBinding] = useState<ScopeBindingView | null>(null);
    const [scopeLoading, setScopeLoading] = useState(false);
    const [projectsLoading, setProjectsLoading] = useState(false);
    const [runEntries, setRunEntries] = useState<RunRecordView[]>([]);
    const [runActionLoading, setRunActionLoading] = useState(false);
    const [sessionProjection, setSessionProjection] = useState<AuthoritativeSessionView | null>(null);
    const [isTimelineOpen, setIsTimelineOpen] = useState(false);
    const [selectedRuntimeId, setSelectedRuntimeId] = useState<RuntimeId | null>(null);
    const [isContextExpanded, setIsContextExpanded] = useState(false);
    const [localHour, setLocalHour] = useState<number>(9);
    const viewportBaselineRef = useRef(0);
    const [mobileKeyboardInset, setMobileKeyboardInset] = useState(0);

    useEffect(() => {
        if (typeof window === "undefined") {
            return;
        }

        const mediaQuery = window.matchMedia("(max-width: 767px)");
        const visualViewport = window.visualViewport;

        const updateMobileViewport = () => {
            if (!mediaQuery.matches) {
                viewportBaselineRef.current = 0;
                setMobileKeyboardInset(0);
                return;
            }

            const currentHeight = Math.round(visualViewport?.height ?? window.innerHeight);
            if (viewportBaselineRef.current === 0 || currentHeight > viewportBaselineRef.current) {
                viewportBaselineRef.current = currentHeight;
            }

            const baselineHeight = viewportBaselineRef.current || currentHeight;
            const offsetTop = Math.max(0, Math.round(visualViewport?.offsetTop ?? 0));
            const visualViewportInset = Math.max(0, Math.round(window.innerHeight - currentHeight - offsetTop));
            const shrinkInset = Math.max(0, baselineHeight - currentHeight);
            const nextInset = Math.max(visualViewportInset, shrinkInset > 96 ? shrinkInset : 0);

            setMobileKeyboardInset(nextInset > 24 ? nextInset : 0);
        };

        const handleViewportReset = () => {
            viewportBaselineRef.current = 0;
            updateMobileViewport();
        };

        updateMobileViewport();
        visualViewport?.addEventListener("resize", updateMobileViewport);
        visualViewport?.addEventListener("scroll", updateMobileViewport);
        window.addEventListener("resize", updateMobileViewport);
        window.addEventListener("orientationchange", handleViewportReset);

        if (typeof mediaQuery.addEventListener === "function") {
            mediaQuery.addEventListener("change", handleViewportReset);
        } else {
            mediaQuery.addListener(handleViewportReset);
        }

        return () => {
            visualViewport?.removeEventListener("resize", updateMobileViewport);
            visualViewport?.removeEventListener("scroll", updateMobileViewport);
            window.removeEventListener("resize", updateMobileViewport);
            window.removeEventListener("orientationchange", handleViewportReset);
            if (typeof mediaQuery.removeEventListener === "function") {
                mediaQuery.removeEventListener("change", handleViewportReset);
            } else {
                mediaQuery.removeListener(handleViewportReset);
            }
        };
    }, []);

    // Initialize Hook
    const { messages, isLoading, sendMessage, stop, setMessages, sendToolOutput, resolveApproval, dispatchRunCommand } = useLangGraphStream({
        apiEndpoint: `/api/chat`,
        onFinish: () => {
            refreshConversations();
            streamingConversationIdRef.current = null; // Reset when done
            const conversationId = activeConversationIdRef.current;
            if (conversationId) {
                void loadRuns(conversationId);
            }
        },
        onConnect: (newId) => {
            // Record that we are streaming this ID
            streamingConversationIdRef.current = newId;

            // Silently update URL if it changes (e.g. from new chat)
            if (activeConversationId !== newId) {
                console.log(`[ChatClient] Conversation ID established: ${newId}`);
                setActiveConversationId(newId); // Update React State immediately
                window.history.replaceState(null, '', `/chat?id=${newId}`);
                // NOTE: We don't use router.push/replace here to avoid triggering specific useEffect re-runs
                // that might reset the chat state.
            }
            void loadRuns(newId);
        },
        onError: (error) => {
            console.error("Chat error:", error);
            streamingConversationIdRef.current = null;
            if (error.message.includes("Conversation not found") || error.message.includes("404")) {
                router.replace('/chat');
            }
        },
        onCustomEvent: (event) => {
            if (event.name === "ask_user") {
                applyPendingApproval({
                    id: event.data.approvalId || "",
                    run_id: event.run_id || event.data.runId || "",
                    approval_kind: event.data.approvalKind || "",
                    interactionKind: event.data.interactionKind || "",
                    request: {
                        question: event.data.question,
                        toolCallId: event.data.toolCallId,
                        approvalKind: event.data.approvalKind,
                        interactionKind: event.data.interactionKind,
                    },
                });
                const conversationId = activeConversationIdRef.current;
                if (conversationId) {
                    void loadRuns(conversationId);
                }
            }
            if (event.name === "run_controlled") {
                const conversationId = activeConversationIdRef.current;
                if (conversationId) {
                    void loadRuns(conversationId);
                }
            }
        }
    });

    const activeConversationIdRef = useRef<string | null>(activeConversationId);
    const isLoadingRef = useRef(isLoading);
    const messagesRef = useRef<Message[]>(messages);
    const realtimeMessageStateRef = useRef(
        createInitialSessionRealtimeMessageState<Message>([], WEB_STREAM_LIFECYCLE_OPTIONS),
    );
    const latestRealtimeSeqRef = useRef<number>(0);
    const runtimeFlushFrameRef = useRef<number | null>(null);
    const runtimeFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const pendingConversationCreationRef = useRef<Promise<string | null> | null>(null);
    const selectedProject = projects.find((project) => project.id === selectedProjectId) || null;
    const currentRun = sessionProjection?.currentRun || runEntries[0] || null;
    const hasPendingApproval = Boolean(askUserApprovalId);
    const projectionRunId = (sessionProjection?.controls?.runId || sessionProjection?.currentRun?.id || sessionProjection?.workflow?.rootRunId) ?? undefined;
    const effectiveRunId = currentRun?.id || askUserRunId || projectionRunId;
    const effectiveStatus = hasPendingApproval
        ? "waiting_approval"
        : sessionProjection?.runtimeStatus
            || currentRun?.status
            || normalizeWorkflowStatusForRunBar(sessionProjection?.controls?.workflowStatus)
            || normalizeWorkflowStatusForRunBar(sessionProjection?.workflow?.status);
    const projectionPendingApproval = (sessionProjection?.approvals?.length || 0) > 0;
    const effectivePendingApproval = hasPendingApproval || projectionPendingApproval || currentRun?.status === "waiting_approval";
    const projectionTodos = sessionProjection?.todos?.items || [];
    const projectionTodoStale = Boolean(sessionProjection?.todos?.isStale);
    const projectionProcesses = sessionProjection?.processes || [];
    const projectionContextReferences = sessionProjection?.contextReferences || [];
    const projectionRuntimeTimeline = useMemo(
        () => normalizeRuntimeTimeline(sessionProjection?.runtimeTimeline || []),
        [sessionProjection?.runtimeTimeline],
    );
    const runtimeStageModel = useMemo(() => buildRuntimeStageModel(messages, {
        ownerRuntime: sessionProjection?.workflow?.ownerRuntime || sessionProjection?.summary?.ownerRuntime || null,
        status: effectiveStatus || null,
        pendingApproval: effectivePendingApproval,
        recoverable: Boolean(sessionProjection?.recoverable?.recoverable),
        currentStepTitle: sessionProjection?.workflow?.currentStepTitle || sessionProjection?.summary?.currentStepTitle || null,
        runtimeTimeline: projectionRuntimeTimeline,
    }), [effectivePendingApproval, effectiveStatus, messages, projectionRuntimeTimeline, sessionProjection?.recoverable?.recoverable, sessionProjection?.summary?.currentStepTitle, sessionProjection?.summary?.ownerRuntime, sessionProjection?.workflow?.currentStepTitle, sessionProjection?.workflow?.ownerRuntime]);
    const historyPreview = useMemo(
        () => deriveHistoryPreview(messages, sessionProjection?.summary),
        [messages, sessionProjection?.summary],
    );
    const contentShellClassName = "w-full max-w-[68rem]";
    const greetingText = useMemo(() => {
        const hour = localHour;
        if (locale === "en") {
            if (hour < 12) return "Good morning";
            if (hour < 18) return "Good afternoon";
            return "Good evening";
        }
        if (hour < 12) return "上午好";
        if (hour < 18) return "下午好";
        return "晚上好";
    }, [localHour, locale]);

    useEffect(() => {
        setLocalHour(new Date().getHours());
    }, []);

    useEffect(() => {
        if (runtimeStageModel.activeRuntimeId) {
            setSelectedRuntimeId((prev) => prev || runtimeStageModel.activeRuntimeId);
        }
    }, [runtimeStageModel.activeRuntimeId]);

    useEffect(() => {
        if (!activeConversationId) {
            return;
        }
        patchConversationSummary(activeConversationId, {
            lastActivityAt: new Date().toISOString(),
            workflowStatus: effectiveStatus,
            statusLabel: sessionProjection?.summary?.workflowStatus === effectiveStatus
                ? (sessionProjection?.summary as Record<string, unknown> | null)?.statusLabel as string | undefined
                : undefined,
            ownerRuntime: sessionProjection?.workflow?.ownerRuntime || sessionProjection?.summary?.ownerRuntime || undefined,
            currentStepTitle: sessionProjection?.workflow?.currentStepTitle || sessionProjection?.summary?.currentStepTitle || undefined,
            previewExcerpt: historyPreview,
            lastNarrativeExcerpt: historyPreview,
            lastRuntimeSummary: sessionProjection?.summary && typeof (sessionProjection.summary as Record<string, unknown>).lastRuntimeSummary === "string"
                ? String((sessionProjection.summary as Record<string, unknown>).lastRuntimeSummary)
                : (sessionProjection?.workflow?.currentStepTitle || sessionProjection?.summary?.currentStepTitle || undefined),
            pendingApprovalCount: effectivePendingApproval
                ? Math.max(
                    Number(sessionProjection?.approvals?.length || 0),
                    Number(sessionProjection?.controls?.pendingApprovalCount || 0),
                    askUserApprovalId ? 1 : 0,
                )
                : 0,
            hasPendingApproval: effectivePendingApproval,
            recoverable: Boolean(sessionProjection?.recoverable?.recoverable),
            controls: sessionProjection?.controls || undefined,
        });
    }, [
        activeConversationId,
        askUserApprovalId,
        effectivePendingApproval,
        effectiveStatus,
        historyPreview,
        patchConversationSummary,
        sessionProjection,
    ]);

    const clearApprovalState = useCallback((options?: { closeModal?: boolean }) => {
        setAskUserApprovalId("");
        setAskUserQuestion("");
        setAskUserToolCallId("");
        setAskUserRunId("");
        setAskUserInteractionKind("approval");
        if (options?.closeModal !== false) {
            setAskUserModalOpen(false);
        }
    }, []);

    const applyPendingApproval = useCallback((approval: {
        id?: string;
        approval_id?: string;
        run_id?: string;
        runId?: string;
        approval_kind?: string;
        interactionKind?: string;
        question?: string;
        prompt?: string;
        toolCallId?: string;
        request?: { question?: string; prompt?: string; toolCallId?: string; approvalKind?: string; interactionKind?: string };
    } | null, options?: { openModal?: boolean }) => {
        if (!approval) {
            clearApprovalState();
            return;
        }

        const request = approval.request || {};
        const approvalId = approval.id || approval.approval_id || "";
        const question = approval.question || approval.prompt || request.question || request.prompt || "";
        if (!approvalId || !question) {
            clearApprovalState();
            return;
        }

        const interactionKind =
            approval.interactionKind
            || request.interactionKind
            || (approvalId ? "approval" : "ask_user");
        setAskUserApprovalId(approvalId);
        setAskUserToolCallId(approval.toolCallId || request.toolCallId || "");
        setAskUserQuestion(question);
        setAskUserRunId(approval.run_id || approval.runId || "");
        setAskUserInteractionKind(interactionKind === "ask_user" ? "ask_user" : "approval");
        const shouldOpenModal =
            typeof options?.openModal === "boolean"
                ? options.openModal
                : interactionKind === "ask_user";
        if (shouldOpenModal) {
            setAskUserModalOpen(true);
        }
    }, [clearApprovalState]);

    useEffect(() => {
        activeConversationIdRef.current = activeConversationId;
    }, [activeConversationId]);

    useEffect(() => {
        isLoadingRef.current = isLoading;
    }, [isLoading]);

    useEffect(() => {
        messagesRef.current = messages;
        realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
            messages,
            WEB_STREAM_LIFECYCLE_OPTIONS,
        );
    }, [messages]);

    const isLocalStreamActive = useCallback((sessionId: string | null | undefined) => {
        if (!sessionId) return false;
        return isLoadingRef.current && streamingConversationIdRef.current === sessionId;
    }, []);

    const applyProjectedSnapshot = useCallback((projectedMessages: unknown[], latestSeq = 0) => {
        if (runtimeFlushFrameRef.current !== null && typeof window !== "undefined") {
            window.cancelAnimationFrame(runtimeFlushFrameRef.current);
            runtimeFlushFrameRef.current = null;
        }
        if (runtimeFlushTimerRef.current) {
            clearTimeout(runtimeFlushTimerRef.current);
            runtimeFlushTimerRef.current = null;
        }
        const normalized = normalizeProjectedMessages(projectedMessages);
        realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
            normalized,
            WEB_STREAM_LIFECYCLE_OPTIONS,
        );
        latestRealtimeSeqRef.current = latestSeq;
        messagesRef.current = normalizeMessagesForState(normalized);
        setMessages(normalizeMessagesForState(normalized));
        return normalized;
    }, [setMessages]);

    const loadConversationHistory = useCallback(async (conversationId: string) => {
        const snapshotRes = await fetch(`/api/realtime/sessions/${conversationId}/snapshot`, { cache: "no-store" });
        if (!snapshotRes.ok) {
            if (snapshotRes.status === 404) {
                router.replace("/chat");
                return;
            }
            throw new Error(`Failed to load authoritative snapshot: ${snapshotRes.status}`);
        }

        const data = await snapshotRes.json();
        const snapshotPayload = (data?.payload && typeof data.payload === "object") ? data.payload : data;
        const projection = deriveAuthoritativeSessionView(snapshotPayload).view;
        setSessionProjection(projection);
        if ((projection?.approvals?.length || 0) > 0) {
            applyPendingApproval(projection?.approvals?.[0] || null, { openModal: false });
        }

        const authoritativeMessages = Array.isArray(snapshotPayload?.snapshot?.messages)
            ? snapshotPayload.snapshot.messages
            : Array.isArray(snapshotPayload?.messages)
                ? snapshotPayload.messages
                : [];
        const normalized = normalizeProjectedMessages(authoritativeMessages);
        latestRealtimeSeqRef.current = Number(snapshotPayload?.latestSeq || snapshotPayload?.snapshot?.latest_seq || 0);
        realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
            normalized,
            WEB_STREAM_LIFECYCLE_OPTIONS,
        );
        messagesRef.current = normalizeMessagesForState(normalized);
        setMessages(normalizeMessagesForState(normalized));
    }, [applyPendingApproval, applyProjectedSnapshot, router, setMessages]);

    const loadProjects = useCallback(async () => {
        setProjectsLoading(true);
        try {
            const res = await fetch("/api/projects", { cache: "no-store" });
            if (!res.ok) {
                return;
            }
            const data = await res.json();
            const nextProjects = Array.isArray(data?.projects) ? data.projects : [];
            const nextDefaultProjectId = typeof data?.defaultProjectId === "string" ? data.defaultProjectId : null;
            setProjects(nextProjects);
            setDefaultProjectId(nextDefaultProjectId);
            setSelectedProjectId((current) => {
                if (current || activeConversationIdRef.current || !nextDefaultProjectId) {
                    return current;
                }
                return nextDefaultProjectId;
            });
        } catch (error) {
            console.warn("[ChatClient] Failed to load projects:", error);
        } finally {
            setProjectsLoading(false);
        }
    }, []);

    const loadSessionScope = useCallback(async (conversationId: string) => {
        setScopeLoading(true);
        try {
            const res = await fetch(`/api/sessions/${conversationId}/scope`, { cache: "no-store" });
            if (!res.ok) {
                setScopeBinding(null);
                return;
            }
            const data = await res.json();
            const normalized = normalizeScopeBinding(data?.binding);
            setScopeBinding(normalized);
            if (normalized?.projectId) {
                setSelectedProjectId(normalized.projectId);
            } else if (!normalized && defaultProjectId) {
                setSelectedProjectId(defaultProjectId);
            }
        } catch (error) {
            console.warn("[ChatClient] Failed to load scope binding:", error);
            setScopeBinding(null);
        } finally {
            setScopeLoading(false);
        }
    }, [defaultProjectId]);

    const loadRuns = useCallback(async (conversationId: string) => {
        try {
            const res = await fetch(`/api/runs?session_id=${encodeURIComponent(conversationId)}&limit=8`, { cache: "no-store" });
            if (!res.ok) {
                setRunEntries([]);
                return;
            }
            const data = await res.json().catch(() => ({}));
            setRunEntries(Array.isArray(data?.runs) ? data.runs : []);
        } catch (error) {
            console.warn("[ChatClient] Failed to load runs:", error);
            setRunEntries([]);
        }
    }, []);

    const buildScopePayload = useCallback((conversationId?: string | null) => ({
        conversationId: conversationId || activeConversationIdRef.current || undefined,
        projectId: selectedProjectId || undefined,
        workspaceId: selectedProject?.workspaceId,
        workspacePath: selectedProject?.workspacePath,
        // 用户显式选项目时，优先使用该项目的 defaultScope，避免旧 heuristic scope 抢优先级。
        scopeHint: selectedProjectId
            ? (selectedProject?.defaultScope || undefined)
            : (scopeBinding?.resolvedScope || selectedProject?.defaultScope),
        scopeMode: selectedProjectId ? "explicit" : "mixed",
    }), [scopeBinding?.resolvedScope, selectedProject?.defaultScope, selectedProject?.workspaceId, selectedProject?.workspacePath, selectedProjectId]);

    const ensureConversationId = useCallback(async (seedTitle: string) => {
        if (activeConversationIdRef.current) {
            return activeConversationIdRef.current;
        }

        if (pendingConversationCreationRef.current) {
            return pendingConversationCreationRef.current;
        }

        const scopePayload = buildScopePayload(undefined);
        const creationPayload: CreateConversationPayload = {
            title: seedTitle.trim().slice(0, 50) || "New Chat",
            projectId: scopePayload.projectId,
            workspaceId: scopePayload.workspaceId,
            workspacePath: scopePayload.workspacePath,
            scopeHint: scopePayload.scopeHint,
            scopeMode: scopePayload.scopeMode,
        };

        const creationPromise = (async () => {
            const newConversation = await createConversation(creationPayload);
            if (!newConversation?.id) {
                return null;
            }

            activeConversationIdRef.current = newConversation.id;
            setActiveConversationId(newConversation.id);
            window.history.replaceState(null, "", `/chat?id=${newConversation.id}`);

            try {
                await loadSessionScope(newConversation.id);
            } catch (error) {
                console.warn("[ChatClient] Failed to hydrate scope after session creation:", error);
            }

            return newConversation.id;
        })().finally(() => {
            pendingConversationCreationRef.current = null;
        });

        pendingConversationCreationRef.current = creationPromise;
        return creationPromise;
    }, [buildScopePayload, createConversation, loadSessionScope]);

    const handleProjectSelect = useCallback(async (projectId: string) => {
        const nextProjectId = projectId === "__auto__" ? "" : projectId;
        setSelectedProjectId(nextProjectId);

        if (!activeConversationIdRef.current) {
            if (!nextProjectId) {
                setScopeBinding(null);
            }
            return;
        }

        try {
            if (nextProjectId) {
                const nextProject = projects.find((project) => project.id === nextProjectId);
                const res = await fetch(`/api/sessions/${activeConversationIdRef.current}/scope`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        projectId: nextProjectId,
                        workspaceId: nextProject?.workspaceId,
                        workspacePath: nextProject?.workspacePath,
                        scopeHint: nextProject?.defaultScope,
                        scopeSource: "web_selected",
                        scopeConfidence: 1,
                    }),
                });
                if (!res.ok) {
                    throw new Error(`Scope update failed: ${res.status}`);
                }
            } else {
                const latestUserText = [...messages].reverse().find((message) => message.role === "user")?.content || "";
                const res = await fetch(`/api/sessions/${activeConversationIdRef.current}/scope/re-resolve`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        sessionId: activeConversationIdRef.current,
                        userQuery: latestUserText,
                        scopeMode: "mixed",
                    }),
                });
                if (!res.ok) {
                    throw new Error(`Scope re-resolve failed: ${res.status}`);
                }
            }
            await loadSessionScope(activeConversationIdRef.current);
            await refreshConversations();
        } catch (error) {
            console.error("[ChatClient] Failed to update session scope:", error);
        }
    }, [loadSessionScope, messages, projects, refreshConversations]);

    const handleReresolveScope = useCallback(async () => {
        if (!activeConversationIdRef.current) {
            return;
        }
        const latestUserText = [...messages].reverse().find((message) => message.role === "user")?.content || input;
        try {
            const res = await fetch(`/api/sessions/${activeConversationIdRef.current}/scope/re-resolve`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    sessionId: activeConversationIdRef.current,
                    userQuery: latestUserText,
                    projectId: selectedProjectId || undefined,
                    workspaceId: selectedProject?.workspaceId,
                    workspacePath: selectedProject?.workspacePath,
                    scopeMode: selectedProjectId ? "explicit" : "mixed",
                }),
            });
            if (!res.ok) {
                throw new Error(`Scope re-resolve failed: ${res.status}`);
            }
            await loadSessionScope(activeConversationIdRef.current);
            await refreshConversations();
        } catch (error) {
            console.error("[ChatClient] Failed to re-resolve scope:", error);
        }
    }, [input, loadSessionScope, messages, refreshConversations, selectedProject?.workspaceId, selectedProject?.workspacePath, selectedProjectId]);

    useEffect(() => {
        if (status === "authenticated") {
            void loadProjects();
            return;
        }
        if (status === "unauthenticated") {
            setProjects([]);
            setDefaultProjectId(null);
            setSelectedProjectId("");
            setProjectsLoading(false);
        }
    }, [loadProjects, status]);

    const applyRemoteRuntimeEvent = useCallback((rawEvent: unknown) => {
        const conversationId = activeConversationIdRef.current;
        if (!conversationId) {
            return;
        }

        const runtimeTimelineEntry = buildRuntimeTimelineEntryFromEvent(rawEvent);
        const normalizedEvent = normalizeRealtimeEvent(rawEvent);
        if (!normalizedEvent) {
            return;
        }

        const localStreamActive = isLocalStreamActive(conversationId);
        if (
            localStreamActive
            && !runtimeTimelineEntry
            && !(normalizedEvent.type === "custom_event" && normalizedEvent.name === "artifact_recorded")
        ) {
            return;
        }

        if (normalizedEvent.type === "custom_event" && normalizedEvent.name === "ask_user") {
            const eventData = typeof normalizedEvent.data === "object" && normalizedEvent.data !== null
                ? normalizedEvent.data as Record<string, unknown>
                : {};
            applyPendingApproval({
                id: String(eventData.approvalId || ""),
                run_id: String((normalizedEvent as Record<string, unknown>).run_id || ""),
                request: {
                    question: String(eventData.question || ""),
                    toolCallId: String(eventData.toolCallId || ""),
                },
            });
        }

        if (normalizedEvent.topic === "approval.approved" || normalizedEvent.topic === "approval.rejected") {
            clearApprovalState();
        }

        const trackedTopics = new Set([
            "run.state.changed",
            "run.completed",
            "run.failed",
            "run.cancelled",
            "run.paused",
            "run.resumed",
            "run.interrupted",
            "run.retry.requested",
            "approval.requested",
            "approval.approved",
            "approval.rejected",
        ]);
        if (normalizedEvent.topic && trackedTopics.has(String(normalizedEvent.topic))) {
            void loadRuns(conversationId);
        }

        const rawSeq = typeof rawEvent === "object" && rawEvent !== null
            ? Number((rawEvent as Record<string, unknown>).seq || 0)
            : 0;
        const normalizedSeq = Number((normalizedEvent as Record<string, unknown>).seq || 0);
        const eventSeq = rawSeq || normalizedSeq;
        if (eventSeq && eventSeq <= latestRealtimeSeqRef.current) {
            return;
        }

        if (eventSeq) {
            latestRealtimeSeqRef.current = eventSeq;
        }

        if (runtimeTimelineEntry) {
            setSessionProjection((current) => {
                if (!current) {
                    return current;
                }
                return {
                    ...current,
                    runtimeTimeline: mergeRuntimeTimeline(
                        normalizeRuntimeTimeline(current.runtimeTimeline || []),
                        [runtimeTimelineEntry],
                    ),
                };
            });
        }
        queueSessionRealtimeRuntimeEvent(realtimeMessageStateRef.current, normalizedEvent);

        const flush = () => {
            runtimeFlushFrameRef.current = null;
            runtimeFlushTimerRef.current = null;
            const nextState = flushQueuedSessionRealtimeRuntimeEvents(
                messagesRef.current,
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

            messagesRef.current = nextState.messages;
            setMessages(nextState.messages);
        };

        if (runtimeFlushFrameRef.current !== null || runtimeFlushTimerRef.current) {
            return;
        }

        if (typeof window !== "undefined" && typeof window.requestAnimationFrame === "function") {
            runtimeFlushFrameRef.current = window.requestAnimationFrame(flush);
        } else {
            runtimeFlushTimerRef.current = setTimeout(flush, 16);
        }
    }, [applyPendingApproval, clearApprovalState, isLocalStreamActive, loadRuns, setMessages]);

    useEffect(() => {
        return () => {
            if (runtimeFlushFrameRef.current !== null && typeof window !== "undefined") {
                window.cancelAnimationFrame(runtimeFlushFrameRef.current);
            }
            if (runtimeFlushTimerRef.current) {
                clearTimeout(runtimeFlushTimerRef.current);
            }
            realtimeMessageStateRef.current.pendingRuntimeEvents = [];
        };
    }, []);

    const handleAskUserSubmit = async (answer: string, approve: boolean) => {
        try {
            if (askUserApprovalId) {
                await resolveApproval(askUserApprovalId, answer, approve);
            } else if (approve) {
                await sendToolOutput(askUserToolCallId, answer, buildScopePayload(activeConversationId));
            }
            clearApprovalState();
            if (activeConversationIdRef.current) {
                void loadRuns(activeConversationIdRef.current);
            }
        } catch (error) {
            console.error("[ChatClient] Failed to resolve ask_user request:", error);
        }
    };

    const handleInterruptRun = useCallback(async () => {
        const targetRunId = currentRun?.id || projectionRunId;
        if (!targetRunId) {
            return;
        }
        setRunActionLoading(true);
        try {
            await dispatchRunCommand(targetRunId, "interrupt", "web_interrupt");
            if (activeConversationIdRef.current) {
                await loadRuns(activeConversationIdRef.current);
            }
        } catch (error) {
            console.error("[ChatClient] Failed to interrupt run:", error);
        } finally {
            setRunActionLoading(false);
        }
    }, [currentRun?.id, dispatchRunCommand, loadRuns, projectionRunId]);

    const handleRetryRun = useCallback(async () => {
        const targetRunId = currentRun?.id || projectionRunId;
        if (!targetRunId) {
            return;
        }
        setRunActionLoading(true);
        try {
            await dispatchRunCommand(targetRunId, "retry", "web_retry");
            if (activeConversationIdRef.current) {
                await loadRuns(activeConversationIdRef.current);
            }
        } catch (error) {
            console.error("[ChatClient] Failed to retry run:", error);
        } finally {
            setRunActionLoading(false);
        }
    }, [currentRun?.id, dispatchRunCommand, loadRuns, projectionRunId]);

    // Handle New Message Sound Effect
    useEffect(() => {
        if (messages.length === 0) return;

        const latestMsg = messages[messages.length - 1];

        // 1. Filter out user messages & system messages
        if (latestMsg.role !== 'assistant') {
            lastMessageIdRef.current = latestMsg.id;
            lastMessageLengthRef.current = latestMsg.nodes.length; // Approximate
            return;
        }

        // 2. Identify if this is a NEW message (ID changed)
        const isNewMessageObject = latestMsg.id !== lastMessageIdRef.current;

        if (isNewMessageObject) {
            // New message object detected.
            // If it HAS content immediately, it's likely history loading (OR a very fast full response).
            // But usually history loading brings full content.
            // To be safe: If it's history, we usually setMessages with MANY messages.
            // Check if we are currently loading history? No, relying on state is tricky.
            // Heuristic: If content length > 0 immediately on first sight, Assume History/Snapshot. 
            // Real streaming starts with empty string usually.

            // However, our optimistic UI adds an empty placeholder `currentAiMsg` first.
            // So for streaming, we see: ID_NEW, Length 0. -> Then Length > 0.

            lastMessageIdRef.current = latestMsg.id;
            lastMessageLengthRef.current = latestMsg.content.length;
        } else {
            // Same message object, content updating.
            const currentLength = latestMsg.content.length;
            const previousLength = lastMessageLengthRef.current;

            // 3. Trigger Sound: If length transitions from 0 to > 0
            if (previousLength === 0 && currentLength > 0) {
                // Check if it's "智能主管" (Supervisor) or actual Agent
                // We might want sound for both.
                // Play Sound!
                audioRef.current?.play().catch(e => console.error("Audio play failed", e));
            }

            lastMessageLengthRef.current = currentLength;
        }

    }, [messages]);

    // Handle Input Change
    const handleInputChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => {
        setInput(e.target.value);
    };

    // Handle Send
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const handleSend = async (e: React.FormEvent<HTMLFormElement>, options?: { data?: any }) => {
        e.preventDefault();
        const hasText = input.trim().length > 0;
        const hasCommandPreset = Boolean(options?.data?.commandPreset?.name);
        const hasSkillReferences = Array.isArray(options?.data?.skillReferences) && options.data.skillReferences.length > 0;
        const hasFiles = Array.isArray(options?.data?.fileUrls) && options.data.fileUrls.length > 0;
        if (status !== 'authenticated' || (!hasText && !hasCommandPreset && !hasSkillReferences && !hasFiles) || isLoading) return;

        const currentInput = input;
        setInput(""); // Clear immediately (Optimistic)

        // [REMOVED] Optimistic UI: The useLangGraphStream hook now handles both User and AI placeholders internally.
        // This prevents the "Flicker" caused by state conflicts (Client vs Hook)

        try {
            const seedTitle = currentInput.trim()
                || options?.data?.commandPreset?.name
                || options?.data?.skillReferences?.[0]?.name
                || (hasFiles ? t(lt("新文件任务", "New file task")) : t(lt("新任务", "New task")));
            const ensuredConversationId = await ensureConversationId(seedTitle);
            if (!ensuredConversationId) {
                throw new Error("Conversation creation failed");
            }

            await sendMessage(currentInput, {
                agentId: undefined, // selectedAgent?.id,
                userId: session?.user?.id,
                ...buildScopePayload(ensuredConversationId),
                ...options?.data // Pass data (fileUrls) to sendMessage
            });
        } catch (error) {
            console.error("[ChatClient] Failed to send initial message:", error);
            setInput(currentInput);
        }
    };

    // Fetch history when ID changes
    useEffect(() => {
        if (activeConversationId) {
            // CRITICAL FIX: If we are currently streaming content for this ID, 
            // DO NOT fetch from DB. The DB history is stale (empty) compared to our live stream.
            // Fetching would overwrite our live state with empty history, causing the "Flicker/Disappear" bug.
            if (isLoading && streamingConversationIdRef.current === activeConversationId) {
                console.log(`[ChatClient] Skipping history fetch for ${activeConversationId} (Streaming active)`);
                return;
            }
            console.log(`[ChatClient] Fetching history for ${activeConversationId}`);
            void loadConversationHistory(activeConversationId).catch((err) => {
                console.error("Failed to load chat history", err);
            });
            void loadSessionScope(activeConversationId);
            void loadRuns(activeConversationId);
        } else {
            console.log("[ChatClient] New conversation reset");
            if (isLoading) stop();
            latestRealtimeSeqRef.current = 0;
            realtimeMessageStateRef.current = createInitialSessionRealtimeMessageState<Message>([], WEB_STREAM_LIFECYCLE_OPTIONS);
            setScopeBinding(null);
            setSessionProjection(null);
            clearApprovalState();
            setRunEntries([]);
            if (defaultProjectId && !selectedProjectId) {
                setSelectedProjectId(defaultProjectId);
            }
            messagesRef.current = [];
            setMessages([]);
        }
    }, [activeConversationId, clearApprovalState, defaultProjectId, isLoading, loadConversationHistory, loadRuns, loadSessionScope, selectedProjectId, stop, setMessages]);

    useEffect(() => {
        if (!activeConversationId) {
            return;
        }

        const eventSource = new EventSource(`/api/realtime/sessions/${activeConversationId}/stream`);

        const handleSnapshot = (event: MessageEvent) => {
            if (isLocalStreamActive(activeConversationId)) {
                return;
            }
            try {
                const data = JSON.parse(event.data);
                const snapshotPayload = (data?.payload && typeof data.payload === "object") ? data.payload : data;
                setSessionProjection((current) => deriveAuthoritativeSessionView(snapshotPayload).view || current);
                if (Array.isArray(snapshotPayload?.snapshot?.messages)) {
                    applyProjectedSnapshot(
                        snapshotPayload.snapshot.messages,
                        Number(snapshotPayload.latestSeq || snapshotPayload.snapshot?.latest_seq || 0),
                    );
                }
            } catch (error) {
                console.warn("[ChatClient] Failed to parse snapshot SSE payload:", error);
            }
        };

        const handleRuntime = (event: MessageEvent) => {
            try {
                const rawEvent = JSON.parse(event.data);
                applyRemoteRuntimeEvent(rawEvent);
            } catch (error) {
                console.warn("[ChatClient] Failed to parse runtime SSE payload:", error);
            }
        };

        const handleError = () => {
            if (!isLocalStreamActive(activeConversationId)) {
                void loadConversationHistory(activeConversationId).catch((error) => {
                    console.warn("[ChatClient] Realtime resync failed:", error);
                });
            }
        };

        eventSource.addEventListener("snapshot", handleSnapshot as EventListener);
        eventSource.addEventListener("runtime", handleRuntime as EventListener);
        eventSource.addEventListener("error", handleError as EventListener);

        return () => {
            eventSource.removeEventListener("snapshot", handleSnapshot as EventListener);
            eventSource.removeEventListener("runtime", handleRuntime as EventListener);
            eventSource.removeEventListener("error", handleError as EventListener);
            eventSource.close();
        };
    }, [activeConversationId, applyProjectedSnapshot, applyRemoteRuntimeEvent, isLocalStreamActive, loadConversationHistory]);

    // Auth Check UI
    if (status === "loading") {
        return <div className="flex h-full items-center justify-center">{t(lt("加载中...", "Loading..."))}</div>;
    }

    if (status === "unauthenticated") {
        return (
            <div className="flex flex-col items-center justify-center h-full space-y-6">
                <div className="w-16 h-16 rounded-full bg-primary/10 flex items-center justify-center">
                    <Bot className="w-8 h-8 text-primary" />
                </div>
                <div className="text-center space-y-2">
                    <h1 className="text-2xl font-bold">{t(lt("欢迎使用 V8 Agent OS", "Welcome to V8 Agent OS"))}</h1>
                    <p className="text-muted-foreground">{t(lt("请登录以开始对话", "Sign in to start chatting"))}</p>
                </div>
                <div className="scale-125">
                    <LoginDialog />
                </div>
            </div>
        );
    }

    const composerShellStyle = {
        paddingBottom: mobileKeyboardInset > 0
            ? `calc(0.5rem + env(safe-area-inset-bottom) + ${mobileKeyboardInset}px)`
            : "calc(0.5rem + env(safe-area-inset-bottom))",
    };
    const hudStackStyle = mobileKeyboardInset > 0
        ? { maxHeight: "5rem" }
        : undefined;

    return (
        <div className="relative flex h-full min-h-0 w-full overflow-hidden overscroll-none bg-transparent">
            <div className={cn("mx-auto flex h-full min-h-0 w-full flex-col px-2 pt-0.5 sm:px-4 sm:pt-1 lg:px-6", contentShellClassName)}>
                <div className="shrink-0 flex flex-col gap-1">
                    <div className="scrollbar-none flex flex-nowrap items-center gap-1 overflow-x-auto overflow-y-hidden pb-0.5 sm:gap-1">
                        <button
                            type="button"
                            className={`inline-flex h-[28px] w-[28px] shrink-0 items-center justify-center rounded-xl border bg-background/78 text-muted-foreground shadow-sm backdrop-blur transition-colors hover:text-foreground sm:h-[30px] sm:w-[30px] ${
                                isContextExpanded
                                    ? "border-primary/35 bg-primary/8 text-primary"
                                    : "border-border/60"
                            }`}
                            onClick={() => setIsContextExpanded((current) => !current)}
                            aria-expanded={isContextExpanded}
                            aria-label={t(lt("项目上下文", "Project context"))}
                            title={isContextExpanded ? t(lt("收起项目上下文", "Collapse project context")) : t(lt("展开项目上下文", "Expand project context"))}
                        >
                            <FolderTree className="h-[11px] w-[11px] shrink-0 sm:h-[13px] sm:w-[13px]" />
                        </button>
                        <RunControlBar
                            runId={effectiveRunId}
                            status={effectiveStatus}
                            pendingApproval={effectivePendingApproval}
                            isBusy={runActionLoading || isLoading}
                            onInterrupt={handleInterruptRun}
                            onRetry={handleRetryRun}
                            onOpenApproval={() => {
                                if (askUserApprovalId && askUserInteractionKind === "ask_user") {
                                    setAskUserModalOpen(true);
                                    return;
                                }
                                if ((sessionProjection?.approvals?.length || 0) > 0) {
                                    applyPendingApproval(sessionProjection?.approvals?.[0] || null, {
                                        openModal:
                                            String(
                                                sessionProjection?.approvals?.[0]?.request?.interactionKind
                                                || "",
                                            ).trim() === "ask_user",
                                    });
                                }
                            }}
                        />
                        <div className="ml-auto flex shrink-0 justify-end">
                            <RuntimeDock
                                model={runtimeStageModel}
                                selectedRuntimeId={selectedRuntimeId}
                                isPanelOpen={isTimelineOpen}
                                onSelectRuntime={(runtimeId) => {
                                    if (isTimelineOpen && selectedRuntimeId === runtimeId) {
                                        setIsTimelineOpen(false);
                                        return;
                                    }
                                    setSelectedRuntimeId(runtimeId);
                                    setIsTimelineOpen(true);
                                }}
                            />
                        </div>
                    </div>

                    {isContextExpanded && (
                        <div className="rounded-[24px] border border-border/50 bg-background/82 px-3 py-3 shadow-sm backdrop-blur-xl sm:px-4">
                            <div className="space-y-3">
                                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                                    <div className="min-w-0 flex-1">
                                        <div className="flex items-center gap-2 text-sm font-medium">
                                            <FolderTree className="h-4 w-4 shrink-0 text-primary" />
                                            {t(lt("项目上下文", "Project context"))}
                                        </div>
                                        <p className="mt-1 text-xs text-muted-foreground">
                                            {t(lt("仅在需要时手动切换项目与 scope。", "Switch project and scope manually only when needed."))}
                                        </p>
                                    </div>
                                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                                        <select
                                            className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none transition focus:border-primary sm:min-w-[220px]"
                                            value={selectedProjectId || "__auto__"}
                                            onChange={(event) => void handleProjectSelect(event.target.value)}
                                            disabled={projectsLoading}
                                        >
                                            <option value="__auto__">{t(lt("自动推断项目", "Auto-detect project"))}</option>
                                            {projects
                                                .filter((project) => project.active !== false)
                                                .map((project) => (
                                                    <option key={project.id} value={project.id}>
                                                        {project.name} ({project.id})
                                                    </option>
                                                ))}
                                        </select>
                                        <Button
                                            type="button"
                                            variant="outline"
                                            size="sm"
                                            className="w-full sm:w-auto"
                                            onClick={() => void handleReresolveScope()}
                                            disabled={!activeConversationId || scopeLoading}
                                        >
                                            <RefreshCw className="mr-2 h-4 w-4 shrink-0" />
                                            {t(lt("重新解析", "Re-resolve"))}
                                        </Button>
                                    </div>
                                </div>

                                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                                    <span className="rounded-full bg-primary/10 px-2.5 py-1 text-primary">
                                        {t(lt("项目", "Project"))}: {selectedProject?.name || (defaultProjectId && !selectedProjectId ? defaultProjectId : t(lt("自动", "Auto")))}
                                    </span>
                                    <span className="rounded-full bg-accent px-2.5 py-1 text-accent-foreground">
                                        Scope: {scopeBinding?.resolvedScope || t(lt("待解析", "Pending"))}
                                    </span>
                                    <span className="rounded-full bg-secondary px-2.5 py-1 text-secondary-foreground">
                                        {t(lt("来源", "Source"))}: {scopeBinding?.scopeSource || t(lt("未绑定", "Unbound"))}
                                    </span>
                                    {scopeBinding?.workspaceId && (
                                        <span className="max-w-[200px] truncate rounded-full bg-muted px-2.5 py-1 text-muted-foreground sm:max-w-none">
                                            {t(lt("工作区", "Workspace"))}: {scopeBinding.workspaceId}
                                        </span>
                                    )}
                                    {projects.length === 0 && !projectsLoading && (
                                        <span className="text-muted-foreground">
                                            {t(lt("暂无项目注册表。", "No project registry is available yet."))}
                                        </span>
                                    )}
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                <div className="min-h-0 flex-1 overflow-hidden py-1 sm:py-1.5">
                    {messages.length === 0 && !activeConversationId ? (
                        <div className="flex h-full min-h-0 flex-col items-center justify-center overflow-y-auto p-8 animate-in fade-in zoom-in-95 duration-500">
                            <div className="w-full space-y-8">
                                <div className="space-y-6 text-center">
                                    <h1 className="bg-gradient-to-r from-foreground to-foreground/70 bg-clip-text text-4xl font-bold tracking-tight text-transparent sm:text-5xl">
                                        {greetingText}
                                    </h1>
                                </div>
                            </div>
                        </div>
                    ) : (
                        <ChatWindow
                            key={activeConversationId || "new"}
                            messages={messages}
                            processes={projectionProcesses}
                            contextReferences={projectionContextReferences}
                            isLoading={isLoading}
                            userAvatar={session?.user?.image}
                            shellClassName="w-full"
                            onDeleteMessage={(messageId) => {
                                setMessages((prev) => prev.filter((message) => message.id !== messageId));
                                const conversationId = activeConversationIdRef.current;
                                if (conversationId) {
                                    void loadConversationHistory(conversationId).catch((error) => {
                                        console.warn("[ChatClient] Failed to refresh conversation after deleting message:", error);
                                    });
                                }
                            }}
                        />
                    )}
                </div>

                <div
                    className="shrink-0 pt-1 transition-[padding-bottom] duration-200 sm:pb-[calc(0.75rem+env(safe-area-inset-bottom))]"
                    style={composerShellStyle}
                >
                    <div className="flex flex-col gap-2">
                        <div
                            className="empty:hidden flex max-h-[22vh] max-w-full flex-col items-end gap-2 overflow-y-auto overscroll-contain sm:max-h-[28vh]"
                            style={hudStackStyle}
                        >
                            <ProcessesHUD processes={projectionProcesses} />
                            <TodosHUD items={projectionTodos} isStale={projectionTodoStale} />
                        </div>
                        <div className="relative shrink-0">
                            <div className="pointer-events-none absolute inset-x-4 -top-7 hidden h-7 bg-gradient-to-t from-background via-background/82 to-transparent blur-sm sm:block" />
                            <InputArea
                                key={activeConversationId || "new-session"}
                                input={input}
                                handleInputChange={handleInputChange}
                                handleSubmit={handleSend}
                                onVoiceTranscript={(transcript) => {
                                    setInput((prev) => {
                                        const prefix = prev.trim();
                                        return prefix ? `${prefix}\n${transcript}` : transcript;
                                    });
                                }}
                                isLoading={isLoading}
                                onStop={stop}
                                selectedAgentName={t(lt("智能主管", "Supervisor"))}
                                shellClassName="w-full"
                            />
                        </div>
                    </div>
                </div>
            </div>

            <AskUserModal
                key={askUserApprovalId || askUserToolCallId || 'default-modal'}
                isOpen={askUserModalOpen}
                question={askUserQuestion}
                toolCallId={askUserToolCallId}
                onSubmit={(_, answer, approve) => handleAskUserSubmit(answer, approve)}
                onCancel={() => {
                    setAskUserModalOpen(false);
                }}
            />

            <ArtifactsPanel sessionId={activeConversationId} />

            <RuntimeTimelinePanel
                isOpen={isTimelineOpen}
                onClose={() => setIsTimelineOpen(false)}
                model={runtimeStageModel}
                selectedRuntimeId={selectedRuntimeId}
                processes={projectionProcesses}
                overallStatus={effectiveStatus}
                currentStepTitle={sessionProjection?.workflow?.currentStepTitle || sessionProjection?.summary?.currentStepTitle || null}
                pendingApproval={effectivePendingApproval}
                onSelectRuntime={setSelectedRuntimeId}
            />
        </div>
    );
}
