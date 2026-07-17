import { memo, useEffect, useMemo, useState } from "react";
import { Image, Pressable, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import * as Clipboard from "expo-clipboard";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import {
    buildCollaborationMicroStages,
    buildMessageTimelineSegments,
    isRuntimeEpisodeGraphActivity,
    type AdminProcessRef,
    type CollaborationMicroStageActivityInput,
    type MessageTimelineSegment,
} from "@v8/session-realtime";
import { buildComposerInlineSegments } from "@v8/session-realtime/composer-inline-references";
import {
    buildMessageBoundCollaborationMicroStagePlacement,
    buildMessageBoundExecutionNodes,
    getMessageBoundExecutionTimelineNodeIdentityCandidates,
    type MessageBoundExecutionMessage,
} from "@v8/session-realtime/message-bound-execution-node";
import Animated, {
    Easing,
    cancelAnimation,
    useAnimatedStyle,
    useSharedValue,
    withDelay,
    withRepeat,
    withSequence,
    withTiming,
} from "react-native-reanimated";

import { resolveAdminAssetUrl } from "@/src/lib/admin-client";
import { buildPhoneToolExecutionView } from "@/src/lib/chat-node-visibility";
import { buildVoicePlaybackKey, parsePhoneContentBlocks } from "@/src/lib/content-detector";
import { formatClock } from "@/src/lib/time";
import { resolveRenderableMediaUrl } from "@/src/lib/workspace-links";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type { ChatMessage, ComposerPresentation, PhoneUiExecutionNode, PhoneUiTimelineNode, SkillReferenceSummary } from "@/src/types/admin";
import { ContentDispatcher } from "@/src/components/chat/ContentDispatcher";
import {
    CollaborationMicroStageScene,
    type CollaborationMicroStageDetailTarget,
} from "@/src/components/chat/collaboration/CollaborationMicroStageScene";
import { NodeRenderBoundary } from "@/src/components/chat/NodeRenderBoundary";
import { MessageBlockItem } from "@/src/components/chat/MessageBlockItem";
import { MediaPlayer } from "@/src/components/chat/MediaRenderers";
import { MediaViewerLightbox, type MediaItem } from "@/src/components/chat/MediaViewerLightbox";
import { SupervisorActivitySummary } from "@/src/components/chat/SupervisorActivitySummary";
import {
    type PhoneRuntimeStageActivity,
} from "@/src/lib/runtime-stage";

const BRAND_MARK = require("../../../assets/images/brand-mark.png");
const MICRO_STAGE_TOOL_NAMES = new Set(["delegation_broker", "runtime_broker"]);
const MICRO_STAGE_ACTIVITY_LIMIT = 80;

type PhoneTimelineRenderSegment = MessageTimelineSegment<PhoneUiTimelineNode> | {
    kind: "collaboration_stage";
    id: string;
};

function isExecutionNode(node: PhoneUiTimelineNode): node is PhoneUiExecutionNode {
    return node.kind === "execution";
}

function isSupervisorVisibleActivityNode(node: PhoneUiTimelineNode) {
    if (node.kind === "narrative") {
        return parsePhoneContentBlocks(String(node.content || ""), false, 0, false)
            .some((block) => block.type !== "voice" && block.content.trim().length > 0);
    }

    if (!isExecutionNode(node)) {
        return false;
    }

    const executionType = String(node.executionType || "").trim();
    return executionType === "reasoning"
        || executionType === "tool_call"
        || executionType === "tool_result";
}

function toMicroStageActivityInput(activity: PhoneRuntimeStageActivity): CollaborationMicroStageActivityInput {
    return {
        id: activity.id,
        topic: activity.topic,
        summary: activity.summary,
        timestamp: activity.timestamp,
        runtimeId: activity.runtimeId,
        data: activity.node.kind === "execution" && activity.node.data && typeof activity.node.data === "object"
            ? activity.node.data as Record<string, unknown>
            : {},
    };
}

function isPhoneMicroStageActivity(activity: PhoneRuntimeStageActivity): boolean {
    return isRuntimeEpisodeGraphActivity({
        topic: activity.topic || ("topic" in activity.node ? String(activity.node.topic || "") : ""),
    });
}

function getExecutionTopic(node: PhoneUiExecutionNode) {
    return String(node.topic || node.data?.topic || "").trim().toLowerCase();
}

function getExecutionToolName(node: PhoneUiExecutionNode) {
    return String(node.toolName || node.data?.toolName || node.data?.tool_name || "").trim().toLowerCase();
}

function isMicroStageSupersededTimelineNode(node: PhoneUiTimelineNode, microStageVisible: boolean) {
    if (!microStageVisible || !isExecutionNode(node)) {
        return false;
    }

    const topic = getExecutionTopic(node);
    if (node.executionType === "runtime_progress") {
        return topic.startsWith("runtime.episode.")
            || topic.startsWith("handoff.ref.")
            || topic.startsWith("subagent.task.")
            || topic.startsWith("delegation.")
            || topic.startsWith("delegation_broker.");
    }

    return (node.executionType === "tool_call" || node.executionType === "tool_result")
        && MICRO_STAGE_TOOL_NAMES.has(getExecutionToolName(node));
}

function findMicroStageAnchorIndex(nodes: PhoneUiTimelineNode[], sourceNodeIds: string[]) {
    if (sourceNodeIds.length === 0) {
        return nodes.length;
    }
    const sourceIds = new Set(sourceNodeIds);
    const index = nodes.findIndex((node) => (
        getMessageBoundExecutionTimelineNodeIdentityCandidates(node).some((id) => sourceIds.has(id))
    ));
    return index >= 0 ? index : nodes.length;
}

function extractSupervisorMicroStageSpeech(nodes: PhoneUiTimelineNode[], anchorIndex: number) {
    const orderedCandidates = [
        ...nodes.slice(0, anchorIndex).reverse(),
        ...nodes.slice(anchorIndex),
    ];
    for (const node of orderedCandidates) {
        if (node.kind !== "narrative" || node.role !== "assistant") {
            continue;
        }
        const text = parsePhoneContentBlocks(String(node.content || ""), false, 0, false)
            .filter((block) => block.type !== "voice")
            .map((block) => block.content.trim())
            .filter(Boolean)
            .join(" ")
            .replace(/\s+/g, " ")
            .trim();
        if (!text) {
            continue;
        }
        const sentence = text.match(/^.*?[。！？!?](?:\s|$)/)?.[0]?.trim() || text;
        return sentence.length > 64 ? `${sentence.slice(0, 63)}…` : sentence;
    }
    return "";
}

function hasToolCallId(node: PhoneUiTimelineNode): node is PhoneUiExecutionNode & { toolCallId: string } {
    return isExecutionNode(node) && typeof node.toolCallId === "string" && node.toolCallId.trim().length > 0;
}

type Translate = (key: string, params?: Record<string, string | number>) => string;

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

function compactTraceText(value: unknown, maxLength = 92) {
    const text = String(value || "").replace(/\s+/g, " ").trim();
    if (!text) {
        return "";
    }
    return text.length > maxLength ? `${text.slice(0, Math.max(0, maxLength - 3))}...` : text;
}

function toPositiveNumber(value: unknown) {
    const parsed = Number(value ?? 0);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
}

function readStringField(value: unknown, keys: string[]) {
    const record = asRecord(value);
    for (const key of keys) {
        const candidate = record[key];
        if (typeof candidate === "string" && candidate.trim()) {
            return candidate.trim();
        }
    }
    return "";
}

function getToolDisplayName(node: PhoneUiExecutionNode) {
    return String(
        node.toolName
        || node.data?.toolName
        || node.data?.tool_name
        || node.label
        || node.topic
        || "",
    ).trim();
}

function isCommandToolName(toolName: string) {
    return /(shell|terminal|command|cmd|bash|powershell|process|exec|read_thread_terminal)/i.test(toolName);
}

function isFileEditToolName(toolName: string) {
    return /(apply_patch|write|edit|replace|create_file|update_file|delete_file|move_file|filesystem|file_write)/i.test(toolName);
}

function extractToolInput(node: PhoneUiExecutionNode) {
    if (node.args !== undefined && node.args !== null) {
        return node.args;
    }
    const data = asRecord(node.data);
    return data.args ?? data.input ?? data.payload ?? data;
}

function extractCommandPreview(node: PhoneUiExecutionNode) {
    const input = extractToolInput(node);
    const direct = readStringField(input, ["command", "cmd", "script", "input", "code", "text"]);
    if (direct) {
        return compactTraceText(direct);
    }
    if (typeof input === "string") {
        return compactTraceText(input);
    }
    return compactTraceText(getToolDisplayName(node));
}

function formatTraceDuration(node: PhoneUiExecutionNode, resultNode?: PhoneUiExecutionNode) {
    const data = asRecord(node.data);
    const resultData = asRecord(resultNode?.data);
    const ms = toPositiveNumber(
        data.durationMs
        || data.duration_ms
        || resultData.durationMs
        || resultData.duration_ms
        || resultData.elapsedMs
        || resultData.elapsed_ms,
    );
    const seconds = toPositiveNumber(
        data.durationSeconds
        || data.duration_seconds
        || data.elapsedSeconds
        || data.elapsed_seconds
        || resultData.durationSeconds
        || resultData.duration_seconds
        || resultData.elapsedSeconds
        || resultData.elapsed_seconds,
    ) || (ms > 0 ? ms / 1000 : 0);
    if (!seconds) {
        return "";
    }
    if (seconds < 1) {
        return `${Math.max(1, Math.round(seconds * 1000))}ms`;
    }
    if (seconds < 10) {
        return `${Number(seconds.toFixed(1))}s`;
    }
    return `${Math.round(seconds)}s`;
}

function looksLikeFilePath(value: string) {
    const text = value.trim();
    if (!text || text.length > 260) {
        return false;
    }
    return /[\\/]/.test(text) || /\.[a-z0-9]{1,8}$/i.test(text);
}

function addPatchFilePaths(text: string, paths: Set<string>) {
    const patchPathPattern = /^(?:\*\*\* (?:Add|Update|Delete) File:|--- a\/|\+\+\+ b\/)\s*(.+)$/gmi;
    let match = patchPathPattern.exec(text);
    while (match) {
        const candidate = match[1]?.trim();
        if (candidate && !candidate.startsWith("/dev/null") && looksLikeFilePath(candidate)) {
            paths.add(candidate);
        }
        match = patchPathPattern.exec(text);
    }
}

function collectFilePaths(value: unknown, paths: Set<string>, depth = 0) {
    if (depth > 4 || value === null || value === undefined) {
        return;
    }
    if (typeof value === "string") {
        addPatchFilePaths(value, paths);
        if (looksLikeFilePath(value)) {
            paths.add(value.trim());
        }
        return;
    }
    if (Array.isArray(value)) {
        value.forEach((item) => collectFilePaths(item, paths, depth + 1));
        return;
    }
    const record = asRecord(value);
    Object.entries(record).forEach(([key, item]) => {
        const normalizedKey = key.toLowerCase();
        if (typeof item === "string") {
            addPatchFilePaths(item, paths);
            if (
                /(path|file|filename|target|source|destination|workspace|canonical)/.test(normalizedKey)
                && looksLikeFilePath(item)
            ) {
                paths.add(item.trim());
            }
            return;
        }
        collectFilePaths(item, paths, depth + 1);
    });
}

function buildTraceGroupSummary(
    nodes: PhoneUiTimelineNode[],
    resultNodesByToolCallId: Map<string, PhoneUiExecutionNode>,
    t: Translate,
) {
    const executionNodes = nodes.filter(isExecutionNode);
    const toolCalls = executionNodes.filter((node) => node.executionType === "tool_call");
    const reasoningCount = executionNodes.filter((node) => node.executionType === "reasoning").length;
    const runtimeProgressCount = executionNodes.filter((node) => node.executionType === "runtime_progress").length;
    const commandCount = toolCalls.filter((node) => isCommandToolName(getToolDisplayName(node))).length;
    const editedFilePaths = new Set<string>();

    toolCalls.forEach((node) => {
        const toolName = getToolDisplayName(node);
        if (!isFileEditToolName(toolName)) {
            return;
        }
        collectFilePaths(extractToolInput(node), editedFilePaths);
        if (hasToolCallId(node)) {
            const resultNode = resultNodesByToolCallId.get(node.toolCallId.trim());
            collectFilePaths(resultNode?.result ?? resultNode?.data, editedFilePaths);
        }
    });

    const toolCount = toolCalls.length;
    const editedFileCount = editedFilePaths.size;
    const previewLines: string[] = [];
    const pushPreview = (line: string) => {
        const compacted = compactTraceText(line, 110);
        if (compacted && !previewLines.includes(compacted)) {
            previewLines.push(compacted);
        }
    };

    toolCalls.forEach((node) => {
        if (previewLines.length >= 3) {
            return;
        }
        const toolName = getToolDisplayName(node);
        if (isCommandToolName(toolName)) {
            const command = extractCommandPreview(node);
            const resultNode = hasToolCallId(node) ? resultNodesByToolCallId.get(node.toolCallId.trim()) : undefined;
            const duration = formatTraceDuration(node, resultNode);
            pushPreview(duration
                ? t("src.components.chat.messagebubble.trace_preview_ran_command_with_duration", { command, duration })
                : t("src.components.chat.messagebubble.trace_preview_ran_command", { command }));
            return;
        }
        pushPreview(t("src.components.chat.messagebubble.trace_preview_called_tool", {
            tool: compactTraceText(toolName || t("src.components.chat.messagebubble.tool"), 64),
        }));
    });

    if (previewLines.length === 0 && reasoningCount > 0) {
        const reasoningNode = executionNodes.find((node) => node.executionType === "reasoning");
        pushPreview(compactTraceText(
            reasoningNode?.content
            || reasoningNode?.label
            || reasoningNode?.topic
            || t("src.components.chat.messagebubble.trace_preview_thought_complete"),
        ));
    }

    if (previewLines.length === 0 && runtimeProgressCount > 0) {
        const progressNode = executionNodes.find((node) => node.executionType === "runtime_progress");
        pushPreview(compactTraceText(
            progressNode?.label
            || progressNode?.topic
            || t("src.components.chat.messagebubble.trace_runtime_updated"),
        ));
    }

    let title = "";
    if (reasoningCount > 0 && toolCount > 0 && editedFileCount === 0) {
        title = commandCount > 0 && commandCount === toolCount
            ? t("src.components.chat.messagebubble.trace_thought_and_ran_commands", { count: commandCount })
            : t("src.components.chat.messagebubble.trace_thought_and_called_tools", { count: toolCount });
    } else {
        const parts: string[] = [];
        if (editedFileCount > 0) {
            parts.push(t("src.components.chat.messagebubble.trace_edited_files", { count: editedFileCount }));
        }
        if (toolCount > 0) {
            parts.push(commandCount > 0 && commandCount === toolCount
                ? t("src.components.chat.messagebubble.trace_ran_commands", { count: commandCount })
                : t("src.components.chat.messagebubble.trace_called_tools", { count: toolCount }));
        }
        if (parts.length > 0) {
            title = parts.join("  ");
        } else if (reasoningCount > 0) {
            title = t("src.components.chat.messagebubble.trace_thought_complete");
        } else if (runtimeProgressCount > 0) {
            title = t("src.components.chat.messagebubble.trace_runtime_updated");
        } else {
            title = t("src.components.chat.messagebubble.trace_activity_updated");
        }
    }

    const iconName = editedFileCount > 0
        ? "file-document-edit-outline"
        : commandCount > 0
            ? "console-line"
            : toolCount > 0
                ? "tools"
                : "orbit";

    return {
        title,
        iconName,
        previewLines,
    };
}

function TraceGroup({
    id,
    nodes,
    collapsedByDefault,
    messageIdentity,
    assistantActive,
    streamPhase,
    speakingKey,
    onSpeakVoice,
    processes,
    resultNodesByToolCallId,
    borderColor,
    backgroundColor,
    titleColor,
    textColor,
    expanded,
    onToggle,
    label,
    t,
    fallbackTitle,
    fallbackDescription,
}: {
    id: string;
    nodes: PhoneUiTimelineNode[];
    collapsedByDefault: boolean;
    messageIdentity: string;
    assistantActive: boolean;
    streamPhase?: ChatMessage["uiStreamPhase"];
    speakingKey?: string;
    onSpeakVoice?: (text: string, messageKey: string) => void;
    processes: AdminProcessRef[];
    resultNodesByToolCallId: Map<string, PhoneUiExecutionNode>;
    borderColor: string;
    backgroundColor: string;
    titleColor: string;
    textColor: string;
    expanded?: boolean;
    onToggle: () => void;
    label: string;
    t: Translate;
    fallbackTitle: string;
    fallbackDescription: string;
}) {
    const isExpanded = expanded ?? !collapsedByDefault;
    const summary = buildTraceGroupSummary(nodes, resultNodesByToolCallId, t);

    return (
        <View style={styles.traceGroup}>
            <SupervisorActivitySummary
                title={summary.title}
                iconName={summary.iconName}
                expanded={isExpanded}
                previewLines={summary.previewLines}
                accessibilityLabel={label}
                onPress={onToggle}
            />
            {isExpanded ? (
                <View style={styles.traceGroupContent}>
                    {nodes.map((node, index) => (
                        <NodeRenderBoundary
                            key={node.id || `${id}:trace-node:${index}`}
                            title={fallbackTitle}
                            description={fallbackDescription}
                            borderColor={borderColor}
                            backgroundColor={backgroundColor}
                            titleColor={titleColor}
                            textColor={textColor}
                        >
                            <ContentDispatcher
                                node={node}
                                messageIdentity={`${messageIdentity}:trace:${id}:${index}`}
                                isExecuting={assistantActive}
                                isStreaming={streamPhase === "streaming" || streamPhase === "agent_started"}
                                speakingKey={speakingKey}
                                onSpeakVoice={onSpeakVoice}
                                processes={processes}
                                resultNode={hasToolCallId(node) && node.executionType === "tool_call"
                                    ? resultNodesByToolCallId.get(node.toolCallId.trim())
                                    : undefined}
                            />
                        </NodeRenderBoundary>
                    ))}
                </View>
            ) : null}
        </View>
    );
}

function AssistantActivityDots({
    active,
    primaryColor,
}: {
    active: boolean;
    primaryColor: string;
}) {
    const dotA = useSharedValue(0);
    const dotB = useSharedValue(0);
    const dotC = useSharedValue(0);

    useEffect(() => {
        const start = (dot: typeof dotA, delayMs: number) => {
            dot.value = withDelay(
                delayMs,
                withRepeat(
                    withSequence(
                        withTiming(1, { duration: 260, easing: Easing.out(Easing.cubic) }),
                        withTiming(0, { duration: 260, easing: Easing.in(Easing.cubic) }),
                        withTiming(0, { duration: 360 }),
                    ),
                    -1,
                    false,
                ),
            );
        };

        if (!active) {
            cancelAnimation(dotA);
            cancelAnimation(dotB);
            cancelAnimation(dotC);
            dotA.value = withTiming(0, { duration: 120 });
            dotB.value = withTiming(0, { duration: 120 });
            dotC.value = withTiming(0, { duration: 120 });
            return;
        }

        start(dotA, 0);
        start(dotB, 140);
        start(dotC, 280);

        return () => {
            cancelAnimation(dotA);
            cancelAnimation(dotB);
            cancelAnimation(dotC);
        };
    }, [active, dotA, dotB, dotC]);

    const dotAStyle = useAnimatedStyle(() => ({
        opacity: 0.38 + (dotA.value * 0.56),
        transform: [{ translateY: -dotA.value * 4 }, { scale: 0.92 + (dotA.value * 0.14) }],
    }));
    const dotBStyle = useAnimatedStyle(() => ({
        opacity: 0.38 + (dotB.value * 0.56),
        transform: [{ translateY: -dotB.value * 4 }, { scale: 0.92 + (dotB.value * 0.14) }],
    }));
    const dotCStyle = useAnimatedStyle(() => ({
        opacity: 0.38 + (dotC.value * 0.56),
        transform: [{ translateY: -dotC.value * 4 }, { scale: 0.92 + (dotC.value * 0.14) }],
    }));

    return (
        <View style={styles.activityDotsWrap} accessibilityLabel="assistant active">
            <Animated.View style={[styles.activityDot, { backgroundColor: primaryColor }, dotAStyle]} />
            <Animated.View style={[styles.activityDot, { backgroundColor: primaryColor }, dotBStyle]} />
            <Animated.View style={[styles.activityDot, { backgroundColor: primaryColor }, dotCStyle]} />
        </View>
    );
}

function extractCommandPresetName(message: ChatMessage) {
    const metadata = message.metadata?.commandPreset;
    if (!metadata || typeof metadata !== "object") return "";
    const name = (metadata as { name?: string }).name;
    return typeof name === "string" ? name.trim() : "";
}

function escapeRegExp(value: string) {
    return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function stripCommandPresetPrefix(content: string, commandName: string) {
    const trimmed = String(content || "").trim();
    const normalizedCommand = String(commandName || "").trim();
    if (!trimmed || !normalizedCommand) {
        return trimmed;
    }
    const commandPattern = escapeRegExp(normalizedCommand.replace(/^\/+/, ""));
    return trimmed
        .replace(new RegExp(`^\\s*/\\s*${commandPattern}(?:\\s+|\\r?\\n|$)`, "i"), "")
        .trim();
}

function isComposerSpecMode(message: ChatMessage) {
    const metadata = message.metadata && typeof message.metadata === "object"
        ? message.metadata as Record<string, unknown>
        : {};
    if (metadata.taskPlanningMode !== true && metadata.specMode !== true) {
        return false;
    }
    return metadata.taskPlanningSource === "composer"
        || metadata.taskPlanningModeSource === "composer"
        || metadata.taskPlanningRequestedByComposer === true;
}

function extractSkillReferences(message: ChatMessage): SkillReferenceSummary[] {
    const raw = message.metadata?.skillReferences;
    if (!Array.isArray(raw)) return [];
    return raw
        .filter((item): item is SkillReferenceSummary => Boolean(item) && typeof item === "object")
        .map((item) => ({
            name: String(item.name || "").trim(),
            description: String(item.description || "").trim(),
            path: String(item.path || "").trim(),
        }))
        .filter((item) => item.name || item.path);
}

type UserMentionReference = {
    key: string;
    kind: "subagent_family" | "plugin";
    label: string;
};

function extractUserMentionReferences(message: ChatMessage): UserMentionReference[] {
    const normalized: UserMentionReference[] = [];
    const seen = new Set<string>();
    const contextMentions = message.metadata?.contextMentions;
    if (Array.isArray(contextMentions)) {
        for (const item of contextMentions) {
            if (!item || item.kind !== "subagent_family") continue;
            const id = String(item.familyId || item.id || "").trim();
            const label = String(item.label || item.name || id).trim();
            if (!label) continue;
            const key = `subagent_family:${id || label.toLowerCase()}`;
            if (seen.has(key)) continue;
            seen.add(key);
            normalized.push({ key, kind: "subagent_family", label });
        }
    }
    const pluginReferences = message.metadata?.pluginReferences;
    if (Array.isArray(pluginReferences)) {
        for (const item of pluginReferences) {
            if (!item || typeof item !== "object") continue;
            const record = item as unknown as Record<string, unknown>;
            const id = String(record.pluginId || "").trim();
            const label = String(record.displayName || record.name || id).trim();
            if (!label) continue;
            const key = `plugin:${id || label.toLowerCase()}`;
            if (seen.has(key)) continue;
            seen.add(key);
            normalized.push({ key, kind: "plugin", label });
        }
    }
    return normalized;
}

function extractContextSessionRefs(message: ChatMessage): string[] {
    const raw = message.metadata?.contextSessionRefs;
    if (!Array.isArray(raw)) return [];
    return Array.from(new Set(
        raw
            .filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
            .map((item) => typeof item.sessionId === "string" ? item.sessionId.trim() : "")
            .filter(Boolean),
    )).slice(0, 3);
}

function extractComposerPresentation(message: ChatMessage): ComposerPresentation | null {
    const raw = message.metadata?.composerPresentation;
    if (!raw || typeof raw !== "object") return null;
    const text = typeof raw.text === "string" ? raw.text : "";
    const references = Array.isArray(raw.references)
        ? raw.references.filter((reference) => Boolean(
            reference
            && typeof reference.id === "string"
            && typeof reference.label === "string"
            && ["command", "skill", "subagent_family", "plugin"].includes(reference.kind),
        ))
        : [];
    return text && references.length > 0 ? { text, references } : null;
}

type UserAttachmentItem = {
    key: string;
    name: string;
    url: string;
    mimeType: string;
    size?: number;
    kind: "image" | "video" | "audio" | "file";
};

function attachmentKind(name: string, mimeType: string, mediaKind = ""): UserAttachmentItem["kind"] {
    const normalizedName = (() => {
        try {
            return decodeURIComponent(String(name || "")).toLowerCase();
        } catch {
            return String(name || "").toLowerCase();
        }
    })();
    const normalizedType = String(mimeType || "").toLowerCase();
    const normalizedKind = String(mediaKind || "").toLowerCase();
    if (normalizedKind === "image" || normalizedType.startsWith("image/")) {
        return "image";
    }
    if (normalizedKind === "video" || normalizedType.startsWith("video/")) {
        return "video";
    }
    if (normalizedKind === "audio" || normalizedType.startsWith("audio/")) {
        return "audio";
    }
    if (/\.(png|jpe?g|webp|gif|bmp|heic|heif)(?:[?#\s].*)?$/i.test(normalizedName)) return "image";
    if (/\.(mp4|mov|m4v|webm|mkv|avi)(?:[?#\s].*)?$/i.test(normalizedName)) return "video";
    if (/\.(mp3|m4a|wav|ogg|opus|aac|flac)(?:[?#\s].*)?$/i.test(normalizedName)) return "audio";
    return "file";
}

function fileExtensionLabel(name: string, mimeType: string) {
    const ext = String(name || "").split(".").pop()?.trim();
    if (ext && ext !== name) {
        return ext.slice(0, 5).toUpperCase();
    }
    const subtype = String(mimeType || "").split("/").pop()?.trim();
    return subtype ? subtype.slice(0, 5).toUpperCase() : "FILE";
}

function formatAttachmentSize(value: unknown) {
    const size = typeof value === "number" ? value : Number(value);
    if (!Number.isFinite(size) || size <= 0) {
        return "";
    }
    if (size < 1024) {
        return `${Math.round(size)} B`;
    }
    if (size < 1024 * 1024) {
        return `${Math.round(size / 1024)} KB`;
    }
    return `${(size / 1024 / 1024).toFixed(size < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

function extractUserAttachments(message: ChatMessage, adminBaseUrl: string): UserAttachmentItem[] {
    const metadata = message.metadata && typeof message.metadata === "object"
        ? message.metadata as Record<string, unknown>
        : {};
    const rawAttachments = Array.isArray(metadata.attachments)
        ? metadata.attachments.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
        : [];

    if (rawAttachments.length > 0) {
        return rawAttachments
            .map((item, index) => {
                const resourceRef = item.resourceRef && typeof item.resourceRef === "object"
                    ? item.resourceRef as Record<string, unknown>
                    : null;
                const previewCandidate = String(
                    item.signedUrl
                    || resourceRef?.signedUrl
                    || item.previewUrl
                    || item.externalUrl
                    || item.publicUrl
                    || item.url
                    || "",
                ).trim();
                const displayPath = String(item.workspacePath || item.path || item.sourcePath || item.source_path || "").trim();
                const rawNameCandidate = previewCandidate || displayPath;
                const name = String(item.name || item.filename || rawNameCandidate.split(/[\\/]/).filter(Boolean).pop() || `file-${index + 1}`).trim();
                const mimeType = String(item.mimeType || item.type || "").trim();
                const url = previewCandidate
                    ? (
                        resolveRenderableMediaUrl(adminBaseUrl, {
                            value: previewCandidate,
                            resourceRef: resourceRef as any,
                            previewUrl: String(item.previewUrl || resourceRef?.previewUrl || "").trim() || undefined,
                            externalUrl: String(item.externalUrl || resourceRef?.externalUrl || "").trim() || undefined,
                            workspacePath: displayPath || undefined,
                            sourcePath: String(item.sourcePath || item.source_path || "").trim() || undefined,
                        })
                        || resolveAdminAssetUrl(adminBaseUrl, previewCandidate)
                    )
                    : "";
                return {
                    key: String(item.id || item.localId || previewCandidate || displayPath || `${name}:${index}`),
                    name,
                    url,
                    mimeType,
                    size: typeof item.size === "number" ? item.size : Number(item.size) || undefined,
                    kind: attachmentKind(
                        `${name} ${previewCandidate} ${displayPath}`,
                        mimeType,
                        String(item.mediaKind || item.previewKind || item.kind || ""),
                    ),
                };
            });
    }

    return (Array.isArray(message.images) ? message.images : [])
        .map((item, index) => {
            const rawUrl = String(item || "").trim();
            const url = rawUrl ? (resolveRenderableMediaUrl(adminBaseUrl, rawUrl) || resolveAdminAssetUrl(adminBaseUrl, rawUrl)) : "";
            return {
                key: rawUrl || `image-${index}`,
                name: rawUrl.split(/[\\/]/).filter(Boolean).pop() || `attachment-${index + 1}`,
                url,
                mimeType: "image/*",
                kind: "image" as const,
            };
        })
        .filter((item) => item.url);
}

function readIdentityField(message: ChatMessage, keys: string[]) {
    const metadata = message.metadata && typeof message.metadata === "object"
        ? message.metadata as Record<string, unknown>
        : null;
    const metadataAgent = metadata?.agent && typeof metadata.agent === "object"
        ? metadata.agent as Record<string, unknown>
        : null;
    const parts = Array.isArray((message as unknown as Record<string, unknown>).parts)
        ? ((message as unknown as Record<string, unknown>).parts as Array<Record<string, unknown>>)
        : [];
    const sources: Array<Record<string, unknown>> = [
        message as unknown as Record<string, unknown>,
        ...(metadata ? [metadata] : []),
        ...(metadataAgent ? [metadataAgent] : []),
        ...parts.slice().reverse().filter((part) => Boolean(part) && typeof part === "object"),
    ];

    for (const key of keys) {
        for (const source of sources) {
            const candidate = source?.[key];
            if (typeof candidate === "string" && candidate.trim()) {
                return candidate.trim();
            }
        }
    }
    return "";
}

const EMPTY_RUNTIME_ACTIVITIES: PhoneRuntimeStageActivity[] = [];

export const MessageBubble = memo(function MessageBubble({
    adminBaseUrl,
    message,
    isLast = false,
    isLoading = false,
    streamPhase,
    onDelete,
    onSpeakVoice,
    speakingKey = "",
    userImageUri,
    userDisplayName,
    processes = [],
    runtimeActivities = EMPTY_RUNTIME_ACTIVITIES,
    executionActive = false,
    onOpenOverview,
}: {
    adminBaseUrl: string;
    message: ChatMessage;
    isLast?: boolean;
    isLoading?: boolean;
    streamPhase?: ChatMessage["uiStreamPhase"];
    onDelete?: (message: ChatMessage) => void;
    onSpeakVoice?: (text: string, messageKey: string) => void;
    speakingKey?: string;
    userImageUri?: string;
    userDisplayName?: string;
    processes?: AdminProcessRef[];
    runtimeActivities?: PhoneRuntimeStageActivity[];
    executionActive?: boolean;
    onOpenOverview?: () => void;
}) {
    const { width, height } = useWindowDimensions();
    const { colors: palette, t, themeMode, locale } = useUiPrefs();
    const isLandscape = width > height;
    const isUser = message.role === "user";
    const [copied, setCopied] = useState(false);
    const [userMediaOpen, setUserMediaOpen] = useState(false);
    const [userMediaIndex, setUserMediaIndex] = useState(0);
    const [expandedTraceGroups, setExpandedTraceGroups] = useState<Record<string, boolean>>({});
    const avatarUri = useMemo(
        () => resolveAdminAssetUrl(
            adminBaseUrl,
            readIdentityField(message, [
                "agentAvatar",
                "agent_avatar",
                "avatar",
                "avatarUrl",
                "avatar_url",
                "profileImage",
                "profile_image",
                "profileImageUrl",
                "profile_image_url",
                "image",
                "imageUrl",
                "image_url",
            ]) || "",
        ),
        [adminBaseUrl, message],
    );
    const commandPresetName = useMemo(() => extractCommandPresetName(message), [message]);
    const skillReferences = useMemo(() => extractSkillReferences(message), [message]);
    const mentionReferences = useMemo(() => extractUserMentionReferences(message), [message]);
    const contextSessionRefs = useMemo(() => extractContextSessionRefs(message), [message]);
    const composerPresentation = useMemo(() => extractComposerPresentation(message), [message]);
    const composerPresentationSegments = useMemo(
        () => composerPresentation
            ? buildComposerInlineSegments(composerPresentation.text, composerPresentation.references)
            : [],
        [composerPresentation],
    );
    const userAttachments = useMemo(
        () => extractUserAttachments(message, adminBaseUrl),
        [adminBaseUrl, message],
    );
    const userMediaAttachments = useMemo(
        () => userAttachments.filter((item) => (item.kind === "image" || item.kind === "video") && item.url),
        [userAttachments],
    );
    const userAudioAttachments = useMemo(
        () => userAttachments.filter((item) => item.kind === "audio" && item.url),
        [userAttachments],
    );
    const userFileAttachments = useMemo(
        () => userAttachments.filter((item) => item.kind === "file" || !item.url),
        [userAttachments],
    );
    const userMediaItems = useMemo<MediaItem[]>(
        () => userMediaAttachments.map((item) => ({
            type: item.kind === "video" ? "video" : "image",
            src: item.url,
            name: item.name,
        })),
        [userMediaAttachments],
    );
    const composerSpecMode = isComposerSpecMode(message);
    const userContentText = useMemo(
        () => stripCommandPresetPrefix(String(message.content || "").trim(), commandPresetName),
        [commandPresetName, message.content],
    );
    const renderMessageKey = String(message.renderKey || message.id || "").trim();
    const messageIdentity = useMemo(
        () => String(message.runId || renderMessageKey || message.id || `${message.role}:${message.timestamp || 0}`).trim(),
        [message.id, message.role, message.runId, message.timestamp, renderMessageKey],
    );
    const resolvedUserAvatar = useMemo(
        () => resolveAdminAssetUrl(
            adminBaseUrl,
            userImageUri
            || readIdentityField(message, ["userAvatar", "user_avatar", "profileImage", "profile_image", "image", "imageUrl", "image_url"])
            || "",
        ),
        [adminBaseUrl, message, userImageUri],
    );
    const resolvedAgentName = readIdentityField(message, [
        "agentName",
        "agent_name",
        "displayName",
        "display_name",
        "nickname",
        "nickName",
        "userName",
        "user_name",
        "username",
        "login",
        "name",
    ])
        || t("src.components.chat.messagebubble.supervisor");
    const isSupervisorLike = message.agentType === "supervisor" || message.role === "assistant";
    const resolvedAgentRoleLabel = readIdentityField(message, [
        "agentRoleLabel",
        "agent_role_label",
        "roleLabel",
        "role_label",
        "identityLabel",
        "identity_label",
        "agentTitle",
        "agent_title",
    ]) || (isSupervisorLike ? t("src.components.chat.messagebubble.lead") : "");
    const rolePillBackground = message.agentType === "supervisor" ? "#FFF7ED" : palette.accentSoft;
    const rolePillBorder = message.agentType === "supervisor" ? "rgba(245,158,11,0.24)" : `${palette.accent}33`;
    const rolePillTextColor = message.agentType === "supervisor" ? "#D97706" : palette.accent;
    const toolExecutionView = useMemo(
        () => buildPhoneToolExecutionView(message.nodes),
        [message.nodes],
    );
    const rawRenderableNodes = toolExecutionView.renderableNodes;
    const resultNodesByToolCallId = toolExecutionView.resultNodesByToolCallId;
    const rawHasStructuredNodes = rawRenderableNodes.length > 0;
    const rawFallbackBlocks = useMemo(
        () => (rawHasStructuredNodes ? [] : parsePhoneContentBlocks(String(message.content || ""))),
        [rawHasStructuredNodes, message.content],
    );
    const horizontalBubbleLimit = Math.max(196, width - (isLandscape ? 176 : 76));
    const sharedTextBubbleWidth = Math.max(
        176,
        Math.min(
            horizontalBubbleLimit,
            isLandscape ? 412 : 353,
        ),
    );
    const assistantBubbleBackground = themeMode === "dark" ? "rgba(24,24,27,0.72)" : palette.surfaceStrong;
    const assistantBubbleBorder = themeMode === "dark" ? "rgba(255,255,255,0.08)" : palette.border;
    const assistantActionSurface = themeMode === "dark" ? "rgba(255,255,255,0.05)" : "rgba(255,255,255,0.74)";
    const assistantActive = !isUser && isLast && isLoading;
    const hasSupervisorVisibleActivity = useMemo(() => {
        if (rawHasStructuredNodes) {
            return rawRenderableNodes.some(isSupervisorVisibleActivityNode);
        }
        return rawFallbackBlocks.some((block) => block.type !== "voice" && block.content.trim().length > 0);
    }, [rawFallbackBlocks, rawHasStructuredNodes, rawRenderableNodes]);
    const messageBoundExecutionNodes = useMemo(
        () => buildMessageBoundExecutionNodes([message as unknown as MessageBoundExecutionMessage]),
        [message],
    );
    const messageBoundMicroStagePlacement = useMemo(
        () => buildMessageBoundCollaborationMicroStagePlacement(
            messageBoundExecutionNodes,
            {
                runId: message.runId,
                locale,
                limit: 10,
                maxStepsPerStage: 4,
            },
        ),
        [locale, message.runId, messageBoundExecutionNodes],
    );
    const liveFallbackMicroStages = useMemo(
        () => buildCollaborationMicroStages(
            hasSupervisorVisibleActivity && !isUser && isLast
                ? runtimeActivities
                    .filter(isPhoneMicroStageActivity)
                    .slice(0, MICRO_STAGE_ACTIVITY_LIMIT)
                    .map(toMicroStageActivityInput)
                : [],
            {
                runId: message.runId,
                locale,
                limit: 10,
                maxStepsPerStage: 4,
            },
        ),
        [hasSupervisorVisibleActivity, isLast, isUser, locale, message.runId, runtimeActivities],
    );
    const visibleBubbleMicroStages = messageBoundMicroStagePlacement?.stages.length
        ? messageBoundMicroStagePlacement.stages
        : liveFallbackMicroStages;
    const microStageSceneKey = `collaboration-stage:${message.runId || visibleBubbleMicroStages[0]?.id || messageIdentity}`;
    const microStageAnchorIndex = useMemo(
        () => findMicroStageAnchorIndex(
            rawRenderableNodes,
            messageBoundMicroStagePlacement
                ? [
                    messageBoundMicroStagePlacement.anchorNodeId,
                    ...messageBoundMicroStagePlacement.sourceNodeIds,
                ]
                : [],
        ),
        [messageBoundMicroStagePlacement, rawRenderableNodes],
    );
    const microStageSupervisorSpeech = useMemo(
        () => extractSupervisorMicroStageSpeech(rawRenderableNodes, microStageAnchorIndex),
        [microStageAnchorIndex, rawRenderableNodes],
    );
    const microStageVisible = visibleBubbleMicroStages.length > 0;
    const renderableNodes = useMemo(
        () => rawRenderableNodes.filter((node) => !isMicroStageSupersededTimelineNode(node, microStageVisible)),
        [microStageVisible, rawRenderableNodes],
    );
    const hasStructuredNodes = renderableNodes.length > 0 || microStageVisible;
    const fallbackBlocks = useMemo(
        () => (hasStructuredNodes ? [] : parsePhoneContentBlocks(String(message.content || ""))),
        [hasStructuredNodes, message.content],
    );
    const timelineSegments = useMemo(() => {
        const segments: PhoneTimelineRenderSegment[] = [];
        let chunk: PhoneUiTimelineNode[] = [];
        let chunkIndex = 0;
        let stageInserted = false;
        const flushChunk = () => {
            if (chunk.length === 0) return;
            const grouped = buildMessageTimelineSegments(chunk, { active: assistantActive });
            segments.push(...grouped.map((segment) => ({
                ...segment,
                id: `chunk-${chunkIndex}:${segment.id}`,
            })));
            chunk = [];
            chunkIndex += 1;
        };

        rawRenderableNodes.forEach((node, index) => {
            if (microStageVisible && !stageInserted && index === microStageAnchorIndex) {
                flushChunk();
                segments.push({
                    kind: "collaboration_stage",
                    id: messageBoundMicroStagePlacement?.id || `collaboration-stage:${messageIdentity}`,
                });
                stageInserted = true;
            }
            if (isMicroStageSupersededTimelineNode(node, microStageVisible)) {
                return;
            }
            chunk.push(node);
        });
        flushChunk();

        if (microStageVisible && !stageInserted) {
            segments.push({
                kind: "collaboration_stage",
                id: messageBoundMicroStagePlacement?.id || `collaboration-stage:${messageIdentity}`,
            });
        }
        return segments;
    }, [
        assistantActive,
        messageBoundMicroStagePlacement?.id,
        messageIdentity,
        microStageAnchorIndex,
        microStageVisible,
        rawRenderableNodes,
    ]);
    const assistantEmptyActive = assistantActive && !hasStructuredNodes && fallbackBlocks.length === 0;
    const voiceDescriptors = useMemo(() => {
        const descriptors: Array<{ key: string; text: string }> = [];
        if (hasStructuredNodes) {
            renderableNodes.forEach((node, nodeIndex) => {
                if (node.kind !== "narrative") {
                    return;
                }
                parsePhoneContentBlocks(String(node.content || ""), false, 0, false).forEach((block, blockIndex) => {
                    if (block.type !== "voice" || !block.content.trim()) {
                        return;
                    }
                    descriptors.push({
                        key: buildVoicePlaybackKey(messageIdentity, `${nodeIndex}:${blockIndex}`, block.content),
                        text: block.content,
                    });
                });
            });
            return descriptors;
        }
        fallbackBlocks.forEach((block, blockIndex) => {
            if (block.type !== "voice" || !block.content.trim()) {
                return;
            }
            descriptors.push({
                key: buildVoicePlaybackKey(messageIdentity, String(blockIndex), block.content),
                text: block.content,
            });
        });
        return descriptors;
    }, [fallbackBlocks, hasStructuredNodes, messageIdentity, renderableNodes]);
    const hasRenderableText = useMemo(() => {
        if (hasStructuredNodes) {
            return renderableNodes.some((node) => {
                if (node.kind === "narrative") {
                    return parsePhoneContentBlocks(String(node.content || ""), false, 0, false).some((block) => block.type !== "voice" && block.content.trim());
                }
                return true;
            });
        }
        return fallbackBlocks.some((block) => block.type !== "voice" && block.content.trim());
    }, [fallbackBlocks, hasStructuredNodes, renderableNodes]);
    const hasAssistantNarrativeText = useMemo(() => {
        if (hasStructuredNodes) {
            return renderableNodes.some((node) => (
                node.kind === "narrative"
                && parsePhoneContentBlocks(String(node.content || ""), false, 0, false).some((block) => block.type !== "voice" && block.content.trim())
            ));
        }
        return fallbackBlocks.some((block) => block.type !== "voice" && block.content.trim());
    }, [fallbackBlocks, hasStructuredNodes, renderableNodes]);
    const showInlineActivityDots = assistantActive && hasStructuredNodes && !hasAssistantNarrativeText;
    const voiceOnly = !isUser && voiceDescriptors.length > 0 && !hasRenderableText;
    const taskProgress = message.metadata?.assistantTaskProgress && typeof message.metadata.assistantTaskProgress === "object"
        ? message.metadata.assistantTaskProgress as {
            phase?: string;
            label?: string;
            subtitle?: string;
            currentStep?: string;
            completedCount?: number;
            totalCount?: number;
        }
        : null;
    const assistantPhaseLabel = taskProgress?.label || (streamPhase === "placeholder"
        ? t("src.components.chat.messagebubble.thinking")
        : streamPhase === "task_planning"
            ? t("src.components.chat.messagebubble.advancing_task")
            : streamPhase === "tooling"
                ? t("src.components.chat.messagebubble.using_tools")
                : streamPhase === "artifact_ready"
                    ? t("src.components.chat.messagebubble.artifact_ready")
                    : streamPhase === "waiting_input"
                        ? t("src.components.chat.messagebubble.waiting_for_your_input")
        : streamPhase === "agent_started"
            ? t("src.components.chat.messagebubble.starting")
            : streamPhase === "streaming"
                ? t("src.components.chat.messagebubble.streaming")
                : streamPhase === "settling"
                    ? t("src.components.chat.messagebubble.finishing")
                    : streamPhase === "error"
                        ? t("src.components.chat.messagebubble.stream_error")
                        : "");
    const showAssistantPhasePill = Boolean(assistantPhaseLabel) && !assistantActive;

    const handleCopy = async () => {
        if (!copyValue) {
            return;
        }
        await Clipboard.setStringAsync(copyValue);
        setCopied(true);
    };

    const handleOpenMicroStageDetailRef = async (target: CollaborationMicroStageDetailTarget) => {
        await Clipboard.setStringAsync(target.detailRef);
        setCopied(true);
    };

    const assistantBubbleWidth = assistantEmptyActive
        ? Math.min(horizontalBubbleLimit, isLandscape ? 286 : 264)
        : voiceOnly
        ? Math.min(width * 0.72, 300)
        : sharedTextBubbleWidth;
    const assistantMinWidth = assistantEmptyActive
        ? Math.min(horizontalBubbleLimit, isLandscape ? 260 : 238)
        : voiceOnly
        ? Math.min(width * 0.72, 300)
        : sharedTextBubbleWidth;
    const assistantColumnWidthStyle = assistantEmptyActive
        ? { width: assistantBubbleWidth, maxWidth: assistantBubbleWidth, minWidth: assistantMinWidth }
        : voiceOnly
        ? { maxWidth: assistantBubbleWidth, minWidth: assistantMinWidth }
        : { width: assistantBubbleWidth, maxWidth: assistantBubbleWidth, minWidth: assistantBubbleWidth };
    const copyValue = useMemo(() => {
        const directContent = isUser
            ? (composerPresentation?.text || userContentText)
            : String(message.content || "").trim();
        if (directContent) {
            return directContent;
        }
        if (renderableNodes.length > 0) {
            const nodeContent = renderableNodes
                .map((node) => {
                    if ("content" in node && typeof node.content === "string") {
                        return node.content.trim();
                    }
                    if ("topic" in node && typeof node.topic === "string") {
                        return node.topic.trim();
                    }
                    if ("question" in node && typeof node.question === "string") {
                        return node.question.trim();
                    }
                    return "";
                })
                .filter(Boolean)
                .join("\n\n")
                .trim();
            if (nodeContent) {
                return nodeContent;
            }
        }
        const metadataLines = [
            commandPresetName ? `/${commandPresetName}` : "",
            ...skillReferences.map((skill) => `@${skill.name || skill.path}`),
            ...mentionReferences.map((reference) => `@${reference.label}`),
            ...contextSessionRefs.map((sessionId) => `${t("shared.conversation.context_session_ref")}: ${sessionId}`),
            composerSpecMode ? "Spec" : "",
            ...userAttachments.map((attachment) => attachment.name),
        ].filter(Boolean);
        if (metadataLines.length > 0) {
            return metadataLines.join("\n");
        }
        return "";
    }, [commandPresetName, composerPresentation?.text, composerSpecMode, contextSessionRefs, isUser, mentionReferences, renderableNodes, skillReferences, t, userAttachments, userContentText]);

    useEffect(() => {
        if (!copied) {
            return undefined;
        }
        const timer = setTimeout(() => setCopied(false), 1600);
        return () => clearTimeout(timer);
    }, [copied]);

    const userColumnWidth = sharedTextBubbleWidth;
    const userBubbleWidth = sharedTextBubbleWidth;
    const hasUserVisualPayload = Boolean(
        composerPresentation
        || commandPresetName
        || skillReferences.length > 0
        || mentionReferences.length > 0
        || contextSessionRefs.length > 0
        || composerSpecMode
        || userAttachments.length > 0,
    );

    if (isUser) {
        return (
            <View style={styles.userRow}>
                <View style={styles.userRowInner}>
                    <View style={[styles.userColumn, { width: userColumnWidth, maxWidth: userColumnWidth }]}>
                        <Text style={[styles.userLabel, { color: palette.textMuted }]} numberOfLines={1}>
                            {userDisplayName || t("src.components.chat.messagebubble.you")}
                        </Text>
                        <View style={[styles.userBubbleShell, { width: userBubbleWidth, maxWidth: userBubbleWidth }]}>
                            <LinearGradient
                                colors={[palette.primary, palette.accent]}
                                start={{ x: 0, y: 0 }}
                                end={{ x: 1, y: 1 }}
                                style={[styles.userBubble, { shadowColor: palette.primaryDeep }]}
                            >
                                {userMediaAttachments.length > 0 ? (
                                    <View style={styles.imageRow}>
                                        {userMediaAttachments.map((attachment, index) => (
                                            <Pressable
                                                key={attachment.key}
                                                onPress={() => {
                                                    setUserMediaIndex(index);
                                                    setUserMediaOpen(true);
                                                }}
                                            >
                                                <Image source={{ uri: attachment.url }} style={styles.inlineImage} />
                                            </Pressable>
                                        ))}
                                    </View>
                                ) : null}

                                {userAudioAttachments.length > 0 ? (
                                    <View style={styles.userAudioList}>
                                        {userAudioAttachments.map((attachment) => (
                                            <MediaPlayer
                                                key={attachment.key}
                                                src={attachment.url}
                                                type="audio"
                                                title={attachment.name || "Voice message"}
                                            />
                                        ))}
                                    </View>
                                ) : null}

                                {userFileAttachments.length > 0 ? (
                                    <View style={styles.userFileList}>
                                        {userFileAttachments.map((attachment) => (
                                            <View key={attachment.key} style={styles.userFileCard}>
                                                <View style={styles.userFileIcon}>
                                                    <Text style={styles.userFileExt} numberOfLines={1}>
                                                        {fileExtensionLabel(attachment.name, attachment.mimeType)}
                                                    </Text>
                                                </View>
                                                <View style={styles.userFileMeta}>
                                                    <Text style={styles.userFileName} numberOfLines={1}>
                                                        {attachment.name}
                                                    </Text>
                                                    <Text style={styles.userFileSub} numberOfLines={1}>
                                                        {formatAttachmentSize(attachment.size) || attachment.mimeType || t("src.components.chat.downloadfilecard.file")}
                                                    </Text>
                                                </View>
                                            </View>
                                        ))}
                                    </View>
                                ) : null}

                                {(composerPresentation
                                    || commandPresetName
                                    || skillReferences.length > 0
                                    || mentionReferences.length > 0
                                    || contextSessionRefs.length > 0
                                    || composerSpecMode
                                    || userContentText
                                    || !hasUserVisualPayload) ? (
                                    <View style={styles.userInlineContent}>
                                        {!composerPresentation && commandPresetName ? (
                                            <View style={styles.userChip}>
                                                <Text style={[styles.userChipPrefix, styles.userCommandText]}>/</Text>
                                                <Text style={[styles.userChipText, styles.userCommandText]}>{commandPresetName.replace(/^\/+/, "")}</Text>
                                            </View>
                                        ) : null}
                                        {!composerPresentation && skillReferences.map((skill) => (
                                            <View key={`${skill.name}:${skill.path || ""}`} style={styles.userChip}>
                                                <MaterialCommunityIcons name="at" size={14} color="#FDBA74" />
                                                <Text style={[styles.userChipText, styles.userMentionText]}>{skill.name}</Text>
                                            </View>
                                        ))}
                                        {!composerPresentation && mentionReferences.map((reference) => (
                                            <View key={reference.key} style={styles.userChip}>
                                                <MaterialCommunityIcons name={reference.kind === "plugin" ? "puzzle-outline" : "at"} size={14} color="#FDBA74" />
                                                <Text style={[styles.userChipText, styles.userMentionText]}>{reference.label}</Text>
                                            </View>
                                        ))}
                                        {contextSessionRefs.map((sessionId) => (
                                            <View key={sessionId} style={styles.userChip}>
                                                <MaterialCommunityIcons name="message-arrow-right-outline" size={14} color="#FFFFFF" />
                                                <Text style={styles.userChipText} numberOfLines={1}>
                                                    {t("shared.conversation.context_session_ref")} · {sessionId.slice(0, 10)}
                                                </Text>
                                            </View>
                                        ))}
                                        {composerSpecMode ? (
                                            <View style={styles.userChip}>
                                                <MaterialCommunityIcons name="file-document-edit-outline" size={14} color="#FFFFFF" />
                                                <Text style={styles.userChipText}>Spec</Text>
                                            </View>
                                        ) : null}
                                        {composerPresentation ? (
                                            <Text selectable style={styles.userPresentationText}>
                                                {composerPresentationSegments.map((segment, index) => (
                                                    <Text
                                                        key={`${segment.start}:${segment.end}:${index}`}
                                                        style={segment.type === "reference"
                                                            ? segment.reference?.kind === "command"
                                                                ? styles.userCommandText
                                                                : styles.userMentionText
                                                            : undefined}
                                                    >
                                                        {segment.text}
                                                    </Text>
                                                ))}
                                            </Text>
                                        ) : userContentText ? (
                                            <Text selectable style={[styles.userText, styles.userTextInline]}>{userContentText}</Text>
                                        ) : !hasUserVisualPayload ? (
                                            <Text selectable style={[styles.userText, styles.userTextInline]}>{t("src.components.chat.messagebubble.empty_message")}</Text>
                                        ) : null}
                                    </View>
                                ) : null}
                            </LinearGradient>
                        </View>

                        <View style={styles.footerRow}>
                            <Text style={[styles.timeLabel, styles.timeLabelUser, { color: palette.textSoft }]}>
                                {message.timestamp ? formatClock(message.timestamp, locale) : ""}
                            </Text>
                            <View style={styles.footerActions}>
                                {copyValue ? (
                                    <Pressable style={[styles.actionButtonGhost, { backgroundColor: assistantActionSurface, borderColor: palette.border }]} onPress={() => void handleCopy()}>
                                        <MaterialCommunityIcons name={copied ? "check" : "content-copy"} size={15} color={copied ? palette.success : palette.textSoft} />
                                    </Pressable>
                                ) : null}
                                {onDelete ? (
                                    <Pressable style={[styles.actionButtonGhost, { backgroundColor: assistantActionSurface, borderColor: palette.border }]} onPress={() => onDelete(message)}>
                                        <MaterialCommunityIcons name="trash-can-outline" size={15} color={palette.textSoft} />
                                    </Pressable>
                                ) : null}
                            </View>
                        </View>
                        <MediaViewerLightbox
                            items={userMediaItems}
                            initialIndex={userMediaIndex}
                            isOpen={userMediaOpen}
                            onClose={() => setUserMediaOpen(false)}
                        />
                    </View>

                    <View style={styles.userAvatarShell}>
                        {resolvedUserAvatar ? (
                            <Image source={{ uri: resolvedUserAvatar }} style={styles.avatar} />
                        ) : (
                            <Image source={BRAND_MARK} style={styles.avatar} />
                        )}
                    </View>
                </View>
            </View>
        );
    }

    if (assistantEmptyActive) {
        return (
            <View style={styles.assistantActivityRow}>
                <AssistantActivityDots active={assistantActive} primaryColor={palette.primary} />
            </View>
        );
    }

    return (
        <View style={styles.assistantRow}>
            <View style={[styles.avatarShell, assistantActive && styles.avatarShellActive]}>
                {avatarUri ? (
                    <Image source={{ uri: avatarUri }} style={styles.avatar} />
                ) : (
                    <Image source={BRAND_MARK} style={styles.avatar} />
                )}
                {assistantActive ? (
                    <View style={[styles.avatarStatusDot, { backgroundColor: palette.primary }]}>
                        <View style={[styles.avatarStatusDotInner, { backgroundColor: "#FFFFFF" }]} />
                    </View>
                ) : null}
            </View>

            <View style={[styles.assistantColumn, assistantColumnWidthStyle]}>
                <View style={styles.agentMeta}>
                    <Text style={[styles.agentName, { color: palette.textMuted }]} numberOfLines={1}>{resolvedAgentName}</Text>
                    {resolvedAgentRoleLabel ? (
                        <View style={[styles.rolePill, { backgroundColor: rolePillBackground, borderColor: rolePillBorder }]}>
                            <Text style={[styles.rolePillText, { color: rolePillTextColor }]}>{resolvedAgentRoleLabel}</Text>
                        </View>
                    ) : null}
                    {showAssistantPhasePill ? (
                        <View style={[styles.streamPill, { backgroundColor: `${palette.primary}14`, borderColor: `${palette.primary}2A` }]}>
                            <MaterialCommunityIcons
                                name={assistantActive ? "progress-clock" : "robot-excited-outline"}
                                size={14}
                                color={assistantActive ? palette.primary : palette.textSoft}
                            />
                            <Text style={[styles.streamPillText, { color: assistantActive ? palette.primary : palette.textSoft }]}>
                                {assistantPhaseLabel}
                            </Text>
                        </View>
                    ) : null}
                </View>

                <View style={[
                    styles.assistantBubbleShell,
                    voiceOnly && styles.assistantBubbleVoiceOnly,
                    assistantActive && { shadowColor: palette.primaryDeep, shadowOpacity: 0.14, elevation: 4 },
                    assistantEmptyActive && styles.assistantBubbleShellActiveEmpty,
                ]}>
                    <View
                        style={[
                            styles.assistantBubbleClip,
                            assistantEmptyActive && styles.assistantBubbleClipActiveEmpty,
                            {
                                backgroundColor: assistantEmptyActive ? `${palette.primary}08` : assistantBubbleBackground,
                                borderColor: assistantActive ? `${palette.primary}40` : assistantBubbleBorder,
                            },
                        ]}
                    >
                        {!assistantEmptyActive ? (
                            <View style={[styles.assistantBubbleSheen, { backgroundColor: assistantActive ? `${palette.primary}66` : `${palette.primary}40` }]} />
                        ) : null}
                        <View style={[styles.assistantInner, assistantEmptyActive && styles.assistantInnerActiveEmpty, voiceOnly && styles.assistantInnerVoiceOnly]}>
                            {hasStructuredNodes ? (
                                timelineSegments.map((segment, index) => {
                                    if (segment.kind === "collaboration_stage") {
                                        return (
                                            <View key={microStageSceneKey} style={styles.assistantExecutionMap}>
                                                <CollaborationMicroStageScene
                                                    stages={visibleBubbleMicroStages}
                                                    executionActive={executionActive}
                                                    palette={palette}
                                                    dark={themeMode === "dark"}
                                                    locale={locale}
                                                    supervisorSpeech={microStageSupervisorSpeech}
                                                    onOpenDetailRef={(target) => void handleOpenMicroStageDetailRef(target)}
                                                    overviewLinkLabel={t("src.components.chat.collaborationmicrostagescene.view_overview")}
                                                    onOpenOverview={onOpenOverview}
                                                />
                                            </View>
                                        );
                                    }
                                    if (segment.kind === "trace_group") {
                                        const expanded = expandedTraceGroups[segment.id];
                                        return (
                                            <TraceGroup
                                                key={segment.id}
                                                id={segment.id}
                                                nodes={segment.nodes as PhoneUiTimelineNode[]}
                                                collapsedByDefault={segment.collapsedByDefault}
                                                messageIdentity={messageIdentity}
                                                assistantActive={assistantActive}
                                                streamPhase={streamPhase}
                                                speakingKey={speakingKey}
                                                onSpeakVoice={onSpeakVoice}
                                                processes={processes}
                                                resultNodesByToolCallId={resultNodesByToolCallId}
                                                borderColor={palette.border}
                                                backgroundColor={palette.surface}
                                                titleColor={palette.text}
                                                textColor={palette.textMuted}
                                                expanded={expanded}
                                                onToggle={() => setExpandedTraceGroups((current) => ({
                                                    ...current,
                                                    [segment.id]: !(expanded ?? !segment.collapsedByDefault),
                                                }))}
                                                label={expanded ?? !segment.collapsedByDefault
                                                    ? t("src.components.chat.messagebubble.collapse_trace_group")
                                                    : t("src.components.chat.messagebubble.expand_trace_group")}
                                                t={t}
                                                fallbackTitle={t("src.components.chat.messagebubble.node_render_failed")}
                                                fallbackDescription={t("src.components.chat.messagebubble.this_node_has_been_downgraded_so_the_rest_of_the_reply_remains_visible")}
                                            />
                                        );
                                    }
                                    const node = segment.node as PhoneUiTimelineNode;
                                    return (
                                        <NodeRenderBoundary
                                            key={node.id || `${messageIdentity}:node:${index}`}
                                            title={t("src.components.chat.messagebubble.node_render_failed")}
                                            description={t("src.components.chat.messagebubble.this_node_has_been_downgraded_so_the_rest_of_the_reply_remains_visible")}
                                            borderColor={palette.border}
                                            backgroundColor={palette.surface}
                                            titleColor={palette.text}
                                            textColor={palette.textMuted}
                                        >
                                            <ContentDispatcher
                                                node={node}
                                                messageIdentity={`${messageIdentity}:node:${index}`}
                                                isExecuting={assistantActive}
                                                isStreaming={streamPhase === "streaming" || streamPhase === "agent_started"}
                                                speakingKey={speakingKey}
                                                onSpeakVoice={onSpeakVoice}
                                                processes={processes}
                                                resultNode={hasToolCallId(node) && node.executionType === "tool_call"
                                                    ? resultNodesByToolCallId.get(node.toolCallId.trim())
                                                    : undefined}
                                            />
                                        </NodeRenderBoundary>
                                    );
                                })
                            ) : fallbackBlocks.length > 0 ? (
                                fallbackBlocks.map((block, index) => {
                                    const voiceKey = block.type === "voice"
                                        ? buildVoicePlaybackKey(messageIdentity, String(index), block.content)
                                        : "";
                                    return (
                                        <NodeRenderBoundary
                                            key={block.id}
                                            title={t("src.components.chat.messagebubble.content_render_failed")}
                                            description={t("src.components.chat.messagebubble.this_content_block_has_been_downgraded_so_the_rest_of_the_reply_remains_visible")}
                                            borderColor={palette.border}
                                            backgroundColor={palette.surface}
                                            titleColor={palette.text}
                                            textColor={palette.textMuted}
                                        >
                                            <MessageBlockItem
                                                block={block}
                                                isStreaming={assistantActive && (streamPhase === "streaming" || streamPhase === "agent_started")}
                                                speaking={Boolean(voiceKey) && speakingKey === voiceKey}
                                                onSpeak={voiceKey && onSpeakVoice ? () => onSpeakVoice(block.content, voiceKey) : undefined}
                                            />
                                        </NodeRenderBoundary>
                                    );
                                })
                            ) : null}
                            {showInlineActivityDots ? (
                                <View style={styles.assistantInlineActivity}>
                                    <AssistantActivityDots active={assistantActive} primaryColor={palette.primary} />
                                </View>
                            ) : null}

                        </View>
                    </View>
                </View>

                <View style={styles.footerRow}>
                    <Text style={[styles.timeLabel, { color: palette.textSoft }]}>
                        {message.timestamp ? formatClock(message.timestamp, locale) : ""}
                    </Text>
                    <View style={styles.footerActions}>
                        {copyValue ? (
                            <Pressable style={[styles.actionButtonGhost, { backgroundColor: assistantActionSurface, borderColor: palette.border }]} onPress={() => void handleCopy()}>
                                <MaterialCommunityIcons name={copied ? "check" : "content-copy"} size={15} color={copied ? palette.success : palette.textSoft} />
                            </Pressable>
                        ) : null}
                        {onDelete ? (
                            <Pressable style={[styles.actionButtonGhost, { backgroundColor: assistantActionSurface, borderColor: palette.border }]} onPress={() => onDelete(message)}>
                                <MaterialCommunityIcons name="trash-can-outline" size={15} color={palette.textSoft} />
                            </Pressable>
                        ) : null}
                    </View>
                </View>
            </View>
        </View>
    );
});

const styles = StyleSheet.create({
    userRow: {
        marginBottom: spacing.lg,
        alignItems: "flex-end",
        minWidth: 0,
        width: "100%",
    },
    userRowInner: {
        flexDirection: "row",
        alignItems: "flex-start",
        justifyContent: "flex-end",
        gap: 8,
        minWidth: 0,
        maxWidth: "100%",
        width: "100%",
        overflow: "hidden",
    },
    userColumn: {
        minWidth: 0,
        flexShrink: 1,
        alignItems: "stretch",
        gap: 7,
        maxWidth: "100%",
        overflow: "hidden",
    },
    userAvatarShell: {
        width: 36,
        paddingTop: 2,
        alignItems: "flex-end",
    },
    userLabel: {
        fontSize: 13,
        lineHeight: 18,
        fontWeight: "700",
        letterSpacing: -0.1,
        paddingRight: 1,
        textAlign: "right",
    },
    userBubbleShell: {
        alignSelf: "flex-end",
        position: "relative",
        overflow: "visible",
    },
    userBubble: {
        borderRadius: 18,
        borderTopRightRadius: 5,
        paddingHorizontal: 17,
        paddingVertical: 15,
        overflow: "hidden",
        alignSelf: "flex-end",
        shadowOpacity: 0.16,
        shadowRadius: 16,
        shadowOffset: { width: 0, height: 8 },
        elevation: 3,
        maxWidth: "100%",
        width: "100%",
    },
    userInlineContent: {
        flexDirection: "row",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 8,
        maxWidth: "100%",
        width: "100%",
    },
    userChip: {
        flexDirection: "row",
        alignItems: "center",
        gap: 4,
        minHeight: 21,
        maxWidth: "100%",
    },
    userChipPrefix: {
        color: "#FFFFFF",
        fontSize: 14,
        fontWeight: "800",
        lineHeight: 21,
        includeFontPadding: false,
    },
    userChipText: {
        color: "#FFFFFF",
        fontSize: 14,
        fontWeight: "800",
        lineHeight: 21,
        includeFontPadding: false,
    },
    userCommandText: {
        color: "#E9D5FF",
        fontWeight: "800",
    },
    userMentionText: {
        color: "#FDBA74",
        fontWeight: "800",
    },
    userPresentationText: {
        color: "#FFFFFF",
        fontSize: 14,
        lineHeight: 21,
        width: "100%",
        includeFontPadding: false,
    },
    userText: {
        color: "#FFFFFF",
        fontSize: 14,
        lineHeight: 21,
        width: "100%",
        flexShrink: 1,
        minWidth: 0,
        maxWidth: "100%",
    },
    userTextInline: {
        width: "auto",
        flexGrow: 1,
        flexShrink: 1,
        flexBasis: 120,
        minWidth: 80,
    },
    footerRow: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 8,
        paddingHorizontal: 2,
        width: "100%",
        maxWidth: "100%",
    },
    assistantRow: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 8,
        marginBottom: spacing.lg,
        minWidth: 0,
    },
    assistantActivityRow: {
        alignSelf: "flex-start",
        marginBottom: spacing.lg,
        paddingLeft: 8,
        paddingVertical: 6,
    },
    avatarShell: {
        width: 36,
        paddingTop: 2,
        position: "relative",
    },
    avatarShellActive: {
        shadowOffset: { width: 0, height: 6 },
        shadowRadius: 14,
    },
    avatarStatusDot: {
        position: "absolute",
        right: -2,
        bottom: 1,
        width: 14,
        height: 14,
        borderRadius: 999,
        alignItems: "center",
        justifyContent: "center",
        borderWidth: 2,
        borderColor: "#FFFFFF",
    },
    avatarStatusDotInner: {
        width: 4,
        height: 4,
        borderRadius: 999,
    },
    avatar: {
        width: 36,
        height: 36,
        borderRadius: 18,
    },
    avatarFallback: {
        alignItems: "center",
        justifyContent: "center",
    },
    avatarFallbackText: {
        fontSize: 11,
        fontWeight: "900",
    },
    assistantColumn: {
        minWidth: 0,
        flexShrink: 1,
        gap: 7,
    },
    agentMeta: {
        flexDirection: "row",
        alignItems: "center",
        gap: 7,
        paddingLeft: 1,
        minHeight: 24,
        flexWrap: "wrap",
    },
    streamPill: {
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        borderRadius: radii.pill,
        borderWidth: 1,
        paddingHorizontal: 8,
        paddingVertical: 4,
    },
    streamPillText: {
        fontSize: 10,
        fontWeight: "800",
        letterSpacing: 0.2,
    },
    agentName: {
        fontSize: 13,
        lineHeight: 18,
        fontWeight: "700",
        letterSpacing: -0.1,
    },
    rolePill: {
        borderRadius: radii.pill,
        paddingHorizontal: 9,
        paddingVertical: 3,
        borderWidth: 1,
    },
    rolePillText: {
        fontSize: 9,
        fontWeight: "800",
        letterSpacing: 0.3,
    },
    assistantBubbleShell: {
        borderRadius: 18,
        borderTopLeftRadius: 5,
        shadowColor: "#0F172A",
        shadowOpacity: 0.08,
        shadowRadius: 20,
        shadowOffset: { width: 0, height: 10 },
        elevation: 2,
        overflow: "visible",
        width: "100%",
    },
    assistantBubbleShellActiveEmpty: {
        shadowOpacity: 0.05,
        shadowRadius: 14,
        shadowOffset: { width: 0, height: 7 },
    },
    assistantBubbleVoiceOnly: {
        minWidth: 260,
    },
    assistantBubbleClip: {
        overflow: "hidden",
        borderRadius: 18,
        borderTopLeftRadius: 5,
        borderWidth: 1,
        width: "100%",
    },
    assistantBubbleClipActiveEmpty: {
        borderRadius: 20,
        borderTopLeftRadius: 20,
    },
    assistantBubbleSheen: {
        height: 2,
    },
    assistantExecutionMap: {
        marginBottom: 2,
    },
    assistantInner: {
        paddingHorizontal: 12,
        paddingTop: 10,
        paddingBottom: 12,
        gap: 8,
        minWidth: 0,
        overflow: "visible",
        width: "100%",
    },
    assistantInnerActiveEmpty: {
        paddingHorizontal: 8,
        paddingVertical: 8,
    },
    assistantInnerVoiceOnly: {
        paddingTop: 18,
        paddingBottom: 20,
    },
    traceGroup: {
        width: "100%",
        gap: 3,
        alignItems: "flex-start",
    },
    traceToggle: {
        minWidth: 16,
        height: 14,
        borderRadius: 0,
        borderWidth: 0,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 0,
        opacity: 0.58,
        backgroundColor: "transparent",
    },
    traceToggleExpanded: {
        opacity: 0.76,
    },
    traceToggleIcon: {
        opacity: 0.82,
    },
    traceGroupContent: {
        width: "100%",
        gap: 3,
    },
    assistantInlineActivity: {
        alignSelf: "flex-start",
        paddingTop: 2,
    },
    assistantText: {
        fontSize: 14,
        lineHeight: 21,
    },
    activityDotsWrap: {
        minWidth: 44,
        minHeight: 22,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        gap: 6,
        borderRadius: radii.pill,
        backgroundColor: "transparent",
    },
    activityDot: {
        width: 6,
        height: 6,
        borderRadius: 999,
    },
    voiceCardWrap: {
        alignSelf: "stretch",
    },
    imageRow: {
        flexDirection: "row",
        flexWrap: "wrap",
        gap: 8,
        marginBottom: 10,
    },
    inlineImage: {
        width: 86,
        height: 86,
        borderRadius: 14,
    },
    userAudioList: {
        gap: 8,
        marginBottom: 10,
        alignSelf: "stretch",
    },
    userFileList: {
        gap: 8,
        marginBottom: 10,
    },
    userFileCard: {
        minHeight: 48,
        borderRadius: 14,
        backgroundColor: "rgba(255,255,255,0.16)",
        borderWidth: 1,
        borderColor: "rgba(255,255,255,0.24)",
        flexDirection: "row",
        alignItems: "center",
        gap: 9,
        paddingHorizontal: 10,
        paddingVertical: 8,
    },
    userFileIcon: {
        width: 32,
        height: 32,
        borderRadius: 10,
        backgroundColor: "rgba(255,255,255,0.18)",
        alignItems: "center",
        justifyContent: "center",
    },
    userFileExt: {
        color: "#FFFFFF",
        fontSize: 8,
        fontWeight: "900",
        letterSpacing: 0.2,
    },
    userFileMeta: {
        flex: 1,
        minWidth: 0,
        gap: 2,
    },
    userFileName: {
        color: "#FFFFFF",
        fontSize: 12,
        lineHeight: 16,
        fontWeight: "800",
    },
    userFileSub: {
        color: "rgba(255,255,255,0.78)",
        fontSize: 10,
        lineHeight: 13,
        fontWeight: "600",
    },
    footerActions: {
        flexDirection: "row",
        gap: 6,
        justifyContent: "flex-end",
        alignItems: "center",
    },
    actionButtonGhost: {
        width: 26,
        height: 26,
        borderRadius: 13,
        alignItems: "center",
        justifyContent: "center",
        borderWidth: 1,
    },
    timeLabel: {
        fontSize: 11,
        paddingHorizontal: 0,
        flexShrink: 1,
    },
    timeLabelUser: {
        textAlign: "left",
    },
});
