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
import type { ClientToolSurface } from "@v8/session-realtime";

import { CodeBlock } from "@/src/components/chat/CodeBlock";
import { useUiPrefs } from "@/src/providers/ui-prefs";

export type ToolInvocation = {
    toolCallId: string;
    toolName: string;
    args: unknown;
    state: "call" | "result";
    result?: unknown;
    clientSurface?: ClientToolSurface;
};

type ToolCardProps = {
    toolInvocation: ToolInvocation;
    hideResult?: boolean;
};

function looksLikeRawStructuredOutput(value: unknown) {
    if (value === null || value === undefined) {
        return false;
    }
    if (typeof value === "string") {
        const trimmed = value.trim();
        return trimmed.startsWith("{") || trimmed.startsWith("[");
    }
    return typeof value === "object";
}

function buildReadableResult(toolInvocation: ToolInvocation) {
    const result = toolInvocation.result ?? "";
    const surface = toolInvocation.clientSurface;
    if (looksLikeRawStructuredOutput(result) && surface) {
        const lines = [
            surface.summary,
            surface.progress ? `进度：${surface.progress}` : "",
            surface.actionable ? `下一步：${surface.actionable}` : "",
            surface.refIds.length ? `续读引用：${surface.refIds.join(", ")}` : "",
        ].filter(Boolean);
        if (lines.length) {
            return lines.join("\n");
        }
    }
    return stringifyPayload(result);
}

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

function payloadLanguage(value: unknown) {
    return typeof value === "string" ? "text" : "json";
}

function resolveToolIconName(toolName: string) {
    const normalized = String(toolName || "").toLowerCase();
    if (/(command|shell|bash|terminal|process|session)/.test(normalized)) {
        return "console-line";
    }
    if (/(file|directory|read|write|edit|path|workspace)/.test(normalized)) {
        return "file-document-outline";
    }
    if (/(web|search|read_url|browser|research|fetch)/.test(normalized)) {
        return "magnify";
    }
    if (/(safety|approval|risk|guard)/.test(normalized)) {
        return "shield-check-outline";
    }
    if (/(image|video|audio|media|render)/.test(normalized)) {
        return "image-multiple-outline";
    }
    return "tools";
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
    const iconName = resolveToolIconName(toolInvocation.toolName);
    const readableResult = buildReadableResult(toolInvocation);

    return (
        <View style={styles.wrap}>
            {!isComplete ? <View style={[styles.activeGlow, { backgroundColor: "rgba(59,130,246,0.055)" }]} /> : null}
            <View
                style={[
                    styles.card,
                    {
                        backgroundColor: isExpanded
                            ? (themeMode === "dark" ? "rgba(15,23,42,0.38)" : "rgba(255,255,255,0.50)")
                            : (themeMode === "dark" ? "rgba(15,23,42,0.18)" : "rgba(255,255,255,0.20)"),
                        borderColor: isExpanded
                            ? `${accent}24`
                            : (themeMode === "dark" ? "rgba(255,255,255,0.035)" : "rgba(15,23,42,0.035)"),
                    },
                ]}
            >
                <Pressable style={styles.header} onPress={() => setIsExpanded((value) => !value)}>
                    <View style={styles.headerLeft}>
                        <View
                            style={[
                                styles.iconWrap,
                                {
                                    backgroundColor: isComplete ? "rgba(20,184,166,0.10)" : "rgba(59,130,246,0.12)",
                                    borderColor: isComplete ? "rgba(20,184,166,0.18)" : "rgba(59,130,246,0.22)",
                                },
                            ]}
                        >
                            <MaterialCommunityIcons
                                name={iconName as never}
                                size={13}
                                color={isComplete ? "#14B8A6" : "#3B82F6"}
                            />
                            {!isComplete ? <View style={[styles.pingRing, { borderColor: "rgba(59,130,246,0.20)" }]} /> : null}
                        </View>

                        <Text
                            style={[styles.title, { color: isExpanded ? colors.text : colors.textMuted }]}
                            numberOfLines={1}
                            ellipsizeMode="tail"
                        >
                            {toolInvocation.toolName}
                        </Text>

                        <Text style={[
                            styles.statusPill,
                            {
                                color: isComplete ? "#0F766E" : "#2563EB",
                                backgroundColor: isComplete ? "rgba(20,184,166,0.10)" : "rgba(59,130,246,0.10)",
                                borderColor: isComplete ? "rgba(20,184,166,0.14)" : "rgba(59,130,246,0.16)",
                            },
                        ]}>
                            {isComplete ? t("src.components.chat.toolcard.complete") : t("src.components.chat.toolcard.running")}
                        </Text>
                    </View>

                    <Animated.View style={{ transform: [{ rotate }] }}>
                        <MaterialCommunityIcons
                            name="chevron-down"
                            size={18}
                            color={isExpanded ? accent : colors.textSoft}
                        />
                    </Animated.View>
                </Pressable>

                {toolInvocation.clientSurface?.summary && !toolInvocation.clientSurface.summary.startsWith("{") && !toolInvocation.clientSurface.summary.startsWith("[") ? (
                    <Text
                        style={[styles.summary, { color: colors.textMuted }]}
                        numberOfLines={2}
                        ellipsizeMode="tail"
                    >
                        {toolInvocation.clientSurface.summary}
                    </Text>
                ) : null}

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
                                {t("src.components.chat.toolcard.input")}
                            </Text>
                            <CodeBlock language="json" value={stringifyPayload(toolInvocation.args ?? {})} />
                        </View>

                        {isComplete && !hideResult ? (
                            <View style={styles.section}>
                                <Text style={[styles.sectionLabel, { color: colors.textSoft }]}>
                                    {t("src.components.chat.toolcard.output")}
                                </Text>
                                <CodeBlock
                                    language={payloadLanguage(readableResult)}
                                    value={readableResult}
                                />
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
    && JSON.stringify(prev.toolInvocation.clientSurface ?? null) === JSON.stringify(next.toolInvocation.clientSurface ?? null)
));

const styles = StyleSheet.create({
    wrap: {
        width: "100%",
        marginVertical: 1,
        position: "relative",
    },
    activeGlow: {
        position: "absolute",
        inset: 0,
        borderRadius: 13,
    },
    card: {
        width: "100%",
        overflow: "hidden",
        borderRadius: 13,
        borderWidth: 1,
    },
    header: {
        minHeight: 27,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        paddingHorizontal: 8,
        paddingVertical: 4,
    },
    headerLeft: {
        flexDirection: "row",
        alignItems: "center",
        gap: 5,
        flex: 1,
        minWidth: 0,
    },
    iconWrap: {
        width: 18,
        height: 18,
        borderRadius: 6,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
        position: "relative",
    },
    pingRing: {
        position: "absolute",
        inset: -1,
        borderRadius: 7,
        borderWidth: 1,
    },
    title: {
        fontSize: 11,
        fontWeight: "700",
        letterSpacing: 0,
        flexShrink: 1,
        minWidth: 0,
        includeFontPadding: false,
    },
    statusPill: {
        overflow: "hidden",
        borderRadius: 999,
        borderWidth: 1,
        paddingHorizontal: 5,
        paddingVertical: 1,
        fontSize: 9,
        lineHeight: 12,
        fontWeight: "800",
        flexShrink: 0,
    },
    summary: {
        paddingHorizontal: 8,
        paddingBottom: 5,
        marginTop: -1,
        fontSize: 10,
        lineHeight: 15,
        fontWeight: "600",
    },
    content: {
        paddingHorizontal: 8,
        paddingBottom: 6,
        paddingTop: 1,
        gap: 4,
    },
    section: {
        gap: 3,
    },
    sectionLabel: {
        fontSize: 10,
        fontWeight: "700",
        letterSpacing: 1.2,
        textTransform: "uppercase",
    },
});
