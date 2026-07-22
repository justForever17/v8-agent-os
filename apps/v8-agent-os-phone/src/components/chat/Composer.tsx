import { memo, useEffect, useMemo, useRef, useState } from "react";
import {
    Modal,
    Platform,
    Image,
    Pressable,
    ScrollView,
    StyleSheet,
    Text,
    TextInput,
    useWindowDimensions,
    View,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { Gauge } from "lucide-react-native";
import { LinearGradient } from "expo-linear-gradient";
import Animated, {
    Easing,
    cancelAnimation,
    useAnimatedStyle,
    useSharedValue,
    withRepeat,
    withTiming,
} from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
    buildComposerInlineSegments,
    type ComposerInlineReference,
} from "@v8/session-realtime/composer-inline-references";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";
import { normalizeRenderableWorkspaceUrl } from "@/src/lib/workspace-links";
import type { CommandPresetSummary, PluginReferenceSummary, SkillReferenceSummary, SubagentFamilySummary, UploadedWorkspaceFile } from "@/src/types/admin";
import { PhoneReasoningEffortControl } from "./ReasoningEffortControl";

type ReasoningEffortLevel = "auto" | "none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";
type SafetyApprovalMode = "manual" | "reduced" | "minimal";
type ContextSessionReference = { sessionId: string; source: "history_menu" };

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

function SpecModeOrbitIcon({
    active,
    color,
}: {
    active: boolean;
    color: string;
}) {
    const spin = useSharedValue(0);

    useEffect(() => {
        if (!active) {
            cancelAnimation(spin);
            spin.value = withTiming(0, { duration: 180 });
            return;
        }

        spin.value = withRepeat(
            withTiming(1, { duration: 1600, easing: Easing.linear }),
            -1,
            false,
        );

        return () => {
            cancelAnimation(spin);
        };
    }, [active, spin]);

    const orbitStyle = useAnimatedStyle(() => ({
        transform: [{ rotate: `${spin.value * 360}deg` }],
    }));

    return (
        <Animated.View style={orbitStyle}>
            <MaterialCommunityIcons name="orbit" size={17} color={color} />
        </Animated.View>
    );
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
    onBodyBackspace,
    composerReferences,
    selection,
    onSelectionChange,
    onSend,
    busy = false,
    isRunning = false,
    canStop = false,
    onStop,
    allowQueueWhileRunning = false,
    selectedCommand,
    selectedSkills,
    selectedSubagentFamilies,
    selectedPlugins,
    onChangePluginScope,
    onRemovePlugin,
    contextSessionRefs,
    onRemoveContextSessionRef,
    specModeEnabled,
    onToggleSpecMode,
    supervisorWorkMode = "daily",
    onChangeSupervisorWorkMode,
    safetyApprovalMode,
    onChangeSafetyApprovalMode,
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
    onBodyBackspace?: () => void;
    composerReferences: ComposerInlineReference[];
    selection: { start: number; end: number };
    onSelectionChange: (selection: { start: number; end: number }) => void;
    onSend: () => void;
    busy?: boolean;
    isRunning?: boolean;
    canStop?: boolean;
    onStop?: () => void;
    allowQueueWhileRunning?: boolean;
    selectedCommand: CommandPresetSummary | null;
    selectedSkills: SkillReferenceSummary[];
    selectedSubagentFamilies: SubagentFamilySummary[];
    selectedPlugins: PluginReferenceSummary[];
    onChangePluginScope: (pluginId: string, scope: "task" | "session") => void;
    onRemovePlugin: (pluginId: string) => void;
    contextSessionRefs: ContextSessionReference[];
    onRemoveContextSessionRef: (sessionId: string) => void;
    specModeEnabled: boolean;
    onToggleSpecMode: () => void;
    supervisorWorkMode?: "daily" | "engineering";
    onChangeSupervisorWorkMode?: (mode: "daily" | "engineering") => void;
    safetyApprovalMode: SafetyApprovalMode;
    onChangeSafetyApprovalMode: (mode: SafetyApprovalMode) => void;
    reasoningEffortVisible?: boolean;
    reasoningEffortLevels?: ReasoningEffortLevel[];
    reasoningEffort?: ReasoningEffortLevel;
    onChangeReasoningEffort?: (level: ReasoningEffortLevel) => void | Promise<void>;
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
    const safeAreaInsets = useSafeAreaInsets();
    const { width: windowWidth } = useWindowDimensions();
    const [isFocused, setIsFocused] = useState(false);
    const [safetyApprovalOpen, setSafetyApprovalOpen] = useState(false);
    const [reasoningEffortOpen, setReasoningEffortOpen] = useState(false);
    const [pluginSheetId, setPluginSheetId] = useState("");
    const [editorScrollY, setEditorScrollY] = useState(0);
    const bodyInputRef = useRef<TextInput | null>(null);
    const hasPayload = Boolean(bodyValue.trim() || selectedCommand || selectedSkills.length > 0 || selectedSubagentFamilies.length > 0 || selectedPlugins.length > 0 || uploadedFiles.length > 0);
    const stopAvailable = Boolean(isRunning && canStop && onStop);
    const canQueue = Boolean(hasPayload && !busy && isRunning && allowQueueWhileRunning);
    const canSend = hasPayload && !busy && (!isRunning || allowQueueWhileRunning);
    const canAct = canSend || (stopAvailable && !hasPayload);
    const pluginInSheet = selectedPlugins.find((plugin) => plugin.pluginId === pluginSheetId) || null;
    const composerSegments = useMemo(
        () => buildComposerInlineSegments(bodyValue, composerReferences),
        [bodyValue, composerReferences],
    );
    const commandColor = themeMode === "dark" ? "#C4B5FD" : "#7C3AED";
    const mentionColor = themeMode === "dark" ? "#FDBA74" : "#F97316";
    const reasoningEffortLabelMap: Record<ReasoningEffortLevel, string> = {
        auto: t("src.components.chat.composer.reasoning_effort_auto"),
        none: "None",
        minimal: "Minimal",
        low: t("src.components.chat.composer.reasoning_effort_low"),
        medium: t("src.components.chat.composer.reasoning_effort_medium"),
        high: t("src.components.chat.composer.reasoning_effort_high"),
        xhigh: "X-High",
        max: "Max",
    };
    const safetyApprovalOptions: Array<{
        mode: SafetyApprovalMode;
        title: string;
        description: string;
        icon: React.ComponentProps<typeof MaterialCommunityIcons>["name"];
        color: string;
    }> = [
        {
            mode: "manual",
            title: t("src.components.chat.composer.safety_approval_manual_title"),
            description: t("src.components.chat.composer.safety_approval_manual_description"),
            icon: "shield-alert-outline",
            color: colors.danger,
        },
        {
            mode: "reduced",
            title: t("src.components.chat.composer.safety_approval_reduced_title"),
            description: t("src.components.chat.composer.safety_approval_reduced_description"),
            icon: "shield-outline",
            color: colors.warning,
        },
        {
            mode: "minimal",
            title: t("src.components.chat.composer.safety_approval_minimal_title"),
            description: t("src.components.chat.composer.safety_approval_minimal_description"),
            icon: "shield-check-outline",
            color: colors.success,
        },
    ];
    const activeSafetyApproval = safetyApprovalOptions.find((option) => option.mode === safetyApprovalMode) || safetyApprovalOptions[1]!;
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
        <>
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
                            {contextSessionRefs.map((reference) => (
                                <View
                                    key={reference.sessionId}
                                    style={styles.tokenChip}
                                >
                                    <MaterialCommunityIcons name="message-arrow-right-outline" size={14} color={colors.accent} />
                                    <Text style={[styles.tokenText, { color: colors.accent }]} numberOfLines={1}>
                                        {t("shared.conversation.context_session_ref")} · {reference.sessionId.slice(0, 10)}
                                    </Text>
                                    <Pressable
                                        accessibilityRole="button"
                                        accessibilityLabel={t("shared.conversation.remove_context_session_ref")}
                                        onPress={() => onRemoveContextSessionRef(reference.sessionId)}
                                        hitSlop={8}
                                    >
                                        <MaterialCommunityIcons name="close" size={12} color={colors.textMuted} />
                                    </Pressable>
                                </View>
                            ))}
                            <View style={styles.composerInputLayer}>
                                <View pointerEvents="none" style={styles.inputMirrorViewport}>
                                    <Text
                                        style={[
                                            styles.inputMirrorText,
                                            {
                                                color: colors.text,
                                                transform: [{ translateY: -editorScrollY }],
                                            },
                                        ]}
                                    >
                                        {composerSegments.map((segment, index) => (
                                            <Text
                                                key={`${segment.start}:${segment.end}:${index}`}
                                                style={segment.type === "reference"
                                                    ? {
                                                        color: segment.reference?.kind === "command" ? commandColor : mentionColor,
                                                        fontWeight: "700",
                                                    }
                                                    : undefined}
                                            >
                                                {segment.text}
                                            </Text>
                                        ))}
                                    </Text>
                                </View>
                                <TextInput
                                    ref={bodyInputRef}
                                    value={bodyValue}
                                    onChangeText={onChangeBody}
                                    selection={selection}
                                    onSelectionChange={(event) => onSelectionChange(event.nativeEvent.selection)}
                                    onScroll={(event) => setEditorScrollY(event.nativeEvent.contentOffset?.y || 0)}
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
                                        styles.inputStandalone,
                                        { color: "transparent" },
                                        inputWebStyle,
                                    ]}
                                />
                            </View>
                        </View>
                    </View>

                    <View style={styles.bottomControls}>
                        <View style={styles.leftControls}>
                            <Pressable
                                accessibilityRole="button"
                                accessibilityLabel={supervisorWorkMode === "engineering"
                                    ? t("src.components.chat.composer.switch_daily_mode")
                                    : t("src.components.chat.composer.switch_engineering_mode")}
                                style={[
                                    styles.taskModeButton,
                                    styles.workModeButton,
                                    {
                                        backgroundColor: supervisorWorkMode === "engineering" ? colors.primarySoft : "transparent",
                                        borderColor: supervisorWorkMode === "engineering" ? `${colors.primary}26` : "transparent",
                                    },
                                ]}
                                onPress={() => onChangeSupervisorWorkMode?.(supervisorWorkMode === "engineering" ? "daily" : "engineering")}
                            >
                                <MaterialCommunityIcons
                                    name={supervisorWorkMode === "engineering" ? "code-tags" : "message-processing-outline"}
                                    size={19}
                                    color={supervisorWorkMode === "engineering" ? colors.primaryDeep : colors.textMuted}
                                />
                            </Pressable>
                            <Pressable
                                accessibilityRole="button"
                                accessibilityLabel={specModeEnabled
                                    ? t("src.components.chat.composer.disable_spec_mode")
                                    : t("src.components.chat.composer.enable_spec_mode")}
                                style={[
                                    styles.taskModeButton,
                                    {
                                        backgroundColor: specModeEnabled ? colors.primarySoft : "transparent",
                                        borderColor: specModeEnabled ? `${colors.primary}26` : "transparent",
                                    },
                                ]}
                                onPress={onToggleSpecMode}
                            >
                                <SpecModeOrbitIcon
                                    active={specModeEnabled}
                                    color={specModeEnabled ? colors.primaryDeep : colors.textMuted}
                                />
                            </Pressable>

                            <View style={styles.safetyControl}>
                                <Pressable
                                    accessibilityRole="button"
                                    accessibilityLabel={activeSafetyApproval.title}
                                    style={[
                                        styles.safetyModeButton,
                                        {
                                            backgroundColor: safetyApprovalOpen
                                                ? `${activeSafetyApproval.color}1A`
                                                : "transparent",
                                            borderColor: safetyApprovalOpen
                                                ? `${activeSafetyApproval.color}33`
                                                : "transparent",
                                        },
                                    ]}
                                    onPress={() => setSafetyApprovalOpen((current) => !current)}
                                >
                                    <MaterialCommunityIcons
                                        name={activeSafetyApproval.icon}
                                        size={18}
                                        color={activeSafetyApproval.color}
                                    />
                                </Pressable>
                            </View>

                            {reasoningEffortVisible ? (
                                <Pressable
                                    accessibilityRole="button"
                                    accessibilityLabel={t("src.components.chat.composer.reasoning_effort")}
                                    accessibilityState={{ expanded: reasoningEffortOpen }}
                                    style={[
                                        styles.reasoningEffortButton,
                                        {
                                            backgroundColor: reasoningEffort === "auto"
                                                ? "transparent"
                                                : themeMode === "dark" ? "rgba(139,92,246,0.18)" : "rgba(124,58,237,0.12)",
                                            borderColor: reasoningEffort === "auto"
                                                ? "transparent"
                                                : themeMode === "dark" ? "rgba(167,139,250,0.42)" : "rgba(124,58,237,0.3)",
                                        },
                                    ]}
                                    onPress={() => setReasoningEffortOpen(true)}
                                >
                                    <Gauge
                                        size={18}
                                        strokeWidth={1.75}
                                        color={reasoningEffort === "auto" ? colors.textMuted : colors.primary}
                                    />
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
                            specMode={specModeEnabled}
                            onPress={handlePrimaryAction}
                            colors={colors}
                            t={t}
                        />
                    </View>
                </View>
            </View>
        </View>
        <Modal
            visible={reasoningEffortOpen && reasoningEffortVisible}
            transparent
            animationType="fade"
            statusBarTranslucent
            onRequestClose={() => setReasoningEffortOpen(false)}
        >
            <View style={styles.reasoningEffortOverlay}>
                <Pressable
                    style={StyleSheet.absoluteFill}
                    onPress={() => setReasoningEffortOpen(false)}
                    accessibilityRole="button"
                    accessibilityLabel={t("src.components.chat.mediaviewerlightbox.cancel")}
                />
                <View
                    style={[
                        styles.reasoningEffortPopover,
                        {
                            width: Math.min(352, Math.max(288, windowWidth - 24)),
                            marginBottom: Math.max(safeAreaInsets.bottom + 78, 92),
                        },
                    ]}
                >
                    <PhoneReasoningEffortControl
                        levels={reasoningEffortLevels}
                        value={reasoningEffort}
                        onValueCommit={async (level) => {
                            await onChangeReasoningEffort?.(level);
                        }}
                        colors={colors}
                        themeMode={themeMode}
                        label={t("src.components.chat.composer.reasoning_effort_label")}
                        fasterLabel={t("src.components.chat.composer.reasoning_effort_faster")}
                        smarterLabel={t("src.components.chat.composer.reasoning_effort_smarter")}
                        ariaLabel={t("src.components.chat.composer.reasoning_effort")}
                        labelFormatter={(level) => reasoningEffortLabelMap[level] || level}
                    />
                </View>
            </View>
        </Modal>
        <Modal
            visible={safetyApprovalOpen}
            transparent
            animationType="fade"
            statusBarTranslucent
            onRequestClose={() => setSafetyApprovalOpen(false)}
        >
            <View style={styles.safetyOverlay}>
                <Pressable
                    style={StyleSheet.absoluteFill}
                    onPress={() => setSafetyApprovalOpen(false)}
                    accessibilityRole="button"
                    accessibilityLabel={t("src.components.chat.mediaviewerlightbox.cancel")}
                />
                <View
                    style={[
                        styles.safetySheet,
                        {
                            paddingBottom: Math.max(safeAreaInsets.bottom, 16),
                            backgroundColor: themeMode === "dark" ? "rgba(24,24,27,0.99)" : "rgba(255,255,255,0.99)",
                            borderColor: colors.border,
                            shadowColor: themeMode === "dark" ? "#000000" : "#0F172A",
                        },
                    ]}
                >
                    <View style={[styles.safetySheetHandle, { backgroundColor: colors.border }]} />
                    <ScrollView
                        style={styles.safetySheetScroll}
                        contentContainerStyle={styles.safetySheetContent}
                        showsVerticalScrollIndicator={false}
                    >
                        {safetyApprovalOptions.map((option) => {
                            const active = option.mode === safetyApprovalMode;
                            return (
                                <Pressable
                                    key={option.mode}
                                    accessibilityRole="button"
                                    accessibilityLabel={option.title}
                                    accessibilityState={{ selected: active }}
                                    style={({ pressed }) => [
                                        styles.safetyMenuItem,
                                        {
                                            backgroundColor: active ? `${option.color}1A` : pressed ? colors.surfaceStrong : "transparent",
                                        },
                                    ]}
                                    onPress={() => {
                                        onChangeSafetyApprovalMode(option.mode);
                                        setSafetyApprovalOpen(false);
                                    }}
                                >
                                    <MaterialCommunityIcons
                                        name={option.icon}
                                        size={19}
                                        color={active ? option.color : colors.textMuted}
                                    />
                                    <View style={styles.safetyMenuText}>
                                        <Text style={[styles.safetyMenuTitle, { color: colors.text }]} numberOfLines={1}>
                                            {option.title}
                                        </Text>
                                        <Text style={[styles.safetyMenuDescription, { color: colors.textMuted }]} numberOfLines={3}>
                                            {option.description}
                                        </Text>
                                    </View>
                                </Pressable>
                            );
                        })}
                    </ScrollView>
                </View>
            </View>
        </Modal>
        <Modal
            visible={Boolean(pluginInSheet)}
            transparent
            animationType="slide"
            statusBarTranslucent
            onRequestClose={() => setPluginSheetId("")}
        >
            <View style={styles.safetyOverlay}>
                <Pressable style={StyleSheet.absoluteFill} onPress={() => setPluginSheetId("")} />
                <View style={[styles.safetySheet, { paddingBottom: Math.max(safeAreaInsets.bottom, 16), backgroundColor: themeMode === "dark" ? "rgba(24,24,27,0.99)" : "rgba(255,255,255,0.99)", borderColor: colors.border }]}>
                    <View style={[styles.safetySheetHandle, { backgroundColor: colors.border }]} />
                    {pluginInSheet ? (
                        <View style={styles.safetySheetContent}>
                            <Text style={[styles.safetyMenuTitle, { color: colors.text, marginBottom: 4 }]}>{pluginInSheet.displayName}</Text>
                        <Text style={[styles.safetyMenuDescription, { color: colors.textMuted, marginBottom: 12 }]}>{t("src.components.chat.composer.plugin_authorization_description")}</Text>
                            {(["task", "session"] as const).map((scope) => (
                                <Pressable key={scope} style={({ pressed }) => [styles.safetyMenuItem, { backgroundColor: pluginInSheet.grantScope === scope ? `${colors.success}18` : pressed ? colors.surfaceStrong : "transparent" }]} onPress={() => { onChangePluginScope(pluginInSheet.pluginId, scope); setPluginSheetId(""); }}>
                                    <MaterialCommunityIcons name={scope === "task" ? "checkbox-marked-circle-outline" : "chat-processing-outline"} size={19} color={pluginInSheet.grantScope === scope ? colors.success : colors.textMuted} />
                                    <View style={styles.safetyMenuText}>
                                        <Text style={[styles.safetyMenuTitle, { color: colors.text }]}>{scope === "task" ? t("src.components.chat.composer.plugin_scope_task_title") : t("src.components.chat.composer.plugin_scope_session_title")}</Text>
                                        <Text style={[styles.safetyMenuDescription, { color: colors.textMuted }]}>{scope === "task" ? t("src.components.chat.composer.plugin_scope_task_description") : t("src.components.chat.composer.plugin_scope_session_description")}</Text>
                                    </View>
                                </Pressable>
                            ))}
                            <Pressable style={({ pressed }) => [styles.safetyMenuItem, { opacity: pressed ? 0.7 : 1 }]} onPress={() => { onRemovePlugin(pluginInSheet.pluginId); setPluginSheetId(""); }}>
                                <MaterialCommunityIcons name="trash-can-outline" size={19} color={colors.danger} />
                                <Text style={[styles.safetyMenuTitle, { color: colors.danger }]}>{t("src.components.chat.composer.remove_plugin")}</Text>
                            </Pressable>
                        </View>
                    ) : null}
                </View>
            </View>
        </Modal>
        </>
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
        columnGap: 7,
        rowGap: 2,
    },
    composerInputLayer: {
        position: "relative",
        width: "100%",
        minHeight: 32,
        overflow: "hidden",
    },
    inputMirrorViewport: {
        ...StyleSheet.absoluteFillObject,
        overflow: "hidden",
    },
    inputMirrorText: {
        minHeight: 32,
        paddingHorizontal: 2,
        paddingTop: 2,
        paddingBottom: 4,
        fontSize: 16,
        lineHeight: 24,
        includeFontPadding: false,
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
    tokenChip: {
        minHeight: 24,
        maxWidth: "100%",
        paddingHorizontal: 1,
        flexDirection: "row",
        alignItems: "center",
        gap: 4,
    },
    tokenText: {
        fontSize: 16,
        fontWeight: "700",
        lineHeight: 24,
        flexShrink: 1,
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
    workModeButton: {
        width: 32,
        paddingHorizontal: 0,
    },
    safetyControl: {
        zIndex: 20,
    },
    safetyModeButton: {
        width: 32,
        height: 32,
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 12,
        borderWidth: 1,
    },
    safetyOverlay: {
        flex: 1,
        justifyContent: "flex-end",
        backgroundColor: "rgba(2,6,23,0.42)",
    },
    safetySheet: {
        maxHeight: "58%",
        borderTopLeftRadius: 24,
        borderTopRightRadius: 24,
        borderWidth: 1,
        paddingHorizontal: 12,
        paddingTop: 8,
        shadowOpacity: 0.2,
        shadowRadius: 24,
        shadowOffset: { width: 0, height: -8 },
        elevation: 24,
    },
    safetySheetHandle: {
        width: 38,
        height: 4,
        borderRadius: 999,
        alignSelf: "center",
        marginBottom: 8,
    },
    safetySheetScroll: {
        flexGrow: 0,
    },
    safetySheetContent: {
        gap: 4,
    },
    safetyMenuItem: {
        minHeight: 52,
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 8,
        borderRadius: 14,
        paddingHorizontal: 10,
        paddingVertical: 9,
    },
    safetyMenuText: {
        flex: 1,
        minWidth: 0,
        gap: 2,
    },
    safetyMenuTitle: {
        fontSize: 12,
        lineHeight: 16,
        fontWeight: "800",
    },
    safetyMenuDescription: {
        fontSize: 11,
        lineHeight: 15,
        fontWeight: "600",
    },
    reasoningEffortButton: {
        width: 32,
        height: 32,
        borderRadius: 14,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    reasoningEffortOverlay: {
        flex: 1,
        alignItems: "center",
        justifyContent: "flex-end",
        backgroundColor: "rgba(2,6,23,0.28)",
    },
    reasoningEffortPopover: {
        maxWidth: 352,
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
