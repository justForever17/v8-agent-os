import { useEffect, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
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
    stateLabel: string;
};

function toneForStatus(status: string, colors: ReturnType<typeof useUiPrefs>["colors"]): SlotTone {
    switch (status) {
        case "running":
            return {
                dot: colors.success,
                surface: "rgba(16,185,129,0.10)",
                border: "rgba(16,185,129,0.18)",
                stateLabel: "shared.runtime_status.running",
            };
        case "waiting_approval":
            return {
                dot: colors.warning,
                surface: "rgba(245,158,11,0.10)",
                border: "rgba(245,158,11,0.18)",
                stateLabel: "shared.runtime_status.waiting_approval",
            };
        case "waiting_input":
            return {
                dot: colors.warning,
                surface: "rgba(245,158,11,0.10)",
                border: "rgba(245,158,11,0.18)",
                stateLabel: "shared.runtime_status.waiting_input",
            };
        case "failed":
            return {
                dot: colors.danger,
                surface: "rgba(244,63,94,0.10)",
                border: "rgba(244,63,94,0.18)",
                stateLabel: "shared.runtime_status.failed",
            };
        case "cancelled":
            return {
                dot: colors.danger,
                surface: "rgba(244,63,94,0.10)",
                border: "rgba(244,63,94,0.18)",
                stateLabel: "shared.runtime_status.cancelled",
            };
        case "paused":
            return {
                dot: colors.danger,
                surface: "rgba(244,63,94,0.10)",
                border: "rgba(244,63,94,0.18)",
                stateLabel: "shared.runtime_status.paused",
            };
        case "queued":
            return {
                dot: colors.textSoft,
                surface: "rgba(120,113,108,0.10)",
                border: "rgba(120,113,108,0.16)",
                stateLabel: "shared.runtime_status.queued",
            };
        case "completed":
            return {
                dot: colors.textSoft,
                surface: "rgba(120,113,108,0.10)",
                border: "rgba(120,113,108,0.16)",
                stateLabel: "shared.runtime_status.completed",
            };
        default:
            return {
                dot: colors.textSoft,
                surface: "rgba(120,113,108,0.10)",
                border: "rgba(120,113,108,0.16)",
                stateLabel: "shared.runtime_status.idle",
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
    const [stateInfoOpen, setStateInfoOpen] = useState(false);
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
        ? t("src.components.chat.runcontrolbar.open_approvals")
        : stateMode === "danger"
            ? (canResume ? t("src.components.chat.runcontrolbar.resume_run") : t("src.components.chat.runcontrolbar.retry_run"))
            : stateMode === "success"
                ? t("src.components.chat.runcontrolbar.interrupt_run")
                : t("src.components.chat.runcontrolbar.no_action_available");
    const actionDisabled = !actionPress || busy || stateMode === "idle";
    const availableActions = [
        canOpenApproval ? t("src.components.chat.runcontrolbar.open_approvals") : "",
        canResume ? t("src.components.chat.runcontrolbar.resume_run") : "",
        canRetry ? t("src.components.chat.runcontrolbar.retry_run") : "",
        canInterrupt ? t("src.components.chat.runcontrolbar.interrupt_run") : "",
    ].filter(Boolean);
    const shortRunId = String(runId || "").trim().slice(0, 8);
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
            style={[styles.wrap, { backgroundColor: "transparent", borderColor: "transparent" }]}
            accessibilityLabel={runId ? `${t("src.components.chat.runcontrolbar.run_controls")} ${runId}` : t("src.components.chat.runcontrolbar.run_controls")}
        >
            <Pressable
                accessibilityRole="button"
                accessibilityLabel={t(tone.stateLabel)}
                onPress={() => setStateInfoOpen((current) => !current)}
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
            </Pressable>

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
            {stateInfoOpen ? (
                <View style={[styles.statePopover, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                    <Text style={[styles.statePopoverTitle, { color: colors.text }]}>{t(tone.stateLabel)}</Text>
                    <Text style={[styles.statePopoverText, { color: colors.textMuted }]}>
                        {shortRunId ? t("src.components.chat.runcontrolbar.run_short_id", { runId: shortRunId }) : t("src.components.chat.runcontrolbar.no_active_run")}
                    </Text>
                    <Text style={[styles.statePopoverText, { color: colors.textMuted }]}>
                        {t("src.components.chat.runcontrolbar.next_action", { action: actionLabel })}
                    </Text>
                    <Text style={[styles.statePopoverText, { color: colors.textMuted }]}>
                        {t("src.components.chat.runcontrolbar.available_actions", {
                            actions: availableActions.length ? availableActions.join(" / ") : t("src.components.chat.runcontrolbar.none"),
                        })}
                    </Text>
                </View>
            ) : null}
        </View>
    );
}

const styles = StyleSheet.create({
    wrap: {
        minHeight: 40,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 3,
        borderRadius: radii.pill,
        borderWidth: 0,
        paddingHorizontal: 0,
        paddingVertical: 2,
        width: 69,
        minWidth: 69,
        maxWidth: 69,
        flexShrink: 0,
    },
    iconSlot: {
        width: 33,
        height: 33,
        borderRadius: 17,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
        position: "relative",
    },
    stateLightOuter: {
        position: "absolute",
        width: 18,
        height: 18,
        borderRadius: 999,
        borderWidth: 1,
        opacity: 0.52,
    },
    stateMotionRing: {
        position: "absolute",
        width: 27,
        height: 27,
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
    statePopover: {
        position: "absolute",
        top: 43,
        left: -8,
        width: 190,
        borderWidth: 1,
        borderRadius: 14,
        paddingHorizontal: 12,
        paddingVertical: 10,
        gap: 4,
        shadowColor: "#0F172A",
        shadowOpacity: 0.14,
        shadowRadius: 16,
        shadowOffset: { width: 0, height: 8 },
        elevation: 8,
        zIndex: 20,
    },
    statePopoverTitle: {
        fontSize: 13,
        fontWeight: "800",
    },
    statePopoverText: {
        fontSize: 11,
        fontWeight: "600",
        lineHeight: 15,
    },
});
