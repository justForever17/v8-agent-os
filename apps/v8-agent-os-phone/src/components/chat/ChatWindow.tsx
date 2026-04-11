import { memo, useEffect, useMemo, useRef, useState } from "react";
import {
    Pressable,
    RefreshControl,
    ScrollView,
    StyleSheet,
    Text,
    View,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import { isAskUserInteractionApproval, type AdminProcessRef, type ContextReferenceItem } from "@v8/session-realtime";

import { AskUserModal } from "@/src/components/chat/AskUserModal";
import { ContextReferencesHUD } from "@/src/components/chat/ContextReferencesHUD";
import { MessageBubble } from "@/src/components/chat/MessageBubble";
import { hasRenderablePhoneTimelineNodes } from "@/src/lib/chat-node-visibility";
import { isActiveAssistantStreamPhase } from "@/src/lib/chat-stream-state";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type { ChatMessage, PendingApproval } from "@/src/types/admin";

type ChatWindowProps = {
    adminBaseUrl: string;
    messages: ChatMessage[];
    scrollLocked?: boolean;
    refreshing?: boolean;
    onRefresh?: () => void;
    onDeleteMessage?: (message: ChatMessage) => void;
    speakingKey?: string;
    onSpeakVoice?: (text: string, messageKey: string) => void;
    userImageUri?: string;
    userDisplayName?: string;
    processes: AdminProcessRef[];
    contextReferences: ContextReferenceItem[];
    pendingApproval?: PendingApproval | null;
    pendingApprovalCount?: number;
    approvalBusy?: boolean;
    onResolveApproval?: (approval: PendingApproval, answer: string, approve: boolean) => void | Promise<void>;
    onOpenApprovalPanel?: () => void;
    isLandscape?: boolean;
    bottomInset?: number;
    emptyState?: {
        icon?: keyof typeof MaterialCommunityIcons.glyphMap;
        title: string;
        subtitle?: string;
    } | null;
};

function isAskUserApproval(approval: PendingApproval | null | undefined) {
    return isAskUserInteractionApproval(approval);
}

function hasRenderableMessage(message: ChatMessage) {
    if (message.uiEphemeral || isActiveAssistantStreamPhase(message.uiStreamPhase)) {
        return true;
    }
    if (String(message.content || "").trim()) {
        return true;
    }
    if (Array.isArray(message.images) && message.images.length > 0) {
        return true;
    }
    if (hasRenderablePhoneTimelineNodes(message.nodes)) {
        return true;
    }
    return false;
}

export const ChatWindow = memo(function ChatWindow({
    adminBaseUrl,
    messages,
    scrollLocked = false,
    refreshing = false,
    onRefresh,
    onDeleteMessage,
    speakingKey,
    onSpeakVoice,
    userImageUri,
    userDisplayName,
    processes,
    contextReferences,
    pendingApproval,
    pendingApprovalCount = 0,
    approvalBusy = false,
    onResolveApproval,
    onOpenApprovalPanel,
    isLandscape = false,
    bottomInset = 172,
    emptyState,
}: ChatWindowProps) {
    const { colors, t } = useUiPrefs();
    const scrollRef = useRef<ScrollView | null>(null);
    const [isAtBottom, setIsAtBottom] = useState(true);
    const [askUserOpen, setAskUserOpen] = useState(Boolean(pendingApproval && isAskUserApproval(pendingApproval)));
    const visibleMessages = useMemo(
        () => messages.filter(hasRenderableMessage),
        [messages],
    );
    const lastVisibleMessage = visibleMessages[visibleMessages.length - 1] || null;
    const lastVisibleSignature = useMemo(() => {
        if (!lastVisibleMessage) {
            return "";
        }
        return [
            String(lastVisibleMessage.renderKey || lastVisibleMessage.id || ""),
            String(lastVisibleMessage.content || "").length,
            Array.isArray(lastVisibleMessage.nodes) ? lastVisibleMessage.nodes.length : 0,
            String(lastVisibleMessage.uiStreamPhase || ""),
            lastVisibleMessage.uiEphemeral ? "1" : "0",
        ].join(":");
    }, [lastVisibleMessage]);
    const resolvedEmptyState = emptyState || {
        icon: "robot-happy-outline" as const,
        title: t("没有消息历史", "No messages yet"),
        subtitle: t("打个招呼吧", "Start the conversation"),
    };
    const scrollToBottomOffset = Math.max(92, bottomInset - 40);
    const hudBottomOffset = Math.max(56, bottomInset - 96);

    useEffect(() => {
        if (isAtBottom) {
            requestAnimationFrame(() => scrollRef.current?.scrollToEnd({ animated: true }));
        }
    }, [isAtBottom, lastVisibleSignature, visibleMessages.length]);

    useEffect(() => {
        setAskUserOpen(Boolean(pendingApproval && isAskUserApproval(pendingApproval)));
    }, [pendingApproval]);

    return (
        <View style={styles.root}>
            <View style={styles.contextWrap}>
                <ContextReferencesHUD contextReferences={contextReferences} />
            </View>

            <View style={[styles.messagesShell, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                <ScrollView
                    ref={scrollRef}
                    style={styles.scroll}
                    scrollEnabled={!scrollLocked}
                    keyboardShouldPersistTaps="always"
                    keyboardDismissMode="none"
                    contentContainerStyle={[
                        styles.messagesContent,
                        isLandscape && styles.messagesContentLandscape,
                        { paddingBottom: bottomInset },
                        visibleMessages.length === 0 && styles.messagesContentEmpty,
                    ]}
                    refreshControl={onRefresh ? <RefreshControl refreshing={refreshing} onRefresh={onRefresh} /> : undefined}
                    onScroll={(event) => {
                        const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
                        const distanceToBottom = contentSize.height - contentOffset.y - layoutMeasurement.height;
                        setIsAtBottom(distanceToBottom < 96);
                    }}
                    scrollEventThrottle={32}
                >
                    {visibleMessages.length === 0 ? (
                        <View style={styles.emptyState}>
                            <MaterialCommunityIcons
                                name={resolvedEmptyState.icon || "robot-happy-outline"}
                                size={42}
                                color={colors.textSoft}
                            />
                            <Text style={[styles.emptyTitle, { color: colors.textMuted }]}>
                                {resolvedEmptyState.title}
                            </Text>
                            {resolvedEmptyState.subtitle ? (
                                <Text style={[styles.emptySubtitle, { color: colors.textSoft }]}>
                                    {resolvedEmptyState.subtitle}
                                </Text>
                            ) : null}
                        </View>
                    ) : (
                        visibleMessages.map((message, index) => (
                            <MessageBubble
                                key={message.renderKey || message.id}
                                adminBaseUrl={adminBaseUrl}
                                message={message}
                                isLast={index === visibleMessages.length - 1}
                                isLoading={message.role === "assistant" && isActiveAssistantStreamPhase(message.uiStreamPhase)}
                                streamPhase={message.uiStreamPhase}
                                onDelete={onDeleteMessage}
                                onSpeakVoice={onSpeakVoice}
                                speakingKey={speakingKey}
                                userImageUri={userImageUri}
                                userDisplayName={userDisplayName}
                                processes={processes}
                            />
                        ))
                    )}
                </ScrollView>
                <LinearGradient
                    pointerEvents="none"
                    colors={["rgba(255,255,255,0)", colors.surfaceStrong]}
                    style={styles.bottomFade}
                />
            </View>

            {!isAtBottom ? (
                <Pressable
                    style={[
                        styles.scrollToBottom,
                        {
                            backgroundColor: colors.surfaceStrong,
                            borderColor: colors.border,
                            bottom: scrollToBottomOffset,
                        },
                    ]}
                    onPress={() => scrollRef.current?.scrollToEnd({ animated: true })}
                >
                    <MaterialCommunityIcons name="arrow-down" size={18} color={colors.text} />
                </Pressable>
            ) : null}

            <View
                pointerEvents="box-none"
                style={[
                    styles.hudStack,
                    isLandscape && styles.hudStackLandscape,
                    { bottom: hudBottomOffset },
                ]}
            >
                {pendingApproval && isAskUserApproval(pendingApproval) ? (
                    <Pressable
                        style={[styles.artifactsPill, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}
                        onPress={() => setAskUserOpen(true)}
                    >
                        <MaterialCommunityIcons name="message-processing-outline" size={14} color={colors.warning} />
                        <Text style={[styles.artifactsPillText, { color: colors.text }]}>
                            {t("等待输入", "Waiting input")}
                        </Text>
                    </Pressable>
                ) : null}
                {pendingApproval && !isAskUserApproval(pendingApproval) && onOpenApprovalPanel ? (
                    <Pressable
                        style={[styles.artifactsPill, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}
                        onPress={onOpenApprovalPanel}
                    >
                        <MaterialCommunityIcons name="source-branch" size={14} color={colors.warning} />
                        <Text style={[styles.artifactsPillText, { color: colors.text }]}>
                            {t("待确认", "Approvals")} {pendingApprovalCount || 1}
                        </Text>
                    </Pressable>
                ) : null}
            </View>

            {pendingApproval && isAskUserApproval(pendingApproval) ? (
                <AskUserModal
                    visible={askUserOpen}
                    question={String(
                        pendingApproval.request?.question
                        || pendingApproval.request?.prompt
                        || "",
                    )}
                    toolCallId={String(
                        pendingApproval.request?.toolCallId
                        || pendingApproval.id
                        || pendingApproval.approval_id
                        || "",
                    )}
                    busy={approvalBusy}
                    onCancel={() => setAskUserOpen(false)}
                    onSubmit={async (_toolCallId, answer, approve) => {
                        if (!pendingApproval || !onResolveApproval) {
                            return;
                        }
                        await onResolveApproval(pendingApproval, answer, approve);
                        setAskUserOpen(false);
                    }}
                />
            ) : null}
        </View>
    );
});

const styles = StyleSheet.create({
    root: {
        flex: 1,
        position: "relative",
    },
    contextWrap: {
        paddingHorizontal: 14,
        paddingBottom: 6,
    },
    messagesShell: {
        flex: 1,
        marginHorizontal: 8,
        borderRadius: 28,
        borderWidth: 1,
        overflow: "hidden",
        shadowColor: "#0F172A",
        shadowOpacity: 0.06,
        shadowRadius: 16,
        shadowOffset: { width: 0, height: 8 },
        elevation: 2,
    },
    scroll: {
        flex: 1,
    },
    messagesContent: {
        width: "100%",
        maxWidth: 760,
        alignSelf: "center",
        paddingHorizontal: 14,
        paddingTop: 8,
        paddingBottom: 180,
    },
    messagesContentLandscape: {
        maxWidth: 860,
        paddingHorizontal: 22,
        paddingBottom: 148,
    },
    messagesContentEmpty: {
        flexGrow: 1,
        justifyContent: "center",
    },
    emptyState: {
        minHeight: 340,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 28,
        gap: 10,
    },
    emptyTitle: {
        fontSize: 18,
        fontWeight: "800",
        textAlign: "center",
    },
    emptySubtitle: {
        fontSize: 12,
        textAlign: "center",
    },
    bottomFade: {
        position: "absolute",
        left: 0,
        right: 0,
        bottom: 0,
        height: 56,
    },
    scrollToBottom: {
        position: "absolute",
        right: 18,
        zIndex: 12,
        width: 38,
        height: 38,
        borderRadius: 19,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
        shadowColor: "#0F172A",
        shadowOpacity: 0.08,
        shadowRadius: 10,
        shadowOffset: { width: 0, height: 6 },
        elevation: 3,
    },
    hudStack: {
        position: "absolute",
        right: 16,
        zIndex: 18,
        width: 228,
        gap: 6,
    },
    hudStackLandscape: {
        right: 18,
    },
    artifactsPill: {
        minHeight: 28,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        paddingHorizontal: 10,
        borderRadius: radii.pill,
        borderWidth: 1,
        alignSelf: "flex-end",
    },
    artifactsPillText: {
        fontSize: 11,
        fontWeight: "700",
    },
});
