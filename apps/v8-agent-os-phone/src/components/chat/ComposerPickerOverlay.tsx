import { memo, useMemo, type ReactNode } from "react";
import {
    Pressable,
    StyleSheet,
    Text,
    View,
    useWindowDimensions,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { FlatList as GestureFlatList } from "react-native-gesture-handler";
import Svg, { Circle } from "react-native-svg";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import type { CommandPresetSummary, PluginReferenceSummary, SkillReferenceSummary, SubagentFamilySummary } from "@/src/types/admin";

const PICKER_MIN_VIEWPORT_HEIGHT = 248;
const PICKER_MAX_VIEWPORT_HEIGHT = 372;

function ContextUsageRing({ percent, color, trackColor }: { percent: number; color: string; trackColor: string }) {
    const radius = 9;
    const circumference = Math.PI * 2 * radius;
    const progress = circumference * Math.max(0, Math.min(100, percent)) / 100;
    return (
        <Svg width={24} height={24} viewBox="0 0 24 24" style={styles.contextRing}>
            <Circle cx="12" cy="12" r={radius} fill="none" stroke={trackColor} strokeWidth={3} />
            <Circle
                cx="12"
                cy="12"
                r={radius}
                fill="none"
                stroke={color}
                strokeWidth={3}
                strokeLinecap="round"
                strokeDasharray={`${progress} ${circumference - progress}`}
                rotation={-90}
                origin="12, 12"
            />
        </Svg>
    );
}

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
    onSelectPlugin: (plugin: PluginReferenceSummary) => void;
};

type ComposerMentionItem =
    | { kind: "skill"; key: string; skill: SkillReferenceSummary }
    | { kind: "subagent_family"; key: string; family: SubagentFamilySummary }
    | { kind: "plugin"; key: string; plugin: PluginReferenceSummary };

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
    onSelectPlugin,
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
    const panelHeight = Math.min(viewportHeight, Math.max(64, Math.min(data.length, 7) * 48 + 8));
    const renderRow = ({
        title,
        summary,
        meta,
        icon,
        onPress,
    }: {
        title: string;
        summary?: string;
        meta: string;
        icon: ReactNode;
        onPress: () => void;
    }) => (
        <Pressable
            onPress={onPress}
            style={({ pressed }) => [styles.row, pressed ? { backgroundColor: colors.primarySoft } : null]}
        >
            <View style={styles.iconSlot}>{icon}</View>
            <Text style={styles.rowCopy} numberOfLines={1}>
                <Text style={[styles.rowTitle, { color: colors.text }]}>{title}</Text>
                {summary ? <Text style={[styles.rowSummary, { color: colors.textMuted }]}>  {summary}</Text> : null}
            </Text>
            <Text style={[styles.rowMeta, { color: colors.textSoft }]} numberOfLines={1}>{meta}</Text>
        </Pressable>
    );
    const renderCommandItem = ({ item }: { item: CommandPresetSummary }) => renderRow({
        title: item.name,
        summary: item.summary,
        meta: item.readOnlyKind === "context_usage" && typeof item.usagePercent === "number" ? `${item.usagePercent}%` : "Command",
        icon: item.readOnlyKind === "context_usage" && typeof item.usagePercent === "number" ? (
            <ContextUsageRing percent={item.usagePercent} color={colors.primary} trackColor={colors.border} />
        ) : (
            <MaterialCommunityIcons
                name={item.specCommandAction ? "file-document-check-outline" : "text-short"}
                size={16}
                color={colors.primary}
            />
        ),
        onPress: () => onSelectCommand(item),
    });
    const renderMentionItem = ({ item }: { item: ComposerMentionItem }) => {
        if (item.kind === "skill") {
            return renderRow({
                title: item.skill.name,
                summary: item.skill.description || item.skill.path,
                meta: "Skill",
                icon: <MaterialCommunityIcons name="at" size={16} color={colors.primary} />,
                onPress: () => onSelectSkill(item.skill),
            });
        }
        if (item.kind === "subagent_family") {
            return renderRow({
                title: item.family.displayName || item.family.familyId,
                summary: item.family.description,
                meta: "Agent",
                icon: <MaterialCommunityIcons name="account-group-outline" size={16} color={colors.primary} />,
                onPress: () => onSelectSubagentFamily(item.family),
            });
        }
        return renderRow({
            title: item.plugin.displayName,
            summary: item.plugin.description,
            meta: item.plugin.status === "ready" ? "Plugin · Ready" : "Plugin · Setup",
            icon: <MaterialCommunityIcons name="puzzle-outline" size={16} color={item.plugin.status === "ready" ? colors.success : colors.warning} />,
            onPress: () => onSelectPlugin(item.plugin),
        });
    };
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
                    <View style={[styles.viewport, { height: panelHeight }]}>
                        <GestureFlatList
                            data={data}
                            keyExtractor={(item) => isCommand ? (item as CommandPresetSummary).name : (item as ComposerMentionItem).key}
                            renderItem={renderPickerItem}
                            scrollEnabled
                            nestedScrollEnabled
                            keyboardShouldPersistTaps="handled"
                            keyboardDismissMode="none"
                            showsVerticalScrollIndicator={false}
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
    contextRing: { marginTop: 1 },
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
        borderRadius: 18,
        borderWidth: 1,
        padding: 6,
        shadowOpacity: 0.1,
        shadowRadius: 18,
        shadowOffset: { width: 0, height: 12 },
        elevation: 8,
    },
    viewport: {
        borderRadius: 12,
        overflow: "hidden",
    },
    listContent: {
        paddingVertical: 2,
    },
    row: {
        minHeight: 46,
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
        paddingVertical: 8,
        paddingHorizontal: 10,
        borderRadius: 10,
    },
    iconSlot: {
        width: 24,
        height: 24,
        alignItems: "center",
        justifyContent: "center",
    },
    rowCopy: {
        flex: 1,
        minWidth: 0,
    },
    rowTitle: {
        fontSize: 14,
        fontWeight: "800",
        lineHeight: 20,
    },
    rowSummary: {
        fontSize: 12,
        lineHeight: 20,
    },
    rowMeta: {
        maxWidth: 92,
        fontSize: 11,
        fontWeight: "700",
        lineHeight: 16,
    },
    emptyText: {
        fontSize: 12,
        paddingVertical: 12,
        textAlign: "center",
    },
});
