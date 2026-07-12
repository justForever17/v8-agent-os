import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Alert, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View, useWindowDimensions } from "react-native";
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
    onUpdateConversationPresentation,
    onUpdateWorkspacePresentation,
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
    onUpdateConversationPresentation: (item: ConversationSummary, patch: { title?: string; pinned?: boolean }) => Promise<boolean>;
    onUpdateWorkspacePresentation: (group: ConversationWorkspaceGroup, patch: { displayName?: string; pinned?: boolean }) => Promise<boolean>;
    onDeleteConversation: (item: ConversationSummary) => void;
}) {
    const insets = useSafeAreaInsets();
    const { width } = useWindowDimensions();
    const { colors, t, locale } = useUiPrefs();
    const { getEngineNowMs } = useAppSession();
    const panelWidth = Math.min(width * 0.86, 352);
    const groups = useMemo(() => groupedItems || groupConversationsByWorkspace(items, locale), [groupedItems, items, locale]);
    const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
    const [editingSessionId, setEditingSessionId] = useState<string | null>(null);
    const [sessionTitleDraft, setSessionTitleDraft] = useState("");
    const [editingGroupKey, setEditingGroupKey] = useState<string | null>(null);
    const [groupNameDraft, setGroupNameDraft] = useState("");
    const [presentationBusyKey, setPresentationBusyKey] = useState<string | null>(null);
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
                {
                    text: t("shared.conversation.rename_task"),
                    onPress: () => {
                        setSessionTitleDraft(item.title || "");
                        setEditingSessionId(canonicalSessionId);
                    },
                },
                {
                    text: t(item.pinned ? "shared.conversation.unpin_task" : "shared.conversation.pin_task"),
                    onPress: () => void toggleConversationPin(item),
                },
                { text: t("shared.conversation.continue_in_new_session"), onPress: () => onContinueConversation(item) },
                { text: t("shared.conversation.copy_session_id"), onPress: () => void copySessionId(item) },
                { text: t("src.screens.chatscreen.delete_conversation"), style: "destructive", onPress: () => onDeleteConversation(item) },
                { text: t("src.components.chat.mediaviewerlightbox.cancel"), style: "cancel" },
            ],
        );
    };

    const openWorkspaceActions = (group: ConversationWorkspaceGroup) => {
        if (!group.workspacePath) return;
        Alert.alert(
            group.label,
            t("shared.workspace.project_actions"),
            [
                {
                    text: t("shared.workspace.rename_project"),
                    onPress: () => {
                        setGroupNameDraft(group.label);
                        setEditingGroupKey(group.key);
                    },
                },
                {
                    text: t(group.pinned ? "shared.workspace.unpin_project" : "shared.workspace.pin_project"),
                    onPress: () => void toggleWorkspacePin(group),
                },
                { text: t("src.components.chat.mediaviewerlightbox.cancel"), style: "cancel" },
            ],
        );
    };

    const saveConversationRename = async (item: ConversationSummary) => {
        const canonicalSessionId = item.sessionId || item.id;
        const title = sessionTitleDraft.trim();
        if (!title || title === item.title) {
            setEditingSessionId(null);
            return;
        }
        setPresentationBusyKey(`session:${canonicalSessionId}`);
        const saved = await onUpdateConversationPresentation(item, { title });
        setPresentationBusyKey(null);
        if (saved) setEditingSessionId(null);
    };

    const toggleConversationPin = async (item: ConversationSummary) => {
        const canonicalSessionId = item.sessionId || item.id;
        if (presentationBusyKey) return;
        setPresentationBusyKey(`session:${canonicalSessionId}`);
        await onUpdateConversationPresentation(item, { pinned: !item.pinned });
        setPresentationBusyKey(null);
    };

    const saveWorkspaceRename = async (group: ConversationWorkspaceGroup) => {
        const displayName = groupNameDraft.trim();
        if (!displayName || displayName === group.label) {
            setEditingGroupKey(null);
            return;
        }
        setPresentationBusyKey(`group:${group.key}`);
        const saved = await onUpdateWorkspacePresentation(group, { displayName });
        setPresentationBusyKey(null);
        if (saved) setEditingGroupKey(null);
    };

    const toggleWorkspacePin = async (group: ConversationWorkspaceGroup) => {
        if (presentationBusyKey) return;
        setPresentationBusyKey(`group:${group.key}`);
        await onUpdateWorkspacePresentation(group, { pinned: !group.pinned });
        setPresentationBusyKey(null);
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
                                                    onLongPress={() => openWorkspaceActions(group)}
                                                    accessibilityRole="button"
                                                    accessibilityState={{ expanded: Boolean(isOpen) }}
                                                >
                                                    <MaterialCommunityIcons
                                                        name={isOpen ? "chevron-down" : "chevron-right"}
                                                        size={16}
                                                        color={colors.textMuted}
                                                    />
                                                    {editingGroupKey === group.key ? (
                                                        <TextInput
                                                            autoFocus
                                                            value={groupNameDraft}
                                                            maxLength={80}
                                                            returnKeyType="done"
                                                            selectTextOnFocus
                                                            style={[styles.inlineInput, styles.groupInlineInput, { color: colors.text, borderColor: colors.primary, backgroundColor: colors.surfaceStrong }]}
                                                            onChangeText={setGroupNameDraft}
                                                            onSubmitEditing={() => void saveWorkspaceRename(group)}
                                                            onBlur={() => setEditingGroupKey(null)}
                                                            accessibilityLabel={t("shared.workspace.rename_project")}
                                                        />
                                                    ) : (
                                                        <Text style={[styles.groupTitle, { color: colors.textMuted }]} numberOfLines={1}>{group.label}</Text>
                                                    )}
                                                </Pressable>
                                                {group.workspacePath ? (
                                                    <Pressable
                                                        style={styles.groupCreateButton}
                                                        onPress={() => void toggleWorkspacePin(group)}
                                                        disabled={Boolean(presentationBusyKey)}
                                                        accessibilityRole="button"
                                                        accessibilityState={{ selected: group.pinned, disabled: Boolean(presentationBusyKey) }}
                                                        accessibilityLabel={t(group.pinned ? "shared.workspace.unpin_project" : "shared.workspace.pin_project")}
                                                        hitSlop={4}
                                                    >
                                                        {presentationBusyKey === `group:${group.key}` ? (
                                                            <ActivityIndicator size="small" color={colors.primary} />
                                                        ) : (
                                                            <MaterialCommunityIcons
                                                                name={group.pinned ? "pin" : "pin-outline"}
                                                                size={19}
                                                                color={group.pinned ? colors.primary : colors.textMuted}
                                                            />
                                                        )}
                                                    </Pressable>
                                                ) : null}
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
                                                                if (editingSessionId === canonicalSessionId) {
                                                                    return;
                                                                }
                                                                if (suppressNextPressRef.current === canonicalSessionId) {
                                                                    suppressNextPressRef.current = null;
                                                                    return;
                                                                }
                                                                onSelectConversation(item);
                                                            }}
                                                            onLongPress={() => editingSessionId !== canonicalSessionId && openConversationActions(item)}
                                                        >
                                                            <MaterialCommunityIcons
                                                                name="message-outline"
                                                                size={15}
                                                                color={active ? colors.primary : colors.textMuted}
                                                            />
                                                            <View style={styles.itemBody}>
                                                                <View style={styles.itemTitleRow}>
                                                                    {editingSessionId === canonicalSessionId ? (
                                                                        <TextInput
                                                                            autoFocus
                                                                            value={sessionTitleDraft}
                                                                            maxLength={80}
                                                                            returnKeyType="done"
                                                                            selectTextOnFocus
                                                                            style={[styles.inlineInput, { color: colors.text, borderColor: colors.primary, backgroundColor: colors.surfaceStrong }]}
                                                                            onChangeText={setSessionTitleDraft}
                                                                            onSubmitEditing={() => void saveConversationRename(item)}
                                                                            onBlur={() => setEditingSessionId(null)}
                                                                            accessibilityLabel={t("shared.conversation.rename_task")}
                                                                        />
                                                                    ) : (
                                                                        <Text style={[styles.itemTitle, { color: active ? colors.primaryDeep : colors.text, flex: 1 }]} numberOfLines={1}>
                                                                            {item.title || t("shared.conversation.fallback_title", { id: canonicalSessionId.slice(0, 8) })}
                                                                        </Text>
                                                                    )}
                                                                    {item.pinned && editingSessionId !== canonicalSessionId ? (
                                                                        <MaterialCommunityIcons name="pin" size={14} color={colors.primary} />
                                                                    ) : null}
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
    inlineInput: {
        flex: 1,
        minHeight: 36,
        borderWidth: 1,
        borderRadius: 10,
        paddingHorizontal: 10,
        paddingVertical: 6,
        fontSize: 14,
        fontWeight: "800",
    },
    groupInlineInput: {
        minHeight: 34,
        fontSize: 12,
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
