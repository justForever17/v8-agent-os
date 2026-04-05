import { useCallback, useEffect, useState } from "react";
import {
    Alert,
    Pressable,
    RefreshControl,
    ScrollView,
    StyleSheet,
    Text,
    View,
} from "react-native";
import { Redirect, router, type Href } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { GlassCard } from "@/src/components/common/GlassCard";
import { LoadingScreen } from "@/src/components/common/LoadingScreen";
import { PhoneTopbar, type PhoneTopbarAction } from "@/src/components/layout/PhoneTopbar";
import { conversationGroupLabels, conversationGroupOrder, groupConversations, type ConversationGroupKey } from "@/src/lib/conversation-groups";
import { createConversation, deleteConversation, listConversations } from "@/src/lib/phone-api";
import { formatRelativeTime } from "@/src/lib/time";
import { useAppSession } from "@/src/providers/app-session";
import { colors, radii, spacing } from "@/src/theme/tokens";
import type { ConversationSummary } from "@/src/types/admin";

const groupIcons: Record<ConversationGroupKey, keyof typeof MaterialCommunityIcons.glyphMap> = {
    channels: "earth",
    cron: "clock-outline",
    hooks: "lightning-bolt-outline",
    web: "message-outline",
};

export default function SessionsScreen() {
    const { status, activeConversationId, setActiveConversationId, authorizedFetch } = useAppSession();
    const [conversations, setConversations] = useState<ConversationSummary[]>([]);
    const [refreshing, setRefreshing] = useState(false);
    const [busy, setBusy] = useState(false);
    const grouped = groupConversations(conversations);

    const actions: PhoneTopbarAction[] = [
        { key: "chat", icon: "chat-processing-outline", onPress: () => router.push("/chat" as Href) },
        { key: "connect", icon: "lan-connect", onPress: () => router.push("/connect" as Href) },
        { key: "desktop-live", icon: "monitor-dashboard", onPress: () => router.push("/desktop-live" as Href), tone: "primary" },
        { key: "rpa", icon: "robot-outline", onPress: () => router.push("/rpa" as Href), tone: "accent" },
        { key: "settings", icon: "cog-outline", onPress: () => router.push("/settings" as Href) },
    ];

    const load = useCallback(async () => {
        setRefreshing(true);
        try {
            setConversations(await listConversations(authorizedFetch));
        } catch (error) {
            Alert.alert("读取失败", error instanceof Error ? error.message : "无法加载会话列表");
        } finally {
            setRefreshing(false);
        }
    }, [authorizedFetch]);

    useEffect(() => {
        if (status === "authenticated") {
            void load();
        }
    }, [load, status]);

    const createNew = async () => {
        setBusy(true);
        try {
            const created = await createConversation(authorizedFetch, "New Chat");
            await setActiveConversationId(created.id);
            await load();
            router.push("/chat" as Href);
        } catch (error) {
            Alert.alert("创建失败", error instanceof Error ? error.message : "无法创建新会话");
        } finally {
            setBusy(false);
        }
    };

    const remove = async (item: ConversationSummary) => {
        Alert.alert("删除会话", "确定删除这个会话吗？", [
            { text: "取消", style: "cancel" },
            {
                text: "删除",
                style: "destructive",
                onPress: async () => {
                    try {
                        await deleteConversation(authorizedFetch, item.id);
                        if (activeConversationId === item.id) {
                            await setActiveConversationId(null);
                        }
                        await load();
                    } catch (error) {
                        Alert.alert("删除失败", error instanceof Error ? error.message : "无法删除会话");
                    }
                },
            },
        ]);
    };

    if (status === "booting") {
        return <LoadingScreen label="正在同步会话列表…" />;
    }

    if (status === "anonymous") {
        return <Redirect href="/login" />;
    }

    return (
        <LinearGradient
            colors={[colors.background, "#FFF7ED"]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={styles.gradient}
        >
            <SafeAreaView style={styles.safeArea} edges={["top", "left", "right"]}>
                <PhoneTopbar actions={actions} />

                <ScrollView
                    contentContainerStyle={styles.content}
                    refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load()} />}
                >
                    <Pressable style={[styles.newButton, busy && styles.disabled]} onPress={() => void createNew()}>
                        <MaterialCommunityIcons name="plus" size={18} color="#FFFFFF" />
                        <Text style={styles.newButtonText}>新建对话</Text>
                    </Pressable>

                    {conversations.length === 0 ? (
                        <Text style={styles.emptyBody}>当前没有会话</Text>
                    ) : null}

                    {conversationGroupOrder
                        .filter((groupKey) => grouped[groupKey].length > 0)
                        .map((groupKey) => (
                            <View key={groupKey} style={styles.groupSection}>
                                <View style={styles.groupHeader}>
                                    <View style={styles.groupPill}>
                                        <MaterialCommunityIcons name={groupIcons[groupKey]} size={14} color={colors.textMuted} />
                                        <Text style={styles.groupLabel}>{conversationGroupLabels[groupKey]}</Text>
                                    </View>
                                    <Text style={styles.groupCount}>{grouped[groupKey].length}</Text>
                                </View>

                                {grouped[groupKey].map((item) => {
                                    const active = item.id === activeConversationId;
                                    return (
                                        <Pressable
                                            key={item.id}
                                            onPress={async () => {
                                                await setActiveConversationId(item.id);
                                                router.push("/chat" as Href);
                                            }}
                                        >
                                            <GlassCard>
                                                <View style={styles.itemHeader}>
                                                    <View style={[styles.itemDot, active && styles.itemDotActive]} />
                                                    <View style={styles.itemBody}>
                                                        <Text style={styles.itemTitle} numberOfLines={1}>
                                                            {item.title || `会话 ${item.id.slice(0, 8)}`}
                                                        </Text>
                                                        <Text style={styles.itemMeta}>
                                                            {formatRelativeTime(item.updatedAt || item.createdAt || "")}
                                                        </Text>
                                                    </View>
                                                    <Pressable onPress={() => void remove(item)} hitSlop={8}>
                                                        <MaterialCommunityIcons name="trash-can-outline" size={18} color={colors.textSoft} />
                                                    </Pressable>
                                                </View>
                                            </GlassCard>
                                        </Pressable>
                                    );
                                })}
                            </View>
                        ))}
                </ScrollView>
            </SafeAreaView>
        </LinearGradient>
    );
}

const styles = StyleSheet.create({
    gradient: {
        flex: 1,
    },
    safeArea: {
        flex: 1,
    },
    newButton: {
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        backgroundColor: colors.primary,
        paddingHorizontal: 14,
        paddingVertical: 10,
        borderRadius: radii.pill,
    },
    newButtonText: {
        color: "#FFFFFF",
        fontWeight: "800",
    },
    disabled: {
        opacity: 0.6,
    },
    content: {
        paddingHorizontal: spacing.lg,
        paddingBottom: spacing.xl,
        gap: spacing.md,
    },
    groupSection: {
        gap: spacing.sm,
    },
    groupHeader: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
    },
    groupPill: {
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
        borderRadius: radii.pill,
        backgroundColor: colors.surfaceStrong,
        borderWidth: 1,
        borderColor: colors.border,
        paddingHorizontal: 10,
        paddingVertical: 6,
    },
    groupLabel: {
        color: colors.textSoft,
        fontSize: 11,
        fontWeight: "800",
        textTransform: "uppercase",
        letterSpacing: 0.7,
    },
    groupCount: {
        color: colors.textSoft,
        fontSize: 12,
        fontWeight: "800",
    },
    emptyBody: {
        color: colors.textMuted,
        fontSize: 14,
        lineHeight: 20,
        paddingVertical: spacing.sm,
    },
    itemHeader: {
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
    },
    itemDot: {
        width: 10,
        height: 10,
        borderRadius: 5,
        backgroundColor: colors.textSoft,
    },
    itemDotActive: {
        backgroundColor: colors.primary,
    },
    itemBody: {
        flex: 1,
        gap: 4,
    },
    itemTitle: {
        color: colors.text,
        fontSize: 15,
        fontWeight: "800",
    },
    itemMeta: {
        color: colors.textMuted,
        fontSize: 12,
    },
});
