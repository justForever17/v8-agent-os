import { memo, useEffect, useRef, useState } from "react";
import {
    Animated,
    Easing,
    Pressable,
    StyleSheet,
    Text,
    View,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { CodeBlock } from "@/src/components/chat/CodeBlock";
import { Badge } from "@/src/components/ui/badge";
import { useUiPrefs } from "@/src/providers/ui-prefs";

export type ToolInvocation = {
    toolCallId: string;
    toolName: string;
    args: unknown;
    state: "call" | "result";
    result?: unknown;
};

type ToolCardProps = {
    toolInvocation: ToolInvocation;
    hideResult?: boolean;
};

function stringifyPayload(value: unknown) {
    if (typeof value === "string") {
        return value;
    }
    try {
        return JSON.stringify(value, null, 2);
    } catch {
        return String(value ?? "");
    }
}

export const ToolCard = memo(function ToolCard({ toolInvocation, hideResult }: ToolCardProps) {
    const { colors, themeMode, t } = useUiPrefs();
    const [isExpanded, setIsExpanded] = useState(false);
    const progress = useRef(new Animated.Value(0)).current;
    const isComplete = toolInvocation.state === "result";

    useEffect(() => {
        Animated.timing(progress, {
            toValue: isExpanded ? 1 : 0,
            duration: 260,
            easing: Easing.out(Easing.cubic),
            useNativeDriver: true,
        }).start();
    }, [isExpanded, progress]);

    const rotate = progress.interpolate({
        inputRange: [0, 1],
        outputRange: ["0deg", "180deg"],
    });

    const contentOpacity = progress.interpolate({
        inputRange: [0, 1],
        outputRange: [0, 1],
    });

    const contentTranslateY = progress.interpolate({
        inputRange: [0, 1],
        outputRange: [-6, 0],
    });

    const accent = isComplete ? "#14B8A6" : "#3B82F6";

    return (
        <View style={styles.wrap}>
            {!isComplete ? <View style={[styles.activeGlow, { backgroundColor: "rgba(59,130,246,0.1)" }]} /> : null}
            <View
                style={[
                    styles.card,
                    {
                        backgroundColor: isExpanded
                            ? (themeMode === "dark" ? "rgba(15,23,42,0.46)" : "rgba(255,255,255,0.56)")
                            : (themeMode === "dark" ? "rgba(15,23,42,0.26)" : "rgba(255,255,255,0.28)"),
                        borderColor: isExpanded
                            ? `${accent}4D`
                            : (themeMode === "dark" ? "rgba(255,255,255,0.08)" : "rgba(255,255,255,0.22)"),
                    },
                ]}
            >
                <Pressable style={styles.header} onPress={() => setIsExpanded((value) => !value)}>
                    <View style={styles.headerLeft}>
                        <View
                            style={[
                                styles.iconWrap,
                                {
                                    backgroundColor: isComplete ? "rgba(20,184,166,0.12)" : "rgba(59,130,246,0.18)",
                                    borderColor: isComplete ? "rgba(20,184,166,0.3)" : "rgba(59,130,246,0.4)",
                                },
                            ]}
                        >
                            <MaterialCommunityIcons
                                name="source-branch"
                                size={14}
                                color={isComplete ? "#14B8A6" : "#3B82F6"}
                            />
                            {!isComplete ? <View style={[styles.pingRing, { borderColor: "rgba(59,130,246,0.35)" }]} /> : null}
                        </View>

                        <Text style={[styles.title, { color: isExpanded ? colors.text : colors.textMuted }]}>
                            {toolInvocation.toolName}
                        </Text>

                        <Badge variant={isComplete ? "secondary" : "outline"}>
                            {isComplete ? t("已完成", "Complete") : t("执行中", "Running")}
                        </Badge>
                    </View>

                    <Animated.View style={{ transform: [{ rotate }] }}>
                        <MaterialCommunityIcons
                            name="chevron-down"
                            size={18}
                            color={isExpanded ? accent : colors.textSoft}
                        />
                    </Animated.View>
                </Pressable>

                {isExpanded ? (
                    <Animated.View
                        style={[
                            styles.content,
                            {
                                opacity: contentOpacity,
                                transform: [{ translateY: contentTranslateY }],
                            },
                        ]}
                    >
                        <View style={styles.section}>
                            <Text style={[styles.sectionLabel, { color: colors.textSoft }]}>
                                {t("输入", "Input")}
                            </Text>
                            <CodeBlock language="json" value={stringifyPayload(toolInvocation.args ?? {})} />
                        </View>

                        {isComplete && !hideResult ? (
                            <View style={styles.section}>
                                <Text style={[styles.sectionLabel, { color: colors.textSoft }]}>
                                    {t("输出", "Output")}
                                </Text>
                                <CodeBlock language="json" value={stringifyPayload(toolInvocation.result ?? "")} />
                            </View>
                        ) : null}
                    </Animated.View>
                ) : null}
            </View>
        </View>
    );
}, (prev, next) => (
    prev.toolInvocation.toolCallId === next.toolInvocation.toolCallId
    && prev.toolInvocation.state === next.toolInvocation.state
    && prev.hideResult === next.hideResult
    && JSON.stringify(prev.toolInvocation.args ?? {}) === JSON.stringify(next.toolInvocation.args ?? {})
    && JSON.stringify(prev.toolInvocation.result ?? null) === JSON.stringify(next.toolInvocation.result ?? null)
));

const styles = StyleSheet.create({
    wrap: {
        width: "100%",
        marginVertical: 4,
        position: "relative",
    },
    activeGlow: {
        position: "absolute",
        inset: 0,
        borderRadius: 16,
    },
    card: {
        width: "100%",
        overflow: "hidden",
        borderRadius: 16,
        borderWidth: 1,
    },
    header: {
        minHeight: 40,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        paddingHorizontal: 14,
        paddingVertical: 8,
    },
    headerLeft: {
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
        flex: 1,
    },
    iconWrap: {
        width: 22,
        height: 22,
        borderRadius: 8,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
        position: "relative",
    },
    pingRing: {
        position: "absolute",
        inset: -1,
        borderRadius: 8,
        borderWidth: 1,
    },
    title: {
        fontSize: 11,
        fontWeight: "700",
        letterSpacing: 0.4,
        flexShrink: 1,
    },
    content: {
        paddingHorizontal: 14,
        paddingBottom: 14,
        paddingTop: 2,
        gap: 10,
    },
    section: {
        gap: 6,
    },
    sectionLabel: {
        fontSize: 10,
        fontWeight: "700",
        letterSpacing: 1.2,
        textTransform: "uppercase",
    },
});
