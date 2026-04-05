import { memo, useEffect, useRef, useState } from "react";
import {
    Keyboard,
    Platform,
    Pressable,
    ScrollView,
    StyleSheet,
    Text,
    TextInput,
    View,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { LinearGradient } from "expo-linear-gradient";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";
import type { CommandPresetSummary, SkillReferenceSummary, UploadedWorkspaceFile } from "@/src/types/admin";

export const Composer = memo(function Composer({
    value,
    onChange,
    onSend,
    busy = false,
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
    const [keyboardVisible, setKeyboardVisible] = useState(false);
    const inputRef = useRef<TextInput | null>(null);
    const canSend = Boolean(value.trim() || selectedCommand || selectedSkills.length > 0 || uploadedFiles.length > 0) && !busy;
    const inputShellBackground = themeMode === "dark"
        ? "rgba(28,25,23,0.86)"
        : "rgba(255,255,255,0.92)";
    const inputShellBorder = isFocused
        ? (themeMode === "dark" ? "rgba(245,158,11,0.32)" : "rgba(249,115,22,0.28)")
        : "transparent";
    const inputWebStyle: any = Platform.OS === "web"
        ? {
            outlineStyle: "none",
            boxShadow: "none",
            appearance: "none",
            WebkitAppearance: "none",
        }
        : null;

    const forceFocusInput = () => {
        if (!inputRef.current) {
            return;
        }
        if (Platform.OS === "android" && isFocused && !keyboardVisible) {
            inputRef.current.blur();
            setTimeout(() => inputRef.current?.focus(), 40);
            return;
        }
        inputRef.current.focus();
    };

    const handleInputShellPress = () => {
        forceFocusInput();
    };

    useEffect(() => {
        const showEvent = Platform.OS === "ios" ? "keyboardWillShow" : "keyboardDidShow";
        const hideEvent = Platform.OS === "ios" ? "keyboardWillHide" : "keyboardDidHide";
        const showSubscription = Keyboard.addListener(showEvent, () => setKeyboardVisible(true));
        const hideSubscription = Keyboard.addListener(hideEvent, () => setKeyboardVisible(false));
        return () => {
            showSubscription.remove();
            hideSubscription.remove();
        };
    }, []);

    return (
        <View style={styles.shell}>
            <View style={styles.inputStage}>
                {(selectedCommand || selectedSkills.length > 0 || taskPlanningMode) && (
                    <ScrollView
                        horizontal
                        showsHorizontalScrollIndicator={false}
                        contentContainerStyle={styles.chipRow}
                    >
                        {selectedCommand ? (
                            <View style={[styles.metaChip, { backgroundColor: colors.accentSoft, borderColor: colors.accentSoft }]}>
                                <MaterialCommunityIcons name="slash-forward" size={14} color={colors.accent} />
                                <Text style={[styles.metaChipText, { color: colors.accent }]}>Preset: {selectedCommand.name}</Text>
                                <Pressable onPress={onClearCommand}>
                                    <MaterialCommunityIcons name="close" size={15} color={colors.accent} />
                                </Pressable>
                            </View>
                        ) : null}
                        {selectedSkills.map((skill) => (
                            <View key={`${skill.name}:${skill.path || ""}`} style={[styles.metaChip, { backgroundColor: colors.primarySoft, borderColor: colors.primarySoft }]}>
                                <MaterialCommunityIcons name="at" size={14} color={colors.primary} />
                                <Text style={[styles.metaChipText, { color: colors.primaryDeep }]}>{skill.name}</Text>
                                <Pressable onPress={() => onRemoveSkill(skill)}>
                                    <MaterialCommunityIcons name="close" size={15} color={colors.primaryDeep} />
                                </Pressable>
                            </View>
                        ))}
                        {taskPlanningMode ? (
                            <View style={[styles.metaChip, { backgroundColor: "rgba(16,185,129,0.12)", borderColor: "rgba(16,185,129,0.18)" }]}>
                                <MaterialCommunityIcons name="format-list-checks" size={14} color={colors.success} />
                                <Text style={[styles.metaChipText, { color: colors.success }]}>{t("任务模式", "Task mode")}</Text>
                            </View>
                        ) : null}
                    </ScrollView>
                )}

                {uploadedFiles.length > 0 && (
                    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
                        {uploadedFiles.map((file) => (
                            <View
                                key={`${file.url || file.publicUrl || file.name || "file"}:${file.createdAt || ""}`}
                                style={[styles.metaChip, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}
                            >
                                <MaterialCommunityIcons name="paperclip" size={14} color={colors.textMuted} />
                                <Text style={[styles.metaChipText, { color: colors.text, maxWidth: 180 }]} numberOfLines={1}>
                                    {file.name || t("附件", "Attachment")}
                                </Text>
                                <Pressable onPress={() => onRemoveUploadedFile(file)}>
                                    <MaterialCommunityIcons name="close" size={15} color={colors.textMuted} />
                                </Pressable>
                            </View>
                        ))}
                    </ScrollView>
                )}

                <View
                    style={[
                        styles.inputShell,
                        {
                            backgroundColor: inputShellBackground,
                            borderColor: inputShellBorder,
                            shadowColor: themeMode === "dark" ? "#000000" : "#0F172A",
                        },
                    ]}
                >
                    <Pressable style={styles.inputFocusSurface} onPress={handleInputShellPress} onPressIn={handleInputShellPress}>
                        <View style={styles.editorSurface}>
                            <TextInput
                                ref={inputRef}
                                value={value}
                                onChangeText={onChange}
                                onFocus={() => setIsFocused(true)}
                                onBlur={() => setIsFocused(false)}
                                placeholder={t("给 智能主管 发送消息...", "Message Supervisor...")}
                            placeholderTextColor={colors.textSoft}
                            multiline
                            underlineColorAndroid="transparent"
                            selectionColor={Platform.OS === "android" ? "rgba(0,0,0,0)" : colors.primary}
                            cursorColor={colors.accent}
                            autoCorrect={false}
                            spellCheck={false}
                            autoComplete="off"
                            textAlignVertical="top"
                            onTouchStart={handleInputShellPress}
                            style={[styles.input, { color: colors.text }, inputWebStyle]}
                            />
                        </View>
                    </Pressable>

                    <View style={styles.bottomControls}>
                        <Pressable
                            style={[
                                styles.taskModeButton,
                                taskPlanningMode && { backgroundColor: colors.primarySoft },
                            ]}
                            onPress={onToggleTaskPlanningMode}
                        >
                            <MaterialCommunityIcons
                                name="format-list-checks"
                                size={15}
                                color={taskPlanningMode ? colors.primaryDeep : colors.textMuted}
                            />
                            <Text style={[styles.taskModeText, { color: taskPlanningMode ? colors.primaryDeep : colors.textMuted }]}>
                                {t("任务模式", "Task mode")}
                            </Text>
                        </Pressable>

                        <View style={styles.actionRow}>
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
                            <Pressable disabled={!canSend} onPress={onSend} style={[styles.sendWrap, !canSend && styles.disabled]}>
                                <LinearGradient
                                    colors={canSend ? [colors.accent, "#F59E0B"] : ["#CBD5E1", "#CBD5E1"]}
                                    start={{ x: 0, y: 0 }}
                                    end={{ x: 1, y: 1 }}
                                    style={styles.sendButton}
                                >
                                    <MaterialCommunityIcons name={busy ? "loading" : "send"} size={18} color="#FFFFFF" />
                                </LinearGradient>
                            </Pressable>
                        </View>
                    </View>
                </View>
            </View>
        </View>
    );
});

const styles = StyleSheet.create({
    shell: {
        gap: 8,
    },
    inputStage: {
        position: "relative",
        gap: 8,
    },
    chipRow: {
        flexDirection: "row",
        gap: 8,
        paddingHorizontal: 2,
    },
    metaChip: {
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        borderRadius: radii.pill,
        paddingHorizontal: 12,
        paddingVertical: 7,
        borderWidth: 1,
    },
    metaChipText: {
        fontSize: 11,
        fontWeight: "700",
    },
    inputShell: {
        borderRadius: 30,
        minHeight: 108,
        paddingHorizontal: 14,
        paddingTop: 14,
        paddingBottom: 12,
        borderWidth: 1,
        shadowOpacity: 0.08,
        shadowRadius: 16,
        shadowOffset: { width: 0, height: 8 },
        elevation: 2,
    },
    inputFocusSurface: {
        minHeight: 56,
        justifyContent: "flex-start",
    },
    editorSurface: {
        minHeight: 56,
        borderRadius: 20,
        justifyContent: "flex-start",
        backgroundColor: "transparent",
        overflow: "hidden",
        paddingBottom: 2,
    },
    input: {
        minHeight: 56,
        maxHeight: 140,
        fontSize: 15,
        lineHeight: 22,
        textAlignVertical: "top",
        backgroundColor: "transparent",
        borderWidth: 0,
        paddingTop: 2,
        paddingBottom: 6,
        paddingHorizontal: 0,
        paddingVertical: 0,
        margin: 0,
        includeFontPadding: false,
        borderRadius: 0,
        borderBottomWidth: 0,
        borderBottomColor: "transparent",
    },
    bottomControls: {
        marginTop: 4,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 8,
    },
    taskModeButton: {
        minHeight: 28,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        paddingHorizontal: 6,
        borderRadius: 10,
    },
    taskModeText: {
        fontSize: 12,
        fontWeight: "600",
    },
    actionRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
    },
    inlineButton: {
        width: 28,
        height: 28,
        borderRadius: 10,
        alignItems: "center",
        justifyContent: "center",
    },
    sendWrap: {
        shadowColor: "#FB923C",
        shadowOpacity: 0.18,
        shadowRadius: 10,
        shadowOffset: { width: 0, height: 6 },
        elevation: 2,
    },
    sendButton: {
        width: 36,
        height: 36,
        borderRadius: 18,
        alignItems: "center",
        justifyContent: "center",
    },
    disabled: {
        opacity: 0.56,
    },
});
