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

import { useUiPrefs } from "@/src/providers/ui-prefs";

type ThinkingCardProps = {
    content: string;
    isStreaming?: boolean;
    elapsedTime?: number;
    data?: {
        startTime?: number;
        endTime?: number;
    };
};

export const ThinkingCard = memo(function ThinkingCard({
    content,
    isStreaming = false,
    elapsedTime,
    data,
}: ThinkingCardProps) {
    const { colors, themeMode, t } = useUiPrefs();
    const [isExpanded, setIsExpanded] = useState(false);
    const [currentElapsedTime, setCurrentElapsedTime] = useState(elapsedTime || 0);
    const hasAutoExpanded = useRef(false);
    const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const progress = useRef(new Animated.Value(0)).current;

    useEffect(() => {
        Animated.timing(progress, {
            toValue: isExpanded ? 1 : 0,
            duration: 260,
            easing: Easing.out(Easing.cubic),
            useNativeDriver: true,
        }).start();
    }, [isExpanded, progress]);

    useEffect(() => {
        if (isStreaming && !hasAutoExpanded.current) {
            const timer = setTimeout(() => {
                setIsExpanded(true);
                hasAutoExpanded.current = true;
            }, 0);
            return () => clearTimeout(timer);
        }
        return undefined;
    }, [isStreaming]);

    useEffect(() => {
        if (!isStreaming && hasAutoExpanded.current) {
            const timer = setTimeout(() => {
                setIsExpanded(false);
                hasAutoExpanded.current = false;
            }, 800);
            return () => clearTimeout(timer);
        }
        return undefined;
    }, [isStreaming]);

    useEffect(() => {
        if (isStreaming && data?.startTime) {
            timerRef.current = setInterval(() => {
                setCurrentElapsedTime(Date.now() - data.startTime!);
            }, 100);
            return () => {
                if (timerRef.current) {
                    clearInterval(timerRef.current);
                    timerRef.current = null;
                }
            };
        }
        return undefined;
    }, [data?.startTime, isStreaming]);

    useEffect(() => {
        if (!isStreaming && elapsedTime !== undefined) {
            const timer = setTimeout(() => setCurrentElapsedTime(elapsedTime), 0);
            return () => clearTimeout(timer);
        }
        return undefined;
    }, [elapsedTime, isStreaming]);

    if (!content) {
        return null;
    }

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

    const formatTime = (ms: number) => {
        if (ms < 1000) return `${ms}ms`;
        return `${(ms / 1000).toFixed(1)}s`;
    };

    const wrapperBackground = isExpanded
        ? (themeMode === "dark" ? "rgba(15,23,42,0.38)" : "rgba(255,255,255,0.50)")
        : (themeMode === "dark" ? "rgba(15,23,42,0.18)" : "rgba(255,255,255,0.20)");
    const wrapperBorder = isExpanded
        ? "rgba(139,92,246,0.20)"
        : (themeMode === "dark" ? "rgba(255,255,255,0.055)" : "rgba(15,23,42,0.055)");

    return (
        <View style={styles.wrap}>
            {isStreaming ? <View style={[styles.activeGlow, { backgroundColor: "rgba(139,92,246,0.065)" }]} /> : null}
            <View
                style={[
                    styles.card,
                    {
                        backgroundColor: wrapperBackground,
                        borderColor: wrapperBorder,
                    },
                ]}
            >
                <Pressable style={styles.header} onPress={() => setIsExpanded((value) => !value)}>
                    <View style={styles.headerLeft}>
                        <View
                            style={[
                                styles.iconWrap,
                                {
                                    backgroundColor: isStreaming
                                        ? "rgba(139,92,246,0.12)"
                                        : (themeMode === "dark" ? "rgba(39,39,42,0.72)" : "rgba(241,245,249,0.72)"),
                                    borderColor: isStreaming
                                        ? "rgba(139,92,246,0.22)"
                                        : (themeMode === "dark" ? "rgba(255,255,255,0.06)" : "rgba(15,23,42,0.06)"),
                                },
                            ]}
                        >
                            <MaterialCommunityIcons
                                name="brain"
                                size={13}
                                color={isStreaming ? colors.primary : colors.textMuted}
                            />
                            {isStreaming ? <View style={[styles.pingRing, { borderColor: "rgba(139,92,246,0.20)" }]} /> : null}
                        </View>

                        <Text style={[styles.title, { color: isExpanded ? colors.text : colors.textMuted }]}>
                            {t("src.components.chat.thinkingcard.reasoning")}
                        </Text>

                        {isStreaming ? (
                            <Text style={[styles.time, { color: colors.primary }]}>
                                {formatTime(currentElapsedTime)}
                            </Text>
                        ) : currentElapsedTime > 0 ? (
                            <Text style={[styles.time, { color: colors.textSoft }]}>
                                {formatTime(currentElapsedTime)}
                            </Text>
                        ) : null}
                    </View>

                    <Animated.View style={{ transform: [{ rotate }] }}>
                        <MaterialCommunityIcons
                            name="chevron-down"
                            size={18}
                            color={isExpanded ? colors.primary : colors.textSoft}
                        />
                    </Animated.View>
                </Pressable>

                {isExpanded ? (
                    <Animated.View
                        style={[
                            styles.contentOuter,
                            {
                                opacity: contentOpacity,
                                transform: [{ translateY: contentTranslateY }],
                            },
                        ]}
                    >
                        <View
                            style={[
                                styles.contentInner,
                                {
                                    backgroundColor: themeMode === "dark" ? "rgba(0,0,0,0.18)" : "rgba(15,23,42,0.04)",
                                    borderColor: themeMode === "dark" ? "rgba(255,255,255,0.06)" : "rgba(15,23,42,0.05)",
                                },
                            ]}
                        >
                            <Text selectable style={[styles.contentText, { color: themeMode === "dark" ? "#A1A1AA" : "#52525B" }]}>
                                {content}
                            </Text>
                            {isStreaming ? <View style={[styles.cursor, { backgroundColor: colors.primary }]} /> : null}
                        </View>
                    </Animated.View>
                ) : null}
            </View>
        </View>
    );
});

const styles = StyleSheet.create({
    wrap: {
        width: "100%",
        marginVertical: 2,
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
        minHeight: 32,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        paddingHorizontal: 10,
        paddingVertical: 6,
    },
    headerLeft: {
        flexDirection: "row",
        alignItems: "center",
        gap: 7,
        flex: 1,
    },
    iconWrap: {
        width: 20,
        height: 20,
        borderRadius: 7,
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
        letterSpacing: 0.2,
    },
    time: {
        fontSize: 10,
        fontWeight: "600",
    },
    contentOuter: {
        paddingHorizontal: 10,
        paddingBottom: 10,
        paddingTop: 2,
    },
    contentInner: {
        borderRadius: 10,
        borderWidth: 1,
        paddingHorizontal: 9,
        paddingVertical: 8,
        flexDirection: "row",
        alignItems: "flex-end",
        flexWrap: "wrap",
        gap: 4,
    },
    contentText: {
        fontSize: 11,
        lineHeight: 17,
        fontFamily: "monospace",
        flexShrink: 1,
    },
    cursor: {
        width: 6,
        height: 14,
        borderRadius: 3,
        marginBottom: 1,
    },
});
