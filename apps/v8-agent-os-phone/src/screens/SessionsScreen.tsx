import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import * as Clipboard from "expo-clipboard";

import { GlassCard } from "@/src/components/common/GlassCard";
import { LoadingScreen } from "@/src/components/common/LoadingScreen";
import { PhoneTopbar, type PhoneTopbarAction } from "@/src/components/layout/PhoneTopbar";
import { useGoHomeToChat } from "@/src/hooks/use-go-home-to-chat";
import { getConversationActivityState, groupConversationsByWorkspace } from "@/src/lib/conversation-groups";
import { deleteConversation, listConversations } from "@/src/lib/phone-api";
import { formatRelativeTime } from "@/src/lib/time";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { buildLocalSessionIndexNamespace, localDatabase } from "@/src/services/LocalDatabaseService";
import { colors, radii, spacing } from "@/src/theme/tokens";
import type { ConversationSummary } from "@/src/types/admin";

export default function SessionsScreen() {
    const { status, user, userAvatarUri, adminBaseUrl, activeConversationId, sessionActivityVersion, setActiveConversationId, authorizedFetch, getEngineNowMs } = useAppSession();
    const { t, locale } = useUiPrefs();
    const goHomeToChat = useGoHomeToChat();
    const sessionIndexNamespace = useMemo(
        () => buildLocalSessionIndexNamespace(adminBaseUrl, user?.id || user?.email || user?.login || "local"),
        [adminBaseUrl, user?.email, user?.id, user?.login],
    );
    const [conversations, setConversations] = useState<ConversationSummary[]>([]);
    const [refreshing, setRefreshing] = useState(false);
    const [busy, setBusy] = useState(false);
    const grouped = groupConversationsByWorkspace(conversations, locale);
    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
    const suppressNextPressRef = useRef<string | null>(null);

    const actions: PhoneTopbarAction[] = [
        { key: "chat", icon: "chat-processing-outline", onPress: () => router.push("/chat" as Href) },
        { key: "connect", icon: "lan-connect", onPress: () => router.push("/connect" as Href) },
        { key: "desktop-live", icon: "monitor-dashboard", onPress: () => router.push("/desktop-live" as Href), tone: "primary" },
        { key: "rpa", icon: "robot-outline", onPress: () => router.push("/rpa" as Href), tone: "accent" },
        { key: "settings", icon: "cog-outline", onPress: () => router.push("/settings" as Href) },
    ];

    const load = useCallback(async (options?: { showSpinner?: boolean; useCache?: boolean; surfaceErrors?: boolean }) => {
        const showSpinner = options?.showSpinner !== false;
        const useCache = options?.useCache !== false;
        const surfaceErrors = options?.surfaceErrors !== false;
        if (showSpinner) setRefreshing(true);
        let hasCachedSessions = false;
        try {
            if (useCache) {
                const cached = await localDatabase.getSessionIndex<ConversationSummary>(sessionIndexNamespace);
                if (cached.length > 0) {
                    hasCachedSessions = true;
                    setConversations(cached);
                }
            }
            const next = await listConversations(authorizedFetch);
            setConversations(next);
            await localDatabase.setSessionIndex(sessionIndexNamespace, next);
        } catch (error) {
            if (surfaceErrors && !hasCachedSessions) {
                Alert.alert(t("src.screens.approvalsscreen.load_failed"), error instanceof Error ? error.message : t("src.screens.sessionsscreen.unable_to_load_the_conversation_list"));
            }
        } finally {
            if (showSpinner) setRefreshing(false);
        }
    }, [authorizedFetch, sessionIndexNamespace, t]);

    useEffect(() => {
        if (status === "authenticated") {
            void load({ showSpinner: true, useCache: true, surfaceErrors: true });
        }
    }, [load, status]);

    useEffect(() => {
        if (status !== "authenticated" || sessionActivityVersion <= 0) return;
        void load({ showSpinner: false, useCache: false, surfaceErrors: false });
    }, [load, sessionActivityVersion, status]);

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
                        await localDatabase.deleteSessionData(canonicalSessionId);
                        if (activeConversationId === canonicalSessionId) {
                            await setActiveConversationId(null);
                        }
                        const next = conversations.filter((conversation) => (conversation.sessionId || conversation.id) !== canonicalSessionId);
                        setConversations(next);
                        await localDatabase.setSessionIndex(sessionIndexNamespace, next);
                    } catch (error) {
                        Alert.alert(t("src.screens.chatscreen.delete_failed"), error instanceof Error ? error.message : t("src.screens.sessionsscreen.unable_to_delete_the_conversation"));
                    }
                },
            },
        ]);
    };

    const copySessionId = async (item: ConversationSummary) => {
        const canonicalSessionId = item.sessionId || item.id;
        if (!canonicalSessionId) return;
        await Clipboard.setStringAsync(canonicalSessionId);
        Alert.alert(t("shared.conversation.session_id_copied"), canonicalSessionId);
    };

    const continueInNewConversation = async (item: ConversationSummary) => {
        const canonicalSessionId = item.sessionId || item.id;
        if (!canonicalSessionId) return;
        await setActiveConversationId(null);
        router.push(`/chat?new=1&contextSessionId=${encodeURIComponent(canonicalSessionId)}` as Href);
    };

    const openConversationActions = (item: ConversationSummary) => {
        const canonicalSessionId = item.sessionId || item.id;
        suppressNextPressRef.current = canonicalSessionId;
        setTimeout(() => {
            if (suppressNextPressRef.current === canonicalSessionId) {
                suppressNextPressRef.current = null;
            }
        }, 900);
        Alert.alert(
            item.title || t("shared.conversation.fallback_title", { id: canonicalSessionId.slice(0, 8) }),
            t("shared.conversation.history_actions"),
            [
                { text: t("shared.conversation.continue_in_new_session"), onPress: () => void continueInNewConversation(item) },
                { text: t("shared.conversation.copy_session_id"), onPress: () => void copySessionId(item) },
                { text: t("src.screens.chatscreen.delete_conversation"), style: "destructive", onPress: () => void remove(item) },
                { text: t("src.screens.chatscreen.cancel"), style: "cancel" },
            ],
        );
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
                <PhoneTopbar actions={actions} userImageUri={userAvatarUri || undefined} onBrandPress={() => void goHomeToChat()} />

                <ScrollView
                    contentContainerStyle={styles.content}
                    refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void load({ showSpinner: true, useCache: false, surfaceErrors: true })} />}
                >
                    <Pressable style={[styles.newButton, busy && styles.disabled]} onPress={() => void createNew()}>
                        <MaterialCommunityIcons name="plus" size={18} color="#FFFFFF" />
                        <Text style={styles.newButtonText}>{t("src.screens.sessionsscreen.new_chat")}</Text>
                    </Pressable>

                    {conversations.length === 0 ? (
                        <Text style={styles.emptyBody}>{t("src.screens.sessionsscreen.there_are_no_conversations_yet")}</Text>
                    ) : null}

                    {grouped.map((group, index) => {
                        const isOpen = openGroups[group.key] ?? index === 0;
                        return (
                            <View key={group.key} style={styles.groupSection}>
                                <Pressable
                                    style={styles.groupHeader}
                                    onPress={() => setOpenGroups((current) => ({ ...current, [group.key]: !isOpen }))}
                                >
                                    <View style={styles.groupPill}>
                                        <MaterialCommunityIcons name={isOpen ? "chevron-down" : "chevron-right"} size={16} color={colors.textMuted} />
                                        <Text style={styles.groupLabel} numberOfLines={1}>{group.label}</Text>
                                    </View>
                                    <Text style={styles.groupCount}>{group.items.length}</Text>
                                </Pressable>

                                {isOpen ? group.items.map((item) => {
                                    const canonicalSessionId = item.sessionId || item.id;
                                    const active = canonicalSessionId === activeConversationId;
                                    const activityState = getConversationActivityState(item);
                                    return (
                                        <Pressable
                                            key={canonicalSessionId}
                                            onPress={async () => {
                                                if (suppressNextPressRef.current === canonicalSessionId) {
                                                    suppressNextPressRef.current = null;
                                                    return;
                                                }
                                                await setActiveConversationId(canonicalSessionId);
                                                router.push("/chat" as Href);
                                            }}
                                            onLongPress={() => openConversationActions(item)}
                                        >
                                            <GlassCard>
                                                <View style={styles.itemHeader}>
                                                    <View style={[styles.itemDot, active && styles.itemDotActive]} />
                                                    <View style={styles.itemBody}>
                                                        <View style={styles.itemTitleRow}>
                                                            <Text style={styles.itemTitle} numberOfLines={1}>
                                                                {item.title || t("shared.conversation.fallback_title", { id: canonicalSessionId.slice(0, 8) })}
                                                            </Text>
                                                            {activityState === "active" ? (
                                                                <MaterialCommunityIcons name="progress-clock" size={14} color={colors.primary} />
                                                            ) : null}
                                                            {activityState === "failed" ? (
                                                                <MaterialCommunityIcons name="alert-circle-outline" size={14} color={colors.danger} />
                                                            ) : null}
                                                        </View>
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
                                }) : null}
                            </View>
                        );
                    })}
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
        flexShrink: 1,
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
        maxWidth: 210,
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
        flex: 1,
        color: colors.text,
        fontSize: 15,
        fontWeight: "800",
    },
    itemTitleRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
    },
    itemMeta: {
        color: colors.textMuted,
        fontSize: 12,
    },
});
