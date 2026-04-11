import { memo, useEffect, useMemo, useState } from "react";
import { Image, Linking, Pressable, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import * as Clipboard from "expo-clipboard";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import { type AdminProcessRef } from "@v8/session-realtime";
import Animated, {
    Easing,
    cancelAnimation,
    useAnimatedStyle,
    useSharedValue,
    withRepeat,
    withTiming,
} from "react-native-reanimated";

import { resolveAdminAssetUrl } from "@/src/lib/admin-client";
import { getRenderablePhoneTimelineNodes } from "@/src/lib/chat-node-visibility";
import { buildVoicePlaybackKey, parsePhoneContentBlocks } from "@/src/lib/content-detector";
import { formatClock } from "@/src/lib/time";
import { resolveRenderableMediaUrl } from "@/src/lib/workspace-links";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type { ChatMessage, PhoneUiExecutionNode, PhoneUiTimelineNode, SkillReferenceSummary } from "@/src/types/admin";
import { ContentDispatcher } from "@/src/components/chat/ContentDispatcher";
import { NodeRenderBoundary } from "@/src/components/chat/NodeRenderBoundary";
import { MessageBlockItem } from "@/src/components/chat/MessageBlockItem";

const BRAND_MARK = require("../../../assets/images/brand-mark.png");

function isExecutionNode(node: PhoneUiTimelineNode): node is PhoneUiExecutionNode {
    return node.kind === "execution";
}

function hasToolCallId(node: PhoneUiTimelineNode): node is PhoneUiExecutionNode & { toolCallId: string } {
    return isExecutionNode(node) && typeof node.toolCallId === "string" && node.toolCallId.trim().length > 0;
}

function imageUrl(adminBaseUrl: string, value: string) {
    return resolveRenderableMediaUrl(adminBaseUrl, value);
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
    const attachmentImages = useMemo(
        () => (Array.isArray(message.images) ? message.images.map((item) => imageUrl(adminBaseUrl, item)).filter(Boolean) : []),
        [adminBaseUrl, message.images],
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
        || t("智能主管", "Supervisor");
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
    ]) || (isSupervisorLike ? t("主理人", "Lead") : "");
    const rolePillBackground = message.agentType === "supervisor" ? "#FFF7ED" : palette.accentSoft;
    const rolePillBorder = message.agentType === "supervisor" ? "rgba(245,158,11,0.24)" : `${palette.accent}33`;
    const rolePillTextColor = message.agentType === "supervisor" ? "#D97706" : palette.accent;
    const renderableNodes = useMemo(
        () => getRenderablePhoneTimelineNodes(message.nodes),
        [message.nodes],
    );
    const resultNodesByToolCallId = useMemo(() => {
        const mapping = new Map<string, PhoneUiExecutionNode>();
        for (const node of message.nodes || []) {
            if (hasToolCallId(node) && node.executionType === "tool_result") {
                mapping.set(node.toolCallId.trim(), node);
            }
        }
        return mapping;
    }, [message.nodes]);
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
    const horizontalBubbleLimit = Math.max(180, width - (isLandscape ? 232 : 134));
    const sharedTextBubbleWidth = Math.max(
        176,
        Math.min(
            horizontalBubbleLimit,
            isLandscape ? 360 : 308,
        ),
    );
    const assistantBubbleBackground = themeMode === "dark" ? "rgba(24,24,27,0.72)" : palette.surfaceStrong;
    const assistantBubbleBorder = themeMode === "dark" ? "rgba(255,255,255,0.08)" : palette.border;
    const assistantActionSurface = themeMode === "dark" ? "rgba(255,255,255,0.05)" : "rgba(255,255,255,0.74)";
    const assistantActive = !isUser && isLast && isLoading;
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
        ? t("正在理解…", "Thinking…")
        : streamPhase === "task_planning"
            ? t("正在推进任务…", "Advancing task…")
            : streamPhase === "tooling"
                ? t("正在调用工具…", "Using tools…")
                : streamPhase === "artifact_ready"
                    ? t("产物已就绪…", "Artifact ready…")
                    : streamPhase === "waiting_input"
                        ? t("等待你的输入…", "Waiting for your input…")
        : streamPhase === "agent_started"
            ? t("开始执行…", "Starting…")
            : streamPhase === "streaming"
                ? t("正在输出…", "Streaming…")
                : streamPhase === "settling"
                    ? t("即将完成…", "Finishing…")
                    : streamPhase === "error"
                        ? t("执行异常", "Stream error")
                        : "");
    const showAssistantPhasePill = Boolean(assistantPhaseLabel) && !assistantActive;

    const handleCopy = async () => {
        if (!copyValue) {
            return;
        }
        await Clipboard.setStringAsync(copyValue);
        setCopied(true);
    };

    const assistantBubbleWidth = voiceOnly
        ? Math.min(width * 0.72, 300)
        : sharedTextBubbleWidth;
    const assistantMinWidth = voiceOnly
        ? Math.min(width * 0.72, 300)
        : sharedTextBubbleWidth;
    const assistantColumnWidthStyle = voiceOnly
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
        return "";
    }, [message.content, renderableNodes]);

    useEffect(() => {
        if (!copied) {
            return undefined;
        }
        const timer = setTimeout(() => setCopied(false), 1600);
        return () => clearTimeout(timer);
    }, [copied]);

    const userColumnWidth = sharedTextBubbleWidth;
    const userBubbleWidth = sharedTextBubbleWidth;

    if (isUser) {
        return (
            <View style={styles.userRow}>
                <View style={styles.userRowInner}>
                    <View style={[styles.userColumn, { width: userColumnWidth, maxWidth: userColumnWidth }]}>
                        <Text style={[styles.userLabel, { color: palette.textMuted }]} numberOfLines={1}>
                            {userDisplayName || t("你", "You")}
                        </Text>
                        <LinearGradient
                            colors={[palette.primary, palette.accent]}
                            start={{ x: 0, y: 0 }}
                            end={{ x: 1, y: 1 }}
                            style={[styles.userBubble, { shadowColor: palette.primaryDeep, width: userBubbleWidth, maxWidth: userBubbleWidth }]}
                        >
                            {(commandPresetName || skillReferences.length > 0 || taskPlanningMode) && (
                                <View style={styles.userMetaRow}>
                                    {commandPresetName ? (
                                        <View style={styles.userChip}>
                                            <Text style={styles.userChipText}>/{commandPresetName}</Text>
                                        </View>
                                    ) : null}
                                    {skillReferences.map((skill) => (
                                        <View key={`${skill.name}:${skill.path || ""}`} style={styles.userChip}>
                                            <Text style={styles.userChipText}>@{skill.name}</Text>
                                        </View>
                                    ))}
                                    {taskPlanningMode ? (
                                        <View style={styles.userChip}>
                                            <Text style={styles.userChipText}>任务模式</Text>
                                        </View>
                                    ) : null}
                                </View>
                            )}

                            {attachmentImages.length > 0 ? (
                                <View style={styles.imageRow}>
                                    {attachmentImages.map((url) => (
                                        <Pressable key={url} onPress={() => void Linking.openURL(url)}>
                                            <Image source={{ uri: url }} style={styles.inlineImage} />
                                        </Pressable>
                                    ))}
                                </View>
                            ) : null}

                            <Text selectable style={styles.userText}>{message.content || t("（空消息）", "(Empty message)")}</Text>
                        </LinearGradient>

                        <View style={styles.actionRowUser}>
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

                        <Text style={[styles.timeLabelUser, { color: palette.textSoft }]}>{message.timestamp ? formatClock(message.timestamp, locale) : ""}</Text>
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
                    <Text style={[styles.agentName, { color: palette.text }]} numberOfLines={1}>{resolvedAgentName}</Text>
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
                ]}>
                    <View
                        style={[
                            styles.assistantBubbleClip,
                            {
                                backgroundColor: assistantBubbleBackground,
                                borderColor: assistantActive ? `${palette.primary}40` : assistantBubbleBorder,
                            },
                        ]}
                    >
                        <View style={[styles.assistantBubbleSheen, { backgroundColor: assistantActive ? `${palette.primary}66` : `${palette.primary}40` }]} />
                        <View style={[styles.assistantInner, voiceOnly && styles.assistantInnerVoiceOnly]}>
                            {hasStructuredNodes ? (
                                renderableNodes.map((node, index) => (
                                    <NodeRenderBoundary
                                        key={node.id || `${messageIdentity}:node:${index}`}
                                        title={t("节点渲染失败", "Node render failed")}
                                        description={t("这条子节点已降级显示，不影响整条正式回复。", "This node has been downgraded so the rest of the reply remains visible.")}
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
                                ))
                            ) : fallbackBlocks.length > 0 ? (
                                fallbackBlocks.map((block, index) => {
                                    const voiceKey = block.type === "voice"
                                        ? buildVoicePlaybackKey(messageIdentity, String(index), block.content)
                                        : "";
                                    return (
                                        <NodeRenderBoundary
                                            key={block.id}
                                            title={t("内容渲染失败", "Content render failed")}
                                            description={t("这段内容已降级显示，不影响整条正式回复。", "This content block has been downgraded so the rest of the reply remains visible.")}
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
                                    label={assistantPhaseLabel || t("工作中", "Working")}
                                    subtitle={taskProgress?.currentStep || ""}
                                />
                            )}

                        </View>
                    </View>
                </View>

                <View style={styles.actionRowAssistant}>
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

                <Text style={[styles.timeLabel, { color: palette.textSoft }]}>{message.timestamp ? formatClock(message.timestamp, locale) : ""}</Text>
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
        gap: 12,
        paddingLeft: 18,
        minWidth: 0,
        maxWidth: "100%",
        width: "100%",
        overflow: "hidden",
    },
    userColumn: {
        minWidth: 0,
        flexShrink: 1,
        alignItems: "flex-end",
        gap: 7,
        maxWidth: "100%",
        overflow: "hidden",
    },
    userAvatarShell: {
        width: 42,
        paddingTop: 2,
        alignItems: "flex-end",
    },
    userLabel: {
        fontSize: 11,
        fontWeight: "700",
        paddingRight: 2,
    },
    userBubble: {
        borderRadius: 30,
        borderTopRightRadius: 12,
        paddingHorizontal: 18,
        paddingVertical: 16,
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
        borderRadius: radii.pill,
        backgroundColor: "rgba(255,255,255,0.18)",
        borderWidth: 1,
        borderColor: "rgba(255,255,255,0.26)",
        paddingHorizontal: 10,
        paddingVertical: 5,
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
    actionRowUser: {
        flexDirection: "row",
        justifyContent: "flex-end",
        gap: 6,
        paddingRight: 6,
        width: "100%",
        maxWidth: "100%",
        alignSelf: "flex-end",
    },
    assistantRow: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 12,
        marginBottom: spacing.lg,
        minWidth: 0,
    },
    avatarShell: {
        width: 42,
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
        width: 42,
        height: 42,
        borderRadius: 21,
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
        paddingLeft: 2,
        minHeight: 26,
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
        fontSize: 14,
        fontWeight: "900",
        letterSpacing: -0.2,
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
        borderRadius: 24,
        borderTopLeftRadius: 9,
        shadowColor: "#0F172A",
        shadowOpacity: 0.08,
        shadowRadius: 20,
        shadowOffset: { width: 0, height: 10 },
        elevation: 2,
        overflow: "visible",
        width: "100%",
    },
    assistantBubbleVoiceOnly: {
        minWidth: 260,
    },
    assistantBubbleClip: {
        overflow: "hidden",
        borderRadius: 24,
        borderTopLeftRadius: 9,
        borderWidth: 1,
        width: "100%",
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
    assistantInnerVoiceOnly: {
        paddingTop: 18,
        paddingBottom: 20,
    },
    assistantText: {
        fontSize: 14,
        lineHeight: 21,
    },
    workIndicator: {
        minHeight: 56,
        borderRadius: 18,
        borderWidth: 1,
        overflow: "hidden",
        flexDirection: "row",
        alignItems: "center",
        gap: 12,
        paddingHorizontal: 12,
        paddingVertical: 11,
        position: "relative",
    },
    workIndicatorScan: {
        position: "absolute",
        top: 0,
        bottom: 0,
        width: 54,
        borderRadius: 999,
    },
    workIndicatorOrbWrap: {
        width: 36,
        height: 36,
        alignItems: "center",
        justifyContent: "center",
    },
    workIndicatorPulse: {
        position: "absolute",
        width: 36,
        height: 36,
        borderRadius: 18,
    },
    workIndicatorOrb: {
        width: 28,
        height: 28,
        borderRadius: 14,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    workIndicatorDot: {
        width: 9,
        height: 9,
        borderRadius: 999,
    },
    workIndicatorCopy: {
        flex: 1,
        minWidth: 0,
        gap: 5,
    },
    workIndicatorTitle: {
        fontSize: 13,
        fontWeight: "900",
        letterSpacing: -0.1,
    },
    workIndicatorSubtitle: {
        fontSize: 11,
        lineHeight: 16,
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
    actionRowAssistant: {
        flexDirection: "row",
        gap: 6,
        justifyContent: "flex-end",
        paddingRight: 6,
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
        paddingHorizontal: 2,
    },
    timeLabelUser: {
        fontSize: 11,
        paddingHorizontal: 2,
    },
});
