import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Alert, Modal, Pressable, ScrollView, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import * as Clipboard from "expo-clipboard";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";

import { formatRelativeTime } from "@/src/lib/time";
import { getConversationActivityState, groupConversationsByWorkspace, type ConversationWorkspaceGroup } from "@/src/lib/conversation-groups";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { spacing } from "@/src/theme/tokens";
import type { ConversationSummary } from "@/src/types/admin";

export function HistoryDrawer({
    visible,
    items,
    groups: groupedItems,
    activeConversationId,
    loading,
    onClose,
    onSelectConversation,
    onContinueConversation,
    onNewConversation,
    onCreateConversationInGroup,
    creatingGroupKey,
    onDeleteConversation,
}: {
    visible: boolean;
    items: ConversationSummary[];
    groups?: ConversationWorkspaceGroup[];
    activeConversationId: string | null;
    loading?: boolean;
    onClose: () => void;
    onSelectConversation: (item: ConversationSummary) => void;
    onContinueConversation: (item: ConversationSummary) => void;
    onNewConversation: () => void;
    onCreateConversationInGroup: (group: ConversationWorkspaceGroup) => void;
    creatingGroupKey?: string | null;
    onDeleteConversation: (item: ConversationSummary) => void;
}) {
    const insets = useSafeAreaInsets();
    const { width } = useWindowDimensions();
    const { colors, t, locale } = useUiPrefs();
    const { getEngineNowMs } = useAppSession();
    const panelWidth = Math.min(width * 0.86, 352);
    const groups = useMemo(() => groupedItems || groupConversationsByWorkspace(items, locale), [groupedItems, items, locale]);
    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
    const suppressNextPressRef = useRef<string | null>(null);

    useEffect(() => {
        if (!visible) {
            return;
        }
        setOpenGroups((current) => {
            const next: Record<string, boolean> = {};
            groups.forEach((group, index) => {
                next[group.key] = current[group.key] ?? index === 0;
            });
            return next;
        });
    }, [groups, visible]);

    const toggleGroup = (key: string) => {
        setOpenGroups((current) => ({ ...current, [key]: !current[key] }));
    };

    const copySessionId = async (item: ConversationSummary) => {
        const canonicalSessionId = item.sessionId || item.id;
        if (!canonicalSessionId) return;
        await Clipboard.setStringAsync(canonicalSessionId);
        Alert.alert(t("shared.conversation.session_id_copied"), canonicalSessionId);
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
                { text: t("shared.conversation.continue_in_new_session"), onPress: () => onContinueConversation(item) },
                { text: t("shared.conversation.copy_session_id"), onPress: () => void copySessionId(item) },
                { text: t("src.screens.chatscreen.delete_conversation"), style: "destructive", onPress: () => onDeleteConversation(item) },
                { text: t("src.components.chat.mediaviewerlightbox.cancel"), style: "cancel" },
            ],
        );
    };

    return (
        <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
            <View style={[styles.overlay, { backgroundColor: colors.overlay }]}>
                <View style={styles.frame}>
                    <View style={[styles.panel, {
                        width: panelWidth,
                        backgroundColor: colors.surface,
                        borderRightColor: colors.border,
                        paddingTop: Math.max(insets.top + 6, 16),
                        paddingBottom: Math.max(insets.bottom + 14, 18),
                    }]}>
                        <View style={styles.header}>
                            <Pressable onPress={onNewConversation} style={styles.newChatButtonWrap}>
                                <LinearGradient
                                    colors={[colors.primary, "#7C3AED"]}
                                    start={{ x: 0, y: 0.2 }}
                                    end={{ x: 1, y: 0.8 }}
                                    style={styles.newChatButton}
                                >
                                    <MaterialCommunityIcons name="plus" size={17} color="#FFFFFF" />
                                    <Text style={styles.newChatText}>{t("src.components.layout.historydrawer.new_chat")}</Text>
                                </LinearGradient>
                            </Pressable>
                            <Pressable
                                style={[styles.collapseButton, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}
                                onPress={onClose}
                            >
                                <MaterialCommunityIcons name="dock-left" size={18} color={colors.textMuted} />
                            </Pressable>
                        </View>

                        <ScrollView contentContainerStyle={styles.scrollContent} showsVerticalScrollIndicator={false}>
                            <View style={styles.sectionHeader}>
                                <Text style={[styles.sectionLabel, { color: colors.textMuted }]}>{t("src.components.layout.historydrawer.history")}</Text>
                            </View>

                            {items.length === 0 ? (
                                <Text style={[styles.emptyText, { color: colors.textMuted }]}>
                                    {loading ? t("src.components.layout.historydrawer.syncing_history") : t("src.components.layout.historydrawer.no_history_yet")}
                                </Text>
                            ) : (
                                groups.map((group) => {
                                    const entries = group.items;
                                    const isOpen = openGroups[group.key];
                                    return (
                                        <View key={group.key} style={styles.groupWrap}>
                                            <View style={styles.groupHeader}>
                                                <Pressable
                                                    style={styles.groupToggle}
                                                    onPress={() => toggleGroup(group.key)}
                                                    accessibilityRole="button"
                                                    accessibilityState={{ expanded: Boolean(isOpen) }}
                                                >
                                                    <MaterialCommunityIcons
                                                        name={isOpen ? "chevron-down" : "chevron-right"}
                                                        size={16}
                                                        color={colors.textMuted}
                                                    />
                                                    <Text style={[styles.groupTitle, { color: colors.textMuted }]} numberOfLines={1}>{group.label}</Text>
                                                </Pressable>
                                                {group.creationBinding ? (
                                                    <Pressable
                                                        style={styles.groupCreateButton}
                                                        onPress={() => onCreateConversationInGroup(group)}
                                                        disabled={Boolean(creatingGroupKey)}
                                                        accessibilityRole="button"
                                                        accessibilityLabel={t("src.components.layout.historydrawer.create_in_workspace", { value0: group.label })}
                                                        hitSlop={4}
                                                    >
                                                        {creatingGroupKey === group.key ? (
                                                            <ActivityIndicator size="small" color={colors.primary} />
                                                        ) : (
                                                            <MaterialCommunityIcons name="plus" size={19} color={colors.textMuted} />
                                                        )}
                                                    </Pressable>
                                                ) : null}
                                            </View>

                                            {isOpen ? (
                                                <View style={styles.items}>
                                                    {entries.map((item) => {
                                                        const canonicalSessionId = item.sessionId || item.id;
                                                        const active = canonicalSessionId === activeConversationId;
                                                        const activityState = getConversationActivityState(item);
                                                    return (
                                                        <Pressable
                                                            key={canonicalSessionId}
                                                            style={[
                                                                styles.item,
                                                                { backgroundColor: active ? colors.primarySoft : "transparent" },
                                                            ]}
                                                            onPress={() => {
                                                                if (suppressNextPressRef.current === canonicalSessionId) {
                                                                    suppressNextPressRef.current = null;
                                                                    return;
                                                                }
                                                                onSelectConversation(item);
                                                            }}
                                                            onLongPress={() => openConversationActions(item)}
                                                        >
                                                            <MaterialCommunityIcons
                                                                name="message-outline"
                                                                size={15}
                                                                color={active ? colors.primary : colors.textMuted}
                                                            />
                                                            <View style={styles.itemBody}>
                                                                <View style={styles.itemTitleRow}>
                                                                    <Text style={[styles.itemTitle, { color: active ? colors.primaryDeep : colors.text, flex: 1 }]} numberOfLines={1}>
                                                                        {item.title || t("shared.conversation.fallback_title", { id: canonicalSessionId.slice(0, 8) })}
                                                                    </Text>
                                                                    {activityState === "active" ? (
                                                                        <ActivityIndicator size="small" color={colors.primary} style={styles.itemTitleSpinner} />
                                                                    ) : null}
                                                                    {activityState === "failed" ? (
                                                                        <MaterialCommunityIcons name="alert-circle-outline" size={14} color={colors.danger} style={styles.itemTitleError} />
                                                                    ) : null}
                                                                </View>
                                                                <Text style={[styles.itemMeta, { color: colors.textMuted }]} numberOfLines={1}>
                                                                    {formatRelativeTime(item.historySortAt || item.createdAt || "", locale, getEngineNowMs())}
                                                                </Text>
                                                            </View>
                                                            <Pressable
                                                                hitSlop={8}
                                                                style={styles.deleteButton}
                                                                onPress={(event) => {
                                                                    event.stopPropagation();
                                                                    onDeleteConversation(item);
                                                                }}
                                                            >
                                                                <MaterialCommunityIcons name="trash-can-outline" size={15} color={colors.textSoft} />
                                                            </Pressable>
                                                        </Pressable>
                                                    );
                                                    })}
                                                </View>
                                            ) : null}
                                        </View>
                                    );
                                })
                            )}
                        </ScrollView>

                    </View>
                    <Pressable style={styles.backdrop} onPress={onClose} />
                </View>
            </View>
        </Modal>
    );
}

const styles = StyleSheet.create({
    overlay: {
        flex: 1,
    },
    frame: {
        flex: 1,
        flexDirection: "row",
    },
    panel: {
        borderRightWidth: StyleSheet.hairlineWidth,
        paddingHorizontal: 12,
        shadowColor: "#0F172A",
        shadowOpacity: 0.16,
        shadowRadius: 22,
        shadowOffset: { width: 6, height: 0 },
        elevation: 6,
    },
    backdrop: {
        flex: 1,
    },
    header: {
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
        marginBottom: 12,
    },
    newChatButtonWrap: {
        flex: 1,
        borderRadius: 18,
        overflow: "hidden",
        shadowColor: "#7C3AED",
        shadowOpacity: 0.22,
        shadowRadius: 16,
        shadowOffset: { width: 0, height: 8 },
        elevation: 4,
    },
    newChatButton: {
        minHeight: 44,
        borderRadius: 18,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
    },
    newChatText: {
        color: "#FFFFFF",
        fontSize: 14,
        fontWeight: "800",
    },
    collapseButton: {
        width: 44,
        height: 44,
        borderRadius: 18,
        alignItems: "center",
        justifyContent: "center",
        borderWidth: 1,
    },
    scrollContent: {
        gap: 8,
        paddingBottom: spacing.lg,
    },
    sectionHeader: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        paddingHorizontal: 4,
        marginBottom: 4,
    },
    sectionLabel: {
        fontSize: 11,
        fontWeight: "900",
        letterSpacing: 0.8,
        textTransform: "uppercase",
    },
    emptyText: {
        fontSize: 13,
        lineHeight: 18,
        paddingHorizontal: 4,
        paddingVertical: 10,
    },
    groupWrap: {
        marginBottom: 6,
    },
    groupHeader: {
        flexDirection: "row",
        alignItems: "center",
        borderRadius: 12,
        paddingLeft: 10,
        minHeight: 44,
    },
    groupToggle: {
        minHeight: 44,
        flex: 1,
        minWidth: 0,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
    },
    groupTitle: {
        fontSize: 12,
        fontWeight: "800",
        flex: 1,
    },
    groupCreateButton: {
        width: 44,
        height: 44,
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 14,
    },
    items: {
        gap: 4,
        paddingTop: 4,
    },
    item: {
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
        borderRadius: 16,
        paddingHorizontal: 12,
        paddingVertical: 10,
    },
    itemBody: {
        flex: 1,
        gap: 2,
    },
    itemTitle: {
        fontSize: 14,
        fontWeight: "800",
    },
    itemMeta: {
        fontSize: 11,
        marginTop: 2,
    },
    deleteButton: {
        width: 28,
        height: 28,
        borderRadius: 14,
        alignItems: "center",
        justifyContent: "center",
        opacity: 0.82,
    },
    itemTitleRow: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 6,
    },
    itemTitleSpinner: {
        marginLeft: 4,
    },
    itemTitleError: {
        marginLeft: 4,
    },
});
