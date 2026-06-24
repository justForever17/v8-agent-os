import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { LinearGradient } from "expo-linear-gradient";

import { formatRelativeTime } from "@/src/lib/time";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type { ConversationSummary } from "@/src/types/admin";

type GroupKey = "channels" | "cron" | "hooks" | "web";

const GROUP_ORDER: Array<{ key: GroupKey; icon: keyof typeof MaterialCommunityIcons.glyphMap; labelKey: string }> = [
    { key: "channels", icon: "web", labelKey: "shared.history.group.channels" },
    { key: "cron", icon: "clock-time-four-outline", labelKey: "shared.history.group.cron" },
    { key: "hooks", icon: "lightning-bolt-outline", labelKey: "shared.history.group.hooks" },
    { key: "web", icon: "message-outline", labelKey: "shared.history.group.web" },
];

function groupConversations(items: ConversationSummary[]) {
    const groups: Record<GroupKey, ConversationSummary[]> = {
        channels: [],
        cron: [],
        hooks: [],
        web: [],
    };
    for (const item of items) {
        const key = item.sourceGroup === "cron"
            ? "cron"
            : item.sourceGroup === "hooks"
                ? "hooks"
                : item.sourceGroup === "channels"
                    ? "channels"
                    : "web";
        groups[key].push(item);
    }
    return groups;
}

export function HistoryDrawer({
    visible,
    items,
    groups: groupedItems,
    activeConversationId,
    loading,
    onClose,
    onSelectConversation,
    onNewConversation,
    onDeleteConversation,
}: {
    visible: boolean;
    items: ConversationSummary[];
    groups?: Record<GroupKey, ConversationSummary[]>;
    activeConversationId: string | null;
    loading?: boolean;
    onClose: () => void;
    onSelectConversation: (item: ConversationSummary) => void;
    onNewConversation: () => void;
    onDeleteConversation: (item: ConversationSummary) => void;
}) {
    const insets = useSafeAreaInsets();
    const { width } = useWindowDimensions();
    const { colors, t, locale } = useUiPrefs();
    const { getEngineNowMs } = useAppSession();
    const panelWidth = Math.min(width * 0.86, 352);
    const groups = useMemo(() => groupedItems || groupConversations(items), [groupedItems, items]);
    const [openGroups, setOpenGroups] = useState<Record<GroupKey, boolean>>({
        channels: true,
        cron: false,
        hooks: false,
        web: true,
    });

    useEffect(() => {
        if (!visible) {
            return;
        }
        setOpenGroups((current) => ({
            channels: groups.channels.length > 0 ? current.channels : false,
            cron: groups.cron.length > 0 ? current.cron : false,
            hooks: groups.hooks.length > 0 ? current.hooks : false,
            web: groups.web.length > 0 ? current.web : false,
        }));
    }, [groups.channels.length, groups.cron.length, groups.hooks.length, groups.web.length, visible]);

    const toggleGroup = (key: GroupKey) => {
        setOpenGroups((current) => ({ ...current, [key]: !current[key] }));
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
                                <Pressable onPress={onClose}>
                                    <MaterialCommunityIcons name="trash-can-outline" size={16} color={colors.textSoft} />
                                </Pressable>
                            </View>

                            {items.length === 0 ? (
                                <Text style={[styles.emptyText, { color: colors.textMuted }]}>
                                    {loading ? t("src.components.layout.historydrawer.syncing_history") : t("src.components.layout.historydrawer.no_history_yet")}
                                </Text>
                            ) : (
                                GROUP_ORDER.map((group) => {
                                    const entries = groups[group.key];
                                    if (entries.length === 0) return null;
                                    const isOpen = openGroups[group.key];
                                    return (
                                        <View key={group.key} style={styles.groupWrap}>
                                            <Pressable
                                                style={styles.groupHeader}
                                                onPress={() => toggleGroup(group.key)}
                                            >
                                                <MaterialCommunityIcons
                                                    name={isOpen ? "chevron-down" : "chevron-right"}
                                                    size={16}
                                                    color={colors.textMuted}
                                                />
                                                <MaterialCommunityIcons name={group.icon} size={14} color={colors.textMuted} />
                                                <Text style={[styles.groupTitle, { color: colors.textMuted }]}>{t(group.labelKey)}</Text>
                                                <View style={[styles.groupCountPill, { backgroundColor: `${colors.primary}14` }]}>
                                                    <Text style={[styles.groupCount, { color: colors.textSoft }]}>{entries.length}</Text>
                                                </View>
                                            </Pressable>

                                            {isOpen ? (
                                                <View style={styles.items}>
                                                    {entries.map((item) => {
                                                        const canonicalSessionId = item.sessionId || item.id;
                                                        const active = canonicalSessionId === activeConversationId;
                                                    return (
                                                        <Pressable
                                                            key={canonicalSessionId}
                                                            style={[
                                                                styles.item,
                                                                { backgroundColor: active ? colors.primarySoft : "transparent" },
                                                            ]}
                                                            onPress={() => onSelectConversation(item)}
                                                        >
                                                            <MaterialCommunityIcons
                                                                name={group.icon}
                                                                size={15}
                                                                color={active ? colors.primary : colors.textMuted}
                                                            />
                                                            <View style={styles.itemBody}>
                                                                <View style={styles.itemTitleRow}>
                                                                    <Text style={[styles.itemTitle, { color: active ? colors.primaryDeep : colors.text, flex: 1 }]} numberOfLines={1}>
                                                                        {item.title || t("shared.conversation.fallback_title", { id: canonicalSessionId.slice(0, 8) })}
                                                                    </Text>
                                                                    {(() => {
                                                                        const activeStatus = String(item.workflowStatus || "").trim().toLowerCase();
                                                                        const isRunning = ["running", "queued", "pending", "starting", "streaming", "waiting_input", "waiting_approval"].includes(activeStatus);
                                                                        const isFailed = ["failed", "cancelled"].includes(activeStatus);
                                                                        if (isRunning) {
                                                                            return <ActivityIndicator size="small" color={colors.primary} style={styles.itemTitleSpinner} />;
                                                                        } else if (isFailed) {
                                                                            return <MaterialCommunityIcons name="alert-circle" size={14} color={colors.danger} style={styles.itemTitleError} />;
                                                                        }
                                                                        return null;
                                                                    })()}
                                                                </View>
                                                                {(item.ownerRuntime || item.workflowStatus || Number(item.pendingApprovalCount || 0) > 0 || item.recoverable) ? (
                                                                    <View style={styles.badgeRow}>
                                                                        {item.ownerRuntime ? (
                                                                            <View style={[styles.metaBadge, { backgroundColor: "rgba(16,185,129,0.10)" }]}>
                                                                                <Text style={[styles.metaBadgeText, { color: "#047857" }]}>{item.ownerRuntime}</Text>
                                                                            </View>
                                                                        ) : null}
                                                                        {(() => {
                                                                            const activeStatus = String(item.workflowStatus || "").trim().toLowerCase();
                                                                            const isRunning = ["running", "queued", "pending", "starting", "streaming", "waiting_input", "waiting_approval"].includes(activeStatus);
                                                                            const isFailed = ["failed", "cancelled"].includes(activeStatus);
                                                                            const isCompleted = ["completed", "idle"].includes(activeStatus);
                                                                            if (item.workflowStatus && !isRunning && !isFailed && !isCompleted) {
                                                                                return (
                                                                                    <View style={[styles.metaBadge, { backgroundColor: "rgba(245,158,11,0.10)" }]}>
                                                                                        <Text style={[styles.metaBadgeText, { color: "#B45309" }]}>{item.statusLabel || item.workflowStatus}</Text>
                                                                                    </View>
                                                                                );
                                                                            }
                                                                            return null;
                                                                        })()}
                                                                        {item.recoverable ? (
                                                                            <View style={[styles.metaBadge, { backgroundColor: "rgba(14,165,233,0.10)" }]}>
                                                                                <Text style={[styles.metaBadgeText, { color: "#0369A1" }]}>{t("src.components.layout.historydrawer.recoverable")}</Text>
                                                                            </View>
                                                                        ) : null}
                                                                        {Number(item.pendingApprovalCount || 0) > 0 ? (
                                                                            <View style={[styles.metaBadge, { backgroundColor: "rgba(168,85,247,0.10)" }]}>
                                                                                <Text style={[styles.metaBadgeText, { color: "#7C3AED" }]}>
                                                                                    {t("src.components.layout.historydrawer.approval")} {Number(item.pendingApprovalCount || 0)}
                                                                                </Text>
                                                                            </View>
                                                                        ) : null}
                                                                    </View>
                                                                ) : null}
                                                                {item.currentStepTitle || item.previewExcerpt ? (
                                                                    <Text style={[styles.itemExcerpt, { color: colors.textMuted }]} numberOfLines={2}>
                                                                        {item.currentStepTitle || item.previewExcerpt}
                                                                    </Text>
                                                                ) : null}
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
        gap: 6,
        borderRadius: 12,
        paddingHorizontal: 10,
        paddingVertical: 8,
    },
    groupTitle: {
        fontSize: 12,
        fontWeight: "800",
        flex: 1,
    },
    groupCountPill: {
        minWidth: 24,
        height: 18,
        borderRadius: 999,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 6,
    },
    groupCount: {
        fontSize: 10,
        fontWeight: "800",
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
    badgeRow: {
        flexDirection: "row",
        flexWrap: "wrap",
        gap: 4,
    },
    metaBadge: {
        borderRadius: radii.pill,
        paddingHorizontal: 6,
        paddingVertical: 2,
    },
    metaBadgeText: {
        fontSize: 10,
        fontWeight: "700",
    },
    itemExcerpt: {
        fontSize: 11,
        lineHeight: 16,
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
