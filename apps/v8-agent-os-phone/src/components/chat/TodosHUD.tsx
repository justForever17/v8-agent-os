import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Animated, Easing, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { Card, CardContent } from "@/src/components/ui/card";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type { SessionTodoItem } from "@/src/types/admin";

type TodosHUDProps = {
    items: SessionTodoItem[];
    shouldAutoHide?: boolean;
    dismissDelayMs?: number;
};

export const TodosHUD = memo(function TodosHUD({
    items,
    shouldAutoHide = false,
    dismissDelayMs = 2600,
}: TodosHUDProps) {
    const { colors, themeMode, t } = useUiPrefs();
    const [isCollapsed, setIsCollapsed] = useState(true);
    const [dismissed, setDismissed] = useState(false);
    const progress = useRef(new Animated.Value(0)).current;

    const todos = useMemo(
        () => items.filter((item) => String(item.content || "").trim()),
        [items],
    );
    const completedCount = todos.filter((item) => item.status === "done").length;
    const todosSignature = useMemo(
        () => todos.map((item, index) => `${item.id || index}:${item.status}:${String(item.content || "").trim()}`).join("|"),
        [todos],
    );
    const allCompleted = todos.length > 0 && todos.every((item) => item.status === "done" || item.status === "skipped");

    useEffect(() => {
        setDismissed(false);
    }, [todosSignature]);

    useEffect(() => {
        if (!shouldAutoHide || !allCompleted || todos.length === 0) {
            return undefined;
        }
        const timer = setTimeout(() => {
            setDismissed(true);
        }, dismissDelayMs);
        return () => clearTimeout(timer);
    }, [allCompleted, dismissDelayMs, shouldAutoHide, todos.length]);

    if (todos.length === 0 || dismissed) {
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
                        backgroundColor: themeMode === "dark" ? "rgba(28,25,23,0.72)" : "rgba(255,255,255,0.74)",
                        borderBottomColor: isCollapsed ? "transparent" : colors.border,
                    },
                ]}
                onPress={toggle}
            >
                <View style={styles.headerLeft}>
                    <View style={[styles.iconWrap, { backgroundColor: "rgba(124,58,237,0.12)" }]}>
                        <MaterialCommunityIcons name="format-list-checks" size={16} color={colors.primary} />
                    </View>
                    <Text style={[styles.title, { color: colors.text }]}>{t("src.components.chat.todoshud.task_progress")}</Text>
                    <View style={[styles.counterPill, { backgroundColor: themeMode === "dark" ? "rgba(255,255,255,0.08)" : "rgba(241,245,249,0.96)" }]}>
                        <Text style={[styles.counterText, { color: colors.textMuted }]}>{completedCount}/{todos.length}</Text>
                    </View>
                </View>
                <Animated.View style={{ transform: [{ rotate: rotation }] }}>
                    <MaterialCommunityIcons name="chevron-down" size={18} color={colors.textSoft} />
                </Animated.View>
            </Pressable>

            {!isCollapsed ? (
                <CardContent style={styles.content}>
                    <ScrollView
                        nestedScrollEnabled
                        keyboardShouldPersistTaps="handled"
                        directionalLockEnabled
                        overScrollMode="auto"
                        showsVerticalScrollIndicator={todos.length > 4}
                        scrollEventThrottle={16}
                        style={styles.scrollArea}
                        contentContainerStyle={styles.scrollContent}
                        onTouchStart={(event) => event.stopPropagation()}
                    >
                        {todos.map((todo) => {
                            const status = String(todo.status || "pending");
                            const isDone = status === "done";
                            const isProgress = status === "in_progress";
                            const isSkipped = status === "skipped";
                            return (
                                <View
                                    key={String(todo.id || todo.content)}
                                    style={[
                                        styles.todoRow,
                                        {
                                            backgroundColor: isProgress
                                                ? "rgba(124,58,237,0.1)"
                                                : themeMode === "dark"
                                                    ? "rgba(255,255,255,0.04)"
                                                    : "rgba(248,250,252,0.92)",
                                            borderColor: isProgress ? "rgba(124,58,237,0.18)" : colors.border,
                                        },
                                    ]}
                                >
                                    <View style={styles.todoIcon}>
                                        {isDone ? (
                                            <MaterialCommunityIcons name="check-circle" size={16} color="#16A34A" />
                                        ) : isProgress ? (
                                            <MaterialCommunityIcons name="progress-clock" size={16} color={colors.primary} />
                                        ) : isSkipped ? (
                                            <MaterialCommunityIcons name="minus-circle-outline" size={16} color={colors.textSoft} />
                                        ) : (
                                            <MaterialCommunityIcons name="circle-outline" size={16} color={colors.textSoft} />
                                        )}
                                    </View>
                                    <Text
                                        style={[
                                            styles.todoText,
                                            {
                                                color: isDone ? colors.textMuted : colors.text,
                                                textDecorationLine: isDone || isSkipped ? "line-through" : "none",
                                            },
                                        ]}
                                    >
                                        {String(todo.content || "").trim()}
                                    </Text>
                                </View>
                            );
                        })}
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
        borderRadius: radii.pill,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 8,
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
        gap: spacing.xs,
    },
    todoRow: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 10,
        borderRadius: 16,
        borderWidth: 1,
        paddingHorizontal: 11,
        paddingVertical: 10,
    },
    todoIcon: {
        width: 16,
        alignItems: "center",
        paddingTop: 1,
    },
    todoText: {
        flex: 1,
        fontSize: 12,
        lineHeight: 18,
    },
});
