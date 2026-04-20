import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { ScrollView as GestureScrollView } from "react-native-gesture-handler";

import { getRuntimeDockIcon } from "@/src/components/chat/RuntimeDock";
import {
    formatPhoneRelativeRuntimeTime,
    getPhoneRuntimeDescriptor,
    type PhoneRuntimeId,
    type PhoneRuntimeStageActivity,
    type PhoneRuntimeStageCard,
} from "@/src/lib/runtime-stage";
import { ContentDispatcher } from "@/src/components/chat/ContentDispatcher";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";
import { type AdminProcessRef } from "@v8/session-realtime";

function getKindTone(kind: PhoneRuntimeStageActivity["kind"], colors: ReturnType<typeof useUiPrefs>["colors"]) {
    switch (kind) {
        case "governance":
            return { labelKey: "src.components.chat.runtimetimelinepanel.kind_control" as const, tint: colors.danger };
        case "artifact":
            return { labelKey: "src.components.chat.runtimetimelinepanel.kind_artifact" as const, tint: colors.primaryDeep };
        case "tool":
            return { labelKey: "src.components.chat.runtimetimelinepanel.kind_tool" as const, tint: colors.accent };
        case "handoff":
            return { labelKey: "src.components.chat.runtimetimelinepanel.kind_handoff" as const, tint: colors.warning };
        case "progress":
        default:
            return { labelKey: "src.components.chat.runtimetimelinepanel.kind_progress" as const, tint: colors.success };
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

function BroadcastRail({ activities }: { activities: PhoneRuntimeStageActivity[] }) {
    const { colors, t, locale } = useUiPrefs();
    const { getEngineNowMs } = useAppSession();
    const [index, setIndex] = useState(0);

    useEffect(() => {
        setIndex(0);
    }, [activities]);

    useEffect(() => {
        if (activities.length <= 1) {
            return undefined;
        }
        const timer = setInterval(() => {
            setIndex((current) => (current + 1) % activities.length);
        }, 2600);
        return () => clearInterval(timer);
    }, [activities.length]);

    if (activities.length === 0) {
        return null;
    }

    const active = activities[Math.min(index, activities.length - 1)] || activities[0];
    const tone = getKindTone(active.kind, colors);
    const iconName = getKindIconName(active.kind);
    const queue = activities.slice(0, 3);

    return (
        <LinearGradient
            colors={["#151515", "#1F1B17"]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={styles.broadcastCard}
        >
            <View style={styles.broadcastHeader}>
                <View style={styles.broadcastLive}>
                    <View style={styles.broadcastDotWrap}>
                        <View style={styles.broadcastDotPulse} />
                        <View style={[styles.broadcastDot, { backgroundColor: "#FBBF24" }]} />
                    </View>
                    <Text style={styles.broadcastLabel}>{t("src.components.chat.runtimetimelinepanel.broadcast")}</Text>
                </View>
                <Text style={styles.broadcastCount}>
                    {index + 1}/{activities.length}
                </Text>
            </View>
            <View style={styles.broadcastHero}>
                <View style={styles.broadcastBody}>
                    <View style={styles.broadcastIcon}>
                        <MaterialCommunityIcons name={iconName} size={16} color={tone.tint} />
                    </View>
                    <View style={styles.broadcastTextWrap}>
                        <Text style={styles.broadcastTitle} numberOfLines={2}>
                            {active.summary || t("src.components.chat.runtimetimelinepanel.runtime_activity_is_updating")}
                        </Text>
                        <Text style={styles.broadcastSubtitle} numberOfLines={1}>
                            {(active.actorLabel || t(tone.labelKey))} · {formatPhoneRelativeRuntimeTime(active.timestamp, locale, getEngineNowMs())}
                        </Text>
                    </View>
                </View>
                <Text style={styles.broadcastTopic} numberOfLines={3}>
                    {active.topic || t("src.components.chat.runtimetimelinepanel.runtime_activity_is_streaming_here_while_you_stay_in_chat")}
                </Text>
            </View>
            {queue.length > 1 ? (
                <View style={styles.broadcastQueue}>
                    {queue.map((item, itemIndex) => {
                        const itemTone = getKindTone(item.kind, colors);
                        const itemActive = item.id === active.id || itemIndex === index;
                        return (
                            <View
                                key={item.id}
                                style={[
                                    styles.broadcastQueueItem,
                                    itemActive && styles.broadcastQueueItemActive,
                                ]}
                            >
                                <MaterialCommunityIcons
                                    name={getKindIconName(item.kind)}
                                    size={12}
                                    color={itemTone.tint}
                                />
                                <Text style={styles.broadcastQueueText} numberOfLines={1}>
                                    {item.summary}
                                </Text>
                            </View>
                        );
                    })}
                </View>
            ) : null}
            <View style={styles.broadcastFooter}>
                <Text style={styles.broadcastFooterText}>{t("src.components.chat.runtimetimelinepanel.now_broadcasting")}</Text>
            </View>
        </LinearGradient>
    );
}

function ActivityFeedItem({
    activity,
    processes,
}: {
    activity: PhoneRuntimeStageActivity;
    processes: AdminProcessRef[];
}) {
    const { colors, locale, t } = useUiPrefs();
    const { getEngineNowMs } = useAppSession();
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
                    <Text style={[styles.feedKindText, { color: tone.tint }]}>{t(tone.labelKey)}</Text>
                </View>
                {activity.actorLabel ? (
                    <View style={[styles.feedActorPill, { backgroundColor: colors.surface }]}>
                        <Text style={[styles.feedActorText, { color: colors.textMuted }]}>{activity.actorLabel}</Text>
                    </View>
                ) : null}
                <Text style={[styles.feedTimeText, { color: colors.textSoft }]}>
                    {formatPhoneRelativeRuntimeTime(activity.timestamp, locale, getEngineNowMs())}
                </Text>
            </View>

            <Text style={[styles.feedSummary, { color: colors.text }]}>{activity.summary}</Text>

            <View style={[styles.feedBody, { backgroundColor: colors.surface, borderColor: colors.border }]}>
                <ContentDispatcher node={activity.node} processes={processes} />
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
    processes,
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
    processes: AdminProcessRef[];
    currentRunLabel: string;
    currentStepTitle?: string | null;
    onClose: () => void;
    onSelectRuntime: (runtimeId: PhoneRuntimeId) => void;
}) {
    const { colors, themeMode, t, locale } = useUiPrefs();
    const contentScrollRef = useRef<FlatList<PhoneRuntimeStageActivity> | null>(null);
    const runtimeTabsScrollRef = useRef<GestureScrollView | null>(null);
    const shouldForceScrollTopRef = useRef(false);
    const [tabsContainerWidth, setTabsContainerWidth] = useState(0);
    const [tabsContentWidth, setTabsContentWidth] = useState(0);
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
    const tabsOverflow = tabsContainerWidth > 0 && tabsContentWidth > tabsContainerWidth + 2;
    const visibleActivities = useMemo(() => {
        if (!effectiveSelectedRuntimeId) {
            return [];
        }
        return activities
            .filter((activity) => {
                if (activity.runtimeId !== effectiveSelectedRuntimeId) {
                    return false;
                }
                if (effectiveSelectedRuntimeId === "context_governance") {
                    return true;
                }
                const nodeTopic = "topic" in activity.node ? activity.node.topic : undefined;
                const topic = String(activity.topic || nodeTopic || "").trim().toLowerCase();
                const isGovernanceNode = activity.node.kind === "governance";
                const governanceType = isGovernanceNode
                    ? String((activity.node as Extract<typeof activity.node, { kind: "governance" }>).governanceType || "").trim().toLowerCase()
                    : "";
                if (topic.startsWith("context.") || topic === "context_governance_changed") {
                    return false;
                }
                if (governanceType === "context_governance") {
                    return false;
                }
                return true;
            })
            .slice(0, 24);
    }, [activities, effectiveSelectedRuntimeId]);
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
        if (!tabsOverflow) {
            return;
        }
        const selectedIndex = items.findIndex((item) => item.id === effectiveSelectedRuntimeId);
        if (selectedIndex < 0) {
            return;
        }
        const estimatedItemWidth = 44;
        const estimatedOffset = Math.max(0, selectedIndex * estimatedItemWidth - 36);
        requestAnimationFrame(() => {
            runtimeTabsScrollRef.current?.scrollTo({ x: estimatedOffset, animated: true });
        });
    }, [effectiveSelectedRuntimeId, items, tabsOverflow]);

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
        ({ item }: { item: PhoneRuntimeStageActivity }) => <ActivityFeedItem activity={item} processes={processes} />,
        [processes],
    );
    const renderEmptyState = useCallback(
        () => (
            <View style={[styles.emptyState, { borderColor: colors.border, backgroundColor: colors.surfaceStrong }]}>
                <Text style={[styles.emptyStateText, { color: colors.textMuted }]}>
                    {effectiveSelectedRuntimeId === "context_governance"
                        ? t("src.components.chat.runtimetimelinepanel.there_are_no_context_governance_entries_for_this_session_yet")
                        : t("src.components.chat.runtimetimelinepanel.there_are_no_runtime_entries_to_display_yet")}
                </Text>
            </View>
        ),
        [colors.border, colors.surfaceStrong, colors.textMuted, effectiveSelectedRuntimeId, t],
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
                                        {selectedDescriptor?.label || effectiveSelectedRuntimeDockItem?.label || t("src.components.chat.runtimetimelinepanel.runtime")}
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

                        <View
                            style={styles.tabsWrap}
                            onLayout={(event) => {
                                const nextWidth = Math.round(event.nativeEvent.layout.width);
                                if (nextWidth !== tabsContainerWidth) {
                                    setTabsContainerWidth(nextWidth);
                                }
                            }}
                        >
                            {tabsOverflow ? (
                                <GestureScrollView
                                    ref={runtimeTabsScrollRef}
                                    horizontal
                                    scrollEnabled
                                    nestedScrollEnabled
                                    directionalLockEnabled
                                    showsHorizontalScrollIndicator={false}
                                    keyboardShouldPersistTaps="handled"
                                    overScrollMode="never"
                                    scrollEventThrottle={16}
                                    contentContainerStyle={[styles.tabsRow, styles.tabsRowScrollable]}
                                    onContentSizeChange={(width) => {
                                        const nextWidth = Math.round(width);
                                        if (nextWidth !== tabsContentWidth) {
                                            setTabsContentWidth(nextWidth);
                                        }
                                    }}
                                >
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
                                </GestureScrollView>
                            ) : (
                                <View
                                    style={[styles.tabsRow, styles.tabsRowCentered]}
                                    onLayout={(event) => {
                                        const nextWidth = Math.round(event.nativeEvent.layout.width);
                                        if (nextWidth !== tabsContentWidth) {
                                            setTabsContentWidth(nextWidth);
                                        }
                                    }}
                                >
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
                            )}
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
                            ListHeaderComponent={visibleActivities.length > 0 ? <BroadcastRail activities={visibleActivities} /> : undefined}
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
        alignItems: "center",
    },
    tabsRow: {
        flexDirection: "row",
        paddingVertical: 8,
        paddingHorizontal: 8,
        gap: 6,
        alignItems: "center",
    },
    tabsRowCentered: {
        justifyContent: "center",
        minWidth: "100%",
    },
    tabsRowScrollable: {
        justifyContent: "flex-start",
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
    governanceSection: {
        gap: 12,
        paddingBottom: 12,
    },
    governanceCard: {
        borderWidth: 1,
        borderRadius: 22,
        padding: 14,
        gap: 10,
    },
    governanceMetaRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
        flexWrap: "wrap",
    },
    governancePill: {
        borderRadius: radii.pill,
        paddingHorizontal: 10,
        paddingVertical: 5,
    },
    governancePillText: {
        fontSize: 10,
        fontWeight: "800",
    },
    governanceSmallText: {
        fontSize: 10,
        lineHeight: 14,
    },
    governanceChips: {
        flexDirection: "row",
        flexWrap: "wrap",
        gap: 8,
    },
    governanceChip: {
        borderRadius: radii.pill,
        paddingHorizontal: 10,
        paddingVertical: 6,
    },
    governanceTags: {
        flexDirection: "row",
        flexWrap: "wrap",
        gap: 6,
    },
    governanceTag: {
        borderWidth: 1,
        borderRadius: radii.pill,
        paddingHorizontal: 9,
        paddingVertical: 4,
    },
    governanceBody: {
        gap: 6,
    },
    governanceBodyText: {
        fontSize: 12,
        lineHeight: 18,
    },
    governanceSummaries: {
        gap: 8,
        marginTop: 2,
    },
    governanceSummaryCard: {
        borderRadius: 16,
        paddingHorizontal: 12,
        paddingVertical: 10,
    },
    contentEmpty: {
        paddingTop: 12,
        paddingBottom: 18,
    },
    feedGap: {
        height: 12,
    },
    broadcastCard: {
        borderRadius: 22,
        padding: 12,
        marginBottom: 12,
        gap: 10,
        shadowColor: "#0F172A",
        shadowOpacity: 0.18,
        shadowRadius: 22,
        shadowOffset: { width: 0, height: 12 },
        elevation: 8,
    },
    broadcastHeader: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        borderBottomWidth: 1,
        borderBottomColor: "rgba(255,255,255,0.08)",
        paddingBottom: 8,
    },
    broadcastLive: {
        flexDirection: "row",
        alignItems: "center",
        gap: 7,
    },
    broadcastDotWrap: {
        width: 12,
        height: 12,
        alignItems: "center",
        justifyContent: "center",
    },
    broadcastDotPulse: {
        position: "absolute",
        width: 12,
        height: 12,
        borderRadius: 6,
        backgroundColor: "rgba(251,191,36,0.32)",
    },
    broadcastDot: {
        width: 8,
        height: 8,
        borderRadius: 4,
    },
    broadcastLabel: {
        fontSize: 10,
        fontWeight: "900",
        letterSpacing: 1.6,
        color: "#D6D3D1",
    },
    broadcastCount: {
        fontSize: 10,
        fontWeight: "800",
        color: "#D6D3D1",
    },
    broadcastHero: {
        gap: 10,
        borderRadius: 18,
        borderWidth: 1,
        borderColor: "rgba(255,255,255,0.08)",
        backgroundColor: "rgba(255,255,255,0.04)",
        padding: 12,
    },
    broadcastBody: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 10,
    },
    broadcastIcon: {
        width: 34,
        height: 34,
        borderRadius: 17,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(255,255,255,0.10)",
    },
    broadcastTextWrap: {
        flex: 1,
        minWidth: 0,
    },
    broadcastTitle: {
        fontSize: 13,
        fontWeight: "800",
        color: "#FAFAF9",
    },
    broadcastSubtitle: {
        marginTop: 2,
        fontSize: 11,
        color: "#A8A29E",
    },
    broadcastTopic: {
        fontSize: 12,
        lineHeight: 18,
        color: "#D6D3D1",
    },
    broadcastQueue: {
        gap: 8,
    },
    broadcastQueueItem: {
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
        minHeight: 38,
        borderRadius: 14,
        backgroundColor: "rgba(255,255,255,0.04)",
        paddingHorizontal: 10,
    },
    broadcastQueueItemActive: {
        backgroundColor: "rgba(255,255,255,0.10)",
    },
    broadcastQueueText: {
        flex: 1,
        minWidth: 0,
        fontSize: 12,
        color: "#E7E5E4",
    },
    broadcastFooter: {
        borderRadius: 14,
        borderWidth: 1,
        borderColor: "rgba(255,255,255,0.08)",
        backgroundColor: "rgba(0,0,0,0.12)",
        paddingHorizontal: 12,
        paddingVertical: 9,
    },
    broadcastFooterText: {
        fontSize: 10,
        fontWeight: "800",
        letterSpacing: 1.3,
        color: "#A8A29E",
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
