import { memo, useEffect, useRef, useState } from "react";
import {
    Platform,
    Pressable,
    StyleSheet,
    TextInput,
    View,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";
import Animated, {
    Easing,
    cancelAnimation,
    useAnimatedStyle,
    useSharedValue,
    withRepeat,
    withTiming,
} from "react-native-reanimated";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";
import type { CommandPresetSummary, SkillReferenceSummary, UploadedWorkspaceFile } from "@/src/types/admin";

function ComposerActionButton({
    mode,
    disabled,
    onPress,
    colors,
}: {
    mode: "send" | "stop" | "busy";
    disabled: boolean;
    onPress: () => void;
    colors: ReturnType<typeof useUiPrefs>["colors"];
}) {
    const spin = useSharedValue(0);
    const pulse = useSharedValue(0);
    const active = mode === "stop";

    useEffect(() => {
        if (!active) {
            cancelAnimation(spin);
            cancelAnimation(pulse);
            spin.value = withTiming(0, { duration: 180 });
            pulse.value = withTiming(0, { duration: 160 });
            return;
        }

        spin.value = withRepeat(
            withTiming(1, { duration: 1280, easing: Easing.linear }),
            -1,
            false,
        );
        pulse.value = withRepeat(
            withTiming(1, { duration: 860, easing: Easing.inOut(Easing.ease) }),
            -1,
            true,
        );

        return () => {
            cancelAnimation(spin);
            cancelAnimation(pulse);
        };
    }, [active, pulse, spin]);

    const orbitStyle = useAnimatedStyle(() => ({
        opacity: active && !disabled ? 1 : 0,
        transform: [{ rotate: `${spin.value * 360}deg` }],
    }));
    const pulseStyle = useAnimatedStyle(() => ({
        opacity: active && !disabled ? 0.18 + (pulse.value * 0.18) : 0,
        transform: [{ scale: 1 + (pulse.value * 0.08) }],
    }));
    const iconName: React.ComponentProps<typeof MaterialCommunityIcons>["name"] = mode === "stop"
        ? "stop"
        : mode === "busy"
            ? "progress-clock"
            : "send";
    const buttonColors = mode === "stop"
        ? ["#FFF7ED", "#FFF1F2"]
        : mode === "busy"
            ? ["#CBD5E1", "#CBD5E1"]
            : [colors.accent, "#F59E0B"];
    const orbitColors = mode === "stop"
        ? ["#FB7185", "#EF4444", "#F97316", "#FB7185"]
        : ["#F97316", "#F59E0B", "#7C3AED", "#F97316"];

    return (
        <Pressable
            accessibilityRole="button"
            accessibilityLabel={mode === "stop" ? "中断运行" : "发送消息"}
            disabled={disabled}
            onPress={onPress}
            style={[styles.sendWrap, disabled && styles.disabled]}
        >
            <Animated.View pointerEvents="none" style={[styles.sendPulse, pulseStyle]} />
            <Animated.View pointerEvents="none" style={[styles.sendOrbit, orbitStyle]}>
                <LinearGradient
                    colors={orbitColors as [string, string, string, string]}
                    start={{ x: 0, y: 0 }}
                    end={{ x: 1, y: 1 }}
                    style={styles.sendOrbitGradient}
                />
            </Animated.View>
            <LinearGradient
                colors={buttonColors as [string, string]}
                start={{ x: 0, y: 0 }}
                end={{ x: 1, y: 1 }}
                style={[styles.sendButton, mode === "stop" && styles.sendButtonStop]}
            >
                {mode === "stop" ? (
                    <View style={styles.stopGlyph} />
                ) : (
                    <MaterialCommunityIcons name={iconName} size={18} color="#FFFFFF" />
                )}
            </LinearGradient>
        </Pressable>
    );
}

export const Composer = memo(function Composer({
    value,
    onChange,
    onSend,
    busy = false,
    isRunning = false,
    canStop = false,
    onStop,
    selectedCommand,
    onClearCommand,
    selectedSkills,
    onRemoveSkill,
    taskPlanningMode,
    onToggleTaskPlanningMode,
    uploadedFiles,
    onRemoveUploadedFile,
    onPickAttachment,
    onToggleRecording,
    attachmentBusy = false,
    recording = false,
    transcribing = false,
}: {
    value: string;
    onChange: (next: string) => void;
    onSend: () => void;
    busy?: boolean;
    isRunning?: boolean;
    canStop?: boolean;
    onStop?: () => void;
    selectedCommand: CommandPresetSummary | null;
    onClearCommand: () => void;
    selectedSkills: SkillReferenceSummary[];
    onRemoveSkill: (skill: SkillReferenceSummary) => void;
    taskPlanningMode: boolean;
    onToggleTaskPlanningMode: () => void;
    uploadedFiles: UploadedWorkspaceFile[];
    onRemoveUploadedFile: (file: UploadedWorkspaceFile) => void;
    onPickAttachment: () => void;
    onToggleRecording: () => void;
    attachmentBusy?: boolean;
    recording?: boolean;
    transcribing?: boolean;
}) {
    const { colors, t, themeMode } = useUiPrefs();
    const [isFocused, setIsFocused] = useState(false);
    const inputRef = useRef<TextInput | null>(null);
    void onClearCommand;
    void onRemoveSkill;
    void onRemoveUploadedFile;
    const hasPayload = Boolean(value.trim() || selectedCommand || selectedSkills.length > 0 || uploadedFiles.length > 0);
    const stopAvailable = Boolean(isRunning && canStop && onStop);
    const canSend = hasPayload && !busy && !isRunning;
    const canAct = stopAvailable || canSend;
    const actionMode: "send" | "stop" | "busy" = stopAvailable
        ? "stop"
        : (busy || isRunning)
            ? "busy"
            : "send";
    const shellBackground = themeMode === "dark"
        ? "rgba(28,25,23,0.9)"
        : "rgba(255,255,255,0.94)";
    const shellBorder = isFocused
        ? (themeMode === "dark" ? "rgba(245,158,11,0.32)" : "rgba(249,115,22,0.28)")
        : colors.border;
    const editorBackground = themeMode === "dark"
        ? "rgba(17,24,39,0.46)"
        : "rgba(248,250,252,0.92)";
    const inputWebStyle: any = Platform.OS === "web"
        ? {
            outlineStyle: "none",
            boxShadow: "none",
            appearance: "none",
            WebkitAppearance: "none",
        }
        : null;
    const handlePrimaryAction = () => {
        if (stopAvailable && onStop) {
            onStop();
            return;
        }
        if (canSend) {
            onSend();
        }
    };

    return (
        <View style={styles.shell}>
            <View style={styles.inputStage}>
                <View
                    style={[
                        styles.composerCard,
                        {
                            backgroundColor: shellBackground,
                            borderColor: shellBorder,
                            shadowColor: themeMode === "dark" ? "#000000" : "#0F172A",
                        },
                    ]}
                >
                <View
                    pointerEvents="box-none"
                    style={[
                        styles.editorCard,
                        {
                            backgroundColor: editorBackground,
                                borderColor: isFocused ? `${colors.primary}2F` : "transparent",
                            },
                        ]}
                >
                        <TextInput
                            ref={inputRef}
                            value={value}
                            onChangeText={onChange}
                            onFocus={() => setIsFocused(true)}
                            onBlur={() => setIsFocused(false)}
                            placeholder={t("给 智能主管 发送消息...", "Message Supervisor...")}
                            placeholderTextColor={colors.textSoft}
                            multiline
                            editable
                            scrollEnabled
                            showSoftInputOnFocus
                            blurOnSubmit={false}
                            underlineColorAndroid="transparent"
                            selectionColor={colors.primary}
                            selectionHandleColor={colors.primary}
                            cursorColor={colors.accent}
                            caretHidden={false}
                            autoCorrect={false}
                            spellCheck={false}
                            autoComplete="off"
                            importantForAutofill="no"
                            selectTextOnFocus={false}
                            contextMenuHidden={false}
                            disableFullscreenUI
                            returnKeyType="default"
                            textAlignVertical="top"
                            style={[styles.input, { color: colors.text }, inputWebStyle]}
                        />
                    </View>

                    <View style={styles.bottomControls}>
                        <View style={styles.leftControls}>
                            <Pressable
                                style={[
                                    styles.taskModeButton,
                                    {
                                        backgroundColor: taskPlanningMode ? colors.primarySoft : "transparent",
                                        borderColor: taskPlanningMode ? `${colors.primary}26` : "transparent",
                                    },
                                ]}
                                onPress={onToggleTaskPlanningMode}
                            >
                                <MaterialCommunityIcons
                                    name="format-list-checks"
                                    size={15}
                                    color={taskPlanningMode ? colors.primaryDeep : colors.textMuted}
                                />
                            </Pressable>

                            <Pressable
                                style={[styles.inlineButton, attachmentBusy && styles.disabled]}
                                onPress={onPickAttachment}
                                disabled={attachmentBusy}
                            >
                                <MaterialCommunityIcons
                                    name={attachmentBusy ? "loading" : "paperclip"}
                                    size={18}
                                    color={colors.textMuted}
                                />
                            </Pressable>
                            <Pressable
                                style={[
                                    styles.inlineButton,
                                    recording && { backgroundColor: colors.danger },
                                    transcribing && styles.disabled,
                                ]}
                                onPress={onToggleRecording}
                                disabled={transcribing}
                                >
                                    <MaterialCommunityIcons
                                        name={transcribing ? "loading" : recording ? "stop" : "microphone-outline"}
                                        size={18}
                                        color={recording ? "#FFFFFF" : colors.textMuted}
                                    />
                                </Pressable>
                        </View>
                        <ComposerActionButton
                            mode={actionMode}
                            disabled={!canAct}
                            onPress={handlePrimaryAction}
                            colors={colors}
                        />
                    </View>
                </View>
            </View>
        </View>
    );
});

const styles = StyleSheet.create({
    shell: {
        gap: 0,
    },
    inputStage: {
        position: "relative",
        gap: 0,
    },
    composerCard: {
        borderRadius: 28,
        paddingHorizontal: 12,
        paddingTop: 12,
        paddingBottom: 8,
        borderWidth: 1,
        shadowOpacity: 0.08,
        shadowRadius: 16,
        shadowOffset: { width: 0, height: 8 },
        elevation: 2,
        gap: 8,
    },
    editorCard: {
        minHeight: 82,
        borderRadius: 22,
        borderWidth: 1,
        overflow: "visible",
        justifyContent: "flex-start",
        width: "100%",
        padding: 0,
    },
    input: {
        minHeight: 82,
        maxHeight: 220,
        width: "100%",
        flex: 1,
        flexGrow: 1,
        alignSelf: "stretch",
        fontSize: 16,
        lineHeight: 24,
        textAlignVertical: "top",
        backgroundColor: "transparent",
        borderWidth: 0,
        includeFontPadding: false,
        paddingTop: 12,
        paddingBottom: 12,
        paddingHorizontal: 14,
        paddingVertical: 12,
        margin: 0,
        borderRadius: 0,
        borderBottomWidth: 0,
        borderBottomColor: "transparent",
    },
    bottomControls: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 8,
    },
    leftControls: {
        flexDirection: "row",
        alignItems: "center",
        gap: 4,
        flex: 1,
        flexWrap: "nowrap",
    },
    taskModeButton: {
        width: 32,
        height: 32,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 12,
        borderWidth: 1,
    },
    inlineButton: {
        width: 32,
        height: 32,
        borderRadius: 12,
        alignItems: "center",
        justifyContent: "center",
    },
    sendWrap: {
        width: 48,
        height: 48,
        borderRadius: 24,
        alignItems: "center",
        justifyContent: "center",
        shadowColor: "#FB923C",
        shadowOpacity: 0.18,
        shadowRadius: 10,
        shadowOffset: { width: 0, height: 6 },
        elevation: 2,
        position: "relative",
    },
    sendPulse: {
        position: "absolute",
        width: 48,
        height: 48,
        borderRadius: 24,
        backgroundColor: "#FB923C",
    },
    sendOrbit: {
        position: "absolute",
        width: 46,
        height: 46,
        borderRadius: 23,
        padding: 2,
    },
    sendOrbitGradient: {
        width: "100%",
        height: "100%",
        borderRadius: 23,
    },
    sendButton: {
        width: 36,
        height: 36,
        borderRadius: 18,
        alignItems: "center",
        justifyContent: "center",
        zIndex: 2,
    },
    sendButtonStop: {
        borderWidth: 1,
        borderColor: "rgba(239,68,68,0.16)",
    },
    stopGlyph: {
        width: 12,
        height: 12,
        borderRadius: 4,
        backgroundColor: "#EF4444",
    },
    disabled: {
        opacity: 0.56,
    },
});
