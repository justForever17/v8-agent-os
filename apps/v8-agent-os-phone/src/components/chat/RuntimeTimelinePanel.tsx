import { memo, useCallback, useEffect, useMemo, useRef } from "react";
import {
    FlatList,
    Modal,
    Pressable,
    StyleSheet,
    Text,
    View,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";

import { getRuntimeDockIcon } from "@/src/components/chat/RuntimeDock";
import {
    formatPhoneRelativeRuntimeTime,
    getPhoneRuntimeDescriptor,
    type PhoneRuntimeId,
    type PhoneRuntimeStageActivity,
    type PhoneRuntimeStageCard,
} from "@/src/lib/runtime-stage";
import { ContentDispatcher } from "@/src/components/chat/ContentDispatcher";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";

function getKindTone(kind: PhoneRuntimeStageActivity["kind"], colors: ReturnType<typeof useUiPrefs>["colors"]) {
    switch (kind) {
        case "governance":
            return { label: "控制", tint: colors.danger };
        case "artifact":
            return { label: "产物", tint: colors.primaryDeep };
        case "tool":
            return { label: "工具", tint: colors.accent };
        case "handoff":
            return { label: "交接", tint: colors.warning };
        case "progress":
        default:
            return { label: "运行", tint: colors.success };
    }
}

function getKindIconName(kind: PhoneRuntimeStageActivity["kind"]): React.ComponentProps<typeof MaterialCommunityIcons>["name"] {
    switch (kind) {
        case "governance":
            return "shield-alert-outline";
        case "artifact":
            return "package-variant-closed";
        case "tool":
            return "console-line";
        case "handoff":
            return "source-branch";
        case "progress":
        default:
            return "pulse";
    }
}

function ActivityFeedItem({
    activity,
}: {
    activity: PhoneRuntimeStageActivity;
}) {
    const { colors, locale } = useUiPrefs();
    const tone = getKindTone(activity.kind, colors);
    const iconName = getKindIconName(activity.kind);

    return (
        <View
            style={[
                styles.feedCard,
                {
                    backgroundColor: colors.surfaceStrong,
                    borderColor: colors.border,
                    shadowColor: colors.text,
                },
            ]}
        >
            <View style={styles.feedMetaRow}>
                <View style={[styles.feedKindPill, { backgroundColor: colors.surface }]}>
                    <MaterialCommunityIcons name={iconName} size={12} color={tone.tint} />
                    <Text style={[styles.feedKindText, { color: tone.tint }]}>{tone.label}</Text>
                </View>
                {activity.actorLabel ? (
                    <View style={[styles.feedActorPill, { backgroundColor: colors.surface }]}>
                        <Text style={[styles.feedActorText, { color: colors.textMuted }]}>{activity.actorLabel}</Text>
                    </View>
                ) : null}
                <Text style={[styles.feedTimeText, { color: colors.textSoft }]}>
                    {formatPhoneRelativeRuntimeTime(activity.timestamp, locale)}
                </Text>
            </View>

            <Text style={[styles.feedSummary, { color: colors.text }]}>{activity.summary}</Text>

            <View style={[styles.feedBody, { backgroundColor: colors.surface, borderColor: colors.border }]}>
                <ContentDispatcher node={activity.node} />
            </View>
        </View>
    );
}

export const RuntimeTimelinePanel = memo(function RuntimeTimelinePanel({
    visible,
    items,
    selectedRuntimeId,
    selectedRuntimeDockItem,
    activities,
    currentRunLabel,
    currentStepTitle,
    onClose,
    onSelectRuntime,
}: {
    visible: boolean;
    items: PhoneRuntimeStageCard[];
    selectedRuntimeId: PhoneRuntimeId | null;
    selectedRuntimeDockItem?: PhoneRuntimeStageCard;
    activities: PhoneRuntimeStageActivity[];
    currentRunLabel: string;
    currentStepTitle?: string | null;
    onClose: () => void;
    onSelectRuntime: (runtimeId: PhoneRuntimeId) => void;
}) {
    const { colors, themeMode, t, locale } = useUiPrefs();
    const contentScrollRef = useRef<FlatList<PhoneRuntimeStageActivity> | null>(null);
    const shouldForceScrollTopRef = useRef(false);
    const effectiveSelectedRuntimeId = useMemo(
        () => (selectedRuntimeId && items.some((item) => item.id === selectedRuntimeId) ? selectedRuntimeId : (items[0]?.id ?? null)),
        [items, selectedRuntimeId],
    );
    const effectiveSelectedRuntimeDockItem = useMemo(
        () => items.find((item) => item.id === effectiveSelectedRuntimeId) || selectedRuntimeDockItem,
        [effectiveSelectedRuntimeId, items, selectedRuntimeDockItem],
    );
    const selectedDescriptor = effectiveSelectedRuntimeId ? getPhoneRuntimeDescriptor(effectiveSelectedRuntimeId, locale) : null;
    const panelGradient: [string, string] = themeMode === "dark"
        ? ["rgba(24,24,27,0.985)", "rgba(15,15,18,0.975)"]
        : ["rgba(255,255,255,0.98)", "rgba(247,244,238,0.97)"];
    const overlayColor = themeMode === "dark" ? "rgba(0,0,0,0.58)" : "rgba(15,23,42,0.38)";
    const visibleActivities = useMemo(() => activities.slice(0, 24), [activities]);
    const runtimeListKey = useMemo(
        () => `${visible ? "open" : "closed"}:${effectiveSelectedRuntimeId || "runtime"}:${visibleActivities.map((activity) => activity.id).join("|")}`,
        [effectiveSelectedRuntimeId, visible, visibleActivities],
    );
    const resetScrollTop = useCallback(() => {
        if (!visible) {
            return;
        }
        contentScrollRef.current?.scrollToOffset({ offset: 0, animated: false });
        shouldForceScrollTopRef.current = false;
    }, [visible]);

    useEffect(() => {
        if (!items.length || !effectiveSelectedRuntimeId) {
            return;
        }
        if (selectedRuntimeId !== effectiveSelectedRuntimeId) {
            onSelectRuntime(effectiveSelectedRuntimeId);
        }
    }, [effectiveSelectedRuntimeId, items.length, onSelectRuntime, selectedRuntimeId]);

    useEffect(() => {
        if (!visible) {
            return undefined;
        }
        shouldForceScrollTopRef.current = true;
        const handle = requestAnimationFrame(() => {
            resetScrollTop();
        });
        const fallback = setTimeout(() => {
            resetScrollTop();
        }, 120);
        return () => {
            cancelAnimationFrame(handle);
            clearTimeout(fallback);
        };
    }, [activities.length, effectiveSelectedRuntimeId, resetScrollTop, visible]);

    const renderActivityItem = useCallback(
        ({ item }: { item: PhoneRuntimeStageActivity }) => <ActivityFeedItem activity={item} />,
        [],
    );
    const renderEmptyState = useCallback(
        () => (
            <View style={[styles.emptyState, { borderColor: colors.border, backgroundColor: colors.surfaceStrong }]}>
                <Text style={[styles.emptyStateText, { color: colors.textMuted }]}>
                    {t("当前还没有可展示的运行记录。", "There are no runtime entries to display yet.")}
                </Text>
            </View>
        ),
        [colors.border, colors.surfaceStrong, colors.textMuted, t],
    );

    return (
        <Modal visible={visible} transparent animationType="fade" onRequestClose={onClose}>
            <View style={[styles.overlay, { backgroundColor: overlayColor }]}>
                <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
                <View style={[styles.panelCard, { borderColor: colors.border, shadowColor: colors.text }]}>
                    <LinearGradient colors={panelGradient} style={StyleSheet.absoluteFill} />
                    <View style={styles.panelInner}>
                        <View style={[styles.header, { borderBottomColor: colors.border }]}>
                            <View style={styles.headerMain}>
                                <View style={[styles.hero, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                                    {effectiveSelectedRuntimeDockItem ? (() => {
                                        const Icon = getRuntimeDockIcon(effectiveSelectedRuntimeDockItem?.id || items[0]?.id || "chat");
                                        return (
                                            <Icon
                                                size={16}
                                                color={
                                                    effectiveSelectedRuntimeDockItem?.status === "attention"
                                                        ? colors.danger
                                                        : effectiveSelectedRuntimeDockItem?.status === "active"
                                                            ? colors.warning
                                                            : colors.text
                                                }
                                                strokeWidth={2}
                                            />
                                        );
                                    })() : null}
                                </View>
                                <View style={styles.headerBody}>
                                    <Text style={[styles.title, { color: colors.text }]}>
                                        {selectedDescriptor?.label || effectiveSelectedRuntimeDockItem?.label || t("运行状态", "Runtime")}
                                    </Text>
                                    <Text style={[styles.subtitle, { color: colors.textMuted }]} numberOfLines={1}>
                                        {currentRunLabel}
                                        {currentStepTitle ? ` · ${currentStepTitle}` : ""}
                                    </Text>
                                </View>
                            </View>
                            <Pressable
                                style={[styles.closeButton, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}
                                onPress={onClose}
                            >
                                <MaterialCommunityIcons name="close" size={18} color={colors.textMuted} />
                            </Pressable>
                        </View>

                        <View style={styles.tabsWrap}>
                            <View style={styles.tabsRow}>
                                {items.map((item) => (
                                    <Pressable
                                        key={item.id}
                                        style={[
                                            styles.tabButton,
                                            { backgroundColor: colors.surfaceStrong, borderColor: colors.border },
                                            item.id === effectiveSelectedRuntimeId && {
                                                backgroundColor: themeMode === "dark" ? "rgba(245,158,11,0.10)" : "rgba(255,247,237,0.96)",
                                                borderColor: "rgba(245,158,11,0.34)",
                                                shadowColor: "#F59E0B",
                                            },
                                        ]}
                                        onPress={() => onSelectRuntime(item.id)}
                                    >
                                        {(() => {
                                            const Icon = getRuntimeDockIcon(item.id);
                                            return <Icon size={14} color={item.id === effectiveSelectedRuntimeId ? "#B45309" : colors.textMuted} strokeWidth={2} />;
                                        })()}
                                        {item.eventCount > 0 ? (
                                            <View style={[styles.tabBadge, { backgroundColor: colors.text }]}>
                                                <Text style={styles.tabBadgeText}>{Math.min(item.eventCount, 9)}</Text>
                                            </View>
                                        ) : null}
                                    </Pressable>
                                ))}
                            </View>
                        </View>

                        <FlatList
                            key={runtimeListKey}
                            ref={contentScrollRef}
                            data={visibleActivities}
                            keyExtractor={(activity) => activity.id}
                            renderItem={renderActivityItem}
                            style={styles.contentList}
                            contentContainerStyle={[
                                styles.content,
                                visibleActivities.length === 0 && styles.contentEmpty,
                            ]}
                            ItemSeparatorComponent={() => <View style={styles.feedGap} />}
                            overScrollMode="never"
                            onLayout={() => {
                                if (shouldForceScrollTopRef.current) {
                                    resetScrollTop();
                                }
                            }}
                            onContentSizeChange={() => {
                                if (shouldForceScrollTopRef.current) {
                                    resetScrollTop();
                                }
                            }}
                            ListEmptyComponent={renderEmptyState}
                        />
                    </View>
                </View>
            </View>
        </Modal>
    );
});

const styles = StyleSheet.create({
    overlay: {
        flex: 1,
        justifyContent: "center",
        paddingHorizontal: 12,
        paddingVertical: 24,
    },
    panelCard: {
        borderWidth: 1,
        borderRadius: 24,
        overflow: "hidden",
        width: "100%",
        height: "84%",
        minHeight: 360,
        maxHeight: 720,
        shadowOpacity: 0.18,
        shadowRadius: 30,
        shadowOffset: { width: 0, height: 18 },
        elevation: 22,
        maxWidth: 660,
        alignSelf: "center",
    },
    panelInner: {
        flex: 1,
        minHeight: 0,
    },
    header: {
        paddingHorizontal: 18,
        paddingVertical: 11,
        borderBottomWidth: 1,
        flexDirection: "row",
        alignItems: "flex-start",
        justifyContent: "space-between",
        gap: 12,
    },
    headerMain: {
        flexDirection: "row",
        alignItems: "center",
        gap: 12,
        flex: 1,
    },
    hero: {
        width: 36,
        height: 36,
        borderRadius: 14,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    headerBody: {
        flex: 1,
        gap: 2,
    },
    title: {
        fontSize: 15,
        fontWeight: "800",
        letterSpacing: -0.2,
    },
    subtitle: {
        fontSize: 11,
        lineHeight: 15,
    },
    closeButton: {
        width: 34,
        height: 34,
        borderRadius: 17,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    tabsWrap: {
        height: 48,
        minHeight: 48,
        maxHeight: 48,
        flexGrow: 0,
        flexShrink: 0,
        justifyContent: "center",
    },
    tabsRow: {
        flexDirection: "row",
        paddingVertical: 8,
        gap: 6,
        alignItems: "center",
        justifyContent: "center",
    },
    tabButton: {
        width: 32,
        height: 32,
        borderRadius: 12,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
        shadowOpacity: 0.12,
        shadowRadius: 12,
        shadowOffset: { width: 0, height: 4 },
    },
    tabBadge: {
        position: "absolute",
        right: -3,
        bottom: -3,
        minWidth: 14,
        height: 14,
        borderRadius: 999,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 3,
    },
    tabBadgeText: {
        color: "#FFFFFF",
        fontSize: 8,
        fontWeight: "800",
        lineHeight: 9,
    },
    contentList: {
        flex: 1,
        minHeight: 0,
    },
    content: {
        paddingHorizontal: 18,
        paddingTop: 0,
        paddingBottom: 18,
    },
    contentEmpty: {
        paddingTop: 12,
        paddingBottom: 18,
    },
    feedGap: {
        height: 12,
    },
    feedCard: {
        borderWidth: 1,
        borderRadius: 22,
        padding: 14,
        gap: 10,
        shadowOpacity: 0.06,
        shadowRadius: 20,
        shadowOffset: { width: 0, height: 10 },
    },
    feedMetaRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
    },
    feedKindPill: {
        borderRadius: radii.pill,
        paddingHorizontal: 10,
        paddingVertical: 4,
        flexDirection: "row",
        alignItems: "center",
        gap: 5,
    },
    feedKindText: {
        fontSize: 10,
        fontWeight: "800",
    },
    feedActorPill: {
        borderRadius: radii.pill,
        paddingHorizontal: 10,
        paddingVertical: 4,
    },
    feedActorText: {
        fontSize: 10,
    },
    feedTimeText: {
        marginLeft: "auto",
        fontSize: 10,
    },
    feedSummary: {
        fontSize: 13,
        lineHeight: 20,
        fontWeight: "600",
    },
    feedBody: {
        borderWidth: 1,
        borderRadius: 18,
        paddingHorizontal: 12,
        paddingVertical: 11,
    },
    emptyState: {
        borderWidth: 1,
        borderStyle: "dashed",
        borderRadius: 20,
        paddingHorizontal: 18,
        paddingVertical: 22,
    },
    emptyStateText: {
        textAlign: "center",
        fontSize: 14,
        lineHeight: 22,
    },
});
