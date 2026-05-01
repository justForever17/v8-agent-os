import { memo, useEffect, useRef, useState } from "react";
import {
    Platform,
    Image,
    Pressable,
    ScrollView,
    StyleSheet,
    Text,
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
import { normalizeRenderableWorkspaceUrl } from "@/src/lib/workspace-links";
import type { CommandPresetSummary, SkillReferenceSummary, SubagentFamilySummary, UploadedWorkspaceFile } from "@/src/types/admin";

function fileExtension(name?: string) {
    const ext = String(name || "").split(".").pop()?.trim();
    return ext && ext !== name ? ext.slice(0, 4).toUpperCase() : "FILE";
}

function isImageFile(file: UploadedWorkspaceFile) {
    const type = String(file.type || "").toLowerCase();
    const name = String(file.name || file.url || file.publicUrl || "").toLowerCase();
    return type.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp|heic|heif)$/i.test(name);
}

function isVideoFile(file: UploadedWorkspaceFile) {
    const type = String(file.type || "").toLowerCase();
    const name = String(file.name || file.url || file.publicUrl || "").toLowerCase();
    return type.startsWith("video/") || /\.(mp4|mov|m4v|webm|mkv|avi)$/i.test(name);
}

function resolvePreviewUri(adminBaseUrl: string, file: UploadedWorkspaceFile) {
    const candidate = String(
        file.previewUri
        || file.localUri
        || file.url
        || file.publicUrl
        || file.path
        || "",
    ).trim();
    if (!candidate) {
        return "";
    }
    return candidate.startsWith("file:")
        || candidate.startsWith("content:")
        || candidate.startsWith("data:")
        ? candidate
        : normalizeRenderableWorkspaceUrl(adminBaseUrl, candidate);
}

function ComposerActionButton({
    mode,
    disabled,
    onPress,
    colors,
    t,
}: {
    mode: "send" | "stop" | "busy";
    disabled: boolean;
    onPress: () => void;
    colors: ReturnType<typeof useUiPrefs>["colors"];
    t: ReturnType<typeof useUiPrefs>["t"];
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
            accessibilityLabel={mode === "stop"
                ? t("src.components.chat.composer.stop_run")
                : t("src.components.chat.composer.send_message")}
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
    bodyValue,
    onChangeBody,
    activeQueryMode,
    activeQueryText,
    onChangeQueryText,
    onBodyBackspace,
    onQueryBackspace,
    onSend,
    busy = false,
    isRunning = false,
    canStop = false,
    onStop,
    selectedCommand,
    selectedSkills,
    selectedSubagentFamilies,
    taskPlanningMode,
    onToggleTaskPlanningMode,
    uploadedFiles,
    onRemoveUploadedFile,
    adminBaseUrl,
    onPickAttachment,
    onToggleRecording,
    attachmentBusy = false,
    recording = false,
    transcribing = false,
}: {
    bodyValue: string;
    onChangeBody: (next: string) => void;
    activeQueryMode: "command" | "skill" | null;
    activeQueryText: string;
    onChangeQueryText: (next: string) => void;
    onBodyBackspace?: () => void;
    onQueryBackspace?: () => void;
    onSend: () => void;
    busy?: boolean;
    isRunning?: boolean;
    canStop?: boolean;
    onStop?: () => void;
    selectedCommand: CommandPresetSummary | null;
    selectedSkills: SkillReferenceSummary[];
    selectedSubagentFamilies: SubagentFamilySummary[];
    taskPlanningMode: boolean;
    onToggleTaskPlanningMode: () => void;
    uploadedFiles: UploadedWorkspaceFile[];
    onRemoveUploadedFile: (file: UploadedWorkspaceFile) => void;
    adminBaseUrl: string;
    onPickAttachment: () => void;
    onToggleRecording: () => void;
    attachmentBusy?: boolean;
    recording?: boolean;
    transcribing?: boolean;
}) {
    const { colors, t, themeMode } = useUiPrefs();
    const [isFocused, setIsFocused] = useState(false);
    const bodyInputRef = useRef<TextInput | null>(null);
    const queryInputRef = useRef<TextInput | null>(null);
    const hasPayload = Boolean(bodyValue.trim() || selectedCommand || selectedSkills.length > 0 || selectedSubagentFamilies.length > 0 || uploadedFiles.length > 0);
    const stopAvailable = Boolean(isRunning && canStop && onStop);
    const canSend = hasPayload && !busy && !isRunning;
    const canAct = stopAvailable || canSend;
    const hasFlowTokens = Boolean(selectedCommand || selectedSkills.length > 0 || selectedSubagentFamilies.length > 0 || activeQueryMode);
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
    const bodyPlaceholder = activeQueryMode
        ? ""
        : t("src.components.chat.composer.message_supervisor");

    useEffect(() => {
        const targetRef = activeQueryMode ? queryInputRef : bodyInputRef;
        const timer = setTimeout(() => {
            targetRef.current?.focus();
        }, 16);
        return () => clearTimeout(timer);
    }, [activeQueryMode]);

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
                        style={[
                            styles.editorCard,
                            {
                                backgroundColor: editorBackground,
                                borderColor: isFocused ? `${colors.primary}2F` : "transparent",
                            },
                        ]}
                    >
                        <View style={styles.editorFlow}>
                            {selectedCommand ? (
                                <View
                                    style={[
                                        styles.tokenChip,
                                        styles.commandTokenChip,
                                        {
                                            backgroundColor: themeMode === "dark" ? "rgba(167,139,250,0.18)" : "rgba(139,92,246,0.12)",
                                            borderColor: themeMode === "dark" ? "rgba(167,139,250,0.28)" : "rgba(139,92,246,0.22)",
                                        },
                                    ]}
                                >
                                    <MaterialCommunityIcons name="slash-forward" size={12} color={colors.primary} />
                                    <Text style={[styles.tokenText, { color: colors.text }]} numberOfLines={1}>
                                        {selectedCommand.name}
                                    </Text>
                                </View>
                            ) : null}
                            {selectedSkills.map((skill) => (
                                <View
                                    key={`${skill.name}:${skill.path || ""}`}
                                    style={[
                                        styles.tokenChip,
                                        {
                                            backgroundColor: themeMode === "dark" ? "rgba(251,191,36,0.16)" : "rgba(251,191,36,0.14)",
                                            borderColor: themeMode === "dark" ? "rgba(251,191,36,0.24)" : "rgba(251,191,36,0.22)",
                                        },
                                    ]}
                                >
                                    <MaterialCommunityIcons name="at" size={12} color={colors.warning} />
                                    <Text style={[styles.tokenText, { color: colors.text }]} numberOfLines={1}>
                                        {skill.name}
                                    </Text>
                                </View>
                            ))}
                            {selectedSubagentFamilies.map((family) => (
                                <View
                                    key={family.familyId}
                                    style={[
                                        styles.tokenChip,
                                        {
                                            backgroundColor: themeMode === "dark" ? "rgba(14,165,233,0.16)" : "rgba(14,165,233,0.12)",
                                            borderColor: themeMode === "dark" ? "rgba(14,165,233,0.24)" : "rgba(14,165,233,0.22)",
                                        },
                                    ]}
                                >
                                    <MaterialCommunityIcons name="account-group-outline" size={12} color={colors.accent} />
                                    <Text style={[styles.tokenText, { color: colors.text }]} numberOfLines={1}>
                                        {family.displayName || family.familyId}
                                    </Text>
                                </View>
                            ))}
                            {activeQueryMode ? (
                                <View
                                    style={[
                                        styles.queryChip,
                                        {
                                            backgroundColor: themeMode === "dark" ? "rgba(255,255,255,0.05)" : "rgba(255,255,255,0.78)",
                                            borderColor: `${colors.primary}26`,
                                        },
                                    ]}
                                >
                                    <Text style={[styles.queryPrefix, { color: colors.primary }]}>
                                        {activeQueryMode === "command" ? "/" : "@"}
                                    </Text>
                                    <TextInput
                                        ref={queryInputRef}
                                        value={activeQueryText}
                                        onChangeText={onChangeQueryText}
                                        onFocus={() => setIsFocused(true)}
                                        onBlur={() => setIsFocused(false)}
                                        onKeyPress={(event) => {
                                            if (event.nativeEvent.key === "Backspace" && !activeQueryText) {
                                                onQueryBackspace?.();
                                            }
                                        }}
                                        placeholder={activeQueryMode === "command"
                                            ? t("src.components.chat.composer.search_command")
                                            : t("src.components.chat.composer.search_skill")}
                                        placeholderTextColor={colors.textSoft}
                                        autoCorrect={false}
                                        spellCheck={false}
                                        autoComplete="off"
                                        importantForAutofill="no"
                                        selectionColor={colors.primary}
                                        cursorColor={colors.accent}
                                        returnKeyType="done"
                                        style={[styles.queryInput, { color: colors.text }, inputWebStyle]}
                                    />
                                </View>
                            ) : (
                                <TextInput
                                    ref={bodyInputRef}
                                    value={bodyValue}
                                    onChangeText={onChangeBody}
                                    onFocus={() => setIsFocused(true)}
                                    onBlur={() => setIsFocused(false)}
                                    onKeyPress={(event) => {
                                        if (event.nativeEvent.key === "Backspace" && !bodyValue) {
                                            onBodyBackspace?.();
                                        }
                                    }}
                                    placeholder={bodyPlaceholder}
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
                                    style={[
                                        styles.input,
                                        hasFlowTokens ? styles.inputInline : styles.inputStandalone,
                                        { color: colors.text },
                                        inputWebStyle,
                                    ]}
                                />
                            )}
                        </View>
                    </View>

                    <View style={styles.bottomControls}>
                        <View style={styles.leftControls}>
                            <Pressable
                                accessibilityRole="button"
                                accessibilityLabel={taskPlanningMode
                                    ? t("src.components.chat.composer.disable_task_planning")
                                    : t("src.components.chat.composer.enable_task_planning")}
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
                            {uploadedFiles.length > 0 ? (
                                <ScrollView
                                    horizontal
                                    showsHorizontalScrollIndicator={false}
                                    keyboardShouldPersistTaps="always"
                                    overScrollMode="never"
                                    contentContainerStyle={styles.filePreviewRow}
                                    style={styles.filePreviewScroll}
                                >
                                    {uploadedFiles.map((file) => {
                                        const previewUrl = resolvePreviewUri(adminBaseUrl, file);
                                        const image = isImageFile(file) && previewUrl;
                                        const video = isVideoFile(file) && previewUrl;
                                        const previewKey = file.localId || file.id || previewUrl || file.name;
                                        return (
                                            <Pressable
                                                key={previewKey}
                                                accessibilityRole="button"
                                                accessibilityLabel={t("src.components.chat.composer.remove_attachment")}
                                                hitSlop={6}
                                                style={({ pressed }) => [
                                                    styles.filePreviewButton,
                                                    {
                                                        borderColor: colors.border,
                                                        backgroundColor: themeMode === "dark" ? "rgba(255,255,255,0.06)" : "rgba(255,255,255,0.84)",
                                                        opacity: pressed ? 0.72 : 1,
                                                    },
                                                ]}
                                                onPress={() => onRemoveUploadedFile(file)}
                                            >
                                                {image ? (
                                                    <Image source={{ uri: previewUrl }} style={styles.filePreviewImage} />
                                                ) : video ? (
                                                    <View style={styles.filePreviewVideo}>
                                                        <Image source={{ uri: previewUrl }} style={styles.filePreviewImage} />
                                                        {file.durationLabel ? (
                                                            <View style={styles.filePreviewDurationBadge}>
                                                                <Text style={styles.filePreviewDurationText}>
                                                                    {file.durationLabel}
                                                                </Text>
                                                            </View>
                                                        ) : null}
                                                    </View>
                                                ) : (
                                                    <View style={[styles.filePreviewFallback, { backgroundColor: colors.surfaceStrong }]}>
                                                        <Text style={[styles.filePreviewExt, { color: colors.textMuted }]} numberOfLines={1}>
                                                            {fileExtension(file.name)}
                                                        </Text>
                                                    </View>
                                                )}
                                            </Pressable>
                                        );
                                    })}
                                </ScrollView>
                            ) : null}
                        </View>
                        <ComposerActionButton
                            mode={actionMode}
                            disabled={!canAct}
                            onPress={handlePrimaryAction}
                            colors={colors}
                            t={t}
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
        overflow: "hidden",
        justifyContent: "flex-start",
        width: "100%",
        paddingHorizontal: 10,
        paddingTop: 10,
        paddingBottom: 6,
    },
    editorFlow: {
        minHeight: 60,
        width: "100%",
        flexDirection: "row",
        flexWrap: "wrap",
        alignItems: "flex-start",
        alignContent: "flex-start",
        columnGap: 6,
        rowGap: 8,
    },
    input: {
        minHeight: 28,
        maxHeight: 220,
        fontSize: 16,
        lineHeight: 24,
        textAlignVertical: "top",
        backgroundColor: "transparent",
        borderWidth: 0,
        includeFontPadding: false,
        paddingTop: 2,
        paddingBottom: 0,
        paddingHorizontal: 0,
        paddingVertical: 0,
        margin: 0,
        borderRadius: 0,
        borderBottomWidth: 0,
        borderBottomColor: "transparent",
    },
    inputStandalone: {
        width: "100%",
        minHeight: 66,
        alignSelf: "stretch",
        paddingHorizontal: 4,
        paddingTop: 6,
        paddingBottom: 8,
    },
    inputInline: {
        flexGrow: 1,
        flexShrink: 1,
        flexBasis: 108,
        minWidth: 92,
        maxWidth: "100%",
        paddingLeft: 0,
        paddingRight: 0,
        paddingTop: 1,
        paddingBottom: 2,
    },
    tokenChip: {
        minHeight: 28,
        maxWidth: "100%",
        paddingHorizontal: 10,
        borderRadius: 14,
        borderWidth: 1,
        flexDirection: "row",
        alignItems: "center",
        gap: 4,
        alignSelf: "flex-start",
    },
    commandTokenChip: {
        maxWidth: 176,
    },
    tokenText: {
        fontSize: 13,
        fontWeight: "700",
        lineHeight: 18,
        flexShrink: 1,
    },
    queryChip: {
        minHeight: 28,
        minWidth: 68,
        maxWidth: 180,
        paddingHorizontal: 10,
        borderRadius: 14,
        borderWidth: 1,
        flexDirection: "row",
        alignItems: "center",
        gap: 4,
        alignSelf: "flex-start",
    },
    queryPrefix: {
        fontSize: 13,
        fontWeight: "800",
    },
    queryInput: {
        minWidth: 36,
        maxWidth: 120,
        paddingVertical: 0,
        paddingHorizontal: 0,
        fontSize: 13,
        lineHeight: 18,
        includeFontPadding: false,
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
        minWidth: 0,
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
    filePreviewScroll: {
        flexShrink: 1,
        minWidth: 0,
        maxWidth: 132,
    },
    filePreviewRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 4,
        paddingHorizontal: 2,
    },
    filePreviewButton: {
        width: 32,
        height: 32,
        borderRadius: 12,
        borderWidth: 1,
        overflow: "hidden",
        alignItems: "center",
        justifyContent: "center",
    },
    filePreviewImage: {
        width: "100%",
        height: "100%",
    },
    filePreviewVideo: {
        width: "100%",
        height: "100%",
    },
    filePreviewFallback: {
        width: "100%",
        height: "100%",
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 2,
    },
    filePreviewExt: {
        fontSize: 8,
        fontWeight: "900",
        letterSpacing: 0.2,
    },
    filePreviewDurationBadge: {
        position: "absolute",
        right: 2,
        bottom: 2,
        paddingHorizontal: 4,
        minHeight: 12,
        borderRadius: 6,
        backgroundColor: "rgba(15,23,42,0.78)",
        alignItems: "center",
        justifyContent: "center",
    },
    filePreviewDurationText: {
        color: "#FFFFFF",
        fontSize: 7,
        fontWeight: "800",
        letterSpacing: 0.2,
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
