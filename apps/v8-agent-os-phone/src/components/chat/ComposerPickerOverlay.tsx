import { memo, useMemo } from "react";
import {
    FlatList,
    Pressable,
    StyleSheet,
    Text,
    View,
    useWindowDimensions,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";
import type { CommandPresetSummary, SkillReferenceSummary } from "@/src/types/admin";

const PICKER_MIN_VIEWPORT_HEIGHT = 248;
const PICKER_MAX_VIEWPORT_HEIGHT = 372;

type ComposerPickerOverlayProps = {
    visible: boolean;
    mode: "command" | "skill" | null;
    left: number;
    right: number;
    bottom: number;
    position?: "absolute" | "inline";
    commands: CommandPresetSummary[];
    skills: SkillReferenceSummary[];
    onSelectCommand: (command: CommandPresetSummary) => void;
    onSelectSkill: (skill: SkillReferenceSummary) => void;
};

export const ComposerPickerOverlay = memo(function ComposerPickerOverlay({
    visible,
    mode,
    left,
    right,
    bottom,
    position = "absolute",
    commands,
    skills,
    onSelectCommand,
    onSelectSkill,
}: ComposerPickerOverlayProps) {
    const { colors, t, themeMode } = useUiPrefs();
    const { height: windowHeight } = useWindowDimensions();
    const viewportHeight = useMemo(
        () => Math.max(PICKER_MIN_VIEWPORT_HEIGHT, Math.min(PICKER_MAX_VIEWPORT_HEIGHT, Math.round(windowHeight * 0.42))),
        [windowHeight],
    );

    if (!visible || !mode) {
        return null;
    }

    const isCommand = mode === "command";
    const data = isCommand ? commands : skills;
    const renderCommandItem = ({ item }: { item: CommandPresetSummary }) => (
        <Pressable onPress={() => onSelectCommand(item)} style={styles.row}>
            <MaterialCommunityIcons name="slash-forward" size={16} color={colors.accent} />
            <View style={styles.body}>
                <Text style={[styles.title, { color: colors.text }]}>{item.name}</Text>
                {item.summary ? (
                    <Text style={[styles.summary, { color: colors.textMuted }]} numberOfLines={2}>
                        {item.summary}
                    </Text>
                ) : null}
            </View>
        </Pressable>
    );
    const renderSkillItem = ({ item }: { item: SkillReferenceSummary }) => (
        <Pressable onPress={() => onSelectSkill(item)} style={styles.row}>
            <MaterialCommunityIcons name="at" size={16} color={colors.primary} />
            <View style={styles.body}>
                <Text style={[styles.title, { color: colors.text }]}>{item.name}</Text>
                {item.description ? (
                    <Text style={[styles.summary, { color: colors.textMuted }]} numberOfLines={2}>
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
    );

    return (
        <View
            style={[
                position === "absolute"
                    ? [
                        styles.overlay,
                        {
                            left,
                            right,
                            bottom,
                        },
                    ]
                    : styles.inlineOverlay,
            ]}
            pointerEvents={position === "absolute" ? "box-none" : "auto"}
        >
            <View
                style={[
                    styles.panel,
                    {
                        backgroundColor: colors.surfaceStrong,
                        borderColor: colors.border,
                        shadowColor: themeMode === "dark" ? "#000000" : "#0F172A",
                    },
                ]}
            >
                <Text style={[styles.hint, { color: colors.textMuted }]}>
                    {isCommand
                        ? t("输入 / 选择命令预设", "Type / to choose a preset")
                        : t("输入 @ 选择一个或多个 Skill", "Type @ to choose one or more skills")}
                </Text>

                <View style={[styles.viewport, { height: viewportHeight }]}>
                    <FlatList
                        data={data}
                        keyExtractor={(item) => isCommand ? item.name : `${item.name}:${item.path || ""}`}
                        renderItem={isCommand ? renderCommandItem : renderSkillItem}
                        scrollEnabled
                        nestedScrollEnabled
                        persistentScrollbar
                        keyboardShouldPersistTaps="handled"
                        keyboardDismissMode="none"
                        showsVerticalScrollIndicator
                        overScrollMode="never"
                        removeClippedSubviews={false}
                        contentContainerStyle={styles.listContent}
                        ListEmptyComponent={(
                            <Text style={[styles.emptyText, { color: colors.textMuted }]}>
                                {isCommand
                                    ? t("没有匹配的命令预设", "No matching presets")
                                    : t("没有匹配的 Skill", "No matching skills")}
                            </Text>
                        )}
                    />
                </View>
            </View>
        </View>
    );
});

const styles = StyleSheet.create({
    overlay: {
        position: "absolute",
        zIndex: 32,
        elevation: 32,
    },
    inlineOverlay: {
        width: "100%",
        zIndex: 32,
        elevation: 32,
    },
    panel: {
        borderRadius: 22,
        borderWidth: 1,
        paddingHorizontal: 12,
        paddingTop: 10,
        paddingBottom: 8,
        shadowOpacity: 0.12,
        shadowRadius: 18,
        shadowOffset: { width: 0, height: 12 },
        elevation: 8,
    },
    hint: {
        fontSize: 12,
        fontWeight: "700",
        marginBottom: 8,
    },
    viewport: {
        borderRadius: 18,
        overflow: "hidden",
    },
    listContent: {
        paddingBottom: 8,
    },
    row: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 10,
        paddingVertical: 10,
        paddingHorizontal: 2,
    },
    body: {
        flex: 1,
        minWidth: 0,
        gap: 2,
    },
    title: {
        fontSize: 14,
        fontWeight: "800",
    },
    summary: {
        fontSize: 12,
        lineHeight: 18,
    },
    pathText: {
        fontSize: 11,
    },
    emptyText: {
        fontSize: 12,
        paddingVertical: 12,
        textAlign: "center",
    },
});
