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

type ReasoningEffortLevel = "auto" | "low" | "medium" | "high";

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
    specMode,
    onPress,
    colors,
    t,
}: {
    mode: "send" | "queue" | "stop" | "busy";
    disabled: boolean;
    specMode: boolean;
    onPress: () => void;
    colors: ReturnType<typeof useUiPrefs>["colors"];
    t: ReturnType<typeof useUiPrefs>["t"];
}) {
    const { themeMode } = useUiPrefs();
    const spin = useSharedValue(0);

    const getSpinDuration = () => {
        if (mode === "stop") return 800;
        if (specMode) return 1200;
        if (mode === "send") return 1800;
        if (mode === "queue") return 1400;
        return 2000;
    };

    useEffect(() => {
        if (disabled) {
            cancelAnimation(spin);
            spin.value = withTiming(0, { duration: 180 });
            return;
        }

        const duration = getSpinDuration();
        spin.value = withRepeat(
            withTiming(1, { duration, easing: Easing.linear }),
            -1,
            false,
        );

        return () => {
            cancelAnimation(spin);
        };
    }, [disabled, mode, specMode, spin]);

    const orbitStyle = useAnimatedStyle(() => ({
        opacity: !disabled ? 1 : 0,
        transform: [{ rotate: `${spin.value * 360}deg` }],
    }));

    const iconName: React.ComponentProps<typeof MaterialCommunityIcons>["name"] = mode === "stop"
        ? "stop"
        : mode === "busy"
            ? "progress-clock"
            : mode === "queue"
                ? "playlist-plus"
            : "send";

    const buttonColors = disabled
        ? [colors.surfaceStrong, colors.surfaceStrong]
        : mode === "stop"
            ? [colors.surface, colors.surface]
            : mode === "busy"
                ? [colors.surfaceStrong, colors.surfaceStrong]
                : mode === "queue"
                    ? ["#A78BFA", "#C084FC"]
                : specMode
                    ? ["#EF4444", "#F97316"]
                : ["#6366F1", "#8B5CF6"];

    const orbitColors = mode === "stop"
        ? ["#FF3B30", "rgba(255, 59, 48, 0.15)", "rgba(255, 59, 48, 0.02)", "#FF3B30"]
        : specMode
            ? ["#F97316", "rgba(249, 115, 22, 0.15)", "rgba(249, 115, 22, 0.02)", "#F97316"]
        : mode === "queue"
            ? ["#C084FC", "rgba(192, 132, 252, 0.15)", "rgba(192, 132, 252, 0.02)", "#C084FC"]
        : mode === "busy"
            ? ["#64748B", "rgba(100, 116, 139, 0.15)", "rgba(100, 116, 139, 0.02)", "#64748B"]
        : ["#8B5CF6", "rgba(139, 92, 246, 0.15)", "rgba(139, 92, 246, 0.02)", "#8B5CF6"];

    const getIconColor = () => {
        if (mode === "busy") return colors.textMuted;
        if (disabled) return colors.textMuted;
        return "#FFFFFF";
    };

    return (
        <Pressable
            accessibilityRole="button"
            accessibilityLabel={mode === "stop"
                ? t("src.components.chat.composer.stop_run")
                : mode === "queue"
                    ? t("src.components.chat.composer.queue_message")
                : t("src.components.chat.composer.send_message")}
            disabled={disabled}
            onPress={onPress}
            style={[
                styles.sendWrap,
                {
                    shadowColor: mode === "stop" ? "#FF3B30" : themeMode === "dark" ? "#000000" : "#0F172A",
                    shadowOpacity: disabled ? 0 : mode === "stop" ? 0.22 : 0.14,
                },
                disabled && styles.disabled,
            ]}
        >
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
                    <MaterialCommunityIcons
                        name={iconName}
                        size={16}
                        color={getIconColor()}
                        style={iconName === "send" ? styles.paperPlaneIcon : null}
                    />
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
    allowQueueWhileRunning = false,
    selectedCommand,
    selectedSkills,
    selectedSubagentFamilies,
    taskPlanningMode,
    onToggleTaskPlanningMode,
    reasoningEffortVisible = false,
    reasoningEffortLevels = ["auto"],
    reasoningEffort = "auto",
    onChangeReasoningEffort,
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
    allowQueueWhileRunning?: boolean;
    selectedCommand: CommandPresetSummary | null;
    selectedSkills: SkillReferenceSummary[];
    selectedSubagentFamilies: SubagentFamilySummary[];
    taskPlanningMode: boolean;
    onToggleTaskPlanningMode: () => void;
    reasoningEffortVisible?: boolean;
    reasoningEffortLevels?: ReasoningEffortLevel[];
    reasoningEffort?: ReasoningEffortLevel;
    onChangeReasoningEffort?: (level: ReasoningEffortLevel) => void;
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
    const canQueue = Boolean(hasPayload && !busy && isRunning && allowQueueWhileRunning);
    const canSend = hasPayload && !busy && (!isRunning || allowQueueWhileRunning);
    const canAct = canSend || (stopAvailable && !hasPayload);
    const hasFlowTokens = Boolean(selectedCommand || selectedSkills.length > 0 || selectedSubagentFamilies.length > 0 || activeQueryMode);
    const reasoningEffortLabelMap: Record<ReasoningEffortLevel, string> = {
        auto: t("src.components.chat.composer.reasoning_effort_auto_short"),
        low: t("src.components.chat.composer.reasoning_effort_low_short"),
        medium: t("src.components.chat.composer.reasoning_effort_medium_short"),
        high: t("src.components.chat.composer.reasoning_effort_high_short"),
    };
    const cycleReasoningEffort = () => {
        if (!reasoningEffortVisible || !onChangeReasoningEffort) return;
        const levels: ReasoningEffortLevel[] = reasoningEffortLevels.length > 0 ? reasoningEffortLevels : ["auto"];
        const index = Math.max(0, levels.indexOf(reasoningEffort));
        onChangeReasoningEffort(levels[(index + 1) % levels.length] || "auto");
    };
    const actionMode: "send" | "queue" | "stop" | "busy" = canQueue
        ? "queue"
        : stopAvailable
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

    const inputWebStyle: any = Platform.OS === "web"
        ? {
            outlineStyle: "none",
            boxShadow: "none",
            appearance: "none",
            WebkitAppearance: "none",
        }
        : null;
    const bodyPlaceholder = "";

    useEffect(() => {
        const targetRef = activeQueryMode ? queryInputRef : bodyInputRef;
        const timer = setTimeout(() => {
            targetRef.current?.focus();
        }, 16);
        return () => clearTimeout(timer);
    }, [activeQueryMode]);

    const handlePrimaryAction = () => {
        if (canSend) {
            onSend();
            return;
        }
        if (stopAvailable && onStop) {
            onStop();
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
                                backgroundColor: "transparent",
                                borderColor: "transparent",
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
                                    <MaterialCommunityIcons name="slash-forward" size={11} color={colors.primary} />
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
                                    <MaterialCommunityIcons name="at" size={11} color={colors.warning} />
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
                                    <MaterialCommunityIcons name="account-group-outline" size={11} color={colors.accent} />
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
                                    ? t("src.components.chat.composer.disable_spec_mode")
                                    : t("src.components.chat.composer.enable_spec_mode")}
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
                                    name="file-document-edit-outline"
                                    size={15}
                                    color={taskPlanningMode ? colors.primaryDeep : colors.textMuted}
                                />
                            </Pressable>

                            {reasoningEffortVisible ? (
                                <Pressable
                                    accessibilityRole="button"
                                    accessibilityLabel={t("src.components.chat.composer.reasoning_effort")}
                                    style={[
                                        styles.reasoningEffortButton,
                                        {
                                            backgroundColor: reasoningEffort === "auto"
                                                ? "transparent"
                                                : themeMode === "dark" ? "rgba(245,158,11,0.18)" : "rgba(251,191,36,0.16)",
                                            borderColor: reasoningEffort === "auto"
                                                ? "transparent"
                                                : themeMode === "dark" ? "rgba(245,158,11,0.28)" : "rgba(217,119,6,0.22)",
                                        },
                                    ]}
                                    onPress={cycleReasoningEffort}
                                >
                                    <MaterialCommunityIcons
                                        name="brain"
                                        size={14}
                                        color={reasoningEffort === "auto" ? colors.textMuted : colors.warning}
                                    />
                                    <Text
                                        style={[
                                            styles.reasoningEffortText,
                                            { color: reasoningEffort === "auto" ? colors.textMuted : colors.warning },
                                        ]}
                                        numberOfLines={1}
                                    >
                                        {t("src.components.chat.composer.reasoning_effort_prefix")}·{reasoningEffortLabelMap[reasoningEffort] || reasoningEffortLabelMap.auto}
                                    </Text>
                                </Pressable>
                            ) : null}

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
                            specMode={taskPlanningMode}
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
        borderRadius: 24,
        paddingHorizontal: 6,
        paddingTop: 4,
        paddingBottom: 4,
        borderWidth: 1,
        shadowOpacity: 0.08,
        shadowRadius: 16,
        shadowOffset: { width: 0, height: 8 },
        elevation: 2,
        gap: 6,
    },
    editorCard: {
        minHeight: 40,
        borderRadius: 18,
        borderWidth: 1,
        overflow: "hidden",
        justifyContent: "flex-start",
        width: "100%",
        paddingHorizontal: 4,
        paddingTop: 4,
        paddingBottom: 4,
    },
    editorFlow: {
        minHeight: 32,
        width: "100%",
        flexDirection: "row",
        flexWrap: "wrap",
        alignItems: "center",
        alignContent: "flex-start",
        columnGap: 6,
        rowGap: 6,
    },
    input: {
        minHeight: 24,
        maxHeight: 220,
        fontSize: 16,
        lineHeight: 24,
        textAlignVertical: "top",
        backgroundColor: "transparent",
        borderWidth: 0,
        includeFontPadding: false,
        paddingTop: 2,
        paddingBottom: 2,
        paddingHorizontal: 0,
        paddingVertical: 0,
        margin: 0,
        borderRadius: 0,
        borderBottomWidth: 0,
        borderBottomColor: "transparent",
    },
    inputStandalone: {
        width: "100%",
        minHeight: 32,
        alignSelf: "stretch",
        paddingHorizontal: 2,
        paddingTop: 2,
        paddingBottom: 4,
    },
    inputInline: {
        flexGrow: 1,
        flexShrink: 1,
        flexBasis: 108,
        minWidth: 92,
        maxWidth: "100%",
        paddingLeft: 0,
        paddingRight: 0,
        paddingTop: 0,
        paddingBottom: 0,
    },
    tokenChip: {
        minHeight: 24,
        maxWidth: "100%",
        paddingHorizontal: 8,
        borderRadius: 12,
        borderWidth: 1,
        flexDirection: "row",
        alignItems: "center",
        gap: 4,
    },
    commandTokenChip: {
        maxWidth: 176,
    },
    tokenText: {
        fontSize: 12,
        fontWeight: "700",
        lineHeight: 16,
        flexShrink: 1,
    },
    queryChip: {
        minHeight: 24,
        minWidth: 68,
        maxWidth: 180,
        paddingHorizontal: 8,
        borderRadius: 12,
        borderWidth: 1,
        flexDirection: "row",
        alignItems: "center",
        gap: 4,
    },
    queryPrefix: {
        fontSize: 12,
        fontWeight: "800",
    },
    queryInput: {
        minWidth: 36,
        maxWidth: 120,
        paddingVertical: 0,
        paddingHorizontal: 0,
        fontSize: 12,
        lineHeight: 16,
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
    reasoningEffortButton: {
        height: 32,
        maxWidth: 74,
        paddingHorizontal: 8,
        borderRadius: 14,
        borderWidth: 1,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        gap: 4,
    },
    reasoningEffortText: {
        fontSize: 11,
        lineHeight: 14,
        fontWeight: "800",
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
        width: 40,
        height: 40,
        borderRadius: 20,
        alignItems: "center",
        justifyContent: "center",
        shadowRadius: 8,
        shadowOffset: { width: 0, height: 4 },
        elevation: 2,
        position: "relative",
    },
    sendOrbit: {
        position: "absolute",
        width: 40,
        height: 40,
        borderRadius: 20,
        padding: 0,
    },
    sendOrbitGradient: {
        width: "100%",
        height: "100%",
        borderRadius: 20,
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
        borderColor: "rgba(255,59,48,0.16)",
    },
    stopGlyph: {
        width: 11,
        height: 11,
        borderRadius: 2,
        backgroundColor: "#FF3B30",
    },
    paperPlaneIcon: {
        transform: [{ rotate: "-35deg" }],
        marginLeft: 2,
        marginTop: -1,
    },
    disabled: {
        opacity: 0.46,
    },
});
