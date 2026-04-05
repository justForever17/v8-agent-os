import { memo, useEffect, useRef } from "react";
import { Animated, Easing, Pressable, StyleSheet, Text, View } from "react-native";
import type { LucideIcon } from "lucide-react-native";
import { Blocks, Bot, Cpu, Database, RadioTower, TerminalSquare, Workflow } from "lucide-react-native";

import type { PhoneRuntimeId, PhoneRuntimeStageCard } from "@/src/lib/runtime-stage";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";

const RUNTIME_ICON_MAP: Record<PhoneRuntimeId, LucideIcon> = {
    chat: Bot,
    extensions: Blocks,
    automation: Workflow,
    memory: Database,
    plugin_host: RadioTower,
    computer_use: TerminalSquare,
    rpa: Cpu,
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
            return {
                border: dark ? "rgba(255,255,255,0.12)" : "rgba(214,211,209,0.92)",
                background: dark ? "rgba(255,255,255,0.05)" : "rgba(255,255,255,0.84)",
                icon: palette.text,
                dot: dark ? "#D6D3D1" : "#78716C",
            };
        case "idle":
        default:
            return {
                border: dark ? "rgba(255,255,255,0.10)" : "rgba(231,229,228,0.95)",
                background: dark ? "rgba(255,255,255,0.04)" : "rgba(255,255,255,0.72)",
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
}: {
    item: PhoneRuntimeStageCard;
    selected: boolean;
    dark: boolean;
    colors: ReturnType<typeof useUiPrefs>["colors"];
    onSelectRuntime: (runtimeId: PhoneRuntimeId) => void;
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
                <Icon size={12} color={tone.icon} strokeWidth={2} />
            </Animated.View>
            <View style={[styles.dot, { backgroundColor: tone.dot, borderColor: dark ? "rgba(24,24,27,0.92)" : "#FFFFFF" }]} />
            {item.eventCount > 0 ? (
                <View style={[styles.badge, { backgroundColor: dark ? "#F5F5F4" : "#0F172A", borderColor: dark ? "rgba(24,24,27,0.92)" : "#FFFFFF" }]}>
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
}: {
    items: PhoneRuntimeStageCard[];
    selectedRuntimeId: PhoneRuntimeId | null;
    panelOpen: boolean;
    onSelectRuntime: (runtimeId: PhoneRuntimeId) => void;
}) {
    const { colors, themeMode } = useUiPrefs();
    const dark = themeMode === "dark";

    return (
        <View
            style={[
                styles.wrap,
                {
                    backgroundColor: themeMode === "dark" ? "rgba(24,24,27,0.75)" : "rgba(255,255,255,0.76)",
                    borderColor: panelOpen ? "rgba(245,158,11,0.36)" : `${colors.border}CC`,
                },
            ]}
        >
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
                    />
                );
            })}
        </View>
    );
});

const styles = StyleSheet.create({
    wrap: {
        flexDirection: "row",
        alignItems: "center",
        gap: 0.5,
        borderWidth: 1,
        borderRadius: radii.pill,
        paddingHorizontal: 3,
        paddingVertical: 3,
        flexShrink: 0,
        shadowColor: "#0F172A",
        shadowOpacity: 0.08,
        shadowRadius: 20,
        shadowOffset: { width: 0, height: 10 },
        elevation: 2,
    },
    item: {
        width: 28,
        height: 28,
        borderRadius: 12,
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
        borderRadius: 12,
    },
    dot: {
        position: "absolute",
        top: -0.5,
        right: -0.5,
        width: 6,
        height: 6,
        borderRadius: 999,
        borderWidth: 1,
    },
    badge: {
        position: "absolute",
        right: -3,
        bottom: -3,
        minWidth: 13,
        height: 13,
        borderRadius: 999,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 2,
        borderWidth: 1,
    },
    badgeText: {
        color: "#FFFFFF",
        fontSize: 8,
        fontWeight: "800",
        lineHeight: 9,
    },
});
