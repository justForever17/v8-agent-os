import { memo, useEffect, useState } from "react";
import {
    Pressable,
    StyleSheet,
    Text,
    View,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { formatClientToolResult, type ClientToolSurface, type ClientToolSurfaceStatus } from "@v8/session-realtime";
import Animated, {
    Easing,
    Keyframe,
    LinearTransition,
    ReduceMotion,
    useAnimatedStyle,
    useSharedValue,
    withTiming,
} from "react-native-reanimated";

import { CodeBlock } from "@/src/components/chat/CodeBlock";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import type { TranslationParams } from "@/src/providers/ui-prefs";

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

const CONTENT_ENTER = new Keyframe({
    0: { opacity: 0, transform: [{ translateY: -6 }] },
    100: { opacity: 1, transform: [{ translateY: 0 }], easing: Easing.out(Easing.cubic) },
}).duration(220).reduceMotion(ReduceMotion.System);
const CONTENT_EXIT = new Keyframe({
    0: { opacity: 1, transform: [{ translateY: 0 }] },
    100: { opacity: 0, transform: [{ translateY: -6 }], easing: Easing.out(Easing.cubic) },
}).duration(180).reduceMotion(ReduceMotion.System);
const CARD_LAYOUT = LinearTransition.duration(220).reduceMotion(ReduceMotion.System);

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

type Translate = (key: string, params?: TranslationParams) => string;

function buildReadableResult(toolInvocation: ToolInvocation, t: Translate) {
    const result = toolInvocation.result ?? "";
    const surface = toolInvocation.clientSurface;
    if (looksLikeRawStructuredOutput(result) && surface) {
        const lines = [
            surface.summary,
            surface.progress ? t("src.components.chat.toolcard.progress_line", { value: surface.progress }) : "",
            surface.actionable ? t("src.components.chat.toolcard.next_line", { value: surface.actionable }) : "",
            surface.refIds.length ? t("src.components.chat.toolcard.refs_line", { value: surface.refIds.join(", ") }) : "",
        ].filter(Boolean);
        if (lines.length) {
            return lines.join("\n");
        }
    }
    return stringifyPayload(result);
}

function stringifyPayload(value: unknown) {
    return formatClientToolResult(value);
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

function resolveToolStatus(toolInvocation: ToolInvocation): ClientToolSurfaceStatus {
    return toolInvocation.clientSurface?.status || (toolInvocation.state === "result" ? "completed" : "running");
}

function statusPresentation(status: ClientToolSurfaceStatus) {
    if (status === "completed") return { key: "src.components.chat.toolcard.complete", color: "#14B8A6" };
    if (status === "running") return { key: "src.components.chat.toolcard.running", color: "#3B82F6" };
    if (status === "waiting") return { key: "src.components.chat.toolcard.waiting", color: "#D97706" };
    if (status === "blocked") return { key: "src.components.chat.toolcard.blocked", color: "#EA580C" };
    if (status === "timed_out") return { key: "src.components.chat.toolcard.timed_out", color: "#DC2626" };
    if (status === "terminated") return { key: "src.components.chat.toolcard.terminated", color: "#71717A" };
    if (status === "failed") return { key: "src.components.chat.toolcard.failed", color: "#DC2626" };
    return { key: "src.components.chat.toolcard.unknown", color: "#71717A" };
}

export const ToolCard = memo(function ToolCard({ toolInvocation, hideResult }: ToolCardProps) {
    const { colors, themeMode, t } = useUiPrefs();
    const [isExpanded, setIsExpanded] = useState(false);
    const progress = useSharedValue(0);
    const hasResult = toolInvocation.state === "result";
    const status = resolveToolStatus(toolInvocation);
    const presentation = statusPresentation(status);
    const isActive = status === "running";

    useEffect(() => {
        progress.value = withTiming(isExpanded ? 1 : 0, {
            duration: 260,
            easing: Easing.out(Easing.cubic),
            reduceMotion: ReduceMotion.System,
        });
    }, [isExpanded, progress]);

    const chevronStyle = useAnimatedStyle(() => ({
        transform: [{ rotate: `${progress.value * 180}deg` }],
    }));

    const accent = presentation.color;
    const iconName = resolveToolIconName(toolInvocation.toolName);
    const readableResult = buildReadableResult(toolInvocation, t);

    return (
        <View style={styles.wrap}>
            {isActive ? <View style={[styles.activeGlow, { backgroundColor: "rgba(59,130,246,0.055)" }]} /> : null}
            <Animated.View
                layout={CARD_LAYOUT}
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
                                    backgroundColor: `${accent}18`,
                                    borderColor: `${accent}30`,
                                },
                            ]}
                        >
                            <MaterialCommunityIcons
                                name={iconName as never}
                                size={10}
                                color={accent}
                            />
                            {isActive ? <View style={[styles.pingRing, { borderColor: "rgba(59,130,246,0.20)" }]} /> : null}
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
                                color: accent,
                                backgroundColor: `${accent}14`,
                                borderColor: `${accent}24`,
                            },
                        ]}>
                            {t(presentation.key)}
                        </Text>
                    </View>

                    <Animated.View style={chevronStyle}>
                        <MaterialCommunityIcons
                            name="chevron-down"
                            size={18}
                            color={isExpanded ? accent : colors.textSoft}
                        />
                    </Animated.View>
                </Pressable>

                {isExpanded ? (
                    <Animated.View
                        entering={CONTENT_ENTER}
                        exiting={CONTENT_EXIT}
                        layout={CARD_LAYOUT}
                        style={styles.content}
                    >
                        {toolInvocation.clientSurface?.summary && !toolInvocation.clientSurface.summary.startsWith("{") && !toolInvocation.clientSurface.summary.startsWith("[") ? (
                            <Text style={[styles.summary, { color: colors.textMuted }]}>
                                {toolInvocation.clientSurface.summary}
                            </Text>
                        ) : null}
                        <View style={styles.section}>
                            <Text style={[styles.sectionLabel, { color: colors.textSoft }]}>
                                {t("src.components.chat.toolcard.input")}
                            </Text>
                            <CodeBlock language="json" value={stringifyPayload(toolInvocation.args ?? {})} />
                        </View>

                        {hasResult && !hideResult ? (
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
            </Animated.View>
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
        marginVertical: 0.5,
        position: "relative",
    },
    activeGlow: {
        position: "absolute",
        inset: 0,
        borderRadius: 8,
    },
    card: {
        width: "100%",
        overflow: "hidden",
        borderRadius: 8,
        borderWidth: 1,
    },
    header: {
        minHeight: 22,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        paddingHorizontal: 6,
        paddingVertical: 2,
    },
    headerLeft: {
        flexDirection: "row",
        alignItems: "center",
        gap: 4,
        flex: 1,
        minWidth: 0,
    },
    iconWrap: {
        width: 14,
        height: 14,
        borderRadius: 4,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
        position: "relative",
    },
    pingRing: {
        position: "absolute",
        inset: -1,
        borderRadius: 5,
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
        paddingHorizontal: 4,
        paddingVertical: 0.5,
        fontSize: 8,
        lineHeight: 11,
        fontWeight: "800",
        flexShrink: 0,
    },
    summary: {
        paddingHorizontal: 6,
        paddingBottom: 4,
        marginTop: -1,
        fontSize: 10,
        lineHeight: 14,
        fontWeight: "600",
    },
    content: {
        paddingHorizontal: 6,
        paddingBottom: 4,
        paddingTop: 0.5,
        gap: 4,
    },
    section: {
        gap: 2,
    },
    sectionLabel: {
        fontSize: 9,
        fontWeight: "700",
        letterSpacing: 1.2,
        textTransform: "uppercase",
    },
});
