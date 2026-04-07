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
import { ComposerPickerOverlay } from "@/src/components/chat/ComposerPickerOverlay";
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
import { getDayGreeting } from "@/src/lib/time";
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
    getRealtimeSnapshot,
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
    PhoneUiTimelineNode,
    DesktopLiveStatus,
    RealtimeSessionSnapshot,
    SessionTodoItem,
    SkillReferenceSummary,
    UploadedWorkspaceFile,
    DesktopLiveSessionPayload,
} from "@/src/types/admin";
import {
    createInitialSessionRealtimeMessageState,
    type AdminProcessRef,
    type ContextReferenceItem,
    deriveAuthoritativeSessionView,
    flushQueuedSessionRealtimeRuntimeEvents,
    isAskUserInteractionApproval,
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
        workspaceRoot: artifact.workspaceRoot,
        workspaceRelativePath: artifact.workspaceRelativePath,
        canonicalPath: artifact.canonicalPath,
        projectId: artifact.projectId,
        workspaceId: artifact.workspaceId,
        storageClass: artifact.storageClass,
        surfaceVisible: artifact.surfaceVisible,
        mimeType: artifact.mimeType,
        resourceRef: artifact.resourceRef || null,
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

function findLatestAssistantShellIndex(messages: ChatMessage[]) {
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
    let targetIndex = findLatestAssistantShellIndex(next);

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
        uiEphemeral: target.uiEphemeral !== false,
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

    return Array.from(keys);
}

function hasRenderableMessagePayload(message: ChatMessage) {
    return Boolean(
        String(message.content || "").trim()
        || (Array.isArray(message.images) && message.images.length > 0)
        || (Array.isArray(message.artifacts) && message.artifacts.length > 0)
        || (Array.isArray(message.nodes) && message.nodes.length > 0),
    );
}

function hasActiveLocalAssistantShell(messages: ChatMessage[]) {
    return messages.some((message) =>
        message.role === "assistant"
        && isActiveAssistantStreamPhase(message.uiStreamPhase),
    );
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

function mergeAuthoritativeSnapshotMessages(
    current: ChatMessage[],
    snapshotMessages: ChatMessage[],
    preserveOptimisticLocalState: boolean,
) {
    const normalizedSnapshot = normalizeMessagesForState(snapshotMessages);
    if (!preserveOptimisticLocalState) {
        return normalizedSnapshot;
    }

    const optimisticLocals = current.filter(isOptimisticLocalMessage);
    if (optimisticLocals.length === 0) {
        return normalizedSnapshot;
    }

    const usedOptimisticLocals = new Set<string>();
    const mergedSnapshotMessages = normalizedSnapshot.map((snapshotMessage) => {
        const snapshotKeys = new Set(buildMessageComparisonKeys(snapshotMessage));
        const matchingLocal = optimisticLocals.find((candidate) => {
            const localId = String(candidate.id || "").trim();
            if (localId && usedOptimisticLocals.has(localId)) {
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
            usedOptimisticLocals.add(matchingLocalId);
        }

        const mergedMessage = normalizeMessagesForState([snapshotMessage, matchingLocal])[0] || snapshotMessage;
        const localStreamActive = matchingLocal.role === "assistant" && isActiveAssistantStreamPhase(matchingLocal.uiStreamPhase);
        const snapshotRenderable = hasRenderableMessagePayload(snapshotMessage);
        const localRenderable = hasRenderableMessagePayload(matchingLocal);
        const preferLocalPayload = localRenderable && buildMessageRichness(matchingLocal) > buildMessageRichness(snapshotMessage);

        if (matchingLocal.role === "assistant" && matchingLocal.uiEphemeral) {
            mergedMessage.uiEphemeral = !snapshotRenderable;
            mergedMessage.uiStreamPhase = localStreamActive
                ? matchingLocal.uiStreamPhase
                : snapshotMessage.uiStreamPhase;
        }

        if (matchingLocal.role === "assistant") {
            const taskProgress = asRecord(matchingLocal.metadata?.assistantTaskProgress);
            if (Object.keys(taskProgress).length > 0 && (localStreamActive || !snapshotRenderable)) {
                mergedMessage.metadata = {
                    ...(mergedMessage.metadata || {}),
                    assistantTaskProgress: taskProgress,
                };
            }
        }

        if (matchingLocal.role === "assistant" && (localStreamActive || preferLocalPayload)) {
            if (!snapshotRenderable || preferLocalPayload) {
                if (String(matchingLocal.content || "").trim().length > String(mergedMessage.content || "").trim().length) {
                    mergedMessage.content = matchingLocal.content;
                }
                if ((matchingLocal.nodes || []).length > (mergedMessage.nodes || []).length) {
                    mergedMessage.nodes = matchingLocal.nodes;
                }
                if ((matchingLocal.images || []).length > 0) {
                    mergedMessage.images = mergeMessageImages(mergedMessage.images || [], matchingLocal.images || []);
                }
                if ((matchingLocal.artifacts || []).length > 0) {
                    mergedMessage.artifacts = mergeArtifacts(mergedMessage.artifacts || [], matchingLocal.artifacts || []);
                }
                if (matchingLocal.metadata) {
                    mergedMessage.metadata = {
                        ...(mergedMessage.metadata || {}),
                        ...matchingLocal.metadata,
                    };
                }
            }
            if (!snapshotRenderable && matchingLocal.uiEphemeral) {
                mergedMessage.uiEphemeral = true;
            }
        }

        return mergedMessage;
    });

    const unmatchedOptimisticLocals = optimisticLocals.filter((message) => {
        const messageId = String(message.id || "").trim();
        if (messageId && usedOptimisticLocals.has(messageId)) {
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
    const messageCandidates = [root.messages, snapshot.messages];
    for (const candidate of messageCandidates) {
        if (Array.isArray(candidate)) {
            return candidate.filter((item): item is ChatMessage => Boolean(item) && typeof item === "object");
        }
    }
    return null;
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" ? value as Record<string, unknown> : {};
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
    const {
        status,
        user,
        adminBaseUrl,
        activeConversationId,
        setActiveConversationId,
        authorizedFetch,
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
    const lastMessageFingerprintRef = useRef("");
    const lastAppliedSnapshotSeqRef = useRef(0);
    const lastAppliedSnapshotFingerprintRef = useRef("");
    const lastRealtimeSnapshotAtRef = useRef(0);
    const seenRealtimeEventKeysRef = useRef<Set<string>>(new Set());
    const messagesRef = useRef<ChatMessage[]>([]);
    const todosRef = useRef<SessionTodoItem[]>([]);
    const realtimeMessageStateRef = useRef(
        createInitialSessionRealtimeMessageState<ChatMessage>([], PHONE_STREAM_LIFECYCLE_OPTIONS),
    );
    const runtimeFlushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
    const runtimeRef = useRef<RuntimeSummary>({ status: "idle", latestSeq: 0 });
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
    const [processes, setProcesses] = useState<AdminProcessRef[]>([]);
    const [contextReferences, setContextReferences] = useState<ContextReferenceItem[]>([]);
    const [musicTracks, setMusicTracks] = useState<MusicTrack[]>([]);
    const [commands, setCommands] = useState<CommandPresetSummary[]>([]);
    const [skills, setSkills] = useState<SkillReferenceSummary[]>([]);
    const [uploadedFiles, setUploadedFiles] = useState<UploadedWorkspaceFile[]>([]);
    const [selectedCommand, setSelectedCommand] = useState<CommandPresetSummary | null>(null);
    const [selectedSkills, setSelectedSkills] = useState<SkillReferenceSummary[]>([]);
    const [taskPlanningMode, setTaskPlanningMode] = useState(false);
    const [composerHeight, setComposerHeight] = useState(132);
    const [todosDockHeight, setTodosDockHeight] = useState(0);
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
        todosRef.current = todos;
    }, [todos]);

    const sendingRef = useRef(sending);

    useEffect(() => {
        sendingRef.current = sending;
    }, [sending]);

    const syncArtifactsFromMessages = useCallback((nextMessages: ChatMessage[]) => {
        const derived = collectArtifactsFromMessages(nextMessages).map(toArtifactDetail);
        setArtifacts(mergeArtifactDetails([], derived));
    }, []);

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
        syncArtifactsFromMessages(nextState.messages);
        messagesRef.current = nextState.messages;
        setMessages(nextState.messages);
    }, [syncArtifactsFromMessages]);

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
        const { store, view } = deriveAuthoritativeSessionView(payload);
        if (!view) {
            return;
        }
        const nextApprovals = view.approvals as PendingApproval[];
        const hasAskUserPending = nextApprovals.some((item) => isAskUserInteractionApproval(item));
        const hasGovernanceApprovalPending = nextApprovals.some((item) => !isAskUserInteractionApproval(item));
        const nextTodos = view.todos?.items || [];
        const nextRuntimeEvents = view.runtimeTimeline;
        const nextRuntimeStatus = view.runtimeStatus;
        const nextSummary = asRecord(view.summary);
        const nextCurrentRun = asRecord(view.currentRun);
        const workflowProjection = asRecord(view.workflowProjection);

        setApprovals(nextApprovals);
        setTodos(nextTodos as SessionTodoItem[]);
        todosRef.current = nextTodos as SessionTodoItem[];
        setProcesses(view.processes || []);
        setContextReferences(view.contextReferences || []);
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
                || runtimeRef.current.status
                || "idle",
            ).trim() || "idle",
            latestSeq: Number(store.latestSeq || 0) || 0,
            runId: typeof nextCurrentRun.id === "string"
                ? nextCurrentRun.id
                : typeof nextCurrentRun.runId === "string"
                    ? nextCurrentRun.runId
                    : runtimeRef.current.runId,
            label: typeof nextSummary.currentStepTitle === "string"
                ? nextSummary.currentStepTitle
                : typeof nextSummary.lastRuntimeSummary === "string"
                    ? nextSummary.lastRuntimeSummary
                    : runtimeRef.current.label,
        };
        setRuntime(nextRuntime);
        latestSeqRef.current = nextRuntime.latestSeq;
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
    }, [patchAssistantTaskShell]);

    const applyRealtimeSnapshotPayload = useCallback((payload: Partial<ConversationDetail | RealtimeSessionSnapshot | Record<string, unknown>> | null | undefined) => {
        const snapshotMessages = extractSnapshotMessages(payload);
        const snapshotSeq = buildSnapshotSequence(payload);
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
                const preserveOptimisticLocalState = sendingRef.current || hasActiveLocalAssistantShell(current);
                const normalized = mergeAuthoritativeSnapshotMessages(
                    current,
                    normalizedSnapshot,
                    preserveOptimisticLocalState,
                );
                const fingerprint = buildMessagesFingerprint(normalized);
                if (fingerprint === lastMessageFingerprintRef.current) {
                    return current;
                }
                lastMessageFingerprintRef.current = fingerprint;
                realtimeMessageStateRef.current = syncSessionRealtimeMessageState(
                    normalized,
                    PHONE_STREAM_LIFECYCLE_OPTIONS,
                );
                syncArtifactsFromMessages(normalized);
                messagesRef.current = normalized;
                return normalized;
            });
        }
        if (snapshotSeq > 0) {
            latestSeqRef.current = Math.max(latestSeqRef.current, snapshotSeq);
            lastAppliedSnapshotSeqRef.current = Math.max(lastAppliedSnapshotSeqRef.current, snapshotSeq);
            lastRealtimeSnapshotAtRef.current = Date.now();
        }
        applyConversationProjection(payload);
    }, [applyConversationProjection, syncArtifactsFromMessages]);

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
        if (eventName === "snapshot" && payload && typeof payload === "object") {
            applyRealtimeSnapshotPayload(payload as RealtimeSessionSnapshot);
            return;
        }

        const isLocalStreamEvent = eventName === "local_stream";
        const normalized = normalizePhoneRealtimeEvent(payload, locale);
        if (!normalized) {
            return;
        }

        const dedupKey = buildRealtimeEventDedupKey(normalized);
        if (seenRealtimeEventKeysRef.current.has(dedupKey)) {
            return;
        }
        if (!isLocalStreamEvent && normalized.seq && normalized.seq < latestSeqRef.current) {
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
                normalized.data?.toolName
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
                    await streamRealtimeSession(authorizedFetch, conversationId, handleRealtimeEvent, controller.signal);
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
    }, [authorizedFetch, handleRealtimeEvent, scheduleRealtimeSnapshotRefresh, stopRealtime]);

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
            const snapshotMessages = normalizeMessagesForState(detail.messages || []);
            const preserveOptimisticLocalState = (
                optimisticSeedConversationIdRef.current === conversationId
                || sendingRef.current
                || hasActiveLocalAssistantShell(messagesRef.current)
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
            setMessages(normalized);
            syncArtifactsFromMessages(normalized);
            applyConversationProjection(detail);
            lastAppliedSnapshotSeqRef.current = buildSnapshotSequence(detail);
            lastAppliedSnapshotFingerprintRef.current = buildMessagesFingerprint(normalized);
            lastRealtimeSnapshotAtRef.current = Date.now();
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
    }, [applyConversationProjection, authorizedFetch, resetConversationStreamState, syncArtifactsFromMessages]);

    const ensureConversation = useCallback(async () => {
        if (activeConversationId) {
            return { id: activeConversationId, created: false };
        }

        const created = await createConversation(authorizedFetch, "");
        optimisticSeedConversationIdRef.current = created.id;
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
            resetConversationStreamState();
            messagesRef.current = [];
            setMessages([]);
            setApprovals([]);
            setTodos([]);
            setArtifacts([]);
            setProcesses([]);
            setContextReferences([]);
            setRuntime({ status: "idle", latestSeq: 0 });
            setRuntimeTimeline([]);
            latestSeqRef.current = 0;
            lastAppliedSnapshotSeqRef.current = 0;
            lastAppliedSnapshotFingerprintRef.current = "";
            lastRealtimeSnapshotAtRef.current = 0;
            stopRealtimeRef.current();
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
            latestSeqRef.current = 0;
            lastAppliedSnapshotSeqRef.current = 0;
            lastAppliedSnapshotFingerprintRef.current = "";
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
    }, [activeConversationId, status]);

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
        setHistoryOpen(false);
        setInput("");
        setUploadedFiles([]);
        await setActiveConversationId(item.id);
    }, [setActiveConversationId]);

    const handleNewConversation = useCallback(async () => {
        stopRealtime();
        resetConversationStreamState();
        optimisticSeedConversationIdRef.current = null;
        hydratedConversationIdRef.current = null;
        loadingConversationIdRef.current = null;
        setHistoryOpen(false);
        setInput("");
        messagesRef.current = [];
        setMessages([]);
        setApprovals([]);
        setTodos([]);
        setArtifacts([]);
        setProcesses([]);
        setContextReferences([]);
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
        lastAppliedSnapshotSeqRef.current = 0;
        lastAppliedSnapshotFingerprintRef.current = "";
        lastRealtimeSnapshotAtRef.current = 0;
        await setActiveConversationId(null);
    }, [resetConversationStreamState, setActiveConversationId, stopRealtime]);

    const handleBrandPress = useCallback(async () => {
        await handleNewConversation();
        router.replace("/chat" as Href);
    }, [handleNewConversation]);

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

            const uploaded = await Promise.all(result.assets.map(async (asset) => uploadAttachment(authorizedFetch, {
                uri: asset.uri,
                name: asset.name,
                type: asset.mimeType || "application/octet-stream",
            })));

            setUploadedFiles((current) => [...current, ...uploaded]);
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
            processes,
            contextReferences,
            runtime,
            runtimeTimeline,
            selectedRuntimeId,
            t,
            locale,
        }),
        [activeConversationId, approvals, artifacts, contextReferences, conversations, locale, messages, processes, runtime, runtimeTimeline, selectedRuntimeId, t, todos],
    );

    const latestAutoPlayableVoice = projection.voiceCardDescriptors[projection.voiceCardDescriptors.length - 1] || null;
    const latestProjectedMessage = projection.projectedMessages[projection.projectedMessages.length - 1] || null;
    const latestProjectedMessageKey = String(
        latestProjectedMessage?.renderKey
        || latestProjectedMessage?.id
        || "",
    ).trim();

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

        setSending(true);
        try {
            const ensuredConversation = await ensureConversation();
            const userMessage = buildUserMessage(text, {
                command: selectedCommand,
                skills: selectedSkills,
                taskPlanningMode,
                files: uploadedFiles,
            });
            const assistantPlaceholder = buildAssistantPlaceholder();
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
                return next;
            });
            setRuntime((current) => ({
                ...current,
                status: "running",
            }));
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

            const historyMessages = messagesRef.current.map((message) => ({
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
                    handleRealtimeEvent("local_stream", event);
                    const normalized = normalizePhoneRealtimeEvent(event, locale);
                    if (!normalized) {
                        return;
                    }
                    if (normalized.type === "error") {
                        throw new Error(String(normalized.error || normalized.content || t("聊天流失败", "Chat stream failed")));
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
        locale,
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
            subtitle: t("想先聊什么？", "What would you like to start with?"),
        }
        : null;
    const composerHorizontalInset = isLandscape ? 18 : 10;
    const composerBottomInset = Platform.OS === "ios" ? 8 : 4;
    const todosVisible = projection.todos.length > 0;
    const todosDockBottom = composerBottomInset + composerHeight + 8;
    const chatBottomInset = composerHeight + composerBottomInset + (todosVisible ? todosDockHeight + 24 : 16);
    const pickerOverlayVisible = commandPickerOpen || skillPickerOpen;
    const pickerOverlayMode = commandPickerOpen ? "command" : skillPickerOpen ? "skill" : null;
    const pickerOverlayBottom = todosDockBottom + (todosVisible ? todosDockHeight + 8 : 0);

    const handleSelectCommandFromPicker = (command: CommandPresetSummary) => {
        setSelectedCommand(command);
        setInput("");
    };

    const handleSelectSkillFromPicker = (skill: SkillReferenceSummary) => {
        setSelectedSkills((current) => [...current, skill]);
        setInput("");
    };

    useEffect(() => {
        if (!todosVisible && todosDockHeight !== 0) {
            setTodosDockHeight(0);
        }
    }, [todosDockHeight, todosVisible]);

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

                                <View style={styles.runControlWrap}>
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
                            artifacts={projection.artifacts}
                            processes={projection.processes}
                            contextReferences={projection.contextReferences}
                            pendingApproval={projection.pendingApproval}
                            pendingApprovalCount={projection.pendingApprovalCount}
                            approvalBusy={sending}
                            onResolveApproval={handleApprovalResolve}
                            onOpenApprovalPanel={openApprovalPanel}
                            isLandscape={isLandscape}
                            bottomInset={chatBottomInset}
                            emptyState={greetingEmptyState}
                        />

                        {todosVisible ? (
                            <View
                                pointerEvents="box-none"
                                style={[
                                    styles.todosDock,
                                    isLandscape && styles.todosDockLandscape,
                                    {
                                        left: composerHorizontalInset,
                                        right: composerHorizontalInset,
                                        bottom: todosDockBottom,
                                    },
                                ]}
                                onLayout={(event) => {
                                    const nextHeight = Math.round(event.nativeEvent.layout.height);
                                    if (nextHeight >= 0 && nextHeight !== todosDockHeight) {
                                        setTodosDockHeight(nextHeight);
                                    }
                                }}
                            >
                                <TodosHUD items={projection.todos} />
                            </View>
                        ) : null}

                        <ComposerPickerOverlay
                            visible={pickerOverlayVisible}
                            mode={pickerOverlayMode}
                            left={composerHorizontalInset}
                            right={composerHorizontalInset}
                            bottom={pickerOverlayBottom}
                            commands={filteredCommands}
                            skills={filteredSkills}
                            onSelectCommand={handleSelectCommandFromPicker}
                            onSelectSkill={handleSelectSkillFromPicker}
                        />

                        <View
                            style={[styles.composerWrap, isLandscape && styles.composerWrapLandscape]}
                            pointerEvents="box-none"
                            onLayout={(event) => {
                                const nextHeight = Math.round(event.nativeEvent.layout.height);
                                if (nextHeight > 0 && nextHeight !== composerHeight) {
                                    setComposerHeight(nextHeight);
                                }
                            }}
                        >
                            <Composer
                                value={input}
                                onChange={setInput}
                                onSend={() => void handleSend()}
                                busy={sending}
                                selectedCommand={selectedCommand}
                                onClearCommand={() => setSelectedCommand(null)}
                                selectedSkills={selectedSkills}
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
                    processes={projection.processes}
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
        minHeight: 32,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 0,
        paddingVertical: 0,
        width: 72,
        minWidth: 72,
        maxWidth: 72,
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
    todosDock: {
        position: "absolute",
        zIndex: 24,
    },
    todosDockLandscape: {
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
