import { memo, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { Badge } from "@/src/components/ui/badge";
import { useUiPrefs } from "@/src/providers/ui-prefs";

type ApprovalTone = "approval" | "safety" | "control";

const TONE_STYLES: Record<
    ApprovalTone,
    {
        icon: string;
        lightBackground: string;
        darkBackground: string;
        lightBorder: string;
        darkBorder: string;
        lightText: string;
        darkText: string;
        lightIconBackground: string;
        darkIconBackground: string;
        lightIcon: string;
        darkIcon: string;
    }
> = {
    approval: {
        icon: "shield-alert-outline",
        lightBackground: "rgba(255, 251, 235, 0.92)",
        darkBackground: "rgba(245, 158, 11, 0.12)",
        lightBorder: "rgba(252, 211, 77, 0.68)",
        darkBorder: "rgba(245, 158, 11, 0.34)",
        lightText: "#78350F",
        darkText: "#FEF3C7",
        lightIconBackground: "rgba(245, 158, 11, 0.12)",
        darkIconBackground: "rgba(245, 158, 11, 0.16)",
        lightIcon: "#D97706",
        darkIcon: "#FCD34D",
    },
    safety: {
        icon: "shield-alert-outline",
        lightBackground: "rgba(255, 241, 242, 0.92)",
        darkBackground: "rgba(244, 63, 94, 0.12)",
        lightBorder: "rgba(253, 164, 175, 0.68)",
        darkBorder: "rgba(244, 63, 94, 0.34)",
        lightText: "#881337",
        darkText: "#FFE4E6",
        lightIconBackground: "rgba(244, 63, 94, 0.12)",
        darkIconBackground: "rgba(244, 63, 94, 0.16)",
        lightIcon: "#E11D48",
        darkIcon: "#FDA4AF",
    },
    control: {
        icon: "alert-outline",
        lightBackground: "rgba(248, 250, 252, 0.94)",
        darkBackground: "rgba(51, 65, 85, 0.42)",
        lightBorder: "rgba(203, 213, 225, 0.82)",
        darkBorder: "rgba(100, 116, 139, 0.5)",
        lightText: "#0F172A",
        darkText: "#F8FAFC",
        lightIconBackground: "rgba(100, 116, 139, 0.1)",
        darkIconBackground: "rgba(148, 163, 184, 0.14)",
        lightIcon: "#475569",
        darkIcon: "#CBD5E1",
    },
};

export const ApprovalCard = memo(function ApprovalCard({
    title,
    body,
    status,
    tone = "approval",
    eventSummary,
    compact = true,
}: {
    title: string;
    body: string;
    status?: string;
    tone?: ApprovalTone;
    eventSummary?: unknown;
    compact?: boolean;
}) {
    const { themeMode, t } = useUiPrefs();
    const [detailsExpanded, setDetailsExpanded] = useState(false);
    const accent = TONE_STYLES[tone];
    const isDark = themeMode === "dark";
    const displayStatus = typeof status === "string" && status.trim().toLowerCase() !== "unknown"
        ? status.trim()
        : "";
    const summary = eventSummary && typeof eventSummary === "object" && !Array.isArray(eventSummary)
        ? eventSummary as Record<string, unknown>
        : {};
    const summaryRows = ["operation", "target", "host", "providerId", "credentialClass", "riskCode", "matchedRule", "nextAction"]
        .map((key) => {
            const value = summary[key];
            return typeof value === "string" && value.trim() ? { key, value: value.trim() } : null;
        })
        .filter((item): item is { key: string; value: string } => Boolean(item));
    const visibleSummaryRows = compact && !detailsExpanded
        ? summaryRows.filter((row) => row.key === "operation" || row.key === "target")
        : summaryRows;
    const detailLabel = t(detailsExpanded
        ? "src.components.chat.approvalcard.collapse_details"
        : "src.components.chat.approvalcard.view_details");
    const hint = tone === "control"
        ? t("src.components.chat.approvalcard.this_is_a_runtime_control_state_rather_than_a_regular_tool_result")
        : tone === "safety"
            ? t("src.components.chat.approvalcard.this_is_a_safety_guardian_governance_node")
            : t("src.components.chat.approvalcard.this_is_a_run_node_waiting_for_human_review");

    return (
        <View
            style={[
                styles.card,
                compact && styles.compactCard,
                {
                    backgroundColor: isDark ? accent.darkBackground : accent.lightBackground,
                    borderColor: isDark ? accent.darkBorder : accent.lightBorder,
                },
            ]}
        >
            <View style={[styles.row, compact && styles.compactRow]}>
                <View
                    style={[
                        styles.iconWrap,
                        compact && styles.compactIconWrap,
                        {
                            backgroundColor: isDark ? accent.darkIconBackground : accent.lightIconBackground,
                        },
                    ]}
                >
                    <MaterialCommunityIcons
                        name={accent.icon as never}
                        size={18}
                        color={isDark ? accent.darkIcon : accent.lightIcon}
                    />
                </View>
                <View style={[styles.body, compact && styles.compactBody]}>
                    <Pressable
                        disabled={!compact}
                        onPress={() => setDetailsExpanded((value) => !value)}
                        accessibilityRole={compact ? "button" : undefined}
                        accessibilityLabel={compact ? `${title}: ${detailLabel}` : title}
                        aria-expanded={compact ? detailsExpanded : undefined}
                        hitSlop={compact ? 6 : undefined}
                        style={[styles.header, compact && styles.compactHeader]}
                    >
                        <Text numberOfLines={compact ? 1 : undefined} style={[styles.title, compact && styles.compactTitle, { color: isDark ? accent.darkText : accent.lightText }]}>{title}</Text>
                        {displayStatus ? <Badge variant="outline">{displayStatus}</Badge> : null}
                        {compact ? <MaterialCommunityIcons name={detailsExpanded ? "chevron-up" : "chevron-down"} size={18} color={isDark ? accent.darkIcon : accent.lightIcon} /> : null}
                    </Pressable>
                    <ScrollView
                        style={styles.detailScroll}
                        nestedScrollEnabled
                        scrollEnabled={!compact || detailsExpanded}
                        showsVerticalScrollIndicator={(!compact || detailsExpanded) && (body.length > 220 || summaryRows.length > 4)}
                    >
                        <Text
                            selectable
                            numberOfLines={compact && !detailsExpanded ? 2 : undefined}
                            style={[styles.copy, compact && styles.compactCopy, { color: isDark ? accent.darkText : accent.lightText }]}
                        >{body}</Text>
                        {visibleSummaryRows.length ? (
                            <View style={[styles.summaryBox, compact && styles.compactSummaryBox, { borderColor: isDark ? accent.darkBorder : accent.lightBorder }]}>
                                {visibleSummaryRows.map((row) => (
                                    <View key={row.key} style={styles.summaryRow}>
                                        <Text style={[styles.summaryKey, { color: isDark ? `${accent.darkText}B3` : `${accent.lightText}B3` }]}>{compact ? t(`src.components.chat.approvalcard.field.${row.key}`) : row.key}</Text>
                                        <Text selectable numberOfLines={compact && !detailsExpanded ? 1 : undefined} style={[styles.summaryValue, { color: isDark ? accent.darkText : accent.lightText }]}>{row.value}</Text>
                                    </View>
                                ))}
                            </View>
                        ) : null}
                    </ScrollView>
                    {!compact ? <Text selectable style={[styles.hint, { color: isDark ? `${accent.darkText}B3` : `${accent.lightText}B3` }]}>{hint}</Text> : null}
                </View>
            </View>
        </View>
    );
});

const styles = StyleSheet.create({
    card: {
        borderWidth: 1,
        borderRadius: 22,
        paddingHorizontal: 12,
        paddingVertical: 12,
        shadowColor: "#0F172A",
        shadowOpacity: 0.08,
        shadowRadius: 8,
        shadowOffset: { width: 0, height: 2 },
        elevation: 2,
    },
    compactCard: {
        borderRadius: 14,
        paddingHorizontal: 9,
        paddingVertical: 8,
        shadowOpacity: 0,
        elevation: 0,
    },
    row: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 12,
    },
    compactRow: {
        gap: 8,
    },
    iconWrap: {
        width: 36,
        height: 36,
        borderRadius: 16,
        alignItems: "center",
        justifyContent: "center",
    },
    compactIconWrap: {
        width: 28,
        height: 28,
        borderRadius: 11,
    },
    body: {
        flex: 1,
        gap: 8,
    },
    compactBody: {
        gap: 4,
    },
    header: {
        flexDirection: "row",
        alignItems: "center",
        flexWrap: "wrap",
        gap: 8,
    },
    compactHeader: {
        minHeight: 32,
        flexWrap: "nowrap",
        gap: 5,
    },
    title: {
        fontSize: 12,
        fontWeight: "700",
        letterSpacing: 0.2,
    },
    compactTitle: {
        flex: 1,
    },
    copy: {
        fontSize: 14,
        lineHeight: 22,
    },
    compactCopy: {
        fontSize: 12,
        lineHeight: 18,
    },
    detailScroll: {
        maxHeight: 190,
    },
    hint: {
        fontSize: 12,
        lineHeight: 18,
    },
    summaryBox: {
        borderWidth: 1,
        borderRadius: 14,
        paddingHorizontal: 10,
        paddingVertical: 8,
        gap: 6,
    },
    compactSummaryBox: {
        borderRadius: 10,
        paddingHorizontal: 7,
        paddingVertical: 5,
        gap: 3,
    },
    summaryRow: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 8,
    },
    summaryKey: {
        width: 104,
        fontSize: 11,
        fontWeight: "800",
    },
    summaryValue: {
        flex: 1,
        fontSize: 11,
        lineHeight: 16,
    },
});
