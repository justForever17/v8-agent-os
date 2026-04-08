import { useEffect } from "react";
import { Pressable, StyleSheet, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import Animated, {
    Easing,
    cancelAnimation,
    useAnimatedStyle,
    useSharedValue,
    withRepeat,
    withTiming,
} from "react-native-reanimated";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";

type SlotTone = {
    dot: string;
    surface: string;
    border: string;
    stateLabel: { zh: string; en: string };
};

function toneForStatus(status: string, colors: ReturnType<typeof useUiPrefs>["colors"]): SlotTone {
    switch (status) {
        case "running":
            return {
                dot: colors.success,
                surface: "rgba(16,185,129,0.10)",
                border: "rgba(16,185,129,0.18)",
                stateLabel: { zh: "运行中", en: "Running" },
            };
        case "waiting_approval":
            return {
                dot: colors.warning,
                surface: "rgba(245,158,11,0.10)",
                border: "rgba(245,158,11,0.18)",
                stateLabel: { zh: "等待审批", en: "Waiting approval" },
            };
        case "waiting_input":
            return {
                dot: colors.warning,
                surface: "rgba(245,158,11,0.10)",
                border: "rgba(245,158,11,0.18)",
                stateLabel: { zh: "等待输入", en: "Waiting input" },
            };
        case "failed":
        case "cancelled":
        case "paused":
            return {
                dot: colors.danger,
                surface: "rgba(244,63,94,0.10)",
                border: "rgba(244,63,94,0.18)",
                stateLabel: { zh: "失败", en: "Failed" },
            };
        case "queued":
        case "completed":
        default:
            return {
                dot: colors.textSoft,
                surface: "rgba(120,113,108,0.10)",
                border: "rgba(120,113,108,0.16)",
                stateLabel: { zh: "空闲", en: "Idle" },
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
    const showApprovalAction = Boolean((pendingApproval || normalizedStatus === "waiting_approval") && canOpenApproval && onOpenApproval);
    const showInterruptAction = Boolean(normalizedStatus === "running" && canInterrupt && onInterrupt);
    const showRetryAction = Boolean((canRetry || canResume || ["failed", "cancelled", "paused"].includes(normalizedStatus)) && onRetry);
    const stateMode: "warning" | "danger" | "success" | "idle" = showApprovalAction
        ? "warning"
        : showRetryAction
            ? "danger"
            : showInterruptAction
                ? "success"
                : "idle";
    const actionIcon: React.ComponentProps<typeof MaterialCommunityIcons>["name"] = stateMode === "warning"
        ? "check"
        : stateMode === "danger"
            ? (canResume ? "play" : "refresh")
            : stateMode === "success"
                ? "pause"
                : "minus";
    const actionColor = stateMode === "warning"
        ? "#B45309"
        : stateMode === "danger"
            ? colors.danger
            : stateMode === "success"
                ? colors.success
                : colors.textMuted;
    const actionSurface = stateMode === "warning"
        ? "rgba(245,158,11,0.10)"
        : stateMode === "danger"
            ? "rgba(244,63,94,0.10)"
            : stateMode === "success"
                ? "rgba(16,185,129,0.10)"
                : colors.surface;
    const actionBorder = stateMode === "warning"
        ? "rgba(245,158,11,0.24)"
        : stateMode === "danger"
            ? "rgba(244,63,94,0.22)"
            : stateMode === "success"
                ? "rgba(16,185,129,0.22)"
                : colors.border;
    const actionPress = stateMode === "warning"
        ? onOpenApproval
        : stateMode === "danger"
            ? onRetry
            : stateMode === "success"
                ? onInterrupt
                : undefined;
    const actionLabel = stateMode === "warning"
        ? t("打开审批", "Open approvals")
        : stateMode === "danger"
            ? (canResume ? t("恢复运行", "Resume run") : t("重试运行", "Retry run"))
            : stateMode === "success"
                ? t("中断运行", "Interrupt run")
                : t("当前无可执行动作", "No action available");
    const actionDisabled = !actionPress || busy || stateMode === "idle";
    const motion = useSharedValue(0);
    const highlightState = normalizedStatus === "running" || normalizedStatus === "waiting_approval";

    useEffect(() => {
        if (!highlightState) {
            cancelAnimation(motion);
            motion.value = withTiming(0, { duration: 160 });
            return;
        }
        motion.value = withRepeat(
            withTiming(1, { duration: 960, easing: Easing.inOut(Easing.ease) }),
            -1,
            true,
        );
        return () => {
            cancelAnimation(motion);
        };
    }, [highlightState, motion]);

    const stateMotionStyle = useAnimatedStyle(() => ({
        opacity: highlightState ? 0.18 + (motion.value * 0.38) : 0,
        transform: [{ scale: 0.94 + (motion.value * 0.2) }],
    }));

    return (
        <View
            style={[styles.wrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}
            accessibilityLabel={runId ? `${t("运行控制", "Run controls")} ${runId}` : t("运行控制", "Run controls")}
        >
            <View
                accessibilityRole="image"
                accessibilityLabel={t(tone.stateLabel.zh, tone.stateLabel.en)}
                style={[
                    styles.iconSlot,
                    {
                        backgroundColor: tone.surface,
                        borderColor: tone.border,
                    },
                ]}
            >
                <Animated.View
                    pointerEvents="none"
                    style={[styles.stateMotionRing, { borderColor: tone.dot }, stateMotionStyle]}
                />
                <View style={[styles.stateLightOuter, { borderColor: tone.border }]} />
                <View style={[styles.stateLight, { backgroundColor: tone.dot }]} />
            </View>

            <Pressable
                accessibilityRole="button"
                accessibilityLabel={actionLabel}
                disabled={actionDisabled}
                onPress={actionPress}
                style={[
                    styles.iconSlot,
                    { backgroundColor: actionSurface, borderColor: actionBorder },
                    !actionDisabled ? styles.slotActive : styles.slotInactive,
                    busy ? styles.disabled : null,
                ]}
            >
                {stateMode === "idle" ? null : (
                    <MaterialCommunityIcons
                        name={actionIcon}
                        size={15}
                        color={actionColor}
                    />
                )}
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
        gap: 6,
        borderRadius: radii.pill,
        borderWidth: 1,
        paddingHorizontal: 4,
        paddingVertical: 4,
        width: 72,
        minWidth: 72,
        maxWidth: 72,
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
    stateLightOuter: {
        position: "absolute",
        width: 16,
        height: 16,
        borderRadius: 999,
        borderWidth: 1,
        opacity: 0.52,
    },
    stateMotionRing: {
        position: "absolute",
        width: 23,
        height: 23,
        borderRadius: 999,
        borderWidth: 1,
    },
    stateLight: {
        width: 8,
        height: 8,
        borderRadius: 999,
    },
    slotActive: {
        opacity: 1,
    },
    slotInactive: {
        opacity: 0.92,
    },
    disabled: {
        opacity: 0.52,
    },
});
