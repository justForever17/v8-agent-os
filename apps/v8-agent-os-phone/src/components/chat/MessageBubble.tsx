import { memo, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Image, Linking, Pressable, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import * as Clipboard from "expo-clipboard";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import { resolveAdminResourceUrl, type AdminProcessRef } from "@v8/session-realtime";

import { getArtifactContentUrl, getWorkspaceFileUrl } from "@/src/lib/phone-api";
import { resolveAdminAssetUrl } from "@/src/lib/admin-client";
import { buildVoicePlaybackKey, parsePhoneContentBlocks } from "@/src/lib/content-detector";
import { formatClock } from "@/src/lib/time";
import { normalizeRenderableWorkspaceUrl } from "@/src/lib/workspace-links";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type { ChatArtifact, ChatMessage, SkillReferenceSummary } from "@/src/types/admin";
import { ContentDispatcher } from "@/src/components/chat/ContentDispatcher";
import { MessageBlockItem } from "@/src/components/chat/MessageBlockItem";

const BRAND_MARK = require("../../../assets/images/brand-mark.png");

function artifactUrl(adminBaseUrl: string, artifact: ChatArtifact) {
    const resolved = resolveAdminResourceUrl("phone", adminBaseUrl, artifact.resourceRef || null);
    if (resolved) {
        return resolved;
    }
    if (artifact.id || artifact.artifactId) {
        return getArtifactContentUrl(adminBaseUrl, artifact.id || artifact.artifactId || "");
    }
    if (artifact.workspacePath || artifact.sourcePath?.startsWith("/workspace/")) {
        const workspacePath = String(artifact.workspacePath || artifact.sourcePath || "")
            .replace(/^\/workspace\//, "")
            .replace(/^workspace\//, "");
        return getWorkspaceFileUrl(adminBaseUrl, workspacePath);
    }
    const candidate = artifact.previewUrl || artifact.externalUrl || artifact.sourcePath || "";
    return normalizeRenderableWorkspaceUrl(adminBaseUrl, candidate);
}

function imageUrl(adminBaseUrl: string, value: string) {
    const trimmed = String(value || "").trim();
    if (!trimmed) return "";
    if (trimmed.startsWith("/workspace/")) {
        return getWorkspaceFileUrl(adminBaseUrl, trimmed.replace(/^\/workspace\//, ""));
    }
    return normalizeRenderableWorkspaceUrl(adminBaseUrl, trimmed);
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
    onOpenArtifact,
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
    onOpenArtifact?: (artifact: ChatArtifact) => void;
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
    const hasStructuredNodes = Array.isArray(message.nodes) && message.nodes.length > 0;
    const fallbackBlocks = useMemo(
        () => (hasStructuredNodes ? [] : parsePhoneContentBlocks(String(message.content || ""))),
        [hasStructuredNodes, message.content],
    );
    const voiceDescriptors = useMemo(() => {
        const descriptors: Array<{ key: string; text: string }> = [];
        if (hasStructuredNodes) {
            (message.nodes || []).forEach((node, nodeIndex) => {
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
    }, [fallbackBlocks, hasStructuredNodes, message.nodes, messageIdentity]);
    const hasArtifactNode = Boolean(message.nodes?.some((node) => node.kind === "artifact"));
    const supplementalArtifacts = useMemo(
        () => (Array.isArray(message.artifacts) && !hasArtifactNode ? message.artifacts : []),
        [hasArtifactNode, message.artifacts],
    );
    const hasRenderableText = useMemo(() => {
        if (hasStructuredNodes) {
            return (message.nodes || []).some((node) => {
                if (node.kind === "narrative") {
                    return parsePhoneContentBlocks(String(node.content || "")).some((block) => block.type !== "voice" && block.content.trim());
                }
                return true;
            });
        }
        return fallbackBlocks.some((block) => block.type !== "voice" && block.content.trim());
    }, [fallbackBlocks, hasStructuredNodes, message.nodes]);
    const voiceOnly = !isUser && voiceDescriptors.length > 0 && !hasRenderableText && supplementalArtifacts.length === 0;
    const horizontalBubbleLimit = Math.max(180, width - (isLandscape ? 232 : 134));
    const assistantBubbleBackground = themeMode === "dark" ? "rgba(24,24,27,0.72)" : palette.surfaceStrong;
    const assistantBubbleBorder = themeMode === "dark" ? "rgba(255,255,255,0.08)" : palette.border;
    const assistantActionSurface = themeMode === "dark" ? "rgba(255,255,255,0.05)" : "rgba(255,255,255,0.74)";
    const assistantActive = !isUser && isLast && isLoading;
    const assistantPhaseLabel = streamPhase === "placeholder"
        ? t("正在理解…", "Thinking…")
        : streamPhase === "agent_started"
            ? t("开始执行…", "Starting…")
            : streamPhase === "streaming"
                ? t("正在输出…", "Streaming…")
                : streamPhase === "settling"
                    ? t("即将完成…", "Finishing…")
                    : streamPhase === "error"
                        ? t("执行异常", "Stream error")
                        : "";
    const assistantPhaseSubtitle = streamPhase === "placeholder"
        ? t("正在整理上下文并准备响应。", "Preparing context and the first response.")
        : streamPhase === "agent_started"
            ? t("已进入执行阶段，正在启动相关运行时。", "Execution has started and runtimes are warming up.")
            : streamPhase === "streaming"
                ? t("正在持续整理结果并刷新回复内容。", "Streaming updates into the current reply.")
                : streamPhase === "settling"
                    ? t("正在收尾并与最新快照对齐。", "Settling the run and aligning with the latest snapshot.")
                    : streamPhase === "error"
                        ? t("当前运行出现异常，请稍后查看结果。", "The current run hit an error. Please check the result in a moment.")
                        : "";

    const openArtifact = async (artifact: ChatArtifact) => {
        if (onOpenArtifact) {
            onOpenArtifact(artifact);
            return;
        }
        const url = artifactUrl(adminBaseUrl, artifact);
        if (!url) return;
        await Linking.openURL(url);
    };

    const handleCopy = async () => {
        if (!copyValue) {
            return;
        }
        await Clipboard.setStringAsync(copyValue);
        setCopied(true);
    };

    const bubbleWidth = Math.min(Math.min(width * (isLandscape ? 0.66 : 0.82), isUser ? 420 : 560), horizontalBubbleLimit);
    const assistantBubbleWidth = bubbleWidth;
    const copyValue = useMemo(() => {
        const directContent = String(message.content || "").trim();
        if (directContent) {
            return directContent;
        }
        if (Array.isArray(message.nodes) && message.nodes.length > 0) {
            const nodeContent = message.nodes
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
    }, [message.content, message.nodes]);

    useEffect(() => {
        if (!copied) {
            return undefined;
        }
        const timer = setTimeout(() => setCopied(false), 1600);
        return () => clearTimeout(timer);
    }, [copied]);

    const userActionCount = Number(Boolean(copyValue)) + Number(Boolean(onDelete));
    const userActionFootprint = userActionCount > 0
        ? (userActionCount * 26) + (Math.max(userActionCount - 1, 0) * 6) + 8
        : 0;
    const userColumnWidth = Math.max(
        156,
        Math.min(
            width - 42 - 12 - (isLandscape ? 178 : 108),
            isLandscape ? 328 : 272,
        ),
    );
    const userBubbleWidth = Math.max(152, userColumnWidth - Math.max(userActionFootprint - 2, 0));

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

            <View style={[styles.assistantColumn, { maxWidth: assistantBubbleWidth, minWidth: voiceOnly ? Math.min(width * 0.72, 300) : 140 }]}>
                <View style={styles.agentMeta}>
                    <Text style={[styles.agentName, { color: palette.text }]} numberOfLines={1}>{resolvedAgentName}</Text>
                    {resolvedAgentRoleLabel ? (
                        <View style={[styles.rolePill, { backgroundColor: rolePillBackground, borderColor: rolePillBorder }]}>
                            <Text style={[styles.rolePillText, { color: rolePillTextColor }]}>{resolvedAgentRoleLabel}</Text>
                        </View>
                    ) : null}
                    {assistantPhaseLabel ? (
                        <View style={[styles.streamPill, { backgroundColor: `${palette.primary}14`, borderColor: `${palette.primary}2A` }]}>
                            {assistantActive ? <ActivityIndicator size="small" color={palette.primary} /> : null}
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
                                (message.nodes || []).map((node, index) => (
                                    <ContentDispatcher
                                        key={node.id || `${messageIdentity}:node:${index}`}
                                        node={node}
                                        messageIdentity={`${messageIdentity}:node:${index}`}
                                        isExecuting={assistantActive}
                                        isStreaming={streamPhase === "streaming" || streamPhase === "agent_started"}
                                        speakingKey={speakingKey}
                                        onSpeakVoice={onSpeakVoice}
                                        onOpenArtifact={openArtifact}
                                        processes={processes}
                                    />
                                ))
                            ) : fallbackBlocks.length > 0 ? (
                                fallbackBlocks.map((block, index) => {
                                    const voiceKey = block.type === "voice"
                                        ? buildVoicePlaybackKey(messageIdentity, String(index), block.content)
                                        : "";
                                    return (
                                        <MessageBlockItem
                                            key={block.id}
                                            block={block}
                                            isStreaming={assistantActive && (streamPhase === "streaming" || streamPhase === "agent_started")}
                                            speaking={Boolean(voiceKey) && speakingKey === voiceKey}
                                            onSpeak={voiceKey && onSpeakVoice ? () => onSpeakVoice(block.content, voiceKey) : undefined}
                                            onOpenArtifact={openArtifact}
                                        />
                                    );
                                })
                            ) : (
                                <View style={styles.placeholderWrap}>
                                    <View style={[styles.placeholderBadge, { backgroundColor: `${palette.primary}14`, borderColor: `${palette.primary}28` }]}>
                                        {assistantActive ? <ActivityIndicator size="small" color={palette.primary} /> : null}
                                        <MaterialCommunityIcons name="robot-excited-outline" size={16} color={palette.primary} />
                                    </View>
                                    <View style={styles.placeholderBody}>
                                        <Text style={[styles.assistantText, styles.placeholderTitle, { color: palette.text }]}>
                                            {assistantPhaseLabel || t("智能主管正在处理中…", "Supervisor is working...")}
                                        </Text>
                                        <Text style={[styles.placeholderSubtitle, { color: palette.textMuted }]}>
                                            {assistantPhaseSubtitle}
                                        </Text>
                                    </View>
                                </View>
                            )}

                            {supplementalArtifacts.length > 0 ? (
                                <View style={styles.artifactList}>
                                    {supplementalArtifacts.map((artifact, index) => (
                                        <Pressable
                                            key={`${artifact.id || artifact.artifactId || artifact.workspacePath || artifact.sourcePath || artifact.title || "artifact"}:${index}`}
                                            onPress={() => openArtifact(artifact)}
                                            style={[styles.artifactCard, { backgroundColor: palette.surface, borderColor: palette.border }]}
                                        >
                                            <MaterialCommunityIcons name="file-star-outline" size={16} color={palette.primaryDeep} />
                                            <View style={styles.artifactBody}>
                                                <Text style={[styles.artifactTitle, { color: palette.text }]} numberOfLines={1}>
                                                    {artifact.displayLabel || artifact.title || artifact.kind || t("产物", "Artifact")}
                                                </Text>
                                                <Text style={[styles.artifactSubtitle, { color: palette.textMuted }]} numberOfLines={1}>
                                                    {artifact.displaySubtitle || artifact.kind || artifact.workspacePath || artifact.sourcePath || t("点击打开", "Tap to open")}
                                                </Text>
                                            </View>
                                            <MaterialCommunityIcons name="open-in-new" size={15} color={palette.textSoft} />
                                        </Pressable>
                                    ))}
                                </View>
                            ) : null}
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
    },
    assistantBubbleVoiceOnly: {
        minWidth: 260,
    },
    assistantBubbleClip: {
        overflow: "hidden",
        borderRadius: 24,
        borderTopLeftRadius: 9,
        borderWidth: 1,
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
    },
    assistantInnerVoiceOnly: {
        paddingTop: 18,
        paddingBottom: 20,
    },
    assistantText: {
        fontSize: 14,
        lineHeight: 21,
    },
    placeholderWrap: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 10,
        minHeight: 48,
    },
    placeholderBadge: {
        width: 32,
        height: 32,
        borderRadius: 16,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
        gap: 4,
    },
    placeholderBody: {
        flex: 1,
        minWidth: 0,
        gap: 4,
    },
    placeholderTitle: {
        fontWeight: "800",
        lineHeight: 20,
    },
    placeholderSubtitle: {
        fontSize: 12,
        lineHeight: 18,
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
    artifactList: {
        gap: 8,
        minWidth: 0,
    },
    artifactCard: {
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
        borderRadius: 16,
        borderWidth: 1,
        paddingHorizontal: 12,
        paddingVertical: 10,
        minWidth: 0,
    },
    artifactBody: {
        flex: 1,
        gap: 2,
        minWidth: 0,
    },
    artifactTitle: {
        fontSize: 13,
        fontWeight: "800",
    },
    artifactSubtitle: {
        fontSize: 11,
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
