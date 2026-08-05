import { memo } from "react";
import { StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import type { CreativeCanvasGraphRunHumanSurfaceProjection } from "@v8/session-realtime";

import { getPhoneCanvasGraphRunStateTranslationKey } from "@/src/lib/runtime-stage";
import { useUiPrefs } from "@/src/providers/ui-prefs";

function statusPresentation(
    projection: CreativeCanvasGraphRunHumanSurfaceProjection,
    colors: ReturnType<typeof useUiPrefs>["colors"],
) {
    if (projection.transition === "retry_failed_branch") {
        return { icon: "source-branch-sync" as const, color: colors.warning };
    }
    if (projection.transition === "recovered" || projection.status === "recovered") {
        return { icon: "backup-restore" as const, color: colors.success };
    }
    if (projection.status === "completed") {
        return { icon: "check-circle-outline" as const, color: colors.success };
    }
    if (projection.status === "failed" || projection.status === "interrupted") {
        return { icon: "alert-circle-outline" as const, color: colors.danger };
    }
    if (projection.status === "cancelling") {
        return { icon: "progress-clock" as const, color: colors.warning };
    }
    if (projection.status === "cancelled") {
        return { icon: "stop-circle-outline" as const, color: colors.textMuted };
    }
    if (projection.status === "queued") {
        return { icon: "clock-outline" as const, color: colors.textMuted };
    }
    return { icon: "play-circle-outline" as const, color: colors.primary };
}

export const CanvasGraphStatus = memo(function CanvasGraphStatus({
    projection,
}: {
    projection: CreativeCanvasGraphRunHumanSurfaceProjection;
}) {
    const { colors, t } = useUiPrefs();
    const presentation = statusPresentation(projection, colors);
    const label = t(getPhoneCanvasGraphRunStateTranslationKey(projection));

    return (
        <View
            accessible
            accessibilityLabel={label}
            accessibilityLiveRegion="polite"
            style={styles.root}
        >
            <MaterialCommunityIcons name={presentation.icon} size={16} color={presentation.color} />
            <Text style={[styles.label, { color: colors.textMuted }]}>{label}</Text>
        </View>
    );
});

const styles = StyleSheet.create({
    root: {
        minHeight: 28,
        flexDirection: "row",
        alignItems: "center",
        gap: 7,
        paddingHorizontal: 2,
    },
    label: {
        flex: 1,
        minWidth: 0,
        fontSize: 12,
        lineHeight: 17,
        fontWeight: "600",
    },
});
