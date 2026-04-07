import { memo, useRef, useState } from "react";
import {
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
    const inputRef = useRef<TextInput | null>(null);
    const canSend = Boolean(value.trim() || selectedCommand || selectedSkills.length > 0 || uploadedFiles.length > 0) && !busy;
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
                                <Text style={[styles.taskModeText, { color: taskPlanningMode ? colors.primaryDeep : colors.textMuted }]}>
                                    {t("任务模式", "Task mode")}
                                </Text>
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
    },
    input: {
        minHeight: 82,
        maxHeight: 220,
        width: "100%",
        flexGrow: 1,
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
        flexWrap: "wrap",
    },
    taskModeButton: {
        minHeight: 30,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        paddingHorizontal: 8,
        borderRadius: 12,
        borderWidth: 1,
    },
    taskModeText: {
        fontSize: 12,
        fontWeight: "600",
    },
    inlineButton: {
        width: 32,
        height: 32,
        borderRadius: 12,
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
