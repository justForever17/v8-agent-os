import { memo } from "react";
import { StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import type { ContextReferenceItem } from "@v8/session-realtime";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

type ContextReferencesHUDProps = {
    contextReferences: ContextReferenceItem[];
};

export const ContextReferencesHUD = memo(function ContextReferencesHUD({ contextReferences }: ContextReferencesHUDProps) {
    const { colors, themeMode } = useUiPrefs();
    const visibleReferences = contextReferences.filter((ref) => ref.type !== "memory");

    if (visibleReferences.length === 0) {
        return null;
    }

    const iconName = (type: ContextReferenceItem["type"]) => {
        switch (type) {
            case "file":
                return { name: "file-code-outline" as const, color: "#3B82F6" };
            case "search":
                return { name: "magnify" as const, color: "#F59E0B" };
            case "memory":
                return { name: "brain" as const, color: "#8B5CF6" };
            case "web":
                return { name: "database-search-outline" as const, color: "#10B981" };
            default:
                return { name: "file-outline" as const, color: colors.textSoft };
        }
    };

    return (
        <View style={styles.wrap}>
            {visibleReferences.slice(-10).map((ref) => {
                const icon = iconName(ref.type);
                return (
                    <View
                        key={ref.id}
                        style={[
                            styles.chip,
                            {
                                backgroundColor: themeMode === "dark" ? "rgba(24,24,27,0.72)" : "rgba(255,255,255,0.92)",
                                borderColor: colors.border,
                                shadowColor: colors.text,
                            },
                        ]}
                    >
                        <MaterialCommunityIcons name={icon.name} size={14} color={icon.color} />
                        <Text style={[styles.label, { color: colors.textMuted }]} numberOfLines={1}>
                            {ref.label}
                        </Text>
                    </View>
                );
            })}
        </View>
    );
});

const styles = StyleSheet.create({
    wrap: {
        flexDirection: "row",
        flexWrap: "wrap",
        justifyContent: "center",
        gap: 8,
        width: "100%",
        marginBottom: spacing.md,
    },
    chip: {
        maxWidth: 180,
        minHeight: 31,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        borderRadius: radii.pill,
        borderWidth: 1,
        paddingHorizontal: 12,
        paddingVertical: 6,
        shadowOpacity: 0.05,
        shadowRadius: 10,
        shadowOffset: { width: 0, height: 4 },
        elevation: 2,
    },
    label: {
        flexShrink: 1,
        fontSize: 11,
        fontWeight: "500",
    },
});
