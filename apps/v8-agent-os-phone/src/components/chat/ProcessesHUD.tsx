import { memo, useMemo, useRef, useState } from "react";
import { Animated, Easing, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { InteractiveTerminalCard } from "@/src/components/chat/InteractiveTerminalCard";
import { Card, CardContent } from "@/src/components/ui/card";
import { extractCommandSessionPayload, isCommandSessionTool } from "@/src/lib/chat/command-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type { ChatMessage } from "@/src/types/admin";

type ProcessesHUDProps = {
    messages: ChatMessage[];
};

function useProcessesState(messages: ChatMessage[]) {
    return useMemo(() => {
        const commandIds: string[] = [];
        const extractId = (text: unknown) => {
            if (typeof text !== "string") return null;
            const match = text.match(/ID:\s*([a-f0-9-]+)/i);
            return match ? match[1] : null;
        };

        for (const message of messages) {
            if (message.role !== "assistant") continue;
            const nodes = Array.isArray(message.nodes) ? message.nodes : [];
            const bgToolCallIds = new Set<string>();

            for (const node of nodes) {
                if (node.kind !== "execution" || node.executionType !== "tool_call") continue;
                const toolName = node.toolName || node.data?.toolName || node.data?.tool_name;
                if (!isCommandSessionTool(toolName)) continue;

                const commandId = extractCommandSessionPayload(node.result)?.commandId || extractId(node.result);
                if (commandId && !commandIds.includes(commandId)) {
                    commandIds.push(commandId);
                }
                if (node.toolCallId) {
                    bgToolCallIds.add(node.toolCallId);
                }
            }

            for (const node of nodes) {
                if (node.kind !== "execution" || node.executionType !== "tool_result" || !node.toolCallId || !bgToolCallIds.has(node.toolCallId)) {
                    continue;
                }
                const commandId = extractCommandSessionPayload(node.result)?.commandId || extractId(node.result);
                if (commandId && !commandIds.includes(commandId)) {
                    commandIds.push(commandId);
                }
            }
        }

        return commandIds;
    }, [messages]);
}

export const ProcessesHUD = memo(function ProcessesHUD({ messages }: ProcessesHUDProps) {
    const { colors, themeMode, t } = useUiPrefs();
    const [terminatedIds, setTerminatedIds] = useState<Set<string>>(new Set());
    const [isCollapsed, setIsCollapsed] = useState(false);
    const progress = useRef(new Animated.Value(0)).current;
    const allCommandIds = useProcessesState(messages);
    const activeIds = allCommandIds.filter((id) => !terminatedIds.has(id));

    if (activeIds.length === 0) {
        return null;
    }

    const toggle = () => {
        const nextCollapsed = !isCollapsed;
        setIsCollapsed(nextCollapsed);
        Animated.timing(progress, {
            toValue: nextCollapsed ? 1 : 0,
            duration: 220,
            easing: Easing.out(Easing.cubic),
            useNativeDriver: true,
        }).start();
    };

    const rotation = progress.interpolate({
        inputRange: [0, 1],
        outputRange: ["0deg", "-90deg"],
    });

    return (
        <Card
            style={[
                styles.wrap,
                {
                    backgroundColor: themeMode === "dark" ? "rgba(24,24,27,0.44)" : "rgba(255,255,255,0.46)",
                    borderColor: themeMode === "dark" ? "rgba(255,255,255,0.08)" : "rgba(255,255,255,0.30)",
                },
            ]}
        >
            <Pressable
                style={[
                    styles.header,
                    {
                        backgroundColor: themeMode === "dark" ? "rgba(12,74,110,0.12)" : "rgba(236,253,245,0.78)",
                        borderBottomColor: isCollapsed ? "transparent" : colors.border,
                    },
                ]}
                onPress={toggle}
            >
                <View style={styles.headerLeft}>
                    <View style={[styles.iconWrap, { backgroundColor: "rgba(16,185,129,0.16)" }]}>
                        <MaterialCommunityIcons name="console-network-outline" size={16} color="#10B981" />
                    </View>
                    <Text style={[styles.title, { color: colors.text }]}>{t("后台进程", "Processes")}</Text>
                    <View style={[styles.counterPill, { backgroundColor: themeMode === "dark" ? "rgba(255,255,255,0.08)" : "rgba(255,255,255,0.72)" }]}>
                        <View style={styles.counterDotWrap}>
                            <View style={styles.counterDot} />
                        </View>
                        <Text style={[styles.counterText, { color: colors.textMuted }]}>{activeIds.length}</Text>
                    </View>
                </View>
                <Animated.View style={{ transform: [{ rotate: rotation }] }}>
                    <MaterialCommunityIcons name="chevron-down" size={18} color={colors.textSoft} />
                </Animated.View>
            </Pressable>

            {!isCollapsed ? (
                <CardContent style={styles.content}>
                    <ScrollView nestedScrollEnabled style={styles.scrollArea} contentContainerStyle={styles.scrollContent}>
                        {activeIds.map((commandId) => (
                            <InteractiveTerminalCard
                                key={commandId}
                                commandId={commandId}
                                compact
                                onTerminated={(cmdId) => {
                                    setTerminatedIds((current) => {
                                        const next = new Set(current);
                                        next.add(cmdId);
                                        return next;
                                    });
                                }}
                            />
                        ))}
                    </ScrollView>
                </CardContent>
            ) : null}
        </Card>
    );
});

const styles = StyleSheet.create({
    wrap: {
        width: "100%",
        overflow: "hidden",
    },
    header: {
        minHeight: 38,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: spacing.sm,
        paddingHorizontal: 12,
        paddingVertical: 8,
        borderBottomWidth: StyleSheet.hairlineWidth,
    },
    headerLeft: {
        flex: 1,
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
        minWidth: 0,
    },
    iconWrap: {
        width: 28,
        height: 28,
        borderRadius: 10,
        alignItems: "center",
        justifyContent: "center",
    },
    title: {
        fontSize: 13,
        fontWeight: "800",
        letterSpacing: -0.2,
    },
    counterPill: {
        minHeight: 22,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        borderRadius: radii.pill,
        paddingHorizontal: 8,
    },
    counterDotWrap: {
        width: 8,
        height: 8,
        alignItems: "center",
        justifyContent: "center",
    },
    counterDot: {
        width: 6,
        height: 6,
        borderRadius: 999,
        backgroundColor: "#10B981",
    },
    counterText: {
        fontSize: 11,
        fontWeight: "700",
    },
    content: {
        paddingTop: spacing.sm,
    },
    scrollArea: {
        maxHeight: 208,
    },
    scrollContent: {
        gap: spacing.sm,
    },
});
