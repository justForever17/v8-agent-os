import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    ActivityIndicator,
    Alert,
    Modal,
    Platform,
    Pressable,
    ScrollView,
    StyleSheet,
    Text,
    TextInput,
    View,
    useWindowDimensions,
} from "react-native";
import { Redirect, router, useLocalSearchParams, type Href } from "expo-router";
import { SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import * as DocumentPicker from "expo-document-picker";
import * as FileSystem from "expo-file-system/legacy";
import { WebView, type WebViewMessageEvent } from "react-native-webview";
import { KeyboardStickyView } from "react-native-keyboard-controller";
import {
    RecordingPresets,
    requestRecordingPermissionsAsync,
    setAudioModeAsync,
    useAudioPlayer,
    useAudioPlayerStatus,
    useAudioRecorder,
    useAudioRecorderState,
} from "expo-audio";
import { createVideoPlayer, type SourceLoadEventPayload, type VideoPlayer } from "expo-video";
import { getThumbnailAsync } from "expo-video-thumbnails";

import { ChatWindow } from "@/src/components/chat/ChatWindow";
import { Composer } from "@/src/components/chat/Composer";
import { ComposerPickerOverlay } from "@/src/components/chat/ComposerPickerOverlay";
import { EdgeActionRail } from "@/src/components/chat/EdgeActionRail";
import { GovernanceApprovalModal } from "@/src/components/chat/GovernanceApprovalModal";
import { ProcessesHUD } from "@/src/components/chat/ProcessesHUD";
import { RunControlBar } from "@/src/components/chat/RunControlBar";
import { RuntimeDock } from "@/src/components/chat/RuntimeDock";
import { RuntimeTimelinePanel } from "@/src/components/chat/RuntimeTimelinePanel";
import { TodosHUD } from "@/src/components/chat/TodosHUD";
import { WorkspaceFolderExplorer } from "@/src/components/chat/WorkspaceFolderExplorer";
import { GlassCard } from "@/src/components/common/GlassCard";
import { LoadingScreen } from "@/src/components/common/LoadingScreen";
import { HistoryDrawer } from "@/src/components/layout/HistoryDrawer";
import { PhoneTopbar, type PhoneTopbarAction } from "@/src/components/layout/PhoneTopbar";
import { resolveAdminAssetUrl } from "@/src/lib/admin-client";
import { buildPhoneChatProjection } from "@/src/lib/chat-projection";
import { normalizeMessagesForState, upsertApproval } from "@/src/lib/chat-state";
import {
    buildAssistantMessage,
    isActiveAssistantStreamPhase,
    PHONE_STREAM_LIFECYCLE_OPTIONS,
    type PhoneRealtimeUiEvent,
} from "@/src/lib/chat-stream-state";
import { buildApprovalFromEvent, buildAskUserInteractionFromEvent, normalizePhoneRealtimeEvent } from "@/src/lib/chat-realtime";
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
import { createTranslator, translateCurrent } from "@/src/lib/locale";
import { getDayGreeting } from "@/src/lib/time";
import {
    approvePendingItem,
    createDesktopLiveOffer,
    prepareDesktopLive,
    createDesktopLiveSession,
    createConversation,
    createProject,
    createWorkspaceFolder,
    deleteConversation,
    deleteMessage,
    dispatchRunCommand,
    getDesktopLiveStatus,
    getDesktopLiveStreamUrl,
    getConversationDetail,
    getProjectsRegistry,
    getRealtimeSnapshot,
    getSessionProcesses,
    getSessionScope,
    listWorkspaceFolders,
    listCommandPresets,
    listConversations,
    listSkillsAndSubagentFamilies,
    requestTextToSpeech,
    respondAskUser,
    releaseDesktopLiveSession,
    cancelQueuedChatMessage,
    promoteQueuedChatMessage,
    updateQueuedChatMessage,
    submitChatMessage,
    sendDesktopLiveCandidate,
    speechToText,
    streamRealtimeSession,
    uploadAttachment,
} from "@/src/lib/phone-api";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type {
    ChatArtifact,
    ChatMessage,
    CommandPresetSummary,
    ConversationDetail,
    ConversationSummary,
    AskUserInteraction,
    PendingApproval,
    PhoneUiTimelineNode,
    DesktopLiveStatus,
    RealtimeSessionSnapshot,
    ProjectSummary,
    ScopeBindingView,
    WorkspaceFolderNode,
    SessionTodoItem,
    SkillReferenceSummary,
    ContextMentionSummary,
    SubagentFamilySummary,
    UploadedWorkspaceFile,
    DesktopLiveSessionPayload,
    QueuedChatMessage,
} from "@/src/types/admin";
import {
    createInitialSessionRealtimeMessageState,
    type AdminProcessRef,
    type ContextGovernanceView,
    type ContextReferenceItem,
    deriveAuthoritativeSessionView,
    flushQueuedSessionRealtimeRuntimeEvents,
    mergeTimelineNodesByIdentity,
    queueSessionRealtimeRuntimeEvent,
    shouldAuthoritativelyRefreshOnRuntimeEvent,
    syncSessionRealtimeMessageState,
    shouldApplyRuntimeEventToMessage,
} from "@v8/session-realtime";

const REPLY_POP_SOUND = require("../../assets/audio/message-pop.mp3");

type RuntimeSummary = {
    status: string;
    latestSeq: number;
    runId?: string;
    label?: string;
};

type ComposerMentionItem =
    | { kind: "skill"; key: string; skill: SkillReferenceSummary }
    | { kind: "subagent_family"; key: string; family: SubagentFamilySummary };

type WorkspaceBindingDraft =
    | { kind: "main" }
    | { kind: "project"; projectId: string };

function normalizeWorkspacePathForDisplay(value?: string | null) {
    return String(value || "").trim().replace(/\//g, "\\").replace(/\\+$/, "").toLowerCase();
}

function deriveWorkspaceLabelFromPath(value?: string | null) {
    const normalized = String(value || "").trim().replace(/\\+$/, "").replace(/\/+$/, "");
    if (!normalized) {
        return "";
    }
    const segments = normalized.split(/[\\/]/).filter(Boolean);
    return segments[segments.length - 1] || normalized;
}

function normalizeWorkspaceRootCandidate(value?: string | null, keyHint = "") {
    const raw = String(value || "").trim();
    if (!raw) {
        return "";
    }
    let decoded = raw;
    try {
        decoded = decodeURIComponent(raw);
    } catch {
        decoded = raw;
    }
    const normalized = decoded.replace(/\//g, "\\");
    const lowered = normalized.toLowerCase();
    const markerIndex = lowered.indexOf("\\.v8\\");
    if (markerIndex > 2) {
        return normalized.slice(0, markerIndex).replace(/\\+$/, "");
    }
    if (/\bworkspace_?path\b/i.test(keyHint) && /[A-Za-z]:\\|^\\\\/.test(normalized)) {
        return normalized.replace(/\\+$/, "");
    }
    return "";
}

function deriveWorkspacePathFromMessages(messages: ChatMessage[]) {
    const scanValue = (value: unknown, keyHint = "", depth = 0): string => {
        if (depth > 5 || value == null) {
            return "";
        }
        if (typeof value === "string") {
            return normalizeWorkspaceRootCandidate(value, keyHint);
        }
        if (Array.isArray(value)) {
            for (const item of value) {
                const found = scanValue(item, keyHint, depth + 1);
                if (found) return found;
            }
            return "";
        }
        if (typeof value === "object") {
            const record = value as Record<string, unknown>;
            const priorityKeys = [
                "workspacePath",
                "workspace_path",
                "sourcePath",
                "source_path",
                "path",
                "url",
                "publicUrl",
                "previewUrl",
                "externalUrl",
            ];
            for (const key of priorityKeys) {
                if (!(key in record)) continue;
                const found = scanValue(record[key], key, depth + 1);
                if (found) return found;
            }
            for (const [key, item] of Object.entries(record)) {
                const found = scanValue(item, key, depth + 1);
                if (found) return found;
            }
        }
        return "";
    };
    for (const message of messages) {
        const found = scanValue(message, "message", 0);
        if (found) {
            return found;
        }
    }
    return "";
}

function buildRuntimeTimelineEntry(
    runtimeId: PhoneRuntimeId,
    topic: string,
    summary: string,
    options?: {
        id?: string;
        seq?: number;
        timestamp?: number | string;
        actorLabel?: string;
        status?: string;
        kind?: PhoneRuntimeTimelineEntry["kind"];
        fallbackNowMs?: number;
    },
): PhoneRuntimeTimelineEntry {
    const fallbackNowMs = typeof options?.fallbackNowMs === "number" ? options.fallbackNowMs : Date.now();
    const timestamp = typeof options?.timestamp === "number"
        ? options.timestamp
        : typeof options?.timestamp === "string"
            ? Date.parse(options.timestamp) || fallbackNowMs
            : fallbackNowMs;
    return {
        id: String(options?.id || `${runtimeId}:${topic}:${timestamp}`).trim(),
        seq: Number(options?.seq || 0) || 0,
        kind: options?.kind || "progress",
        runtimeId,
        topic,
        summary,
        timestamp,
        actorLabel: options?.actorLabel,
        status: options?.status,
    };
}

function buildPhaseRuntimeTimelineEntry(
    runtimeId: PhoneRuntimeId,
    phaseKey: string,
    summary: string,
    options?: {
        runId?: string;
        seq?: number;
        timestamp?: number | string;
        actorLabel?: string;
        status?: string;
        topic?: string;
    },
) {
    const runIdentity = String(options?.runId || "active").trim() || "active";
    return buildRuntimeTimelineEntry(
        runtimeId,
        options?.topic || phaseKey,
        summary,
        {
            id: `phase:${runIdentity}:${phaseKey}`,
            seq: options?.seq,
            timestamp: options?.timestamp,
            actorLabel: options?.actorLabel,
            status: options?.status,
            kind: "progress",
        },
    );
}

function buildUserMessage(
    text: string,
    options: {
        command: CommandPresetSummary | null;
        skills: SkillReferenceSummary[];
        subagentFamilies: SubagentFamilySummary[];
        taskPlanningMode: boolean;
        files: UploadedWorkspaceFile[];
    },
    nowMs: number,
): ChatMessage {
    const now = nowMs;
    const metadata: ChatMessage["metadata"] = {};
    const attachments = buildUploadedFileAttachments(options.files);
    if (options.command) {
        metadata.commandPreset = { name: options.command.name };
    }
    if (options.skills.length > 0) {
        metadata.skillReferences = options.skills.map((skill) => ({ ...skill }));
    }
    if (options.skills.length > 0 || options.subagentFamilies.length > 0) {
        metadata.contextMentions = [
            ...options.skills.map((skill) => ({
                kind: "skill" as const,
                name: skill.name,
                label: skill.name,
                description: skill.description,
                path: skill.path,
                sourceType: "explicit_mention",
            })),
            ...options.subagentFamilies.map((family) => ({
                kind: "subagent_family" as const,
                id: family.familyId,
                familyId: family.familyId,
                name: family.displayName || family.familyId,
                label: family.displayName || family.familyId,
                description: family.description,
                sourceType: "explicit_mention",
            })),
        ];
        metadata.explicitSubagentFamilies = options.subagentFamilies.map((family) => family.familyId);
    }
    if (options.taskPlanningMode) {
        metadata.taskPlanningMode = true;
        metadata.taskPlanningSource = "composer";
        metadata.taskPlanningRequestedByComposer = true;
    }
    if (attachments.length > 0) {
        metadata.attachments = attachments;
    }
    metadata.clientMessageId = `user-${now}`;

    return {
        id: metadata.clientMessageId as string,
        role: "user",
        content: text || (
            attachments.length === 1
                ? translateCurrent("shared.upload.uploaded_single")
                : attachments.length > 1
                    ? translateCurrent("shared.upload.uploaded_count", { count: attachments.length })
                    : ""
        ),
        timestamp: now,
        images: options.files
            .map((file) => file.url || file.publicUrl || "")
            .filter(Boolean),
        artifacts: [],
        metadata: Object.keys(metadata).length > 0 ? metadata : undefined,
    };
}

function getUploadedFilePreviewKind(name?: string, mimeType?: string): UploadedWorkspaceFile["previewKind"] {
    const filename = String(name || "").toLowerCase();
    const type = String(mimeType || "").toLowerCase();
    if (type.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp|heic|heif)$/i.test(filename)) {
        return "image";
    }
    if (type.startsWith("video/") || /\.(mp4|mov|m4v|webm|mkv|avi)$/i.test(filename)) {
        return "video";
    }
    return "file";
}

function formatVideoDurationLabel(durationSeconds: number) {
    const safe = Math.max(0, Math.round(durationSeconds));
    const hours = Math.floor(safe / 3600);
    const minutes = Math.floor((safe % 3600) / 60);
    const seconds = safe % 60;
    if (hours > 0) {
        return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
    }
    return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function sanitizeUploadCacheName(value: string) {
    return value.replace(/[<>:"/\\|?*\u0000-\u001F]+/g, "_").replace(/\s+/g, "_").slice(0, 96);
}

async function readLocalVideoDurationSeconds(uri: string) {
    if (!uri || Platform.OS === "web") {
        return undefined;
    }
    const player: VideoPlayer = createVideoPlayer(uri);
    try {
        return await new Promise<number | undefined>((resolve) => {
            let settled = false;
            const settle = (duration?: number) => {
                if (settled) {
                    return;
                }
                settled = true;
                sourceLoadSub.remove();
                statusSub.remove();
                clearTimeout(timeoutId);
                resolve(typeof duration === "number" && Number.isFinite(duration) && duration > 0 ? duration : undefined);
            };

            const sourceLoadSub = player.addListener("sourceLoad", (payload: SourceLoadEventPayload) => {
                settle(payload.duration);
            });
            const statusSub = player.addListener("statusChange", () => {
                if (player.duration > 0) {
                    settle(player.duration);
                }
            });
            const timeoutId = setTimeout(() => settle(player.duration), 2200);

            if (player.duration > 0) {
                settle(player.duration);
            }
        });
    } catch {
        return undefined;
    } finally {
        player.release();
    }
}

async function normalizeUploadAssetUri(asset: DocumentPicker.DocumentPickerAsset) {
    const uri = String(asset.uri || "").trim();
    if (!uri || Platform.OS === "web" || uri.startsWith("file://")) {
        return asset;
    }
    const root = FileSystem.cacheDirectory || FileSystem.documentDirectory;
    if (!root) {
        return asset;
    }
    const safeName = sanitizeUploadCacheName(asset.name || `upload-${Date.now()}`);
    const folder = `${root}v8-agent-os/uploads/`;
    const nonce = Math.random().toString(36).slice(2, 8);
    const target = `${folder}${Date.now()}-${nonce}-${safeName}`;
    try {
        await FileSystem.makeDirectoryAsync(folder, { intermediates: true }).catch(() => undefined);
        await FileSystem.copyAsync({ from: uri, to: target });
        return {
            ...asset,
            uri: target,
        };
    } catch {
        return asset;
    }
}

async function buildLocalUploadedFileDraft(
    asset: DocumentPicker.DocumentPickerAsset,
    uploaded: UploadedWorkspaceFile,
    localId: string,
): Promise<UploadedWorkspaceFile> {
    const previewKind = getUploadedFilePreviewKind(asset.name, asset.mimeType || uploaded.type);
    const draft: UploadedWorkspaceFile = {
        ...uploaded,
        localId,
        localUri: asset.uri,
        previewKind,
    };

    if (previewKind === "image") {
        draft.previewUri = asset.uri;
        return draft;
    }
    if (previewKind === "video") {
        draft.localUri = asset.uri;
        const [thumbnailResult, durationSeconds] = await Promise.allSettled([
            getThumbnailAsync(asset.uri, { time: 0, quality: 0.72 }),
            readLocalVideoDurationSeconds(asset.uri),
        ]);
        if (thumbnailResult.status === "fulfilled" && thumbnailResult.value?.uri) {
            draft.previewUri = thumbnailResult.value.uri;
        }
        if (durationSeconds.status === "fulfilled" && durationSeconds.value) {
            draft.durationLabel = formatVideoDurationLabel(durationSeconds.value);
        }
        return draft;
    }
    return draft;
}

function buildUploadTransportError(asset: DocumentPicker.DocumentPickerAsset, error: unknown, adminBaseUrl?: string | null) {
    const rawMessage = error instanceof Error ? String(error.message || "").trim() : "";
    const lowered = rawMessage.toLowerCase();
    const label = asset.name ? `“${asset.name}”` : translateCurrent("shared.upload.file_fallback_label");
    const adminHint = adminBaseUrl ? `Admin: ${adminBaseUrl}` : translateCurrent("shared.upload.admin_url_missing");
    const networkHint = translateCurrent("shared.upload.admin_transport_hint", {
        reason: rawMessage || translateCurrent("shared.upload.admin_proxy_unreachable"),
        adminHint,
    });
    if (lowered.includes("network request failed") || lowered.includes("upload body") || rawMessage.includes(translateCurrent("shared.upload.body_send_failed_marker"))) {
        return new Error(translateCurrent("shared.upload.transport_failed_with_reason", { label, reason: networkHint }));
    }
    if (lowered.includes("failed to fetch") || lowered.includes("fetch failed") || rawMessage.includes(translateCurrent("shared.upload.transport_marker"))) {
        return new Error(translateCurrent("shared.upload.transport_failed_with_reason", { label, reason: networkHint }));
    }
    if (rawMessage.toLowerCase().includes("unable to reach admin") || rawMessage.includes("Admin")) {
        return new Error(translateCurrent("shared.upload.transport_failed_with_reason", { label, reason: networkHint }));
    }
    return error instanceof Error ? error : new Error(translateCurrent("shared.upload.generic_failed", { label }));
}

const zhNewChatPlaceholder = createTranslator("zh-CN")("src.screens.sessionsscreen.new_chat").trim().toLowerCase();
const legacyZhNewChatPlaceholder = zhNewChatPlaceholder.replace(/\u5efa/g, "");

function normalizeAcceptedUserMessage(raw: unknown, fallback: ChatMessage): ChatMessage | null {
    if (!raw || typeof raw !== "object") {
        return null;
    }
    const record = raw as Record<string, unknown>;
    const metadata = record.metadata && typeof record.metadata === "object"
        ? record.metadata as Record<string, unknown>
        : {};
    const clientMessageId = String(
        record.clientMessageId
        || record.client_message_id
        || metadata.clientMessageId
        || metadata.client_message_id
        || fallback.metadata?.clientMessageId
        || fallback.id
        || ""
    ).trim();
    const attachments = Array.isArray(metadata.attachments) ? metadata.attachments as Array<Record<string, unknown>> : [];
    const images = attachments
        .map((item) => String(item.publicUrl || item.url || "").trim())
        .filter(Boolean);
    const nodes = Array.isArray(record.nodes) ? record.nodes as PhoneUiTimelineNode[] : fallback.nodes || [];
    const acceptedTimestamp = typeof record.timestamp === "number"
        ? record.timestamp
        : Date.parse(String(record.created_at || record.createdAt || record.timestamp || ""));
    return {
        ...fallback,
        id: String(record.id || fallback.id),
        role: record.role === "user" ? "user" : fallback.role,
        runId: String(record.run_id || record.runId || fallback.runId || ""),
        content: String(record.content_text || record.content || fallback.content || ""),
        timestamp: Number.isFinite(acceptedTimestamp) && acceptedTimestamp > 0 ? acceptedTimestamp : fallback.timestamp,
        nodes,
        images: images.length > 0 ? images : fallback.images,
        artifacts: Array.isArray(record.artifacts) ? record.artifacts as ChatArtifact[] : fallback.artifacts,
        metadata: {
            ...(fallback.metadata || {}),
            ...metadata,
            ...(clientMessageId ? { clientMessageId } : {}),
            transcriptVersion: Number(record.version || metadata.transcriptVersion || 0) || undefined,
        },
    };
}

function buildUploadedFileAttachments(files: UploadedWorkspaceFile[]) {
    return files
        .map((file) => {
            const url = String(file.url || file.publicUrl || "").trim();
            const workspacePath = String(file.workspacePath || file.path || "").trim();
            if (!url && !workspacePath) return null;
            return {
                id: file.id || file.localId,
                name: file.name,
                url: url || undefined,
                publicUrl: String(file.publicUrl || file.url || "").trim() || undefined,
                workspacePath: workspacePath || undefined,
                workspaceRelativePath: file.workspaceRelativePath,
                resourceRef: file.resourceRef || undefined,
                mimeType: file.type,
                size: file.size,
                source: "os_phone_upload",
            };
        })
        .filter((item): item is NonNullable<typeof item> => Boolean(item));
}

function buildUploadedFileStableKey(file: UploadedWorkspaceFile) {
    return String(
        file.localId
        || file.id
        || file.url
        || file.publicUrl
        || file.workspacePath
        || file.workspaceRelativePath
        || file.path
        || `${file.name || "file"}:${file.createdAt || ""}`,
    ).trim();
}

function mergeUploadedWorkspaceFiles(
    current: UploadedWorkspaceFile[],
    incoming: UploadedWorkspaceFile[],
) {
    const merged = new Map<string, UploadedWorkspaceFile>();
    [...current, ...incoming].forEach((file) => {
        const key = buildUploadedFileStableKey(file);
        if (!key) {
            return;
        }
        merged.set(key, {
            ...merged.get(key),
            ...file,
        });
    });
    return Array.from(merged.values());
}

function removeUploadedWorkspaceFile(
    current: UploadedWorkspaceFile[],
    target: UploadedWorkspaceFile,
) {
    const targetKey = buildUploadedFileStableKey(target);
    return current.filter((item) => buildUploadedFileStableKey(item) !== targetKey);
}

function buildAssistantPlaceholder(runId?: string): ChatMessage {
    return buildAssistantMessage({
        agentName: translateCurrent("shared.actor.supervisor"),
        agentAvatar: "/brand-mark.png",
        agentRoleLabel: translateCurrent("shared.actor.lead"),
    }, runId, "placeholder");
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

function mergeTimelineNodes(base: PhoneUiTimelineNode[] = [], incoming: PhoneUiTimelineNode[] = []) {
    return mergeTimelineNodesByIdentity(base, incoming) as PhoneUiTimelineNode[];
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

function summarizeRuntime(snapshot: RealtimeSessionSnapshot | null): RuntimeSummary {
    return {
        status: String(snapshot?.runtimeStatus || snapshot?.currentRun?.status || "idle"),
        latestSeq: Number(snapshot?.latestSeq || 0),
        runId: snapshot?.currentRun?.id,
    };
}

const REALTIME_SNAPSHOT_FALLBACK_GRACE_MS = 8000;
const REALTIME_SNAPSHOT_FALLBACK_DEBOUNCE_MS = 2400;
const REALTIME_SNAPSHOT_FALLBACK_FORCE_DEBOUNCE_MS = 900;
const TODO_TOOL_NAMES = new Set(["write_todos", "update_todo"]);

type AssistantTaskProgressPatch = {
    phase?: NonNullable<ChatMessage["uiStreamPhase"]>;
    label?: string;
    subtitle?: string;
    currentStep?: string;
    completedCount?: number;
    totalCount?: number;
};

function normalizeTodoStatus(value: unknown) {
    const normalized = String(value || "").trim().toLowerCase();
    return normalized === "done" || normalized === "in_progress" || normalized === "skipped"
        ? normalized
        : "pending";
}

function extractTodoText(value: unknown) {
    if (typeof value === "string") {
        return value.trim();
    }
    if (!value || typeof value !== "object") {
        return "";
    }
    const record = value as Record<string, unknown>;
    return String(record.content || record.text || record.title || "").trim();
}

function coerceSessionTodoItem(value: unknown, index: number): SessionTodoItem | null {
    const content = extractTodoText(value);
    if (!content) {
        return null;
    }
    const record = value && typeof value === "object" ? value as Record<string, unknown> : {};
    return {
        id: typeof record.id === "string" ? record.id : `todo-${index}`,
        content,
        status: normalizeTodoStatus(record.status),
    };
}

function normalizeTaskTodos(value: unknown): SessionTodoItem[] {
    if (!Array.isArray(value)) {
        return [];
    }
    return value
        .map((item, index) => coerceSessionTodoItem(item, index))
        .filter((item): item is SessionTodoItem => Boolean(item));
}

function buildAssistantTaskProgressPatch(
    todos: SessionTodoItem[],
    options?: {
        phase?: AssistantTaskProgressPatch["phase"];
        label?: string;
        subtitle?: string;
    },
): AssistantTaskProgressPatch | null {
    const normalizedTodos = todos.filter((item) => String(item.content || "").trim());
    const totalCount = normalizedTodos.length;
    if (totalCount === 0 && !options?.label && !options?.subtitle) {
        return null;
    }

    const completedCount = normalizedTodos.filter((item) => normalizeTodoStatus(item.status) === "done").length;
    const activeIndex = normalizedTodos.findIndex((item) => normalizeTodoStatus(item.status) === "in_progress");
    const nextIndex = normalizedTodos.findIndex((item) => normalizeTodoStatus(item.status) === "pending");
    const activeTodo = activeIndex >= 0 ? normalizedTodos[activeIndex] : null;
    const nextTodo = nextIndex >= 0 ? normalizedTodos[nextIndex] : null;
    const currentStep = String(activeTodo?.content || nextTodo?.content || normalizedTodos[normalizedTodos.length - 1]?.content || "").trim();

    let phase = options?.phase;
    let label = options?.label;
    let subtitle = options?.subtitle;

    if (!phase) {
        if (completedCount >= totalCount && totalCount > 0) {
            phase = "settling";
        } else if (activeTodo) {
            phase = "tooling";
        } else if (nextTodo) {
            phase = completedCount > 0 ? "tooling" : "task_planning";
        } else {
            phase = "task_planning";
        }
    }

    if (!label) {
        if (phase === "waiting_input") {
            label = translateCurrent("src.screens.chatscreen.waiting_for_your_answer");
        } else if (phase === "artifact_ready") {
            label = translateCurrent("src.screens.chatscreen.artifact_ready");
        } else if (phase === "settling") {
            label = translateCurrent("src.screens.chatscreen.task_is_nearly_complete");
        } else if (activeTodo) {
            label = translateCurrent("src.screens.chatscreen.step_progress", { current: activeIndex + 1, total: Math.max(totalCount, 1) });
        } else if (nextTodo) {
            label = completedCount > 0
                ? translateCurrent("src.screens.chatscreen.preparing_step_progress", { current: nextIndex + 1, total: Math.max(totalCount, 1) })
                : translateCurrent("src.screens.chatscreen.planning_task");
        } else {
            label = translateCurrent("src.screens.chatscreen.planning_task");
        }
    }

    if (!subtitle) {
        if (phase === "artifact_ready") {
            subtitle = currentStep || (
                totalCount > 0
                    ? translateCurrent("src.screens.chatscreen.completed_step_progress", { completed: completedCount, total: totalCount })
                    : translateCurrent("src.screens.chatscreen.artifact_can_now_be_attached_to_the_workspace")
            );
        } else if (phase === "waiting_input") {
            subtitle = currentStep || translateCurrent("src.screens.chatscreen.please_provide_the_requested_input");
        } else if (activeTodo) {
            subtitle = currentStep;
        } else if (nextTodo) {
            subtitle = currentStep || translateCurrent("src.screens.chatscreen.generated_step_count", { total: totalCount });
        } else if (totalCount > 0) {
            subtitle = translateCurrent("src.screens.chatscreen.completed_step_progress", { completed: completedCount, total: totalCount });
        }
    }

    return {
        phase,
        label,
        subtitle,
        currentStep: currentStep || undefined,
        completedCount,
        totalCount,
    };
}

function readAssistantTaskProgress(message: ChatMessage | null | undefined) {
    return asRecord(message?.metadata?.assistantTaskProgress);
}

function countDefinedAssistantTaskProgressFields(value: Record<string, unknown>) {
    return [
        value.phase,
        value.label,
        value.subtitle,
        value.currentStep,
        value.completedCount,
        value.totalCount,
    ].filter((item) => item !== undefined && item !== null && String(item).trim() !== "").length;
}

function hasStructuredAssistantPayload(message: ChatMessage | null | undefined) {
    if (!message) {
        return false;
    }
    return Boolean(
        (message.nodes || []).length > 0
        || (message.artifacts || []).length > 0
        || (message.images || []).length > 0
        || countDefinedAssistantTaskProgressFields(readAssistantTaskProgress(message)) > 0,
    );
}

function shouldPreserveLocalAssistantMessage(message: ChatMessage | null | undefined) {
    if (!message || message.role !== "assistant") {
        return false;
    }
    return Boolean(
        isOptimisticLocalMessage(message)
        || isActiveAssistantStreamPhase(message.uiStreamPhase)
        || message.uiEphemeral,
    );
}

function findLatestAssistantShellIndex(messages: ChatMessage[], runId?: string) {
    const normalizedRunId = String(runId || "").trim();
    if (normalizedRunId) {
        for (let index = messages.length - 1; index >= 0; index -= 1) {
            const message = messages[index];
            if (message.role !== "assistant") {
                continue;
            }
            if (String(message.runId || message.metadata?.runId || "").trim() === normalizedRunId) {
                return index;
            }
        }
    }

    for (let index = messages.length - 1; index >= 0; index -= 1) {
        const message = messages[index];
        if (message.role !== "assistant") {
            continue;
        }
        if (message.uiEphemeral || isActiveAssistantStreamPhase(message.uiStreamPhase)) {
            return index;
        }
    }
    return -1;
}

function applyAssistantTaskProgressPatch(
    current: ChatMessage[],
    patch: AssistantTaskProgressPatch | null,
    runId?: string,
    options?: { createIfMissing?: boolean },
) {
    if (!patch) {
        return current;
    }

    const next = [...current];
    let targetIndex = findLatestAssistantShellIndex(next, runId);

    if (targetIndex < 0) {
        if (!options?.createIfMissing) {
            return current;
        }
        next.push(buildAssistantPlaceholder(runId));
        targetIndex = next.length - 1;
    }

    const target = next[targetIndex];
    const nextMetadata = {
        ...(target.metadata || {}),
        assistantTaskProgress: {
            phase: patch.phase,
            label: patch.label,
            subtitle: patch.subtitle,
            currentStep: patch.currentStep,
            completedCount: patch.completedCount,
            totalCount: patch.totalCount,
        },
    };

    next[targetIndex] = {
        ...target,
        runId: runId || target.runId,
        uiEphemeral: target.uiEphemeral !== false || !hasRenderableMessagePayload(target),
        uiStreamPhase: patch.phase || target.uiStreamPhase,
        metadata: nextMetadata,
        timestamp: Date.now(),
    };

    return normalizeMessagesForState(next);
}

function applyTodoToolEvent(
    currentTodos: SessionTodoItem[],
    event: PhoneRealtimeUiEvent,
): SessionTodoItem[] | null {
    const toolName = String(event.tool?.toolName || event.data?.toolName || event.data?.tool_name || "").trim();
    if (!TODO_TOOL_NAMES.has(toolName)) {
        return null;
    }

    const args = asRecord(event.tool?.args);
    if (toolName === "write_todos") {
        const nextTodos = normalizeTaskTodos(args.todos);
        return nextTodos.length > 0 ? nextTodos : currentTodos;
    }

    if (toolName === "update_todo") {
        const index = Number(args.index);
        const status = normalizeTodoStatus(args.status);
        if (!Number.isFinite(index) || index < 0 || currentTodos.length === 0) {
            return currentTodos;
        }
        return currentTodos.map((item, itemIndex) => {
            if (itemIndex !== index) {
                if (status === "in_progress" && normalizeTodoStatus(item.status) === "in_progress") {
                    return {
                        ...item,
                        status: "pending",
                    };
                }
                return item;
            }
            return {
                ...item,
                status,
            };
        });
    }

    return null;
}

function buildArtifactFingerprint(artifact: ChatArtifact) {
    return [
        String(artifact.id || artifact.artifactId || artifact.workspacePath || artifact.sourcePath || artifact.previewUrl || artifact.externalUrl || "").trim(),
        String(artifact.kind || "").trim(),
        String(artifact.title || "").trim(),
    ].join("::");
}

function buildMessagesFingerprint(messages: ChatMessage[]) {
    const safeStringify = (value: unknown) => {
        try {
            return JSON.stringify(value);
        } catch {
            return String(value ?? "");
        }
    };
    const buildNodeFingerprint = (node: PhoneUiTimelineNode) => {
        if (!node) {
            return "";
        }
        if (node.kind === "narrative") {
            return [
                node.kind,
                node.role,
                String(node.content || ""),
                String(node.agentName || ""),
            ].join("¦");
        }
        if (node.kind === "execution") {
            return [
                node.kind,
                node.executionType,
                String(node.toolCallId || ""),
                String(node.toolName || ""),
                String(node.topic || ""),
                String(node.label || ""),
                String(node.content || ""),
                safeStringify(node.result ?? node.data ?? node.args ?? null),
            ].join("¦");
        }
        if (node.kind === "governance") {
            return [
                node.kind,
                node.governanceType,
                String(node.approvalId || ""),
                String(node.question || node.reason || node.topic || ""),
                String(node.status || ""),
            ].join("¦");
        }
        if (node.kind === "artifact") {
            return [
                node.kind,
                String(node.artifact.id || ""),
                String(node.artifact.workspacePath || node.artifact.previewUrl || node.artifact.externalUrl || ""),
            ].join("¦");
        }
        return safeStringify(node);
    };

    return messages.map((message) => [
        (() => {
            const taskProgress = asRecord(message.metadata?.assistantTaskProgress);
            return [
                String(taskProgress.phase || ""),
                String(taskProgress.label || ""),
                String(taskProgress.subtitle || ""),
                String(taskProgress.currentStep || ""),
                String(taskProgress.completedCount || ""),
                String(taskProgress.totalCount || ""),
            ].join("¦");
        })(),
        String(message.id || "").trim(),
        String(message.renderKey || "").trim(),
        String(message.role || "").trim(),
        String(message.runId || "").trim(),
        String(message.content || ""),
        String(message.uiStreamPhase || ""),
        message.uiEphemeral ? "1" : "0",
        (message.images || []).join("|"),
        (message.artifacts || []).map(buildArtifactFingerprint).join("|"),
        Array.isArray(message.nodes) ? message.nodes.map(buildNodeFingerprint).join("§") : "0",
    ].join("¦")).join("¶");
}

function buildSnapshotSequence(payload: Partial<ConversationDetail | RealtimeSessionSnapshot | Record<string, unknown>> | null | undefined) {
    const root = asRecord(payload);
    const nestedSnapshot = asRecord(root.snapshot);
    return Number(root.latestSeq || root.latest_seq || nestedSnapshot.latest_seq || 0) || 0;
}

function isOptimisticLocalMessage(message: ChatMessage) {
    const id = String(message.id || "").trim();
    return id.startsWith("user-") || id.startsWith("assistant-");
}

function buildMessageComparisonKeys(message: ChatMessage) {
    const keys = new Set<string>();
    const id = String(message.id || "").trim();
    if (id) {
        keys.add(`id:${id}`);
    }

    const role = String(message.role || "").trim();
    const clientMessageId = String(message.metadata?.clientMessageId || message.metadata?.client_message_id || "").trim();
    if (clientMessageId) {
        keys.add(`client:${role}:${clientMessageId}`);
    }
    const runId = String(message.runId || message.metadata?.runId || "").trim();
    const normalizedContent = String(message.content || "").trim().replace(/\s+/g, " ");
    if (runId) {
        keys.add(`run:${role}:${runId}`);
        if (normalizedContent) {
            keys.add(`run-content:${role}:${runId}:${normalizedContent}`);
        }
    }

    if (normalizedContent) {
        keys.add(`content:${role}:${normalizedContent}`);
    }

    const artifactKeys = (message.artifacts || [])
        .map((artifact) => buildArtifactFingerprint(artifact))
        .filter(Boolean)
        .join("|");
    if (artifactKeys) {
        keys.add(`artifacts:${role}:${artifactKeys}`);
    }

    const taskProgress = readAssistantTaskProgress(message);
    const taskProgressKey = [
        String(taskProgress.phase || "").trim(),
        String(taskProgress.currentStep || "").trim(),
        String(taskProgress.label || "").trim(),
    ].filter(Boolean).join("|");
    if (taskProgressKey) {
        keys.add(`task:${role}:${taskProgressKey}`);
    }

    return Array.from(keys);
}

function hasRenderableMessagePayload(message: ChatMessage) {
    const metadata = message.metadata && typeof message.metadata === "object"
        ? message.metadata as Record<string, unknown>
        : {};
    const composerTaskPlanning = metadata.taskPlanningMode === true
        && (
            metadata.taskPlanningSource === "composer"
            || metadata.taskPlanningModeSource === "composer"
            || metadata.taskPlanningRequestedByComposer === true
        );
    return Boolean(
        String(message.content || "").trim()
        || (Array.isArray(message.images) && message.images.length > 0)
        || (Array.isArray(message.artifacts) && message.artifacts.length > 0)
        || (Array.isArray(message.nodes) && message.nodes.length > 0)
        || composerTaskPlanning
        || (metadata.commandPreset && typeof metadata.commandPreset === "object")
        || (Array.isArray(metadata.skillReferences) && metadata.skillReferences.length > 0)
        || (Array.isArray(metadata.attachments) && metadata.attachments.length > 0)
        || countDefinedAssistantTaskProgressFields(readAssistantTaskProgress(message)) > 0,
    );
}

function hasPreservableLocalAssistantState(messages: ChatMessage[]) {
    return messages.some((message) => shouldPreserveLocalAssistantMessage(message));
}

function describeLatestAssistantMessage(messages: ChatMessage[]) {
    const latestAssistant = [...messages].reverse().find((message) => message.role === "assistant");
    if (!latestAssistant) {
        return null;
    }
    return {
        id: String(latestAssistant.id || "").trim(),
        runId: String(latestAssistant.runId || latestAssistant.metadata?.runId || "").trim(),
        phase: String(latestAssistant.uiStreamPhase || "").trim(),
        nodes: Array.isArray(latestAssistant.nodes) ? latestAssistant.nodes.length : 0,
        artifacts: Array.isArray(latestAssistant.artifacts) ? latestAssistant.artifacts.length : 0,
        contentLength: String(latestAssistant.content || "").trim().length,
    };
}

function buildMessageRichness(message: ChatMessage | null | undefined) {
    if (!message) {
        return 0;
    }
    const metadata = message.metadata && typeof message.metadata === "object"
        ? message.metadata as Record<string, unknown>
        : {};
    const composerTaskPlanning = metadata.taskPlanningMode === true
        && (
            metadata.taskPlanningSource === "composer"
            || metadata.taskPlanningModeSource === "composer"
            || metadata.taskPlanningRequestedByComposer === true
        );
    return (
        String(message.content || "").trim().length
        + ((message.nodes || []).length * 120)
        + ((message.artifacts || []).length * 200)
        + ((message.images || []).length * 80)
        + (metadata.commandPreset ? 40 : 0)
        + (Array.isArray(metadata.skillReferences) ? metadata.skillReferences.length * 40 : 0)
        + (Array.isArray(metadata.contextMentions) ? metadata.contextMentions.length * 40 : 0)
        + (Array.isArray(metadata.attachments) ? metadata.attachments.length * 80 : 0)
        + (composerTaskPlanning ? 30 : 0)
    );
}

function mergeMessageImages(base: string[] = [], incoming: string[] = []) {
    return Array.from(new Set([...base, ...incoming].filter(Boolean)));
}

function mergeUserStructuredMetadata(
    snapshotMessage: ChatMessage,
    matchingLocal: ChatMessage,
    mergedMessage: ChatMessage,
) {
    if (matchingLocal.role !== "user") {
        return mergedMessage;
    }

    const localMetadata = matchingLocal.metadata && typeof matchingLocal.metadata === "object"
        ? matchingLocal.metadata as Record<string, unknown>
        : {};
    const snapshotMetadata = snapshotMessage.metadata && typeof snapshotMessage.metadata === "object"
        ? snapshotMessage.metadata as Record<string, unknown>
        : {};
    const preservedMetadata: Record<string, unknown> = {};

    if (!snapshotMetadata.commandPreset && localMetadata.commandPreset) {
        preservedMetadata.commandPreset = localMetadata.commandPreset;
    }
    if (
        (!Array.isArray(snapshotMetadata.skillReferences) || snapshotMetadata.skillReferences.length === 0)
        && Array.isArray(localMetadata.skillReferences)
        && localMetadata.skillReferences.length > 0
    ) {
        preservedMetadata.skillReferences = localMetadata.skillReferences;
    }
    if (
        (!Array.isArray(snapshotMetadata.contextMentions) || snapshotMetadata.contextMentions.length === 0)
        && Array.isArray(localMetadata.contextMentions)
        && localMetadata.contextMentions.length > 0
    ) {
        preservedMetadata.contextMentions = localMetadata.contextMentions;
    }
    if (
        (!Array.isArray(snapshotMetadata.explicitSubagentFamilies) || snapshotMetadata.explicitSubagentFamilies.length === 0)
        && Array.isArray(localMetadata.explicitSubagentFamilies)
        && localMetadata.explicitSubagentFamilies.length > 0
    ) {
        preservedMetadata.explicitSubagentFamilies = localMetadata.explicitSubagentFamilies;
    }
    if (snapshotMetadata.taskPlanningMode !== true && localMetadata.taskPlanningMode === true) {
        preservedMetadata.taskPlanningMode = true;
    }
    if (!snapshotMetadata.taskPlanningSource && localMetadata.taskPlanningSource) {
        preservedMetadata.taskPlanningSource = localMetadata.taskPlanningSource;
    }
    if (snapshotMetadata.taskPlanningRequestedByComposer !== true && localMetadata.taskPlanningRequestedByComposer === true) {
        preservedMetadata.taskPlanningRequestedByComposer = true;
    }
    if (
        (!Array.isArray(snapshotMetadata.attachments) || snapshotMetadata.attachments.length === 0)
        && Array.isArray(localMetadata.attachments)
        && localMetadata.attachments.length > 0
    ) {
        preservedMetadata.attachments = localMetadata.attachments;
    }
    if (!snapshotMetadata.clientMessageId && localMetadata.clientMessageId) {
        preservedMetadata.clientMessageId = localMetadata.clientMessageId;
    }

    if (Object.keys(preservedMetadata).length === 0) {
        return mergedMessage;
    }

    return {
        ...mergedMessage,
        metadata: {
            ...(mergedMessage.metadata || {}),
            ...preservedMetadata,
        },
        images: mergeMessageImages(matchingLocal.images || [], mergedMessage.images || []),
        artifacts: mergeArtifacts(matchingLocal.artifacts || [], mergedMessage.artifacts || []),
    };
}

function shouldPreserveAssistantPlaceholder(
    snapshotMessage: ChatMessage,
    matchingLocal: ChatMessage,
) {
    if (matchingLocal.role !== "assistant") {
        return false;
    }
    if (!matchingLocal.uiEphemeral && !isActiveAssistantStreamPhase(matchingLocal.uiStreamPhase)) {
        return false;
    }
    const snapshotRenderable = hasRenderableMessagePayload(snapshotMessage);
    return !snapshotRenderable;
}

function mergeAssistantPlaceholder(
    snapshotMessage: ChatMessage,
    matchingLocal: ChatMessage,
    mergedMessage: ChatMessage,
) {
    const localTaskProgress = readAssistantTaskProgress(matchingLocal);
    const nextMessage: ChatMessage = {
        ...mergedMessage,
        runId: matchingLocal.runId || mergedMessage.runId,
        uiEphemeral: true,
        uiStreamPhase: matchingLocal.uiStreamPhase || mergedMessage.uiStreamPhase,
        metadata: {
            ...(matchingLocal.metadata || {}),
            ...(mergedMessage.metadata || {}),
        },
        images: mergeMessageImages(matchingLocal.images || [], mergedMessage.images || []),
        artifacts: mergeArtifacts(matchingLocal.artifacts || [], mergedMessage.artifacts || []),
        nodes: mergeTimelineNodes(mergedMessage.nodes || [], matchingLocal.nodes || []),
    };
    if (String(matchingLocal.content || "").trim().length > String(nextMessage.content || "").trim().length) {
        nextMessage.content = matchingLocal.content;
    }
    if (countDefinedAssistantTaskProgressFields(localTaskProgress) > 0) {
        nextMessage.metadata = {
            ...(nextMessage.metadata || {}),
            assistantTaskProgress: localTaskProgress,
        };
    }
    return nextMessage;
}

function mergeStructuredSnapshotMessages(
    current: ChatMessage[],
    snapshotMessages: ChatMessage[],
) {
    const normalizedSnapshot = normalizeMessagesForState(snapshotMessages);
    if (current.length === 0) {
        return normalizedSnapshot;
    }

    const currentByKey = new Map<string, ChatMessage>();
    current.forEach((message) => {
        buildMessageComparisonKeys(message).forEach((key) => {
            if (!currentByKey.has(key)) {
                currentByKey.set(key, message);
            }
        });
    });

    return normalizeMessagesForState(normalizedSnapshot.map((snapshotMessage) => {
        const matchingLocal = buildMessageComparisonKeys(snapshotMessage)
            .map((key) => currentByKey.get(key))
            .find(Boolean);
        if (!matchingLocal) {
            return snapshotMessage;
        }
        const snapshotTranscriptVersion = Number((snapshotMessage.metadata || {}).transcriptVersion || 0);
        const snapshotCanonical = snapshotTranscriptVersion > 0 || (snapshotMessage.nodes?.length || 0) > 0;
        if (snapshotCanonical) {
            const canonicalSnapshot = normalizeMessagesForState([snapshotMessage])[0] || snapshotMessage;
            if (shouldPreserveAssistantPlaceholder(snapshotMessage, matchingLocal)) {
                return mergeAssistantPlaceholder(snapshotMessage, matchingLocal, canonicalSnapshot);
            }
            return mergeUserStructuredMetadata(snapshotMessage, matchingLocal, canonicalSnapshot);
        }

        const localHasStructuredState = hasStructuredAssistantPayload(matchingLocal);
        const snapshotHasStructuredState = hasStructuredAssistantPayload(snapshotMessage);
        const snapshotRenderable = hasRenderableMessagePayload(snapshotMessage);
        const snapshotAuthoritativeAssistant = snapshotMessage.role === "assistant" && snapshotRenderable && snapshotHasStructuredState;
        const mergedMessage = snapshotAuthoritativeAssistant
            ? (normalizeMessagesForState([snapshotMessage])[0] || snapshotMessage)
            : (normalizeMessagesForState([snapshotMessage, matchingLocal])[0] || snapshotMessage);
        if (snapshotAuthoritativeAssistant && matchingLocal.role === "assistant") {
            mergedMessage.metadata = {
                ...(matchingLocal.metadata || {}),
                ...(mergedMessage.metadata || {}),
            };
            if ((!mergedMessage.images || mergedMessage.images.length === 0) && matchingLocal.images?.length) {
                mergedMessage.images = mergeMessageImages(matchingLocal.images || [], snapshotMessage.images || []);
            }
            if ((!mergedMessage.artifacts || mergedMessage.artifacts.length === 0) && matchingLocal.artifacts?.length) {
                mergedMessage.artifacts = mergeArtifacts(matchingLocal.artifacts || [], snapshotMessage.artifacts || []);
            }
            if ((!mergedMessage.toolInvocations || mergedMessage.toolInvocations.length === 0) && matchingLocal.toolInvocations?.length) {
                mergedMessage.toolInvocations = matchingLocal.toolInvocations;
            }
        }
        if (matchingLocal.role === "assistant" && localHasStructuredState && !snapshotHasStructuredState) {
            mergedMessage.nodes = (matchingLocal.nodes?.length || 0) >= (snapshotMessage.nodes?.length || 0)
                ? matchingLocal.nodes
                : snapshotMessage.nodes;
            mergedMessage.artifacts = (matchingLocal.artifacts?.length || 0) >= (snapshotMessage.artifacts?.length || 0)
                ? matchingLocal.artifacts
                : snapshotMessage.artifacts;
            mergedMessage.images = mergeMessageImages(matchingLocal.images || [], snapshotMessage.images || []);
            if (matchingLocal.toolInvocations?.length) {
                mergedMessage.toolInvocations = matchingLocal.toolInvocations;
            }
        }

        if (shouldPreserveAssistantPlaceholder(snapshotMessage, matchingLocal)) {
            return mergeAssistantPlaceholder(snapshotMessage, matchingLocal, mergedMessage);
        }
        return mergeUserStructuredMetadata(snapshotMessage, matchingLocal, mergedMessage);
    }));
}

function mergeAuthoritativeSnapshotMessages(
    current: ChatMessage[],
    snapshotMessages: ChatMessage[],
    preserveOptimisticLocalState: boolean,
) {
    const normalizedSnapshot = normalizeMessagesForState(snapshotMessages);
    if (!preserveOptimisticLocalState) {
        return mergeStructuredSnapshotMessages(current, normalizedSnapshot);
    }

    const preservableLocals = current.filter((message) =>
        isOptimisticLocalMessage(message)
        || shouldPreserveLocalAssistantMessage(message),
    );
    if (preservableLocals.length === 0) {
        return normalizedSnapshot;
    }

    const usedPreservableLocals = new Set<string>();
    const mergedSnapshotMessages = normalizedSnapshot.map((snapshotMessage) => {
        const snapshotKeys = new Set(buildMessageComparisonKeys(snapshotMessage));
        const matchingLocal = preservableLocals.find((candidate) => {
            const localId = String(candidate.id || "").trim();
            if (localId && usedPreservableLocals.has(localId)) {
                return false;
            }
            if (
                candidate.role === "assistant"
                && snapshotMessage.role === "assistant"
                && candidate.runId
                && snapshotMessage.runId
                && candidate.runId === snapshotMessage.runId
            ) {
                return true;
            }
            return buildMessageComparisonKeys(candidate).some((key) => snapshotKeys.has(key));
        });

        if (!matchingLocal) {
            return snapshotMessage;
        }
        const matchingLocalId = String(matchingLocal.id || "").trim();
        if (matchingLocalId) {
            usedPreservableLocals.add(matchingLocalId);
        }
        const snapshotTranscriptVersion = Number((snapshotMessage.metadata || {}).transcriptVersion || 0);
        const snapshotCanonical = snapshotTranscriptVersion > 0 || (snapshotMessage.nodes?.length || 0) > 0;
        if (snapshotCanonical) {
            if (shouldPreserveAssistantPlaceholder(snapshotMessage, matchingLocal)) {
                return mergeAssistantPlaceholder(snapshotMessage, matchingLocal, snapshotMessage);
            }
            return mergeUserStructuredMetadata(snapshotMessage, matchingLocal, snapshotMessage);
        }

        const localStreamActive = matchingLocal.role === "assistant" && isActiveAssistantStreamPhase(matchingLocal.uiStreamPhase);
        const snapshotRenderable = hasRenderableMessagePayload(snapshotMessage);
        const localRenderable = hasRenderableMessagePayload(matchingLocal);
        const localTaskProgress = readAssistantTaskProgress(matchingLocal);
        const snapshotTaskProgress = readAssistantTaskProgress(snapshotMessage);
        const localHasStructuredState = hasStructuredAssistantPayload(matchingLocal);
        const snapshotHasStructuredState = hasStructuredAssistantPayload(snapshotMessage);
        const snapshotAuthoritativeAssistant = snapshotMessage.role === "assistant" && snapshotRenderable && snapshotHasStructuredState;
        const mergedMessage = snapshotAuthoritativeAssistant
            ? (normalizeMessagesForState([snapshotMessage])[0] || snapshotMessage)
            : (normalizeMessagesForState([snapshotMessage, matchingLocal])[0] || snapshotMessage);
        const localTaskProgressRicher = countDefinedAssistantTaskProgressFields(localTaskProgress) > countDefinedAssistantTaskProgressFields(snapshotTaskProgress);
        const shouldMergeLocalStructuredState = localHasStructuredState
            && localStreamActive
            && (!snapshotRenderable || !snapshotHasStructuredState);

        if (snapshotAuthoritativeAssistant) {
            mergedMessage.metadata = {
                ...(matchingLocal.metadata || {}),
                ...(mergedMessage.metadata || {}),
            };
            if ((!mergedMessage.images || mergedMessage.images.length === 0) && matchingLocal.images?.length) {
                mergedMessage.images = mergeMessageImages(matchingLocal.images || [], snapshotMessage.images || []);
            }
            if ((!mergedMessage.artifacts || mergedMessage.artifacts.length === 0) && matchingLocal.artifacts?.length) {
                mergedMessage.artifacts = mergeArtifacts(matchingLocal.artifacts || [], snapshotMessage.artifacts || []);
            }
            if ((!mergedMessage.toolInvocations || mergedMessage.toolInvocations.length === 0) && matchingLocal.toolInvocations?.length) {
                mergedMessage.toolInvocations = matchingLocal.toolInvocations;
            }
        }

        if (matchingLocal.role === "assistant" && matchingLocal.uiEphemeral) {
            mergedMessage.uiEphemeral = !snapshotRenderable || localStreamActive;
            mergedMessage.uiStreamPhase = localStreamActive
                ? matchingLocal.uiStreamPhase
                : snapshotMessage.uiStreamPhase;
        }

        if (matchingLocal.role === "assistant") {
            if (
                countDefinedAssistantTaskProgressFields(localTaskProgress) > 0
                && (localStreamActive || !snapshotRenderable || !snapshotHasStructuredState || localTaskProgressRicher)
            ) {
                mergedMessage.metadata = {
                    ...(mergedMessage.metadata || {}),
                    assistantTaskProgress: localTaskProgress,
                };
            }
        }

        if (matchingLocal.role === "assistant" && (localStreamActive || shouldMergeLocalStructuredState)) {
            if (localStreamActive && !snapshotRenderable) {
                if (String(matchingLocal.content || "").trim().length > String(mergedMessage.content || "").trim().length) {
                    mergedMessage.content = matchingLocal.content;
                }
            }
            if (shouldMergeLocalStructuredState) {
                mergedMessage.nodes = mergeTimelineNodes(mergedMessage.nodes || [], matchingLocal.nodes || []);
                mergedMessage.images = mergeMessageImages(mergedMessage.images || [], matchingLocal.images || []);
                mergedMessage.artifacts = mergeArtifacts(mergedMessage.artifacts || [], matchingLocal.artifacts || []);
                if ((!mergedMessage.toolInvocations || mergedMessage.toolInvocations.length === 0) && matchingLocal.toolInvocations?.length) {
                    mergedMessage.toolInvocations = matchingLocal.toolInvocations;
                }
            }
            if (countDefinedAssistantTaskProgressFields(localTaskProgress) > 0) {
                mergedMessage.metadata = {
                    ...(mergedMessage.metadata || {}),
                    assistantTaskProgress: localTaskProgress,
                };
            }
            if ((!snapshotRenderable || localStreamActive || !snapshotHasStructuredState) && matchingLocal.uiEphemeral) {
                mergedMessage.uiEphemeral = true;
            }
            if (localStreamActive && matchingLocal.uiStreamPhase) {
                mergedMessage.uiStreamPhase = matchingLocal.uiStreamPhase;
            }
        }

        if (shouldPreserveAssistantPlaceholder(snapshotMessage, matchingLocal)) {
            return mergeAssistantPlaceholder(snapshotMessage, matchingLocal, mergedMessage);
        }
        return mergeUserStructuredMetadata(snapshotMessage, matchingLocal, mergedMessage);
    });

    const unmatchedOptimisticLocals = preservableLocals.filter((message) => {
        const messageId = String(message.id || "").trim();
        if (messageId && usedPreservableLocals.has(messageId)) {
            return false;
        }
        const comparisonKeys = buildMessageComparisonKeys(message);
        return !mergedSnapshotMessages.some((snapshotMessage) => {
            const snapshotKeys = new Set(buildMessageComparisonKeys(snapshotMessage));
            return comparisonKeys.some((key) => snapshotKeys.has(key));
        });
    });

    return normalizeMessagesForState([...mergedSnapshotMessages, ...unmatchedOptimisticLocals]);
}

function extractSnapshotMessages(payload: Partial<ConversationDetail | RealtimeSessionSnapshot | Record<string, unknown>> | null | undefined) {
    const root = asRecord(payload);
    const snapshot = asRecord(root.snapshot);
    const messageCandidates = [root.timeline, snapshot.timeline, root.messages, snapshot.messages];
    for (const candidate of messageCandidates) {
        if (Array.isArray(candidate)) {
            return candidate.filter((item): item is ChatMessage => Boolean(item) && typeof item === "object");
        }
    }
    return null;
}

function extractQueuedMessages(payload: Partial<ConversationDetail | RealtimeSessionSnapshot | Record<string, unknown>> | null | undefined): QueuedChatMessage[] | null {
    const root = asRecord(payload);
    const snapshot = asRecord(root.snapshot);
    const candidates = [root.queuedMessages, snapshot.queuedMessages];
    for (const candidate of candidates) {
        if (Array.isArray(candidate)) {
            return candidate
                .filter((item): item is QueuedChatMessage => Boolean(item) && typeof item === "object")
                .filter((item) => String(item.id || "").trim());
        }
    }
    return null;
}

function isLegacyChatUnsupportedPayload(payload: Partial<ConversationDetail | RealtimeSessionSnapshot | Record<string, unknown>> | null | undefined) {
    const root = asRecord(payload);
    const snapshot = asRecord(root.snapshot);
    return Boolean(root.legacyChatUnsupported || snapshot.legacyChatUnsupported);
}

const QUEUE_ELIGIBLE_RUN_STATUSES = new Set([
    "queued",
    "pending",
    "starting",
    "streaming",
    "running",
    "waiting",
    "waiting_input",
    "waiting_approval",
    "waiting_external_tool",
    "waiting_external",
    "paused",
]);

function isQueueEligibleRunStatus(status: unknown) {
    return QUEUE_ELIGIBLE_RUN_STATUSES.has(String(status || "").trim().toLowerCase());
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
}

function upsertAskUserInteraction(current: AskUserInteraction[], incoming: AskUserInteraction) {
    const incomingId = String(incoming.id || incoming.interactionId || "").trim();
    if (!incomingId) {
        return [incoming, ...current];
    }
    const next = [...current];
    const existingIndex = next.findIndex((item) => String(item.id || item.interactionId || "").trim() === incomingId);
    if (existingIndex >= 0) {
        next[existingIndex] = {
            ...next[existingIndex],
            ...incoming,
            request: {
                ...(next[existingIndex].request || {}),
                ...(incoming.request || {}),
            },
        };
        return next;
    }
    return [incoming, ...next];
}

function readRealtimeDiagnostics(value: unknown) {
    const record = asRecord(value);
    return asRecord(record._diagnostics);
}

function debugRealtimeTrace(stage: string, payload: Record<string, unknown>) {
    if (!__DEV__) {
        return;
    }
    try {
        console.debug(`[phone/realtime/${stage}]`, payload);
    } catch {
        // ignore debug log failures
    }
}

function debugPerfTrace(stage: string, payload: Record<string, unknown>) {
    if (!__DEV__) {
        return;
    }
    try {
        console.debug(`[phone/perf/${stage}]`, payload);
    } catch {
        // ignore debug log failures
    }
}

function getPerfNowMs() {
    if (typeof performance !== "undefined" && typeof performance.now === "function") {
        return performance.now();
    }
    return Date.now();
}

function measureJsonBytes(value: unknown) {
    if (!__DEV__) {
        return 0;
    }
    try {
        return new TextEncoder().encode(JSON.stringify(value)).length;
    } catch {
        try {
            return JSON.stringify(value).length;
        } catch {
            return 0;
        }
    }
}

function readPayloadProfile(payload: unknown) {
    const root = asRecord(payload);
    return asRecord(root._profile);
}

function countPayloadRuntimeEvents(payload: unknown) {
    const root = asRecord(payload);
    const view = deriveAuthoritativeSessionView(payload).view;
    const viewTimeline = Array.isArray(view?.runtimeTimeline) ? view.runtimeTimeline as unknown[] : null;
    const runtimeTimeline: unknown[] = viewTimeline
        ? viewTimeline
        : Array.isArray(root.runtimeTimeline)
            ? root.runtimeTimeline as unknown[]
            : Array.isArray(asRecord(root.snapshot).runtimeTimeline)
                ? asRecord(root.snapshot).runtimeTimeline as unknown[]
                : [];
    return runtimeTimeline.length;
}

type StreamLatencyStats = {
    count: number;
    deltaChars: number[];
    interDeltaMs: number[];
    proxyLagMs: number[];
    clientCommitLagMs: number[];
    renderLagMs: number[];
    firstProviderDeltaAtMs?: number;
    firstPhoneReceiveAtMs?: number;
    lastPhoneReceiveAtMs?: number;
};

function toEpochMs(value: unknown) {
    if (typeof value === "number" && Number.isFinite(value)) {
        return value;
    }
    if (typeof value === "string" && value.trim()) {
        const parsed = Date.parse(value);
        return Number.isFinite(parsed) ? parsed : undefined;
    }
    return undefined;
}

function percentile(values: number[], percentileValue: number) {
    const sorted = values.filter((item) => Number.isFinite(item)).sort((a, b) => a - b);
    if (!sorted.length) {
        return 0;
    }
    const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil((percentileValue / 100) * sorted.length) - 1));
    return Math.round(sorted[index]);
}

function summarizeStreamLatencyStats(stats: StreamLatencyStats) {
    const firstTokenMs = stats.firstProviderDeltaAtMs !== undefined && stats.firstPhoneReceiveAtMs !== undefined
        ? Math.max(0, Math.round(stats.firstPhoneReceiveAtMs - stats.firstProviderDeltaAtMs))
        : 0;
    return {
        count: stats.count,
        firstTokenMs,
        interDeltaP50: percentile(stats.interDeltaMs, 50),
        interDeltaP95: percentile(stats.interDeltaMs, 95),
        deltaCharsP50: percentile(stats.deltaChars, 50),
        deltaCharsP95: percentile(stats.deltaChars, 95),
        proxyLagP95: percentile(stats.proxyLagMs, 95),
        clientCommitLagP95: percentile(stats.clientCommitLagMs, 95),
        renderLagP95: percentile(stats.renderLagMs, 95),
    };
}

function buildRealtimeEventDedupKey(event: PhoneRealtimeUiEvent) {
    const eventId = String((event as Record<string, unknown>).event_id || "").trim();
    if (eventId) {
        return `event:${eventId}`;
    }

    const data = asRecord(event.data);
    const tool = asRecord(event.tool);
    const fingerprint = String(
        event.content
        || data.label
        || data.summary
        || data.topic
        || tool.toolCallId
        || data.toolCallId
        || data.tool_call_id
        || "",
    ).trim().replace(/\s+/g, " ").slice(0, 160);

    return [
        "seq",
        Number(event.seq || 0) || 0,
        String(event.type || "").trim(),
        String(event.name || "").trim(),
        String(event.topic || "").trim(),
        String(event.run_id || "").trim(),
        fingerprint,
    ].join("¦");
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
        currentRunId: String(summary.currentRunId || record.currentRunId || "").trim() || undefined,
        lastRunId: String(summary.lastRunId || record.lastRunId || "").trim() || undefined,
        endedAt: String(summary.endedAt || record.endedAt || "").trim() || undefined,
        pendingApprovalCount: Number(summary.pendingApprovalCount || record.pendingApprovalCount || 0) || 0,
        hasPendingApproval: Boolean(summary.hasPendingApproval || record.hasPendingApproval),
        controls: (record.controls as ConversationSummary["controls"]) || undefined,
        recoverable: typeof record.recoverable === "boolean" ? record.recoverable : undefined,
    };
}

function normalizeDesktopLiveErrorMessage(
    error: unknown,
    t: (key: string, params?: Record<string, string | number>) => string,
) {
    const raw = error instanceof Error ? String(error.message || "").trim() : "";
    if (!raw) {
        return t("src.screens.chatscreen.desktop_preview_is_still_preparing_please_wait");
    }
    if (/fetch failed|network request failed|failed to fetch|bridge is starting|local-offer-unavailable|offer|candidate|session/i.test(raw)) {
        return t("src.screens.chatscreen.desktop_preview_is_still_preparing_please_wait");
    }
    return raw;
}

function canUseDesktopLiveWebrtc(status: DesktopLiveStatus | null | undefined) {
    return status?.available === true && status?.bridgeReady !== false;
}

function canUseDesktopLiveStreamFallback(status: DesktopLiveStatus | null | undefined) {
    return Boolean(status && status.bridgeReady !== false && (status.fallbackAvailable === true || status.streamFallbackReady === true));
}

function canUseDesktopLivePreview(status: DesktopLiveStatus | null | undefined) {
    return canUseDesktopLiveWebrtc(status) || canUseDesktopLiveStreamFallback(status);
}

function isPlaceholderConversationTitle(title: string | null | undefined) {
    const normalized = String(title || "").trim().toLowerCase();
    return !normalized
        || normalized === "new chat"
        || normalized === zhNewChatPlaceholder
        || normalized === legacyZhNewChatPlaceholder;
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
    const params = useLocalSearchParams<{ new?: string | string[] }>();
    const newConversationIntent = Array.isArray(params.new) ? params.new[0] === "1" : params.new === "1";
    const {
        status,
        user,
        adminBaseUrl,
        accessToken,
        activeConversationId,
        setActiveConversationId,
        authorizedFetch,
        authorizedRealtimeStream,
        getEngineNowMs,
    } = useAppSession();
    const {
        locale,
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
    const stopRealtimeRef = useRef<(options?: { preserveMessageState?: boolean }) => void>(() => undefined);
    const closeDesktopPreviewRef = useRef<() => Promise<void>>(async () => undefined);
    const latestSeqRef = useRef(0);
    const desktopPreviewRequestIdRef = useRef(0);
    const desktopPreviewWebViewRef = useRef<WebView | null>(null);
    const desktopPreviewNegotiatedSessionRef = useRef("");
    const desktopLiveUserIntentRef = useRef(false);
    const voiceAutoplayStateRef = useRef(new Map<string, {
        lastSeenMessageKey: string;
        lastAutoPlayedKey: string;
    }>());
    const realtimeSnapshotTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const realtimeSnapshotInflightRef = useRef(false);
    const realtimeSnapshotPendingRef = useRef(false);
    const waitingApprovalRefreshAtRef = useRef(0);
    const recentlyResolvedApprovalIdsRef = useRef<Set<string>>(new Set());
    const lastMessageFingerprintRef = useRef("");
    const lastAppliedSnapshotSeqRef = useRef(0);
    const lastAppliedSnapshotFingerprintRef = useRef("");
    const lastRealtimeSnapshotAtRef = useRef(0);
    const seenRealtimeEventKeysRef = useRef<Set<string>>(new Set());
    const pendingRealtimeRenderDiagnosticRef = useRef<Record<string, unknown> | null>(null);
    const streamLatencyStatsRef = useRef(new Map<string, StreamLatencyStats>());
    const messagesRef = useRef<ChatMessage[]>([]);
    const messageConversationIdRef = useRef<string | null>(activeConversationId);
    const todosRef = useRef<SessionTodoItem[]>([]);
    const realtimeMessageStateRef = useRef(
        createInitialSessionRealtimeMessageState<ChatMessage>([], PHONE_STREAM_LIFECYCLE_OPTIONS),
    );
    const runtimeFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const runtimeFlushFrameRef = useRef<number | null>(null);
    const runtimeRef = useRef<RuntimeSummary>({ status: "idle", latestSeq: 0 });
    const activeRunIdRef = useRef<string>("");
    const activeConversationIdRef = useRef<string | null>(activeConversationId);
    const previousConversationIdRef = useRef<string | null>(null);
    const conversationTransitionTokenRef = useRef(0);
    const optimisticSeedConversationIdRef = useRef<string | null>(null);
    const ttsRequestIdRef = useRef(0);
    const replyPopSeenRef = useRef(new Map<string, string>());
    const replyPopPlayedRef = useRef(new Set<string>());
    const tRef = useRef(t);
    const ttsPlayer = useAudioPlayer();
    const replyPopPlayer = useAudioPlayer(REPLY_POP_SOUND);
    const ttsStatus = useAudioPlayerStatus(ttsPlayer);
    const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
    const recorderState = useAudioRecorderState(recorder);
    const { width, height } = useWindowDimensions();
    const safeAreaInsets = useSafeAreaInsets();
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
    const [legacyChatUnsupported, setLegacyChatUnsupported] = useState(false);
    const [conversations, setConversations] = useState<ConversationSummary[]>([]);
    const [projects, setProjects] = useState<ProjectSummary[]>([]);
    const [mainWorkspacePath, setMainWorkspacePath] = useState("");
    const [workspaceChooserVisible, setWorkspaceChooserVisible] = useState(false);
    const [workspaceChooserBusy, setWorkspaceChooserBusy] = useState(false);
    const [newProjectPath, setNewProjectPath] = useState("");
    const [folderRoots, setFolderRoots] = useState<WorkspaceFolderNode[]>([]);
    const [selectedFolderPath, setSelectedFolderPath] = useState("");
    const [expandedFolderPaths, setExpandedFolderPaths] = useState<Set<string>>(() => new Set());
    const [loadingFolderPaths, setLoadingFolderPaths] = useState<Set<string>>(() => new Set());
    const [newFolderName, setNewFolderName] = useState("");
    const [scopeBinding, setScopeBinding] = useState<ScopeBindingView | null>(null);
    const [scopeLoading, setScopeLoading] = useState(false);
    const [approvals, setApprovals] = useState<PendingApproval[]>([]);
    const [askUserInteractions, setAskUserInteractions] = useState<AskUserInteraction[]>([]);
    const [queuedMessages, setQueuedMessages] = useState<QueuedChatMessage[]>([]);
    const [queuedMessagesCollapsed, setQueuedMessagesCollapsed] = useState(false);
    const [editingQueuedMessage, setEditingQueuedMessage] = useState<QueuedChatMessage | null>(null);
    const [queuedMessageEditText, setQueuedMessageEditText] = useState("");
    const [queuedMessageEditBusy, setQueuedMessageEditBusy] = useState(false);
    const [todos, setTodos] = useState<SessionTodoItem[]>([]);
    const [processes, setProcesses] = useState<AdminProcessRef[]>([]);
    const processesRef = useRef<AdminProcessRef[]>([]);
    const lastProcessSurfaceAtRef = useRef(0);
    const [contextReferences, setContextReferences] = useState<ContextReferenceItem[]>([]);
    const [contextGovernance, setContextGovernance] = useState<ContextGovernanceView | null>(null);
    const [contextGovernanceHistory, setContextGovernanceHistory] = useState<ContextGovernanceView[]>([]);
    const [commands, setCommands] = useState<CommandPresetSummary[]>([]);
    const [skills, setSkills] = useState<SkillReferenceSummary[]>([]);
    const [subagentFamilies, setSubagentFamilies] = useState<SubagentFamilySummary[]>([]);
    const [uploadedFiles, setUploadedFiles] = useState<UploadedWorkspaceFile[]>([]);
    const [selectedCommand, setSelectedCommand] = useState<CommandPresetSummary | null>(null);
    const [selectedSkills, setSelectedSkills] = useState<SkillReferenceSummary[]>([]);
    const [selectedSubagentFamilies, setSelectedSubagentFamilies] = useState<SubagentFamilySummary[]>([]);
    const [activeQueryMode, setActiveQueryMode] = useState<"command" | "skill" | null>(null);
    const [activeQueryText, setActiveQueryText] = useState("");
    const [taskPlanningMode, setTaskPlanningMode] = useState(false);
    const [bottomLayerHeight, setBottomLayerHeight] = useState(132);
    const [runtime, setRuntime] = useState<RuntimeSummary>({ status: "idle", latestSeq: 0 });
    const [runtimeTimeline, setRuntimeTimeline] = useState<PhoneRuntimeTimelineEntry[]>([]);
    const [runtimePanelOpen, setRuntimePanelOpen] = useState(false);
    const [leftRailOpen, setLeftRailOpen] = useState(false);
    const [rightRailOpen, setRightRailOpen] = useState(false);
    const [governanceApprovalOpen, setGovernanceApprovalOpen] = useState(false);
    const [governanceApprovalBusy, setGovernanceApprovalBusy] = useState(false);
    const [dismissedGovernanceApprovalId, setDismissedGovernanceApprovalId] = useState("");
    const [selectedRuntimeId, setSelectedRuntimeId] = useState<PhoneRuntimeId>("chat");
    const [workspaceInfoOpen, setWorkspaceInfoOpen] = useState(false);
    const [desktopPreviewOpen, setDesktopPreviewOpen] = useState(false);
    const [desktopPreviewFullscreen, setDesktopPreviewFullscreen] = useState(false);
    const [desktopPreviewBusy, setDesktopPreviewBusy] = useState(false);
    const [desktopPreviewSessionId, setDesktopPreviewSessionId] = useState("");
    const [desktopPreviewError, setDesktopPreviewError] = useState("");
    const [desktopPreviewState, setDesktopPreviewState] = useState<"closed" | "loading" | "preview" | "error">("closed");
    const [desktopPreviewWebReady, setDesktopPreviewWebReady] = useState(false);
    const [desktopPreviewFallbackUrl, setDesktopPreviewFallbackUrl] = useState("");
    const [desktopLiveStatus, setDesktopLiveStatus] = useState<DesktopLiveStatus | null>(null);

    const queryTerm = activeQueryText.trim().toLowerCase();
    const commandPickerOpen = activeQueryMode === "command" && !selectedCommand;
    const skillPickerOpen = activeQueryMode === "skill";
    const filteredCommands = useMemo(() => {
        if (!queryTerm) {
            return commands;
        }
        return commands.filter((item) =>
            item.name.toLowerCase().includes(queryTerm)
            || String(item.summary || "").toLowerCase().includes(queryTerm),
        );
    }, [commands, queryTerm]);
    const filteredMentionItems = useMemo<ComposerMentionItem[]>(() => {
        const selectedKeys = new Set(selectedSkills.map((skill) => `${skill.name}:${skill.path || ""}`));
        const selectedFamilyIds = new Set(selectedSubagentFamilies.map((family) => family.familyId));
        const base: ComposerMentionItem[] = [
            ...skills
                .filter((item) => !selectedKeys.has(`${item.name}:${item.path || ""}`))
                .map((skill) => ({ kind: "skill" as const, key: `skill:${skill.name}:${skill.path || ""}`, skill })),
            ...subagentFamilies
                .filter((family) => family.familyId && !selectedFamilyIds.has(family.familyId))
                .map((family) => ({ kind: "subagent_family" as const, key: `family:${family.familyId}`, family })),
        ];
        if (!queryTerm) {
            return base;
        }
        return base.filter((item) =>
            item.kind === "skill"
                ? (
                    item.skill.name.toLowerCase().includes(queryTerm)
                    || String(item.skill.description || "").toLowerCase().includes(queryTerm)
                    || String(item.skill.path || "").toLowerCase().includes(queryTerm)
                )
                : (
                    item.family.familyId.toLowerCase().includes(queryTerm)
                    || String(item.family.displayName || "").toLowerCase().includes(queryTerm)
                    || String(item.family.description || "").toLowerCase().includes(queryTerm)
                    || (item.family.aliases || []).some((alias) => String(alias || "").toLowerCase().includes(queryTerm))
                ),
        );
    }, [queryTerm, selectedSkills, selectedSubagentFamilies, skills, subagentFamilies]);
    const boundProject = useMemo(
        () => projects.find((project) => project.id === scopeBinding?.projectId) || null,
        [projects, scopeBinding?.projectId],
    );
    const availableProjects = useMemo(
        () => projects.filter((project) => project.active !== false),
        [projects],
    );

    const clearNewConversationIntent = useCallback(() => {
        if (newConversationIntent) {
            router.replace("/chat" as Href);
        }
    }, [newConversationIntent]);

    useEffect(() => {
        runtimeRef.current = runtime;
    }, [runtime]);

    useEffect(() => {
        tRef.current = t;
    }, [t]);

    useEffect(() => {
        activeConversationIdRef.current = activeConversationId;
    }, [activeConversationId]);

    useEffect(() => {
        messagesRef.current = messages;
        lastMessageFingerprintRef.current = buildMessagesFingerprint(messages);
        realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
            messages,
            PHONE_STREAM_LIFECYCLE_OPTIONS,
        );
    }, [messages]);

    useEffect(() => {
        if (!pendingRealtimeRenderDiagnosticRef.current) {
            return;
        }
        const diagnostic = pendingRealtimeRenderDiagnosticRef.current;
        pendingRealtimeRenderDiagnosticRef.current = null;
        const streamMetricKey = String(diagnostic.streamMetricKey || "").trim();
        if (__DEV__ && streamMetricKey) {
            const stats = streamLatencyStatsRef.current.get(streamMetricKey);
            const phoneCommitAtMs = toEpochMs(diagnostic.phoneCommitAt);
            const phoneReceivedAtMs = toEpochMs(diagnostic.phoneReceivedAt);
            const phoneRenderedAtMs = Date.now();
            if (stats && phoneCommitAtMs !== undefined && phoneReceivedAtMs !== undefined) {
                stats.clientCommitLagMs.push(Math.max(0, phoneCommitAtMs - phoneReceivedAtMs));
                stats.renderLagMs.push(Math.max(0, phoneRenderedAtMs - phoneReceivedAtMs));
                if (stats.count % 20 === 0) {
                    debugRealtimeTrace("stream-summary", {
                        streamMetricKey,
                        ...summarizeStreamLatencyStats(stats),
                    });
                }
            }
        }
        debugRealtimeTrace("render", {
            ...diagnostic,
            phoneRenderedAt: new Date().toISOString(),
            latestSeq: latestSeqRef.current,
            runtimeStatus: runtimeRef.current.status,
            messageFingerprint: lastMessageFingerprintRef.current,
        });
    }, [messages, runtimeTimeline, todos, runtime.status]);

    useEffect(() => {
        todosRef.current = todos;
    }, [todos]);

    const sendingRef = useRef(sending);

    useEffect(() => {
        sendingRef.current = sending;
    }, [sending]);

    const resetConversationStreamState = useCallback(() => {
        realtimeMessageStateRef.current = createInitialSessionRealtimeMessageState<ChatMessage>(
            [],
            PHONE_STREAM_LIFECYCLE_OPTIONS,
        );
        seenRealtimeEventKeysRef.current.clear();
        if (runtimeFlushTimerRef.current) {
            clearTimeout(runtimeFlushTimerRef.current);
            runtimeFlushTimerRef.current = null;
        }
        if (runtimeFlushFrameRef.current !== null) {
            if (typeof cancelAnimationFrame === "function") {
                cancelAnimationFrame(runtimeFlushFrameRef.current);
            }
            runtimeFlushFrameRef.current = null;
        }
        streamLatencyStatsRef.current.clear();
    }, []);

    const applySessionProcessSurface = useCallback((incoming: AdminProcessRef[], options?: { forceClear?: boolean }) => {
        const normalizedIncoming = incoming || [];
        setProcesses((current) => {
            if (normalizedIncoming.length > 0) {
                lastProcessSurfaceAtRef.current = Date.now();
                processesRef.current = normalizedIncoming;
                return normalizedIncoming;
            }
            if (options?.forceClear) {
                lastProcessSurfaceAtRef.current = 0;
                processesRef.current = [];
                return [];
            }
            if (current.length === 0) {
                return current;
            }
            if ((Date.now() - lastProcessSurfaceAtRef.current) <= 3000) {
                processesRef.current = current;
                return current;
            }
            processesRef.current = [];
            return [];
        });
    }, []);

    const clearActiveConversationViewState = useCallback(() => {
        resetConversationStreamState();
        messagesRef.current = [];
        messageConversationIdRef.current = null;
        setMessages([]);
        setLegacyChatUnsupported(false);
        setApprovals([]);
        setAskUserInteractions([]);
        setTodos([]);
        todosRef.current = [];
        applySessionProcessSurface([], { forceClear: true });
        setContextReferences([]);
        setContextGovernance(null);
        setContextGovernanceHistory([]);
        setRuntime({ status: "idle", latestSeq: 0 });
        runtimeRef.current = { status: "idle", latestSeq: 0 };
        activeRunIdRef.current = "";
        setRuntimeTimeline([]);
        latestSeqRef.current = 0;
        lastAppliedSnapshotSeqRef.current = 0;
        lastAppliedSnapshotFingerprintRef.current = "";
        lastRealtimeSnapshotAtRef.current = 0;
        waitingApprovalRefreshAtRef.current = 0;
        setGovernanceApprovalOpen(false);
        setDismissedGovernanceApprovalId("");
    }, [applySessionProcessSurface, resetConversationStreamState]);

    const appendRuntimeTimeline = useCallback((entry: PhoneRuntimeTimelineEntry | null) => {
        if (!entry) {
            return;
        }
        setRuntimeTimeline((current) => mergePhoneRuntimeTimeline(current, [entry]));
    }, []);

    const flushPendingRuntimeEvents = useCallback(() => {
        if (runtimeFlushFrameRef.current !== null) {
            if (typeof cancelAnimationFrame === "function") {
                cancelAnimationFrame(runtimeFlushFrameRef.current);
            }
            runtimeFlushFrameRef.current = null;
        }
        if (runtimeFlushTimerRef.current) {
            clearTimeout(runtimeFlushTimerRef.current);
            runtimeFlushTimerRef.current = null;
        }
        const nextState = flushQueuedSessionRealtimeRuntimeEvents(
            messagesRef.current,
            realtimeMessageStateRef.current,
            {
                normalizeMessages: normalizeMessagesForState,
                lifecycleOptions: PHONE_STREAM_LIFECYCLE_OPTIONS,
            },
        );
        realtimeMessageStateRef.current = nextState.state;
        if (!nextState.changed) {
            return;
        }
        if (pendingRealtimeRenderDiagnosticRef.current) {
            pendingRealtimeRenderDiagnosticRef.current = {
                ...pendingRealtimeRenderDiagnosticRef.current,
                phoneCommitAt: new Date().toISOString(),
            };
        }
        const fingerprint = buildMessagesFingerprint(nextState.messages);
        if (fingerprint === lastMessageFingerprintRef.current) {
            return;
        }
        lastMessageFingerprintRef.current = fingerprint;
        messagesRef.current = nextState.messages;
        messageConversationIdRef.current = String(activeConversationIdRef.current || "").trim() || messageConversationIdRef.current;
        setMessages(nextState.messages);
    }, []);

    const queueRuntimeMessageEvent = useCallback((event: PhoneRealtimeUiEvent, immediate = false) => {
        queueSessionRealtimeRuntimeEvent(realtimeMessageStateRef.current, event);
        if (immediate) {
            flushPendingRuntimeEvents();
            return;
        }
        if (runtimeFlushFrameRef.current !== null || runtimeFlushTimerRef.current) {
            return;
        }
        if (typeof requestAnimationFrame === "function") {
            runtimeFlushFrameRef.current = requestAnimationFrame(() => {
                runtimeFlushFrameRef.current = null;
                flushPendingRuntimeEvents();
            });
            return;
        }
        runtimeFlushTimerRef.current = setTimeout(() => {
            runtimeFlushTimerRef.current = null;
            flushPendingRuntimeEvents();
        }, 16);
    }, [flushPendingRuntimeEvents]);

    const patchAssistantTaskShell = useCallback((
        nextTodos: SessionTodoItem[],
        options?: {
            phase?: AssistantTaskProgressPatch["phase"];
            label?: string;
            subtitle?: string;
            runId?: string;
            createIfMissing?: boolean;
        },
    ) => {
        const patch = buildAssistantTaskProgressPatch(nextTodos, {
            phase: options?.phase,
            label: options?.label,
            subtitle: options?.subtitle,
        });
        if (!patch) {
            return;
        }
        setMessages((current) => {
            const next = applyAssistantTaskProgressPatch(
                current,
                patch,
                options?.runId,
                { createIfMissing: options?.createIfMissing },
            );
            if (next === current) {
                return current;
            }
            const fingerprint = buildMessagesFingerprint(next);
            if (fingerprint === lastMessageFingerprintRef.current) {
                return current;
            }
            lastMessageFingerprintRef.current = fingerprint;
            realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
                next,
                PHONE_STREAM_LIFECYCLE_OPTIONS,
            );
            messagesRef.current = next;
            messageConversationIdRef.current = String(activeConversationIdRef.current || "").trim() || messageConversationIdRef.current;
            return next;
        });
    }, []);

    const stopRealtime = useCallback((options?: { preserveMessageState?: boolean }) => {
        realtimeSubscriptionTokenRef.current += 1;
        if (realtimeSnapshotTimerRef.current) {
            clearTimeout(realtimeSnapshotTimerRef.current);
            realtimeSnapshotTimerRef.current = null;
        }
        realtimeSnapshotPendingRef.current = false;
        realtimeSnapshotInflightRef.current = false;
        if (realtimeAbortRef.current) {
            realtimeAbortRef.current.abort();
            realtimeAbortRef.current = null;
        }
        realtimeConversationIdRef.current = null;
        lastRealtimeSnapshotAtRef.current = 0;
        if (!options?.preserveMessageState) {
            resetConversationStreamState();
        }
    }, [resetConversationStreamState]);

    const refreshDesktopLiveStatus = useCallback(async () => {
        if (!desktopLiveUserIntentRef.current) {
            return desktopLiveStatus || {
                available: false,
                phase: "idle",
                bridgeReady: false,
                bridgeWarming: false,
                retryAllowed: false,
            } satisfies DesktopLiveStatus;
        }
        try {
            const next = await getDesktopLiveStatus(authorizedFetch);
            setDesktopLiveStatus(next);
            return next;
        } catch {
            const fallback = {
                available: false,
                reason: t("src.screens.chatscreen.desktop_preview_is_still_preparing_please_wait"),
                phase: "warming",
                bridgeReady: false,
                bridgeWarming: true,
                retryAllowed: false,
            } satisfies DesktopLiveStatus;
            setDesktopLiveStatus((current) => current || fallback);
            return fallback;
        }
    }, [authorizedFetch, desktopLiveStatus, t]);

    const prepareDesktopLiveBridge = useCallback(async () => {
        if (!desktopLiveUserIntentRef.current) {
            return desktopLiveStatus || {
                available: false,
                phase: "idle",
                bridgeReady: false,
                bridgeWarming: false,
                retryAllowed: false,
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
                phase: "warming",
                bridgeReady: false,
                bridgeWarming: true,
                retryAllowed: false,
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
        setDesktopPreviewFullscreen(false);
        setDesktopPreviewBusy(false);
        setDesktopPreviewState("closed");
        setDesktopPreviewError("");
        setDesktopPreviewSessionId("");
        setDesktopPreviewWebReady(false);
        setDesktopPreviewFallbackUrl("");
        if (!sessionId) {
            return;
        }
        try {
            await releaseDesktopLiveSession(authorizedFetch, sessionId);
        } catch {
            // Best-effort release when the foreground preview closes, without blocking the UI.
        }
        setDesktopLiveStatus((current) => current ? {
            ...current,
            activeSessionId: null,
            phase: "idle",
            bridgeWarming: false,
            retryAllowed: false,
        } : current);
    }, [authorizedFetch, desktopPreviewSessionId]);

    const waitForDesktopLiveAvailability = useCallback(async (requestId: number) => {
        let lastError = "";
        for (let attempt = 0; attempt < 18; attempt += 1) {
            if (desktopPreviewRequestIdRef.current !== requestId) {
                return null;
            }
            const status = await refreshDesktopLiveStatus();
            if (canUseDesktopLivePreview(status)) {
                return status;
            }
            lastError = String(
                status?.phase === "warming" || status?.bridgeWarming === true || status?.bridgeReady === false
                    ? t("src.screens.chatscreen.desktop_preview_bridge_is_starting_please_wait")
                    : status?.reason
                        || t("src.screens.chatscreen.desktop_preview_is_not_ready_yet_please_wait"),
            );
            await new Promise((resolve) => setTimeout(resolve, Math.min(900 + attempt * 150, 1800)));
        }
        throw new Error(lastError || t("src.screens.chatscreen.desktop_preview_is_not_ready_yet_please_wait"));
    }, [refreshDesktopLiveStatus, t]);

    const injectDesktopLiveFallbackStream = useCallback((sessionId?: string | null) => {
        const normalizedSessionId = String(sessionId || "").trim();
        if (!desktopLiveUserIntentRef.current || !desktopPreviewOpen || !normalizedSessionId) {
            return false;
        }
        const streamUrl = getDesktopLiveStreamUrl(adminBaseUrl, normalizedSessionId);
        desktopPreviewNegotiatedSessionRef.current = `fallback:${normalizedSessionId}`;
        setDesktopPreviewFallbackUrl(streamUrl);
        setDesktopPreviewBusy(false);
        setDesktopPreviewState("preview");
        setDesktopPreviewError("");
        return true;
    }, [adminBaseUrl, desktopPreviewOpen]);

    const maybeStartDesktopPreviewNegotiation = useCallback((sessionId?: string | null) => {
        const normalizedSessionId = String(sessionId || "").trim();
        if (!desktopLiveUserIntentRef.current || !desktopPreviewOpen || !normalizedSessionId) {
            return;
        }
        if (!canUseDesktopLiveWebrtc(desktopLiveStatus) && canUseDesktopLiveStreamFallback(desktopLiveStatus)) {
            if (desktopPreviewNegotiatedSessionRef.current === `fallback:${normalizedSessionId}`) {
                return;
            }
            injectDesktopLiveFallbackStream(normalizedSessionId);
            return;
        }
        if (!desktopPreviewWebReady) {
            return;
        }
        if (desktopPreviewNegotiatedSessionRef.current === `webrtc:${normalizedSessionId}`) {
            return;
        }
        desktopPreviewNegotiatedSessionRef.current = `webrtc:${normalizedSessionId}`;
        desktopPreviewWebViewRef.current?.injectJavaScript(
            buildDesktopLiveBridgeInjection({
                type: "start",
                iceServers: desktopLiveStatus?.iceServers || [],
                audioEnabled: desktopLiveStatus?.audioAvailable === true,
            }),
        );
    }, [desktopLiveStatus, desktopPreviewOpen, desktopPreviewWebReady, injectDesktopLiveFallbackStream]);

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
        setDesktopPreviewFallbackUrl("");
        desktopPreviewNegotiatedSessionRef.current = "";
        try {
            let status: DesktopLiveStatus | null = await refreshDesktopLiveStatus();
            if (desktopPreviewRequestIdRef.current !== requestId) {
                return;
            }
            if (!canUseDesktopLivePreview(status)) {
                await prepareDesktopLiveBridge();
                if (desktopPreviewRequestIdRef.current !== requestId) {
                    return;
                }
                status = await waitForDesktopLiveAvailability(requestId) || {
                    available: false,
                    reason: t("src.screens.chatscreen.desktop_preview_is_not_ready_yet_please_wait"),
                    phase: "warming",
                    bridgeReady: false,
                    bridgeWarming: true,
                    retryAllowed: false,
                } satisfies DesktopLiveStatus;
            }
            if (desktopPreviewRequestIdRef.current !== requestId) {
                return;
            }
            if (!canUseDesktopLivePreview(status)) {
                throw new Error(t("src.screens.chatscreen.desktop_preview_is_not_ready_yet_please_wait"));
            }
            setDesktopLiveStatus((current) => ({
                ...(current || {}),
                ...status,
                phase: canUseDesktopLiveWebrtc(status) ? "ready" : "degraded",
                bridgeWarming: false,
                retryAllowed: false,
            }));
            const payload = await retryWithDelay(
                async () => createDesktopLiveSession(authorizedFetch) as Promise<DesktopLiveSessionPayload>,
                6,
                1000,
            );
            const sessionId = String(payload.sessionId || payload.session_id || "").trim();
            if (!sessionId) {
                throw new Error(t("src.screens.chatscreen.desktop_preview_is_not_ready_yet_please_wait"));
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
                available: status?.available === true,
                fallbackAvailable: status?.fallbackAvailable,
                streamFallbackReady: status?.streamFallbackReady,
                phase: canUseDesktopLiveWebrtc(status) ? "ready" : "degraded",
                bridgeReady: status?.bridgeReady,
                bridgeWarming: false,
                activeSessionId: sessionId,
                retryAllowed: false,
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
                phase: "degraded",
                bridgeWarming: false,
                reason: message,
                retryAllowed: true,
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
                    throw new Error(t("src.screens.chatscreen.desktop_preview_negotiation_failed"));
                }
                desktopPreviewWebViewRef.current?.injectJavaScript(
                    buildDesktopLiveBridgeInjection({
                        type: "answer",
                        sdp: answer.sdp,
                        sdpType: answer.type,
                    }),
                );
            } catch (error) {
                if (canUseDesktopLiveStreamFallback(desktopLiveStatus) && injectDesktopLiveFallbackStream(sessionId)) {
                    setDesktopPreviewState("loading");
                    setDesktopPreviewBusy(true);
                    setDesktopPreviewError("");
                    return;
                }
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
            const fallbackPreview = payload.fallback === true;
            setDesktopPreviewBusy(false);
            setDesktopPreviewState("preview");
            setDesktopPreviewError("");
            setDesktopLiveStatus((current) => ({
                ...(current || {}),
                available: fallbackPreview ? current?.available === true : true,
                fallbackAvailable: fallbackPreview ? true : current?.fallbackAvailable,
                streamFallbackReady: fallbackPreview ? true : current?.streamFallbackReady,
                phase: fallbackPreview ? "degraded" : "ready",
                bridgeReady: current?.bridgeReady ?? true,
                bridgeWarming: false,
                activeSessionId: sessionId,
                retryAllowed: false,
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
                if (canUseDesktopLiveStreamFallback(desktopLiveStatus) && injectDesktopLiveFallbackStream(sessionId)) {
                    setDesktopPreviewState("loading");
                    setDesktopPreviewBusy(true);
                    setDesktopPreviewError("");
                    return;
                }
                desktopLiveUserIntentRef.current = false;
                setDesktopPreviewState("error");
                setDesktopPreviewBusy(false);
                setDesktopPreviewError(t("src.screens.chatscreen.desktop_preview_failed_to_connect_check_the_bridge_state_and_try_again_later"));
                setDesktopLiveStatus((current) => ({
                    ...(current || {}),
                    activeSessionId: null,
                    phase: "degraded",
                    bridgeWarming: false,
                    retryAllowed: true,
                }));
            }
            return;
        }

        if (type === "error") {
            if (canUseDesktopLiveStreamFallback(desktopLiveStatus) && injectDesktopLiveFallbackStream(sessionId)) {
                setDesktopPreviewState("loading");
                setDesktopPreviewBusy(true);
                setDesktopPreviewError("");
                return;
            }
            desktopLiveUserIntentRef.current = false;
            setDesktopPreviewState("error");
            setDesktopPreviewBusy(false);
            setDesktopPreviewError(normalizeDesktopLiveErrorMessage(payload.message, t));
            setDesktopLiveStatus((current) => ({
                ...(current || {}),
                activeSessionId: null,
                phase: "degraded",
                bridgeWarming: false,
                retryAllowed: true,
            }));
        }
    }, [authorizedFetch, desktopLiveStatus, desktopPreviewSessionId, injectDesktopLiveFallbackStream, maybeStartDesktopPreviewNegotiation, t]);

    const desktopLiveReady = canUseDesktopLivePreview(desktopLiveStatus);
    const desktopLiveConnecting = desktopPreviewBusy || desktopPreviewState === "loading" || (desktopLiveStatus?.bridgeWarming === true && !desktopLiveReady);
    const desktopLiveConnected = desktopPreviewState === "preview";

    const topbarActions = useMemo<PhoneTopbarAction[]>(() => [
        {
            key: "desktop-live",
            onPress: () => void openDesktopPreview(),
            tone: desktopLiveConnected ? "primary" : "default",
            indicatorColor: desktopLiveConnected ? "#10B981" : undefined,
            disabled: desktopPreviewBusy || (desktopLiveStatus?.bridgeStartable === false && !desktopLiveConnecting && !desktopLiveConnected && !desktopLiveReady),
        },
        { key: "rpa", onPress: () => router.push("/rpa" as Href) },
        { key: "voice", onPress: () => void toggleVoiceEnabled() },
        { key: "theme", onPress: () => void toggleThemeMode() },
    ], [desktopLiveConnected, desktopLiveConnecting, desktopLiveReady, desktopLiveStatus?.bridgeStartable, desktopPreviewBusy, openDesktopPreview, toggleThemeMode, toggleVoiceEnabled]);

    useEffect(() => {
        maybeStartDesktopPreviewNegotiation(desktopPreviewSessionId);
    }, [desktopPreviewSessionId, maybeStartDesktopPreviewNegotiation]);

    const loadProjects = useCallback(async () => {
        try {
            const payload = await getProjectsRegistry(authorizedFetch);
            setProjects(Array.isArray(payload.projects) ? payload.projects : []);
            setMainWorkspacePath(typeof payload.mainWorkspacePath === "string" ? payload.mainWorkspacePath : "");
        } catch (error) {
            console.warn("[phone/chat] loadProjects failed", error instanceof Error ? error.message : error);
            setProjects([]);
            setMainWorkspacePath("");
        }
    }, [authorizedFetch]);

    const loadFolderRoots = useCallback(async () => {
        try {
            const payload = await listWorkspaceFolders(authorizedFetch, { maxDepth: 0, maxChildren: 80 });
            const roots = Array.isArray(payload.roots) ? payload.roots : [];
            setFolderRoots(roots);
            setSelectedFolderPath((current) => current || roots[0]?.path || "");
        } catch (error) {
            console.warn("[phone/chat] loadFolderRoots failed", error instanceof Error ? error.message : error);
            setFolderRoots([]);
        }
    }, [authorizedFetch]);

    const mergeFolderNode = useCallback((roots: WorkspaceFolderNode[], nextNode: WorkspaceFolderNode): WorkspaceFolderNode[] => {
        return roots.map((node) => {
            if (node.path === nextNode.path) {
                return { ...node, ...nextNode };
            }
            if (node.children?.length) {
                return { ...node, children: mergeFolderNode(node.children, nextNode) };
            }
            return node;
        });
    }, []);

    const toggleFolderNode = useCallback(async (node: WorkspaceFolderNode) => {
        const nodePath = String(node.path || "").trim();
        if (!nodePath) return;
        if (expandedFolderPaths.has(nodePath)) {
            setExpandedFolderPaths((current) => {
                const next = new Set(current);
                next.delete(nodePath);
                return next;
            });
            return;
        }
        setExpandedFolderPaths((current) => new Set(current).add(nodePath));
        if (node.children && node.children.length > 0) {
            return;
        }
        setLoadingFolderPaths((current) => new Set(current).add(nodePath));
        try {
            const payload = await listWorkspaceFolders(authorizedFetch, { path: nodePath, maxDepth: 1, maxChildren: 80 });
            if (payload.root) {
                setFolderRoots((current) => mergeFolderNode(current, payload.root as WorkspaceFolderNode));
            }
        } catch (error) {
            console.warn("[phone/chat] toggleFolderNode failed", error instanceof Error ? error.message : error);
        } finally {
            setLoadingFolderPaths((current) => {
                const next = new Set(current);
                next.delete(nodePath);
                return next;
            });
        }
    }, [authorizedFetch, expandedFolderPaths, mergeFolderNode]);

    useEffect(() => {
        if (workspaceChooserVisible) {
            void loadFolderRoots();
        }
    }, [loadFolderRoots, workspaceChooserVisible]);

    const loadSessionScope = useCallback(async (conversationId: string) => {
        setScopeLoading(true);
        try {
            const binding = await getSessionScope(authorizedFetch, conversationId);
            setScopeBinding(binding);
        } catch (error) {
            console.warn("[phone/chat] loadSessionScope failed", error instanceof Error ? error.message : error);
            setScopeBinding(null);
        } finally {
            setScopeLoading(false);
        }
    }, [authorizedFetch]);

    const createBoundConversation = useCallback(async (draft: WorkspaceBindingDraft) => {
        if (workspaceChooserBusy) {
            return;
        }
        setWorkspaceChooserBusy(true);
        try {
            let creationPayload: Parameters<typeof createConversation>[1];
            if (draft.kind === "main") {
                if (!mainWorkspacePath) {
                    throw new Error(t("src.screens.chatscreen.the_main_workspace_path_is_not_ready_yet_please_try_again_shortly"));
                }
                creationPayload = {
                    title: "",
                    workspacePath: mainWorkspacePath,
                    scopeHint: "global",
                    scopeMode: "explicit",
                };
            } else {
                const project = availableProjects.find((item) => item.id === draft.projectId);
                if (!project?.id) {
                    throw new Error(t("src.screens.chatscreen.the_selected_project_workspace_does_not_exist_or_is_not_ready_yet"));
                }
                creationPayload = {
                    title: "",
                    projectId: project.id,
                    workspaceId: project.workspaceId,
                    workspacePath: project.workspacePath,
                    scopeHint: project.defaultScope,
                    scopeMode: "explicit",
                };
            }

            const created = await createConversation(authorizedFetch, creationPayload);
            const createdSessionId = created.sessionId || created.id;
            optimisticSeedConversationIdRef.current = createdSessionId;
            activeConversationIdRef.current = createdSessionId;
            setConversations((current) => [created, ...current.filter((item) => (item.sessionId || item.id) !== createdSessionId)]);
            setWorkspaceChooserVisible(false);
            setWorkspaceInfoOpen(false);
            setNewProjectPath("");
            clearNewConversationIntent();
            await setActiveConversationId(createdSessionId);
            await loadSessionScope(createdSessionId);
        } catch (error) {
            Alert.alert(
                t("src.screens.chatscreen.create_conversation_failed"),
                error instanceof Error ? error.message : t("src.screens.chatscreen.unable_to_create_a_conversation_with_the_selected_workspace_binding"),
            );
        } finally {
            setWorkspaceChooserBusy(false);
        }
    }, [authorizedFetch, availableProjects, clearNewConversationIntent, loadSessionScope, mainWorkspacePath, setActiveConversationId, t, workspaceChooserBusy]);

    const handleCreateProjectConversation = useCallback(async () => {
        const nextProjectPath = newProjectPath.trim();
        if (!nextProjectPath || workspaceChooserBusy) {
            return;
        }
        setWorkspaceChooserBusy(true);
        try {
            const createdProject = await createProject(authorizedFetch, { workspacePath: nextProjectPath });
            const createdProjectId = String(createdProject?.id || "").trim();
            if (!createdProjectId) {
                throw new Error(t("src.screens.chatscreen.project_creation_succeeded_but_returned_no_valid_project_id"));
            }
            await loadProjects();
            const createdConversation = await createConversation(authorizedFetch, {
                title: "",
                projectId: createdProjectId,
                workspaceId: createdProject.workspaceId,
                workspacePath: createdProject.workspacePath,
                scopeHint: createdProject.defaultScope,
                scopeMode: "explicit",
            });
            const createdSessionId = createdConversation.sessionId || createdConversation.id;
            optimisticSeedConversationIdRef.current = createdSessionId;
            activeConversationIdRef.current = createdSessionId;
            setConversations((current) => [createdConversation, ...current.filter((item) => (item.sessionId || item.id) !== createdSessionId)]);
            setWorkspaceChooserVisible(false);
            setWorkspaceInfoOpen(false);
            setNewProjectPath("");
            clearNewConversationIntent();
            await setActiveConversationId(createdSessionId);
            await loadSessionScope(createdSessionId);
        } catch (error) {
            Alert.alert(
                t("src.screens.chatscreen.create_project_workspace_failed"),
                error instanceof Error ? error.message : t("src.screens.chatscreen.unable_to_create_a_new_project_workspace"),
            );
        } finally {
            setWorkspaceChooserBusy(false);
        }
    }, [authorizedFetch, clearNewConversationIntent, loadProjects, loadSessionScope, newProjectPath, setActiveConversationId, t, workspaceChooserBusy]);

    const createProjectConversationAtPath = useCallback(async (workspacePath: string) => {
        const nextProjectPath = workspacePath.trim();
        if (!nextProjectPath || workspaceChooserBusy) {
            return;
        }
        setWorkspaceChooserBusy(true);
        try {
            const createdProject = await createProject(authorizedFetch, { workspacePath: nextProjectPath });
            const createdProjectId = String(createdProject?.id || "").trim();
            if (!createdProjectId) {
                throw new Error(t("src.screens.chatscreen.project_creation_succeeded_but_returned_no_valid_project_id"));
            }
            await loadProjects();
            const createdConversation = await createConversation(authorizedFetch, {
                title: "",
                projectId: createdProjectId,
                workspaceId: createdProject.workspaceId,
                workspacePath: createdProject.workspacePath,
                scopeHint: createdProject.defaultScope,
                scopeMode: "explicit",
            });
            const createdSessionId = createdConversation.sessionId || createdConversation.id;
            optimisticSeedConversationIdRef.current = createdSessionId;
            activeConversationIdRef.current = createdSessionId;
            setConversations((current) => [createdConversation, ...current.filter((item) => (item.sessionId || item.id) !== createdSessionId)]);
            setWorkspaceChooserVisible(false);
            setWorkspaceInfoOpen(false);
            setNewProjectPath("");
            setNewFolderName("");
            clearNewConversationIntent();
            await setActiveConversationId(createdSessionId);
            await loadSessionScope(createdSessionId);
        } catch (error) {
            Alert.alert(
                t("src.screens.chatscreen.create_project_workspace_failed"),
                error instanceof Error ? error.message : t("src.screens.chatscreen.unable_to_create_a_new_project_workspace"),
            );
        } finally {
            setWorkspaceChooserBusy(false);
        }
    }, [authorizedFetch, clearNewConversationIntent, loadProjects, loadSessionScope, setActiveConversationId, t, workspaceChooserBusy]);

    const handleCreateFromSelectedFolder = useCallback(async () => {
        if (!selectedFolderPath || workspaceChooserBusy) {
            return;
        }
        const folderName = newFolderName.trim();
        if (folderName) {
            setWorkspaceChooserBusy(true);
            try {
                const createdFolder = await createWorkspaceFolder(authorizedFetch, { parentPath: selectedFolderPath, folderName });
                if (!createdFolder?.path) {
                    throw new Error(t("src.screens.chatscreen.workspace_folder_create_returned_no_path"));
                }
                setSelectedFolderPath(createdFolder.path);
                setNewFolderName("");
                await loadFolderRoots();
                setWorkspaceChooserBusy(false);
                await createProjectConversationAtPath(createdFolder.path);
                return;
            } catch (error) {
                setWorkspaceChooserBusy(false);
                Alert.alert(
                    t("src.screens.chatscreen.create_project_workspace_failed"),
                    error instanceof Error ? error.message : t("src.screens.chatscreen.unable_to_create_a_new_project_workspace"),
                );
                return;
            }
        }
        await createProjectConversationAtPath(selectedFolderPath);
    }, [authorizedFetch, createProjectConversationAtPath, loadFolderRoots, newFolderName, selectedFolderPath, t, workspaceChooserBusy]);

    const loadSupportData = useCallback(async () => {
        const [nextConversations, nextCommands, nextReferences] = await Promise.all([
            listConversations(authorizedFetch),
            listCommandPresets(authorizedFetch).catch(() => []),
            listSkillsAndSubagentFamilies(authorizedFetch).catch(() => ({ skills: [], subagentFamilies: [] })),
        ]);

        setConversations(nextConversations);
        setCommands(nextCommands);
        setSkills(nextReferences.skills);
        setSubagentFamilies(nextReferences.subagentFamilies);
        await loadProjects();

        if (
            activeConversationIdRef.current
            && !nextConversations.some((item) => (item.sessionId || item.id) === activeConversationIdRef.current)
        ) {
            await setActiveConversationId(null);
        }
    }, [authorizedFetch, loadProjects, setActiveConversationId]);

    const applyConversationProjection = useCallback((payload: Partial<ConversationDetail | RealtimeSessionSnapshot | Record<string, unknown>> | null | undefined) => {
        const profileStartedAt = getPerfNowMs();
        const { store, view } = deriveAuthoritativeSessionView(payload);
        if (!view) {
            return;
        }
        const viewWithGovernance = view as typeof view & {
            contextGovernance?: ContextGovernanceView | null;
            contextGovernanceHistory?: ContextGovernanceView[];
        };
        const record = asRecord(payload);
        const nextApprovals = view.approvals as PendingApproval[];
        const nextAskUserInteractions = (Array.isArray(view.askUserInteractions) ? view.askUserInteractions : []) as AskUserInteraction[];
        const hasAskUserPending = nextAskUserInteractions.some((item) => String(item.status || "pending").toLowerCase() === "pending");
        const hasGovernanceApprovalPending = nextApprovals.length > 0;
        const nextTodos = view.todos?.items || [];
        const nextRuntimeEvents = view.runtimeTimeline;
        const nextRuntimeStatus = view.runtimeStatus;
        const nextSummary = asRecord(view.summary);
        const nextCurrentRun = asRecord(view.currentRun);
        const workflowProjection = asRecord(view.workflowProjection);
        const nextRunId = String(
            nextCurrentRun.id
            || nextCurrentRun.runId
            || nextSummary.currentRunId
            || record.currentRunId
            || "",
        ).trim() || undefined;
        const nextLastRunId = String(
            nextSummary.lastRunId
            || record.lastRunId
            || nextRunId
            || "",
        ).trim() || undefined;
        const nextEndedAt = String(
            nextSummary.endedAt
            || record.endedAt
            || nextCurrentRun.finished_at
            || nextCurrentRun.completed_at
            || "",
        ).trim() || undefined;
        const nextHistorySortAt = String(
            nextSummary.historySortAt
            || record.historySortAt
            || nextSummary.createdAt
            || record.createdAt
            || "",
        ).trim() || undefined;
        const nextLastActivityAt = String(
            nextSummary.lastActivityAt
            || record.lastActivityAt
            || record.updatedAt
            || record.updated_at
            || record.createdAt
            || "",
        ).trim() || undefined;
        const overlayPatch = buildConversationOverlayPatch(payload as Partial<ConversationDetail>);

        setApprovals(nextApprovals);
        setAskUserInteractions(nextAskUserInteractions);
        setTodos(nextTodos as SessionTodoItem[]);
        todosRef.current = nextTodos as SessionTodoItem[];
        setContextReferences(view.contextReferences || []);
        setContextGovernance((current) => viewWithGovernance.contextGovernance || current);
        setContextGovernanceHistory((current) =>
            Array.isArray(viewWithGovernance.contextGovernanceHistory) && viewWithGovernance.contextGovernanceHistory.length > 0
                ? viewWithGovernance.contextGovernanceHistory
                : current,
        );
        if (Array.isArray(view.processes) && view.processes.length > 0) {
            applySessionProcessSurface(view.processes);
        }
        setRuntimeTimeline(normalizePhoneRuntimeTimeline(nextRuntimeEvents));
        const nextRuntime: RuntimeSummary = {
            status: String(
                hasAskUserPending
                ? "waiting_input"
                : hasGovernanceApprovalPending
                    ? "waiting_approval"
                    :
                nextRuntimeStatus
                || nextCurrentRun.status
                || workflowProjection.runtimeStatus
                || "idle",
            ).trim() || "idle",
            latestSeq: Number(store.latestSeq || 0) || 0,
            runId: nextRunId,
            label: typeof nextSummary.currentStepTitle === "string"
                ? nextSummary.currentStepTitle
                : typeof nextSummary.lastRuntimeSummary === "string"
                    ? nextSummary.lastRuntimeSummary
                    : undefined,
        };
        setRuntime(nextRuntime);
        runtimeRef.current = nextRuntime;
        latestSeqRef.current = nextRuntime.latestSeq;
        const activeConversationId = String(activeConversationIdRef.current || "").trim();
        if (activeConversationId) {
            setConversations((current) => {
                const index = current.findIndex((item) => (item.sessionId || item.id) === activeConversationId);
                if (index < 0) {
                    return current;
                }
                const currentConversation = current[index];
                const merged = mergeSessionHistoryOverlay(currentConversation, {
                    ...overlayPatch,
                    historySortAt: nextHistorySortAt || currentConversation.historySortAt,
                    lastActivityAt: nextLastActivityAt || undefined,
                    currentRunId: nextRunId,
                    lastRunId: nextLastRunId,
                    endedAt: nextEndedAt,
                    controls: (record.controls as ConversationSummary["controls"]) || overlayPatch.controls || currentConversation.controls,
                    recoverable: typeof record.recoverable === "boolean"
                        ? record.recoverable
                        : overlayPatch.recoverable ?? currentConversation.recoverable,
                });
                if (JSON.stringify(currentConversation) === JSON.stringify(merged)) {
                    return current;
                }
                const next = [...current];
                next[index] = merged;
                return sortSessionHistory(next);
            });
        }
        if ((nextTodos as SessionTodoItem[]).length > 0) {
            patchAssistantTaskShell(nextTodos as SessionTodoItem[], {
                phase: nextRuntime.status === "waiting_input"
                    ? "waiting_input"
                    : nextRuntime.status === "completed"
                        ? "settling"
                        : undefined,
                runId: nextRuntime.runId,
                createIfMissing: false,
            });
        }
        const elapsedMs = Math.round(getPerfNowMs() - profileStartedAt);
        if (__DEV__ && (elapsedMs >= 16 || nextRuntimeEvents.length >= 40)) {
            debugPerfTrace("projection-state", {
                elapsedMs,
                latestSeq: nextRuntime.latestSeq,
                runtimeEventCount: nextRuntimeEvents.length,
                messageCount: Array.isArray(asRecord(view).messages) ? (asRecord(view).messages as unknown[]).length : 0,
                processCount: Array.isArray(view.processes) ? view.processes.length : 0,
                engineProfile: readPayloadProfile(payload),
            });
        }
    }, [applySessionProcessSurface, patchAssistantTaskShell]);

    const applyRealtimeSnapshotPayload = useCallback((payload: Partial<ConversationDetail | RealtimeSessionSnapshot | Record<string, unknown>> | null | undefined) => {
        const profileStartedAt = getPerfNowMs();
        const payloadBytes = measureJsonBytes(payload);
        const snapshotMessages = extractSnapshotMessages(payload);
        const snapshotSeq = buildSnapshotSequence(payload);
        const snapshotQueuedMessages = extractQueuedMessages(payload);
        const targetConversationId = String(activeConversationIdRef.current || "").trim();
        if (isLegacyChatUnsupportedPayload(payload)) {
            setLegacyChatUnsupported(true);
        }
        if (snapshotQueuedMessages) {
            setQueuedMessages(snapshotQueuedMessages);
        }
        if (snapshotMessages) {
            const normalizedSnapshot = normalizeMessagesForState(snapshotMessages);
            const snapshotFingerprint = buildMessagesFingerprint(normalizedSnapshot);
            const snapshotOlderThanApplied = snapshotSeq > 0 && snapshotSeq < lastAppliedSnapshotSeqRef.current;
            lastRealtimeSnapshotAtRef.current = Date.now();

            if (snapshotOlderThanApplied && snapshotFingerprint === lastAppliedSnapshotFingerprintRef.current) {
                applyConversationProjection(payload);
                return;
            }

            lastAppliedSnapshotFingerprintRef.current = snapshotFingerprint;
            if (snapshotSeq > 0) {
                lastAppliedSnapshotSeqRef.current = Math.max(lastAppliedSnapshotSeqRef.current, snapshotSeq);
            }

            setMessages((current) => {
                const preserveOptimisticLocalState = Boolean(
                    targetConversationId
                    && messageConversationIdRef.current === targetConversationId
                    && (
                        sendingRef.current
                        || optimisticSeedConversationIdRef.current === targetConversationId
                        || hasPreservableLocalAssistantState(current)
                    )
                );
                const normalized = mergeAuthoritativeSnapshotMessages(
                    current,
                    normalizedSnapshot,
                    preserveOptimisticLocalState,
                );
                const beforeAssistant = describeLatestAssistantMessage(current);
                const afterAssistant = describeLatestAssistantMessage(normalized);
                if (__DEV__ && beforeAssistant && afterAssistant) {
                    debugRealtimeTrace("message-merge", {
                        beforeAssistant,
                        afterAssistant,
                        snapshotSeq,
                        preserveOptimisticLocalState,
                    });
                }
                const fingerprint = buildMessagesFingerprint(normalized);
                if (fingerprint === lastMessageFingerprintRef.current) {
                    return current;
                }
                lastMessageFingerprintRef.current = fingerprint;
                realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
                    normalized,
                    PHONE_STREAM_LIFECYCLE_OPTIONS,
                );
                messagesRef.current = normalized;
                messageConversationIdRef.current = targetConversationId || null;
                return normalized;
            });
        }
        if (snapshotSeq > 0) {
            latestSeqRef.current = Math.max(latestSeqRef.current, snapshotSeq);
            lastAppliedSnapshotSeqRef.current = Math.max(lastAppliedSnapshotSeqRef.current, snapshotSeq);
            lastRealtimeSnapshotAtRef.current = Date.now();
        }
        applyConversationProjection(payload);
        const elapsedMs = Math.round(getPerfNowMs() - profileStartedAt);
        if (__DEV__ && (elapsedMs >= 24 || payloadBytes >= 120000)) {
            debugPerfTrace("snapshot-apply", {
                elapsedMs,
                payloadBytes,
                snapshotSeq,
                snapshotMessageCount: Array.isArray(snapshotMessages) ? snapshotMessages.length : 0,
                runtimeEventCount: countPayloadRuntimeEvents(payload),
                queuedMessageCount: Array.isArray(snapshotQueuedMessages) ? snapshotQueuedMessages.length : 0,
                engineProfile: readPayloadProfile(payload),
            });
        }
    }, [applyConversationProjection]);

    const scheduleRealtimeSnapshotRefresh = useCallback((conversationId?: string | null, options?: { force?: boolean }) => {
        const targetConversationId = String(conversationId || activeConversationIdRef.current || "").trim();
        if (!targetConversationId || activeConversationIdRef.current !== targetConversationId) {
            return;
        }
        const force = options?.force === true;
        if (!force && Date.now() - lastRealtimeSnapshotAtRef.current < REALTIME_SNAPSHOT_FALLBACK_GRACE_MS) {
            return;
        }

        const runRefresh = async () => {
            if (realtimeSnapshotInflightRef.current) {
                realtimeSnapshotPendingRef.current = true;
                return;
            }
            realtimeSnapshotInflightRef.current = true;
            realtimeSnapshotPendingRef.current = false;
            try {
                if (!force && lastRealtimeSnapshotAtRef.current > scheduledAt) {
                    return;
                }
                if (!force && Date.now() - lastRealtimeSnapshotAtRef.current < REALTIME_SNAPSHOT_FALLBACK_GRACE_MS) {
                    return;
                }
                const fetchStartedAt = getPerfNowMs();
                const snapshot = await getRealtimeSnapshot(authorizedFetch, targetConversationId);
                if (__DEV__) {
                    const elapsedMs = Math.round(getPerfNowMs() - fetchStartedAt);
                    const payloadBytes = measureJsonBytes(snapshot);
                    if (elapsedMs >= 200 || payloadBytes >= 120000) {
                        debugPerfTrace("snapshot-fetch", {
                            elapsedMs,
                            payloadBytes,
                            latestSeq: buildSnapshotSequence(snapshot),
                            runtimeEventCount: countPayloadRuntimeEvents(snapshot),
                            engineProfile: readPayloadProfile(snapshot),
                        });
                    }
                }
                if (activeConversationIdRef.current === targetConversationId) {
                    applyRealtimeSnapshotPayload(snapshot);
                }
            } catch (error) {
                console.warn("[phone] realtime snapshot refresh failed:", error);
            } finally {
                realtimeSnapshotInflightRef.current = false;
                if (realtimeSnapshotPendingRef.current && activeConversationIdRef.current === targetConversationId) {
                    realtimeSnapshotPendingRef.current = false;
                    realtimeSnapshotTimerRef.current = setTimeout(() => {
                        realtimeSnapshotTimerRef.current = null;
                        void runRefresh();
                    }, force ? REALTIME_SNAPSHOT_FALLBACK_FORCE_DEBOUNCE_MS : REALTIME_SNAPSHOT_FALLBACK_DEBOUNCE_MS);
                }
            }
        };

        if (realtimeSnapshotTimerRef.current) {
            realtimeSnapshotPendingRef.current = true;
            return;
        }

        const scheduledAt = Date.now();
        realtimeSnapshotTimerRef.current = setTimeout(() => {
            realtimeSnapshotTimerRef.current = null;
            void runRefresh();
        }, force ? REALTIME_SNAPSHOT_FALLBACK_FORCE_DEBOUNCE_MS : REALTIME_SNAPSHOT_FALLBACK_DEBOUNCE_MS);
    }, [applyRealtimeSnapshotPayload, authorizedFetch]);

    const upsertQueuedMessage = useCallback((item: QueuedChatMessage | null | undefined) => {
        const id = String(item?.id || "").trim();
        if (!id || !item) {
            return;
        }
        const state = String(item.state || "pending").trim();
        setQueuedMessages((current) => {
            const without = current.filter((candidate) => candidate.id !== id);
            if (["cancelled", "consumed", "injected", "sent", "completed"].includes(state)) {
                return without;
            }
            return [...without, { ...item, state }].sort((a, b) => Number(a.ordinal || 0) - Number(b.ordinal || 0));
        });
    }, []);

    const handleRealtimeEvent = useCallback((eventName: string, payload: unknown) => {
        const upstreamDiagnostics = readRealtimeDiagnostics(payload);
        const phoneReceivedAtMs = Date.now();
        const phoneReceivedAt = new Date(phoneReceivedAtMs).toISOString();
        if (eventName === "snapshot" && payload && typeof payload === "object") {
            pendingRealtimeRenderDiagnosticRef.current = {
                eventName,
                adminForwardedAt: upstreamDiagnostics.adminForwardedAt,
                phoneReceivedAt,
                eventKind: "snapshot",
                latestSeq: buildSnapshotSequence(payload as RealtimeSessionSnapshot),
            };
            debugRealtimeTrace("receive", {
                eventName,
                adminForwardedAt: upstreamDiagnostics.adminForwardedAt,
                phoneReceivedAt,
                latestSeq: buildSnapshotSequence(payload as RealtimeSessionSnapshot),
            });
            applyRealtimeSnapshotPayload(payload as RealtimeSessionSnapshot);
            return;
        }

        const normalized = normalizePhoneRealtimeEvent(payload, locale);
        if (!normalized) {
            return;
        }

        const dedupKey = buildRealtimeEventDedupKey(normalized);
        if (seenRealtimeEventKeysRef.current.has(dedupKey)) {
            return;
        }
        if (normalized.seq && normalized.seq < latestSeqRef.current) {
            return;
        }
        seenRealtimeEventKeysRef.current.add(dedupKey);
        if (seenRealtimeEventKeysRef.current.size > 2048) {
            const first = seenRealtimeEventKeysRef.current.values().next();
            if (!first.done) {
                seenRealtimeEventKeysRef.current.delete(first.value);
            }
        }
        if (normalized.seq) {
            latestSeqRef.current = Math.max(latestSeqRef.current, normalized.seq);
        }
        let streamMetricKey = "";
        if (__DEV__ && (normalized.type === "text_chunk" || normalized.type === "reasoning_chunk")) {
            const runId = String(normalized.run_id || upstreamDiagnostics.runId || "unknown-run").trim();
            const transport = String(upstreamDiagnostics.transport || normalized.data?.transport || "unknown-transport").trim();
            streamMetricKey = `${runId}:${transport}`;
            const stats = streamLatencyStatsRef.current.get(streamMetricKey) || {
                count: 0,
                deltaChars: [],
                interDeltaMs: [],
                proxyLagMs: [],
                clientCommitLagMs: [],
                renderLagMs: [],
            };
            const providerDeltaAtMs = toEpochMs(upstreamDiagnostics.providerDeltaAtMs) ?? toEpochMs(upstreamDiagnostics.providerDeltaAt);
            const proxyFlushAtMs = toEpochMs(upstreamDiagnostics.proxyFlushAt) ?? toEpochMs(upstreamDiagnostics.adminForwardedAt);
            if (stats.count === 0) {
                stats.firstProviderDeltaAtMs = providerDeltaAtMs;
                stats.firstPhoneReceiveAtMs = phoneReceivedAtMs;
            }
            if (stats.lastPhoneReceiveAtMs !== undefined) {
                stats.interDeltaMs.push(Math.max(0, phoneReceivedAtMs - stats.lastPhoneReceiveAtMs));
            }
            if (proxyFlushAtMs !== undefined) {
                stats.proxyLagMs.push(Math.max(0, phoneReceivedAtMs - proxyFlushAtMs));
            }
            stats.deltaChars.push(Number(upstreamDiagnostics.deltaChars || String(normalized.content || "").length) || 0);
            stats.lastPhoneReceiveAtMs = phoneReceivedAtMs;
            stats.count += 1;
            streamLatencyStatsRef.current.set(streamMetricKey, stats);
        }
        pendingRealtimeRenderDiagnosticRef.current = {
            eventName,
            eventType: normalized.type,
            normalizedName: normalized.name,
            topic: normalized.topic,
            seq: normalized.seq,
            engineEmittedAt: normalized.ts,
            adminForwardedAt: upstreamDiagnostics.adminForwardedAt,
            proxyFlushAt: upstreamDiagnostics.proxyFlushAt,
            providerDeltaAt: upstreamDiagnostics.providerDeltaAt,
            canonicalEventAt: upstreamDiagnostics.canonicalEventAt,
            engineYieldAt: upstreamDiagnostics.engineYieldAt,
            phoneReceivedAt,
            streamMetricKey,
        };
        debugRealtimeTrace("receive", {
            eventName,
            type: normalized.type,
            name: normalized.name,
            topic: normalized.topic,
            seq: normalized.seq,
            engineEmittedAt: normalized.ts,
            adminForwardedAt: upstreamDiagnostics.adminForwardedAt,
            phoneReceivedAt,
        });

        const nowMs = getEngineNowMs();
        const shouldFallbackRefresh = shouldAuthoritativelyRefreshOnRuntimeEvent(normalized);
        const normalizedToolName = String(
            normalized.tool?.toolName
            || normalized.data?.toolName
            || normalized.data?.tool_name
            || "",
        ).trim();
        const isTodoToolEvent = (normalized.type === "tool_start" || normalized.type === "tool_result")
            && TODO_TOOL_NAMES.has(normalizedToolName);

        if (isTodoToolEvent) {
            const nextTodos = applyTodoToolEvent(todosRef.current, normalized) || todosRef.current;
            todosRef.current = nextTodos;
            setTodos(nextTodos);

            const taskProgress = buildAssistantTaskProgressPatch(nextTodos, {
                phase: "tooling",
            });
            const taskLabel = taskProgress?.label || tRef.current("src.screens.chatscreen.task_progress_updated");
            const taskSubtitle = taskProgress?.subtitle || tRef.current("src.screens.chatscreen.updating_task_progress");

            patchAssistantTaskShell(nextTodos, {
                phase: taskProgress?.phase || "tooling",
                label: taskLabel,
                subtitle: taskSubtitle,
                runId: normalized.run_id,
                createIfMissing: true,
            });

            appendRuntimeTimeline(
                buildRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.runtimeId || "chat")) || "chat",
                    normalized.topic || `todo.${normalizedToolName}`,
                    `${taskLabel}${taskSubtitle ? ` · ${taskSubtitle}` : ""}`,
                    {
                        id: normalized.event_id || `todo:${normalizedToolName}:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        timestamp: normalized.ts || nowMs,
                        actorLabel: normalized.actorLabel || tRef.current("src.components.chat.messagebubble.supervisor"),
                        status: "running",
                        kind: "progress",
                    },
                ),
            );
            setRuntime((current) => ({
                status: "running",
                latestSeq: normalized.seq || current.latestSeq,
                runId: normalized.run_id || current.runId,
                label: taskLabel,
            }));
            if (shouldFallbackRefresh) {
                scheduleRealtimeSnapshotRefresh(normalized.session_id || normalized.conversation_id || activeConversationIdRef.current);
            }
            return;
        }

        if (shouldApplyRuntimeEventToMessage(normalized)) {
            const shouldFlushImmediately = normalized.type === "agent_start"
                || normalized.type === "tool_start"
                || normalized.type === "tool_result"
                || normalized.type === "done"
                || normalized.type === "error"
                || normalized.name === "ask_user"
                || normalized.name === "approval_requested"
                || normalized.name === "human_guidance"
                || normalized.name === "artifact_recorded";
            queueRuntimeMessageEvent(normalized, shouldFlushImmediately);
        }

        if (normalized.name === "human_guidance") {
            const terminalQueueState = String(normalized.data?.state || "").trim().toLowerCase();
            const eventQueueMessage = normalized.data?.queueMessage as { id?: unknown } | undefined;
            const terminalQueueId = String(
                normalized.data?.queueMessageId
                || normalized.data?.guidanceQueueMessageId
                || eventQueueMessage?.id
                || "",
            ).trim();
            if (
                terminalQueueId
                && (
                    ["human_guidance.injected", "human_guidance.consumed", "human_guidance.cancelled"].includes(String(normalized.topic || "").trim())
                    || ["injected", "consumed", "cancelled"].includes(terminalQueueState)
                )
            ) {
                setQueuedMessages((current) => current.filter((item) => item.id !== terminalQueueId));
            }
            const queuePayload = normalized.data?.queueMessage;
            const queueMessage = queuePayload && typeof queuePayload === "object"
                ? queuePayload as QueuedChatMessage
                : null;
            if (queueMessage) {
                upsertQueuedMessage(queueMessage);
            }
            const guidanceSummary = String(
                normalized.data?.summary
                || queueMessage?.content
                || normalized.content
                || tRef.current("src.screens.chatscreen.mid_run_guidance_updated"),
            ).trim();
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                    "chat",
                    normalized.topic || "human_guidance.updated",
                    guidanceSummary,
                    {
                        id: normalized.event_id || `human-guidance:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "governance",
                        timestamp: normalized.ts || nowMs,
                        actorLabel: normalized.actorLabel || tRef.current("src.components.chat.messagebubble.you"),
                        status: String(queueMessage?.state || normalized.data?.state || "pending"),
                    },
                ),
            );
            if (shouldFallbackRefresh) {
                scheduleRealtimeSnapshotRefresh(
                    normalized.session_id || normalized.conversation_id || activeConversationIdRef.current,
                    { force: normalized.topic === "human_guidance.injected" },
                );
            }
            return;
        }

        if (normalized.type === "agent_start") {
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.runtimeId || "chat")) || "chat",
                    normalized.topic || "agent.started",
                    String(normalized.actorLabel || tRef.current("src.screens.chatscreen.supervisor_started_working")),
                    {
                        id: normalized.event_id || `agent:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "handoff",
                        timestamp: normalized.ts || nowMs,
                        actorLabel: normalized.actorLabel,
                        status: "running",
                    },
                ),
            );
            setRuntime((current) => ({
                status: "running",
                latestSeq: normalized.seq || current.latestSeq,
                runId: normalized.run_id || current.runId,
                label: normalized.actorLabel || tRef.current("src.screens.chatscreen.started"),
            }));
            patchAssistantTaskShell(todosRef.current, {
                phase: "task_planning",
                label: tRef.current("src.screens.chatscreen.task_started"),
                subtitle: tRef.current("src.screens.chatscreen.preparing_context_and_response"),
                runId: normalized.run_id,
                createIfMissing: true,
            });
            if (shouldFallbackRefresh) {
                scheduleRealtimeSnapshotRefresh(normalized.session_id || normalized.conversation_id || activeConversationIdRef.current);
            }
            return;
        }

        if (normalized.type === "reasoning_chunk") {
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildPhaseRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.runtimeId || "chat")) || "chat",
                    "reasoning",
                    String(normalized.content || tRef.current("src.screens.chatscreen.thinking")).trim() || tRef.current("src.screens.chatscreen.thinking"),
                    {
                        runId: normalized.run_id,
                        seq: normalized.seq,
                        timestamp: normalized.ts || nowMs,
                        actorLabel: normalized.actorLabel,
                        status: "running",
                        topic: normalized.topic || "run.reasoning.delta",
                    },
                ),
            );
            setRuntime((current) => ({
                status: "running",
                latestSeq: normalized.seq || current.latestSeq,
                runId: normalized.run_id || current.runId,
                label: tRef.current("src.screens.chatscreen.thinking"),
            }));
            patchAssistantTaskShell(todosRef.current, {
                phase: "task_planning",
                label: tRef.current("src.screens.chatscreen.planning_task"),
                subtitle: tRef.current("src.screens.chatscreen.analyzing_steps_and_execution_order"),
                runId: normalized.run_id,
                createIfMissing: true,
            });
            return;
        }

        if (normalized.type === "text_chunk") {
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildPhaseRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.runtimeId || "chat")) || "chat",
                    "streaming",
                    String(normalized.content || tRef.current("src.screens.chatscreen.replying")).trim() || tRef.current("src.screens.chatscreen.replying"),
                    {
                        runId: normalized.run_id,
                        seq: normalized.seq,
                        timestamp: normalized.ts || nowMs,
                        actorLabel: normalized.actorLabel,
                        status: "running",
                        topic: normalized.topic || "run.text.delta",
                    },
                ),
            );
            setRuntime((current) => ({
                status: "running",
                latestSeq: normalized.seq || current.latestSeq,
                runId: normalized.run_id || current.runId,
                label: tRef.current("src.screens.chatscreen.replying"),
            }));
            patchAssistantTaskShell(todosRef.current, {
                phase: "streaming",
                label: tRef.current("src.screens.chatscreen.replying"),
                subtitle: tRef.current("src.screens.chatscreen.streaming_the_response"),
                runId: normalized.run_id,
                createIfMissing: true,
            });
            return;
        }

        if (normalized.type === "tool_start" || normalized.type === "tool_result") {
            const toolLabel = String(
                normalized.tool?.toolName
                || normalized.data?.toolName
                || normalized.data?.tool_name
                || normalized.data?.label
                || normalized.content
                || tRef.current("src.components.chat.contentdispatcher.tool_call"),
            ).trim();
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.runtimeId || normalized.topic || "chat")) || "chat",
                    normalized.topic || (normalized.type === "tool_start" ? "tool.started" : "tool.finished"),
                    normalized.type === "tool_start"
                        ? tRef.current("src.screens.chatscreen.starting_tool", { toolLabel })
                        : tRef.current("src.screens.chatscreen.finished_tool", { toolLabel }),
                    {
                        id: normalized.event_id || `${normalized.type}:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "tool",
                        timestamp: normalized.ts || nowMs,
                        actorLabel: normalized.actorLabel,
                    },
                ),
            );
            setRuntime((current) => ({
                status: "running",
                latestSeq: normalized.seq || current.latestSeq,
                runId: normalized.run_id || current.runId,
                label: toolLabel || current.label,
            }));
            patchAssistantTaskShell(todosRef.current, {
                phase: "tooling",
                label: toolLabel || tRef.current("src.screens.chatscreen.running_tool"),
                subtitle: normalized.type === "tool_start"
                    ? tRef.current("src.screens.chatscreen.task_is_calling_a_tool")
                    : tRef.current("src.screens.chatscreen.tool_returned_and_next_step_is_being_prepared"),
                runId: normalized.run_id,
                createIfMissing: true,
            });
            if (shouldFallbackRefresh) {
                scheduleRealtimeSnapshotRefresh(normalized.session_id || normalized.conversation_id || activeConversationIdRef.current);
            }
            return;
        }

        if (normalized.topic === "chat.task_planning_mode.enabled") {
            appendRuntimeTimeline(
                buildRuntimeTimelineEntry(
                    "chat",
                    normalized.topic,
                    tRef.current("src.screens.chatscreen.task_planning_preference_enabled"),
                    {
                        id: normalized.event_id || `task-planning-enabled:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "progress",
                        timestamp: normalized.ts || nowMs,
                        actorLabel: normalized.actorLabel || tRef.current("src.components.chat.messagebubble.supervisor"),
                        status: "running",
                    },
                ),
            );
            patchAssistantTaskShell(todosRef.current, {
                phase: "task_planning",
                label: tRef.current("src.screens.chatscreen.task_planning_preference_enabled_2"),
                subtitle: tRef.current("src.screens.chatscreen.multi_step_tasks_will_more_readily_use_todo_planning"),
                runId: normalized.run_id,
                createIfMissing: true,
            });
            return;
        }

        if (normalized.topic === "chat.task_planning_mode.decided") {
            const usedTodos = Boolean(normalized.data?.usedTodos);
            const summary = String(
                normalized.content
                || normalized.data?.summary
                || (usedTodos
                    ? tRef.current("src.screens.chatscreen.task_planning_preference_entered_the_todo_lane")
                    : tRef.current("src.screens.chatscreen.task_planning_was_enabled_but_this_run_completed_as_a_single_step_task")),
            ).trim();
            const subtitle = String(
                normalized.data?.message
                || (usedTodos
                    ? tRef.current("src.screens.chatscreen.this_run_created_or_updated_todo_items_and_progressed_through_a_task_plan")
                    : tRef.current("src.screens.chatscreen.this_run_did_not_enter_the_todo_lane_which_usually_means_the_model_judged_continuous_tracking_unnecessary")),
            ).trim();
            appendRuntimeTimeline(
                buildRuntimeTimelineEntry(
                    "chat",
                    normalized.topic,
                    `${summary}${subtitle ? ` · ${subtitle}` : ""}`,
                    {
                        id: normalized.event_id || `task-planning-decision:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "progress",
                        timestamp: normalized.ts || nowMs,
                        actorLabel: normalized.actorLabel || tRef.current("src.components.chat.messagebubble.supervisor"),
                        status: "completed",
                    },
                ),
            );
            patchAssistantTaskShell(todosRef.current, {
                phase: usedTodos ? "tooling" : "settling",
                label: summary,
                subtitle,
                runId: normalized.run_id,
                createIfMissing: true,
            });
            return;
        }

        if (normalized.type === "done") {
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.runtimeId || "chat")) || "chat",
                    normalized.topic || "run.completed",
                    String(normalized.content || tRef.current("src.screens.chatscreen.run_completed")),
                    {
                        id: normalized.event_id || `done:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "progress",
                        timestamp: normalized.ts || nowMs,
                        actorLabel: normalized.actorLabel,
                        status: "completed",
                    },
                ),
            );
            setRuntime((current) => ({
                ...current,
                status: current.status === "waiting_approval" || current.status === "waiting_input" ? current.status : "completed",
                latestSeq: normalized.seq || current.latestSeq,
                runId: normalized.run_id || current.runId,
                label: tRef.current("src.screens.chatscreen.completed"),
            }));
            patchAssistantTaskShell(todosRef.current, {
                phase: "settling",
                label: tRef.current("src.screens.chatscreen.task_completed"),
                subtitle: tRef.current("src.screens.chatscreen.preparing_final_response_and_artifacts"),
                runId: normalized.run_id,
                createIfMissing: false,
            });
            if (shouldFallbackRefresh) {
                scheduleRealtimeSnapshotRefresh(
                    normalized.session_id || normalized.conversation_id || activeConversationIdRef.current,
                    { force: true },
                );
            }
            return;
        }

        if (normalized.type === "error") {
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.runtimeId || "chat")) || "chat",
                    normalized.topic || "run.failed",
                    String(normalized.error || normalized.content || tRef.current("src.screens.chatscreen.run_failed")),
                    {
                        id: normalized.event_id || `error:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "progress",
                        timestamp: normalized.ts || nowMs,
                        actorLabel: normalized.actorLabel,
                        status: "failed",
                    },
                ),
            );
            setRuntime((current) => ({
                ...current,
                status: "failed",
                latestSeq: normalized.seq || current.latestSeq,
                runId: normalized.run_id || current.runId,
                label: tRef.current("src.screens.chatscreen.failed"),
            }));
            patchAssistantTaskShell(todosRef.current, {
                phase: "error",
                label: tRef.current("src.screens.chatscreen.task_failed"),
                subtitle: String(normalized.error || normalized.content || tRef.current("src.screens.chatscreen.an_error_interrupted_the_run")),
                runId: normalized.run_id,
                createIfMissing: true,
            });
            if (shouldFallbackRefresh) {
                scheduleRealtimeSnapshotRefresh(
                    normalized.session_id || normalized.conversation_id || activeConversationIdRef.current,
                    { force: true },
                );
            }
            return;
        }

        if (normalized.name === "ask_user") {
            if (normalized.topic === "ask_user.resolved") {
                setAskUserInteractions((current) => current.filter((item) => String(item.id || item.interactionId || "") !== String(normalized.data?.interactionId || normalized.data?.id || "")));
                setRuntime((current) => ({
                    ...current,
                    status: "running",
                    latestSeq: normalized.seq || current.latestSeq,
                    runId: normalized.run_id || current.runId,
                    label: tRef.current("src.screens.chatscreen.continuing"),
                }));
                patchAssistantTaskShell(todosRef.current, {
                    phase: "tooling",
                    label: tRef.current("src.screens.chatscreen.continuing"),
                    subtitle: tRef.current("src.screens.chatscreen.your_answer_was_received_and_the_task_is_continuing"),
                    runId: normalized.run_id,
                    createIfMissing: false,
                });
                appendRuntimeTimeline(
                    buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                        normalizePhoneRuntimeId(String(normalized.runtimeId || normalized.topic || "chat")) || "chat",
                        normalized.topic || "ask_user.resolved",
                        tRef.current("src.screens.chatscreen.input_received_continuing"),
                        {
                            id: normalized.event_id || `ask-user-resolved:${normalized.seq || Date.now()}`,
                            seq: normalized.seq,
                            kind: "governance",
                            timestamp: normalized.ts || nowMs,
                            actorLabel: normalized.actorLabel || tRef.current("src.components.chat.messagebubble.supervisor"),
                        },
                    ),
                );
                if (shouldFallbackRefresh) {
                    scheduleRealtimeSnapshotRefresh(normalized.session_id || normalized.conversation_id || activeConversationIdRef.current);
                }
                return;
            }
            const interaction = buildAskUserInteractionFromEvent(normalized);
            if (interaction) {
                setAskUserInteractions((current) => upsertAskUserInteraction(current, interaction));
                setRuntime((current) => ({
                    ...current,
                    status: "waiting_input",
                    latestSeq: normalized.seq || current.latestSeq,
                    runId: normalized.run_id || current.runId,
                    label: tRef.current("src.screens.chatscreen.waiting_for_your_answer"),
                }));
                patchAssistantTaskShell(todosRef.current, {
                    phase: "waiting_input",
                    label: tRef.current("src.screens.chatscreen.waiting_for_your_answer"),
                    subtitle: typeof normalized.data?.question === "string"
                        ? normalized.data.question
                        : tRef.current("src.screens.chatscreen.please_provide_the_requested_input"),
                    runId: normalized.run_id,
                    createIfMissing: true,
                });
                appendRuntimeTimeline(
                    buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                        normalizePhoneRuntimeId(String(normalized.runtimeId || normalized.topic || normalized.data?.topic || "chat")) || "chat",
                        normalized.topic || "ask_user.requested",
                        String(normalized.data?.question || tRef.current("src.screens.chatscreen.waiting_for_your_answer")),
                        {
                            id: normalized.event_id || `ask-user:${normalized.seq || Date.now()}`,
                            seq: normalized.seq,
                            kind: "governance",
                            timestamp: normalized.ts || nowMs,
                            actorLabel: normalized.actorLabel || tRef.current("src.components.chat.messagebubble.supervisor"),
                        },
                    ),
                );
            }
            if (shouldFallbackRefresh) {
                scheduleRealtimeSnapshotRefresh(normalized.session_id || normalized.conversation_id || activeConversationIdRef.current);
            }
            return;
        }

        if (normalized.name === "approval_requested") {
            const approval = buildApprovalFromEvent(normalized);
            if (approval) {
                const approvalId = String(approval.id || approval.approval_id || "").trim();
                if (approvalId && recentlyResolvedApprovalIdsRef.current.has(approvalId)) {
                    if (shouldFallbackRefresh) {
                        scheduleRealtimeSnapshotRefresh(normalized.session_id || normalized.conversation_id || activeConversationIdRef.current);
                    }
                    return;
                }
                setApprovals((current) => upsertApproval(current, approval));
                setRuntime((current) => ({
                    ...current,
                    status: "waiting_approval",
                    latestSeq: normalized.seq || current.latestSeq,
                    runId: normalized.run_id || current.runId,
                    label: tRef.current("src.screens.chatscreen.waiting_for_approval"),
                }));
                patchAssistantTaskShell(todosRef.current, {
                    phase: "tooling",
                    label: tRef.current("src.screens.chatscreen.waiting_for_approval"),
                    subtitle: typeof normalized.data?.question === "string"
                        ? normalized.data.question
                        : tRef.current("src.screens.chatscreen.approval_is_required_to_continue"),
                    runId: normalized.run_id,
                    createIfMissing: true,
                });
                appendRuntimeTimeline(
                    buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                        normalizePhoneRuntimeId(String(normalized.runtimeId || normalized.topic || normalized.data?.topic || "automation")) || "automation",
                        normalized.topic || "approval.requested",
                        String(
                            normalized.data?.question
                            || tRef.current("src.screens.chatscreen.waiting_for_approval"),
                        ),
                        {
                            id: normalized.event_id || `approval:${normalized.seq || Date.now()}`,
                            seq: normalized.seq,
                            kind: "governance",
                            timestamp: normalized.ts || nowMs,
                            actorLabel: normalized.actorLabel || tRef.current("src.screens.chatscreen.automation"),
                        },
                    ),
                );
            }
            if (shouldFallbackRefresh) {
                scheduleRealtimeSnapshotRefresh(normalized.session_id || normalized.conversation_id || activeConversationIdRef.current);
            }
            return;
        }

        if (normalized.type === "custom_event" && normalized.name === "artifact_recorded") {
            patchAssistantTaskShell(todosRef.current, {
                phase: "artifact_ready",
                label: tRef.current("src.screens.chatscreen.artifact_ready"),
                subtitle: String(
                    normalized.artifact?.title
                    || normalized.data?.title
                    || normalized.data?.displayLabel
                    || tRef.current("src.screens.chatscreen.a_new_artifact_is_ready"),
                ),
                runId: normalized.run_id,
                createIfMissing: true,
            });
        }

        if (normalized.name === "artifact_recorded") {
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.runtimeId || normalized.topic || normalized.artifact?.kind || "chat")) || "chat",
                    normalized.topic || "artifact.recorded",
                    String(normalized.artifact?.title || normalized.artifact?.kind || tRef.current("src.screens.chatscreen.recorded_a_new_artifact")),
                    {
                        id: normalized.event_id || `artifact:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "artifact",
                        timestamp: normalized.ts || nowMs,
                        actorLabel: normalized.actorLabel,
                    },
                ),
            );
            if (shouldFallbackRefresh) {
                scheduleRealtimeSnapshotRefresh(normalized.session_id || normalized.conversation_id || activeConversationIdRef.current);
            }
            return;
        }

        if (normalized.name === "runtime_progress") {
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.runtimeId || normalized.topic || normalized.data?.topic || "chat")) || "chat",
                    normalized.topic || String(normalized.data?.topic || "runtime.progress"),
                    String(normalized.data?.label || normalized.topic || tRef.current("src.screens.chatscreen.runtime_updated")),
                    {
                        id: normalized.event_id || `runtime:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "progress",
                        timestamp: normalized.ts || nowMs,
                        actorLabel: normalized.actorLabel,
                    },
                ),
            );
            setRuntime((current) => ({
                status: "running",
                latestSeq: normalized.seq || current.latestSeq,
                runId: normalized.run_id || current.runId,
                label: typeof normalized.data?.label === "string" ? normalized.data.label : current.label,
            }));
            if (shouldFallbackRefresh) {
                scheduleRealtimeSnapshotRefresh(normalized.session_id || normalized.conversation_id || activeConversationIdRef.current);
            }
            return;
        }

        if (normalized.name === "run_controlled") {
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.runtimeId || normalized.topic || normalized.data?.topic || "chat")) || "chat",
                    normalized.topic || String(normalized.data?.topic || "run.controlled"),
                    String(normalized.data?.topic || tRef.current("src.screens.chatscreen.run_control_updated")),
                    {
                        id: normalized.event_id || `control:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "governance",
                        timestamp: normalized.ts || nowMs,
                        actorLabel: normalized.actorLabel,
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
            if (shouldFallbackRefresh) {
                scheduleRealtimeSnapshotRefresh(normalized.session_id || normalized.conversation_id || activeConversationIdRef.current);
            }
            return;
        }

        const topic = String(normalized.topic || normalized.data?.topic || normalized.name || "").trim();
        if (!topic) {
            return;
        }

        appendRuntimeTimeline(
            buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                normalizePhoneRuntimeId(String(normalized.runtimeId || normalized.data?.runtimeId || normalized.data?.runtime || topic)) || "chat",
                topic,
                String(normalized.data?.label || normalized.data?.summary || topic),
                {
                    id: normalized.event_id || `runtime:${normalized.seq || Date.now()}`,
                    seq: normalized.seq,
                    kind: "progress",
                    timestamp: normalized.ts || nowMs,
                    actorLabel: normalized.actorLabel || (typeof normalized.data?.actorLabel === "string" ? normalized.data.actorLabel : undefined),
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
        if (shouldFallbackRefresh) {
            scheduleRealtimeSnapshotRefresh(
                normalized.session_id || normalized.conversation_id || activeConversationIdRef.current,
                { force: normalized.type === "done" || normalized.type === "error" },
            );
        }
    }, [
        appendRuntimeTimeline,
        applyRealtimeSnapshotPayload,
        getEngineNowMs,
        locale,
        patchAssistantTaskShell,
        queueRuntimeMessageEvent,
        scheduleRealtimeSnapshotRefresh,
        upsertQueuedMessage,
    ]);

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
        stopRealtime({ preserveMessageState: true });
        const controller = new AbortController();
        const subscriptionToken = realtimeSubscriptionTokenRef.current;
        realtimeAbortRef.current = controller;
        realtimeConversationIdRef.current = conversationId;
        let reconnectAttempt = 0;
        try {
            while (
                !controller.signal.aborted
                && realtimeSubscriptionTokenRef.current === subscriptionToken
                && activeConversationIdRef.current === conversationId
                && (typeof transitionToken !== "number" || conversationTransitionTokenRef.current === transitionToken)
            ) {
                try {
                    await streamRealtimeSession(authorizedRealtimeStream, conversationId, handleRealtimeEvent, controller.signal);
                } catch (error) {
                    if (!controller.signal.aborted) {
                        console.warn("[phone] realtime stream stopped:", error);
                    }
                }

                if (
                    controller.signal.aborted
                    || realtimeSubscriptionTokenRef.current !== subscriptionToken
                    || activeConversationIdRef.current !== conversationId
                    || (typeof transitionToken === "number" && conversationTransitionTokenRef.current !== transitionToken)
                ) {
                    break;
                }

                scheduleRealtimeSnapshotRefresh(conversationId, { force: true });
                const currentStatus = String(runtimeRef.current.status || "").trim().toLowerCase();
                const keepRealtimeAlive = sendingRef.current
                    || currentStatus === "running"
                    || currentStatus === "waiting_input"
                    || currentStatus === "waiting_approval";

                if (!keepRealtimeAlive) {
                    break;
                }

                reconnectAttempt += 1;
                const backoffMs = Math.min(800 + reconnectAttempt * 600, 3200);
                await new Promise((resolve) => setTimeout(resolve, backoffMs));
            }
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
    }, [authorizedRealtimeStream, handleRealtimeEvent, scheduleRealtimeSnapshotRefresh, stopRealtime]);

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
            const [detail, processSurface] = await Promise.all([
                getConversationDetail(authorizedFetch, conversationId),
                getSessionProcesses(authorizedFetch, conversationId).catch(() => ({ processes: [] as AdminProcessRef[] })),
            ]);
            if (
                activeConversationIdRef.current !== conversationId
                || conversationTransitionTokenRef.current !== transitionToken
            ) {
                return false;
            }
            const timelineMessages = Array.isArray(detail.timeline)
                ? detail.timeline
                : [];
            setLegacyChatUnsupported(isLegacyChatUnsupportedPayload(detail));
            const snapshotMessages = normalizeMessagesForState(timelineMessages);
            const preserveOptimisticLocalState = Boolean(
                messageConversationIdRef.current === conversationId
                && (
                    optimisticSeedConversationIdRef.current === conversationId
                    || sendingRef.current
                    || hasPreservableLocalAssistantState(messagesRef.current)
                )
            );
            const normalized = preserveOptimisticLocalState
                ? mergeAuthoritativeSnapshotMessages(messagesRef.current, snapshotMessages, true)
                : snapshotMessages;
            if (!preserveOptimisticLocalState) {
                resetConversationStreamState();
            }
            realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
                normalized,
                PHONE_STREAM_LIFECYCLE_OPTIONS,
            );
            lastMessageFingerprintRef.current = buildMessagesFingerprint(normalized);
            messagesRef.current = normalized;
            messageConversationIdRef.current = conversationId;
            setMessages(normalized);
            setQueuedMessages(extractQueuedMessages(detail) || []);
            applyConversationProjection(detail);
            if (Array.isArray(processSurface.processes) && processSurface.processes.length > 0) {
                applySessionProcessSurface(processSurface.processes);
            }
            lastAppliedSnapshotSeqRef.current = buildSnapshotSequence(detail);
            lastAppliedSnapshotFingerprintRef.current = buildMessagesFingerprint(normalized);
            lastRealtimeSnapshotAtRef.current = Date.now();
            hydratedConversationIdRef.current = conversationId;
            return true;
        } catch (error) {
            if (
                activeConversationIdRef.current === conversationId
                && conversationTransitionTokenRef.current === transitionToken
            ) {
                Alert.alert(t("src.screens.chatscreen.load_failed"), error instanceof Error ? error.message : t("src.screens.chatscreen.unable_to_load_the_conversation_detail"));
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
    }, [applyConversationProjection, applySessionProcessSurface, authorizedFetch, resetConversationStreamState]);

    loadSupportDataRef.current = loadSupportData;
    loadConversationRef.current = loadConversation;
    startRealtimeRef.current = startRealtime;
    stopRealtimeRef.current = stopRealtime;
    closeDesktopPreviewRef.current = closeDesktopPreview;

    useEffect(() => {
        if (!activeConversationId) {
            applySessionProcessSurface([], { forceClear: true });
            return;
        }

        applySessionProcessSurface([], { forceClear: true });
        void getSessionProcesses(authorizedFetch, activeConversationId)
            .then((payload) => {
                applySessionProcessSurface(payload.processes || []);
            })
            .catch((error) => {
                console.warn("[phone/chat] session process polling failed", error instanceof Error ? error.message : error);
                applySessionProcessSurface([]);
            });

        const timer = setInterval(() => {
            const runtimeStatus = String(runtimeRef.current.status || "").trim().toLowerCase();
            const shouldPollProcesses = Boolean(
                sendingRef.current
                || processesRef.current.length > 0
                || runtimeStatus === "running"
                || runtimeStatus === "waiting_input"
                || runtimeStatus === "waiting_approval"
            );
            if (!shouldPollProcesses) {
                return;
            }
            void getSessionProcesses(authorizedFetch, activeConversationId)
                .then((payload) => {
                    if (activeConversationIdRef.current === activeConversationId) {
                        applySessionProcessSurface(payload.processes || []);
                    }
                })
                .catch((error) => {
                    if (activeConversationIdRef.current === activeConversationId) {
                        console.warn("[phone/chat] session process polling failed", error instanceof Error ? error.message : error);
                        applySessionProcessSurface([]);
                    }
                });
        }, 5000);

        return () => {
            clearInterval(timer);
        };
    }, [activeConversationId, applySessionProcessSurface, authorizedFetch]);

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
            } catch (error) {
                console.warn("[phone/chat] loadSupportData failed", error instanceof Error ? error.message : error);
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
            return;
        }
        if (!activeConversationId) {
            setScopeBinding(null);
            setScopeLoading(false);
            return;
        }
        void loadSessionScope(activeConversationId);
    }, [activeConversationId, loadSessionScope, status]);

    useEffect(() => {
        if (status !== "authenticated") {
            setWorkspaceChooserVisible(false);
            setWorkspaceInfoOpen(false);
            setMainWorkspacePath("");
            setProjects([]);
            setNewProjectPath("");
            return;
        }
        if (activeConversationId) {
            setWorkspaceChooserVisible(false);
            clearNewConversationIntent();
            return;
        }
        if (newConversationIntent) {
            setWorkspaceChooserVisible(true);
        }
    }, [activeConversationId, clearNewConversationIntent, newConversationIntent, status]);

    useEffect(() => {
        if (status !== "authenticated") {
            conversationTransitionTokenRef.current += 1;
            previousConversationIdRef.current = null;
            optimisticSeedConversationIdRef.current = null;
            hydratedConversationIdRef.current = null;
            loadingConversationIdRef.current = null;
            latestSeqRef.current = 0;
            lastAppliedSnapshotSeqRef.current = 0;
            lastAppliedSnapshotFingerprintRef.current = "";
            lastRealtimeSnapshotAtRef.current = 0;
            stopRealtimeRef.current();
            return;
        }
        if (!activeConversationId) {
            conversationTransitionTokenRef.current += 1;
            previousConversationIdRef.current = null;
            optimisticSeedConversationIdRef.current = null;
            hydratedConversationIdRef.current = null;
            loadingConversationIdRef.current = null;
            stopRealtimeRef.current();
            clearActiveConversationViewState();
            return;
        }
        const conversationChanged = previousConversationIdRef.current !== activeConversationId;
        previousConversationIdRef.current = activeConversationId;
        const transitionToken = conversationTransitionTokenRef.current + 1;
        conversationTransitionTokenRef.current = transitionToken;
        const skipInitialHydration = conversationChanged && optimisticSeedConversationIdRef.current === activeConversationId;
        if (conversationChanged) {
            seenRealtimeEventKeysRef.current.clear();
            stopRealtimeRef.current(skipInitialHydration ? { preserveMessageState: true } : undefined);
            if (!skipInitialHydration) {
                clearActiveConversationViewState();
            }
        }
        let cancelled = false;
        void (async () => {
            if (skipInitialHydration) {
                optimisticSeedConversationIdRef.current = null;
                await startRealtimeRef.current(activeConversationId, transitionToken);
                return;
            }
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
    }, [activeConversationId, clearActiveConversationViewState, status]);

    useEffect(() => {
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
        const canonicalSessionId = item.sessionId || item.id;
        setHistoryOpen(false);
        setInput("");
        setActiveQueryMode(null);
        setActiveQueryText("");
        setUploadedFiles([]);
        setSelectedCommand(null);
        setSelectedSkills([]);
        setTaskPlanningMode(false);
        setWorkspaceChooserVisible(false);
        setWorkspaceInfoOpen(false);
        setNewProjectPath("");
        clearNewConversationIntent();
        if (canonicalSessionId === activeConversationIdRef.current) {
            return;
        }
        stopRealtimeRef.current();
        optimisticSeedConversationIdRef.current = null;
        hydratedConversationIdRef.current = null;
        loadingConversationIdRef.current = null;
        clearActiveConversationViewState();
        await setActiveConversationId(canonicalSessionId);
        router.replace("/chat" as Href);
    }, [clearActiveConversationViewState, clearNewConversationIntent, setActiveConversationId]);

    const handleNewConversation = useCallback(async () => {
        stopRealtime();
        optimisticSeedConversationIdRef.current = null;
        hydratedConversationIdRef.current = null;
        loadingConversationIdRef.current = null;
        setHistoryOpen(false);
        setInput("");
        setActiveQueryMode(null);
        setActiveQueryText("");
        clearActiveConversationViewState();
        setUploadedFiles([]);
        setSelectedCommand(null);
        setSelectedSkills([]);
        setTaskPlanningMode(false);
        setWorkspaceInfoOpen(false);
        setWorkspaceChooserVisible(true);
        setNewProjectPath("");
        setScopeBinding(null);
        setRuntimePanelOpen(false);
        setSelectedRuntimeId("chat");
        await setActiveConversationId(null);
        router.replace("/chat?new=1" as Href);
    }, [clearActiveConversationViewState, setActiveConversationId, stopRealtime]);

    const handleBrandPress = useCallback(async () => {
        stopRealtime();
        optimisticSeedConversationIdRef.current = null;
        hydratedConversationIdRef.current = null;
        loadingConversationIdRef.current = null;
        setHistoryOpen(false);
        setInput("");
        setActiveQueryMode(null);
        setActiveQueryText("");
        clearActiveConversationViewState();
        setUploadedFiles([]);
        setSelectedCommand(null);
        setSelectedSkills([]);
        setTaskPlanningMode(false);
        setWorkspaceChooserVisible(false);
        setWorkspaceInfoOpen(false);
        setNewProjectPath("");
        setScopeBinding(null);
        setRuntimePanelOpen(false);
        setSelectedRuntimeId("chat");
        await setActiveConversationId(null);
        clearNewConversationIntent();
        router.replace("/chat" as Href);
    }, [clearActiveConversationViewState, clearNewConversationIntent, setActiveConversationId, stopRealtime]);

    const handleDeleteConversation = useCallback((item: ConversationSummary) => {
        const canonicalSessionId = item.sessionId || item.id;
        Alert.alert(t("src.screens.chatscreen.delete_conversation"), t("src.screens.chatscreen.delete_this_conversation"), [
            { text: t("src.components.chat.mediaviewerlightbox.cancel"), style: "cancel" },
            {
                text: t("src.screens.chatscreen.delete"),
                style: "destructive",
                onPress: () => {
                    void (async () => {
                        await deleteConversation(authorizedFetch, canonicalSessionId);
                        const nextConversations = conversations.filter((conversation) => (conversation.sessionId || conversation.id) !== canonicalSessionId);
                        setConversations(nextConversations);
                        if (activeConversationId === canonicalSessionId) {
                            const fallbackId = nextConversations[0]?.sessionId || nextConversations[0]?.id || null;
                            await setActiveConversationId(fallbackId);
                        }
                    })().catch((error) => {
                        Alert.alert(t("src.screens.chatscreen.delete_failed"), error instanceof Error ? error.message : t("src.screens.chatscreen.unable_to_delete_conversation"));
                    });
                },
            },
        ]);
    }, [activeConversationId, authorizedFetch, conversations, setActiveConversationId, t]);

    const handleDeleteMessage = useCallback((message: ChatMessage) => {
        Alert.alert(t("src.screens.chatscreen.delete_message"), t("src.screens.chatscreen.delete_this_message"), [
            { text: t("src.components.chat.mediaviewerlightbox.cancel"), style: "cancel" },
            {
                text: t("src.screens.chatscreen.delete"),
                style: "destructive",
                onPress: () => {
                    void (async () => {
                        if (message.id && !message.id.startsWith("user-") && !message.id.startsWith("assistant-")) {
                            await deleteMessage(authorizedFetch, message.id);
                        }
                        setMessages((current) => {
                            const next = current.filter((item) => item.renderKey !== message.renderKey);
                            messagesRef.current = next;
                            realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
                                next,
                                PHONE_STREAM_LIFECYCLE_OPTIONS,
                            );
                            return next;
                        });
                    })().catch((error) => {
                        Alert.alert(t("src.screens.chatscreen.delete_failed"), error instanceof Error ? error.message : t("src.screens.chatscreen.unable_to_delete_message"));
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

            const uploaded: UploadedWorkspaceFile[] = [];
            for (const [index, asset] of result.assets.entries()) {
                const localId = `upload:${Date.now()}:${index}:${asset.name || "file"}`;
                const normalizedAsset = await normalizeUploadAssetUri(asset);
                const previewDraft = await buildLocalUploadedFileDraft(
                    normalizedAsset,
                    {
                        localId,
                        name: normalizedAsset.name,
                        type: normalizedAsset.mimeType || "application/octet-stream",
                    },
                    localId,
                );
                try {
                    const nextFile = await uploadAttachment(authorizedFetch, {
                        uri: normalizedAsset.uri,
                        name: normalizedAsset.name,
                        type: normalizedAsset.mimeType || "application/octet-stream",
                    }, {
                        sessionId: activeConversationIdRef.current,
                        conversationId: activeConversationIdRef.current,
                        workspaceId: scopeBinding?.workspaceId,
                        workspacePath: scopeBinding?.workspacePath,
                        projectId: scopeBinding?.projectId,
                    });
                    uploaded.push({
                        ...previewDraft,
                        ...nextFile,
                        localId: nextFile.localId || previewDraft.localId || localId,
                        localUri: previewDraft.localUri,
                        previewUri: previewDraft.previewUri,
                        previewKind: previewDraft.previewKind,
                        durationLabel: previewDraft.durationLabel,
                    });
                } catch (error) {
                    throw buildUploadTransportError(normalizedAsset, error, adminBaseUrl);
                }
            }

            setUploadedFiles((current) => mergeUploadedWorkspaceFiles(current, uploaded));
        } catch (error) {
            Alert.alert(t("src.screens.chatscreen.upload_failed"), error instanceof Error ? error.message : t("src.screens.chatscreen.unable_to_upload_the_attachment"));
        } finally {
            setAttachmentBusy(false);
        }
    }, [authorizedFetch]);

    const handleToggleRecording = useCallback(async () => {
        try {
            if (!recorder.isRecording) {
                const permission = await requestRecordingPermissionsAsync();
                if (!permission.granted) {
                    throw new Error(t("src.screens.chatscreen.microphone_access_is_required"));
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
                throw new Error(t("src.screens.chatscreen.no_recording_file_found"));
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
                throw new Error(String(payload.error || t("src.screens.chatscreen.no_speech_detected")));
            }
            setInput((current) => [current.trim(), text].filter(Boolean).join(current.trim() ? "\n" : ""));
        } catch (error) {
            Alert.alert(t("src.screens.chatscreen.recording_failed"), error instanceof Error ? error.message : t("src.screens.chatscreen.unable_to_transcribe_recording"));
        } finally {
            setTranscribing(false);
        }
    }, [authorizedFetch, recorder, t]);

    const handleBodyInputChange = useCallback((next: string) => {
        const leading = next.trimStart();
        const commandQuery = leading.match(/^\/([^\s]*)$/);
        const skillQuery = leading.match(/^@([^\s]*)$/);
        if (!input.trim() && !activeQueryMode) {
            if (!selectedCommand && commandQuery) {
                setActiveQueryMode("command");
                setActiveQueryText(commandQuery[1] || "");
                setInput("");
                return;
            }
            if (skillQuery) {
                setActiveQueryMode("skill");
                setActiveQueryText(skillQuery[1] || "");
                setInput("");
                return;
            }
        }
        setInput(next);
    }, [activeQueryMode, input, selectedCommand]);

    const handleQueryBackspace = useCallback(() => {
        if (activeQueryText) {
            return;
        }
        setActiveQueryMode(null);
        setActiveQueryText("");
    }, [activeQueryText]);

    const handleComposerBackspace = useCallback(() => {
        if (input.trim()) {
            return;
        }
        if (selectedSkills.length > 0) {
            setSelectedSkills((current) => current.slice(0, -1));
            return;
        }
        if (selectedSubagentFamilies.length > 0) {
            setSelectedSubagentFamilies((current) => current.slice(0, -1));
            return;
        }
        setSelectedCommand((current) => (current ? null : current));
    }, [input, selectedSkills.length, selectedSubagentFamilies.length]);

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
                throw new Error(t("src.screens.chatscreen.failed_to_create_audio_file"));
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
            Alert.alert(t("src.screens.chatscreen.speech_playback_failed"), error instanceof Error ? error.message : t("src.screens.chatscreen.unable_to_play_audio"));
        }
    }, [authorizedFetch, speakingId, t, ttsPlayer, ttsStatus.playing]);

    const handleApprovalResolve = useCallback(async (approval: PendingApproval | AskUserInteraction, answer: string, approve: boolean) => {
        const askInteraction = approval as PendingApproval & AskUserInteraction;
        const isAskUser = Boolean(
            askInteraction.interactionId
            || askInteraction.request?.interactionKind === "ask_user"
        );
        const approvalId = isAskUser
            ? String(askInteraction.id || askInteraction.interactionId || "")
            : String(approval.id || (approval as PendingApproval).approval_id || "");
        if (!approvalId) {
            return;
        }
        if (isAskUser) {
            if (!approve) {
                setAskUserInteractions((current) => current.filter((item) => String(item.id || item.interactionId || "") !== approvalId));
            } else {
                await respondAskUser(authorizedFetch, approvalId, answer);
            }
        } else {
            await approvePendingItem(authorizedFetch, approvalId, answer, approve);
            recentlyResolvedApprovalIdsRef.current.add(approvalId);
            setTimeout(() => {
                recentlyResolvedApprovalIdsRef.current.delete(approvalId);
            }, 30000);
            setApprovals((current) => current.filter((item) => String(item.id || item.approval_id || "") !== approvalId));
        }
    }, [adminBaseUrl, authorizedFetch, scopeBinding?.projectId, scopeBinding?.workspaceId, scopeBinding?.workspacePath]);

    const handleRunCommand = useCallback(async (command: "interrupt" | "retry") => {
        const runId = String(activeRunIdRef.current || "").trim();
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
                command === "interrupt" ? t("src.screens.chatscreen.stop_failed") : t("src.screens.chatscreen.retry_failed"),
                error instanceof Error ? error.message : t("src.screens.chatscreen.run_command_failed"),
            );
        } finally {
            setRunActionBusy(false);
        }
    }, [authorizedFetch, runActionBusy, t]);

    const handlePromoteQueuedMessage = useCallback(async (item: QueuedChatMessage) => {
        const id = String(item.id || "").trim();
        if (!id) {
            return;
        }
        try {
            const result = await promoteQueuedChatMessage(authorizedFetch, id);
            upsertQueuedMessage(result.queuedMessage || { ...item, state: "promoted" });
        } catch (error) {
            Alert.alert(
                t("src.screens.chatscreen.promote_guidance_failed"),
                error instanceof Error ? error.message : t("src.screens.chatscreen.unable_to_promote_guidance"),
            );
        }
    }, [authorizedFetch, t, upsertQueuedMessage]);

    const handleCancelQueuedMessage = useCallback(async (item: QueuedChatMessage) => {
        const id = String(item.id || "").trim();
        if (!id) {
            return;
        }
        try {
            const result = await cancelQueuedChatMessage(authorizedFetch, id);
            upsertQueuedMessage(result.queuedMessage || { ...item, state: "cancelled" });
        } catch (error) {
            Alert.alert(
                t("src.screens.chatscreen.cancel_queued_message_failed"),
                error instanceof Error ? error.message : t("src.screens.chatscreen.unable_to_cancel_queued_message"),
            );
        }
    }, [authorizedFetch, t, upsertQueuedMessage]);

    const handleOpenQueuedMessageEditor = useCallback((item: QueuedChatMessage) => {
        if (String(item.state || "pending").trim() !== "pending") {
            return;
        }
        setEditingQueuedMessage(item);
        setQueuedMessageEditText(String(item.content || ""));
    }, []);

    const handleSaveQueuedMessageEdit = useCallback(async () => {
        const item = editingQueuedMessage;
        const id = String(item?.id || "").trim();
        const nextContent = queuedMessageEditText.trim();
        if (!id || !item || !nextContent || queuedMessageEditBusy) {
            return;
        }
        setQueuedMessageEditBusy(true);
        try {
            const result = await updateQueuedChatMessage(authorizedFetch, id, nextContent);
            upsertQueuedMessage(result.queuedMessage || { ...item, content: nextContent, state: "pending" });
            setEditingQueuedMessage(null);
            setQueuedMessageEditText("");
        } catch (error) {
            Alert.alert(
                t("src.screens.chatscreen.edit_queued_message_failed"),
                error instanceof Error ? error.message : t("src.screens.chatscreen.unable_to_edit_queued_message"),
            );
        } finally {
            setQueuedMessageEditBusy(false);
        }
    }, [authorizedFetch, editingQueuedMessage, queuedMessageEditBusy, queuedMessageEditText, t, upsertQueuedMessage]);

    const projection = useMemo(
        () => {
            const profileStartedAt = getPerfNowMs();
            const nextProjection = buildPhoneChatProjection({
                conversations,
                activeConversationId,
                messages,
                approvals,
                askUserInteractions,
                todos,
                processes,
                contextReferences,
                contextGovernance,
                contextGovernanceHistory,
                runtime,
                runtimeTimeline,
                selectedRuntimeId,
                t,
                locale,
            });
            const elapsedMs = Math.round(getPerfNowMs() - profileStartedAt);
            if (__DEV__ && (elapsedMs >= 16 || runtimeTimeline.length >= 40 || messages.length >= 30)) {
                debugPerfTrace("projection-build", {
                    elapsedMs,
                    messageCount: messages.length,
                    projectedMessageCount: nextProjection.projectedMessages.length,
                    runtimeEventCount: runtimeTimeline.length,
                    processCount: processes.length,
                    todoCount: todos.length,
                    selectedRuntimeId,
                });
            }
            return nextProjection;
        },
        [activeConversationId, approvals, askUserInteractions, contextGovernance, contextGovernanceHistory, contextReferences, conversations, locale, messages, processes, runtime, runtimeTimeline, selectedRuntimeId, t, todos],
    );

    const latestAutoPlayableVoice = projection.voiceCardDescriptors[projection.voiceCardDescriptors.length - 1] || null;
    const latestProjectedMessage = projection.projectedMessages[projection.projectedMessages.length - 1] || null;
    const latestProjectedMessageKey = String(
        latestProjectedMessage?.renderKey
        || latestProjectedMessage?.id
        || "",
    ).trim();
    const governancePendingApprovalId = String(
        projection.governancePendingApproval?.id
        || projection.governancePendingApproval?.approval_id
        || "",
    ).trim();
    const governanceApprovalShouldSurface = Boolean(
        governancePendingApprovalId
        || projection.runControlState.status === "waiting_approval",
    );

    const playReplyPop = useCallback(() => {
        if (!voiceEnabled) {
            return;
        }
        try {
            const player = replyPopPlayer as typeof replyPopPlayer & { seekTo?: (position: number) => void };
            player.seekTo?.(0);
            player.play();
        } catch {
            // The reply sound is a non-critical affordance; never block chat rendering.
        }
    }, [replyPopPlayer, voiceEnabled]);

    const hudProcesses = useMemo(
        () => (projection.processes.length > 0 ? projection.processes : processes),
        [processes, projection.processes],
    );

    useEffect(() => {
        activeRunIdRef.current = String(projection.runControlState.runId || "").trim();
    }, [projection.runControlState.runId]);

    useEffect(() => {
        if (!activeConversationId || !latestProjectedMessage || latestProjectedMessage.role !== "assistant") {
            return;
        }
        const messageId = String(latestProjectedMessage.id || latestProjectedMessage.renderKey || "").trim();
        const contentLength = String(latestProjectedMessage.content || "").trim().length;
        if (!messageId || contentLength === 0 || isActiveAssistantStreamPhase(latestProjectedMessage.uiStreamPhase)) {
            return;
        }
        const currentKey = `${messageId}:${contentLength}`;
        const previousKey = replyPopSeenRef.current.get(activeConversationId);
        if (!previousKey) {
            replyPopSeenRef.current.set(activeConversationId, currentKey);
            return;
        }
        if (previousKey === currentKey || replyPopPlayedRef.current.has(messageId)) {
            return;
        }
        replyPopSeenRef.current.set(activeConversationId, currentKey);
        replyPopPlayedRef.current.add(messageId);
        playReplyPop();
    }, [activeConversationId, latestProjectedMessage, playReplyPop]);

    useEffect(() => {
        if (!activeConversationId) {
            return;
        }
        const hasRuntimeNeed = Boolean(
            projection.runControlState.runId
            || projection.runControlState.canInterrupt
            || projection.runControlState.status === "running",
        );
        if (hasRuntimeNeed && processes.length > 0 && hudProcesses.length === 0) {
            console.warn("[phone/chat] process surface dropped after hydration/filtering", {
                activeConversationId,
                polledProcesses: processes.length,
                projectedProcesses: projection.processes.length,
                runId: projection.runControlState.runId || null,
            });
        }
    }, [activeConversationId, hudProcesses.length, processes.length, projection.processes.length, projection.runControlState.canInterrupt, projection.runControlState.runId, projection.runControlState.status]);

    useEffect(() => {
        if (projection.selectedRuntimeId !== selectedRuntimeId) {
            setSelectedRuntimeId(projection.selectedRuntimeId);
        }
    }, [projection.selectedRuntimeId, selectedRuntimeId]);

    useEffect(() => {
        if (!governanceApprovalShouldSurface) {
            setGovernanceApprovalOpen(false);
            if (dismissedGovernanceApprovalId) {
                setDismissedGovernanceApprovalId("");
            }
            return;
        }
        if (governancePendingApprovalId && dismissedGovernanceApprovalId === governancePendingApprovalId) {
            return;
        }
        setGovernanceApprovalOpen(true);
    }, [dismissedGovernanceApprovalId, governanceApprovalShouldSurface, governancePendingApprovalId]);

    useEffect(() => {
        if (
            projection.runControlState.status !== "waiting_approval"
            || governancePendingApprovalId
            || !activeConversationIdRef.current
        ) {
            return;
        }
        const now = Date.now();
        if (now - waitingApprovalRefreshAtRef.current < 1200) {
            return;
        }
        waitingApprovalRefreshAtRef.current = now;
        scheduleRealtimeSnapshotRefresh(activeConversationIdRef.current, { force: true });
    }, [governancePendingApprovalId, projection.runControlState.status, scheduleRealtimeSnapshotRefresh]);

    const openGovernanceApproval = useCallback(() => {
        if (!governanceApprovalShouldSurface) {
            return;
        }
        setDismissedGovernanceApprovalId("");
        setGovernanceApprovalOpen(true);
    }, [governanceApprovalShouldSurface]);

    const openApprovalPanel = useCallback(() => {
        if (governancePendingApprovalId) {
            openGovernanceApproval();
            return;
        }
        setSelectedRuntimeId("automation");
        setRuntimePanelOpen(true);
    }, [governancePendingApprovalId, openGovernanceApproval]);

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
        setRuntimePanelOpen(true);
    }, [governancePendingApprovalId]);

    const handleGovernanceApprovalResolve = useCallback(async (answer: string, approve: boolean) => {
        const approval = projection.governancePendingApproval;
        if (!approval) {
            return;
        }
        setGovernanceApprovalBusy(true);
        try {
            await handleApprovalResolve(approval, answer, approve);
            setGovernanceApprovalOpen(false);
            setDismissedGovernanceApprovalId("");
        } finally {
            setGovernanceApprovalBusy(false);
        }
    }, [handleApprovalResolve, projection.governancePendingApproval]);

    useEffect(() => {
        if (!activeConversationId || !latestProjectedMessageKey) {
            return;
        }

        const current = voiceAutoplayStateRef.current.get(activeConversationId) || {
            lastSeenMessageKey: "",
            lastAutoPlayedKey: "",
        };
        const latestMessageId = String(latestProjectedMessage?.id || "").trim();
        const belongsToLatestMessage = Boolean(
            latestAutoPlayableVoice?.autoPlayKey
            && (
                (latestAutoPlayableVoice.renderKey && latestAutoPlayableVoice.renderKey === latestProjectedMessageKey)
                || (!latestAutoPlayableVoice.renderKey && latestAutoPlayableVoice.messageId === latestMessageId)
            ),
        );

        if (!belongsToLatestMessage) {
            if (current.lastSeenMessageKey !== latestProjectedMessageKey) {
                voiceAutoplayStateRef.current.set(activeConversationId, {
                    ...current,
                    lastSeenMessageKey: latestProjectedMessageKey,
                });
            }
            return;
        }

        if (!voiceEnabled || !latestAutoPlayableVoice?.voiceText.trim()) {
            if (current.lastSeenMessageKey !== latestProjectedMessageKey) {
                voiceAutoplayStateRef.current.set(activeConversationId, {
                    ...current,
                    lastSeenMessageKey: latestProjectedMessageKey,
                });
            }
            return;
        }

        if (!current.lastSeenMessageKey) {
            voiceAutoplayStateRef.current.set(activeConversationId, {
                ...current,
                lastSeenMessageKey: latestProjectedMessageKey,
            });
            return;
        }

        if (current.lastSeenMessageKey === latestProjectedMessageKey) {
            return;
        }

        const nextState = {
            lastSeenMessageKey: latestProjectedMessageKey,
            lastAutoPlayedKey: current.lastAutoPlayedKey,
        };

        if (current.lastAutoPlayedKey === latestAutoPlayableVoice.autoPlayKey) {
            voiceAutoplayStateRef.current.set(activeConversationId, nextState);
            return;
        }

        voiceAutoplayStateRef.current.set(activeConversationId, {
            ...nextState,
            lastAutoPlayedKey: latestAutoPlayableVoice.autoPlayKey,
        });
        void handleSpeakVoice(latestAutoPlayableVoice.voiceText, latestAutoPlayableVoice.autoPlayKey);
    }, [
        activeConversationId,
        handleSpeakVoice,
        latestAutoPlayableVoice,
        latestProjectedMessage?.id,
        latestProjectedMessageKey,
        voiceEnabled,
    ]);

    const handleSend = useCallback(async () => {
        const text = input.trim();
        if (!text && !selectedCommand && selectedSkills.length === 0 && selectedSubagentFamilies.length === 0 && uploadedFiles.length === 0) {
            return;
        }
        const currentConversationId = activeConversationIdRef.current;
        if (!currentConversationId) {
            setWorkspaceChooserVisible(true);
            clearNewConversationIntent();
            return;
        }

        const pendingCommand = selectedCommand;
        const pendingSkills = [...selectedSkills];
        const pendingSubagentFamilies = [...selectedSubagentFamilies];
        const pendingFiles = [...uploadedFiles];
        const effectiveText = text || (
            pendingFiles.length === 1
                ? t("shared.upload.uploaded_single")
                : pendingFiles.length > 1
                    ? t("shared.upload.uploaded_count", { count: pendingFiles.length })
                    : ""
        );
        const engineNowMs = getEngineNowMs();
        const engineNowIso = new Date(engineNowMs).toISOString();
        let submissionAccepted = false;
        let optimisticUserMessageId = "";
        let optimisticAssistantMessageId = "";
        let localQueueId = "";
        let submittedClientMessageId = "";
        setSending(true);
        try {
            const historyMessages = messagesRef.current
                .filter((message) => !message.uiEphemeral)
                .map((message) => ({
                    role: message.role,
                    content: message.content,
                }));

            if (realtimeConversationIdRef.current !== currentConversationId || !realtimeAbortRef.current) {
                activeConversationIdRef.current = currentConversationId;
                void startRealtimeRef.current(currentConversationId);
            }

            const userMessage = buildUserMessage(effectiveText, {
                command: pendingCommand,
                skills: pendingSkills,
                subagentFamilies: pendingSubagentFamilies,
                taskPlanningMode,
                files: pendingFiles,
            }, engineNowMs);
            const clientMessageId = userMessage.id;
            submittedClientMessageId = clientMessageId;
            const queueEligible = isQueueEligibleRunStatus(projection.runControlState.status);
            const activeRunId = String(projection.runControlState.runId || activeRunIdRef.current || "").trim();

            if (queueEligible) {
                localQueueId = `local_queued_${clientMessageId}`;
                upsertQueuedMessage({
                    id: localQueueId,
                    sessionId: currentConversationId,
                    runId: activeRunId || undefined,
                    clientMessageId,
                    content: effectiveText,
                    state: "pending",
                    ordinal: queuedMessages.length + 1,
                    createdAt: engineNowIso,
                    updatedAt: engineNowIso,
                });
                setRuntime((current) => ({
                    ...current,
                    label: t("src.screens.chatscreen.message_queued"),
                }));
                setInput("");
                setActiveQueryMode(null);
                setActiveQueryText("");
                setSelectedCommand(null);
                setSelectedSkills([]);
                setSelectedSubagentFamilies([]);
                setUploadedFiles([]);

                if (effectiveText) {
                    setConversations((current) => current.map((conversation) =>
                        conversation.id === currentConversationId
                            ? {
                                ...conversation,
                                title: isPlaceholderConversationTitle(conversation.title)
                                    ? (effectiveText.slice(0, 36) || conversation.title || "")
                                    : conversation.title,
                                updatedAt: engineNowIso,
                                historySortAt: engineNowIso,
                                previewExcerpt: effectiveText.slice(0, 120),
                            }
                            : conversation,
                    ));
                }

                const submitResult = await submitChatMessage(
                    authorizedFetch,
                    effectiveText,
                    {
                        messages: historyMessages,
                        conversationId: currentConversationId,
                        clientMessageId,
                        commandPresetName: pendingCommand?.name || null,
                        skillReferences: pendingSkills,
                        contextMentions: [
                            ...pendingSkills.map((skill): ContextMentionSummary => ({
                                kind: "skill",
                                name: skill.name,
                                label: skill.name,
                                description: skill.description,
                                path: skill.path,
                                sourceType: "explicit_mention",
                            })),
                            ...pendingSubagentFamilies.map((family): ContextMentionSummary => ({
                                kind: "subagent_family",
                                id: family.familyId,
                                familyId: family.familyId,
                                name: family.displayName || family.familyId,
                                label: family.displayName || family.familyId,
                                description: family.description,
                                sourceType: "explicit_mention",
                            })),
                        ],
                        fileUrls: pendingFiles.map((file) => file.url || file.publicUrl || "").filter(Boolean),
                        attachments: buildUploadedFileAttachments(pendingFiles),
                        taskPlanningMode,
                    },
                );
                if (submitResult.accepted === false) {
                    throw new Error(t("src.screens.chatscreen.unable_to_submit_message"));
                }
                submissionAccepted = true;
                setQueuedMessages((current) => current.filter((item) => item.id !== localQueueId));
                if (submitResult.queued && submitResult.queuedMessage) {
                    upsertQueuedMessage(submitResult.queuedMessage);
                    return;
                }

                const acceptedUserMessage = normalizeAcceptedUserMessage(submitResult.userMessage, userMessage) || userMessage;
                const assistantPlaceholder = buildAssistantPlaceholder();
                assistantPlaceholder.metadata = {
                    ...(assistantPlaceholder.metadata || {}),
                    clientMessageId,
                };
                if (taskPlanningMode) {
                    assistantPlaceholder.uiStreamPhase = "task_planning";
                    assistantPlaceholder.metadata = {
                        ...(assistantPlaceholder.metadata || {}),
                        assistantTaskProgress: {
                            phase: "task_planning",
                            label: t("src.screens.chatscreen.planning_task"),
                            subtitle: t("src.screens.chatscreen.breaking_down_the_steps_and_preparing_execution"),
                        },
                    };
                }
                const submittedRunId = String(
                    submitResult.runId
                    || submitResult.run_id
                    || "",
                ).trim();
                if (submittedRunId) {
                    assistantPlaceholder.runId = submittedRunId;
                    assistantPlaceholder.metadata = {
                        ...(assistantPlaceholder.metadata || {}),
                        runId: submittedRunId,
                    };
                }
                setMessages((current) => {
                    const next = normalizeMessagesForState([
                        ...current,
                        acceptedUserMessage,
                        assistantPlaceholder,
                    ]);
                    realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
                        next,
                        PHONE_STREAM_LIFECYCLE_OPTIONS,
                    );
                    lastMessageFingerprintRef.current = buildMessagesFingerprint(next);
                    messagesRef.current = next;
                    messageConversationIdRef.current = currentConversationId;
                    return next;
                });
                if (submittedRunId) {
                    setRuntime((current) => ({
                        ...current,
                        status: "running",
                        runId: submittedRunId,
                    }));
                }
                return;
            }

            const assistantPlaceholder = buildAssistantPlaceholder();
            assistantPlaceholder.metadata = {
                ...(assistantPlaceholder.metadata || {}),
                clientMessageId,
            };
            optimisticUserMessageId = userMessage.id;
            optimisticAssistantMessageId = assistantPlaceholder.id;
            if (taskPlanningMode) {
                assistantPlaceholder.uiStreamPhase = "task_planning";
                assistantPlaceholder.metadata = {
                    ...(assistantPlaceholder.metadata || {}),
                    assistantTaskProgress: {
                        phase: "task_planning",
                        label: t("src.screens.chatscreen.planning_task"),
                        subtitle: t("src.screens.chatscreen.breaking_down_the_steps_and_preparing_execution"),
                    },
                };
            }

            setMessages((current) => {
                const next = normalizeMessagesForState([
                    ...current,
                    userMessage,
                    assistantPlaceholder,
                ]);
                realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
                    next,
                    PHONE_STREAM_LIFECYCLE_OPTIONS,
                );
                lastMessageFingerprintRef.current = buildMessagesFingerprint(next);
                messagesRef.current = next;
                messageConversationIdRef.current = currentConversationId;
                return next;
            });
            setRuntime((current) => ({
                ...current,
                status: "running",
            }));
            setInput("");
            setActiveQueryMode(null);
            setActiveQueryText("");
            setSelectedCommand(null);
            setSelectedSkills([]);
            setSelectedSubagentFamilies([]);
            setUploadedFiles([]);

            if (effectiveText) {
                setConversations((current) => current.map((conversation) =>
                    conversation.id === currentConversationId
                        ? {
                            ...conversation,
                            title: isPlaceholderConversationTitle(conversation.title)
                                ? (effectiveText.slice(0, 36) || conversation.title || "")
                                : conversation.title,
                            updatedAt: engineNowIso,
                            historySortAt: engineNowIso,
                            previewExcerpt: effectiveText.slice(0, 120),
                        }
                        : conversation,
                ));
            }

            const submitResult = await submitChatMessage(
                authorizedFetch,
                effectiveText,
                {
                    messages: historyMessages,
                    conversationId: currentConversationId,
                    clientMessageId,
                    commandPresetName: pendingCommand?.name || null,
                    skillReferences: pendingSkills,
                    contextMentions: [
                        ...pendingSkills.map((skill): ContextMentionSummary => ({
                            kind: "skill",
                            name: skill.name,
                            label: skill.name,
                            description: skill.description,
                            path: skill.path,
                            sourceType: "explicit_mention",
                        })),
                        ...pendingSubagentFamilies.map((family): ContextMentionSummary => ({
                            kind: "subagent_family",
                            id: family.familyId,
                            familyId: family.familyId,
                            name: family.displayName || family.familyId,
                            label: family.displayName || family.familyId,
                            description: family.description,
                            sourceType: "explicit_mention",
                        })),
                    ],
                    fileUrls: pendingFiles.map((file) => file.url || file.publicUrl || "").filter(Boolean),
                    attachments: buildUploadedFileAttachments(pendingFiles),
                    taskPlanningMode,
                },
            );
            if (submitResult.accepted === false) {
                throw new Error(t("src.screens.chatscreen.unable_to_submit_message"));
            }
            submissionAccepted = true;
            if (submitResult.queued && submitResult.queuedMessage) {
                upsertQueuedMessage(submitResult.queuedMessage);
                setMessages((current) => {
                    const next = normalizeMessagesForState(current.filter((message) =>
                        message.id !== optimisticAssistantMessageId && message.id !== optimisticUserMessageId,
                    ));
                    realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
                        next,
                        PHONE_STREAM_LIFECYCLE_OPTIONS,
                    );
                    lastMessageFingerprintRef.current = buildMessagesFingerprint(next);
                    messagesRef.current = next;
                    return next;
                });
                setRuntime((current) => ({
                    ...current,
                    label: t("src.screens.chatscreen.message_queued"),
                }));
                return;
            }
            const acceptedUserMessage = normalizeAcceptedUserMessage(submitResult.userMessage, userMessage);
            if (acceptedUserMessage) {
                setMessages((current) => {
                    const next = normalizeMessagesForState(current.map((message) =>
                        message.id === userMessage.id ? acceptedUserMessage : message,
                    ));
                    realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
                        next,
                        PHONE_STREAM_LIFECYCLE_OPTIONS,
                    );
                    lastMessageFingerprintRef.current = buildMessagesFingerprint(next);
                    messagesRef.current = next;
                    messageConversationIdRef.current = currentConversationId;
                    return next;
                });
            }
            setSelectedCommand(null);
            setSelectedSkills([]);
            setSelectedSubagentFamilies([]);
            setUploadedFiles([]);
            setActiveQueryMode(null);
            setActiveQueryText("");

            const submittedRunId = String(
                submitResult.runId
                || submitResult.run_id
                || "",
            ).trim();
            if (submittedRunId) {
                setRuntime((current) => ({
                    ...current,
                    status: current.status === "waiting_input" || current.status === "waiting_approval" ? current.status : "running",
                    runId: submittedRunId,
                }));
                setMessages((current) => {
                    const targetIndex = findLatestAssistantShellIndex(current);
                    if (targetIndex < 0) {
                        return current;
                    }
                    const next = [...current];
                    next[targetIndex] = {
                        ...next[targetIndex],
                        runId: submittedRunId,
                        metadata: {
                            ...(next[targetIndex].metadata || {}),
                            runId: submittedRunId,
                            clientMessageId,
                        },
                    };
                    messagesRef.current = next;
                    messageConversationIdRef.current = currentConversationId;
                    realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
                        next,
                        PHONE_STREAM_LIFECYCLE_OPTIONS,
                    );
                    lastMessageFingerprintRef.current = buildMessagesFingerprint(next);
                    return next;
                });
            }

        } catch (error) {
            if (!submissionAccepted) {
                setInput(text);
                setSelectedCommand(pendingCommand);
                setSelectedSkills(pendingSkills);
                setSelectedSubagentFamilies(pendingSubagentFamilies);
                setUploadedFiles(pendingFiles);
                setQueuedMessages((current) => current.filter((item) =>
                    item.id !== localQueueId && item.clientMessageId !== submittedClientMessageId,
                ));
                setMessages((current) => {
                    const next = normalizeMessagesForState(current.filter((message) =>
                        message.id !== optimisticAssistantMessageId && message.id !== optimisticUserMessageId,
                    ));
                    realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
                        next,
                        PHONE_STREAM_LIFECYCLE_OPTIONS,
                    );
                    lastMessageFingerprintRef.current = buildMessagesFingerprint(next);
                    messagesRef.current = next;
                    return next;
                });
            }
            Alert.alert(t("src.screens.chatscreen.send_failed"), error instanceof Error ? error.message : t("src.screens.chatscreen.unable_to_send_message"));
        } finally {
            setSending(false);
        }
    }, [
        authorizedFetch,
        clearNewConversationIntent,
        getEngineNowMs,
        input,
        projection.runControlState.runId,
        projection.runControlState.status,
        queuedMessages.length,
        selectedCommand,
        selectedSkills,
        selectedSubagentFamilies,
        taskPlanningMode,
        t,
        uploadedFiles,
        upsertQueuedMessage,
    ]);

    if (status === "booting") {
        return <LoadingScreen label={t("src.screens.chatscreen.loading_the_conversation_lane")} />;
    }

    if (status === "anonymous") {
        return <Redirect href="/login" />;
    }

    const profileImageUri = resolveAdminAssetUrl(adminBaseUrl, user?.image || "");
    const greetingEmptyState = !activeConversationId && projection.projectedMessages.length === 0
        ? {
            title: getDayGreeting(locale),
            subtitle: t("src.screens.chatscreen.choose_a_workspace_then_start_a_new_conversation"),
            actionLabel: t("src.screens.chatscreen.start_a_new_conversation"),
            onAction: () => void handleNewConversation(),
            variant: "greeting" as const,
        }
        : null;
    const legacyChatEmptyState = activeConversationId && legacyChatUnsupported && projection.projectedMessages.length === 0
        ? {
            icon: "archive-alert-outline" as const,
            title: t("src.screens.chatscreen.legacy_conversation_is_not_on_canonical_transcript"),
            subtitle: t("src.screens.chatscreen.this_history_record_has_no_stable_transcript_nodes_to_avoid_mixed_source_drift_this_version_no_longer_replays_legacy_chat_content"),
        }
        : null;
    const conversationWorkspacePath = String(projection.activeConversation?.workspacePath || "").trim();
    const transcriptWorkspacePath = useMemo(() => deriveWorkspacePathFromMessages(messages), [messages]);
    const scopedWorkspacePath = String(scopeBinding?.workspacePath || "").trim();
    const mainWorkspacePathValue = String(mainWorkspacePath || "").trim();
    const scopeUsesMainWorkspace = scopedWorkspacePath
        && normalizeWorkspacePathForDisplay(scopedWorkspacePath) === normalizeWorkspacePathForDisplay(mainWorkspacePathValue);
    const conversationUsesMainWorkspace = conversationWorkspacePath
        && normalizeWorkspacePathForDisplay(conversationWorkspacePath) === normalizeWorkspacePathForDisplay(mainWorkspacePathValue);
    const transcriptUsesMainWorkspace = transcriptWorkspacePath
        && normalizeWorkspacePathForDisplay(transcriptWorkspacePath) === normalizeWorkspacePathForDisplay(mainWorkspacePathValue);
    const effectiveWorkspacePath = scopedWorkspacePath && !scopeUsesMainWorkspace
        ? scopedWorkspacePath
        : conversationWorkspacePath && !conversationUsesMainWorkspace
            ? conversationWorkspacePath
            : transcriptWorkspacePath && !transcriptUsesMainWorkspace
                ? transcriptWorkspacePath
                : scopedWorkspacePath;
    const currentWorkspaceLabel = boundProject?.name
        || (effectiveWorkspacePath && normalizeWorkspacePathForDisplay(effectiveWorkspacePath) !== normalizeWorkspacePathForDisplay(mainWorkspacePathValue) ? deriveWorkspaceLabelFromPath(effectiveWorkspacePath) : "")
        || t("src.screens.chatscreen.main_workspace");
    const currentWorkspacePath = effectiveWorkspacePath || mainWorkspacePathValue || t("src.screens.chatscreen.unbound");
    const showWorkspaceChooser = !activeConversationId && workspaceChooserVisible;
    const composerHorizontalInset = isLandscape ? 18 : 10;
    const composerBottomInset = Math.max(safeAreaInsets.bottom, Platform.OS === "ios" ? 8 : 10);
    const todosVisible = projection.todos.length > 0;
    const chatBottomInset = Math.max(isLandscape ? 32 : 24, bottomLayerHeight + 18);
    const pickerOverlayVisible = commandPickerOpen || skillPickerOpen;
    const pickerOverlayMode = commandPickerOpen ? "command" : skillPickerOpen ? "skill" : null;
    const runControlStatus = String(projection.runControlState.status || "").trim().toLowerCase();
    const todosAllCompleted = projection.todos.length > 0
        && projection.todos.every((item) => {
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
    const todoHudShouldAutoHide = todosAllCompleted
        && !projection.runControlState.pendingApproval
        && !projectionHasActiveProcess
        && !["running", "waiting_input", "waiting_approval", "queued", "pending", "starting", "streaming"].includes(runControlStatus);
    const composerRunActive = isQueueEligibleRunStatus(runControlStatus);
    const composerCanStop = Boolean(composerRunActive && (projection.runControlState.canInterrupt || projection.runControlState.runId));
    const hasOverlayLayer = Boolean(
        pickerOverlayVisible
        || hudProcesses.length > 0
        || todosVisible,
    );
    const accessoryBottomOffset = bottomLayerHeight > 0 ? bottomLayerHeight + 8 : 144;
    const hudBottomOffset = accessoryBottomOffset + 10;
    const pickerBottomOffset = accessoryBottomOffset;
    const visibleQueuedMessages = queuedMessages.filter((item) => {
        const state = String(item.state || "pending").trim().toLowerCase();
        return state === "pending" || state === "promoted";
    });

    const handleSelectCommandFromPicker = (command: CommandPresetSummary) => {
        setSelectedCommand(command);
        setActiveQueryMode(null);
        setActiveQueryText("");
    };

    const handleSelectSkillFromPicker = (skill: SkillReferenceSummary) => {
        setSelectedSkills((current) => {
            const exists = current.some((item) => item.name === skill.name && (item.path || "") === (skill.path || ""));
            return exists ? current : [...current, skill];
        });
        setActiveQueryMode(null);
        setActiveQueryText("");
    };

    const handleSelectSubagentFamilyFromPicker = (family: SubagentFamilySummary) => {
        setSelectedSubagentFamilies((current) => {
            const exists = current.some((item) => item.familyId === family.familyId);
            return exists ? current : [...current, family];
        });
        setActiveQueryMode(null);
        setActiveQueryText("");
    };

    const overlayDockContent = hasOverlayLayer ? (
        <View pointerEvents="box-none" style={styles.keyboardOverlayHost}>
            {pickerOverlayVisible ? (
                <ComposerPickerOverlay
                    visible={pickerOverlayVisible}
                    mode={pickerOverlayMode}
                    left={composerHorizontalInset}
                    right={composerHorizontalInset}
                    bottom={pickerBottomOffset}
                    position="absolute"
                    commands={filteredCommands}
                    mentions={filteredMentionItems}
                    onSelectCommand={handleSelectCommandFromPicker}
                    onSelectSkill={handleSelectSkillFromPicker}
                    onSelectSubagentFamily={handleSelectSubagentFamilyFromPicker}
                />
            ) : null}
            <View
                pointerEvents={pickerOverlayVisible ? "none" : "box-none"}
                style={[
                    styles.hudOverlayStack,
                    {
                        bottom: hudBottomOffset,
                    },
                ]}
            >
                {hudProcesses.length > 0 ? (
                    <View style={styles.processesOverlayDock} pointerEvents="box-none">
                        <ProcessesHUD processes={hudProcesses} />
                    </View>
                ) : null}
                {todosVisible ? (
                    <View style={styles.todosOverlayDock} pointerEvents="auto">
                        <TodosHUD items={projection.todos} shouldAutoHide={todoHudShouldAutoHide} />
                    </View>
                ) : null}
            </View>
        </View>
    ) : null;

    const composerDockContent = (
        <View
            pointerEvents="box-none"
            style={[
                styles.composerDock,
                isLandscape && styles.composerDockLandscape,
                {
                    paddingBottom: composerBottomInset,
                    paddingLeft: composerHorizontalInset,
                    paddingRight: composerHorizontalInset,
                },
            ]}
            onLayout={(event) => {
                const nextHeight = Math.round(event.nativeEvent.layout.height);
                if (nextHeight > 0 && nextHeight !== bottomLayerHeight) {
                    setBottomLayerHeight(nextHeight);
                }
            }}
        >
            {activeConversationId && visibleQueuedMessages.length > 0 ? (
                <GlassCard style={[styles.queuedMessageStrip, { backgroundColor: palette.surfaceStrong, borderColor: palette.border }]}>
                    <Pressable
                        style={styles.queuedMessageStripHeader}
                        onPress={() => setQueuedMessagesCollapsed((current) => !current)}
                        accessibilityRole="button"
                    >
                        <View style={styles.queuedMessageStripTitleRow}>
                            <Text style={[styles.queuedMessageStripTitle, { color: palette.text }]}>
                                {t("src.screens.chatscreen.queued_messages")}
                            </Text>
                            <Text style={[styles.queuedMessageStripCount, { backgroundColor: palette.surface, color: palette.textMuted }]}>
                                {visibleQueuedMessages.length}
                            </Text>
                        </View>
                        <View style={styles.queuedMessageStripHeaderRight}>
                            <Text style={[styles.queuedMessageStripHint, { color: palette.textMuted }]} numberOfLines={1}>
                                {t("src.screens.chatscreen.queued_messages_waiting_hint")}
                            </Text>
                            <MaterialCommunityIcons
                                name={queuedMessagesCollapsed ? "chevron-up" : "chevron-down"}
                                size={18}
                                color={palette.textMuted}
                            />
                        </View>
                    </Pressable>
                    {!queuedMessagesCollapsed ? (
                        <ScrollView
                            horizontal
                            showsHorizontalScrollIndicator={false}
                            contentContainerStyle={styles.queuedMessageList}
                            keyboardShouldPersistTaps="handled"
                        >
                            {visibleQueuedMessages.map((item) => {
                                const state = String(item.state || "pending").trim().toLowerCase();
                                const promoted = state === "promoted";
                                return (
                                    <View key={item.id} style={[styles.queuedMessageChip, { backgroundColor: palette.surface, borderColor: promoted ? palette.primary : palette.border }]}>
                                        <View style={styles.queuedMessageChipHeader}>
                                            <Text style={[styles.queuedMessageState, { color: promoted ? palette.primary : palette.textMuted }]}>
                                                {promoted
                                                    ? t("src.screens.chatscreen.queued_message_promoted")
                                                    : t("src.screens.chatscreen.queued_message_pending")}
                                            </Text>
                                            <Text style={[styles.queuedMessageOrdinal, { color: palette.textSoft }]}>
                                                #{Number(item.ordinal || 1)}
                                            </Text>
                                        </View>
                                        <Text style={[styles.queuedMessagePreview, { color: palette.text }]} numberOfLines={2}>
                                            {item.content || t("src.screens.chatscreen.empty_queued_message")}
                                        </Text>
                                        <View style={styles.queuedMessageActions}>
                                            <Pressable
                                                style={[styles.queuedMessageActionButton, { borderColor: palette.border, opacity: promoted ? 0.48 : 1 }]}
                                                disabled={promoted}
                                                onPress={() => handleOpenQueuedMessageEditor(item)}
                                            >
                                                <Text style={[styles.queuedMessageActionText, { color: palette.textMuted }]}>
                                                    {t("src.screens.chatscreen.edit")}
                                                </Text>
                                            </Pressable>
                                            <Pressable
                                                style={[styles.queuedMessageActionButton, { borderColor: palette.border, opacity: promoted ? 0.48 : 1 }]}
                                                disabled={promoted}
                                                onPress={() => void handlePromoteQueuedMessage(item)}
                                            >
                                                <Text style={[styles.queuedMessageActionText, { color: palette.primary }]}>
                                                    {t("src.screens.chatscreen.promote_guidance")}
                                                </Text>
                                            </Pressable>
                                            <Pressable
                                                style={[styles.queuedMessageActionButton, { borderColor: palette.border }]}
                                                onPress={() => void handleCancelQueuedMessage(item)}
                                            >
                                                <Text style={[styles.queuedMessageActionText, { color: palette.danger }]}>
                                                    {t("src.screens.chatscreen.cancel")}
                                                </Text>
                                            </Pressable>
                                        </View>
                                    </View>
                                );
                            })}
                        </ScrollView>
                    ) : null}
                </GlassCard>
            ) : null}
            {activeConversationId ? (
                <Composer
                    bodyValue={input}
                    onChangeBody={handleBodyInputChange}
                    activeQueryMode={activeQueryMode}
                    activeQueryText={activeQueryText}
                    onChangeQueryText={setActiveQueryText}
                    onBodyBackspace={handleComposerBackspace}
                    onQueryBackspace={handleQueryBackspace}
                    onSend={() => void handleSend()}
                    busy={sending}
                    isRunning={composerRunActive}
                    canStop={composerCanStop}
                    onStop={() => void handleRunCommand("interrupt")}
                    allowQueueWhileRunning
                    selectedCommand={selectedCommand}
                    selectedSkills={selectedSkills}
                    selectedSubagentFamilies={selectedSubagentFamilies}
                    taskPlanningMode={taskPlanningMode}
                    onToggleTaskPlanningMode={() => setTaskPlanningMode((current) => !current)}
                    uploadedFiles={uploadedFiles}
                    onRemoveUploadedFile={(file) => setUploadedFiles((current) => removeUploadedWorkspaceFile(current, file))}
                    adminBaseUrl={adminBaseUrl}
                    onPickAttachment={() => void handlePickAttachment()}
                    onToggleRecording={() => void handleToggleRecording()}
                    attachmentBusy={attachmentBusy}
                    recording={recorderState.isRecording}
                    transcribing={transcribing}
                />
            ) : (
                <GlassCard style={[styles.workspaceHintCard, { backgroundColor: palette.surfaceStrong, borderColor: palette.border }]}>
                    <Text style={[styles.workspaceHintText, { color: palette.textMuted }]}>
                        {t("src.screens.chatscreen.choose_the_main_workspace_or_a_project_workspace_before_starting_a_new_conversation")}
                    </Text>
                </GlassCard>
            )}
        </View>
    );

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
                    onBrandPress={() => void handleBrandPress()}
                    onProfilePress={() => router.push("/settings" as Href)}
                />

                <View style={styles.chatShell}>
                    <View style={[styles.chatStage, isLandscape && styles.chatStageLandscape]}>
                        <View style={styles.chatWindowWrap}>
                            {showWorkspaceChooser ? (
                                <ScrollView
                                    contentContainerStyle={styles.workspaceChooserStage}
                                    keyboardShouldPersistTaps="handled"
                                    showsVerticalScrollIndicator={false}
                                >
                                    <GlassCard style={[styles.workspaceChooserCard, { backgroundColor: palette.surfaceStrong, borderColor: palette.border }]}>
                                        <View style={styles.workspaceChooserHeader}>
                                            <View style={styles.workspaceChooserHeaderBody}>
                                                <Text style={[styles.workspaceChooserTitle, { color: palette.text }]}>
                                                    {t("src.screens.chatscreen.choose_a_workspace")}
                                                </Text>
                                                <Text style={[styles.workspaceChooserSubtitle, { color: palette.textMuted }]}>
                                                    {t("src.screens.chatscreen.history_always_stays_higher_priority_this_only_appears_when_you_explicitly_start_a_new_conversation")}
                                                </Text>
                                            </View>
                                            <Pressable
                                                style={[styles.workspaceChooserCloseButton, { backgroundColor: palette.surface, borderColor: palette.border }]}
                                                onPress={() => {
                                                    setWorkspaceChooserVisible(false);
                                                    clearNewConversationIntent();
                                                }}
                                            >
                                                <MaterialCommunityIcons name="close" size={18} color={palette.textMuted} />
                                            </Pressable>
                                        </View>

                                        <Pressable
                                            style={[styles.workspaceOptionCard, { backgroundColor: palette.surface, borderColor: palette.border }]}
                                            disabled={workspaceChooserBusy || !mainWorkspacePath}
                                            onPress={() => void createBoundConversation({ kind: "main" })}
                                        >
                                            <Text style={[styles.workspaceOptionTitle, { color: palette.text }]}>
                                                {t("src.screens.chatscreen.main_workspace")}
                                            </Text>
                                            <Text style={[styles.workspaceOptionMeta, { color: palette.textMuted }]}>
                                                {mainWorkspacePath || t("src.screens.chatscreen.loading_main_workspace_path")}
                                            </Text>
                                        </Pressable>

                                        <View style={[styles.workspaceChooserSection, { borderColor: palette.border }]}>
                                            <Text style={[styles.workspaceSectionLabel, { color: palette.textMuted }]}>
                                                {t("src.screens.chatscreen.existing_project_workspaces")}
                                            </Text>
                                            <ScrollView
                                                style={styles.workspaceOptionScrollList}
                                                contentContainerStyle={styles.workspaceOptionList}
                                                nestedScrollEnabled
                                                showsVerticalScrollIndicator
                                            >
                                                {availableProjects.length === 0 ? (
                                                    <Text style={[styles.workspaceEmptyText, { color: palette.textMuted }]}>
                                                        {t("src.screens.chatscreen.no_project_workspaces_are_available_yet")}
                                                    </Text>
                                                ) : (
                                                    availableProjects.map((project) => (
                                                        <Pressable
                                                            key={project.id || project.name}
                                                            style={[styles.workspaceOptionCard, { backgroundColor: palette.surface, borderColor: palette.border }]}
                                                            disabled={workspaceChooserBusy || !project.id}
                                                            onPress={() => project.id && void createBoundConversation({ kind: "project", projectId: project.id })}
                                                        >
                                                            <Text style={[styles.workspaceOptionTitle, { color: palette.text }]}>
                                                                {project.name || project.id || t("src.screens.chatscreen.unnamed_project")}
                                                            </Text>
                                                            <Text style={[styles.workspaceOptionMeta, { color: palette.textMuted }]}>
                                                                {project.workspacePath || project.id || t("src.screens.chatscreen.path_unavailable")}
                                                            </Text>
                                                        </Pressable>
                                                    ))
                                                )}
                                            </ScrollView>
                                        </View>

                                        <View style={[styles.workspaceChooserSection, { borderColor: palette.border }]}>
                                            <Text style={[styles.workspaceSectionLabel, { color: palette.textMuted }]}>
                                                {t("src.screens.chatscreen.create_a_project_workspace")}
                                            </Text>
                                            <Text style={[styles.workspaceSectionHint, { color: palette.textMuted }]}>
                                                {t("src.screens.chatscreen.choose_or_create_folder_on_engine_machine")}
                                            </Text>
                                            <WorkspaceFolderExplorer
                                                roots={folderRoots}
                                                selectedPath={selectedFolderPath}
                                                expandedPaths={expandedFolderPaths}
                                                loadingPaths={loadingFolderPaths}
                                                onSelect={(node) => setSelectedFolderPath(node.path)}
                                                onToggle={(node) => void toggleFolderNode(node)}
                                                emptyLabel={t("src.screens.chatscreen.folder_tree_empty")}
                                            />
                                            <View style={styles.workspaceSelectedPathCard}>
                                                <Text style={[styles.workspaceSelectedPathLabel, { color: palette.textMuted }]}>
                                                    {t("src.screens.chatscreen.selected_folder")}
                                                </Text>
                                                <Text style={[styles.workspaceSelectedPathText, { color: palette.text }]} numberOfLines={2}>
                                                    {selectedFolderPath || t("src.screens.chatscreen.no_folder_selected")}
                                                </Text>
                                            </View>
                                            <View style={styles.workspaceCreateRow}>
                                                <TextInput
                                                    value={newFolderName}
                                                    onChangeText={setNewFolderName}
                                                    placeholder={t("src.screens.chatscreen.optional_new_child_folder_name")}
                                                    placeholderTextColor={palette.textSoft}
                                                    style={[styles.workspaceNameInput, { color: palette.text, backgroundColor: palette.surface, borderColor: palette.border }]}
                                                />
                                                <Pressable
                                                    style={[
                                                        styles.workspaceCreateButton,
                                                        {
                                                            backgroundColor: palette.primary,
                                                            opacity: workspaceChooserBusy || !selectedFolderPath ? 0.56 : 1,
                                                        },
                                                    ]}
                                                    disabled={workspaceChooserBusy || !selectedFolderPath}
                                                    onPress={() => void handleCreateFromSelectedFolder()}
                                                >
                                                    {workspaceChooserBusy ? (
                                                        <ActivityIndicator size="small" color="#FFFFFF" />
                                                    ) : (
                                                        <Text style={styles.workspaceCreateButtonText}>
                                                            {newFolderName.trim() ? t("src.screens.chatscreen.create_folder_and_start") : t("src.screens.chatscreen.use_selected_folder")}
                                                        </Text>
                                                    )}
                                                </Pressable>
                                            </View>
                                        </View>

                                        <View style={[styles.workspaceChooserSection, { borderColor: palette.border }]}>
                                            <Text style={[styles.workspaceSectionLabel, { color: palette.textMuted }]}>
                                                {t("src.screens.chatscreen.absolute_path_fallback")}
                                            </Text>
                                            <Text style={[styles.workspaceSectionHint, { color: palette.textMuted }]}>
                                                {t("src.screens.chatscreen.enter_an_absolute_project_folder_path_the_folder_name_becomes_the_project_name")}
                                            </Text>
                                            <View style={styles.workspaceCreateRow}>
                                                <TextInput
                                                    value={newProjectPath}
                                                    onChangeText={setNewProjectPath}
                                                    placeholder={t("src.screens.chatscreen.project_folder_path_examples")}
                                                    placeholderTextColor={palette.textSoft}
                                                    style={[styles.workspaceNameInput, { color: palette.text, backgroundColor: palette.surface, borderColor: palette.border }]}
                                                />
                                                <Pressable
                                                    style={[
                                                        styles.workspaceCreateButton,
                                                        {
                                                            backgroundColor: palette.primary,
                                                            opacity: workspaceChooserBusy || newProjectPath.trim().length === 0 ? 0.56 : 1,
                                                        },
                                                    ]}
                                                    disabled={workspaceChooserBusy || newProjectPath.trim().length === 0}
                                                    onPress={() => void handleCreateProjectConversation()}
                                                >
                                                    {workspaceChooserBusy ? (
                                                        <ActivityIndicator size="small" color="#FFFFFF" />
                                                    ) : (
                                                        <Text style={styles.workspaceCreateButtonText}>{t("src.screens.chatscreen.create_and_start")}</Text>
                                                    )}
                                                </Pressable>
                                            </View>
                                        </View>
                                    </GlassCard>
                                </ScrollView>
                            ) : (
                                <ChatWindow
                                    adminBaseUrl={adminBaseUrl}
                                    messages={projection.projectedMessages}
                                    scrollLocked={pickerOverlayVisible}
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
                                    processes={hudProcesses}
                                    contextReferences={projection.contextReferences}
                                    pendingApproval={projection.pendingApproval}
                                    pendingApprovalCount={projection.pendingApprovalCount}
                                    approvalBusy={sending}
                                    onResolveApproval={handleApprovalResolve}
                                    onOpenApprovalPanel={openApprovalPanel}
                                    isLandscape={isLandscape}
                                    bottomInset={chatBottomInset}
                                    emptyState={legacyChatEmptyState || greetingEmptyState}
                                />
                            )}

                            <EdgeActionRail
                                side="left"
                                open={leftRailOpen}
                                expandedWidth={154}
                                top={4}
                                onOpen={() => {
                                    setLeftRailOpen(true);
                                    setRightRailOpen(false);
                                }}
                                onClose={() => setLeftRailOpen(false)}
                            >
                                <View style={styles.leftEdgeRailContent}>
                                    <Pressable
                                        style={[styles.edgeIconButton, { backgroundColor: palette.surfaceStrong, borderColor: "transparent" }]}
                                        onPress={() => {
                                            setLeftRailOpen(false);
                                            setHistoryOpen(true);
                                        }}
                                    >
                                        <MaterialCommunityIcons name="view-headline" size={20} color={palette.text} />
                                    </Pressable>
                                    <Pressable
                                        accessibilityRole="button"
                                        accessibilityLabel={t("src.screens.chatscreen.current_workspace")}
                                        style={[
                                            styles.edgeIconButton,
                                            {
                                                backgroundColor: workspaceInfoOpen || workspaceChooserVisible ? palette.primarySoft : palette.surfaceStrong,
                                                borderColor: workspaceInfoOpen || workspaceChooserVisible ? palette.primary : "transparent",
                                            },
                                        ]}
                                        onPress={() => {
                                            setLeftRailOpen(false);
                                            if (activeConversationId) {
                                                setWorkspaceInfoOpen(true);
                                                return;
                                            }
                                            setWorkspaceChooserVisible(true);
                                            clearNewConversationIntent();
                                        }}
                                    >
                                        {scopeLoading ? (
                                            <ActivityIndicator size="small" color={workspaceInfoOpen || workspaceChooserVisible ? palette.primary : palette.textMuted} />
                                        ) : (
                                            <MaterialCommunityIcons
                                                name="file-tree-outline"
                                                size={18}
                                                color={workspaceInfoOpen || workspaceChooserVisible ? palette.primary : palette.textMuted}
                                            />
                                        )}
                                    </Pressable>
                                    <RunControlBar
                                        runId={projection.runControlState.runId}
                                        status={projection.runControlState.status}
                                        pendingApproval={projection.runControlState.pendingApproval}
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
                            </EdgeActionRail>

                            <EdgeActionRail
                                side="right"
                                open={rightRailOpen}
                                expandedWidth={356}
                                top={4}
                                onOpen={() => {
                                    setRightRailOpen(true);
                                    setLeftRailOpen(false);
                                }}
                                onClose={() => setRightRailOpen(false)}
                            >
                                <RuntimeDock
                                    items={projection.runtimeStageModel.items}
                                    selectedRuntimeId={projection.selectedRuntimeId}
                                    panelOpen={runtimePanelOpen}
                                    onSelectRuntime={(runtimeId) => {
                                        setSelectedRuntimeId(runtimeId);
                                        setRuntimePanelOpen(true);
                                    }}
                                    leadingAccessory={(
                                        <Pressable
                                            accessibilityRole="button"
                                            accessibilityLabel={t("src.components.chat.runtimetimelinepanel.episode_topology")}
                                            style={({ pressed }) => [
                                                styles.executionMapButton,
                                                {
                                                    backgroundColor: runtimePanelOpen ? palette.primarySoft : palette.surfaceStrong,
                                                    borderColor: runtimePanelOpen ? palette.primary : "transparent",
                                                    opacity: pressed ? 0.82 : 1,
                                                },
                                            ]}
                                            onPress={() => setRuntimePanelOpen(true)}
                                        >
                                            <MaterialCommunityIcons
                                                name="file-tree-outline"
                                                size={16}
                                                color={runtimePanelOpen ? palette.primary : palette.textMuted}
                                            />
                                        </Pressable>
                                    )}
                                />
                            </EdgeActionRail>
                        </View>
                    </View>
                    {overlayDockContent}
                    {Platform.OS === "ios" ? (
                        <KeyboardStickyView
                            pointerEvents="box-none"
                            style={styles.keyboardDockHost}
                            offset={{ closed: 0, opened: 0 }}
                        >
                            {composerDockContent}
                        </KeyboardStickyView>
                    ) : (
                        <View pointerEvents="box-none" style={styles.keyboardDockHost}>
                            {composerDockContent}
                        </View>
                    )}
                </View>

                <RuntimeTimelinePanel
                    visible={runtimePanelOpen}
                    items={projection.runtimeStageModel.items}
                    selectedRuntimeId={projection.selectedRuntimeId}
                    selectedRuntimeDockItem={projection.selectedRuntimeDockItem}
                    activities={projection.runtimeStageModel.activities}
                    processes={hudProcesses}
                    currentRunLabel={projection.currentRunLabel}
                    currentStepTitle={projection.currentStepTitle}
                    onClose={() => setRuntimePanelOpen(false)}
                    onSelectRuntime={setSelectedRuntimeId}
                />

                <GovernanceApprovalModal
                    visible={governanceApprovalOpen}
                    approval={projection.governancePendingApproval}
                    busy={governanceApprovalBusy}
                    onApprove={(answer) => handleGovernanceApprovalResolve(answer, true)}
                    onReject={(answer) => handleGovernanceApprovalResolve(answer, false)}
                    onViewDetails={handleGovernanceApprovalViewDetails}
                    onClose={handleGovernanceApprovalDismiss}
                />

                <Modal visible={Boolean(editingQueuedMessage)} transparent animationType="fade" onRequestClose={() => setEditingQueuedMessage(null)}>
                    <View style={[styles.scopeSheetOverlay, { backgroundColor: palette.overlay }]}>
                        <Pressable style={StyleSheet.absoluteFill} onPress={() => setEditingQueuedMessage(null)} />
                        <GlassCard style={[styles.queuedEditCard, { backgroundColor: palette.surfaceStrong, borderColor: palette.border }]}>
                            <Text style={[styles.queuedEditTitle, { color: palette.text }]}>
                                {t("src.screens.chatscreen.edit_queued_message")}
                            </Text>
                            <Text style={[styles.queuedEditSubtitle, { color: palette.textMuted }]}>
                                {t("src.screens.chatscreen.edit_queued_message_hint")}
                            </Text>
                            <TextInput
                                value={queuedMessageEditText}
                                onChangeText={setQueuedMessageEditText}
                                multiline
                                placeholder={t("src.screens.chatscreen.queued_message_placeholder")}
                                placeholderTextColor={palette.textSoft}
                                style={[styles.queuedEditInput, { color: palette.text, backgroundColor: palette.surface, borderColor: palette.border }]}
                            />
                            <View style={styles.queuedEditActions}>
                                <Pressable
                                    style={[styles.queuedEditButton, { backgroundColor: palette.surface, borderColor: palette.border }]}
                                    onPress={() => setEditingQueuedMessage(null)}
                                >
                                    <Text style={[styles.queuedEditButtonText, { color: palette.textMuted }]}>
                                        {t("src.screens.chatscreen.cancel")}
                                    </Text>
                                </Pressable>
                                <Pressable
                                    style={[styles.queuedEditButton, { backgroundColor: palette.primary, borderColor: palette.primary, opacity: queuedMessageEditText.trim() ? 1 : 0.56 }]}
                                    disabled={!queuedMessageEditText.trim() || queuedMessageEditBusy}
                                    onPress={() => void handleSaveQueuedMessageEdit()}
                                >
                                    {queuedMessageEditBusy ? (
                                        <ActivityIndicator size="small" color="#FFFFFF" />
                                    ) : (
                                        <Text style={styles.queuedEditPrimaryText}>
                                            {t("src.screens.chatscreen.save")}
                                        </Text>
                                    )}
                                </Pressable>
                            </View>
                        </GlassCard>
                    </View>
                </Modal>

                <Modal visible={workspaceInfoOpen} transparent animationType="fade" onRequestClose={() => setWorkspaceInfoOpen(false)}>
                    <View style={[styles.scopeSheetOverlay, { backgroundColor: palette.overlay }]}>
                        <Pressable style={StyleSheet.absoluteFill} onPress={() => setWorkspaceInfoOpen(false)} />
                        <GlassCard style={[styles.scopeSheetCard, { backgroundColor: palette.surfaceStrong, borderColor: palette.border }]}>
                            <View style={[styles.scopeSheetHandle, { backgroundColor: palette.border }]} />
                            <View style={styles.scopeSheetHeader}>
                                <View style={styles.scopeSheetHeaderText}>
                                    <Text style={[styles.contextTitle, { color: palette.text }]}>{t("src.screens.chatscreen.current_workspace")}</Text>
                                    <Text style={[styles.contextSubtitle, { color: palette.textMuted }]}>
                                        {t("src.screens.chatscreen.this_conversation_binding_is_frozen_after_creation_start_a_new_conversation_to_switch_workspaces")}
                                    </Text>
                                </View>
                                <Pressable
                                    style={[styles.scopeSheetCloseButton, { backgroundColor: palette.surface, borderColor: palette.border }]}
                                    onPress={() => setWorkspaceInfoOpen(false)}
                                >
                                    <MaterialCommunityIcons name="close" size={18} color={palette.textMuted} />
                                </Pressable>
                            </View>

                            <ScrollView
                                style={styles.scopeSheetScroll}
                                contentContainerStyle={styles.scopeSheetScrollContent}
                                keyboardShouldPersistTaps="handled"
                                showsVerticalScrollIndicator={false}
                            >
                                <View style={styles.scopeSheetSection}>
                                    <Text style={[styles.scopeSheetSectionLabel, { color: palette.textMuted }]}>
                                        {t("src.screens.chatscreen.current_binding")}
                                    </Text>
                                    <View style={styles.scopeOptionGrid}>
                                        <View style={[styles.scopeOptionChip, { backgroundColor: palette.primarySoft, borderColor: `${palette.primary}33` }]}>
                                            <Text style={[styles.scopeOptionTitle, { color: palette.primaryDeep }]}>
                                                {currentWorkspaceLabel}
                                            </Text>
                                            <Text style={[styles.scopeOptionMeta, { color: palette.textMuted }]}>
                                                {currentWorkspacePath}
                                            </Text>
                                        </View>
                                    </View>
                                </View>

                                <View style={styles.scopeSheetSection}>
                                    <Text style={[styles.scopeSheetSectionLabel, { color: palette.textMuted }]}>
                                        {t("src.screens.chatscreen.conversation_info")}
                                    </Text>
                                    <View style={styles.contextChips}>
                                        <View style={[styles.contextChip, { backgroundColor: palette.primarySoft, borderColor: `${palette.primary}1A` }]}>
                                            <Text style={[styles.contextChipText, { color: palette.primaryDeep }]}>
                                                {t("src.screens.artifactsscreen.conversation")}：{projection.activeConversation?.title || t("src.screens.chatscreen.current_conversation")}
                                            </Text>
                                        </View>
                                        <View style={[styles.contextChip, { backgroundColor: palette.surface, borderColor: palette.border }]}>
                                            <Text style={[styles.contextChipText, { color: palette.textMuted }]}>
                                                {t("src.screens.chatscreen.workspace_kind")}：{currentWorkspaceLabel}
                                            </Text>
                                        </View>
                                        <View style={[styles.contextChip, { backgroundColor: palette.surface, borderColor: palette.border }]}>
                                            <Text style={[styles.contextChipText, { color: palette.textMuted }]}>
                                                Scope：{scopeBinding?.resolvedScope || "global"}
                                            </Text>
                                        </View>
                                        <View style={[styles.contextChip, { backgroundColor: palette.surface, borderColor: palette.border }]}>
                                            <Text style={[styles.contextChipText, { color: palette.textMuted }]}>
                                                {t("src.screens.chatscreen.path")}：{currentWorkspacePath}
                                            </Text>
                                        </View>
                                    </View>
                                </View>
                            </ScrollView>
                        </GlassCard>
                    </View>
                </Modal>

                <Modal visible={desktopPreviewOpen} transparent animationType="fade" onRequestClose={() => void closeDesktopPreview()}>
                    <View
                        style={[
                            styles.previewOverlay,
                            desktopPreviewFullscreen && styles.previewOverlayFullscreen,
                            { backgroundColor: palette.overlay },
                        ]}
                    >
                        <Pressable style={StyleSheet.absoluteFill} onPress={() => void closeDesktopPreview()} />
                        <Pressable
                            style={[styles.previewFullscreenButton, { backgroundColor: palette.surfaceStrong, borderColor: palette.border }]}
                            onPress={() => setDesktopPreviewFullscreen((current) => !current)}
                        >
                            <MaterialCommunityIcons
                                name={desktopPreviewFullscreen ? "fullscreen-exit" : "fullscreen"}
                                size={22}
                                color={palette.text}
                            />
                        </Pressable>
                        <Pressable
                            style={[styles.previewCloseButton, { backgroundColor: palette.surfaceStrong, borderColor: palette.border }]}
                            onPress={() => void closeDesktopPreview()}
                        >
                            <MaterialCommunityIcons name="close" size={22} color={palette.text} />
                        </Pressable>

                        <View
                            style={[
                                styles.previewCard,
                                desktopPreviewFullscreen && styles.previewCardFullscreen,
                                { backgroundColor: themeMode === "dark" ? "#020617" : "#000000" },
                            ]}
                        >
                            <WebView
                                ref={desktopPreviewWebViewRef}
                                originWhitelist={["*"]}
                                source={desktopPreviewFallbackUrl
                                    ? {
                                        uri: desktopPreviewFallbackUrl,
                                        ...(accessToken ? { headers: { Authorization: `Bearer ${accessToken}` } } : {}),
                                    }
                                    : { html: desktopPreviewHtml }}
                                style={[styles.previewWebview, desktopPreviewFullscreen && styles.previewWebviewFullscreen]}
                                allowsInlineMediaPlayback
                                mediaPlaybackRequiresUserAction={false}
                                setSupportMultipleWindows={false}
                                javaScriptEnabled
                                onMessage={(event) => void handleDesktopPreviewMessage(event)}
                                onError={() => {
                                    setDesktopPreviewState("error");
                                    setDesktopPreviewBusy(false);
                                    setDesktopPreviewError(t("src.screens.chatscreen.desktop_preview_is_not_ready_yet_please_wait"));
                                }}
                            />
                            {(desktopPreviewBusy || desktopPreviewState === "loading" || (!desktopPreviewError && desktopPreviewState !== "preview")) ? (
                                <View style={styles.previewLoadingWrap}>
                                    <ActivityIndicator color="#FFFFFF" />
                                    <Text style={styles.previewLoadingText}>{t("src.screens.chatscreen.connecting_desktop_stream")}</Text>
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
    chatShell: {
        flex: 1,
        position: "relative",
    },
    chatStage: {
        flex: 1,
        position: "relative",
    },
    chatStageLandscape: {
        alignSelf: "center",
        width: "100%",
        maxWidth: 980,
    },
    chatStageHeader: {
        position: "absolute",
        top: 10,
        left: 12,
        right: 12,
        zIndex: 18,
        gap: 8,
    },
    chatStageHeaderLandscape: {
        top: 12,
        left: 18,
        right: 18,
    },
    chatStageTopRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
        minWidth: 0,
    },
    chatStageTopRowLandscape: {
        alignItems: "center",
    },
    historyFab: {
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
        flexShrink: 0,
    },
    runControlWrap: {
        flexShrink: 0,
        justifyContent: "center",
    },
    chatWindowWrap: {
        flex: 1,
        minHeight: 0,
        position: "relative",
    },
    controlRailPrimary: {
        minHeight: 40,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        flex: 1,
        minWidth: 0,
        borderRadius: radii.pill,
        paddingHorizontal: 4,
        paddingVertical: 4,
        borderWidth: 1,
        shadowColor: "#0F172A",
        shadowOpacity: 0.03,
        shadowRadius: 10,
        shadowOffset: { width: 0, height: 6 },
        elevation: 2,
    },
    runtimeDockInline: {
        flex: 1,
        minWidth: 0,
        justifyContent: "center",
    },
    leftEdgeRailContent: {
        flexDirection: "row",
        alignItems: "center",
        gap: 1,
    },
    edgeIconButton: {
        width: 32,
        height: 32,
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 16,
        borderWidth: StyleSheet.hairlineWidth,
    },
    executionMapButton: {
        width: 28,
        height: 28,
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 12,
        borderWidth: StyleSheet.hairlineWidth,
    },
    scopeTrigger: {
        width: 40,
        height: 40,
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 20,
        borderWidth: 1,
    },
    scopeTriggerIconWrap: {
        width: 18,
        height: 18,
        alignItems: "center",
        justifyContent: "center",
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
        paddingBottom: 32,
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
    keyboardOverlayHost: {
        ...StyleSheet.absoluteFillObject,
        zIndex: 34,
    },
    keyboardDockHost: {
        width: "100%",
        zIndex: 28,
        alignSelf: "stretch",
    },
    composerDock: {
        width: "100%",
        overflow: "visible",
    },
    composerDockLandscape: {
        alignSelf: "stretch",
    },
    queuedMessageStrip: {
        borderWidth: 1,
        borderRadius: 18,
        paddingHorizontal: 12,
        paddingVertical: 10,
        marginBottom: 8,
        gap: 8,
    },
    queuedMessageStripHeader: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 10,
    },
    queuedMessageStripTitleRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
        flexShrink: 0,
    },
    queuedMessageStripTitle: {
        fontSize: 13,
        fontWeight: "900",
    },
    queuedMessageStripCount: {
        minWidth: 24,
        height: 22,
        borderRadius: 999,
        paddingHorizontal: 8,
        textAlign: "center",
        textAlignVertical: "center",
        overflow: "hidden",
        fontSize: 11,
        fontWeight: "900",
    },
    queuedMessageStripHeaderRight: {
        flex: 1,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "flex-end",
        gap: 6,
        minWidth: 0,
    },
    queuedMessageStripHint: {
        flex: 1,
        textAlign: "right",
        fontSize: 10,
        fontWeight: "700",
    },
    queuedMessageList: {
        gap: 8,
        paddingRight: 4,
    },
    queuedMessageChip: {
        width: 236,
        borderWidth: 1,
        borderRadius: 14,
        paddingHorizontal: 10,
        paddingVertical: 8,
        gap: 6,
    },
    queuedMessageChipHeader: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 8,
    },
    queuedMessageState: {
        fontSize: 10,
        fontWeight: "900",
        textTransform: "uppercase",
        letterSpacing: 0.6,
    },
    queuedMessageOrdinal: {
        fontSize: 10,
        fontWeight: "800",
    },
    queuedMessagePreview: {
        fontSize: 12,
        lineHeight: 17,
        fontWeight: "700",
    },
    queuedMessageActions: {
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
    },
    queuedMessageActionButton: {
        minHeight: 28,
        borderRadius: 999,
        borderWidth: 1,
        paddingHorizontal: 9,
        alignItems: "center",
        justifyContent: "center",
    },
    queuedMessageActionText: {
        fontSize: 10,
        fontWeight: "900",
    },
    queuedEditCard: {
        width: "90%",
        maxWidth: 420,
        borderRadius: 22,
        borderWidth: 1,
        padding: 16,
        gap: 10,
    },
    queuedEditTitle: {
        fontSize: 18,
        fontWeight: "900",
    },
    queuedEditSubtitle: {
        fontSize: 12,
        lineHeight: 18,
        fontWeight: "700",
    },
    queuedEditInput: {
        minHeight: 124,
        borderRadius: 16,
        borderWidth: 1,
        paddingHorizontal: 12,
        paddingVertical: 10,
        textAlignVertical: "top",
        fontSize: 14,
        lineHeight: 20,
        fontWeight: "700",
    },
    queuedEditActions: {
        flexDirection: "row",
        justifyContent: "flex-end",
        gap: 8,
    },
    queuedEditButton: {
        minHeight: 38,
        borderRadius: 999,
        borderWidth: 1,
        paddingHorizontal: 16,
        alignItems: "center",
        justifyContent: "center",
    },
    queuedEditButtonText: {
        fontSize: 12,
        fontWeight: "900",
    },
    queuedEditPrimaryText: {
        color: "#FFFFFF",
        fontSize: 12,
        fontWeight: "900",
    },
    hudOverlayStack: {
        position: "absolute",
        left: 0,
        right: 0,
        zIndex: 34,
        gap: 10,
    },
    processesOverlayDock: {
        width: "100%",
    },
    todosOverlayDock: {
        width: "100%",
    },
    composerAccessoryTray: {
        position: "absolute",
        left: 0,
        right: 0,
        zIndex: 25,
    },
    composerAccessoryTrayContent: {
        flexDirection: "row",
        gap: 8,
        paddingHorizontal: 2,
    },
    accessoryChip: {
        minHeight: 34,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        borderRadius: 999,
        borderWidth: 1,
        paddingHorizontal: 12,
        paddingVertical: 8,
    },
    accessoryChipText: {
        fontSize: 11,
        fontWeight: "700",
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
    workspaceChooserStage: {
        flexGrow: 1,
        justifyContent: "center",
        paddingHorizontal: 16,
        paddingTop: 88,
        paddingBottom: 180,
    },
    workspaceChooserCard: {
        borderRadius: 24,
        borderWidth: 1,
        paddingHorizontal: 16,
        paddingVertical: 16,
        gap: 14,
    },
    workspaceChooserHeader: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 12,
    },
    workspaceChooserHeaderBody: {
        flex: 1,
        gap: 4,
    },
    workspaceChooserTitle: {
        fontSize: 16,
        fontWeight: "900",
        letterSpacing: -0.3,
    },
    workspaceChooserSubtitle: {
        fontSize: 12,
        lineHeight: 18,
    },
    workspaceChooserCloseButton: {
        width: 34,
        height: 34,
        borderRadius: 17,
        alignItems: "center",
        justifyContent: "center",
        borderWidth: 1,
    },
    workspaceChooserSection: {
        gap: 10,
        borderTopWidth: StyleSheet.hairlineWidth,
        paddingTop: 14,
    },
    workspaceSectionLabel: {
        fontSize: 12,
        fontWeight: "800",
    },
    workspaceSectionHint: {
        fontSize: 11,
        lineHeight: 17,
    },
    workspaceOptionScrollList: {
        maxHeight: 260,
    },
    workspaceOptionList: {
        gap: 8,
        paddingRight: 4,
    },
    workspaceOptionCard: {
        borderRadius: 18,
        borderWidth: 1,
        paddingHorizontal: 14,
        paddingVertical: 12,
        gap: 4,
    },
    workspaceOptionTitle: {
        fontSize: 14,
        fontWeight: "800",
    },
    workspaceOptionMeta: {
        fontSize: 11,
        lineHeight: 17,
    },
    workspaceEmptyText: {
        fontSize: 12,
        lineHeight: 18,
    },
    workspaceSelectedPathCard: {
        gap: 4,
        paddingVertical: 4,
    },
    workspaceSelectedPathLabel: {
        fontSize: 11,
        fontWeight: "800",
        letterSpacing: 0.2,
    },
    workspaceSelectedPathText: {
        fontSize: 12,
        fontWeight: "700",
        lineHeight: 17,
    },
    workspaceCreateRow: {
        gap: 10,
    },
    workspaceNameInput: {
        minHeight: 46,
        borderRadius: 16,
        borderWidth: 1,
        paddingHorizontal: 14,
        fontSize: 14,
        fontWeight: "600",
    },
    workspaceCreateButton: {
        minHeight: 44,
        borderRadius: 16,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 14,
    },
    workspaceCreateButtonText: {
        color: "#FFFFFF",
        fontSize: 13,
        fontWeight: "800",
    },
    workspaceHintCard: {
        borderRadius: 18,
        borderWidth: 1,
        paddingHorizontal: 14,
        paddingVertical: 12,
    },
    workspaceHintText: {
        fontSize: 13,
        lineHeight: 19,
        textAlign: "center",
    },
    scopeSheetOverlay: {
        flex: 1,
        justifyContent: "flex-end",
    },
    scopeSheetCard: {
        borderTopLeftRadius: 26,
        borderTopRightRadius: 26,
        borderBottomLeftRadius: 0,
        borderBottomRightRadius: 0,
        borderWidth: 1,
        paddingHorizontal: 16,
        paddingTop: 10,
        paddingBottom: 18,
        maxHeight: "74%",
    },
    scopeSheetHandle: {
        alignSelf: "center",
        width: 42,
        height: 5,
        borderRadius: 999,
        marginBottom: 12,
        opacity: 0.72,
    },
    scopeSheetHeader: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 12,
    },
    scopeSheetHeaderText: {
        flex: 1,
        minWidth: 0,
    },
    scopeSheetCloseButton: {
        width: 32,
        height: 32,
        borderRadius: 16,
        alignItems: "center",
        justifyContent: "center",
        borderWidth: 1,
    },
    scopeSheetScroll: {
        marginTop: 12,
    },
    scopeSheetScrollContent: {
        gap: 16,
        paddingBottom: 12,
    },
    scopeSheetSection: {
        gap: 10,
    },
    scopeSheetSectionLabel: {
        fontSize: 12,
        fontWeight: "700",
    },
    scopeOptionGrid: {
        gap: 9,
    },
    scopeOptionChip: {
        borderRadius: 18,
        borderWidth: 1,
        paddingHorizontal: 12,
        paddingVertical: 11,
        gap: 4,
    },
    scopeOptionTitle: {
        fontSize: 13,
        fontWeight: "800",
    },
    scopeOptionMeta: {
        fontSize: 11,
        lineHeight: 16,
    },
    scopeHintText: {
        fontSize: 11,
        lineHeight: 16,
    },
    scopeSheetActions: {
        flexDirection: "row",
        justifyContent: "flex-end",
    },
    scopeActionButton: {
        minHeight: 34,
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
        paddingHorizontal: 14,
        borderRadius: radii.pill,
        borderWidth: 1,
    },
    scopeActionButtonText: {
        fontSize: 12,
        fontWeight: "800",
    },
    previewOverlay: {
        flex: 1,
        justifyContent: "center",
        alignItems: "center",
        paddingHorizontal: 18,
    },
    previewOverlayFullscreen: {
        paddingHorizontal: 0,
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
    previewFullscreenButton: {
        position: "absolute",
        top: 52,
        right: 78,
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
    previewCardFullscreen: {
        height: "100%",
        minHeight: "100%",
        maxWidth: undefined,
        borderRadius: 0,
        shadowOpacity: 0,
        shadowRadius: 0,
        shadowOffset: { width: 0, height: 0 },
        elevation: 0,
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
    previewWebviewFullscreen: {
        height: "100%",
        minHeight: "100%",
    },
});
