import { memo, useMemo, useState } from "react";
import {
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

function extractSkillQuery(input: string) {
    const match = input.match(/(?:^|\s)@([^\s@]*)$/);
    return match ? match[1] : "";
}

export const Composer = memo(function Composer({
    value,
    onChange,
    onSend,
    busy = false,
    selectedCommand,
    onSelectCommand,
    onClearCommand,
    selectedSkills,
    onAddSkill,
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
    commands,
    skills,
}: {
    value: string;
    onChange: (next: string) => void;
    onSend: () => void;
    busy?: boolean;
    selectedCommand: CommandPresetSummary | null;
    onSelectCommand: (command: CommandPresetSummary) => void;
    onClearCommand: () => void;
    selectedSkills: SkillReferenceSummary[];
    onAddSkill: (skill: SkillReferenceSummary) => void;
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
    commands: CommandPresetSummary[];
    skills: SkillReferenceSummary[];
}) {
    const { colors, t, themeMode } = useUiPrefs();
    const [isFocused, setIsFocused] = useState(false);

    const slashQuery = useMemo(() => {
        const trimmed = value.trimStart();
        return !selectedCommand && trimmed.startsWith("/") ? trimmed.slice(1).trim().toLowerCase() : "";
    }, [selectedCommand, value]);

    const skillQuery = useMemo(() => extractSkillQuery(value).toLowerCase(), [value]);

    const filteredCommands = useMemo(() => {
        if (!slashQuery) return commands;
        return commands.filter((item) =>
            item.name.toLowerCase().includes(slashQuery)
            || String(item.summary || "").toLowerCase().includes(slashQuery),
        );
    }, [commands, slashQuery]);

    const filteredSkills = useMemo(() => {
        const selectedKeys = new Set(selectedSkills.map((skill) => `${skill.name}:${skill.path || ""}`));
        const base = skills.filter((item) => !selectedKeys.has(`${item.name}:${item.path || ""}`));
        if (!skillQuery) return base;
        return base.filter((item) =>
            item.name.toLowerCase().includes(skillQuery)
            || String(item.description || "").toLowerCase().includes(skillQuery)
            || String(item.path || "").toLowerCase().includes(skillQuery),
        );
    }, [selectedSkills, skillQuery, skills]);

    const commandPickerOpen = !selectedCommand && value.trimStart().startsWith("/");
    const skillPickerOpen = /(?:^|\s)@([^\s@]*)$/.test(value);
    const canSend = Boolean(value.trim() || selectedCommand || selectedSkills.length > 0 || uploadedFiles.length > 0) && !busy;
    const inputShellBackground = themeMode === "dark"
        ? "rgba(28,25,23,0.86)"
        : "rgba(255,255,255,0.92)";
    const inputShellBorder = isFocused
        ? (themeMode === "dark" ? "rgba(245,158,11,0.32)" : "rgba(249,115,22,0.28)")
        : "transparent";

    return (
        <View style={styles.shell}>
            <View style={styles.inputStage}>
                {commandPickerOpen ? (
                    <View style={[styles.pickerPopover, { backgroundColor: colors.surface, borderColor: colors.border }]}>
                        <Text style={[styles.pickerHint, { color: colors.textMuted }]}>{t("输入 / 选择命令预设", "Type / to choose a preset")}</Text>
                        <ScrollView nestedScrollEnabled style={styles.pickerScroll}>
                            {filteredCommands.map((item) => (
                                <Pressable key={item.name} onPress={() => onSelectCommand(item)} style={styles.pickerItem}>
                                    <MaterialCommunityIcons name="slash-forward" size={16} color={colors.accent} />
                                    <View style={styles.pickerBody}>
                                        <Text style={[styles.pickerTitle, { color: colors.text }]}>{item.name}</Text>
                                        {item.summary ? (
                                            <Text style={[styles.pickerSummary, { color: colors.textMuted }]} numberOfLines={2}>
                                                {item.summary}
                                            </Text>
                                        ) : null}
                                    </View>
                                </Pressable>
                            ))}
                            {filteredCommands.length === 0 ? <Text style={[styles.emptyText, { color: colors.textMuted }]}>{t("没有匹配的命令预设", "No matching presets")}</Text> : null}
                        </ScrollView>
                    </View>
                ) : null}

                {skillPickerOpen ? (
                    <View style={[styles.pickerPopover, { backgroundColor: colors.surface, borderColor: colors.border }]}>
                        <Text style={[styles.pickerHint, { color: colors.textMuted }]}>{t("输入 @ 选择一个或多个 Skill", "Type @ to choose one or more skills")}</Text>
                        <ScrollView nestedScrollEnabled style={styles.pickerScroll}>
                            {filteredSkills.map((item) => (
                                <Pressable
                                    key={`${item.name}:${item.path || ""}`}
                                    onPress={() => onAddSkill(item)}
                                    style={styles.pickerItem}
                                >
                                    <MaterialCommunityIcons name="at" size={16} color={colors.primary} />
                                    <View style={styles.pickerBody}>
                                        <Text style={[styles.pickerTitle, { color: colors.text }]}>{item.name}</Text>
                                        {item.description ? (
                                            <Text style={[styles.pickerSummary, { color: colors.textMuted }]} numberOfLines={2}>
                                                {item.description}
                                            </Text>
                                        ) : null}
                                        {item.path ? (
                                            <Text style={[styles.pathText, { color: colors.textSoft }]} numberOfLines={1}>
                                                {item.path}
                                            </Text>
                                        ) : null}
                                    </View>
                                </Pressable>
                            ))}
                            {filteredSkills.length === 0 ? <Text style={[styles.emptyText, { color: colors.textMuted }]}>{t("没有匹配的 Skill", "No matching skills")}</Text> : null}
                        </ScrollView>
                    </View>
                ) : null}

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
                    <View style={styles.inputCore}>
                        <TextInput
                            value={value}
                            onChangeText={onChange}
                            onFocus={() => setIsFocused(true)}
                            onBlur={() => setIsFocused(false)}
                            placeholder={t("给 智能主管 发送消息...", "Message Supervisor...")}
                            placeholderTextColor={colors.textSoft}
                            multiline
                            underlineColorAndroid="transparent"
                            selectionColor={colors.primary}
                            cursorColor={colors.primary}
                            textAlignVertical="top"
                            style={[styles.input, { color: colors.text }]}
                        />
                    </View>

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
        paddingTop: 12,
        paddingBottom: 10,
        borderWidth: 1,
        shadowOpacity: 0.08,
        shadowRadius: 16,
        shadowOffset: { width: 0, height: 8 },
        elevation: 2,
    },
    input: {
        minHeight: 44,
        maxHeight: 140,
        fontSize: 15,
        lineHeight: 22,
        textAlignVertical: "top",
        backgroundColor: "transparent",
        borderWidth: 0,
        paddingTop: 4,
        paddingBottom: 2,
        paddingHorizontal: 0,
        margin: 0,
        includeFontPadding: false,
    },
    inputCore: {
        minHeight: 52,
        backgroundColor: "transparent",
        borderRadius: 20,
        overflow: "hidden",
        justifyContent: "flex-start",
    },
    bottomControls: {
        marginTop: 6,
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
    pickerPopover: {
        position: "absolute",
        left: 0,
        right: 0,
        bottom: "100%",
        marginBottom: 8,
        zIndex: 20,
        maxHeight: 240,
        borderRadius: 20,
        borderWidth: 1,
        paddingHorizontal: 12,
        paddingTop: 10,
        paddingBottom: 6,
        shadowColor: "#0F172A",
        shadowOpacity: 0.08,
        shadowRadius: 16,
        shadowOffset: { width: 0, height: 10 },
        elevation: 3,
    },
    pickerHint: {
        fontSize: 12,
        fontWeight: "700",
        marginBottom: 8,
    },
    pickerScroll: {
        maxHeight: 190,
    },
    pickerItem: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 10,
        paddingVertical: 10,
    },
    pickerBody: {
        flex: 1,
        gap: 2,
    },
    pickerTitle: {
        fontSize: 14,
        fontWeight: "800",
    },
    pickerSummary: {
        fontSize: 12,
        lineHeight: 18,
    },
    pathText: {
        fontSize: 11,
    },
    emptyText: {
        fontSize: 12,
        paddingVertical: 10,
    },
    disabled: {
        opacity: 0.56,
    },
});
