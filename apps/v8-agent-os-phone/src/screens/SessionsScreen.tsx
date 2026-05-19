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
import { useGoHomeToChat } from "@/src/hooks/use-go-home-to-chat";
import { resolveAdminAssetUrl } from "@/src/lib/admin-client";
import { conversationGroupOrder, getConversationGroupLabel, groupConversations, type ConversationGroupKey } from "@/src/lib/conversation-groups";
import { deleteConversation, listConversations } from "@/src/lib/phone-api";
import { formatRelativeTime } from "@/src/lib/time";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, radii, spacing } from "@/src/theme/tokens";
import type { ConversationSummary } from "@/src/types/admin";

const groupIcons: Record<ConversationGroupKey, keyof typeof MaterialCommunityIcons.glyphMap> = {
    channels: "earth",
    cron: "clock-outline",
    hooks: "lightning-bolt-outline",
    web: "message-outline",
};

export default function SessionsScreen() {
    const { status, user, adminBaseUrl, activeConversationId, setActiveConversationId, authorizedFetch, getEngineNowMs } = useAppSession();
    const { t, locale } = useUiPrefs();
    const goHomeToChat = useGoHomeToChat();
    const profileImageUri = resolveAdminAssetUrl(adminBaseUrl, user?.image || "");
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
            Alert.alert(t("src.screens.approvalsscreen.load_failed"), error instanceof Error ? error.message : t("src.screens.sessionsscreen.unable_to_load_the_conversation_list"));
        } finally {
            setRefreshing(false);
        }
    }, [authorizedFetch, t]);

    useEffect(() => {
        if (status === "authenticated") {
            void load();
        }
    }, [load, status]);

    const createNew = async () => {
        setBusy(true);
        try {
            await setActiveConversationId(null);
            router.push("/chat?new=1" as Href);
        } catch (error) {
            Alert.alert(t("src.screens.sessionsscreen.create_failed"), error instanceof Error ? error.message : t("src.screens.sessionsscreen.unable_to_create_a_new_conversation"));
        } finally {
            setBusy(false);
        }
    };

    const remove = async (item: ConversationSummary) => {
        const canonicalSessionId = item.sessionId || item.id;
        Alert.alert(t("src.screens.chatscreen.delete_conversation"), t("src.screens.chatscreen.delete_this_conversation"), [
            { text: t("src.components.chat.mediaviewerlightbox.cancel"), style: "cancel" },
            {
                text: t("src.screens.chatscreen.delete"),
                style: "destructive",
                onPress: async () => {
                    try {
                        await deleteConversation(authorizedFetch, canonicalSessionId);
                        if (activeConversationId === canonicalSessionId) {
                            await setActiveConversationId(null);
                        }
                        await load();
                    } catch (error) {
                        Alert.alert(t("src.screens.chatscreen.delete_failed"), error instanceof Error ? error.message : t("src.screens.sessionsscreen.unable_to_delete_the_conversation"));
                    }
                },
            },
        ]);
    };

    if (status === "booting") {
        return <LoadingScreen label={t("src.screens.sessionsscreen.syncing_conversations")} />;
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
                <PhoneTopbar actions={actions} userImageUri={profileImageUri || undefined} onBrandPress={() => void goHomeToChat()} />

                <ScrollView
                    contentContainerStyle={styles.content}
                    refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load()} />}
                >
                    <Pressable style={[styles.newButton, busy && styles.disabled]} onPress={() => void createNew()}>
                        <MaterialCommunityIcons name="plus" size={18} color="#FFFFFF" />
                        <Text style={styles.newButtonText}>{t("src.screens.sessionsscreen.new_chat")}</Text>
                    </Pressable>

                    {conversations.length === 0 ? (
                        <Text style={styles.emptyBody}>{t("src.screens.sessionsscreen.there_are_no_conversations_yet")}</Text>
                    ) : null}

                    {conversationGroupOrder
                        .filter((groupKey) => grouped[groupKey].length > 0)
                        .map((groupKey) => (
                            <View key={groupKey} style={styles.groupSection}>
                                <View style={styles.groupHeader}>
                                    <View style={styles.groupPill}>
                                        <MaterialCommunityIcons name={groupIcons[groupKey]} size={14} color={colors.textMuted} />
                                        <Text style={styles.groupLabel}>{getConversationGroupLabel(groupKey, locale)}</Text>
                                    </View>
                                    <Text style={styles.groupCount}>{grouped[groupKey].length}</Text>
                                </View>

                                {grouped[groupKey].map((item) => {
                                    const canonicalSessionId = item.sessionId || item.id;
                                    const active = canonicalSessionId === activeConversationId;
                                    return (
                                        <Pressable
                                            key={canonicalSessionId}
                                            onPress={async () => {
                                                await setActiveConversationId(canonicalSessionId);
                                                router.push("/chat" as Href);
                                            }}
                                        >
                                            <GlassCard>
                                                <View style={styles.itemHeader}>
                                                    <View style={[styles.itemDot, active && styles.itemDotActive]} />
                                                    <View style={styles.itemBody}>
                                                        <Text style={styles.itemTitle} numberOfLines={1}>
                                                            {item.title || t("shared.conversation.fallback_title", { id: canonicalSessionId.slice(0, 8) })}
                                                        </Text>
                                                        <Text style={styles.itemMeta}>
                                                            {formatRelativeTime(item.historySortAt || item.createdAt || "", locale, getEngineNowMs())}
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
