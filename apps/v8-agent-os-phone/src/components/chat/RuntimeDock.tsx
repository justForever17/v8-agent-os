import { memo, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Animated, Easing, Pressable, StyleSheet, Text, View, Platform, type LayoutChangeEvent } from "react-native";
import type { LucideIcon } from "lucide-react-native";
import { Blocks, Bot, Code2, Cpu, Database, GitBranch, Globe, RadioTower, Route, Search, Shield, Sparkles, TerminalSquare, Workflow } from "lucide-react-native";
import { ScrollView as GestureScrollView } from "react-native-gesture-handler";

import type { PhoneRuntimeId, PhoneRuntimeStageCard } from "@/src/lib/runtime-stage";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";

const RUNTIME_ICON_MAP: Record<PhoneRuntimeId, LucideIcon> = {
    chat: Bot,
    planner_lane: Route,
    engineering: Code2,
    engineering_lane: Code2,
    extensions: Blocks,
    research: Search,
    creative_media: Sparkles,
    automation: Workflow,
    memory: Database,
    context_governance: Shield,
    subagent_swarm: GitBranch,
    network_supervisor: Globe,
    plugin_host_tool: RadioTower,
    plugin_host_channel: RadioTower,
    computer_use: TerminalSquare,
    rpa: Cpu,
    desktop_live: RadioTower,
};

export function getRuntimeDockIcon(runtimeId: PhoneRuntimeId) {
    return RUNTIME_ICON_MAP[runtimeId];
}

function toneColors(
    tone: PhoneRuntimeStageCard["status"],
    palette: ReturnType<typeof useUiPrefs>["colors"],
    dark: boolean,
) {
    switch (tone) {
        case "active":
            return {
                border: dark ? "rgba(245,158,11,0.30)" : "rgba(252,211,77,0.85)",
                background: dark ? "rgba(245,158,11,0.10)" : "rgba(255,251,235,0.92)",
                icon: "#B45309",
                dot: "#F59E0B",
            };
        case "attention":
            return {
                border: dark ? "rgba(244,63,94,0.24)" : "rgba(253,164,175,0.8)",
                background: dark ? "rgba(244,63,94,0.10)" : "rgba(255,241,242,0.92)",
                icon: palette.danger,
                dot: palette.danger,
            };
        case "recent":
        case "idle":
        default:
            return {
                border: "transparent",
                background: "transparent",
                icon: palette.textMuted,
                dot: dark ? "rgba(113,113,122,0.95)" : "rgba(214,211,209,0.95)",
            };
    }
}

function RuntimeDockItem({
    item,
    selected,
    dark,
    colors,
    onSelectRuntime,
    onLayout,
}: {
    item: PhoneRuntimeStageCard;
    selected: boolean;
    dark: boolean;
    colors: ReturnType<typeof useUiPrefs>["colors"];
    onSelectRuntime: (runtimeId: PhoneRuntimeId) => void;
    onLayout: (event: LayoutChangeEvent) => void;
}) {
    const Icon = getRuntimeDockIcon(item.id);
    const tone = toneColors(item.status, colors, dark);
    const pulse = useRef(new Animated.Value(0.2)).current;
    const wiggle = useRef(new Animated.Value(0)).current;
    const pulsing = item.status === "active" || item.status === "attention";
    const attention = item.status === "attention";

    useEffect(() => {
        if (!pulsing) {
            pulse.stopAnimation();
            pulse.setValue(0);
            return;
        }
        const loop = Animated.loop(
            Animated.sequence([
                Animated.timing(pulse, {
                    toValue: 0.72,
                    duration: 900,
                    easing: Easing.inOut(Easing.ease),
                    useNativeDriver: true,
                }),
                Animated.timing(pulse, {
                    toValue: 0.18,
                    duration: 900,
                    easing: Easing.inOut(Easing.ease),
                    useNativeDriver: true,
                }),
            ]),
        );
        loop.start();
        return () => loop.stop();
    }, [pulse, pulsing]);

    useEffect(() => {
        if (!attention) {
            wiggle.stopAnimation();
            wiggle.setValue(0);
            return;
        }
        const loop = Animated.loop(
            Animated.sequence([
                Animated.timing(wiggle, { toValue: -4, duration: 90, easing: Easing.linear, useNativeDriver: true }),
                Animated.timing(wiggle, { toValue: 4, duration: 90, easing: Easing.linear, useNativeDriver: true }),
                Animated.timing(wiggle, { toValue: -3, duration: 80, easing: Easing.linear, useNativeDriver: true }),
                Animated.timing(wiggle, { toValue: 3, duration: 80, easing: Easing.linear, useNativeDriver: true }),
                Animated.timing(wiggle, { toValue: 0, duration: 90, easing: Easing.linear, useNativeDriver: true }),
                Animated.delay(2400),
            ]),
        );
        loop.start();
        return () => loop.stop();
    }, [attention, wiggle]);

    return (
        <Pressable
            key={item.id}
            onPress={() => onSelectRuntime(item.id)}
            onLayout={onLayout}
            style={({ pressed }) => [
                styles.item,
                selected ? styles.itemSelected : null,
                {
                    backgroundColor: tone.background,
                    borderColor: selected
                        ? (dark ? "rgba(251,191,36,0.62)" : "rgba(245,158,11,0.58)")
                        : tone.border,
                    opacity: pressed ? 0.8 : 1,
                },
            ]}
        >
            {pulsing ? (
                <Animated.View
                    pointerEvents="none"
                    style={[
                        styles.pulseFill,
                        {
                            backgroundColor: attention ? "rgba(244,63,94,0.12)" : "rgba(245,158,11,0.12)",
                            opacity: pulse,
                            transform: [{
                                scale: pulse.interpolate({
                                    inputRange: [0, 1],
                                    outputRange: [0.96, 1.04],
                                }),
                            }],
                        },
                    ]}
                />
            ) : null}

            <Animated.View style={{ transform: [{ rotate: attention ? wiggle.interpolate({
                inputRange: [-4, 4],
                outputRange: ["-4deg", "4deg"],
            }) : "0deg" }] }}>
                <Icon size={22} color={tone.icon} strokeWidth={2.2} />
            </Animated.View>
            <View style={[styles.dot, { backgroundColor: tone.dot, borderColor: dark ? "rgba(24,24,27,0.92)" : "#FFFFFF" }]} />
            {item.eventCount > 0 ? (
                <View style={[styles.badge, { backgroundColor: dark ? "#F8FAFC" : "#0F172A", borderColor: dark ? "rgba(15,23,42,0.88)" : "#FFFFFF" }]}>
                    <Text style={[styles.badgeText, { color: dark ? "#0F172A" : "#FFFFFF" }]}>{Math.min(item.eventCount, 9)}</Text>
                </View>
            ) : null}
        </Pressable>
    );
}

export const RuntimeDock = memo(function RuntimeDock({
    items,
    selectedRuntimeId,
    panelOpen,
    onSelectRuntime,
    leadingAccessory,
}: {
    items: PhoneRuntimeStageCard[];
    selectedRuntimeId: PhoneRuntimeId | null;
    panelOpen: boolean;
    onSelectRuntime: (runtimeId: PhoneRuntimeId) => void;
    leadingAccessory?: ReactNode;
}) {
    const { colors, themeMode } = useUiPrefs();
    const dark = themeMode === "dark";
    const scrollRef = useRef<GestureScrollView | null>(null);
    const itemLayoutsRef = useRef<Record<string, { x: number; width: number }>>({});
    const [containerWidth, setContainerWidth] = useState(0);

    const handleItemLayout = useCallback((runtimeId: PhoneRuntimeId, event: LayoutChangeEvent) => {
        const { x, width } = event.nativeEvent.layout;
        itemLayoutsRef.current[runtimeId] = { x, width };
    }, []);

    useEffect(() => {
        const selectedIndex = items.findIndex((item) => item.id === selectedRuntimeId);
        if (selectedIndex < 0) {
            return;
        }
        const selectedItem = items[selectedIndex];
        const measuredLayout = selectedItem ? itemLayoutsRef.current[selectedItem.id] : undefined;
        const estimatedItemWidth = 38;
        const estimatedOffset = Math.max(0, (selectedIndex * estimatedItemWidth) - 18);
        const nextOffset = measuredLayout && containerWidth > 0
            ? Math.max(0, measuredLayout.x - ((containerWidth - measuredLayout.width) / 2))
            : estimatedOffset;
        requestAnimationFrame(() => {
            scrollRef.current?.scrollTo({ x: nextOffset, animated: true });
        });
    }, [containerWidth, items, selectedRuntimeId]);

    return (
        <View
            style={[
                styles.wrap,
                {
                    backgroundColor: themeMode === "dark" ? "rgba(24, 24, 27, 0.76)" : "rgba(255, 255, 255, 0.78)",
                    borderColor: panelOpen ? "rgba(245,158,11,0.36)" : colors.border,
                    ...Platform.select({
                        web: { backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)" } as any,
                    }),
                },
            ]}
            onLayout={(event) => {
                const nextWidth = Math.round(event.nativeEvent.layout.width);
                if (nextWidth !== containerWidth) {
                    setContainerWidth(nextWidth);
                }
            }}
        >
            <GestureScrollView
                ref={scrollRef}
                style={styles.scroll}
                horizontal
                scrollEnabled
                nestedScrollEnabled
                showsHorizontalScrollIndicator={false}
                overScrollMode="never"
                directionalLockEnabled
                keyboardShouldPersistTaps="handled"
                scrollEventThrottle={16}
                contentContainerStyle={styles.scrollContent}
            >
                {leadingAccessory ? <View style={styles.leadingAccessory}>{leadingAccessory}</View> : null}
                {items.map((item) => {
                    const selected = panelOpen && item.id === selectedRuntimeId;
                    return (
                        <RuntimeDockItem
                            key={item.id}
                            item={item}
                            selected={selected}
                            dark={dark}
                            colors={colors}
                            onSelectRuntime={onSelectRuntime}
                            onLayout={(event) => handleItemLayout(item.id, event)}
                        />
                    );
                })}
            </GestureScrollView>
        </View>
    );
});

const styles = StyleSheet.create({
    wrap: {
        borderWidth: 1,
        borderRadius: 10,
        paddingHorizontal: 7,
        paddingVertical: 0,
        height: 38,
        alignSelf: "center",
        minWidth: 0,
        shadowColor: "#0F172A",
        shadowOpacity: 0,
        elevation: 0,
    },
    scrollContent: {
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
        paddingLeft: 1,
        paddingRight: 3,
    },
    scroll: {
        width: "100%",
        minWidth: 0,
    },
    leadingAccessory: {
        flexShrink: 0,
    },
    item: {
        width: 38,
        height: 38,
        borderRadius: 10,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
        overflow: "visible",
    },
    itemSelected: {
        shadowColor: "#F59E0B",
        shadowOpacity: 0.18,
        shadowRadius: 14,
        shadowOffset: { width: 0, height: 0 },
        elevation: 2,
    },
    pulseFill: {
        ...StyleSheet.absoluteFillObject,
        borderRadius: 10,
    },
    dot: {
        position: "absolute",
        top: -3,
        right: -3,
        width: 7,
        height: 7,
        borderRadius: 999,
        borderWidth: 1,
    },
    badge: {
        position: "absolute",
        right: -5,
        bottom: -5,
        minWidth: 21,
        height: 21,
        borderRadius: 999,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 1.5,
        borderWidth: 1,
        zIndex: 5,
        elevation: 5,
    },
    badgeText: {
        color: "#FFFFFF",
        fontSize: 12,
        fontWeight: "800",
        lineHeight: 15,
    },
});
