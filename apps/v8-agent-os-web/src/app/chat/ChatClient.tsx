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
import { Bot, FolderTree } from "lucide-react";
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
    type AdminProcessRef,
    deriveMemoryRuntimeInsightFromGovernance,
    deriveAuthoritativeSessionView,
    flushQueuedSessionRealtimeRuntimeEvents,
    isAskUserInteractionApproval,
    queueSessionRealtimeRuntimeEvent,
    syncSessionRealtimeMessageState,
    type AuthoritativeSessionView,
} from "@v8/session-realtime";

const AskUserModal = dynamic(
    () => import("@/components/chat/AskUserModal").then((mod) => mod.AskUserModal),
    { ssr: false }
);

const GovernanceApprovalModal = dynamic(
    () => import("@/components/chat/GovernanceApprovalModal").then((mod) => mod.GovernanceApprovalModal),
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

type WorkspaceBindingDraft =
    | { kind: "main" }
    | { kind: "project"; projectId: string }

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

type SessionProjectionView = AuthoritativeSessionView & {
    contextGovernance?: Record<string, unknown> | null;
    contextGovernanceHistory?: Record<string, unknown>[];
};

function isLegacyChatUnsupportedPayload(value: unknown) {
    const root = value && typeof value === "object" ? value as Record<string, unknown> : {};
    const snapshot = root.snapshot && typeof root.snapshot === "object" ? root.snapshot as Record<string, unknown> : {};
    return Boolean(root.legacyChatUnsupported || snapshot.legacyChatUnsupported);
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

function buildWebMessageComparisonKeys(message: Message) {
    const keys = new Set<string>();
    const id = String(message.id || "").trim();
    const runId = String(message.runId || "").trim();
    const role = String(message.role || "").trim();
    const timestamp = Number(message.timestamp || 0) || 0;
    if (id) keys.add(`id:${id}`);
    if (runId && role) keys.add(`run:${runId}:${role}`);
    if (role && timestamp > 0) keys.add(`role:${role}:ts:${timestamp}`);
    return Array.from(keys);
}

function buildWebMessageRichness(message: Message | null | undefined) {
    if (!message) {
        return 0;
    }
    return (
        String(message.content || "").trim().length
        + ((message.nodes || []).length * 120)
        + ((message.artifacts || []).length * 200)
        + ((message.images || []).length * 80)
    );
}

function hasStructuredAssistantPayload(message: Message | null | undefined) {
    return Boolean(
        message
        && message.role === "assistant"
        && (
            (Array.isArray(message.nodes) && message.nodes.length > 0)
            || (Array.isArray(message.artifacts) && message.artifacts.length > 0)
            || (Array.isArray(message.images) && message.images.length > 0)
        ),
    );
}

function hasRenderableWebMessagePayload(message: Message | null | undefined) {
    return Boolean(
        message
        && (
            String(message.content || "").trim()
            || (Array.isArray(message.nodes) && message.nodes.length > 0)
            || (Array.isArray(message.artifacts) && message.artifacts.length > 0)
            || (Array.isArray(message.images) && message.images.length > 0)
        ),
    );
}

function mergeWebMessagePayload(base: Message, incoming: Message): Message {
    const incomingTranscriptVersion = Number((incoming.metadata || {}).transcriptVersion || 0);
    const incomingCanonical = incomingTranscriptVersion > 0 || (incoming.nodes?.length || 0) > 0;
    const merged: Message = {
        ...base,
        ...incoming,
        metadata: {
            ...(base.metadata || {}),
            ...(incoming.metadata || {}),
        },
    };
    if (!incomingCanonical && (base.nodes?.length || 0) > (incoming.nodes?.length || 0)) {
        merged.nodes = base.nodes;
    }
    if (!incomingCanonical && (base.artifacts?.length || 0) > (incoming.artifacts?.length || 0)) {
        merged.artifacts = base.artifacts;
    }
    if (!incomingCanonical && (base.images?.length || 0) > (incoming.images?.length || 0)) {
        merged.images = base.images;
    }
    if (!merged.agentName && base.agentName) merged.agentName = base.agentName;
    if (!merged.agentAvatar && base.agentAvatar) merged.agentAvatar = base.agentAvatar;
    if (!merged.agentRoleLabel && base.agentRoleLabel) merged.agentRoleLabel = base.agentRoleLabel;
    if (!merged.toolInvocations?.length && base.toolInvocations?.length) {
        merged.toolInvocations = base.toolInvocations;
    }
    return merged;
}

function mergeProjectedSnapshotMessages(current: Message[], projectedMessages: unknown[]) {
    const normalizedSnapshot = normalizeProjectedMessages(projectedMessages);
    if (current.length === 0) {
        return normalizeMessagesForState(normalizedSnapshot);
    }
    const currentByKey = new Map<string, Message>();
    current.forEach((message) => {
        buildWebMessageComparisonKeys(message).forEach((key) => {
            if (!currentByKey.has(key)) {
                currentByKey.set(key, message);
            }
        });
    });
    return normalizeMessagesForState(
        normalizedSnapshot.map((snapshotMessage) => {
            const matchingCurrent = buildWebMessageComparisonKeys(snapshotMessage)
                .map((key) => currentByKey.get(key))
                .find(Boolean);
        if (!matchingCurrent) {
            return snapshotMessage;
        }
        const snapshotTranscriptVersion = Number((snapshotMessage.metadata || {}).transcriptVersion || 0);
        const snapshotCanonical = snapshotTranscriptVersion > 0 || (snapshotMessage.nodes?.length || 0) > 0;
        if (snapshotCanonical) {
            return snapshotMessage;
        }
        const snapshotAuthoritativeAssistant = snapshotMessage.role === "assistant"
            && hasRenderableWebMessagePayload(snapshotMessage)
            && hasStructuredAssistantPayload(snapshotMessage);
        if (!snapshotAuthoritativeAssistant) {
            return mergeWebMessagePayload(matchingCurrent, snapshotMessage);
        }
        return {
            ...snapshotMessage,
            metadata: {
                ...(matchingCurrent.metadata || {}),
                ...(snapshotMessage.metadata || {}),
            },
            images: (snapshotMessage.images?.length || 0) > 0 ? snapshotMessage.images : matchingCurrent.images,
            artifacts: (snapshotMessage.artifacts?.length || 0) > 0 ? snapshotMessage.artifacts : matchingCurrent.artifacts,
            toolInvocations: (snapshotMessage.toolInvocations?.length || 0) > 0 ? snapshotMessage.toolInvocations : matchingCurrent.toolInvocations,
        };
    }),
  );
}

function dedupeProcesses(processes: AdminProcessRef[]) {
    return Array.from(
        new Map(
            processes
                .filter((process) => String(process.processId || process.commandId || "").trim())
                .map((process) => [String(process.processId || process.commandId || "").trim(), process]),
        ).values(),
    );
}

function filterConversationProcesses(
    processes: AdminProcessRef[],
    {
        activeConversationId,
        currentConversationRunId,
        messageIds,
    }: {
        activeConversationId: string | null;
        currentConversationRunId: string;
        messageIds: Set<string>;
    },
) {
    return processes.filter((process) => {
        if (!activeConversationId) {
            return true;
        }
        const processSessionId = String((process as AdminProcessRef & { sessionId?: string | null }).sessionId || "").trim();
        if (processSessionId) {
            return processSessionId === activeConversationId;
        }
        const processRunId = String(process.runId || "").trim();
        if (currentConversationRunId && processRunId) {
            return processRunId === currentConversationRunId;
        }
        const sourceMessageId = String(process.sourceMessageId || "").trim();
        if (sourceMessageId) {
            return messageIds.has(sourceMessageId);
        }
        return true;
    });
}



export default function ChatClient() {
    const t = useT();
    const { locale } = useLocale();
    const { status, data: session } = useSession();
    const searchParams = useSearchParams();
    const urlId = searchParams.get("id");
    const newConversationIntent = searchParams.get("new") === "1";
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
    const [governanceApprovalOpen, setGovernanceApprovalOpen] = useState(false);
    const [governanceApprovalBusy, setGovernanceApprovalBusy] = useState(false);
    const [dismissedGovernanceApprovalId, setDismissedGovernanceApprovalId] = useState("");
    const [projects, setProjects] = useState<ProjectDescriptor[]>([]);
    const [mainWorkspacePath, setMainWorkspacePath] = useState("");
    const [workspaceChooserVisible, setWorkspaceChooserVisible] = useState(false);
    const [workspaceChooserBusy, setWorkspaceChooserBusy] = useState(false);
    const [newProjectName, setNewProjectName] = useState("");
    const [scopeBinding, setScopeBinding] = useState<ScopeBindingView | null>(null);
    const [scopeLoading, setScopeLoading] = useState(false);
    const [projectsLoading, setProjectsLoading] = useState(false);
    const [runEntries, setRunEntries] = useState<RunRecordView[]>([]);
    const [runActionLoading, setRunActionLoading] = useState(false);
    const [sessionProjection, setSessionProjection] = useState<SessionProjectionView | null>(null);
    const [legacyChatUnsupported, setLegacyChatUnsupported] = useState(false);
    const [sessionProcessSurface, setSessionProcessSurface] = useState<AdminProcessRef[]>([]);
    const lastSessionProcessSurfaceAtRef = useRef(0);
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
                applyAskUserPendingApproval({
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
    const boundProject = useMemo(
        () => projects.find((project) => project.id === scopeBinding?.projectId) || null,
        [projects, scopeBinding?.projectId],
    );
    const currentRun = sessionProjection?.currentRun || runEntries[0] || null;
    const askUserPendingProjection = useMemo(
        () => (sessionProjection?.approvals || []).find((item) => isAskUserInteractionApproval(item)) || null,
        [sessionProjection?.approvals],
    );
    const governanceApprovals = useMemo(
        () => (sessionProjection?.approvals || []).filter((item) => !isAskUserInteractionApproval(item)),
        [sessionProjection?.approvals],
    );
    const governancePendingApproval = governanceApprovals[0] || null;
    const governancePendingApprovalId = String(governancePendingApproval?.id || "").trim();
    const hasAskUserPending = Boolean(askUserApprovalId);
    const projectionRunId = (sessionProjection?.controls?.runId || sessionProjection?.currentRun?.id || sessionProjection?.workflow?.rootRunId) ?? undefined;
    const effectiveRunId = currentRun?.id || askUserRunId || projectionRunId;
    const effectiveStatus = hasAskUserPending
        ? "waiting_input"
        : governancePendingApprovalId || currentRun?.status === "waiting_approval"
            ? "waiting_approval"
        : sessionProjection?.runtimeStatus
            || currentRun?.status
            || normalizeWorkflowStatusForRunBar(sessionProjection?.controls?.workflowStatus)
            || normalizeWorkflowStatusForRunBar(sessionProjection?.workflow?.status);
    const effectivePendingApproval = Boolean(
        governancePendingApprovalId
        || currentRun?.status === "waiting_approval",
    );
    const projectionTodos = sessionProjection?.todos?.items || [];
    const projectionTodoStale = Boolean(sessionProjection?.todos?.isStale);
    const projectionProcesses = useMemo(
        () => {
            const messageIds = new Set(
                messages
                    .map((message) => String(message.id || "").trim())
                    .filter(Boolean),
            );
            const currentConversationRunId = String(currentRun?.id || projectionRunId || "").trim();
            const projectionScopedProcesses = filterConversationProcesses(
                dedupeProcesses(sessionProjection?.processes || []),
                {
                    activeConversationId,
                    currentConversationRunId,
                    messageIds,
                },
            );
            const sessionScopedProcesses = dedupeProcesses(
                (sessionProcessSurface || []).filter((process) => {
                    const processSessionId = String((process as AdminProcessRef & { sessionId?: string | null }).sessionId || "").trim();
                    return !activeConversationId || !processSessionId || processSessionId === activeConversationId;
                }),
            );
            return dedupeProcesses([
                ...projectionScopedProcesses,
                ...sessionScopedProcesses,
            ]);
        },
        [activeConversationId, currentRun?.id, messages, projectionRunId, sessionProcessSurface, sessionProjection?.processes],
    );
    const hudProcesses = useMemo(
        () => dedupeProcesses([
            ...projectionProcesses,
            ...dedupeProcesses(sessionProcessSurface || []),
        ]),
        [projectionProcesses, sessionProcessSurface],
    );
    const projectionContextReferences = sessionProjection?.contextReferences || [];
    const projectionContextGovernance = sessionProjection?.contextGovernance || null;
    const projectionContextGovernanceHistory = sessionProjection?.contextGovernanceHistory || [];
    const projectionRuntimeTimeline = useMemo(
        () => normalizeRuntimeTimeline(sessionProjection?.runtimeTimeline || []),
        [sessionProjection?.runtimeTimeline],
    );
    const projectionMemoryInsight = useMemo(
        () => deriveMemoryRuntimeInsightFromGovernance(
            projectionContextGovernance,
            projectionContextGovernanceHistory,
        ),
        [projectionContextGovernance, projectionContextGovernanceHistory],
    );
    const projectionTodosAllCompleted = projectionTodos.length > 0
        && projectionTodos.every((item) => {
            const status = String(item.status || "").trim().toLowerCase();
            return status === "done" || status === "skipped";
        });
    const projectionHasActiveProcess = hudProcesses.some((process) => {
        const status = String(process.status || "").trim().toLowerCase();
        return status !== "stopped"
            && status !== "terminated"
            && status !== "completed"
            && status !== "failed";
    });
    const todoHudShouldAutoHide = projectionTodosAllCompleted
        && !effectivePendingApproval
        && !projectionHasActiveProcess
        && !["running", "waiting_input", "waiting_approval", "queued", "pending", "starting", "streaming"].includes(String(effectiveStatus || "").trim().toLowerCase());
    const runtimeStageModel = useMemo(() => buildRuntimeStageModel(messages, {
        ownerRuntime: sessionProjection?.workflow?.ownerRuntime || sessionProjection?.summary?.ownerRuntime || null,
        status: effectiveStatus || null,
        pendingApproval: effectivePendingApproval,
        recoverable: Boolean(sessionProjection?.recoverable?.recoverable),
        currentStepTitle: sessionProjection?.workflow?.currentStepTitle || sessionProjection?.summary?.currentStepTitle || null,
        runtimeTimeline: projectionRuntimeTimeline,
        memoryInsight: projectionMemoryInsight,
    }), [effectivePendingApproval, effectiveStatus, messages, projectionMemoryInsight, projectionRuntimeTimeline, sessionProjection?.recoverable?.recoverable, sessionProjection?.summary?.currentStepTitle, sessionProjection?.summary?.ownerRuntime, sessionProjection?.workflow?.currentStepTitle, sessionProjection?.workflow?.ownerRuntime]);
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
                    Number(governanceApprovals.length || 0),
                    Number(sessionProjection?.controls?.pendingApprovalCount || 0),
                )
                : 0,
            hasPendingApproval: effectivePendingApproval,
            recoverable: Boolean(sessionProjection?.recoverable?.recoverable),
            controls: sessionProjection?.controls || undefined,
        });
    }, [
        activeConversationId,
        effectivePendingApproval,
        effectiveStatus,
        governanceApprovals.length,
        historyPreview,
        patchConversationSummary,
        sessionProjection,
    ]);

    const clearApprovalState = useCallback((options?: { closeModal?: boolean }) => {
        setAskUserApprovalId("");
        setAskUserQuestion("");
        setAskUserToolCallId("");
        setAskUserRunId("");
        if (options?.closeModal !== false) {
            setAskUserModalOpen(false);
        }
    }, []);

    const applyAskUserPendingApproval = useCallback((approval: {
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
        const interactionKind = approval.interactionKind || request.interactionKind || approval.approval_kind || request.approvalKind || "";
        if (String(interactionKind).trim() !== "ask_user") {
            clearApprovalState({ closeModal: false });
            return;
        }
        const approvalId = approval.id || approval.approval_id || "";
        const question = approval.question || approval.prompt || request.question || request.prompt || "";
        if (!approvalId || !question) {
            clearApprovalState();
            return;
        }

        setAskUserApprovalId(approvalId);
        setAskUserToolCallId(approval.toolCallId || request.toolCallId || "");
        setAskUserQuestion(question);
        setAskUserRunId(approval.run_id || approval.runId || "");
        const shouldOpenModal =
            typeof options?.openModal === "boolean"
                ? options.openModal
                : true;
        if (shouldOpenModal) {
            setAskUserModalOpen(true);
        }
    }, [clearApprovalState]);

    useEffect(() => {
        const nextAskUserApprovalId = String(askUserPendingProjection?.id || "").trim();
        if (!nextAskUserApprovalId) {
            if (askUserApprovalId) {
                clearApprovalState({ closeModal: true });
            }
            return;
        }
        if (nextAskUserApprovalId !== askUserApprovalId) {
            applyAskUserPendingApproval(askUserPendingProjection, { openModal: false });
        }
    }, [applyAskUserPendingApproval, askUserApprovalId, askUserPendingProjection, clearApprovalState]);

    useEffect(() => {
        if (!governancePendingApprovalId) {
            setGovernanceApprovalOpen(false);
            if (dismissedGovernanceApprovalId) {
                setDismissedGovernanceApprovalId("");
            }
            return;
        }
        if (dismissedGovernanceApprovalId === governancePendingApprovalId) {
            return;
        }
        setGovernanceApprovalOpen(true);
    }, [dismissedGovernanceApprovalId, governancePendingApprovalId]);

    const openGovernanceApproval = useCallback(() => {
        if (!governancePendingApprovalId) {
            setSelectedRuntimeId("automation");
            setIsTimelineOpen(true);
            return;
        }
        setDismissedGovernanceApprovalId("");
        setGovernanceApprovalOpen(true);
    }, [governancePendingApprovalId]);

    const handleGovernanceApprovalDismiss = useCallback(() => {
        if (governancePendingApprovalId) {
            setDismissedGovernanceApprovalId(governancePendingApprovalId);
        }
        setGovernanceApprovalOpen(false);
    }, [governancePendingApprovalId]);

    const handleGovernanceApprovalViewDetails = useCallback(() => {
        if (governancePendingApprovalId) {
            setDismissedGovernanceApprovalId(governancePendingApprovalId);
        }
        setGovernanceApprovalOpen(false);
        setSelectedRuntimeId("automation");
        setIsTimelineOpen(true);
    }, [governancePendingApprovalId]);

    const handleGovernanceApprovalResolve = useCallback(async (answer: string, approve: boolean) => {
        if (!governancePendingApprovalId) {
            return;
        }
        setGovernanceApprovalBusy(true);
        try {
            await resolveApproval(governancePendingApprovalId, answer, approve);
            setGovernanceApprovalOpen(false);
            setDismissedGovernanceApprovalId("");
        } finally {
            setGovernanceApprovalBusy(false);
        }
    }, [governancePendingApprovalId, resolveApproval]);

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
        const normalized = mergeProjectedSnapshotMessages(messagesRef.current, projectedMessages);
        realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
            normalized,
            WEB_STREAM_LIFECYCLE_OPTIONS,
        );
        latestRealtimeSeqRef.current = latestSeq;
        messagesRef.current = normalizeMessagesForState(normalized);
        setMessages(normalizeMessagesForState(normalized));
        return normalized;
    }, [setMessages]);

    const applySessionProcessSurface = useCallback((incoming: AdminProcessRef[], options?: { forceClear?: boolean }) => {
        const normalizedIncoming = dedupeProcesses(incoming || []);
        setSessionProcessSurface((current) => {
            if (normalizedIncoming.length > 0) {
                lastSessionProcessSurfaceAtRef.current = Date.now();
                return normalizedIncoming;
            }
            if (options?.forceClear) {
                lastSessionProcessSurfaceAtRef.current = 0;
                return [];
            }
            if (current.length === 0) {
                return current;
            }
            return (Date.now() - lastSessionProcessSurfaceAtRef.current) <= 3000 ? current : [];
        });
    }, []);

    const loadConversationHistory = useCallback(async (conversationId: string) => {
        const detailRes = await fetch(`/api/client/conversations/${conversationId}`, { cache: "no-store" });
        if (!detailRes.ok) {
            if (detailRes.status === 404) {
                router.replace("/chat");
                return;
            }
            throw new Error(`Failed to load conversation detail: ${detailRes.status}`);
        }

        const data = await detailRes.json();
        const detailPayload = (data?.payload && typeof data.payload === "object") ? data.payload : data;
        const projectionPayload = (detailPayload?.projection && typeof detailPayload.projection === "object")
            ? detailPayload.projection
            : detailPayload;
        setLegacyChatUnsupported(isLegacyChatUnsupportedPayload(detailPayload) || isLegacyChatUnsupportedPayload(projectionPayload));
        const projection = deriveAuthoritativeSessionView(projectionPayload).view as SessionProjectionView | null;
        setSessionProjection(projection);
        if (projection?.approvals?.length) {
            const askUserApproval = projection.approvals.find((item) => isAskUserInteractionApproval(item)) || null;
            if (askUserApproval) {
                applyAskUserPendingApproval(askUserApproval, { openModal: false });
            }
        }

        const authoritativeMessages = Array.isArray((detailPayload as { timeline?: unknown[] } | null | undefined)?.timeline)
            ? (detailPayload as { timeline: unknown[] }).timeline
            : [];
        const hasTimelineNodes = authoritativeMessages.some((message) =>
            Boolean(message && typeof message === "object" && Array.isArray((message as { nodes?: unknown[] }).nodes)),
        );
        const normalized = hasTimelineNodes
            ? normalizeMessagesForState(authoritativeMessages as Message[])
            : normalizeProjectedMessages(authoritativeMessages);
        latestRealtimeSeqRef.current = Number(projectionPayload?.latestSeq || projectionPayload?.snapshot?.latest_seq || 0);
        realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
            normalized,
            WEB_STREAM_LIFECYCLE_OPTIONS,
        );
        messagesRef.current = normalizeMessagesForState(normalized);
        setMessages(normalizeMessagesForState(normalized));
        const detailProcesses = Array.isArray(detailPayload?.processes) ? detailPayload.processes : [];
        if (detailProcesses.length > 0) {
            applySessionProcessSurface(detailProcesses);
        }
    }, [applyAskUserPendingApproval, applySessionProcessSurface, router, setMessages]);

    const loadProjects = useCallback(async () => {
        setProjectsLoading(true);
        try {
            const res = await fetch("/api/projects", { cache: "no-store" });
            if (!res.ok) {
                return;
            }
            const data = await res.json();
            const nextProjects = Array.isArray(data?.projects) ? data.projects : [];
            const nextMainWorkspacePath = typeof data?.mainWorkspacePath === "string" ? data.mainWorkspacePath : "";
            setProjects(nextProjects);
            setMainWorkspacePath(nextMainWorkspacePath);
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
        } catch (error) {
            console.warn("[ChatClient] Failed to load scope binding:", error);
            setScopeBinding(null);
        } finally {
            setScopeLoading(false);
        }
    }, []);

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

    const loadSessionProcesses = useCallback(async (conversationId: string) => {
        try {
            const res = await fetch(`/api/client/sessions/${encodeURIComponent(conversationId)}/processes`, { cache: "no-store" });
            if (!res.ok) {
                applySessionProcessSurface([]);
                return;
            }
            const data = await res.json().catch(() => ({}));
            applySessionProcessSurface(Array.isArray(data?.processes) ? data.processes : []);
        } catch (error) {
            console.warn("[ChatClient] Failed to load session processes:", error);
            applySessionProcessSurface([]);
        }
    }, [applySessionProcessSurface]);

    useEffect(() => {
        if (!activeConversationId) {
            applySessionProcessSurface([], { forceClear: true });
            return;
        }

        applySessionProcessSurface([], { forceClear: true });
        void loadSessionProcesses(activeConversationId);
        const timer = window.setInterval(() => {
            void loadSessionProcesses(activeConversationId);
        }, 1800);

        return () => {
            window.clearInterval(timer);
        };
    }, [activeConversationId, applySessionProcessSurface, loadSessionProcesses]);

    const buildScopePayload = useCallback((conversationId?: string | null) => ({
        conversationId: conversationId || activeConversationIdRef.current || undefined,
        projectId: scopeBinding?.projectId || undefined,
        workspaceId: scopeBinding?.workspaceId || undefined,
        workspacePath: scopeBinding?.workspacePath || undefined,
        scopeHint: scopeBinding?.resolvedScope || undefined,
        scopeMode: "explicit",
    }), [scopeBinding?.projectId, scopeBinding?.resolvedScope, scopeBinding?.workspaceId, scopeBinding?.workspacePath]);

    const clearNewConversationIntent = useCallback(() => {
        if (typeof window === "undefined") {
            return;
        }
        window.history.replaceState(null, "", "/chat");
    }, []);

    const createBoundConversation = useCallback(async (draft: WorkspaceBindingDraft) => {
        if (workspaceChooserBusy) {
            return;
        }
        setWorkspaceChooserBusy(true);
        try {
            let creationPayload: CreateConversationPayload = {
                title: "New Chat",
                scopeMode: "explicit",
            };
            if (draft.kind === "main") {
                creationPayload = {
                    ...creationPayload,
                    workspacePath: mainWorkspacePath || undefined,
                    scopeHint: "global",
                };
            } else {
                const project = projects.find((item) => item.id === draft.projectId);
                if (!project?.id) {
                    throw new Error("Project not found");
                }
                creationPayload = {
                    ...creationPayload,
                    projectId: project.id,
                    workspaceId: project.workspaceId,
                    workspacePath: project.workspacePath,
                    scopeHint: project.defaultScope,
                };
            }
            const newConversation = await createConversation(creationPayload);
            if (!newConversation?.id) {
                throw new Error("Conversation creation failed");
            }
            activeConversationIdRef.current = newConversation.id;
            setActiveConversationId(newConversation.id);
            setWorkspaceChooserVisible(false);
            setNewProjectName("");
            window.history.replaceState(null, "", `/chat?id=${newConversation.id}`);
            await loadSessionScope(newConversation.id);
            await refreshConversations();
        } finally {
            setWorkspaceChooserBusy(false);
        }
    }, [createConversation, loadSessionScope, mainWorkspacePath, projects, refreshConversations, workspaceChooserBusy]);

    const handleCreateProjectConversation = useCallback(async () => {
        const trimmedName = newProjectName.trim();
        if (!trimmedName || workspaceChooserBusy) {
            return;
        }
        setWorkspaceChooserBusy(true);
        try {
            const res = await fetch("/api/projects", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: trimmedName }),
            });
            if (!res.ok) {
                throw new Error(`Project creation failed: ${res.status}`);
            }
            const createdProject = await res.json();
            await loadProjects();
            const creationPayload: CreateConversationPayload = {
                title: "New Chat",
                projectId: createdProject?.id,
                workspaceId: createdProject?.workspaceId,
                workspacePath: createdProject?.workspacePath,
                scopeHint: createdProject?.defaultScope,
                scopeMode: "explicit",
            };
            const newConversation = await createConversation(creationPayload);
            if (!newConversation?.id) {
                throw new Error("Conversation creation failed");
            }
            activeConversationIdRef.current = newConversation.id;
            setActiveConversationId(newConversation.id);
            setWorkspaceChooserVisible(false);
            setNewProjectName("");
            window.history.replaceState(null, "", `/chat?id=${newConversation.id}`);
            await loadSessionScope(newConversation.id);
            await refreshConversations();
        } finally {
            setWorkspaceChooserBusy(false);
        }
    }, [createConversation, loadProjects, loadSessionScope, newProjectName, refreshConversations, workspaceChooserBusy]);

    useEffect(() => {
        if (status === "authenticated") {
            void loadProjects();
            return;
        }
        if (status === "unauthenticated") {
            setProjects([]);
            setMainWorkspacePath("");
            setWorkspaceChooserVisible(false);
            setProjectsLoading(false);
        }
    }, [loadProjects, status]);

    useEffect(() => {
        if (activeConversationId) {
            setWorkspaceChooserVisible(false);
            return;
        }
        if (newConversationIntent) {
            setWorkspaceChooserVisible(true);
        }
    }, [activeConversationId, newConversationIntent]);

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
            applyAskUserPendingApproval({
                id: String(eventData.approvalId || ""),
                run_id: String((normalizedEvent as Record<string, unknown>).run_id || ""),
                request: {
                    question: String(eventData.question || ""),
                    toolCallId: String(eventData.toolCallId || ""),
                },
            });
        }

        if (
            normalizedEvent.topic === "approval.approved"
            || normalizedEvent.topic === "approval.rejected"
            || normalizedEvent.topic === "ask_user.resolved"
        ) {
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
            "ask_user.requested",
            "ask_user.resolved",
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
    }, [applyAskUserPendingApproval, clearApprovalState, isLocalStreamActive, loadRuns, setMessages]);

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
        if (!activeConversationIdRef.current) {
            setWorkspaceChooserVisible(true);
            if (!newConversationIntent) {
                clearNewConversationIntent();
            }
            return;
        }

        const currentInput = input;
        setInput(""); // Clear immediately (Optimistic)

        // [REMOVED] Optimistic UI: The useLangGraphStream hook now handles both User and AI placeholders internally.
        // This prevents the "Flicker" caused by state conflicts (Client vs Hook)

        try {
            await sendMessage(currentInput, {
                agentId: undefined, // selectedAgent?.id,
                userId: session?.user?.id,
                ...buildScopePayload(activeConversationIdRef.current),
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
            setLegacyChatUnsupported(false);
            clearApprovalState();
            setRunEntries([]);
            messagesRef.current = [];
            setMessages([]);
        }
    }, [activeConversationId, clearApprovalState, isLoading, loadConversationHistory, loadRuns, loadSessionScope, stop, setMessages]);

    useEffect(() => {
        if (!activeConversationId) {
            return;
        }

        const eventSource = new EventSource(`/api/realtime/sessions/${activeConversationId}/stream`);

        const handleSnapshot = (event: MessageEvent) => {
            try {
                const data = JSON.parse(event.data);
                const snapshotPayload = (data?.payload && typeof data.payload === "object") ? data.payload : data;
                if (isLegacyChatUnsupportedPayload(snapshotPayload)) {
                    setLegacyChatUnsupported(true);
                }
                const localStreamActive = isLocalStreamActive(activeConversationId);
                const nextView = deriveAuthoritativeSessionView(snapshotPayload).view as SessionProjectionView | null;
                setSessionProjection((current) => {
                    if (!nextView) {
                        return current;
                    }
                    if (!current) {
                        return nextView;
                    }
                    return {
                        ...nextView,
                        contextGovernance: nextView.contextGovernance || current.contextGovernance,
                        contextGovernanceHistory:
                            Array.isArray(nextView.contextGovernanceHistory) && nextView.contextGovernanceHistory.length > 0
                                ? nextView.contextGovernanceHistory
                                : current.contextGovernanceHistory,
                    };
                });
                if (Array.isArray(nextView?.processes) && nextView.processes.length > 0) {
                    applySessionProcessSurface(nextView.processes);
                }
                if (!localStreamActive && Array.isArray(snapshotPayload?.snapshot?.messages)) {
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
    }, [activeConversationId, applyProjectedSnapshot, applyRemoteRuntimeEvent, applySessionProcessSurface, isLocalStreamActive, loadConversationHistory]);

    useEffect(() => {
        if (!activeConversationId) {
            return;
        }
        const hasRuntimeNeed = Boolean(
            currentRun?.id
            || sessionProjection?.runtimeStatus === "running"
            || sessionProjection?.controls?.canInterrupt,
        );
        if (hasRuntimeNeed && sessionProcessSurface.length > 0 && hudProcesses.length === 0) {
            console.warn("[ChatClient] process surface dropped after hydration/filtering", {
                activeConversationId,
                currentRunId: currentRun?.id || projectionRunId || null,
                sessionProcessSurface: sessionProcessSurface.length,
                projectionProcessSurface: (sessionProjection?.processes || []).length,
            });
        }
    }, [activeConversationId, currentRun?.id, hudProcesses.length, projectionRunId, sessionProcessSurface.length, sessionProjection?.controls?.canInterrupt, sessionProjection?.processes, sessionProjection?.runtimeStatus]);

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
                                (activeConversationId ? isContextExpanded : workspaceChooserVisible)
                                    ? "border-primary/35 bg-primary/8 text-primary"
                                    : "border-border/60"
                            }`}
                            onClick={() => {
                                if (activeConversationId) {
                                    setIsContextExpanded((current) => !current);
                                    return;
                                }
                                setWorkspaceChooserVisible((current) => !current);
                                if (!workspaceChooserVisible) {
                                    clearNewConversationIntent();
                                }
                            }}
                            aria-expanded={activeConversationId ? isContextExpanded : workspaceChooserVisible}
                            aria-label={activeConversationId ? t(lt("工作区信息", "Workspace info")) : t(lt("新对话工作区选择", "New conversation workspace chooser"))}
                            title={
                                activeConversationId
                                    ? (isContextExpanded ? t(lt("收起工作区信息", "Collapse workspace info")) : t(lt("展开工作区信息", "Expand workspace info")))
                                    : (workspaceChooserVisible ? t(lt("收起工作区选择", "Collapse workspace chooser")) : t(lt("开始新对话", "Start a new conversation")))
                            }
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
                                openGovernanceApproval();
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

                    {activeConversationId && isContextExpanded && (
                        <div className="rounded-[24px] border border-border/50 bg-background/82 px-3 py-3 shadow-sm backdrop-blur-xl sm:px-4">
                            <div className="space-y-3">
                                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                                    <div className="min-w-0 flex-1">
                                        <div className="flex items-center gap-2 text-sm font-medium">
                                            <FolderTree className="h-4 w-4 shrink-0 text-primary" />
                                            {t(lt("当前工作区", "Current workspace"))}
                                        </div>
                                        <p className="mt-1 text-xs text-muted-foreground">
                                            {t(lt("当前会话的工作区绑定在创建后已冻结；如需切换，请新建对话。", "This conversation binding is frozen after creation. Start a new conversation to switch workspaces."))}
                                        </p>
                                    </div>
                                    <div className="text-xs text-muted-foreground">
                                        {scopeLoading ? t(lt("正在同步绑定信息…", "Syncing binding...")) : t(lt("只读展示", "Read only"))}
                                    </div>
                                </div>

                                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                                    <span className="rounded-full bg-primary/10 px-2.5 py-1 text-primary">
                                        {t(lt("会话", "Conversation"))}: {
                                            (sessionProjection?.summary
                                                && typeof (sessionProjection.summary as Record<string, unknown>).title === "string"
                                                ? String((sessionProjection.summary as Record<string, unknown>).title)
                                                : "")
                                            || t(lt("当前对话", "Current conversation"))
                                        }
                                    </span>
                                    <span className="rounded-full bg-accent px-2.5 py-1 text-accent-foreground">
                                        {t(lt("工作区类型", "Workspace kind"))}: {boundProject?.name || t(lt("主工作区", "Main workspace"))}
                                    </span>
                                    <span className="rounded-full bg-secondary px-2.5 py-1 text-secondary-foreground">
                                        Scope: {scopeBinding?.resolvedScope || "global"}
                                    </span>
                                    <span className="max-w-[340px] truncate rounded-full bg-muted px-2.5 py-1 text-muted-foreground sm:max-w-none">
                                        {t(lt("路径", "Path"))}: {scopeBinding?.workspacePath || mainWorkspacePath || t(lt("未绑定", "Unbound"))}
                                    </span>
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                <div className="min-h-0 flex-1 overflow-hidden py-1 sm:py-1.5">
                    {messages.length === 0 && !activeConversationId ? (
                        <div className="flex h-full min-h-0 flex-col items-center justify-center overflow-y-auto p-8 animate-in fade-in zoom-in-95 duration-500">
                            <div className="w-full max-w-3xl space-y-8">
                                <div className="space-y-6 text-center">
                                    <h1 className="bg-gradient-to-r from-foreground to-foreground/70 bg-clip-text text-4xl font-bold tracking-tight text-transparent sm:text-5xl">
                                        {greetingText}
                                    </h1>
                                    <p className="text-sm text-muted-foreground">
                                        {t(lt("新对话会先绑定工作区，再创建会话。", "A new conversation binds a workspace before the session is created."))}
                                    </p>
                                </div>
                                {workspaceChooserVisible ? (
                                    <div className="rounded-[28px] border border-border/60 bg-background/88 p-5 shadow-lg backdrop-blur">
                                        <div className="flex items-center justify-between gap-3">
                                            <div>
                                                <h2 className="text-base font-semibold">{t(lt("选择工作区", "Choose a workspace"))}</h2>
                                                <p className="mt-1 text-xs text-muted-foreground">
                                                    {t(lt("历史会话始终优先；这里只用于明确开始新对话。", "History stays higher priority; this chooser is only for explicitly starting a new conversation."))}
                                                </p>
                                            </div>
                                            <Button
                                                type="button"
                                                variant="ghost"
                                                size="sm"
                                                onClick={() => {
                                                    setWorkspaceChooserVisible(false);
                                                    clearNewConversationIntent();
                                                }}
                                            >
                                                {t(lt("稍后", "Later"))}
                                            </Button>
                                        </div>
                                        <div className="mt-5 grid gap-3">
                                            <button
                                                type="button"
                                                className="rounded-2xl border border-border/60 bg-muted/40 px-4 py-4 text-left transition hover:border-primary/35 hover:bg-primary/5"
                                                onClick={() => void createBoundConversation({ kind: "main" })}
                                                disabled={workspaceChooserBusy || !mainWorkspacePath}
                                            >
                                                <div className="text-sm font-semibold">{t(lt("主工作区", "Main workspace"))}</div>
                                                <div className="mt-1 text-xs text-muted-foreground">{mainWorkspacePath || t(lt("正在读取主工作区路径…", "Loading main workspace path..."))}</div>
                                            </button>
                                            <div className="rounded-2xl border border-border/60 bg-background/70 p-4">
                                                <div className="text-sm font-semibold">{t(lt("现有项目级工作区", "Existing project workspaces"))}</div>
                                                <div className="mt-3 max-h-52 space-y-2 overflow-y-auto pr-1">
                                                    {projects.filter((project) => project.active !== false).length === 0 ? (
                                                        <div className="text-xs text-muted-foreground">
                                                            {t(lt("当前没有可用的项目级工作区。", "No project workspaces are available yet."))}
                                                        </div>
                                                    ) : (
                                                        projects
                                                            .filter((project) => project.active !== false)
                                                            .map((project) => (
                                                                <button
                                                                    key={project.id}
                                                                    type="button"
                                                                    className="w-full rounded-xl border border-border/60 bg-muted/30 px-3 py-3 text-left transition hover:border-primary/35 hover:bg-primary/5"
                                                                    onClick={() => void createBoundConversation({ kind: "project", projectId: project.id })}
                                                                    disabled={workspaceChooserBusy}
                                                                >
                                                                    <div className="text-sm font-medium">{project.name}</div>
                                                                    <div className="mt-1 text-xs text-muted-foreground">{project.workspacePath || project.id}</div>
                                                                </button>
                                                            ))
                                                    )}
                                                </div>
                                            </div>
                                            <div className="rounded-2xl border border-border/60 bg-background/70 p-4">
                                                <div className="text-sm font-semibold">{t(lt("新建项目级工作区", "Create a project workspace"))}</div>
                                                <div className="mt-1 text-xs text-muted-foreground">
                                                    {t(lt("这里只填项目名称；系统会在 ~/.v8-agent-os/workspace/projects 下自动创建路径。", "Only a project name is needed here; the system creates the path under ~/.v8-agent-os/workspace/projects automatically."))}
                                                </div>
                                                <div className="mt-3 flex flex-col gap-3 sm:flex-row">
                                                    <input
                                                        value={newProjectName}
                                                        onChange={(event) => setNewProjectName(event.target.value)}
                                                        placeholder={t(lt("输入项目名称", "Enter a project name"))}
                                                        className="h-11 flex-1 rounded-xl border border-border/60 bg-background px-3 text-sm outline-none transition focus:border-primary"
                                                    />
                                                    <Button
                                                        type="button"
                                                        className="h-11 rounded-xl"
                                                        onClick={() => void handleCreateProjectConversation()}
                                                        disabled={workspaceChooserBusy || newProjectName.trim().length === 0}
                                                    >
                                                        {t(lt("创建并开始", "Create and start"))}
                                                    </Button>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                ) : (
                                    <div className="flex justify-center">
                                        <Button
                                            type="button"
                                            size="lg"
                                            className="rounded-2xl px-6"
                                            onClick={() => {
                                                setWorkspaceChooserVisible(true);
                                                clearNewConversationIntent();
                                            }}
                                        >
                                            {t(lt("开始新对话", "Start a new conversation"))}
                                        </Button>
                                    </div>
                                )}
                            </div>
                        </div>
                    ) : activeConversationId && legacyChatUnsupported && messages.length === 0 ? (
                        <div className="flex h-full min-h-0 flex-col items-center justify-center overflow-y-auto p-8 animate-in fade-in zoom-in-95 duration-300">
                            <div className="max-w-xl rounded-[28px] border border-amber-300/50 bg-amber-50/80 p-6 text-center shadow-sm backdrop-blur dark:border-amber-500/30 dark:bg-amber-500/10">
                                <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-200">
                                    <Bot className="h-6 w-6" />
                                </div>
                                <h2 className="mt-4 text-base font-semibold text-foreground">
                                    {t(lt("旧会话未接入 Canonical Transcript", "Legacy conversation is not on Canonical Transcript"))}
                                </h2>
                                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                                    {t(lt(
                                        "这条历史记录没有稳定 transcript 节点。为避免继续混用 runtime_events / messages / snapshots 造成漂移，当前版本已停止回放旧混源聊天内容。",
                                        "This history record has no stable transcript nodes. To avoid mixing runtime_events, messages and snapshots again, this version no longer replays legacy mixed-source chat content.",
                                    ))}
                                </p>
                            </div>
                        </div>
                    ) : (
                        <ChatWindow
                            key={activeConversationId || "new"}
                            messages={messages}
                            processes={hudProcesses}
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
                            <ProcessesHUD processes={hudProcesses} />
                            <TodosHUD
                                items={projectionTodos}
                                isStale={projectionTodoStale}
                                shouldAutoHide={todoHudShouldAutoHide}
                            />
                        </div>
                        <div className="relative shrink-0">
                            <div className="pointer-events-none absolute inset-x-4 -top-7 hidden h-7 bg-gradient-to-t from-background via-background/82 to-transparent blur-sm sm:block" />
                            {activeConversationId ? (
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
                            ) : (
                                <div className="rounded-2xl border border-dashed border-border/60 bg-background/70 px-4 py-3 text-center text-sm text-muted-foreground">
                                    {t(lt("先选择主工作区或项目级工作区，再开始新对话。", "Choose the main workspace or a project workspace before starting a new conversation."))}
                                </div>
                            )}
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

            <GovernanceApprovalModal
                isOpen={governanceApprovalOpen}
                approval={governancePendingApproval}
                busy={governanceApprovalBusy}
                onApprove={(answer) => handleGovernanceApprovalResolve(answer, true)}
                onReject={(answer) => handleGovernanceApprovalResolve(answer, false)}
                onViewDetails={handleGovernanceApprovalViewDetails}
                onCancel={handleGovernanceApprovalDismiss}
            />

            <ArtifactsPanel sessionId={activeConversationId} />

            <RuntimeTimelinePanel
                isOpen={isTimelineOpen}
                onClose={() => setIsTimelineOpen(false)}
                model={runtimeStageModel}
                selectedRuntimeId={selectedRuntimeId}
                processes={hudProcesses}
                overallStatus={effectiveStatus}
                currentStepTitle={sessionProjection?.workflow?.currentStepTitle || sessionProjection?.summary?.currentStepTitle || null}
                pendingApproval={effectivePendingApproval}
                contextGovernance={projectionContextGovernance}
                contextGovernanceHistory={projectionContextGovernanceHistory}
                onSelectRuntime={setSelectedRuntimeId}
            />
        </div>
    );
}
