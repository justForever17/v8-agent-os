import { Pressable, StyleSheet, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";

type SlotTone = {
    dot: string;
    icon: React.ComponentProps<typeof MaterialCommunityIcons>["name"];
    iconColor: string;
    surface: string;
    border: string;
    label: { zh: string; en: string };
};

function toneForStatus(status: string, colors: ReturnType<typeof useUiPrefs>["colors"]): SlotTone {
    switch (status) {
        case "queued":
            return {
                dot: "#0EA5E9",
                icon: "progress-clock",
                iconColor: "#0284C7",
                surface: "rgba(14,165,233,0.10)",
                border: "rgba(14,165,233,0.18)",
                label: { zh: "排队中", en: "Queued" },
            };
        case "running":
            return {
                dot: colors.success,
                icon: "pulse",
                iconColor: "#047857",
                surface: "rgba(16,185,129,0.10)",
                border: "rgba(16,185,129,0.18)",
                label: { zh: "执行中", en: "Running" },
            };
        case "waiting_approval":
        case "waiting_input":
            return {
                dot: colors.warning,
                icon: "alert-circle-outline",
                iconColor: "#B45309",
                surface: "rgba(245,158,11,0.10)",
                border: "rgba(245,158,11,0.18)",
                label: { zh: "等待审批", en: "Waiting approval" },
            };
        case "failed":
        case "cancelled":
            return {
                dot: colors.danger,
                icon: "alert-octagon-outline",
                iconColor: "#BE123C",
                surface: "rgba(244,63,94,0.10)",
                border: "rgba(244,63,94,0.18)",
                label: { zh: "失败", en: "Failed" },
            };
        case "paused":
        case "completed":
        default:
            return {
                dot: colors.textSoft,
                icon: "check-circle-outline",
                iconColor: colors.textMuted,
                surface: "rgba(120,113,108,0.10)",
                border: "rgba(120,113,108,0.16)",
                label: { zh: "空闲", en: "Idle" },
            };
    }
}

export function RunControlBar({
    runId,
    status,
    pendingApproval,
    canOpenApproval,
    canResume,
    canRetry,
    canInterrupt,
    busy = false,
    onOpenApproval,
    onRetry,
    onInterrupt,
}: {
    runId?: string;
    status?: string;
    pendingApproval?: boolean;
    canOpenApproval?: boolean;
    canResume?: boolean;
    canRetry?: boolean;
    canInterrupt?: boolean;
    busy?: boolean;
    onOpenApproval?: () => void;
    onRetry?: () => void;
    onInterrupt?: () => void;
}) {
    const { colors, t } = useUiPrefs();
    const normalizedStatus = String(status || "completed");
    const tone = toneForStatus(normalizedStatus, colors);
    const showApprovalAction = Boolean(pendingApproval && canOpenApproval && onOpenApproval);
    const showInterruptAction = Boolean(normalizedStatus === "running" && canInterrupt && onInterrupt);
    const showRetryAction = Boolean(
        ((["paused", "failed", "cancelled", "waiting_input"].includes(normalizedStatus)) || canResume)
        && (canRetry || canResume)
        && onRetry,
    );
    const mutedIconColor = colors.textMuted;

    return (
        <View
            style={[styles.wrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}
            accessibilityLabel={runId ? `${t("运行控制", "Run controls")} ${runId}` : t("运行控制", "Run controls")}
        >
            <Pressable
                disabled
                accessibilityRole="button"
                accessibilityLabel={t(tone.label.zh, tone.label.en)}
                style={[
                    styles.iconSlot,
                    {
                        backgroundColor: tone.surface,
                        borderColor: tone.border,
                    },
                ]}
            >
                <MaterialCommunityIcons name={tone.icon} size={14} color={tone.iconColor} />
                <View style={[styles.dotBadge, { backgroundColor: tone.dot }]} />
            </Pressable>

            <Pressable
                accessibilityRole="button"
                accessibilityLabel={t("打开审批", "Open approvals")}
                disabled={!showApprovalAction}
                onPress={onOpenApproval}
                style={[
                    styles.iconSlot,
                    { backgroundColor: colors.surface, borderColor: colors.border },
                    showApprovalAction ? styles.slotActive : styles.slotInactive,
                ]}
            >
                <MaterialCommunityIcons
                    name="check-circle-outline"
                    size={14}
                    color={showApprovalAction ? "#B45309" : mutedIconColor}
                />
                {showApprovalAction ? <View style={[styles.dotBadge, { backgroundColor: colors.warning }]} /> : null}
            </Pressable>

            <Pressable
                accessibilityRole="button"
                accessibilityLabel={canResume ? t("恢复运行", "Resume run") : t("重试运行", "Retry run")}
                disabled={!showRetryAction || busy}
                onPress={onRetry}
                style={[
                    styles.iconSlot,
                    { backgroundColor: colors.surface, borderColor: colors.border },
                    showRetryAction ? styles.slotActive : styles.slotInactive,
                    busy ? styles.disabled : null,
                ]}
            >
                <MaterialCommunityIcons
                    name={canResume ? "play-circle-outline" : "refresh"}
                    size={14}
                    color={showRetryAction ? colors.danger : mutedIconColor}
                />
                {showRetryAction ? <View style={[styles.dotBadge, { backgroundColor: colors.danger }]} /> : null}
            </Pressable>

            <Pressable
                accessibilityRole="button"
                accessibilityLabel={t("中断运行", "Interrupt run")}
                disabled={!showInterruptAction || busy}
                onPress={onInterrupt}
                style={[
                    styles.iconSlot,
                    { backgroundColor: colors.surface, borderColor: colors.border },
                    showInterruptAction ? styles.slotActive : styles.slotInactive,
                    busy ? styles.disabled : null,
                ]}
            >
                <MaterialCommunityIcons
                    name="pause-circle-outline"
                    size={14}
                    color={showInterruptAction ? colors.success : mutedIconColor}
                />
            </Pressable>
        </View>
    );
}

const styles = StyleSheet.create({
    wrap: {
        minHeight: 32,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 4,
        borderRadius: radii.pill,
        borderWidth: 1,
        paddingHorizontal: 4,
        paddingVertical: 4,
        width: 136,
        minWidth: 136,
        maxWidth: 136,
        flexShrink: 0,
    },
    iconSlot: {
        width: 28,
        height: 28,
        borderRadius: 14,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
        position: "relative",
    },
    dotBadge: {
        position: "absolute",
        top: 3,
        right: 3,
        width: 6,
        height: 6,
        borderRadius: 999,
    },
    slotActive: {
        opacity: 1,
    },
    slotInactive: {
        opacity: 0.76,
    },
    disabled: {
        opacity: 0.52,
    },
});
