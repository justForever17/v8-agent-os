import { memo, useEffect, useMemo, useState } from "react";
import { Image, Pressable, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import * as Clipboard from "expo-clipboard";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import { buildMessageTimelineSegments, type AdminProcessRef } from "@v8/session-realtime";
import Animated, {
    Easing,
    cancelAnimation,
    useAnimatedStyle,
    useSharedValue,
    withRepeat,
    withTiming,
} from "react-native-reanimated";

import { resolveAdminAssetUrl } from "@/src/lib/admin-client";
import { buildPhoneToolExecutionView } from "@/src/lib/chat-node-visibility";
import { buildVoicePlaybackKey, parsePhoneContentBlocks } from "@/src/lib/content-detector";
import { formatClock } from "@/src/lib/time";
import { resolveRenderableMediaUrl } from "@/src/lib/workspace-links";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type { ChatMessage, PhoneUiExecutionNode, PhoneUiTimelineNode, SkillReferenceSummary } from "@/src/types/admin";
import { ContentDispatcher } from "@/src/components/chat/ContentDispatcher";
import { NodeRenderBoundary } from "@/src/components/chat/NodeRenderBoundary";
import { MessageBlockItem } from "@/src/components/chat/MessageBlockItem";
import { MediaViewerLightbox, type MediaItem } from "@/src/components/chat/MediaViewerLightbox";

const BRAND_MARK = require("../../../assets/images/brand-mark.png");

function isExecutionNode(node: PhoneUiTimelineNode): node is PhoneUiExecutionNode {
    return node.kind === "execution";
}

function hasToolCallId(node: PhoneUiTimelineNode): node is PhoneUiExecutionNode & { toolCallId: string } {
    return isExecutionNode(node) && typeof node.toolCallId === "string" && node.toolCallId.trim().length > 0;
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
    fallbackTitle: string;
    fallbackDescription: string;
}) {
    const isExpanded = expanded ?? !collapsedByDefault;

    return (
        <View style={styles.traceGroup}>
            <Pressable
                accessibilityRole="button"
                accessibilityLabel={label}
                style={[
                    styles.traceToggle,
                    isExpanded && styles.traceToggleExpanded,
                    { borderColor, backgroundColor },
                ]}
                onPress={onToggle}
            >
                <Text style={[styles.traceToggleText, { color: textColor }]}>
                    {isExpanded ? "⌄" : ">"}
                </Text>
            </Pressable>
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

function AssistantWorkIndicator({
    label,
    subtitle,
    active,
    primaryColor,
    textColor,
    mutedColor,
}: {
    label: string;
    subtitle?: string;
    active: boolean;
    primaryColor: string;
    textColor: string;
    mutedColor: string;
}) {
    const scan = useSharedValue(0);
    const pulse = useSharedValue(0);

    useEffect(() => {
        if (!active) {
            cancelAnimation(scan);
            cancelAnimation(pulse);
            scan.value = withTiming(0, { duration: 180 });
            pulse.value = withTiming(0, { duration: 180 });
            return;
        }

        scan.value = withRepeat(
            withTiming(1, { duration: 1500, easing: Easing.inOut(Easing.ease) }),
            -1,
            false,
        );
        pulse.value = withRepeat(
            withTiming(1, { duration: 900, easing: Easing.inOut(Easing.ease) }),
            -1,
            true,
        );

        return () => {
            cancelAnimation(scan);
            cancelAnimation(pulse);
        };
    }, [active, pulse, scan]);

    const scanStyle = useAnimatedStyle(() => ({
        opacity: active ? 0.28 : 0.12,
        transform: [{ translateX: -96 + (scan.value * 192) }],
    }));
    const pulseStyle = useAnimatedStyle(() => ({
        opacity: active ? 0.2 + (pulse.value * 0.42) : 0.28,
        transform: [{ scale: 0.92 + (pulse.value * 0.16) }],
    }));
    const dotStyle = useAnimatedStyle(() => ({
        opacity: active ? 0.48 + (pulse.value * 0.42) : 0.6,
        transform: [{ scale: 0.88 + (pulse.value * 0.2) }],
    }));

    return (
        <View style={[styles.workIndicator, { borderColor: `${primaryColor}24`, backgroundColor: `${primaryColor}0F` }]}>
            <Animated.View pointerEvents="none" style={[styles.workIndicatorScan, { backgroundColor: primaryColor }, scanStyle]} />
            <View style={styles.workIndicatorOrbWrap}>
                <Animated.View pointerEvents="none" style={[styles.workIndicatorPulse, { backgroundColor: primaryColor }, pulseStyle]} />
                <View style={[styles.workIndicatorOrb, { borderColor: `${primaryColor}3A`, backgroundColor: `${primaryColor}16` }]}>
                    <Animated.View style={[styles.workIndicatorDot, { backgroundColor: primaryColor }, dotStyle]} />
                </View>
            </View>
            <View style={styles.workIndicatorCopy}>
                <Text style={[styles.workIndicatorTitle, { color: textColor }]} numberOfLines={1}>
                    {label}
                </Text>
                {subtitle ? (
                    <Text style={[styles.workIndicatorSubtitle, { color: mutedColor }]} numberOfLines={1}>
                        {subtitle}
                    </Text>
                ) : (
                    <View style={styles.workIndicatorBars} pointerEvents="none">
                        <View style={[styles.workIndicatorBar, { backgroundColor: `${primaryColor}42`, width: 70 }]} />
                        <View style={[styles.workIndicatorBar, { backgroundColor: `${primaryColor}26`, width: 118 }]} />
                    </View>
                )}
            </View>
        </View>
    );
}

function extractCommandPresetName(message: ChatMessage) {
    const metadata = message.metadata?.commandPreset;
    if (!metadata || typeof metadata !== "object") return "";
    const name = (metadata as { name?: string }).name;
    return typeof name === "string" ? name.trim() : "";
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

type UserAttachmentItem = {
    key: string;
    name: string;
    url: string;
    mimeType: string;
    size?: number;
    kind: "image" | "video" | "file";
};

function attachmentKind(name: string, mimeType: string): UserAttachmentItem["kind"] {
    const normalizedName = String(name || "").toLowerCase();
    const normalizedType = String(mimeType || "").toLowerCase();
    if (normalizedType.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp|heic|heif)$/i.test(normalizedName)) {
        return "image";
    }
    if (normalizedType.startsWith("video/") || /\.(mp4|mov|m4v|webm|mkv|avi)$/i.test(normalizedName)) {
        return "video";
    }
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
                    kind: attachmentKind(name, mimeType),
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
    const userAttachments = useMemo(
        () => extractUserAttachments(message, adminBaseUrl),
        [adminBaseUrl, message],
    );
    const userMediaAttachments = useMemo(
        () => userAttachments.filter((item) => (item.kind === "image" || item.kind === "video") && item.url),
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
    const taskPlanningMode = Boolean(message.metadata?.taskPlanningMode);
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
    const renderableNodes = toolExecutionView.renderableNodes;
    const resultNodesByToolCallId = toolExecutionView.resultNodesByToolCallId;
    const hasStructuredNodes = renderableNodes.length > 0;
    const fallbackBlocks = useMemo(
        () => (hasStructuredNodes ? [] : parsePhoneContentBlocks(String(message.content || ""))),
        [hasStructuredNodes, message.content],
    );
    const voiceDescriptors = useMemo(() => {
        const descriptors: Array<{ key: string; text: string }> = [];
        if (hasStructuredNodes) {
            renderableNodes.forEach((node, nodeIndex) => {
                if (node.kind !== "narrative") {
                    return;
                }
                parsePhoneContentBlocks(String(node.content || "")).forEach((block, blockIndex) => {
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
                    return parsePhoneContentBlocks(String(node.content || "")).some((block) => block.type !== "voice" && block.content.trim());
                }
                return true;
            });
        }
        return fallbackBlocks.some((block) => block.type !== "voice" && block.content.trim());
    }, [fallbackBlocks, hasStructuredNodes, renderableNodes]);
    const voiceOnly = !isUser && voiceDescriptors.length > 0 && !hasRenderableText;
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
    const timelineSegments = useMemo(
        () => buildMessageTimelineSegments(renderableNodes, { active: assistantActive }),
        [assistantActive, renderableNodes],
    );
    const assistantEmptyActive = assistantActive && !hasStructuredNodes && fallbackBlocks.length === 0;
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
        const directContent = String(message.content || "").trim();
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
            taskPlanningMode ? t("src.components.chat.messagebubble.task_mode") : "",
            ...userAttachments.map((attachment) => attachment.name),
        ].filter(Boolean);
        if (metadataLines.length > 0) {
            return metadataLines.join("\n");
        }
        return "";
    }, [commandPresetName, message.content, renderableNodes, skillReferences, taskPlanningMode, t, userAttachments]);

    useEffect(() => {
        if (!copied) {
            return undefined;
        }
        const timer = setTimeout(() => setCopied(false), 1600);
        return () => clearTimeout(timer);
    }, [copied]);

    const userColumnWidth = sharedTextBubbleWidth;
    const userBubbleWidth = sharedTextBubbleWidth;
    const userContentText = String(message.content || "").trim();
    const hasUserVisualPayload = Boolean(
        commandPresetName
        || skillReferences.length > 0
        || taskPlanningMode
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
                                {(commandPresetName || skillReferences.length > 0 || taskPlanningMode) && (
                                    <View style={styles.userMetaRow}>
                                        {commandPresetName ? (
                                            <View style={styles.userChip}>
                                                <MaterialCommunityIcons name="slash-forward" size={12} color="#FFFFFF" />
                                                <Text style={styles.userChipText}>/{commandPresetName}</Text>
                                            </View>
                                        ) : null}
                                        {skillReferences.map((skill) => (
                                            <View key={`${skill.name}:${skill.path || ""}`} style={styles.userChip}>
                                                <MaterialCommunityIcons name="at" size={12} color="#FFFFFF" />
                                                <Text style={styles.userChipText}>@{skill.name}</Text>
                                            </View>
                                        ))}
                                        {taskPlanningMode ? (
                                            <View style={styles.userChip}>
                                                <MaterialCommunityIcons name="format-list-checks" size={12} color="#FFFFFF" />
                                                <Text style={styles.userChipText}>{t("src.components.chat.messagebubble.task_mode")}</Text>
                                            </View>
                                        ) : null}
                                    </View>
                                )}

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

                                {userContentText ? (
                                    <Text selectable style={styles.userText}>{userContentText}</Text>
                                ) : !hasUserVisualPayload ? (
                                    <Text selectable style={styles.userText}>{t("src.components.chat.messagebubble.empty_message")}</Text>
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
                            ) : (
                                <AssistantWorkIndicator
                                    active={assistantActive}
                                    primaryColor={palette.primary}
                                    textColor={palette.text}
                                    mutedColor={palette.textMuted}
                                    label={assistantPhaseLabel || t("src.components.chat.messagebubble.working")}
                                    subtitle={taskProgress?.currentStep || taskProgress?.subtitle || ""}
                                />
                            )}

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
    userMetaRow: {
        flexDirection: "row",
        flexWrap: "wrap",
        gap: 8,
        marginBottom: 10,
        maxWidth: "100%",
    },
    userChip: {
        flexDirection: "row",
        alignItems: "center",
        gap: 4,
        borderRadius: radii.pill,
        backgroundColor: "rgba(255,255,255,0.18)",
        borderWidth: 1,
        borderColor: "rgba(255,255,255,0.26)",
        paddingHorizontal: 9,
        paddingVertical: 4,
    },
    userChipText: {
        color: "#FFFFFF",
        fontSize: 11,
        fontWeight: "800",
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
    assistantInner: {
        paddingHorizontal: 16,
        paddingTop: 15,
        paddingBottom: 18,
        gap: 14,
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
        gap: 6,
        alignItems: "flex-start",
    },
    traceToggle: {
        minWidth: 20,
        height: 20,
        borderRadius: 10,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 6,
        opacity: 0.72,
    },
    traceToggleExpanded: {
        opacity: 0.9,
    },
    traceToggleText: {
        fontSize: 13,
        lineHeight: 16,
        fontWeight: "900",
    },
    traceGroupContent: {
        width: "100%",
        gap: 6,
    },
    assistantText: {
        fontSize: 14,
        lineHeight: 21,
    },
    workIndicator: {
        minHeight: 48,
        borderRadius: 16,
        borderWidth: 1,
        overflow: "hidden",
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
        paddingHorizontal: 10,
        paddingVertical: 9,
        position: "relative",
    },
    workIndicatorScan: {
        position: "absolute",
        top: 0,
        bottom: 0,
        width: 44,
        borderRadius: 999,
    },
    workIndicatorOrbWrap: {
        width: 32,
        height: 32,
        alignItems: "center",
        justifyContent: "center",
    },
    workIndicatorPulse: {
        position: "absolute",
        width: 32,
        height: 32,
        borderRadius: 16,
    },
    workIndicatorOrb: {
        width: 24,
        height: 24,
        borderRadius: 12,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    workIndicatorDot: {
        width: 7,
        height: 7,
        borderRadius: 999,
    },
    workIndicatorCopy: {
        flex: 1,
        minWidth: 0,
        gap: 5,
    },
    workIndicatorTitle: {
        fontSize: 12,
        fontWeight: "900",
        letterSpacing: -0.1,
    },
    workIndicatorSubtitle: {
        fontSize: 10,
        lineHeight: 14,
        fontWeight: "600",
    },
    workIndicatorBars: {
        gap: 5,
    },
    workIndicatorBar: {
        height: 5,
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
