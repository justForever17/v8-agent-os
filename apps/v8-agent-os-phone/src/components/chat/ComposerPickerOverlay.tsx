import { memo, useMemo } from "react";
import {
    Pressable,
    StyleSheet,
    Text,
    View,
    useWindowDimensions,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { FlatList as GestureFlatList } from "react-native-gesture-handler";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";
import type { CommandPresetSummary, SkillReferenceSummary, SubagentFamilySummary } from "@/src/types/admin";

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
    mentions: ComposerMentionItem[];
    onSelectCommand: (command: CommandPresetSummary) => void;
    onSelectSkill: (skill: SkillReferenceSummary) => void;
    onSelectSubagentFamily: (family: SubagentFamilySummary) => void;
};

type ComposerMentionItem =
    | { kind: "skill"; key: string; skill: SkillReferenceSummary }
    | { kind: "subagent_family"; key: string; family: SubagentFamilySummary };

export const ComposerPickerOverlay = memo(function ComposerPickerOverlay({
    visible,
    mode,
    left,
    right,
    bottom,
    position = "absolute",
    commands,
    mentions,
    onSelectCommand,
    onSelectSkill,
    onSelectSubagentFamily,
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
    const data: Array<CommandPresetSummary | ComposerMentionItem> = isCommand ? commands : mentions;
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
    const renderMentionItem = ({ item }: { item: ComposerMentionItem }) => (
        item.kind === "skill" ? renderSkillItem({ item: item.skill }) : (
            <Pressable onPress={() => onSelectSubagentFamily(item.family)} style={styles.row}>
                <MaterialCommunityIcons name="account-group-outline" size={16} color={colors.accent} />
                <View style={styles.body}>
                    <Text style={[styles.title, { color: colors.text }]}>{item.family.displayName || item.family.familyId}</Text>
                    {item.family.description ? (
                        <Text style={[styles.summary, { color: colors.textMuted }]} numberOfLines={2}>
                            {item.family.description}
                        </Text>
                    ) : null}
                    <Text style={[styles.pathText, { color: colors.textSoft }]} numberOfLines={1}>
                        {item.family.memberCount ? `${item.family.memberCount} members` : item.family.familyId}
                    </Text>
                </View>
            </Pressable>
        )
    );
    const renderPickerItem = ({ item }: { item: CommandPresetSummary | ComposerMentionItem }) => (
        isCommand
            ? renderCommandItem({ item: item as CommandPresetSummary })
            : renderMentionItem({ item: item as ComposerMentionItem })
    );

    return (
        <View
            style={[
                position === "absolute"
                    ? styles.overlay
                    : styles.inlineOverlay,
            ]}
            pointerEvents={position === "absolute" ? "box-none" : "auto"}
        >
            {position === "absolute" ? (
                <Pressable style={StyleSheet.absoluteFill} onPress={() => {}} />
            ) : null}
            <View
                style={[
                    position === "absolute"
                        ? [
                            styles.panelAnchor,
                            {
                                left,
                                right,
                                bottom,
                            },
                        ]
                        : styles.inlinePanelAnchor,
                ]}
                pointerEvents="box-none"
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
                    pointerEvents="auto"
                >
                    <Text style={[styles.hint, { color: colors.textMuted }]}>
                        {isCommand
                            ? t("src.components.chat.composerpickeroverlay.type_to_choose_a_preset")
                            : t("src.components.chat.composerpickeroverlay.type_to_choose_one_or_more_skills")}
                    </Text>

                    <View style={[styles.viewport, { height: viewportHeight }]}>
                        <GestureFlatList
                            data={data}
                            keyExtractor={(item) => isCommand ? (item as CommandPresetSummary).name : (item as ComposerMentionItem).key}
                            renderItem={renderPickerItem}
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
                                        ? t("src.components.chat.composerpickeroverlay.no_matching_presets")
                                        : t("src.components.chat.composerpickeroverlay.no_matching_skills")}
                                </Text>
                            )}
                        />
                    </View>
                </View>
            </View>
        </View>
    );
});

const styles = StyleSheet.create({
    overlay: {
        ...StyleSheet.absoluteFillObject,
        zIndex: 32,
        elevation: 32,
    },
    inlineOverlay: {
        width: "100%",
        zIndex: 32,
        elevation: 32,
    },
    panelAnchor: {
        position: "absolute",
    },
    inlinePanelAnchor: {
        width: "100%",
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
