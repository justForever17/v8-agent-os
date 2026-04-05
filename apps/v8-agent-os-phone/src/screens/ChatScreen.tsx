import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    ActivityIndicator,
    Alert,
    KeyboardAvoidingView,
    Modal,
    Platform,
    Pressable,
    StyleSheet,
    Text,
    View,
    useWindowDimensions,
} from "react-native";
import { Redirect, router, type Href } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import * as DocumentPicker from "expo-document-picker";
import { WebView, type WebViewMessageEvent } from "react-native-webview";
import {
    RecordingPresets,
    requestRecordingPermissionsAsync,
    setAudioModeAsync,
    useAudioPlayer,
    useAudioPlayerStatus,
    useAudioRecorder,
    useAudioRecorderState,
} from "expo-audio";

import { ChatWindow } from "@/src/components/chat/ChatWindow";
import { Composer } from "@/src/components/chat/Composer";
import { RunControlBar } from "@/src/components/chat/RunControlBar";
import { RuntimeDock } from "@/src/components/chat/RuntimeDock";
import { RuntimeTimelinePanel } from "@/src/components/chat/RuntimeTimelinePanel";
import { GlassCard } from "@/src/components/common/GlassCard";
import { LoadingScreen } from "@/src/components/common/LoadingScreen";
import { HistoryDrawer } from "@/src/components/layout/HistoryDrawer";
import { PhoneTopbar, type PhoneTopbarAction } from "@/src/components/layout/PhoneTopbar";
import { resolveAdminAssetUrl } from "@/src/lib/admin-client";
import { buildPhoneChatProjection } from "@/src/lib/chat-projection";
import { normalizeMessagesForState, upsertApproval } from "@/src/lib/chat-state";
import { buildApprovalFromEvent, collectArtifactsFromMessages, normalizePhoneRealtimeEvent } from "@/src/lib/chat-realtime";
import {
    buildPhoneRuntimeTimelineEntryFromEvent,
    getPhoneRuntimeDescriptor,
    mergePhoneRuntimeTimeline,
    normalizePhoneRuntimeTimeline,
    normalizePhoneRuntimeId,
    type PhoneRuntimeId,
    type PhoneRuntimeTimelineEntry,
} from "@/src/lib/runtime-stage";
import { buildDesktopLiveBridgeInjection, buildDesktopLivePreviewHtml } from "@/src/lib/desktop-live-preview";
import { mergeSessionHistoryOverlay, sortSessionHistory } from "@/src/lib/session-history";
import { saveResponseToCache } from "@/src/lib/file-transfer";
import {
    approvePendingItem,
    createDesktopLiveOffer,
    prepareDesktopLive,
    createDesktopLiveSession,
    createConversation,
    deleteConversation,
    deleteMessage,
    dispatchRunCommand,
    getDesktopLiveStatus,
    getConversationDetail,
    listCommandPresets,
    listConversations,
    listMusicTracks,
    listSkills,
    requestTextToSpeech,
    releaseDesktopLiveSession,
    sendChatMessageStream,
    sendDesktopLiveCandidate,
    speechToText,
    streamRealtimeSession,
    uploadAttachment,
} from "@/src/lib/phone-api";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type {
    ArtifactDetail,
    ChatArtifact,
    ChatMessage,
    ChatStreamEvent,
    CommandPresetSummary,
    ConversationDetail,
    ConversationSummary,
    MusicTrack,
    PendingApproval,
    DesktopLiveStatus,
    RealtimeSessionSnapshot,
    SessionTodoItem,
    SkillReferenceSummary,
    UploadedWorkspaceFile,
    DesktopLiveSessionPayload,
} from "@/src/types/admin";

type RuntimeSummary = {
    status: string;
    latestSeq: number;
    runId?: string;
    label?: string;
};

function buildRuntimeTimelineEntry(
    runtimeId: PhoneRuntimeId,
    topic: string,
    summary: string,
    options?: {
        id?: string;
        timestamp?: number | string;
        actorLabel?: string;
        status?: string;
    },
): PhoneRuntimeTimelineEntry {
    const timestamp = typeof options?.timestamp === "number"
        ? options.timestamp
        : typeof options?.timestamp === "string"
            ? Date.parse(options.timestamp) || Date.now()
            : Date.now();
    return {
        id: String(options?.id || `${runtimeId}:${topic}:${timestamp}`).trim(),
        runtimeId,
        topic,
        summary,
        timestamp,
        actorLabel: options?.actorLabel,
    };
}

function buildUserMessage(
    text: string,
    options: {
        command: CommandPresetSummary | null;
        skills: SkillReferenceSummary[];
        taskPlanningMode: boolean;
        files: UploadedWorkspaceFile[];
    },
): ChatMessage {
    const now = Date.now();
    const metadata: ChatMessage["metadata"] = {};
    if (options.command) {
        metadata.commandPreset = { name: options.command.name };
    }
    if (options.skills.length > 0) {
        metadata.skillReferences = options.skills.map((skill) => ({ ...skill }));
    }
    if (options.taskPlanningMode) {
        metadata.taskPlanningMode = true;
    }

    return {
        id: `user-${now}`,
        role: "user",
        content: text,
        timestamp: now,
        images: options.files
            .map((file) => file.url || file.publicUrl || "")
            .filter(Boolean),
        artifacts: [],
        metadata: Object.keys(metadata).length > 0 ? metadata : undefined,
    };
}

function buildAssistantPlaceholder(runId?: string): ChatMessage {
    return {
        id: `assistant-${Date.now()}`,
        role: "assistant",
        content: "",
        timestamp: Date.now(),
        runId,
        agentName: "智能主管",
        agentAvatar: "/brand-mark.png",
        agentRoleLabel: "主理人",
        agentType: "supervisor",
        artifacts: [],
        images: [],
    };
}

function mergeArtifacts(base: ChatArtifact[] = [], incoming: ChatArtifact[] = []) {
    const merged = new Map<string, ChatArtifact>();
    for (const artifact of [...base, ...incoming]) {
        const key = String(
            artifact.id
            || artifact.artifactId
            || artifact.workspacePath
            || artifact.sourcePath
            || artifact.previewUrl
            || artifact.externalUrl
            || `${artifact.kind || "artifact"}:${artifact.title || ""}`,
        ).trim();
        if (!key) {
            continue;
        }
        merged.set(key, {
            ...(merged.get(key) || {}),
            ...artifact,
        });
    }
    return Array.from(merged.values());
}

function applyTextChunk(current: ChatMessage[], chunk: string, runId?: string) {
    const next = [...current];
    const lastAssistantIndex = [...next].reverse().findIndex((message) => message.role === "assistant");
    const targetIndex = lastAssistantIndex >= 0 ? next.length - 1 - lastAssistantIndex : -1;

    if (targetIndex < 0) {
        next.push({
            ...buildAssistantPlaceholder(runId),
            content: chunk,
        });
        return normalizeMessagesForState(next);
    }

    next[targetIndex] = {
        ...next[targetIndex],
        runId: runId || next[targetIndex].runId,
        content: `${next[targetIndex].content || ""}${chunk}`,
        timestamp: Date.now(),
    };
    return normalizeMessagesForState(next);
}

function applyArtifactEvent(current: ChatMessage[], artifact: ChatArtifact | null, runId?: string) {
    if (!artifact) return current;

    const next = [...current];
    const lastAssistantIndex = [...next].reverse().findIndex((message) => message.role === "assistant");
    const targetIndex = lastAssistantIndex >= 0 ? next.length - 1 - lastAssistantIndex : -1;

    if (targetIndex < 0) {
        next.push({
            ...buildAssistantPlaceholder(runId),
            artifacts: [artifact],
        });
        return normalizeMessagesForState(next);
    }

    next[targetIndex] = {
        ...next[targetIndex],
        runId: runId || next[targetIndex].runId,
        artifacts: mergeArtifacts(next[targetIndex].artifacts || [], [artifact]),
        timestamp: Date.now(),
    };
    return normalizeMessagesForState(next);
}

function toArtifactDetail(artifact: ChatArtifact): ArtifactDetail {
    return {
        id: String(
            artifact.id
            || artifact.artifactId
            || artifact.workspacePath
            || artifact.sourcePath
            || artifact.previewUrl
            || artifact.externalUrl
            || `${artifact.kind || "artifact"}-${artifact.title || "item"}`,
        ),
        artifactId: artifact.artifactId,
        title: artifact.title,
        kind: artifact.kind,
        previewUrl: artifact.previewUrl,
        externalUrl: artifact.externalUrl,
        sourcePath: artifact.sourcePath,
        workspacePath: artifact.workspacePath,
        mimeType: artifact.mimeType,
    };
}

function mergeArtifactDetails(base: ArtifactDetail[], incoming: ArtifactDetail[]) {
    const merged = new Map<string, ArtifactDetail>();
    for (const artifact of [...base, ...incoming]) {
        const key = String(
            artifact.id
            || artifact.artifactId
            || artifact.workspacePath
            || artifact.sourcePath
            || artifact.previewUrl
            || artifact.externalUrl,
        ).trim();
        if (!key) continue;
        merged.set(key, {
            ...(merged.get(key) || {}),
            ...artifact,
        });
    }
    return Array.from(merged.values());
}

function summarizeRuntime(snapshot: RealtimeSessionSnapshot | null): RuntimeSummary {
    return {
        status: String(snapshot?.runtimeStatus || snapshot?.currentRun?.status || "idle"),
        latestSeq: Number(snapshot?.latestSeq || 0),
        runId: snapshot?.currentRun?.id,
    };
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function asRecordArray(...candidates: unknown[]): Array<Record<string, unknown>> {
    for (const candidate of candidates) {
        if (Array.isArray(candidate) && candidate.some((item) => item && typeof item === "object")) {
            return candidate.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object");
        }
    }
    return [];
}

function asTodoItems(value: unknown): SessionTodoItem[] {
    if (Array.isArray(value)) {
        return value.filter((item): item is SessionTodoItem => Boolean(item) && typeof item === "object");
    }
    const record = asRecord(value);
    if (Array.isArray(record.items)) {
        return record.items.filter((item): item is SessionTodoItem => Boolean(item) && typeof item === "object");
    }
    if (Array.isArray(record.todo)) {
        return record.todo.filter((item): item is SessionTodoItem => Boolean(item) && typeof item === "object");
    }
    return [];
}

function pickRuntimeStatus(
    projectionPayload: Record<string, unknown>,
    root: Record<string, unknown>,
    workflowProjection: Record<string, unknown>,
    currentRuntime: RuntimeSummary,
) {
    const currentRun = asRecord(projectionPayload.currentRun || root.currentRun);
    const truthChain = asRecord(workflowProjection.truthChain);
    const summary = asRecord(projectionPayload.summary || root.summary);

    const status = String(
        projectionPayload.runtimeStatus
        || root.runtimeStatus
        || currentRun.status
        || summary.workflowStatus
        || truthChain.status
        || currentRuntime.status
        || "idle",
    ).trim() || "idle";

    const latestSeq = Number(
        projectionPayload.latestSeq
        || projectionPayload.latest_seq
        || root.latestSeq
        || root.latest_seq
        || workflowProjection.latestSeq
        || workflowProjection.latest_seq
        || truthChain.latestSeq
        || truthChain.latest_seq
        || currentRuntime.latestSeq
        || 0,
    ) || 0;

    const runId = String(
        currentRun.id
        || currentRun.run_id
        || projectionPayload.runId
        || root.runId
        || currentRuntime.runId
        || "",
    ).trim() || undefined;

    const label = String(
        summary.lastRuntimeSummary
        || summary.currentStepTitle
        || projectionPayload.currentStepTitle
        || root.currentStepTitle
        || currentRuntime.label
        || "",
    ).trim() || undefined;

    return { status, latestSeq, runId, label };
}

function buildConversationOverlayPatch(detail: Partial<ConversationDetail> | null | undefined): Partial<ConversationSummary> {
    const record = asRecord(detail);
    const summary = asRecord(record.summary);
    const sourceGroup = String(summary.sourceGroup || record.sourceGroup || "").trim().toLowerCase();
    return {
        sourceGroup: sourceGroup === "channels" || sourceGroup === "cron" || sourceGroup === "hooks" || sourceGroup === "web"
            ? sourceGroup
            : undefined,
        workflowStatus: String(summary.workflowStatus || record.workflowStatus || "").trim() || undefined,
        statusLabel: String(summary.statusLabel || record.statusLabel || "").trim() || undefined,
        ownerRuntime: String(summary.ownerRuntime || record.ownerRuntime || "").trim() || undefined,
        currentStepTitle: String(summary.currentStepTitle || record.currentStepTitle || "").trim() || undefined,
        previewExcerpt: String(summary.previewExcerpt || record.previewExcerpt || "").trim() || undefined,
        lastNarrativeExcerpt: String(summary.lastNarrativeExcerpt || record.lastNarrativeExcerpt || "").trim() || undefined,
        lastRuntimeSummary: String(summary.lastRuntimeSummary || record.lastRuntimeSummary || "").trim() || undefined,
        pendingApprovalCount: Number(summary.pendingApprovalCount || record.pendingApprovalCount || 0) || 0,
        hasPendingApproval: Boolean(summary.hasPendingApproval || record.hasPendingApproval),
        controls: (record.controls as ConversationSummary["controls"]) || undefined,
        recoverable: typeof record.recoverable === "boolean" ? record.recoverable : undefined,
    };
}

function normalizeDesktopLiveErrorMessage(
    error: unknown,
    t: (zh: string, en?: string) => string,
) {
    const raw = error instanceof Error ? String(error.message || "").trim() : "";
    if (!raw) {
        return t("桌面预览尚未就绪，请稍后重试", "Desktop preview is not ready yet. Please retry shortly.");
    }
    if (/fetch failed|network request failed|failed to fetch|bridge|local-offer-unavailable|offer|candidate|session/i.test(raw)) {
        return t("桌面预览尚未就绪，请稍后重试", "Desktop preview is not ready yet. Please retry shortly.");
    }
    return raw;
}

function isPlaceholderConversationTitle(title: string | null | undefined) {
    const normalized = String(title || "").trim().toLowerCase();
    return !normalized || normalized === "new chat" || normalized === "新对话";
}

async function retryWithDelay<T>(fn: () => Promise<T>, retries: number, delayMs: number) {
    let lastError: unknown;
    for (let attempt = 0; attempt < retries; attempt += 1) {
        try {
            return await fn();
        } catch (error) {
            lastError = error;
            if (attempt < retries - 1) {
                await new Promise((resolve) => setTimeout(resolve, delayMs));
            }
        }
    }
    throw lastError instanceof Error ? lastError : new Error("Operation failed");
}

export default function ChatScreen() {
    const {
        status,
        user,
        adminBaseUrl,
        activeConversationId,
        setActiveConversationId,
        authorizedFetch,
    } = useAppSession();
    const {
        themeMode,
        voiceEnabled,
        toggleThemeMode,
        toggleVoiceEnabled,
        colors: palette,
        t,
    } = useUiPrefs();

    const realtimeAbortRef = useRef<AbortController | null>(null);
    const realtimeConversationIdRef = useRef<string | null>(null);
    const realtimeSubscriptionTokenRef = useRef(0);
    const loadingConversationIdRef = useRef<string | null>(null);
    const hydratedConversationIdRef = useRef<string | null>(null);
    const loadSupportDataRef = useRef<() => Promise<void>>(async () => undefined);
    const loadConversationRef = useRef<(conversationId: string, options?: { force?: boolean; token?: number }) => Promise<boolean>>(async () => false);
    const startRealtimeRef = useRef<(conversationId: string, transitionToken?: number) => Promise<void>>(async () => undefined);
    const stopRealtimeRef = useRef<() => void>(() => undefined);
    const closeDesktopPreviewRef = useRef<() => Promise<void>>(async () => undefined);
    const latestSeqRef = useRef(0);
    const desktopPreviewRequestIdRef = useRef(0);
    const desktopPreviewWebViewRef = useRef<WebView | null>(null);
    const desktopPreviewNegotiatedSessionRef = useRef("");
    const desktopLiveUserIntentRef = useRef(false);
    const autoPlayedVoiceKeysRef = useRef(new Set<string>());
    const runtimeRef = useRef<RuntimeSummary>({ status: "idle", latestSeq: 0 });
    const activeConversationIdRef = useRef<string | null>(activeConversationId);
    const previousConversationIdRef = useRef<string | null>(null);
    const conversationTransitionTokenRef = useRef(0);
    const ttsRequestIdRef = useRef(0);
    const tRef = useRef(t);
    const ttsPlayer = useAudioPlayer();
    const ttsStatus = useAudioPlayerStatus(ttsPlayer);
    const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
    const recorderState = useAudioRecorderState(recorder);
    const { width, height } = useWindowDimensions();
    const isLandscape = width > height;

    const [loading, setLoading] = useState(true);
    const [conversationBusy, setConversationBusy] = useState(false);
    const [sending, setSending] = useState(false);
    const [runActionBusy, setRunActionBusy] = useState(false);
    const [attachmentBusy, setAttachmentBusy] = useState(false);
    const [transcribing, setTranscribing] = useState(false);
    const [speakingId, setSpeakingId] = useState("");
    const [historyOpen, setHistoryOpen] = useState(false);

    const [input, setInput] = useState("");
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [conversations, setConversations] = useState<ConversationSummary[]>([]);
    const [approvals, setApprovals] = useState<PendingApproval[]>([]);
    const [todos, setTodos] = useState<SessionTodoItem[]>([]);
    const [artifacts, setArtifacts] = useState<ArtifactDetail[]>([]);
    const [musicTracks, setMusicTracks] = useState<MusicTrack[]>([]);
    const [commands, setCommands] = useState<CommandPresetSummary[]>([]);
    const [skills, setSkills] = useState<SkillReferenceSummary[]>([]);
    const [uploadedFiles, setUploadedFiles] = useState<UploadedWorkspaceFile[]>([]);
    const [selectedCommand, setSelectedCommand] = useState<CommandPresetSummary | null>(null);
    const [selectedSkills, setSelectedSkills] = useState<SkillReferenceSummary[]>([]);
    const [taskPlanningMode, setTaskPlanningMode] = useState(false);
    const [runtime, setRuntime] = useState<RuntimeSummary>({ status: "idle", latestSeq: 0 });
    const [runtimeTimeline, setRuntimeTimeline] = useState<PhoneRuntimeTimelineEntry[]>([]);
    const [runtimePanelOpen, setRuntimePanelOpen] = useState(false);
    const [selectedRuntimeId, setSelectedRuntimeId] = useState<PhoneRuntimeId>("chat");
    const [contextExpanded, setContextExpanded] = useState(false);
    const [desktopPreviewOpen, setDesktopPreviewOpen] = useState(false);
    const [desktopPreviewBusy, setDesktopPreviewBusy] = useState(false);
    const [desktopPreviewSessionId, setDesktopPreviewSessionId] = useState("");
    const [desktopPreviewError, setDesktopPreviewError] = useState("");
    const [desktopPreviewState, setDesktopPreviewState] = useState<"closed" | "loading" | "preview" | "error">("closed");
    const [desktopPreviewWebReady, setDesktopPreviewWebReady] = useState(false);
    const [desktopLiveStatus, setDesktopLiveStatus] = useState<DesktopLiveStatus | null>(null);

    useEffect(() => {
        runtimeRef.current = runtime;
    }, [runtime]);

    useEffect(() => {
        tRef.current = t;
    }, [t]);

    useEffect(() => {
        activeConversationIdRef.current = activeConversationId;
    }, [activeConversationId]);

    const syncArtifactsFromMessages = useCallback((nextMessages: ChatMessage[]) => {
        const derived = collectArtifactsFromMessages(nextMessages).map(toArtifactDetail);
        setArtifacts((current) => mergeArtifactDetails(current, derived));
    }, []);

    const appendRuntimeTimeline = useCallback((entry: PhoneRuntimeTimelineEntry | null) => {
        if (!entry) {
            return;
        }
        setRuntimeTimeline((current) => mergePhoneRuntimeTimeline(current, [entry]));
    }, []);

    const stopRealtime = useCallback(() => {
        realtimeSubscriptionTokenRef.current += 1;
        if (realtimeAbortRef.current) {
            realtimeAbortRef.current.abort();
            realtimeAbortRef.current = null;
        }
        realtimeConversationIdRef.current = null;
    }, []);

    const refreshDesktopLiveStatus = useCallback(async () => {
        if (!desktopLiveUserIntentRef.current) {
            return desktopLiveStatus || {
                available: false,
                bridgeReady: false,
                bridgeWarming: false,
            } satisfies DesktopLiveStatus;
        }
        try {
            const next = await getDesktopLiveStatus(authorizedFetch);
            setDesktopLiveStatus(next);
            return next;
        } catch {
            const fallback = {
                available: false,
                reason: t("桌面预览尚未就绪", "Desktop preview is not ready yet"),
                bridgeReady: false,
                bridgeWarming: true,
            } satisfies DesktopLiveStatus;
            setDesktopLiveStatus((current) => current || fallback);
            return fallback;
        }
    }, [authorizedFetch, desktopLiveStatus, t]);

    const prepareDesktopLiveBridge = useCallback(async () => {
        if (!desktopLiveUserIntentRef.current) {
            return desktopLiveStatus || {
                available: false,
                bridgeReady: false,
                bridgeWarming: false,
            } satisfies DesktopLiveStatus;
        }
        try {
            const next = await prepareDesktopLive(authorizedFetch);
            setDesktopLiveStatus(next);
            return next;
        } catch (error) {
            const fallback = {
                available: false,
                reason: normalizeDesktopLiveErrorMessage(error, t),
                bridgeReady: false,
                bridgeWarming: true,
            } satisfies DesktopLiveStatus;
            setDesktopLiveStatus((current) => current || fallback);
            return fallback;
        }
    }, [authorizedFetch, desktopLiveStatus, t]);

    const closeDesktopPreview = useCallback(async () => {
        desktopPreviewRequestIdRef.current += 1;
        desktopLiveUserIntentRef.current = false;
        const sessionId = desktopPreviewSessionId.trim();
        desktopPreviewNegotiatedSessionRef.current = "";
        desktopPreviewWebViewRef.current?.injectJavaScript(buildDesktopLiveBridgeInjection({ type: "close" }));
        setDesktopPreviewOpen(false);
        setDesktopPreviewBusy(false);
        setDesktopPreviewState("closed");
        setDesktopPreviewError("");
        setDesktopPreviewSessionId("");
        setDesktopPreviewWebReady(false);
        if (!sessionId) {
            return;
        }
        try {
            await releaseDesktopLiveSession(authorizedFetch, sessionId);
        } catch {
            // 前景预览关闭时做 best-effort 释放，避免因为释放失败阻塞 UI
        }
        setDesktopLiveStatus((current) => current ? {
            ...current,
            activeSessionId: null,
            bridgeWarming: false,
        } : current);
    }, [authorizedFetch, desktopPreviewSessionId]);

    const waitForDesktopLiveAvailability = useCallback(async (requestId: number) => {
        let lastError = "";
        for (let attempt = 0; attempt < 18; attempt += 1) {
            if (desktopPreviewRequestIdRef.current !== requestId) {
                return null;
            }
            const status = await refreshDesktopLiveStatus();
            if (status?.available === true && status?.bridgeReady !== false) {
                return status;
            }
            lastError = String(
                status?.bridgeWarming === true || status?.bridgeReady === false
                    ? t("桌面预览桥正在启动，请稍后重试", "Desktop preview bridge is starting. Please retry shortly.")
                    : status?.reason
                        || t("桌面预览尚未就绪，请稍后重试", "Desktop preview is not ready yet. Please retry shortly."),
            );
            await new Promise((resolve) => setTimeout(resolve, Math.min(900 + attempt * 150, 1800)));
        }
        throw new Error(lastError || t("桌面预览尚未就绪，请稍后重试", "Desktop preview is not ready yet. Please retry shortly."));
    }, [refreshDesktopLiveStatus, t]);

    const maybeStartDesktopPreviewNegotiation = useCallback((sessionId?: string | null) => {
        const normalizedSessionId = String(sessionId || "").trim();
        if (!desktopLiveUserIntentRef.current || !desktopPreviewOpen || !desktopPreviewWebReady || !normalizedSessionId) {
            return;
        }
        if (desktopPreviewNegotiatedSessionRef.current === normalizedSessionId) {
            return;
        }
        desktopPreviewNegotiatedSessionRef.current = normalizedSessionId;
        desktopPreviewWebViewRef.current?.injectJavaScript(
            buildDesktopLiveBridgeInjection({ type: "start" }),
        );
    }, [desktopPreviewOpen, desktopPreviewWebReady]);

    const openDesktopPreview = useCallback(async () => {
        if (desktopPreviewBusy || desktopPreviewState === "loading") {
            return;
        }
        if (desktopPreviewOpen || desktopPreviewState === "preview" || desktopPreviewState === "error") {
            await closeDesktopPreview();
            return;
        }
        desktopLiveUserIntentRef.current = true;
        const requestId = desktopPreviewRequestIdRef.current + 1;
        desktopPreviewRequestIdRef.current = requestId;
        setDesktopPreviewOpen(true);
        setDesktopPreviewBusy(true);
        setDesktopPreviewState("loading");
        setDesktopPreviewError("");
        setDesktopPreviewSessionId("");
        setDesktopPreviewWebReady(false);
        desktopPreviewNegotiatedSessionRef.current = "";
        try {
            let status = await refreshDesktopLiveStatus();
            if (desktopPreviewRequestIdRef.current !== requestId) {
                return;
            }
            if (status?.available !== true) {
                await prepareDesktopLiveBridge();
                if (desktopPreviewRequestIdRef.current !== requestId) {
                    return;
                }
                status = await waitForDesktopLiveAvailability(requestId) || {
                    available: false,
                    reason: t("桌面预览尚未就绪，请稍后重试", "Desktop preview is not ready yet. Please retry shortly."),
                    bridgeReady: false,
                    bridgeWarming: true,
                };
            }
            if (desktopPreviewRequestIdRef.current !== requestId) {
                return;
            }
            if (!status?.available) {
                throw new Error(t("桌面预览尚未就绪，请稍后重试", "Desktop preview is not ready yet. Please retry shortly."));
            }
            setDesktopLiveStatus((current) => ({
                ...(current || {}),
                ...status,
                bridgeWarming: false,
            }));
            const payload = await retryWithDelay(
                async () => createDesktopLiveSession(authorizedFetch) as Promise<DesktopLiveSessionPayload>,
                6,
                1000,
            );
            const sessionId = String(payload.sessionId || payload.session_id || "").trim();
            if (!sessionId) {
                throw new Error(t("桌面预览尚未就绪，请稍后重试", "Desktop preview is not ready yet. Please retry shortly."));
            }
            if (desktopPreviewRequestIdRef.current !== requestId) {
                try {
                    await releaseDesktopLiveSession(authorizedFetch, sessionId);
                } catch {
                    // ignore
                }
                return;
            }
            setDesktopPreviewSessionId(sessionId);
            setDesktopPreviewError("");
            setDesktopPreviewOpen(true);
            setDesktopLiveStatus((current) => ({
                ...(current || {}),
                available: true,
                bridgeReady: true,
                bridgeWarming: false,
                activeSessionId: sessionId,
            }));
            maybeStartDesktopPreviewNegotiation(sessionId);
        } catch (error) {
            if (desktopPreviewRequestIdRef.current !== requestId) {
                return;
            }
            desktopLiveUserIntentRef.current = false;
            const message = normalizeDesktopLiveErrorMessage(error, t);
            console.warn("[phone] desktop-live preview acquisition failed:", message);
            setDesktopPreviewState("error");
            setDesktopPreviewError(message);
            setDesktopLiveStatus((current) => ({
                ...(current || {}),
                available: false,
                bridgeWarming: false,
                reason: message,
            }));
        } finally {
            if (desktopPreviewRequestIdRef.current === requestId) {
                setDesktopPreviewBusy(false);
            }
        }
    }, [authorizedFetch, closeDesktopPreview, desktopPreviewOpen, desktopPreviewBusy, desktopPreviewState, maybeStartDesktopPreviewNegotiation, prepareDesktopLiveBridge, refreshDesktopLiveStatus, t, waitForDesktopLiveAvailability]);

    const desktopPreviewHtml = useMemo(() => buildDesktopLivePreviewHtml(), []);

    const handleDesktopPreviewMessage = useCallback(async (event: WebViewMessageEvent) => {
        let payload: Record<string, unknown> = {};
        try {
            payload = JSON.parse(String(event.nativeEvent.data || "{}")) as Record<string, unknown>;
        } catch {
            return;
        }

        if (!desktopLiveUserIntentRef.current) {
            return;
        }

        const type = String(payload.type || "").trim();
        const sessionId = desktopPreviewSessionId.trim();
        if (type === "ready") {
            setDesktopPreviewWebReady(true);
            maybeStartDesktopPreviewNegotiation(sessionId);
            return;
        }
        if (!sessionId) {
            return;
        }

        if (type === "local-offer") {
            try {
                const answer = await retryWithDelay(
                    async () => createDesktopLiveOffer(authorizedFetch, {
                        sessionId,
                        sdp: String(payload.sdp || ""),
                        type: String(payload.offerType || payload.type || "offer"),
                    }),
                    10,
                    1000,
                );
                if (!answer?.sdp || !answer?.type) {
                    throw new Error(t("桌面预览协商失败", "Desktop preview negotiation failed"));
                }
                desktopPreviewWebViewRef.current?.injectJavaScript(
                    buildDesktopLiveBridgeInjection({
                        type: "answer",
                        sdp: answer.sdp,
                        sdpType: answer.type,
                    }),
                );
            } catch (error) {
                setDesktopPreviewState("error");
                setDesktopPreviewBusy(false);
                setDesktopPreviewError(normalizeDesktopLiveErrorMessage(error, t));
            }
            return;
        }

        if (type === "ice-candidate") {
            const candidate = payload.candidate;
            void sendDesktopLiveCandidate(authorizedFetch, { sessionId, candidate }).catch(() => undefined);
            return;
        }

        if (type === "video-ready") {
            setDesktopPreviewBusy(false);
            setDesktopPreviewState("preview");
            setDesktopPreviewError("");
            setDesktopLiveStatus((current) => ({
                ...(current || {}),
                available: true,
                bridgeReady: true,
                bridgeWarming: false,
                activeSessionId: sessionId,
            }));
            return;
        }

        if (type === "connection-state") {
            const state = String(payload.state || "").trim().toLowerCase();
            if (state === "connecting" || state === "new" || state === "checking") {
                setDesktopPreviewState("loading");
                setDesktopPreviewBusy(true);
                setDesktopPreviewError("");
            }
            if (state === "failed") {
                desktopLiveUserIntentRef.current = false;
                setDesktopPreviewState("error");
                setDesktopPreviewBusy(false);
                setDesktopPreviewError(t("桌面预览连接失败，请重试", "Desktop preview failed to connect. Please retry."));
                setDesktopLiveStatus((current) => ({
                    ...(current || {}),
                    activeSessionId: null,
                    bridgeWarming: false,
                }));
            }
            return;
        }

        if (type === "error") {
            desktopLiveUserIntentRef.current = false;
            setDesktopPreviewState("error");
            setDesktopPreviewBusy(false);
            setDesktopPreviewError(normalizeDesktopLiveErrorMessage(payload.message, t));
            setDesktopLiveStatus((current) => ({
                ...(current || {}),
                activeSessionId: null,
                bridgeWarming: false,
            }));
        }
    }, [authorizedFetch, desktopPreviewSessionId, maybeStartDesktopPreviewNegotiation, t]);

    const desktopLiveReady = desktopLiveStatus?.available === true && desktopLiveStatus?.bridgeReady !== false;
    const desktopLiveConnecting = desktopPreviewBusy || desktopPreviewState === "loading" || (desktopLiveStatus?.bridgeWarming === true && !desktopLiveReady);
    const desktopLiveConnected = desktopPreviewState === "preview";

    const topbarActions = useMemo<PhoneTopbarAction[]>(() => [
        {
            key: "desktop-live",
            onPress: () => void openDesktopPreview(),
            tone: desktopLiveConnected ? "primary" : "default",
            indicatorColor: desktopLiveConnected ? "#10B981" : undefined,
            disabled: desktopPreviewBusy || (desktopLiveStatus?.bridgeStartable === false && !desktopLiveConnecting && !desktopLiveConnected),
        },
        { key: "rpa", onPress: () => router.push("/rpa" as Href) },
        { key: "voice", onPress: () => void toggleVoiceEnabled() },
        { key: "theme", onPress: () => void toggleThemeMode() },
    ], [desktopLiveConnected, desktopLiveConnecting, desktopLiveStatus?.bridgeStartable, desktopPreviewBusy, openDesktopPreview, toggleThemeMode, toggleVoiceEnabled]);

    useEffect(() => {
        maybeStartDesktopPreviewNegotiation(desktopPreviewSessionId);
    }, [desktopPreviewSessionId, maybeStartDesktopPreviewNegotiation]);

    const loadSupportData = useCallback(async () => {
        const [nextConversations, nextCommands, nextSkills, nextMusic] = await Promise.all([
            listConversations(authorizedFetch),
            listCommandPresets(authorizedFetch).catch(() => []),
            listSkills(authorizedFetch).catch(() => []),
            listMusicTracks(authorizedFetch).catch(() => []),
        ]);

        setConversations(nextConversations);
        setCommands(nextCommands);
        setSkills(nextSkills);
        setMusicTracks(nextMusic);

        if (
            activeConversationIdRef.current
            && !nextConversations.some((item) => item.id === activeConversationIdRef.current)
        ) {
            await setActiveConversationId(null);
        }
    }, [authorizedFetch, setActiveConversationId]);

    const applyConversationProjection = useCallback((payload: Partial<ConversationDetail | RealtimeSessionSnapshot | Record<string, unknown>> | null | undefined) => {
        const root = asRecord(payload);
        const projectionPayload = asRecord(root.projection);
        const effectiveProjection = Object.keys(projectionPayload).length > 0 ? projectionPayload : root;
        const workflowProjection = asRecord(effectiveProjection.workflowProjection || root.workflowProjection);
        const nextApprovals = Array.isArray(effectiveProjection.approvals)
            ? effectiveProjection.approvals as PendingApproval[]
            : Array.isArray(root.approvals)
                ? root.approvals as PendingApproval[]
                : [];
        const nextTodos = asTodoItems(effectiveProjection.todos || root.todos || workflowProjection.todos);
        const nextRuntimeEvents = asRecordArray(
            effectiveProjection.runtimeTimeline,
            effectiveProjection.runtimeEvents,
            root.runtimeTimeline,
            root.runtimeEvents,
            workflowProjection.runtimeTimeline,
            workflowProjection.runtimeEvents,
            workflowProjection.eventTail,
            workflowProjection.activities,
        );
        const nextRuntime = pickRuntimeStatus(effectiveProjection, root, workflowProjection, runtimeRef.current);

        setApprovals(nextApprovals);
        setTodos(nextTodos);
        setRuntime(nextRuntime);
        setRuntimeTimeline(normalizePhoneRuntimeTimeline(nextRuntimeEvents));
        latestSeqRef.current = nextRuntime.latestSeq;
    }, []);

    const handleRealtimeEvent = useCallback((eventName: string, payload: unknown) => {
        if (eventName === "snapshot" && payload && typeof payload === "object") {
            applyConversationProjection(payload as RealtimeSessionSnapshot);
            return;
        }

        const normalized = normalizePhoneRealtimeEvent(payload);
        if (!normalized) {
            return;
        }

        if (normalized.seq && normalized.seq <= latestSeqRef.current) {
            return;
        }
        if (normalized.seq) {
            latestSeqRef.current = normalized.seq;
        }

        if (normalized.name === "ask_user") {
            const approval = buildApprovalFromEvent(normalized);
            if (approval) {
                setApprovals((current) => upsertApproval(current, approval));
                setRuntime((current) => ({
                    ...current,
                    status: "waiting_approval",
                        runId: normalized.run_id || current.runId,
                    }));
                appendRuntimeTimeline(
                    buildPhoneRuntimeTimelineEntryFromEvent({
                        topic: normalized.topic || "approval.requested",
                        seq: normalized.seq,
                        event_id: normalized.event_id,
                        ts: normalized.ts,
                        payload: {
                            question: normalized.data?.question,
                    actorLabel: tRef.current("运行调度", "Automation"),
                },
            }) || buildRuntimeTimelineEntry(
                normalizePhoneRuntimeId(String(normalized.topic || normalized.data?.topic || "automation")) || "automation",
                normalized.topic || "approval.requested",
                String(normalized.data?.question || tRef.current("等待用户确认", "Waiting for approval")),
                {
                    id: normalized.event_id || `approval:${normalized.seq || Date.now()}`,
                    timestamp: normalized.ts || Date.now(),
                    actorLabel: tRef.current("运行调度", "Automation"),
                },
            ),
        );
            }
            return;
        }

        if (normalized.name === "artifact_recorded") {
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent({
                    topic: normalized.topic || "artifact.recorded",
                    seq: normalized.seq,
                    event_id: normalized.event_id,
                    ts: normalized.ts,
                    payload: {
                        title: normalized.artifact?.title,
                        kind: normalized.artifact?.kind,
                        workspacePath: normalized.artifact?.workspacePath,
                    },
                }) || buildRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.topic || normalized.artifact?.kind || "chat")) || "chat",
                    normalized.topic || "artifact.recorded",
                    String(normalized.artifact?.title || normalized.artifact?.kind || "记录新的产物"),
                    {
                        id: normalized.event_id || `artifact:${normalized.seq || Date.now()}`,
                        timestamp: normalized.ts || Date.now(),
                    },
                ),
            );
            setMessages((current) => {
                const next = applyArtifactEvent(current, normalized.artifact || null, normalized.run_id);
                syncArtifactsFromMessages(next);
                return next;
            });
            return;
        }

        if (normalized.name === "runtime_progress") {
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent({
                    topic: normalized.topic || String(normalized.data?.topic || "runtime.progress"),
                    runtimeId: normalized.data?.runtimeId,
                    seq: normalized.seq,
                    event_id: normalized.event_id,
                    ts: normalized.ts,
                    payload: normalized.data,
                }) || buildRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.topic || normalized.data?.topic || "chat")) || "chat",
                    normalized.topic || String(normalized.data?.topic || "runtime.progress"),
                    String(normalized.data?.label || normalized.topic || "运行更新"),
                    {
                        id: normalized.event_id || `runtime:${normalized.seq || Date.now()}`,
                        timestamp: normalized.ts || Date.now(),
                    },
                ),
            );
            setRuntime((current) => ({
                status: "running",
                latestSeq: normalized.seq || current.latestSeq,
                runId: normalized.run_id || current.runId,
                label: typeof normalized.data?.label === "string" ? normalized.data.label : current.label,
            }));
            return;
        }

        if (normalized.name === "run_controlled") {
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent({
                    topic: normalized.topic || String(normalized.data?.topic || "run.controlled"),
                    runtimeId: normalized.data?.runtimeId,
                    seq: normalized.seq,
                    event_id: normalized.event_id,
                    ts: normalized.ts,
                    payload: normalized.data,
                }) || buildRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.topic || normalized.data?.topic || "chat")) || "chat",
                    normalized.topic || String(normalized.data?.topic || "run.controlled"),
                    String(normalized.data?.topic || "运行控制已更新"),
                    {
                        id: normalized.event_id || `control:${normalized.seq || Date.now()}`,
                        timestamp: normalized.ts || Date.now(),
                    },
                ),
            );
            const topic = String(normalized.data?.topic || "");
            setRuntime((current) => ({
                ...current,
                status: topic.includes("paused") ? "waiting_approval" : topic.includes("failed") ? "failed" : current.status,
                latestSeq: normalized.seq || current.latestSeq,
                runId: normalized.run_id || current.runId,
            }));
            return;
        }

        const topic = String(normalized.topic || normalized.data?.topic || normalized.name || "").trim();
        if (!topic) {
            return;
        }

        appendRuntimeTimeline(
            buildPhoneRuntimeTimelineEntryFromEvent({
                topic,
                runtimeId: normalized.data?.runtimeId,
                seq: normalized.seq,
                event_id: normalized.event_id,
                ts: normalized.ts,
                payload: normalized.data,
            }) || buildRuntimeTimelineEntry(
                normalizePhoneRuntimeId(String(normalized.data?.runtimeId || normalized.data?.runtime || topic)) || "chat",
                topic,
                String(normalized.data?.label || normalized.data?.summary || topic),
                {
                    id: normalized.event_id || `runtime:${normalized.seq || Date.now()}`,
                    timestamp: normalized.ts || Date.now(),
                    actorLabel: typeof normalized.data?.actorLabel === "string" ? normalized.data.actorLabel : undefined,
                    status: typeof normalized.data?.status === "string" ? normalized.data.status : undefined,
                },
            ),
        );

        setRuntime((current) => {
            const loweredTopic = topic.toLowerCase();
            let nextStatus = current.status;
            if (loweredTopic.includes("failed") || loweredTopic.includes("error")) {
                nextStatus = "failed";
            } else if (loweredTopic.includes("completed") || loweredTopic.includes("finished") || loweredTopic.includes("succeeded")) {
                nextStatus = "completed";
            } else if (loweredTopic.includes("paused") || loweredTopic.includes("waiting_input")) {
                nextStatus = "waiting_input";
            } else if (loweredTopic.includes("approval")) {
                nextStatus = "waiting_approval";
            } else if (loweredTopic.includes("started") || loweredTopic.includes("running") || loweredTopic.includes("progress") || loweredTopic.includes("acquired")) {
                nextStatus = "running";
            }
            return {
                ...current,
                status: nextStatus,
                latestSeq: normalized.seq || current.latestSeq,
                runId: normalized.run_id || current.runId,
                label: typeof normalized.data?.label === "string" ? normalized.data.label : current.label,
            };
        });
    }, [appendRuntimeTimeline, applyConversationProjection, syncArtifactsFromMessages]);

    const startRealtime = useCallback(async (conversationId: string, transitionToken?: number) => {
        if (
            activeConversationIdRef.current !== conversationId
            || (typeof transitionToken === "number" && conversationTransitionTokenRef.current !== transitionToken)
        ) {
            return;
        }
        if (realtimeConversationIdRef.current === conversationId && realtimeAbortRef.current) {
            return;
        }
        stopRealtime();
        const controller = new AbortController();
        const subscriptionToken = realtimeSubscriptionTokenRef.current;
        realtimeAbortRef.current = controller;
        realtimeConversationIdRef.current = conversationId;
        try {
            await streamRealtimeSession(authorizedFetch, conversationId, handleRealtimeEvent, controller.signal);
        } catch (error) {
            if (!controller.signal.aborted) {
                console.warn("[phone] realtime stream stopped:", error);
            }
        } finally {
            if (realtimeSubscriptionTokenRef.current === subscriptionToken && realtimeAbortRef.current === controller) {
                realtimeAbortRef.current = null;
            }
            if (realtimeSubscriptionTokenRef.current === subscriptionToken && realtimeConversationIdRef.current === conversationId) {
                realtimeConversationIdRef.current = null;
            }
        }
    }, [authorizedFetch, handleRealtimeEvent, stopRealtime]);

    const loadConversation = useCallback(async (conversationId: string, options?: { force?: boolean; token?: number }) => {
        const transitionToken = options?.token ?? conversationTransitionTokenRef.current;
        if (!options?.force) {
            if (loadingConversationIdRef.current === conversationId) {
                return false;
            }
            if (hydratedConversationIdRef.current === conversationId) {
                return false;
            }
        }
        loadingConversationIdRef.current = conversationId;
        setConversationBusy(true);
        try {
            const detail = await getConversationDetail(authorizedFetch, conversationId);
            if (
                activeConversationIdRef.current !== conversationId
                || conversationTransitionTokenRef.current !== transitionToken
            ) {
                return false;
            }
            const normalized = normalizeMessagesForState(detail.messages || []);
            setMessages(normalized);
            syncArtifactsFromMessages(normalized);
            applyConversationProjection(detail);
            hydratedConversationIdRef.current = conversationId;
            const overlayPatch = buildConversationOverlayPatch(detail);
            setConversations((current) => sortSessionHistory(current.map((item) => (
                item.id === conversationId
                    ? mergeSessionHistoryOverlay(item, {
                        ...overlayPatch,
                        controls: detail.controls || overlayPatch.controls || item.controls,
                        recoverable: typeof detail.recoverable === "boolean"
                            ? detail.recoverable
                            : overlayPatch.recoverable ?? item.recoverable,
                    })
                    : item
            ))));
            return true;
        } catch (error) {
            if (
                activeConversationIdRef.current === conversationId
                && conversationTransitionTokenRef.current === transitionToken
            ) {
                Alert.alert("读取会话失败", error instanceof Error ? error.message : "无法加载会话详情");
            }
            return false;
        } finally {
            if (loadingConversationIdRef.current === conversationId) {
                loadingConversationIdRef.current = null;
            }
            if (
                activeConversationIdRef.current === conversationId
                && conversationTransitionTokenRef.current === transitionToken
            ) {
                setConversationBusy(false);
            }
        }
    }, [applyConversationProjection, authorizedFetch, syncArtifactsFromMessages]);

    const ensureConversation = useCallback(async () => {
        if (activeConversationId) {
            return { id: activeConversationId, created: false };
        }

        const created = await createConversation(authorizedFetch, "");
        setConversations((current) => [created, ...current.filter((item) => item.id !== created.id)]);
        await setActiveConversationId(created.id);
        return { id: created.id, created: true };
    }, [activeConversationId, authorizedFetch, setActiveConversationId]);

    loadSupportDataRef.current = loadSupportData;
    loadConversationRef.current = loadConversation;
    startRealtimeRef.current = startRealtime;
    stopRealtimeRef.current = stopRealtime;
    closeDesktopPreviewRef.current = closeDesktopPreview;

    useEffect(() => {
        if (status !== "authenticated") {
            stopRealtimeRef.current();
            if (status !== "booting") {
                setLoading(false);
            }
            return;
        }
        let cancelled = false;
        void (async () => {
            setLoading(true);
            try {
                await loadSupportDataRef.current();
            } finally {
                if (!cancelled) {
                    setLoading(false);
                }
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [status]);

    useEffect(() => {
        if (status !== "authenticated") {
            conversationTransitionTokenRef.current += 1;
            previousConversationIdRef.current = null;
            hydratedConversationIdRef.current = null;
            loadingConversationIdRef.current = null;
            latestSeqRef.current = 0;
            stopRealtimeRef.current();
            return;
        }
        if (!activeConversationId) {
            conversationTransitionTokenRef.current += 1;
            previousConversationIdRef.current = null;
            hydratedConversationIdRef.current = null;
            loadingConversationIdRef.current = null;
            setMessages([]);
            setApprovals([]);
            setTodos([]);
            setArtifacts([]);
            setRuntime({ status: "idle", latestSeq: 0 });
            setRuntimeTimeline([]);
            latestSeqRef.current = 0;
            stopRealtimeRef.current();
            return;
        }
        const conversationChanged = previousConversationIdRef.current !== activeConversationId;
        previousConversationIdRef.current = activeConversationId;
        const transitionToken = conversationTransitionTokenRef.current + 1;
        conversationTransitionTokenRef.current = transitionToken;
        if (conversationChanged) {
            stopRealtimeRef.current();
            latestSeqRef.current = 0;
        }
        let cancelled = false;
        void (async () => {
            const loaded = await loadConversationRef.current(activeConversationId, {
                force: conversationChanged,
                token: transitionToken,
            });
            if (
                cancelled
                || conversationTransitionTokenRef.current !== transitionToken
                || activeConversationIdRef.current !== activeConversationId
            ) {
                return;
            }
            if (loaded || realtimeConversationIdRef.current !== activeConversationId) {
                await startRealtimeRef.current(activeConversationId, transitionToken);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [activeConversationId, status]);

    useEffect(() => {
        autoPlayedVoiceKeysRef.current.clear();
        setSpeakingId("");
    }, [activeConversationId]);

    useEffect(() => {
        if (speakingId && ttsStatus.didJustFinish) {
            setSpeakingId("");
        }
    }, [speakingId, ttsStatus.didJustFinish]);

    useEffect(() => {
        return () => {
            stopRealtimeRef.current();
        };
    }, []);

    useEffect(() => {
        return () => {
            void closeDesktopPreviewRef.current();
        };
    }, []);

    const handleSelectConversation = useCallback(async (item: ConversationSummary) => {
        setHistoryOpen(false);
        setInput("");
        setUploadedFiles([]);
        await setActiveConversationId(item.id);
    }, [setActiveConversationId]);

    const handleNewConversation = useCallback(async () => {
        stopRealtime();
        hydratedConversationIdRef.current = null;
        loadingConversationIdRef.current = null;
        setHistoryOpen(false);
        setInput("");
        setMessages([]);
        setApprovals([]);
        setTodos([]);
        setArtifacts([]);
        setUploadedFiles([]);
        setSelectedCommand(null);
        setSelectedSkills([]);
        setTaskPlanningMode(false);
        setContextExpanded(false);
        setRuntimePanelOpen(false);
        setSelectedRuntimeId("chat");
        setRuntime({ status: "idle", latestSeq: 0 });
        setRuntimeTimeline([]);
        latestSeqRef.current = 0;
        await setActiveConversationId(null);
    }, [setActiveConversationId, stopRealtime]);

    const handleDeleteConversation = useCallback((item: ConversationSummary) => {
        Alert.alert(t("删除会话", "Delete conversation"), t("确定删除这个会话吗？", "Delete this conversation?"), [
            { text: t("取消", "Cancel"), style: "cancel" },
            {
                text: t("删除", "Delete"),
                style: "destructive",
                onPress: () => {
                    void (async () => {
                        await deleteConversation(authorizedFetch, item.id);
                        const nextConversations = conversations.filter((conversation) => conversation.id !== item.id);
                        setConversations(nextConversations);
                        if (activeConversationId === item.id) {
                            const fallbackId = nextConversations[0]?.id || null;
                            await setActiveConversationId(fallbackId);
                        }
                    })().catch((error) => {
                        Alert.alert(t("删除失败", "Delete failed"), error instanceof Error ? error.message : t("无法删除会话", "Unable to delete conversation"));
                    });
                },
            },
        ]);
    }, [activeConversationId, authorizedFetch, conversations, setActiveConversationId, t]);

    const handleDeleteMessage = useCallback((message: ChatMessage) => {
        Alert.alert(t("删除消息", "Delete message"), t("确定删除这条消息吗？", "Delete this message?"), [
            { text: t("取消", "Cancel"), style: "cancel" },
            {
                text: t("删除", "Delete"),
                style: "destructive",
                onPress: () => {
                    void (async () => {
                        if (message.id && !message.id.startsWith("user-") && !message.id.startsWith("assistant-")) {
                            await deleteMessage(authorizedFetch, message.id);
                        }
                        setMessages((current) => current.filter((item) => item.renderKey !== message.renderKey));
                    })().catch((error) => {
                        Alert.alert(t("删除失败", "Delete failed"), error instanceof Error ? error.message : t("无法删除消息", "Unable to delete message"));
                    });
                },
            },
        ]);
    }, [authorizedFetch, t]);

    const handlePickAttachment = useCallback(async () => {
        setAttachmentBusy(true);
        try {
            const result = await DocumentPicker.getDocumentAsync({
                copyToCacheDirectory: true,
            });
            if (result.canceled || !Array.isArray(result.assets) || result.assets.length === 0) {
                return;
            }

            const uploaded = await Promise.all(result.assets.map(async (asset) => uploadAttachment(authorizedFetch, {
                uri: asset.uri,
                name: asset.name,
                type: asset.mimeType || "application/octet-stream",
            })));

            setUploadedFiles((current) => [...current, ...uploaded]);
        } catch (error) {
            Alert.alert("上传失败", error instanceof Error ? error.message : "无法上传附件");
        } finally {
            setAttachmentBusy(false);
        }
    }, [authorizedFetch]);

    const handleToggleRecording = useCallback(async () => {
        try {
            if (!recorder.isRecording) {
                const permission = await requestRecordingPermissionsAsync();
                if (!permission.granted) {
                    throw new Error(t("需要麦克风权限才能录音", "Microphone access is required"));
                }
                await setAudioModeAsync({
                    allowsRecording: true,
                    playsInSilentMode: true,
                });
                await recorder.prepareToRecordAsync();
                recorder.record();
                return;
            }

            await recorder.stop();
            await setAudioModeAsync({
                allowsRecording: false,
                playsInSilentMode: true,
            });
            const uri = recorder.uri;
            if (!uri) {
                throw new Error(t("没有拿到录音文件", "No recording file found"));
            }

            setTranscribing(true);
            const formData = new FormData();
            formData.append("file", {
                uri,
                name: `voice-${Date.now()}.m4a`,
                type: "audio/m4a",
            } as unknown as Blob);
            const payload = await speechToText(authorizedFetch, formData) as { text?: string; error?: string };
            const text = String(payload.text || "").trim();
            if (!text) {
                throw new Error(String(payload.error || t("未识别到语音内容", "No speech detected")));
            }
            setInput((current) => [current.trim(), text].filter(Boolean).join(current.trim() ? "\n" : ""));
        } catch (error) {
            Alert.alert(t("录音失败", "Recording failed"), error instanceof Error ? error.message : t("无法完成录音转写", "Unable to transcribe recording"));
        } finally {
            setTranscribing(false);
        }
    }, [authorizedFetch, recorder, t]);

    const handleSpeakVoice = useCallback(async (text: string, messageKey: string) => {
        const voiceText = text.trim();
        if (!voiceText) {
            return;
        }
        const player = ttsPlayer as typeof ttsPlayer & { seekTo?: (position: number) => void };
        if (speakingId === messageKey && ttsStatus.playing) {
            ttsRequestIdRef.current += 1;
            player.pause();
            player.seekTo?.(0);
            setSpeakingId("");
            return;
        }
        const requestId = ttsRequestIdRef.current + 1;
        ttsRequestIdRef.current = requestId;
        setSpeakingId(messageKey);
        try {
            player.pause();
            player.seekTo?.(0);
            const response = await requestTextToSpeech(authorizedFetch, { text: voiceText });
            const cached = await saveResponseToCache(response, { prefix: "tts", fallbackExtension: "mp3" });
            const audioUri = String(cached.uri || "").trim();
            if (!audioUri) {
                throw new Error(t("语音文件生成失败", "Failed to create audio file"));
            }
            if (ttsRequestIdRef.current !== requestId) {
                return;
            }
            player.replace({ uri: audioUri });
            player.play();
        } catch (error) {
            if (ttsRequestIdRef.current === requestId) {
                setSpeakingId("");
            }
            Alert.alert(t("语音播放失败", "Speech playback failed"), error instanceof Error ? error.message : t("无法播放这条语音", "Unable to play audio"));
        }
    }, [authorizedFetch, speakingId, t, ttsPlayer, ttsStatus.playing]);

    const handleApprovalResolve = useCallback(async (approval: PendingApproval, answer: string, approve: boolean) => {
        const approvalId = approval.id || approval.approval_id;
        if (!approvalId) {
            return;
        }
        await approvePendingItem(authorizedFetch, approvalId, answer, approve);
        setApprovals((current) => current.filter((item) => String(item.id || item.approval_id || "") !== approvalId));
    }, [authorizedFetch]);

    const openApprovalPanel = useCallback(() => {
        setSelectedRuntimeId("automation");
        setRuntimePanelOpen(true);
    }, []);

    const handleRunCommand = useCallback(async (command: "interrupt" | "retry") => {
        const runId = String(runtime.runId || "").trim();
        if (!runId || runActionBusy) {
            return;
        }
        setRunActionBusy(true);
        try {
            await dispatchRunCommand(authorizedFetch, runId, command);
            if (activeConversationIdRef.current) {
                await loadConversationRef.current(activeConversationIdRef.current, { force: true });
            }
        } catch (error) {
            Alert.alert(
                command === "interrupt" ? t("中断失败", "Stop failed") : t("重试失败", "Retry failed"),
                error instanceof Error ? error.message : t("运行控制失败", "Run command failed"),
            );
        } finally {
            setRunActionBusy(false);
        }
    }, [authorizedFetch, runActionBusy, runtime.runId, t]);

    const projection = useMemo(
        () => buildPhoneChatProjection({
            conversations,
            activeConversationId,
            messages,
            approvals,
            todos,
            artifacts,
            runtime,
            runtimeTimeline,
            selectedRuntimeId,
            t,
        }),
        [activeConversationId, approvals, artifacts, conversations, messages, runtime, runtimeTimeline, selectedRuntimeId, t, todos],
    );

    const latestAutoPlayableVoice = projection.voiceCardDescriptors[projection.voiceCardDescriptors.length - 1] || null;

    useEffect(() => {
        if (projection.selectedRuntimeId !== selectedRuntimeId) {
            setSelectedRuntimeId(projection.selectedRuntimeId);
        }
    }, [projection.selectedRuntimeId, selectedRuntimeId]);

    useEffect(() => {
        if (!activeConversationId) {
            return;
        }

        setConversations((current) => {
            const index = current.findIndex((item) => item.id === activeConversationId);
            if (index < 0) {
                return current;
            }

            const currentConversation = current[index];
            const nextWorkflowStatus = String(runtime.status || currentConversation.workflowStatus || "idle").trim() || "idle";
            const nextPreview = projection.historyPreview || currentConversation.previewExcerpt || currentConversation.lastNarrativeExcerpt || undefined;
            const nextRuntimeSummary = projection.selectedRuntimeActivities[0]?.summary
                || projection.selectedRuntimeDockItem?.lastActivity
                || projection.currentStepTitle
                || currentConversation.lastRuntimeSummary
                || undefined;
            const latestActivityTimestamp = projection.projectedMessages[projection.projectedMessages.length - 1]?.timestamp
                || projection.selectedRuntimeActivities[0]?.timestamp
                || currentConversation.lastActivityAt;
            const merged = mergeSessionHistoryOverlay(currentConversation, {
                lastActivityAt: typeof latestActivityTimestamp === "number"
                    ? new Date(latestActivityTimestamp).toISOString()
                    : latestActivityTimestamp,
                workflowStatus: nextWorkflowStatus,
                statusLabel: currentConversation.workflowStatus === nextWorkflowStatus ? currentConversation.statusLabel : undefined,
                ownerRuntime: projection.activeConversation?.ownerRuntime || projection.runtimeStageModel.activeRuntimeId || currentConversation.ownerRuntime,
                currentStepTitle: projection.currentStepTitle || currentConversation.currentStepTitle,
                previewExcerpt: nextPreview,
                lastNarrativeExcerpt: nextPreview,
                lastRuntimeSummary: nextRuntimeSummary,
                pendingApprovalCount: projection.pendingApprovalCount,
                hasPendingApproval: projection.pendingApprovalCount > 0,
                recoverable: Boolean(currentConversation.recoverable || currentConversation.controls?.canRetry),
                controls: currentConversation.controls,
                scopeTags: projection.activeScopeTags.length > 0 ? projection.activeScopeTags : currentConversation.scopeTags,
            });

            if (JSON.stringify(currentConversation) === JSON.stringify(merged)) {
                return current;
            }

            const next = [...current];
            next[index] = merged;
            return sortSessionHistory(next);
        });
    }, [
        activeConversationId,
        projection.activeConversation?.ownerRuntime,
        projection.activeScopeTags,
        projection.currentStepTitle,
        projection.historyPreview,
        projection.pendingApprovalCount,
        projection.runtimeStageModel.activeRuntimeId,
        projection.selectedRuntimeDockItem?.lastActivity,
        projection.selectedRuntimeActivities,
        runtime.status,
    ]);

    useEffect(() => {
        if (!voiceEnabled || !latestAutoPlayableVoice?.autoPlayKey || !latestAutoPlayableVoice.voiceText.trim()) {
            return;
        }
        if (autoPlayedVoiceKeysRef.current.has(latestAutoPlayableVoice.autoPlayKey)) {
            return;
        }
        autoPlayedVoiceKeysRef.current.add(latestAutoPlayableVoice.autoPlayKey);
        void handleSpeakVoice(latestAutoPlayableVoice.voiceText, latestAutoPlayableVoice.autoPlayKey);
    }, [handleSpeakVoice, latestAutoPlayableVoice, voiceEnabled]);

    const handleSend = useCallback(async () => {
        const text = input.trim();
        if (!text && !selectedCommand && selectedSkills.length === 0 && uploadedFiles.length === 0) {
            return;
        }

        setSending(true);
        try {
            const ensuredConversation = await ensureConversation();
            const userMessage = buildUserMessage(text, {
                command: selectedCommand,
                skills: selectedSkills,
                taskPlanningMode,
                files: uploadedFiles,
            });

            setMessages((current) => normalizeMessagesForState([...current, userMessage]));
            setInput("");

            if (text) {
                setConversations((current) => current.map((conversation) =>
                    conversation.id === ensuredConversation.id
                        ? {
                            ...conversation,
                            title: isPlaceholderConversationTitle(conversation.title)
                                ? (text.slice(0, 36) || conversation.title || "")
                                : conversation.title,
                            updatedAt: new Date().toISOString(),
                            previewExcerpt: text.slice(0, 120),
                        }
                        : conversation,
                ));
            }

            const historyMessages = messages.map((message) => ({
                role: message.role,
                content: message.content,
            }));

            await sendChatMessageStream(
                authorizedFetch,
                text,
                {
                    messages: historyMessages,
                    conversationId: ensuredConversation.id,
                commandPresetName: selectedCommand?.name || null,
                    skillReferences: selectedSkills,
                    fileUrls: uploadedFiles.map((file) => file.url || file.publicUrl || "").filter(Boolean),
                    taskPlanningMode,
                },
                (event: ChatStreamEvent) => {
                    if (event.type === "text_chunk") {
                        setMessages((current) => applyTextChunk(current, String(event.content || ""), event.run_id));
                        setRuntime((current) => ({
                            ...current,
                            status: "running",
                            runId: event.run_id || current.runId,
                        }));
                        return;
                    }

                    if (event.type === "custom_event" || event.type === "agent_start") {
                        handleRealtimeEvent("message", event);
                        return;
                    }

                    if (event.type === "done") {
                        setRuntime((current) => ({
                            ...current,
                            status: current.status === "waiting_approval" ? current.status : "completed",
                            runId: event.run_id || current.runId,
                        }));
                        return;
                    }

                    if (event.type === "error") {
                        throw new Error(String(event.error || t("聊天流失败", "Chat stream failed")));
                    }
                },
            );

            setUploadedFiles([]);
        } catch (error) {
            Alert.alert(t("发送失败", "Send failed"), error instanceof Error ? error.message : t("无法发送消息", "Unable to send message"));
        } finally {
            setSending(false);
        }
    }, [
        activeConversationId,
        authorizedFetch,
        ensureConversation,
        handleRealtimeEvent,
        input,
        messages,
        selectedCommand,
        selectedSkills,
        taskPlanningMode,
        t,
        uploadedFiles,
    ]);

    if (status === "booting") {
        return <LoadingScreen label="正在读取聊天主链…" />;
    }

    if (status === "anonymous") {
        return <Redirect href="/login" />;
    }

    const profileImageUri = resolveAdminAssetUrl(adminBaseUrl, user?.image || "");

    return (
        <LinearGradient
            colors={themeMode === "dark" ? [palette.backgroundDeep, palette.background] : [palette.background, palette.backgroundDeep]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={styles.gradient}
        >
            <SafeAreaView style={styles.safeArea} edges={["top", "left", "right"]}>
                <PhoneTopbar
                    actions={topbarActions}
                    userImageUri={profileImageUri || undefined}
                    onProfilePress={() => router.push("/settings" as Href)}
                />

                <KeyboardAvoidingView
                    style={styles.chatShell}
                    behavior={Platform.OS === "ios" ? "padding" : undefined}
                    keyboardVerticalOffset={Platform.OS === "ios" ? 12 : 0}
                >
                    <View style={[styles.chatStage, isLandscape && styles.chatStageLandscape]}>
                        <Pressable
                            style={[
                                styles.historyFab,
                                isLandscape && styles.historyFabLandscape,
                                { backgroundColor: palette.surfaceStrong, borderColor: palette.border },
                            ]}
                            onPress={() => setHistoryOpen(true)}
                        >
                            <MaterialCommunityIcons name="view-headline" size={20} color={palette.text} />
                        </Pressable>

                        <View style={[styles.controlRail, isLandscape && styles.controlRailLandscape]}>
                            <View style={styles.controlRailTopRow}>
                                <Pressable
                                    style={[
                                        styles.contextButton,
                                        { backgroundColor: contextExpanded ? palette.primarySoft : palette.surfaceStrong, borderColor: contextExpanded ? `${palette.primary}33` : palette.border },
                                    ]}
                                    onPress={() => setContextExpanded((current) => !current)}
                                >
                                    <MaterialCommunityIcons
                                        name="file-tree-outline"
                                        size={13}
                                        color={contextExpanded ? palette.primary : palette.textMuted}
                                    />
                                </Pressable>

                                <View style={[styles.runControlWrap, { backgroundColor: palette.surfaceStrong, borderColor: palette.border }]}>
                                    <RunControlBar
                                        runId={projection.runControlState.runId || runtime.runId}
                                        status={projection.runControlState.status || runtime.status}
                                        pendingApproval={projection.pendingApprovalCount > 0}
                                        canOpenApproval={projection.runControlState.canOpenApproval}
                                        canResume={projection.runControlState.canResume}
                                        canRetry={projection.runControlState.canRetry}
                                        canInterrupt={projection.runControlState.canInterrupt}
                                        busy={runActionBusy}
                                        onOpenApproval={openApprovalPanel}
                                        onRetry={() => void handleRunCommand("retry")}
                                        onInterrupt={() => void handleRunCommand("interrupt")}
                                    />
                                </View>

                                <RuntimeDock
                                    items={projection.runtimeStageModel.items}
                                    selectedRuntimeId={projection.selectedRuntimeId}
                                    panelOpen={runtimePanelOpen}
                                    onSelectRuntime={(runtimeId) => {
                                        setSelectedRuntimeId(runtimeId);
                                        setRuntimePanelOpen(true);
                                    }}
                                />
                            </View>

                            {contextExpanded ? (
                                <GlassCard style={[styles.contextCard, { backgroundColor: palette.surfaceStrong, borderColor: palette.border }]}>
                                    <Text style={[styles.contextTitle, { color: palette.text }]}>{t("项目上下文", "Project context")}</Text>
                                    <Text style={[styles.contextSubtitle, { color: palette.textMuted }]}>
                                        {t("仅在需要时手动切换项目与 scope。", "Manually switch project and scope only when needed.")}
                                    </Text>
                                    <View style={styles.contextChips}>
                                        <View style={[styles.contextChip, { backgroundColor: palette.primarySoft }]}>
                                            <Text style={[styles.contextChipText, { color: palette.primaryDeep }]}>
                                                {t("项目", "Project")}：{projection.activeConversation?.title || t("自动", "Auto")}
                                            </Text>
                                        </View>
                                        {projection.activeScopeTags.map((tag) => (
                                            <View key={tag} style={[styles.contextChip, { backgroundColor: palette.surface, borderColor: palette.border }]}>
                                                <Text style={[styles.contextChipText, { color: palette.textMuted }]}>{tag}</Text>
                                            </View>
                                        ))}
                                    </View>
                                </GlassCard>
                            ) : null}
                        </View>

                        <ChatWindow
                            adminBaseUrl={adminBaseUrl}
                            messages={projection.projectedMessages}
                            refreshing={conversationBusy}
                            onRefresh={() => {
                                if (activeConversationId) {
                                    void loadConversation(activeConversationId, { force: true });
                                }
                            }}
                            onDeleteMessage={handleDeleteMessage}
                            speakingKey={speakingId}
                            onSpeakVoice={handleSpeakVoice}
                            userImageUri={profileImageUri || ""}
                            userDisplayName={user?.name || user?.login || user?.email || ""}
                            todos={projection.todos}
                            artifacts={projection.artifacts}
                            pendingApproval={projection.pendingApproval}
                            pendingApprovalCount={projection.pendingApprovalCount}
                            approvalBusy={sending}
                            onResolveApproval={handleApprovalResolve}
                            onOpenApprovalPanel={openApprovalPanel}
                            isLandscape={isLandscape}
                        />

                        <View style={[styles.composerWrap, isLandscape && styles.composerWrapLandscape]}>
                            <Composer
                                value={input}
                                onChange={setInput}
                                onSend={() => void handleSend()}
                                busy={sending}
                                selectedCommand={selectedCommand}
                                onSelectCommand={(command) => {
                                    setSelectedCommand(command);
                                    setInput("");
                                }}
                                onClearCommand={() => setSelectedCommand(null)}
                                selectedSkills={selectedSkills}
                                onAddSkill={(skill) => setSelectedSkills((current) => [...current, skill])}
                                onRemoveSkill={(skill) => setSelectedSkills((current) =>
                                    current.filter((item) => `${item.name}:${item.path || ""}` !== `${skill.name}:${skill.path || ""}`),
                                )}
                                taskPlanningMode={taskPlanningMode}
                                onToggleTaskPlanningMode={() => setTaskPlanningMode((current) => !current)}
                                uploadedFiles={uploadedFiles}
                                onRemoveUploadedFile={(file) => setUploadedFiles((current) => current.filter((item) => item !== file))}
                                onPickAttachment={() => void handlePickAttachment()}
                                onToggleRecording={() => void handleToggleRecording()}
                                attachmentBusy={attachmentBusy}
                                recording={recorderState.isRecording}
                                transcribing={transcribing}
                                commands={commands}
                                skills={skills}
                            />
                        </View>
                    </View>
                </KeyboardAvoidingView>

                <RuntimeTimelinePanel
                    visible={runtimePanelOpen}
                    items={projection.runtimeStageModel.items}
                    selectedRuntimeId={projection.selectedRuntimeId}
                    selectedRuntimeDockItem={projection.selectedRuntimeDockItem}
                    activities={projection.selectedRuntimeActivities}
                    currentRunLabel={projection.currentRunLabel}
                    currentStepTitle={projection.currentStepTitle}
                    onClose={() => setRuntimePanelOpen(false)}
                    onSelectRuntime={setSelectedRuntimeId}
                />

                <Modal visible={desktopPreviewOpen} transparent animationType="fade" onRequestClose={() => void closeDesktopPreview()}>
                    <View style={[styles.previewOverlay, { backgroundColor: palette.overlay }]}>
                        <Pressable style={StyleSheet.absoluteFill} onPress={() => void closeDesktopPreview()} />
                        <Pressable
                            style={[styles.previewCloseButton, { backgroundColor: palette.surfaceStrong, borderColor: palette.border }]}
                            onPress={() => void closeDesktopPreview()}
                        >
                            <MaterialCommunityIcons name="close" size={22} color={palette.text} />
                        </Pressable>

                        <View style={[styles.previewCard, { backgroundColor: themeMode === "dark" ? "#020617" : "#000000" }]}>
                            <WebView
                                ref={desktopPreviewWebViewRef}
                                originWhitelist={["*"]}
                                source={{ html: desktopPreviewHtml }}
                                style={styles.previewWebview}
                                allowsInlineMediaPlayback
                                mediaPlaybackRequiresUserAction={false}
                                setSupportMultipleWindows={false}
                                javaScriptEnabled
                                onMessage={(event) => void handleDesktopPreviewMessage(event)}
                                onError={() => {
                                    setDesktopPreviewState("error");
                                    setDesktopPreviewBusy(false);
                                    setDesktopPreviewError(t("桌面预览尚未就绪，请稍后重试", "Desktop preview is not ready yet. Please retry shortly."));
                                }}
                            />
                            {(desktopPreviewBusy || desktopPreviewState === "loading" || (!desktopPreviewError && desktopPreviewState !== "preview")) ? (
                                <View style={styles.previewLoadingWrap}>
                                    <ActivityIndicator color="#FFFFFF" />
                                    <Text style={styles.previewLoadingText}>{t("正在建立桌面流连接…", "Connecting desktop stream...")}</Text>
                                </View>
                            ) : null}
                            {desktopPreviewError ? (
                                <View style={styles.previewLoadingWrap}>
                                    <Text style={styles.previewErrorText}>{desktopPreviewError}</Text>
                                </View>
                            ) : null}
                        </View>
                    </View>
                </Modal>

                <HistoryDrawer
                    visible={historyOpen}
                    items={conversations}
                    groups={projection.sidebarGroups}
                    activeConversationId={activeConversationId}
                    adminBaseUrl={adminBaseUrl}
                    musicTracks={musicTracks}
                    loading={loading}
                    onClose={() => setHistoryOpen(false)}
                    onSelectConversation={(item) => void handleSelectConversation(item)}
                    onNewConversation={() => void handleNewConversation()}
                    onDeleteConversation={(item) => handleDeleteConversation(item)}
                />
            </SafeAreaView>
        </LinearGradient>
    );
}

const styles = StyleSheet.create({
    gradient: { flex: 1 },
    safeArea: { flex: 1 },
    chatShell: { flex: 1 },
    chatStage: {
        flex: 1,
        position: "relative",
    },
    chatStageLandscape: {
        alignSelf: "center",
        width: "100%",
        maxWidth: 980,
    },
    historyFab: {
        position: "absolute",
        top: 10,
        left: 12,
        zIndex: 30,
        width: 40,
        height: 40,
        borderRadius: 20,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(255,255,255,0.92)",
        borderWidth: 1,
        borderColor: "rgba(148,163,184,0.16)",
        shadowColor: "#0F172A",
        shadowOpacity: 0.06,
        shadowRadius: 10,
        shadowOffset: { width: 0, height: 4 },
        elevation: 2,
    },
    historyFabLandscape: {
        top: 12,
        left: 18,
    },
    controlRail: {
        position: "absolute",
        top: 8,
        left: 58,
        right: 12,
        zIndex: 20,
        gap: 6,
    },
    controlRailLandscape: {
        top: 12,
        left: 72,
        right: 18,
    },
    controlRailTopRow: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "flex-start",
        gap: 6,
        minWidth: 0,
    },
    controlRailPrimary: {
        minHeight: 36,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        flexShrink: 1,
        minWidth: 0,
        borderRadius: radii.pill,
        paddingHorizontal: 4,
        paddingVertical: 3,
        borderWidth: 1,
        shadowColor: "#0F172A",
        shadowOpacity: 0.03,
        shadowRadius: 10,
        shadowOffset: { width: 0, height: 6 },
        elevation: 2,
    },
    contextButton: {
        width: 28,
        height: 28,
        borderRadius: 14,
        alignItems: "center",
        justifyContent: "center",
        borderWidth: 1,
    },
    runControlWrap: {
        minHeight: 36,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 4,
        paddingVertical: 2,
        borderRadius: radii.pill,
        borderWidth: 1,
        width: 144,
        minWidth: 144,
        maxWidth: 144,
        flexGrow: 0,
        flexShrink: 0,
        overflow: "visible",
        zIndex: 4,
    },
    statusPill: {
        minHeight: 22,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        paddingHorizontal: 8,
        paddingVertical: 2,
        borderRadius: radii.pill,
        borderWidth: 1,
    },
    statusDot: {
        width: 8,
        height: 8,
        borderRadius: 4,
    },
    statusPillText: {
        fontSize: 10,
        fontWeight: "700",
    },
    runIdText: {
        flexShrink: 1,
        fontSize: 10,
        fontWeight: "600",
    },
    messagesContent: {
        width: "100%",
        maxWidth: 760,
        alignSelf: "center",
        paddingHorizontal: 14,
        paddingTop: 64,
        paddingBottom: 172,
    },
    emptyState: {
        minHeight: 340,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 28,
        gap: 8,
    },
    emptyStateTitle: {
        fontSize: 30,
        fontWeight: "900",
        letterSpacing: -0.8,
        textAlign: "center",
    },
    composerWrap: {
        position: "absolute",
        left: 10,
        right: 10,
        bottom: Platform.OS === "ios" ? 8 : 4,
        zIndex: 22,
    },
    composerWrapLandscape: {
        left: 18,
        right: 18,
    },
    panelOverlay: {
        flex: 1,
        justifyContent: "center",
        alignItems: "center",
        paddingHorizontal: 14,
        paddingVertical: 24,
    },
    runtimePanelCard: {
        width: "100%",
        maxWidth: 420,
        maxHeight: "84%",
        borderRadius: 24,
        borderWidth: 1,
        overflow: "hidden",
        shadowColor: "#0F172A",
        shadowOpacity: 0.16,
        shadowRadius: 22,
        shadowOffset: { width: 0, height: 14 },
        elevation: 8,
    },
    runtimePanelHeader: {
        paddingHorizontal: 16,
        paddingVertical: 11,
        borderBottomWidth: StyleSheet.hairlineWidth,
        flexDirection: "row",
        alignItems: "flex-start",
        justifyContent: "space-between",
        gap: 12,
    },
    runtimePanelHeaderMain: {
        flexDirection: "row",
        alignItems: "center",
        gap: 12,
        flex: 1,
    },
    runtimePanelHero: {
        width: 36,
        height: 36,
        borderRadius: 18,
        alignItems: "center",
        justifyContent: "center",
        borderWidth: 1,
    },
    runtimePanelHeaderBody: {
        flex: 1,
        gap: 2,
    },
    runtimePanelTitle: {
        fontSize: 15,
        fontWeight: "900",
        letterSpacing: -0.3,
    },
    runtimePanelSubtitle: {
        fontSize: 11,
        lineHeight: 16,
    },
    runtimePanelCloseButton: {
        width: 32,
        height: 32,
        borderRadius: 16,
        alignItems: "center",
        justifyContent: "center",
        borderWidth: 1,
    },
    runtimeTabsRow: {
        flexDirection: "row",
        gap: 5,
        paddingHorizontal: 16,
        paddingVertical: 9,
    },
    runtimeTabButton: {
        width: 30,
        height: 30,
        borderRadius: 15,
        alignItems: "center",
        justifyContent: "center",
        borderWidth: 1,
    },
    runtimePanelScroll: {
        flex: 1,
    },
    runtimePanelContent: {
        gap: 10,
        paddingHorizontal: 16,
        paddingTop: 2,
        paddingBottom: 18,
    },
    runtimeEventCard: {
        borderRadius: 20,
        borderWidth: 1,
        paddingHorizontal: 14,
        paddingVertical: 12,
        gap: 9,
    },
    runtimeEventMetaRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
    },
    runtimeEventKindPill: {
        minHeight: 22,
        flexDirection: "row",
        alignItems: "center",
        paddingHorizontal: 10,
        borderRadius: radii.pill,
    },
    runtimeEventKindText: {
        fontSize: 10,
        fontWeight: "800",
    },
    runtimeEventActor: {
        marginLeft: "auto",
        fontSize: 11,
        fontWeight: "600",
    },
    runtimeEventSummary: {
        fontSize: 13,
        fontWeight: "700",
        lineHeight: 19,
    },
    runtimeEventBody: {
        borderRadius: 16,
        borderWidth: 1,
        paddingHorizontal: 11,
        paddingVertical: 9,
    },
    runtimeEventTopic: {
        fontSize: 12,
        lineHeight: 18,
    },
    runtimeEmptyState: {
        borderRadius: 20,
        borderWidth: 1,
        borderStyle: "dashed",
        paddingHorizontal: 16,
        paddingVertical: 22,
    },
    runtimeEmptyStateText: {
        fontSize: 13,
        lineHeight: 20,
        textAlign: "center",
    },
    contextCard: {
        borderRadius: 18,
        borderWidth: 1,
        paddingHorizontal: 14,
        paddingVertical: 12,
    },
    contextTitle: {
        fontSize: 13,
        fontWeight: "800",
    },
    contextSubtitle: {
        marginTop: 4,
        fontSize: 12,
        lineHeight: 18,
    },
    contextChips: {
        flexDirection: "row",
        flexWrap: "wrap",
        gap: 8,
        marginTop: 10,
    },
    contextChip: {
        minHeight: 24,
        flexDirection: "row",
        alignItems: "center",
        paddingHorizontal: 10,
        paddingVertical: 4,
        borderRadius: radii.pill,
        borderWidth: 1,
    },
    contextChipText: {
        fontSize: 11,
        fontWeight: "700",
    },
    previewOverlay: {
        flex: 1,
        justifyContent: "center",
        alignItems: "center",
        paddingHorizontal: 18,
    },
    previewCloseButton: {
        position: "absolute",
        top: 52,
        right: 18,
        zIndex: 4,
        width: 48,
        height: 48,
        borderRadius: 24,
        alignItems: "center",
        justifyContent: "center",
        borderWidth: 1,
        shadowColor: "#0F172A",
        shadowOpacity: 0.12,
        shadowRadius: 16,
        shadowOffset: { width: 0, height: 8 },
        elevation: 6,
    },
    previewCard: {
        width: "100%",
        maxWidth: 820,
        minHeight: 208,
        borderRadius: 28,
        overflow: "hidden",
        shadowColor: "#000000",
        shadowOpacity: 0.24,
        shadowRadius: 24,
        shadowOffset: { width: 0, height: 14 },
        elevation: 10,
    },
    previewLoadingWrap: {
        minHeight: 210,
        alignItems: "center",
        justifyContent: "center",
        gap: 12,
        paddingHorizontal: 24,
    },
    previewLoadingText: {
        color: "#FFFFFF",
        fontSize: 18,
        fontWeight: "700",
        textAlign: "center",
    },
    previewErrorText: {
        color: "#F8FAFC",
        fontSize: 15,
        lineHeight: 22,
        textAlign: "center",
    },
    previewWebview: {
        width: "100%",
        minHeight: 210,
        backgroundColor: "#000000",
    },
});
