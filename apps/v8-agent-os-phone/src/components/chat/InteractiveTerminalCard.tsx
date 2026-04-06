import { memo, useEffect, useMemo, useRef, useState } from "react";
import {
    Animated,
    Easing,
    Pressable,
    ScrollView,
    StyleSheet,
    Text,
    View,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import {
    resolveAdminProcessWsUrl,
    type AdminProcessRef,
} from "@v8/session-realtime";

import { Button } from "@/src/components/ui/button";
import { Card, CardContent } from "@/src/components/ui/card";
import { Input } from "@/src/components/ui/input";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

type InteractiveTerminalCardProps = {
    process: AdminProcessRef;
    compact?: boolean;
    onTerminated?: (processId: string) => void;
};

function trimTerminalBuffer(value: string, limit = 24000) {
    if (value.length <= limit) {
        return value;
    }
    return value.slice(value.length - limit);
}

export const InteractiveTerminalCard = memo(function InteractiveTerminalCard({
    process,
    compact = false,
    onTerminated,
}: InteractiveTerminalCardProps) {
    const { adminBaseUrl, authorizedFetch } = useAppSession();
    const { colors, themeMode, t } = useUiPrefs();
    const [isRunning, setIsRunning] = useState(() => String(process.status || "").trim().toLowerCase() !== "stopped");
    const [isCollapsed, setIsCollapsed] = useState(compact);
    const [terminalOutput, setTerminalOutput] = useState("");
    const [inputText, setInputText] = useState("");
    const [sendingInput, setSendingInput] = useState(false);
    const rotation = useRef(new Animated.Value(compact ? 1 : 0)).current;
    const scrollRef = useRef<ScrollView | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const notifiedTerminationRef = useRef(false);

    useEffect(() => {
        setIsRunning(String(process.status || "").trim().toLowerCase() !== "stopped");
    }, [process.status]);

    useEffect(() => {
        Animated.timing(rotation, {
            toValue: isCollapsed ? 1 : 0,
            duration: 220,
            easing: Easing.out(Easing.cubic),
            useNativeDriver: true,
        }).start();
    }, [isCollapsed, rotation]);

    useEffect(() => {
        if (!process?.processId) {
            return undefined;
        }

        const wsUrl = resolveAdminProcessWsUrl("phone", adminBaseUrl, process);
        if (!wsUrl) {
            return undefined;
        }
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;
        notifiedTerminationRef.current = false;

        ws.onmessage = (event) => {
            const nextChunk = typeof event.data === "string" ? event.data : String(event.data ?? "");
            if (!nextChunk) {
                return;
            }
            setTerminalOutput((current) => trimTerminalBuffer(`${current}${nextChunk}`));
        };

        ws.onclose = () => {
            setIsRunning(false);
            setTerminalOutput((current) => trimTerminalBuffer(`${current}\n[Process terminated]`));
            if (!notifiedTerminationRef.current) {
                notifiedTerminationRef.current = true;
                onTerminated?.(process.processId);
            }
        };

        ws.onerror = () => {
            setIsRunning(false);
            setTerminalOutput((current) => trimTerminalBuffer(`${current}\n[Connection error]`));
            if (!notifiedTerminationRef.current) {
                notifiedTerminationRef.current = true;
                onTerminated?.(process.processId);
            }
        };

        return () => {
            ws.close();
            wsRef.current = null;
        };
    }, [adminBaseUrl, onTerminated, process]);

    const handleTerminate = async () => {
        if (!process?.terminateAdminPath) {
            return;
        }
        try {
            await authorizedFetch(process.terminateAdminPath, {
                method: "POST",
            });
        } catch {
            // Best-effort termination mirrors the web behavior.
        } finally {
            setIsRunning(false);
            if (!notifiedTerminationRef.current) {
                notifiedTerminationRef.current = true;
                onTerminated?.(process.processId);
            }
        }
    };

    const handleSendInput = async () => {
        if (!inputText.trim() || !process?.inputAdminPath) {
            return;
        }
        setSendingInput(true);
        try {
            await authorizedFetch(process.inputAdminPath, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ input_text: inputText }),
            });
            setInputText("");
        } finally {
            setSendingInput(false);
        }
    };

    const chevronRotation = rotation.interpolate({
        inputRange: [0, 1],
        outputRange: ["0deg", "-90deg"],
    });

    const shortId = useMemo(
        () => {
            const id = String(process.commandId || process.processId || "").trim();
            return id.length > 12 ? `…${id.slice(-8)}` : id;
        },
        [process.commandId, process.processId],
    );
    const title = useMemo(
        () => String(process.title || process.commandPreview || shortId || t("后台进程", "Background process")).trim(),
        [process.commandPreview, process.title, shortId, t],
    );
    const inputEnabled = Boolean(process.canInput) && isRunning;
    const canTerminate = Boolean(process.canTerminate) && isRunning;

    return (
        <Card style={styles.wrap}>
            <Pressable
                style={[
                    styles.header,
                    {
                        backgroundColor: themeMode === "dark" ? "rgba(24,24,27,0.92)" : "rgba(241,245,249,0.96)",
                        borderBottomColor: isCollapsed ? "transparent" : colors.border,
                    },
                ]}
                onPress={() => setIsCollapsed((current) => !current)}
            >
                <View style={styles.headerLeft}>
                    <View style={[styles.titleIcon, { backgroundColor: themeMode === "dark" ? "rgba(39,39,42,0.94)" : "#FFFFFF", borderColor: colors.border }]}>
                        <MaterialCommunityIcons name="console-line" size={14} color={colors.textMuted} />
                    </View>
                    <Text style={[styles.commandId, { color: colors.textMuted }]} numberOfLines={1}>
                        {title}
                    </Text>
                    <View style={styles.statusWrap}>
                        <View
                            style={[
                                styles.statusDot,
                                { backgroundColor: isRunning ? "#10B981" : colors.textSoft },
                            ]}
                        />
                        <Text style={[styles.statusText, { color: colors.textMuted }]}>
                            {isRunning ? t("运行中", "Running") : t("已结束", "Stopped")}
                        </Text>
                    </View>
                </View>

                <View style={styles.headerActions}>
                    {canTerminate ? (
                        <Button variant="destructive" size="sm" onPress={handleTerminate}>
                            {t("停止", "Stop")}
                        </Button>
                    ) : null}
                    <Animated.View style={{ transform: [{ rotate: chevronRotation }] }}>
                        <MaterialCommunityIcons name="chevron-down" size={18} color={colors.textSoft} />
                    </Animated.View>
                </View>
            </Pressable>

            {!isCollapsed ? (
                <CardContent style={styles.content}>
                    <View style={[styles.terminalFrame, { backgroundColor: "#020617", borderColor: colors.border }]}>
                        <ScrollView
                            ref={scrollRef}
                            style={[styles.terminalScroll, compact && styles.terminalScrollCompact]}
                            contentContainerStyle={styles.terminalScrollContent}
                            onContentSizeChange={() => scrollRef.current?.scrollToEnd({ animated: true })}
                        >
                            <Text style={styles.terminalText}>
                                {terminalOutput || t("等待命令输出…", "Waiting for terminal output...")}
                            </Text>
                        </ScrollView>
                    </View>

                    <View style={styles.inputRow}>
                        <Input
                            value={inputText}
                            onChangeText={setInputText}
                            placeholder={t("向后台命令发送输入", "Send input to background command")}
                            editable={inputEnabled && !sendingInput}
                            style={styles.input}
                            onSubmitEditing={() => void handleSendInput()}
                            returnKeyType="send"
                        />
                        <Button
                            size="sm"
                            onPress={() => void handleSendInput()}
                            disabled={!inputEnabled || sendingInput || !inputText.trim()}
                            loading={sendingInput}
                        >
                            {t("发送", "Send")}
                        </Button>
                    </View>
                </CardContent>
            ) : null}
        </Card>
    );
});

const styles = StyleSheet.create({
    wrap: {
        overflow: "hidden",
    },
    header: {
        minHeight: 46,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: spacing.sm,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.sm,
        borderBottomWidth: StyleSheet.hairlineWidth,
    },
    headerLeft: {
        flex: 1,
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
        minWidth: 0,
    },
    titleIcon: {
        width: 24,
        height: 24,
        borderRadius: 8,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    commandId: {
        flexShrink: 1,
        fontSize: 11,
        fontWeight: "700",
        letterSpacing: 0.4,
    },
    statusWrap: {
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
    },
    statusDot: {
        width: 8,
        height: 8,
        borderRadius: 999,
    },
    statusText: {
        fontSize: 11,
        fontWeight: "600",
    },
    headerActions: {
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
    },
    content: {
        gap: spacing.sm,
        paddingTop: spacing.sm,
    },
    terminalFrame: {
        borderRadius: radii.md,
        borderWidth: 1,
        overflow: "hidden",
    },
    terminalScroll: {
        maxHeight: 220,
    },
    terminalScrollCompact: {
        maxHeight: 140,
    },
    terminalScrollContent: {
        paddingHorizontal: 12,
        paddingVertical: 10,
    },
    terminalText: {
        color: "#E2E8F0",
        fontSize: 12,
        lineHeight: 18,
        fontFamily: "monospace",
    },
    inputRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
    },
    input: {
        flex: 1,
    },
});
