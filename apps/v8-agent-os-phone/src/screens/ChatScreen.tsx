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

import { ChatWindow } from "@/src/components/chat/ChatWindow";
import { Composer } from "@/src/components/chat/Composer";
import { ComposerPickerOverlay } from "@/src/components/chat/ComposerPickerOverlay";
import { GovernanceApprovalModal } from "@/src/components/chat/GovernanceApprovalModal";
import { ProcessesHUD } from "@/src/components/chat/ProcessesHUD";
import { RunControlBar } from "@/src/components/chat/RunControlBar";
import { RuntimeDock } from "@/src/components/chat/RuntimeDock";
import { RuntimeTimelinePanel } from "@/src/components/chat/RuntimeTimelinePanel";
import { TodosHUD } from "@/src/components/chat/TodosHUD";
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
import { buildApprovalFromEvent, normalizePhoneRealtimeEvent } from "@/src/lib/chat-realtime";
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
import { getDayGreeting } from "@/src/lib/time";
import {
    approvePendingItem,
    createDesktopLiveOffer,
    prepareDesktopLive,
    createDesktopLiveSession,
    createConversation,
    createProject,
    deleteConversation,
    deleteMessage,
    dispatchRunCommand,
    getDesktopLiveStatus,
    getConversationDetail,
    getProjectsRegistry,
    getRealtimeSnapshot,
    getSessionProcesses,
    getSessionScope,
    listCommandPresets,
    listConversations,
    listMusicTracks,
    listSkills,
    requestTextToSpeech,
    respondAskUser,
    releaseDesktopLiveSession,
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
    MusicTrack,
    PendingApproval,
    PhoneUiTimelineNode,
    DesktopLiveStatus,
    RealtimeSessionSnapshot,
    ProjectSummary,
    ScopeBindingView,
    SessionTodoItem,
    SkillReferenceSummary,
    UploadedWorkspaceFile,
    DesktopLiveSessionPayload,
} from "@/src/types/admin";
import {
    createInitialSessionRealtimeMessageState,
    type AdminProcessRef,
    type ContextGovernanceView,
    type ContextReferenceItem,
    deriveAuthoritativeSessionView,
    flushQueuedSessionRealtimeRuntimeEvents,
    isAskUserInteractionApproval,
    mergeTimelineNodesByIdentity,
    queueSessionRealtimeRuntimeEvent,
    shouldAuthoritativelyRefreshOnRuntimeEvent,
    syncSessionRealtimeMessageState,
    shouldApplyRuntimeEventToMessage,
} from "@v8/session-realtime";

type RuntimeSummary = {
    status: string;
    latestSeq: number;
    runId?: string;
    label?: string;
};

type WorkspaceBindingDraft =
    | { kind: "main" }
    | { kind: "project"; projectId: string };

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
    },
): PhoneRuntimeTimelineEntry {
    const timestamp = typeof options?.timestamp === "number"
        ? options.timestamp
        : typeof options?.timestamp === "string"
            ? Date.parse(options.timestamp) || Date.now()
            : Date.now();
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
        taskPlanningMode: boolean;
        files: UploadedWorkspaceFile[];
    },
): ChatMessage {
    const now = Date.now();
    const metadata: ChatMessage["metadata"] = {};
    const attachments = buildUploadedFileAttachments(options.files);
    if (options.command) {
        metadata.commandPreset = { name: options.command.name };
    }
    if (options.skills.length > 0) {
        metadata.skillReferences = options.skills.map((skill) => ({ ...skill }));
    }
    if (options.taskPlanningMode) {
        metadata.taskPlanningMode = true;
    }
    if (attachments.length > 0) {
        metadata.attachments = attachments;
    }
    metadata.clientMessageId = `user-${now}`;

    return {
        id: metadata.clientMessageId as string,
        role: "user",
        content: text || (attachments.length === 1 ? "已上传 1 个文件" : attachments.length > 1 ? `已上传 ${attachments.length} 个文件` : ""),
        timestamp: now,
        images: options.files
            .map((file) => file.url || file.publicUrl || "")
            .filter(Boolean),
        artifacts: [],
        metadata: Object.keys(metadata).length > 0 ? metadata : undefined,
    };
}

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
        .map((item) => String(item.publicUrl || item.url || item.workspacePath || "").trim())
        .filter(Boolean);
    const nodes = Array.isArray(record.nodes) ? record.nodes as PhoneUiTimelineNode[] : fallback.nodes || [];
    return {
        ...fallback,
        id: String(record.id || fallback.id),
        role: record.role === "user" ? "user" : fallback.role,
        runId: String(record.run_id || record.runId || fallback.runId || ""),
        content: String(record.content_text || record.content || fallback.content || ""),
        timestamp: fallback.timestamp || Date.now(),
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
        agentName: "智能主管",
        agentAvatar: "/brand-mark.png",
        agentRoleLabel: "主理人",
    }, runId, "placeholder");
}

function extractSkillQuery(input: string) {
    const match = input.match(/(?:^|\s)@([^\s@]*)$/);
    return match ? match[1] : "";
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
            label = "等待你的输入";
        } else if (phase === "artifact_ready") {
            label = "产物已就绪";
        } else if (phase === "settling") {
            label = "任务即将完成";
        } else if (activeTodo) {
            label = `步骤 ${activeIndex + 1}/${Math.max(totalCount, 1)}`;
        } else if (nextTodo) {
            label = completedCount > 0
                ? `准备步骤 ${nextIndex + 1}/${Math.max(totalCount, 1)}`
                : "正在规划任务";
        } else {
            label = "正在规划任务";
        }
    }

    if (!subtitle) {
        if (phase === "artifact_ready") {
            subtitle = currentStep || (totalCount > 0 ? `已完成 ${completedCount}/${totalCount} 个步骤` : "产物已经生成，可继续挂载到工作区。");
        } else if (phase === "waiting_input") {
            subtitle = currentStep || "请继续提供必要输入。";
        } else if (activeTodo) {
            subtitle = currentStep;
        } else if (nextTodo) {
            subtitle = currentStep || `已生成 ${totalCount} 个步骤`;
        } else if (totalCount > 0) {
            subtitle = `已完成 ${completedCount}/${totalCount} 个步骤`;
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
        return -1;
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
    return Boolean(
        String(message.content || "").trim()
        || (Array.isArray(message.images) && message.images.length > 0)
        || (Array.isArray(message.artifacts) && message.artifacts.length > 0)
        || (Array.isArray(message.nodes) && message.nodes.length > 0)
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
    return (
        String(message.content || "").trim().length
        + ((message.nodes || []).length * 120)
        + ((message.artifacts || []).length * 200)
        + ((message.images || []).length * 80)
    );
}

function mergeMessageImages(base: string[] = [], incoming: string[] = []) {
    return Array.from(new Set([...base, ...incoming].filter(Boolean)));
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
            return normalizeMessagesForState([snapshotMessage])[0] || snapshotMessage;
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

        return mergedMessage;
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
            return snapshotMessage;
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

        return mergedMessage;
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

function isLegacyChatUnsupportedPayload(payload: Partial<ConversationDetail | RealtimeSessionSnapshot | Record<string, unknown>> | null | undefined) {
    const root = asRecord(payload);
    const snapshot = asRecord(root.snapshot);
    return Boolean(root.legacyChatUnsupported || snapshot.legacyChatUnsupported);
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
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
    t: (zh: string, en?: string) => string,
) {
    const raw = error instanceof Error ? String(error.message || "").trim() : "";
    if (!raw) {
        return t("桌面预览正在准备中，请稍候。", "Desktop preview is still preparing. Please wait.");
    }
    if (/fetch failed|network request failed|failed to fetch|bridge is starting|local-offer-unavailable|offer|candidate|session/i.test(raw)) {
        return t("桌面预览正在准备中，请稍候。", "Desktop preview is still preparing. Please wait.");
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
    const params = useLocalSearchParams<{ new?: string | string[] }>();
    const newConversationIntent = Array.isArray(params.new) ? params.new[0] === "1" : params.new === "1";
    const {
        status,
        user,
        adminBaseUrl,
        activeConversationId,
        setActiveConversationId,
        authorizedFetch,
        authorizedRealtimeStream,
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
    const lastMessageFingerprintRef = useRef("");
    const lastAppliedSnapshotSeqRef = useRef(0);
    const lastAppliedSnapshotFingerprintRef = useRef("");
    const lastRealtimeSnapshotAtRef = useRef(0);
    const seenRealtimeEventKeysRef = useRef<Set<string>>(new Set());
    const pendingRealtimeRenderDiagnosticRef = useRef<Record<string, unknown> | null>(null);
    const messagesRef = useRef<ChatMessage[]>([]);
    const messageConversationIdRef = useRef<string | null>(activeConversationId);
    const todosRef = useRef<SessionTodoItem[]>([]);
    const realtimeMessageStateRef = useRef(
        createInitialSessionRealtimeMessageState<ChatMessage>([], PHONE_STREAM_LIFECYCLE_OPTIONS),
    );
    const runtimeFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const runtimeRef = useRef<RuntimeSummary>({ status: "idle", latestSeq: 0 });
    const activeRunIdRef = useRef<string>("");
    const activeConversationIdRef = useRef<string | null>(activeConversationId);
    const previousConversationIdRef = useRef<string | null>(null);
    const conversationTransitionTokenRef = useRef(0);
    const optimisticSeedConversationIdRef = useRef<string | null>(null);
    const ttsRequestIdRef = useRef(0);
    const tRef = useRef(t);
    const ttsPlayer = useAudioPlayer();
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
    const [newProjectName, setNewProjectName] = useState("");
    const [scopeBinding, setScopeBinding] = useState<ScopeBindingView | null>(null);
    const [scopeLoading, setScopeLoading] = useState(false);
    const [approvals, setApprovals] = useState<PendingApproval[]>([]);
    const [todos, setTodos] = useState<SessionTodoItem[]>([]);
    const [processes, setProcesses] = useState<AdminProcessRef[]>([]);
    const lastProcessSurfaceAtRef = useRef(0);
    const [contextReferences, setContextReferences] = useState<ContextReferenceItem[]>([]);
    const [contextGovernance, setContextGovernance] = useState<ContextGovernanceView | null>(null);
    const [contextGovernanceHistory, setContextGovernanceHistory] = useState<ContextGovernanceView[]>([]);
    const [musicTracks, setMusicTracks] = useState<MusicTrack[]>([]);
    const [commands, setCommands] = useState<CommandPresetSummary[]>([]);
    const [skills, setSkills] = useState<SkillReferenceSummary[]>([]);
    const [uploadedFiles, setUploadedFiles] = useState<UploadedWorkspaceFile[]>([]);
    const [selectedCommand, setSelectedCommand] = useState<CommandPresetSummary | null>(null);
    const [selectedSkills, setSelectedSkills] = useState<SkillReferenceSummary[]>([]);
    const [taskPlanningMode, setTaskPlanningMode] = useState(false);
    const [bottomLayerHeight, setBottomLayerHeight] = useState(132);
    const [runtime, setRuntime] = useState<RuntimeSummary>({ status: "idle", latestSeq: 0 });
    const [runtimeTimeline, setRuntimeTimeline] = useState<PhoneRuntimeTimelineEntry[]>([]);
    const [runtimePanelOpen, setRuntimePanelOpen] = useState(false);
    const [governanceApprovalOpen, setGovernanceApprovalOpen] = useState(false);
    const [governanceApprovalBusy, setGovernanceApprovalBusy] = useState(false);
    const [dismissedGovernanceApprovalId, setDismissedGovernanceApprovalId] = useState("");
    const [selectedRuntimeId, setSelectedRuntimeId] = useState<PhoneRuntimeId>("chat");
    const [workspaceInfoOpen, setWorkspaceInfoOpen] = useState(false);
    const [desktopPreviewOpen, setDesktopPreviewOpen] = useState(false);
    const [desktopPreviewBusy, setDesktopPreviewBusy] = useState(false);
    const [desktopPreviewSessionId, setDesktopPreviewSessionId] = useState("");
    const [desktopPreviewError, setDesktopPreviewError] = useState("");
    const [desktopPreviewState, setDesktopPreviewState] = useState<"closed" | "loading" | "preview" | "error">("closed");
    const [desktopPreviewWebReady, setDesktopPreviewWebReady] = useState(false);
    const [desktopLiveStatus, setDesktopLiveStatus] = useState<DesktopLiveStatus | null>(null);

    const slashQuery = useMemo(() => {
        const trimmed = input.trimStart();
        return !selectedCommand && trimmed.startsWith("/") ? trimmed.slice(1).trim().toLowerCase() : "";
    }, [input, selectedCommand]);
    const skillQuery = useMemo(() => extractSkillQuery(input).toLowerCase(), [input]);
    const commandPickerOpen = !selectedCommand && input.trimStart().startsWith("/");
    const skillPickerOpen = /(?:^|\s)@([^\s@]*)$/.test(input);
    const filteredCommands = useMemo(() => {
        if (!slashQuery) {
            return commands;
        }
        return commands.filter((item) =>
            item.name.toLowerCase().includes(slashQuery)
            || String(item.summary || "").toLowerCase().includes(slashQuery),
        );
    }, [commands, slashQuery]);
    const filteredSkills = useMemo(() => {
        const selectedKeys = new Set(selectedSkills.map((skill) => `${skill.name}:${skill.path || ""}`));
        const base = skills.filter((item) => !selectedKeys.has(`${item.name}:${item.path || ""}`));
        if (!skillQuery) {
            return base;
        }
        return base.filter((item) =>
            item.name.toLowerCase().includes(skillQuery)
            || String(item.description || "").toLowerCase().includes(skillQuery)
            || String(item.path || "").toLowerCase().includes(skillQuery),
        );
    }, [selectedSkills, skillQuery, skills]);
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
    }, []);

    const applySessionProcessSurface = useCallback((incoming: AdminProcessRef[], options?: { forceClear?: boolean }) => {
        const normalizedIncoming = incoming || [];
        setProcesses((current) => {
            if (normalizedIncoming.length > 0) {
                lastProcessSurfaceAtRef.current = Date.now();
                return normalizedIncoming;
            }
            if (options?.forceClear) {
                lastProcessSurfaceAtRef.current = 0;
                return [];
            }
            if (current.length === 0) {
                return current;
            }
            return (Date.now() - lastProcessSurfaceAtRef.current) <= 3000 ? current : [];
        });
    }, []);

    const clearActiveConversationViewState = useCallback(() => {
        resetConversationStreamState();
        messagesRef.current = [];
        messageConversationIdRef.current = null;
        setMessages([]);
        setLegacyChatUnsupported(false);
        setApprovals([]);
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
        if (runtimeFlushTimerRef.current) {
            return;
        }
        runtimeFlushTimerRef.current = setTimeout(() => {
            runtimeFlushTimerRef.current = null;
            flushPendingRuntimeEvents();
        }, 48);
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
                reason: t("桌面预览正在准备中，请稍候。", "Desktop preview is still preparing. Please wait."),
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
            if (status?.available === true && status?.bridgeReady !== false) {
                return status;
            }
            lastError = String(
                status?.phase === "warming" || status?.bridgeWarming === true || status?.bridgeReady === false
                    ? t("桌面预览桥正在启动，请稍候。", "Desktop preview bridge is starting. Please wait.")
                    : status?.reason
                        || t("桌面预览尚未就绪，请稍候。", "Desktop preview is not ready yet. Please wait."),
            );
            await new Promise((resolve) => setTimeout(resolve, Math.min(900 + attempt * 150, 1800)));
        }
        throw new Error(lastError || t("桌面预览尚未就绪，请稍候。", "Desktop preview is not ready yet. Please wait."));
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
                    reason: t("桌面预览尚未就绪，请稍候。", "Desktop preview is not ready yet. Please wait."),
                    phase: "warming",
                    bridgeReady: false,
                    bridgeWarming: true,
                    retryAllowed: false,
                };
            }
            if (desktopPreviewRequestIdRef.current !== requestId) {
                return;
            }
            if (!status?.available) {
                throw new Error(t("桌面预览尚未就绪，请稍候。", "Desktop preview is not ready yet. Please wait."));
            }
            setDesktopLiveStatus((current) => ({
                ...(current || {}),
                ...status,
                phase: "ready",
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
                throw new Error(t("桌面预览尚未就绪，请稍候。", "Desktop preview is not ready yet. Please wait."));
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
                phase: "ready",
                bridgeReady: true,
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
                phase: "ready",
                bridgeReady: true,
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
                desktopLiveUserIntentRef.current = false;
                setDesktopPreviewState("error");
                setDesktopPreviewBusy(false);
                setDesktopPreviewError(t("桌面预览连接失败，请检查桥接状态后再继续。", "Desktop preview failed to connect. Check the bridge state and try again later."));
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
                    throw new Error(t("主工作区路径尚未就绪，请稍后再试。", "The main workspace path is not ready yet. Please try again shortly."));
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
                    throw new Error(t("项目级工作区不存在或尚未就绪。", "The selected project workspace does not exist or is not ready yet."));
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
            setNewProjectName("");
            clearNewConversationIntent();
            await setActiveConversationId(createdSessionId);
            await loadSessionScope(createdSessionId);
        } catch (error) {
            Alert.alert(
                t("创建会话失败", "Create conversation failed"),
                error instanceof Error ? error.message : t("无法按当前工作区绑定创建会话。", "Unable to create a conversation with the selected workspace binding."),
            );
        } finally {
            setWorkspaceChooserBusy(false);
        }
    }, [authorizedFetch, availableProjects, clearNewConversationIntent, loadSessionScope, mainWorkspacePath, setActiveConversationId, t, workspaceChooserBusy]);

    const handleCreateProjectConversation = useCallback(async () => {
        const nextProjectName = newProjectName.trim();
        if (!nextProjectName || workspaceChooserBusy) {
            return;
        }
        setWorkspaceChooserBusy(true);
        try {
            const createdProject = await createProject(authorizedFetch, { name: nextProjectName });
            const createdProjectId = String(createdProject?.id || "").trim();
            if (!createdProjectId) {
                throw new Error(t("项目创建成功但没有返回有效的项目标识。", "Project creation succeeded but returned no valid project id."));
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
            setNewProjectName("");
            clearNewConversationIntent();
            await setActiveConversationId(createdSessionId);
            await loadSessionScope(createdSessionId);
        } catch (error) {
            Alert.alert(
                t("创建项目级工作区失败", "Create project workspace failed"),
                error instanceof Error ? error.message : t("无法创建新的项目级工作区。", "Unable to create a new project workspace."),
            );
        } finally {
            setWorkspaceChooserBusy(false);
        }
    }, [authorizedFetch, clearNewConversationIntent, loadProjects, loadSessionScope, newProjectName, setActiveConversationId, t, workspaceChooserBusy]);

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
        await loadProjects();

        if (
            activeConversationIdRef.current
            && !nextConversations.some((item) => (item.sessionId || item.id) === activeConversationIdRef.current)
        ) {
            await setActiveConversationId(null);
        }
    }, [authorizedFetch, loadProjects, setActiveConversationId]);

    const applyConversationProjection = useCallback((payload: Partial<ConversationDetail | RealtimeSessionSnapshot | Record<string, unknown>> | null | undefined) => {
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
        const hasAskUserPending = nextApprovals.some((item) => isAskUserInteractionApproval(item));
        const hasGovernanceApprovalPending = nextApprovals.some((item) => !isAskUserInteractionApproval(item));
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
    }, [applySessionProcessSurface, patchAssistantTaskShell]);

    const applyRealtimeSnapshotPayload = useCallback((payload: Partial<ConversationDetail | RealtimeSessionSnapshot | Record<string, unknown>> | null | undefined) => {
        const snapshotMessages = extractSnapshotMessages(payload);
        const snapshotSeq = buildSnapshotSequence(payload);
        const targetConversationId = String(activeConversationIdRef.current || "").trim();
        if (isLegacyChatUnsupportedPayload(payload)) {
            setLegacyChatUnsupported(true);
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
                const snapshot = await getRealtimeSnapshot(authorizedFetch, targetConversationId);
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

    const handleRealtimeEvent = useCallback((eventName: string, payload: unknown) => {
        const upstreamDiagnostics = readRealtimeDiagnostics(payload);
        const phoneReceivedAt = new Date().toISOString();
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
        pendingRealtimeRenderDiagnosticRef.current = {
            eventName,
            eventType: normalized.type,
            normalizedName: normalized.name,
            topic: normalized.topic,
            seq: normalized.seq,
            engineEmittedAt: normalized.ts,
            adminForwardedAt: upstreamDiagnostics.adminForwardedAt,
            phoneReceivedAt,
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
            const taskLabel = taskProgress?.label || tRef.current("任务步骤推进中", "Task progress updated");
            const taskSubtitle = taskProgress?.subtitle || tRef.current("正在更新任务进度", "Updating task progress");

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
                        timestamp: normalized.ts || Date.now(),
                        actorLabel: normalized.actorLabel || tRef.current("智能主管", "Supervisor"),
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
                || normalized.type === "reasoning_chunk"
                || normalized.type === "text_chunk"
                || normalized.type === "tool_start"
                || normalized.type === "tool_result"
                || normalized.type === "done"
                || normalized.type === "error"
                || normalized.name === "ask_user"
                || normalized.name === "approval_requested"
                || normalized.name === "artifact_recorded";
            queueRuntimeMessageEvent(normalized, shouldFlushImmediately);
        }

        if (normalized.type === "agent_start") {
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.runtimeId || "chat")) || "chat",
                    normalized.topic || "agent.started",
                    String(normalized.actorLabel || tRef.current("智能主管已开始处理", "Supervisor started working")),
                    {
                        id: normalized.event_id || `agent:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "handoff",
                        timestamp: normalized.ts || Date.now(),
                        actorLabel: normalized.actorLabel,
                        status: "running",
                    },
                ),
            );
            setRuntime((current) => ({
                status: "running",
                latestSeq: normalized.seq || current.latestSeq,
                runId: normalized.run_id || current.runId,
                label: normalized.actorLabel || tRef.current("开始处理", "Started"),
            }));
            patchAssistantTaskShell(todosRef.current, {
                phase: "task_planning",
                label: tRef.current("开始执行任务", "Task started"),
                subtitle: tRef.current("正在整理上下文并准备响应。", "Preparing context and response."),
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
                    String(normalized.content || tRef.current("正在思考", "Thinking")).trim() || tRef.current("正在思考", "Thinking"),
                    {
                        runId: normalized.run_id,
                        seq: normalized.seq,
                        timestamp: normalized.ts || Date.now(),
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
                label: tRef.current("正在思考", "Thinking"),
            }));
            patchAssistantTaskShell(todosRef.current, {
                phase: "task_planning",
                label: tRef.current("正在规划任务", "Planning task"),
                subtitle: tRef.current("正在分析任务步骤与执行顺序。", "Analyzing steps and execution order."),
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
                    String(normalized.content || tRef.current("正在回复", "Replying")).trim() || tRef.current("正在回复", "Replying"),
                    {
                        runId: normalized.run_id,
                        seq: normalized.seq,
                        timestamp: normalized.ts || Date.now(),
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
                label: tRef.current("正在回复", "Replying"),
            }));
            patchAssistantTaskShell(todosRef.current, {
                phase: "streaming",
                label: tRef.current("正在回复", "Replying"),
                subtitle: tRef.current("正在持续输出结果。", "Streaming the response."),
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
                || tRef.current("工具调用", "Tool call"),
            ).trim();
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.runtimeId || normalized.topic || "chat")) || "chat",
                    normalized.topic || (normalized.type === "tool_start" ? "tool.started" : "tool.finished"),
                    normalized.type === "tool_start"
                        ? tRef.current(`开始调用 ${toolLabel}`, `Starting ${toolLabel}`)
                        : tRef.current(`已完成 ${toolLabel}`, `Finished ${toolLabel}`),
                    {
                        id: normalized.event_id || `${normalized.type}:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "tool",
                        timestamp: normalized.ts || Date.now(),
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
                label: toolLabel || tRef.current("正在执行工具", "Running tool"),
                subtitle: normalized.type === "tool_start"
                    ? tRef.current("任务正在调用工具执行步骤。", "Task is calling a tool.")
                    : tRef.current("工具已返回结果，正在整理后续步骤。", "Tool returned and next step is being prepared."),
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
                    tRef.current("已启用任务规划偏好", "Task planning preference enabled"),
                    {
                        id: normalized.event_id || `task-planning-enabled:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "progress",
                        timestamp: normalized.ts || Date.now(),
                        actorLabel: normalized.actorLabel || tRef.current("智能主管", "Supervisor"),
                        status: "running",
                    },
                ),
            );
            patchAssistantTaskShell(todosRef.current, {
                phase: "task_planning",
                label: tRef.current("任务规划偏好已开启", "Task planning preference enabled"),
                subtitle: tRef.current("多步骤任务会更倾向拆解并维护 Todo。", "Multi-step tasks will more readily use Todo planning."),
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
                    ? tRef.current("任务规划偏好已命中 Todo 链", "Task planning preference entered the Todo lane")
                    : tRef.current("任务规划偏好已开启，但本轮按单步任务完成", "Task planning was enabled, but this run completed as a single-step task")),
            ).trim();
            const subtitle = String(
                normalized.data?.message
                || (usedTodos
                    ? tRef.current("本轮已创建或更新 Todo，并按任务计划推进。", "This run created or updated Todo items and progressed through a task plan.")
                    : tRef.current("本轮没有进入 Todo 链，通常表示模型判断无需持续跟踪。", "This run did not enter the Todo lane, which usually means the model judged continuous tracking unnecessary.")),
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
                        timestamp: normalized.ts || Date.now(),
                        actorLabel: normalized.actorLabel || tRef.current("智能主管", "Supervisor"),
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
                    String(normalized.content || tRef.current("本轮任务已完成", "Run completed")),
                    {
                        id: normalized.event_id || `done:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "progress",
                        timestamp: normalized.ts || Date.now(),
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
                label: tRef.current("已完成", "Completed"),
            }));
            patchAssistantTaskShell(todosRef.current, {
                phase: "settling",
                label: tRef.current("任务已完成", "Task completed"),
                subtitle: tRef.current("正在整理最终回复与产物。", "Preparing final response and artifacts."),
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
                    String(normalized.error || normalized.content || tRef.current("本轮任务失败", "Run failed")),
                    {
                        id: normalized.event_id || `error:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "progress",
                        timestamp: normalized.ts || Date.now(),
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
                label: tRef.current("运行失败", "Failed"),
            }));
            patchAssistantTaskShell(todosRef.current, {
                phase: "error",
                label: tRef.current("任务失败", "Task failed"),
                subtitle: String(normalized.error || normalized.content || tRef.current("运行过程中出现错误。", "An error interrupted the run.")),
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

        if (normalized.name === "ask_user" || normalized.name === "approval_requested") {
            const approval = buildApprovalFromEvent(normalized);
            if (approval) {
                const askUserInteraction = isAskUserInteractionApproval(approval);
                setApprovals((current) => upsertApproval(current, approval));
                setRuntime((current) => ({
                    ...current,
                    status: askUserInteraction ? "waiting_input" : "waiting_approval",
                    latestSeq: normalized.seq || current.latestSeq,
                    runId: normalized.run_id || current.runId,
                    label: askUserInteraction
                        ? tRef.current("等待你的输入", "Waiting for your answer")
                        : tRef.current("等待授权确认", "Waiting for approval"),
                }));
                patchAssistantTaskShell(todosRef.current, {
                    phase: askUserInteraction ? "waiting_input" : "tooling",
                    label: askUserInteraction
                        ? tRef.current("等待你的输入", "Waiting for your answer")
                        : tRef.current("等待授权确认", "Waiting for approval"),
                    subtitle: typeof normalized.data?.question === "string"
                        ? normalized.data.question
                        : (askUserInteraction
                            ? tRef.current("请继续补充必要信息。", "Please provide the requested input.")
                            : tRef.current("需要你的授权后才能继续。", "Approval is required to continue.")),
                    runId: normalized.run_id,
                    createIfMissing: true,
                });
                appendRuntimeTimeline(
                    buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                        normalizePhoneRuntimeId(String(normalized.runtimeId || normalized.topic || normalized.data?.topic || (askUserInteraction ? "chat" : "automation"))) || (askUserInteraction ? "chat" : "automation"),
                        normalized.topic || "approval.requested",
                        String(
                            normalized.data?.question
                            || (askUserInteraction
                                ? tRef.current("等待你的输入", "Waiting for your answer")
                                : tRef.current("等待授权确认", "Waiting for approval")),
                        ),
                        {
                            id: normalized.event_id || `${askUserInteraction ? "ask-user" : "approval"}:${normalized.seq || Date.now()}`,
                            seq: normalized.seq,
                            kind: "governance",
                            timestamp: normalized.ts || Date.now(),
                            actorLabel: normalized.actorLabel || (askUserInteraction ? tRef.current("智能主管", "Supervisor") : tRef.current("运行调度", "Automation")),
                        },
                    ),
                );
            }
            if (shouldFallbackRefresh) {
                scheduleRealtimeSnapshotRefresh(normalized.session_id || normalized.conversation_id || activeConversationIdRef.current);
            }
            return;
        }

        if (normalized.topic === "ask_user.resolved") {
            setApprovals((current) => current.filter((item) => !isAskUserInteractionApproval(item)));
            setRuntime((current) => ({
                ...current,
                status: "running",
                latestSeq: normalized.seq || current.latestSeq,
                runId: normalized.run_id || current.runId,
                label: tRef.current("继续执行中", "Continuing"),
            }));
            patchAssistantTaskShell(todosRef.current, {
                phase: "tooling",
                label: tRef.current("继续执行中", "Continuing"),
                subtitle: tRef.current("已收到你的输入，正在继续任务。", "Your answer was received and the task is continuing."),
                runId: normalized.run_id,
                createIfMissing: false,
            });
            appendRuntimeTimeline(
                buildPhoneRuntimeTimelineEntryFromEvent(normalized, { locale }) || buildRuntimeTimelineEntry(
                    normalizePhoneRuntimeId(String(normalized.runtimeId || normalized.topic || "chat")) || "chat",
                    normalized.topic || "ask_user.resolved",
                    tRef.current("已收到你的输入，继续执行中", "Input received, continuing"),
                    {
                        id: normalized.event_id || `ask-user-resolved:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "governance",
                        timestamp: normalized.ts || Date.now(),
                        actorLabel: normalized.actorLabel || tRef.current("智能主管", "Supervisor"),
                    },
                ),
            );
            if (shouldFallbackRefresh) {
                scheduleRealtimeSnapshotRefresh(normalized.session_id || normalized.conversation_id || activeConversationIdRef.current);
            }
            return;
        }

        if (normalized.type === "custom_event" && normalized.name === "artifact_recorded") {
            patchAssistantTaskShell(todosRef.current, {
                phase: "artifact_ready",
                label: tRef.current("产物已就绪", "Artifact ready"),
                subtitle: String(
                    normalized.artifact?.title
                    || normalized.data?.title
                    || normalized.data?.displayLabel
                    || tRef.current("新的产物已经生成。", "A new artifact is ready."),
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
                    String(normalized.artifact?.title || normalized.artifact?.kind || tRef.current("记录新的产物", "Recorded a new artifact")),
                    {
                        id: normalized.event_id || `artifact:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "artifact",
                        timestamp: normalized.ts || Date.now(),
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
                    String(normalized.data?.label || normalized.topic || tRef.current("运行更新", "Runtime updated")),
                    {
                        id: normalized.event_id || `runtime:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "progress",
                        timestamp: normalized.ts || Date.now(),
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
                    String(normalized.data?.topic || tRef.current("运行控制已更新", "Run control updated")),
                    {
                        id: normalized.event_id || `control:${normalized.seq || Date.now()}`,
                        seq: normalized.seq,
                        kind: "governance",
                        timestamp: normalized.ts || Date.now(),
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
                    timestamp: normalized.ts || Date.now(),
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
        locale,
        patchAssistantTaskShell,
        queueRuntimeMessageEvent,
        scheduleRealtimeSnapshotRefresh,
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
                Alert.alert(t("读取会话失败", "Load failed"), error instanceof Error ? error.message : t("无法加载会话详情", "Unable to load the conversation detail"));
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
        }, 1800);

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
            setNewProjectName("");
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
        setUploadedFiles([]);
        setSelectedCommand(null);
        setSelectedSkills([]);
        setTaskPlanningMode(false);
        setWorkspaceChooserVisible(false);
        setWorkspaceInfoOpen(false);
        setNewProjectName("");
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
        clearActiveConversationViewState();
        setUploadedFiles([]);
        setSelectedCommand(null);
        setSelectedSkills([]);
        setTaskPlanningMode(false);
        setWorkspaceInfoOpen(false);
        setWorkspaceChooserVisible(true);
        setNewProjectName("");
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
        clearActiveConversationViewState();
        setUploadedFiles([]);
        setSelectedCommand(null);
        setSelectedSkills([]);
        setTaskPlanningMode(false);
        setWorkspaceChooserVisible(false);
        setWorkspaceInfoOpen(false);
        setNewProjectName("");
        setScopeBinding(null);
        setRuntimePanelOpen(false);
        setSelectedRuntimeId("chat");
        await setActiveConversationId(null);
        clearNewConversationIntent();
        router.replace("/chat" as Href);
    }, [clearActiveConversationViewState, clearNewConversationIntent, setActiveConversationId, stopRealtime]);

    const handleDeleteConversation = useCallback((item: ConversationSummary) => {
        const canonicalSessionId = item.sessionId || item.id;
        Alert.alert(t("删除会话", "Delete conversation"), t("确定删除这个会话吗？", "Delete this conversation?"), [
            { text: t("取消", "Cancel"), style: "cancel" },
            {
                text: t("删除", "Delete"),
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

            const uploaded: UploadedWorkspaceFile[] = [];
            for (const [index, asset] of result.assets.entries()) {
                const nextFile = await uploadAttachment(authorizedFetch, {
                    uri: asset.uri,
                    name: asset.name,
                    type: asset.mimeType || "application/octet-stream",
                });
                uploaded.push({
                    ...nextFile,
                    localId: nextFile.localId || `upload:${Date.now()}:${index}:${asset.name || "file"}`,
                });
            }

            setUploadedFiles((current) => mergeUploadedWorkspaceFiles(current, uploaded));
        } catch (error) {
            Alert.alert(t("上传失败", "Upload failed"), error instanceof Error ? error.message : t("无法上传附件", "Unable to upload the attachment"));
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
        if (isAskUserInteractionApproval(approval)) {
            if (!approve) {
                await approvePendingItem(authorizedFetch, approvalId, answer, false);
            } else {
                await respondAskUser(authorizedFetch, approvalId, answer);
            }
        } else {
            await approvePendingItem(authorizedFetch, approvalId, answer, approve);
        }
        setApprovals((current) => current.filter((item) => String(item.id || item.approval_id || "") !== approvalId));
    }, [authorizedFetch]);

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
                command === "interrupt" ? t("中断失败", "Stop failed") : t("重试失败", "Retry failed"),
                error instanceof Error ? error.message : t("运行控制失败", "Run command failed"),
            );
        } finally {
            setRunActionBusy(false);
        }
    }, [authorizedFetch, runActionBusy, t]);

    const projection = useMemo(
        () => buildPhoneChatProjection({
            conversations,
            activeConversationId,
            messages,
            approvals,
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
        }),
        [activeConversationId, approvals, contextGovernance, contextGovernanceHistory, contextReferences, conversations, locale, messages, processes, runtime, runtimeTimeline, selectedRuntimeId, t, todos],
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

    const hudProcesses = useMemo(
        () => (projection.processes.length > 0 ? projection.processes : processes),
        [processes, projection.processes],
    );

    useEffect(() => {
        activeRunIdRef.current = String(projection.runControlState.runId || "").trim();
    }, [projection.runControlState.runId]);

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
        if (!text && !selectedCommand && selectedSkills.length === 0 && uploadedFiles.length === 0) {
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
        const pendingFiles = [...uploadedFiles];
        const effectiveText = text || (pendingFiles.length === 1 ? "已上传 1 个文件" : pendingFiles.length > 1 ? `已上传 ${pendingFiles.length} 个文件` : "");
        let submissionAccepted = false;
        let optimisticUserMessageId = "";
        let optimisticAssistantMessageId = "";
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
                taskPlanningMode,
                files: pendingFiles,
            });
            const clientMessageId = userMessage.id;
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
                        label: t("正在规划任务", "Planning task"),
                        subtitle: t("正在拆解步骤并准备执行。", "Breaking down the steps and preparing execution."),
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
            setSelectedCommand(null);
            setSelectedSkills([]);
            setUploadedFiles([]);

            if (effectiveText) {
                setConversations((current) => current.map((conversation) =>
                    conversation.id === currentConversationId
                        ? {
                            ...conversation,
                            title: isPlaceholderConversationTitle(conversation.title)
                                ? (effectiveText.slice(0, 36) || conversation.title || "")
                                : conversation.title,
                            updatedAt: new Date().toISOString(),
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
                    fileUrls: pendingFiles.map((file) => file.url || file.publicUrl || "").filter(Boolean),
                    attachments: buildUploadedFileAttachments(pendingFiles),
                    taskPlanningMode,
                },
            );
            if (submitResult.accepted === false) {
                throw new Error(t("消息提交失败", "Unable to submit message"));
            }
            submissionAccepted = true;
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
            setUploadedFiles([]);

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
                setSelectedCommand(pendingCommand);
                setSelectedSkills(pendingSkills);
                setUploadedFiles(pendingFiles);
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
            Alert.alert(t("发送失败", "Send failed"), error instanceof Error ? error.message : t("无法发送消息", "Unable to send message"));
        } finally {
            setSending(false);
        }
    }, [
        authorizedFetch,
        clearNewConversationIntent,
        input,
        selectedCommand,
        selectedSkills,
        taskPlanningMode,
        t,
        uploadedFiles,
    ]);

    if (status === "booting") {
        return <LoadingScreen label={t("正在读取聊天主链…", "Loading the conversation lane...")} />;
    }

    if (status === "anonymous") {
        return <Redirect href="/login" />;
    }

    const profileImageUri = resolveAdminAssetUrl(adminBaseUrl, user?.image || "");
    const greetingEmptyState = !activeConversationId && projection.projectedMessages.length === 0
        ? {
            icon: "hand-wave-outline" as const,
            title: getDayGreeting(locale),
            subtitle: t("新对话会先绑定工作区，再创建会话。", "A new conversation binds a workspace before the session is created."),
        }
        : null;
    const legacyChatEmptyState = activeConversationId && legacyChatUnsupported && projection.projectedMessages.length === 0
        ? {
            icon: "archive-alert-outline" as const,
            title: t("旧会话未接入 Canonical Transcript", "Legacy conversation is not on Canonical Transcript"),
            subtitle: t(
                "这条历史记录没有稳定 transcript 节点。为避免继续混用旧数据源造成漂移，当前版本已停止回放旧混源聊天内容。",
                "This history record has no stable transcript nodes. To avoid mixed-source drift, this version no longer replays legacy chat content.",
            ),
        }
        : null;
    const currentWorkspaceLabel = boundProject?.name || t("主工作区", "Main workspace");
    const currentWorkspacePath = scopeBinding?.workspacePath || mainWorkspacePath || t("未绑定", "Unbound");
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
    const composerRunActive = runControlStatus === "running";
    const composerCanStop = Boolean(composerRunActive && (projection.runControlState.canInterrupt || projection.runControlState.runId));
    const hasAccessoryTray = Boolean(
        selectedCommand
        || selectedSkills.length > 0
        || uploadedFiles.length > 0,
    );
    const hasOverlayLayer = Boolean(
        pickerOverlayVisible
        || hudProcesses.length > 0
        || todosVisible
        || hasAccessoryTray,
    );
    const accessoryBottomOffset = bottomLayerHeight > 0 ? bottomLayerHeight + 8 : 144;
    const hudBottomOffset = accessoryBottomOffset + (hasAccessoryTray ? 50 : 0) + 10;
    const pickerBottomOffset = accessoryBottomOffset + (hasAccessoryTray ? 12 : 0);

    const handleSelectCommandFromPicker = (command: CommandPresetSummary) => {
        setSelectedCommand(command);
        setInput("");
    };

    const handleSelectSkillFromPicker = (skill: SkillReferenceSummary) => {
        setSelectedSkills((current) => [...current, skill]);
        setInput("");
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
                    skills={filteredSkills}
                    onSelectCommand={handleSelectCommandFromPicker}
                    onSelectSkill={handleSelectSkillFromPicker}
                />
            ) : null}

            <View
                pointerEvents="box-none"
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

            {hasAccessoryTray ? (
                <View
                    pointerEvents="box-none"
                    style={[
                        styles.composerAccessoryTray,
                        {
                            bottom: accessoryBottomOffset,
                        },
                    ]}
                >
                    <ScrollView
                        horizontal
                        showsHorizontalScrollIndicator={false}
                        keyboardShouldPersistTaps="always"
                        contentContainerStyle={styles.composerAccessoryTrayContent}
                    >
                        {selectedCommand ? (
                            <Pressable
                                style={[
                                    styles.accessoryChip,
                                    {
                                        backgroundColor: palette.accentSoft,
                                        borderColor: palette.accentSoft,
                                    },
                                ]}
                                onPress={() => setSelectedCommand(null)}
                            >
                                <MaterialCommunityIcons name="slash-forward" size={13} color={palette.accent} />
                                <Text style={[styles.accessoryChipText, { color: palette.accent }]} numberOfLines={1}>
                                    {selectedCommand.name}
                                </Text>
                                <MaterialCommunityIcons name="close" size={14} color={palette.accent} />
                            </Pressable>
                        ) : null}
                        {selectedSkills.map((skill) => (
                            <Pressable
                                key={`${skill.name}:${skill.path || ""}`}
                                style={[
                                    styles.accessoryChip,
                                    {
                                        backgroundColor: palette.primarySoft,
                                        borderColor: palette.primarySoft,
                                    },
                                ]}
                                onPress={() => setSelectedSkills((current) =>
                                    current.filter((item) => `${item.name}:${item.path || ""}` !== `${skill.name}:${skill.path || ""}`),
                                )}
                            >
                                <MaterialCommunityIcons name="at" size={13} color={palette.primary} />
                                <Text style={[styles.accessoryChipText, { color: palette.primaryDeep }]} numberOfLines={1}>
                                    {skill.name}
                                </Text>
                                <MaterialCommunityIcons name="close" size={14} color={palette.primaryDeep} />
                            </Pressable>
                        ))}
                        {uploadedFiles.map((file) => (
                            <Pressable
                                key={buildUploadedFileStableKey(file)}
                                style={[
                                    styles.accessoryChip,
                                    {
                                        backgroundColor: palette.surfaceStrong,
                                        borderColor: palette.border,
                                    },
                                ]}
                                onPress={() => setUploadedFiles((current) => removeUploadedWorkspaceFile(current, file))}
                            >
                                <MaterialCommunityIcons name="paperclip" size={13} color={palette.textMuted} />
                                <Text style={[styles.accessoryChipText, { color: palette.text, maxWidth: 168 }]} numberOfLines={1}>
                                    {file.name || t("附件", "Attachment")}
                                </Text>
                                <MaterialCommunityIcons name="close" size={14} color={palette.textMuted} />
                            </Pressable>
                        ))}
                    </ScrollView>
                </View>
            ) : null}
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
            {activeConversationId ? (
                <Composer
                    value={input}
                    onChange={setInput}
                    onSend={() => void handleSend()}
                    busy={sending}
                    isRunning={composerRunActive}
                    canStop={composerCanStop}
                    onStop={() => void handleRunCommand("interrupt")}
                    selectedCommand={selectedCommand}
                    onClearCommand={() => setSelectedCommand(null)}
                    selectedSkills={selectedSkills}
                    onRemoveSkill={(skill) => setSelectedSkills((current) =>
                        current.filter((item) => `${item.name}:${item.path || ""}` !== `${skill.name}:${skill.path || ""}`),
                    )}
                    taskPlanningMode={taskPlanningMode}
                    onToggleTaskPlanningMode={() => setTaskPlanningMode((current) => !current)}
                    uploadedFiles={uploadedFiles}
                    onRemoveUploadedFile={(file) => setUploadedFiles((current) => removeUploadedWorkspaceFile(current, file))}
                    onPickAttachment={() => void handlePickAttachment()}
                    onToggleRecording={() => void handleToggleRecording()}
                    attachmentBusy={attachmentBusy}
                    recording={recorderState.isRecording}
                    transcribing={transcribing}
                />
            ) : (
                <GlassCard style={[styles.workspaceHintCard, { backgroundColor: palette.surfaceStrong, borderColor: palette.border }]}>
                    <Text style={[styles.workspaceHintText, { color: palette.textMuted }]}>
                        {t("先选择主工作区或项目级工作区，再开始新对话。", "Choose the main workspace or a project workspace before starting a new conversation.")}
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
                                                    {t("选择工作区", "Choose a workspace")}
                                                </Text>
                                                <Text style={[styles.workspaceChooserSubtitle, { color: palette.textMuted }]}>
                                                    {t("历史会话始终优先；这里只在你明确开始新对话时出现。", "History always stays higher priority; this only appears when you explicitly start a new conversation.")}
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
                                                {t("主工作区", "Main workspace")}
                                            </Text>
                                            <Text style={[styles.workspaceOptionMeta, { color: palette.textMuted }]}>
                                                {mainWorkspacePath || t("正在读取主工作区路径…", "Loading main workspace path...")}
                                            </Text>
                                        </Pressable>

                                        <View style={[styles.workspaceChooserSection, { borderColor: palette.border }]}>
                                            <Text style={[styles.workspaceSectionLabel, { color: palette.textMuted }]}>
                                                {t("现有项目级工作区", "Existing project workspaces")}
                                            </Text>
                                            <View style={styles.workspaceOptionList}>
                                                {availableProjects.length === 0 ? (
                                                    <Text style={[styles.workspaceEmptyText, { color: palette.textMuted }]}>
                                                        {t("当前还没有可用的项目级工作区。", "No project workspaces are available yet.")}
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
                                                                {project.name || project.id || t("未命名项目", "Unnamed project")}
                                                            </Text>
                                                            <Text style={[styles.workspaceOptionMeta, { color: palette.textMuted }]}>
                                                                {project.workspacePath || project.id || t("路径未就绪", "Path unavailable")}
                                                            </Text>
                                                        </Pressable>
                                                    ))
                                                )}
                                            </View>
                                        </View>

                                        <View style={[styles.workspaceChooserSection, { borderColor: palette.border }]}>
                                            <Text style={[styles.workspaceSectionLabel, { color: palette.textMuted }]}>
                                                {t("新建项目级工作区", "Create a project workspace")}
                                            </Text>
                                            <Text style={[styles.workspaceSectionHint, { color: palette.textMuted }]}>
                                                {t("这里只填项目名称；系统会在 ~/.v8-agent-os/workspace/projects 下自动创建路径。", "Only a project name is needed here; the system creates the path under ~/.v8-agent-os/workspace/projects automatically.")}
                                            </Text>
                                            <View style={styles.workspaceCreateRow}>
                                                <TextInput
                                                    value={newProjectName}
                                                    onChangeText={setNewProjectName}
                                                    placeholder={t("输入项目名称", "Enter a project name")}
                                                    placeholderTextColor={palette.textSoft}
                                                    style={[styles.workspaceNameInput, { color: palette.text, backgroundColor: palette.surface, borderColor: palette.border }]}
                                                />
                                                <Pressable
                                                    style={[
                                                        styles.workspaceCreateButton,
                                                        {
                                                            backgroundColor: palette.primary,
                                                            opacity: workspaceChooserBusy || newProjectName.trim().length === 0 ? 0.56 : 1,
                                                        },
                                                    ]}
                                                    disabled={workspaceChooserBusy || newProjectName.trim().length === 0}
                                                    onPress={() => void handleCreateProjectConversation()}
                                                >
                                                    {workspaceChooserBusy ? (
                                                        <ActivityIndicator size="small" color="#FFFFFF" />
                                                    ) : (
                                                        <Text style={styles.workspaceCreateButtonText}>{t("创建并开始", "Create and start")}</Text>
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

                            <View pointerEvents="box-none" style={[styles.chatStageHeader, isLandscape && styles.chatStageHeaderLandscape]}>
                                <View style={[styles.chatStageTopRow, isLandscape && styles.chatStageTopRowLandscape]}>
                                    <Pressable
                                        style={[
                                            styles.historyFab,
                                            { backgroundColor: palette.surfaceStrong, borderColor: palette.border },
                                        ]}
                                        onPress={() => setHistoryOpen(true)}
                                    >
                                        <MaterialCommunityIcons name="view-headline" size={20} color={palette.text} />
                                    </Pressable>

                                    <View
                                        style={[
                                            styles.controlRailPrimary,
                                            {
                                                backgroundColor: palette.surfaceStrong,
                                                borderColor: palette.border,
                                            },
                                        ]}
                                    >
                                        <Pressable
                                            accessibilityRole="button"
                                            accessibilityLabel={t("当前工作区", "Current workspace")}
                                            style={[
                                                styles.scopeTrigger,
                                                {
                                                    backgroundColor: workspaceInfoOpen || workspaceChooserVisible ? palette.primarySoft : palette.surfaceStrong,
                                                    borderColor: workspaceInfoOpen || workspaceChooserVisible ? `${palette.primary}33` : palette.border,
                                                },
                                            ]}
                                            onPress={() => {
                                                if (activeConversationId) {
                                                    setWorkspaceInfoOpen(true);
                                                    return;
                                                }
                                                setWorkspaceChooserVisible(true);
                                                clearNewConversationIntent();
                                            }}
                                        >
                                            <View style={styles.scopeTriggerIconWrap}>
                                                {scopeLoading ? (
                                                    <ActivityIndicator size="small" color={workspaceInfoOpen || workspaceChooserVisible ? palette.primary : palette.textMuted} />
                                                ) : (
                                                    <MaterialCommunityIcons
                                                        name="file-tree-outline"
                                                        size={16}
                                                        color={workspaceInfoOpen || workspaceChooserVisible ? palette.primary : palette.textMuted}
                                                    />
                                                )}
                                            </View>
                                        </Pressable>

                                        <View style={styles.runControlWrap}>
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

                                        <View style={styles.runtimeDockInline}>
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
                                    </View>
                                </View>
                            </View>
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

                <Modal visible={workspaceInfoOpen} transparent animationType="fade" onRequestClose={() => setWorkspaceInfoOpen(false)}>
                    <View style={[styles.scopeSheetOverlay, { backgroundColor: palette.overlay }]}>
                        <Pressable style={StyleSheet.absoluteFill} onPress={() => setWorkspaceInfoOpen(false)} />
                        <GlassCard style={[styles.scopeSheetCard, { backgroundColor: palette.surfaceStrong, borderColor: palette.border }]}>
                            <View style={[styles.scopeSheetHandle, { backgroundColor: palette.border }]} />
                            <View style={styles.scopeSheetHeader}>
                                <View style={styles.scopeSheetHeaderText}>
                                    <Text style={[styles.contextTitle, { color: palette.text }]}>{t("当前工作区", "Current workspace")}</Text>
                                    <Text style={[styles.contextSubtitle, { color: palette.textMuted }]}>
                                        {t("当前会话的工作区绑定在创建后已冻结；如需切换，请新建对话。", "This conversation binding is frozen after creation. Start a new conversation to switch workspaces.")}
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
                                        {t("当前绑定", "Current binding")}
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
                                        {t("会话信息", "Conversation info")}
                                    </Text>
                                    <View style={styles.contextChips}>
                                        <View style={[styles.contextChip, { backgroundColor: palette.primarySoft, borderColor: `${palette.primary}1A` }]}>
                                            <Text style={[styles.contextChipText, { color: palette.primaryDeep }]}>
                                                {t("会话", "Conversation")}：{projection.activeConversation?.title || t("当前对话", "Current conversation")}
                                            </Text>
                                        </View>
                                        <View style={[styles.contextChip, { backgroundColor: palette.surface, borderColor: palette.border }]}>
                                            <Text style={[styles.contextChipText, { color: palette.textMuted }]}>
                                                {t("工作区类型", "Workspace kind")}：{currentWorkspaceLabel}
                                            </Text>
                                        </View>
                                        <View style={[styles.contextChip, { backgroundColor: palette.surface, borderColor: palette.border }]}>
                                            <Text style={[styles.contextChipText, { color: palette.textMuted }]}>
                                                Scope：{scopeBinding?.resolvedScope || "global"}
                                            </Text>
                                        </View>
                                        <View style={[styles.contextChip, { backgroundColor: palette.surface, borderColor: palette.border }]}>
                                            <Text style={[styles.contextChipText, { color: palette.textMuted }]}>
                                                {t("路径", "Path")}：{currentWorkspacePath}
                                            </Text>
                                        </View>
                                    </View>
                                </View>
                            </ScrollView>
                        </GlassCard>
                    </View>
                </Modal>

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
                                    setDesktopPreviewError(t("桌面预览尚未就绪，请稍候。", "Desktop preview is not ready yet. Please wait."));
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
        zIndex: 24,
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
    workspaceOptionList: {
        gap: 8,
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
