import { MaterialCommunityIcons } from "@expo/vector-icons";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

type SupervisorActivitySummaryProps = {
    title: string;
    iconName: string;
    expanded: boolean;
    previewLines?: string[];
    accessibilityLabel: string;
    onPress: () => void;
};

type SupervisorContextDividerProps = {
    label: string;
    detail?: string;
    phase: "running" | "completed";
};

function useSupervisorActivityColors() {
    const { colors, themeMode } = useUiPrefs();
    const dark = themeMode === "dark";
    return {
        colors,
        title: dark ? "rgba(255,255,255,0.66)" : colors.textMuted,
        preview: dark ? "rgba(255,255,255,0.38)" : colors.textSoft,
        line: dark ? "rgba(255,255,255,0.18)" : colors.border,
        iconBorder: dark ? "rgba(255,255,255,0.34)" : `${colors.textMuted}55`,
        iconSurface: dark ? "rgba(255,255,255,0.035)" : "rgba(15,23,42,0.035)",
    };
}

export function SupervisorActivitySummary({
    title,
    iconName,
    expanded,
    previewLines = [],
    accessibilityLabel,
    onPress,
}: SupervisorActivitySummaryProps) {
    const activityColors = useSupervisorActivityColors();
    const visiblePreviewLines = expanded ? [] : previewLines.slice(0, 3);

    return (
        <Pressable
            accessibilityRole="button"
            accessibilityLabel={accessibilityLabel}
            style={styles.summaryButton}
            onPress={onPress}
        >
            <View style={styles.summaryHeader}>
                <View
                    style={[
                        styles.summaryIconBox,
                        {
                            borderColor: activityColors.iconBorder,
                            backgroundColor: activityColors.iconSurface,
                        },
                    ]}
                >
                    <MaterialCommunityIcons name={iconName as never} size={13} color={activityColors.title} />
                </View>
                <Text
                    style={[styles.summaryTitle, { color: activityColors.title }]}
                    numberOfLines={1}
                    ellipsizeMode="tail"
                >
                    {title}
                </Text>
                <MaterialCommunityIcons
                    name={expanded ? "chevron-down" : "chevron-right"}
                    size={16}
                    color={activityColors.title}
                    style={styles.summaryChevron}
                />
            </View>
            {visiblePreviewLines.length > 0 ? (
                <View style={styles.previewList}>
                    {visiblePreviewLines.map((line, index) => (
                        <Text
                            key={`${line}:${index}`}
                            style={[styles.previewLine, { color: activityColors.preview }]}
                            numberOfLines={1}
                            ellipsizeMode="tail"
                        >
                            {line}
                        </Text>
                    ))}
                </View>
            ) : null}
        </Pressable>
    );
}

export function SupervisorContextDivider({
    label,
    detail = "",
    phase,
}: SupervisorContextDividerProps) {
    const activityColors = useSupervisorActivityColors();
    const iconName = phase === "running" ? "progress-clock" : "archive-outline";

    return (
        <View style={styles.contextWrap}>
            <View style={[styles.contextLine, { backgroundColor: activityColors.line }]} />
            <View style={styles.contextLabelWrap}>
                <MaterialCommunityIcons name={iconName as never} size={14} color={activityColors.title} />
                <Text
                    style={[styles.contextLabel, { color: activityColors.title }]}
                    numberOfLines={1}
                    ellipsizeMode="tail"
                >
                    {label}
                </Text>
                {detail ? (
                    <Text
                        style={[styles.contextDetail, { color: activityColors.preview }]}
                        numberOfLines={1}
                        ellipsizeMode="tail"
                    >
                        {detail}
                    </Text>
                ) : null}
            </View>
            <View style={[styles.contextLine, { backgroundColor: activityColors.line }]} />
        </View>
    );
}

const styles = StyleSheet.create({
    summaryButton: {
        width: "100%",
        alignSelf: "stretch",
        gap: 5,
        paddingVertical: 1,
    },
    summaryHeader: {
        width: "100%",
        minHeight: 22,
        flexDirection: "row",
        alignItems: "center",
        gap: 7,
        minWidth: 0,
    },
    summaryIconBox: {
        width: 18,
        height: 18,
        borderRadius: 5,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    summaryTitle: {
        flex: 1,
        minWidth: 0,
        fontSize: 13,
        lineHeight: 18,
        fontWeight: "800",
        letterSpacing: 0,
    },
    summaryChevron: {
        opacity: 0.86,
    },
    previewList: {
        width: "100%",
        gap: 3,
        paddingLeft: 25,
        paddingRight: 4,
    },
    previewLine: {
        fontSize: 12,
        lineHeight: 17,
        fontWeight: "600",
        letterSpacing: 0,
    },
    contextWrap: {
        width: "100%",
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
        marginVertical: spacing.xs,
    },
    contextLine: {
        flex: 1,
        height: StyleSheet.hairlineWidth,
        opacity: 0.8,
    },
    contextLabelWrap: {
        maxWidth: "78%",
        minHeight: 22,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        gap: 5,
        borderRadius: radii.pill,
        paddingHorizontal: 2,
        minWidth: 0,
    },
    contextLabel: {
        flexShrink: 1,
        fontSize: 12,
        lineHeight: 17,
        fontWeight: "800",
        letterSpacing: 0,
    },
    contextDetail: {
        flexShrink: 1,
        fontSize: 11,
        lineHeight: 15,
        fontWeight: "600",
        letterSpacing: 0,
    },
});
