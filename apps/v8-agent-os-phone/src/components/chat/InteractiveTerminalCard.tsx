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
    isActiveCommandSessionStatus,
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

const ANSI_ESCAPE_PATTERN = /\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1B\\))/g;

function cleanTerminalOutput(value: string) {
    return String(value || "")
        .replace(ANSI_ESCAPE_PATTERN, "")
        .replace(/\r\n/g, "\n")
        .replace(/\r/g, "\n");
}

function appendTerminalOutput(current: string, chunk: string) {
    const cleaned = cleanTerminalOutput(chunk);
    return cleaned ? trimTerminalBuffer(`${current}${cleaned}`) : current;
}

function normalizeTerminalScreen(value: string) {
    return trimTerminalBuffer(cleanTerminalOutput(value || ""), 32000);
}

function processOutputPath(process: AdminProcessRef) {
    const explicitOutputPath = String(process.outputAdminPath || "").trim();
    if (explicitOutputPath) {
        return explicitOutputPath;
    }
    const explicitStreamPath = String(process.streamAdminPath || "").trim();
    const sourcePath = explicitStreamPath || `/api/client/bg_processes/${encodeURIComponent(process.processId)}/ws`;
    let pathOnly = sourcePath;
    if (/^[a-z][a-z0-9+.-]*:\/\//i.test(sourcePath)) {
        try {
            const parsed = new URL(sourcePath);
            pathOnly = `${parsed.pathname}${parsed.search || ""}`;
        } catch {
            pathOnly = "";
        }
    }
    return pathOnly.replace(/\/ws(?:\?.*)?$/i, "");
}

export const InteractiveTerminalCard = memo(function InteractiveTerminalCard({
    process,
    compact = false,
    onTerminated,
}: InteractiveTerminalCardProps) {
    const processRecord = process as AdminProcessRef & { stableScreenSnapshot?: string };
    const { adminBaseUrl, authorizedFetch } = useAppSession();
    const { colors, themeMode, t } = useUiPrefs();
    const [isRunning, setIsRunning] = useState(() => isActiveCommandSessionStatus(process.status));
    const [isCollapsed, setIsCollapsed] = useState(compact);
    const [terminalOutput, setTerminalOutput] = useState(() => normalizeTerminalScreen(String(processRecord.stableScreenSnapshot || process.screenSnapshot || "")));
    const [inputText, setInputText] = useState("");
    const [sensitiveInput, setSensitiveInput] = useState(false);
    const [sendingInput, setSendingInput] = useState(false);
    const [pollingEnabled, setPollingEnabled] = useState(false);
    const rotation = useRef(new Animated.Value(compact ? 1 : 0)).current;
    const scrollRef = useRef<ScrollView | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const wsOpenedRef = useRef(false);
    const notifiedTerminationRef = useRef(false);
    const outputPath = useMemo(
        () => processOutputPath(process),
        [process.outputAdminPath, process.processId, process.streamAdminPath],
    );
    const prefersScreenPolling = Boolean(process.usesTty && process.interactive);

    useEffect(() => {
        setIsRunning(isActiveCommandSessionStatus(process.status));
    }, [process.status]);

    useEffect(() => {
        const screenSnapshot = normalizeTerminalScreen(String(processRecord.stableScreenSnapshot || process.screenSnapshot || ""));
        if (screenSnapshot) {
            setTerminalOutput(screenSnapshot);
        }
    }, [process.screenSnapshot, processRecord.stableScreenSnapshot]);

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

        if (prefersScreenPolling) {
            setPollingEnabled(true);
            return undefined;
        }

        const wsUrl = resolveAdminProcessWsUrl("phone", adminBaseUrl, process);
        if (!wsUrl) {
            setPollingEnabled(true);
            return undefined;
        }
        let disposed = false;
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;
        wsOpenedRef.current = false;
        notifiedTerminationRef.current = false;

        ws.onopen = () => {
            if (disposed) {
                return;
            }
            wsOpenedRef.current = true;
            setPollingEnabled(false);
        };

        ws.onmessage = (event) => {
            if (disposed) {
                return;
            }
            const nextChunk = typeof event.data === "string" ? event.data : String(event.data ?? "");
            if (!nextChunk) {
                return;
            }
            setTerminalOutput((current) => appendTerminalOutput(current, nextChunk));
        };

        ws.onclose = () => {
            if (disposed) {
                return;
            }
            if (!wsOpenedRef.current) {
                setPollingEnabled(true);
                return;
            }
            setIsRunning(false);
            setTerminalOutput((current) => appendTerminalOutput(current, "\n[Process terminated]"));
        };

        ws.onerror = () => {
            if (disposed) {
                return;
            }
            if (!wsOpenedRef.current) {
                setPollingEnabled(true);
                return;
            }
            setIsRunning(false);
            setTerminalOutput((current) => appendTerminalOutput(current, "\n[Connection error]"));
        };

        return () => {
            disposed = true;
            ws.close();
            wsRef.current = null;
        };
    }, [adminBaseUrl, prefersScreenPolling, process]);

    useEffect(() => {
        if (!pollingEnabled || !process?.processId) {
            return undefined;
        }
        if (!outputPath) {
            return undefined;
        }
        let cancelled = false;
        const poll = async () => {
            try {
                const response = await authorizedFetch(outputPath);
                if (!response.ok) {
                    return;
                }
                const payload = await response.json() as {
                    output?: string;
                    stableScreenSnapshot?: string;
                    screenSnapshot?: string;
                    awaitingInput?: boolean;
                    is_running?: boolean;
                    isRunning?: boolean;
                    process?: {
                        status?: string;
                        is_running?: boolean;
                        stable_screen_snapshot?: string;
                        screen_snapshot?: string;
                    };
                };
                if (cancelled) {
                    return;
                }
                const nextScreen = normalizeTerminalScreen(
                    String(payload.stableScreenSnapshot || payload.screenSnapshot || payload.process?.stable_screen_snapshot || payload.process?.screen_snapshot || ""),
                );
                if (prefersScreenPolling && nextScreen) {
                    setTerminalOutput(nextScreen);
                } else if (payload.output) {
                    setTerminalOutput((current) => appendTerminalOutput(current, payload.output || ""));
                }
                const stillRunning = typeof payload.process?.status === "string"
                    ? isActiveCommandSessionStatus(payload.process.status)
                    : Boolean(payload.is_running ?? payload.isRunning ?? payload.process?.is_running);
                setIsRunning(stillRunning);
                if (!stillRunning) {
                    setPollingEnabled(false);
                }
            } catch {
                // Polling is a best-effort fallback for mobile WebSocket failures.
            }
        };
        void poll();
        const timer = setInterval(() => void poll(), 1200);
        return () => {
            cancelled = true;
            clearInterval(timer);
        };
    }, [authorizedFetch, outputPath, pollingEnabled, prefersScreenPolling, process.processId]);

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
            const inputPath = sensitiveInput
                ? process.inputAdminPath.replace(/\/input(?:\?.*)?$/i, "/sensitive-input")
                : process.inputAdminPath;
            await authorizedFetch(inputPath, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(sensitiveInput
                    ? { input_text: inputText, secret_type: "terminal_secret" }
                    : { input_text: inputText }),
            });
            setInputText("");
            setSensitiveInput(false);
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
        () => String(process.title || process.commandPreview || shortId || t("src.components.chat.interactiveterminalcard.background_process")).trim(),
        [process.commandPreview, process.title, shortId, t],
    );
    const inputEnabled = Boolean(process.canInput) && isRunning;
    const canTerminate = Boolean(process.canTerminate) && isRunning;
    const encodingWarning = useMemo(() => {
        const state = String(process.encodingState || "").trim().toLowerCase();
        if (!state || state === "clean") {
            return "";
        }
        const note = String(process.encodingNotes || "").trim();
        return note || t("src.components.chat.interactiveterminalcard.terminal_encoding_looks_abnormal_content_may_be_distorted");
    }, [process.encodingNotes, process.encodingState, t]);

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
                            {isRunning ? t("src.components.chat.interactiveterminalcard.running") : t("src.components.chat.interactiveterminalcard.stopped")}
                        </Text>
                    </View>
                </View>

                <View style={styles.headerActions}>
                    {canTerminate ? (
                        <Button variant="destructive" size="sm" onPress={handleTerminate}>
                            {t("src.components.chat.interactiveterminalcard.stop")}
                        </Button>
                    ) : null}
                    <Animated.View style={{ transform: [{ rotate: chevronRotation }] }}>
                        <MaterialCommunityIcons name="chevron-down" size={18} color={colors.textSoft} />
                    </Animated.View>
                </View>
            </Pressable>

            {!isCollapsed ? (
                <CardContent style={styles.content}>
                    {encodingWarning ? (
                        <View style={[styles.warningBanner, { backgroundColor: themeMode === "dark" ? "rgba(245,158,11,0.14)" : "rgba(254,243,199,0.92)", borderColor: colors.warning }]}>
                            <MaterialCommunityIcons name="alert-circle-outline" size={14} color={colors.warning} />
                            <Text style={[styles.warningText, { color: colors.warning }]}>
                                {encodingWarning}
                            </Text>
                        </View>
                    ) : null}
                    <View style={[styles.terminalFrame, { backgroundColor: "#020617", borderColor: colors.border }]}>
                        <ScrollView
                            ref={scrollRef}
                            style={[styles.terminalScroll, compact && styles.terminalScrollCompact]}
                            contentContainerStyle={styles.terminalScrollContent}
                            onContentSizeChange={() => scrollRef.current?.scrollToEnd({ animated: true })}
                        >
                            <Text style={styles.terminalText}>
                                {terminalOutput || t("src.components.chat.interactiveterminalcard.waiting_for_terminal_output")}
                            </Text>
                        </ScrollView>
                    </View>

                    <View style={styles.inputRow}>
                        <Input
                            value={inputText}
                            onChangeText={setInputText}
                            placeholder={
                                sensitiveInput
                                    ? t("src.components.chat.interactiveterminalcard.send_sensitive_input_once")
                                    : t("src.components.chat.interactiveterminalcard.send_input_to_background_command")
                            }
                            editable={inputEnabled && !sendingInput}
                            style={styles.input}
                            secureTextEntry={sensitiveInput}
                            onSubmitEditing={() => void handleSendInput()}
                            returnKeyType="send"
                        />
                        <Pressable
                            disabled={!inputEnabled || sendingInput}
                            onPress={() => setSensitiveInput((current) => !current)}
                            style={[
                                styles.secretToggle,
                                {
                                    borderColor: sensitiveInput ? colors.warning : colors.border,
                                    backgroundColor: sensitiveInput
                                        ? (themeMode === "dark" ? "rgba(245,158,11,0.16)" : "rgba(254,243,199,0.92)")
                                        : colors.surface,
                                    opacity: inputEnabled && !sendingInput ? 1 : 0.55,
                                },
                            ]}
                        >
                            <MaterialCommunityIcons
                                name={sensitiveInput ? "lock-check-outline" : "lock-outline"}
                                size={15}
                                color={sensitiveInput ? colors.warning : colors.textMuted}
                            />
                        </Pressable>
                        <Button
                            size="sm"
                            onPress={() => void handleSendInput()}
                            disabled={!inputEnabled || sendingInput || !inputText.trim()}
                            loading={sendingInput}
                        >
                            {t("src.components.chat.interactiveterminalcard.send")}
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
    warningBanner: {
        borderRadius: radii.md,
        borderWidth: 1,
        paddingHorizontal: 10,
        paddingVertical: 8,
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 8,
    },
    warningText: {
        flex: 1,
        fontSize: 11,
        lineHeight: 16,
        fontWeight: "600",
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
    secretToggle: {
        width: 40,
        height: 40,
        borderRadius: radii.sm,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
});
