import { memo, useEffect, type ComponentType } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import Animated, {
    Easing,
    cancelAnimation,
    useAnimatedStyle,
    useSharedValue,
    withRepeat,
    withSequence,
    withTiming,
} from "react-native-reanimated";
import type {
    CollaborationMicroStage,
    CollaborationMicroStageCue,
    CollaborationMicroStageStep,
    CollaborationMicroStageStatus,
} from "@v8/session-realtime";

import type { ThemeColors } from "@/src/theme/tokens";
import { radii, spacing } from "@/src/theme/tokens";
import type { LocaleCode } from "@/src/providers/ui-prefs";
import { createTranslator } from "@/src/lib/locale";

export type CollaborationMicroStageDetailTarget = {
    detailRef: string;
    stage: CollaborationMicroStage;
    step: CollaborationMicroStageStep;
};

export type CollaborationMicroStageRendererProps = {
    stages: CollaborationMicroStage[];
    palette: ThemeColors;
    dark: boolean;
    locale: LocaleCode;
    onOpenDetailRef?: (target: CollaborationMicroStageDetailTarget) => void;
};

type CollaborationMicroStageSceneProps = CollaborationMicroStageRendererProps & {
    renderer?: ComponentType<CollaborationMicroStageRendererProps>;
};

type MicroStageTranslator = ReturnType<typeof createTranslator>;

function statusLabel(status: CollaborationMicroStageStatus, t: MicroStageTranslator) {
    const keys: Record<CollaborationMicroStageStatus, Parameters<MicroStageTranslator>[0]> = {
        active: "src.components.chat.collaborationmicrostagescene.status_active",
        completed: "src.components.chat.collaborationmicrostagescene.status_completed",
        failed: "src.components.chat.collaborationmicrostagescene.status_failed",
        pending: "src.components.chat.collaborationmicrostagescene.status_pending",
        attempted: "src.components.chat.collaborationmicrostagescene.status_attempted",
        degraded: "src.components.chat.collaborationmicrostagescene.status_degraded",
    };
    return t(keys[status]);
}

function statusColor(status: CollaborationMicroStageStatus, palette: ThemeColors) {
    if (status === "completed") return palette.success;
    if (status === "failed") return palette.danger;
    if (status === "degraded" || status === "attempted") return palette.warning;
    if (status === "pending") return palette.textSoft;
    return palette.accent;
}

function cueTint(cue: CollaborationMicroStageCue, palette: ThemeColors) {
    if (cue === "research") return "#0EA5E9";
    if (cue === "engineering") return "#14B8A6";
    if (cue === "creative") return "#EC4899";
    if (cue === "desktop" || cue === "rpa") return "#6366F1";
    if (cue === "failed") return palette.danger;
    if (cue === "degraded") return palette.warning;
    if (cue === "handoff" || cue === "completed") return palette.success;
    return palette.primary;
}

function ActorSprite({
    label,
    tone,
    active,
    variant,
    onPress,
}: {
    label: string;
    tone: string;
    active: boolean;
    variant: "supervisor" | "worker" | "runtime";
    onPress?: () => void;
}) {
    const pulse = useSharedValue(0);

    useEffect(() => {
        if (!active) {
            cancelAnimation(pulse);
            pulse.value = withTiming(0, { duration: 180 });
            return;
        }
        pulse.value = withRepeat(
            withSequence(
                withTiming(1, { duration: 780, easing: Easing.inOut(Easing.cubic) }),
                withTiming(0, { duration: 780, easing: Easing.inOut(Easing.cubic) }),
            ),
            -1,
            false,
        );
        return () => cancelAnimation(pulse);
    }, [active, pulse]);

    const animatedStyle = useAnimatedStyle(() => ({
        transform: [
            { translateY: -2 * pulse.value },
            { scale: 1 + (pulse.value * 0.035) },
        ],
    }));

    const ringStyle = useAnimatedStyle(() => ({
        opacity: active ? 0.22 + (pulse.value * 0.38) : 0.14,
        transform: [{ scale: 0.88 + (pulse.value * 0.22) }],
    }));

    return (
        <Pressable
            style={styles.actorWrap}
            accessibilityLabel={label}
            accessibilityRole={onPress ? "button" : undefined}
            disabled={!onPress}
            onPress={onPress}
        >
            <Animated.View style={[styles.actorAura, { borderColor: tone }, ringStyle]} />
            <Animated.View style={[styles.actorBody, animatedStyle]}>
                <View style={[styles.actorHead, { backgroundColor: variant === "runtime" ? "#0F172A" : tone }]} />
                <View style={[
                    styles.actorTorso,
                    {
                        backgroundColor: variant === "supervisor" ? tone : `${tone}D9`,
                        borderColor: `${tone}55`,
                    },
                ]}>
                    <View style={styles.actorEyeRow}>
                        <View style={styles.actorEye} />
                        <View style={styles.actorEye} />
                    </View>
                </View>
                <View style={[styles.actorFoot, { backgroundColor: `${tone}88` }]} />
            </Animated.View>
            <Text style={styles.actorLabel} numberOfLines={1}>{label}</Text>
        </Pressable>
    );
}

function MagicLink({
    tone,
    active,
    subagent,
}: {
    tone: string;
    active: boolean;
    subagent: boolean;
}) {
    const glow = useSharedValue(0);

    useEffect(() => {
        if (!active) {
            cancelAnimation(glow);
            glow.value = withTiming(0, { duration: 160 });
            return;
        }
        glow.value = withRepeat(
            withSequence(
                withTiming(1, { duration: 620, easing: Easing.out(Easing.cubic) }),
                withTiming(0, { duration: 620, easing: Easing.in(Easing.cubic) }),
            ),
            -1,
            false,
        );
        return () => cancelAnimation(glow);
    }, [active, glow]);

    const sparkStyle = useAnimatedStyle(() => ({
        opacity: active ? 0.36 + (glow.value * 0.6) : 0.22,
        transform: [{ translateX: glow.value * 36 }],
    }));

    return (
        <View style={styles.magicLink}>
            <View style={[styles.magicRail, { backgroundColor: `${tone}30` }]} />
            <Animated.View style={[styles.magicSpark, { backgroundColor: tone, shadowColor: tone }, sparkStyle]} />
            {subagent ? (
                <View style={styles.magicGlyphs}>
                    <View style={[styles.magicGlyph, { borderColor: tone }]} />
                    <View style={[styles.magicGlyphSmall, { backgroundColor: tone }]} />
                    <View style={[styles.magicGlyph, { borderColor: tone }]} />
                </View>
            ) : null}
        </View>
    );
}

function StageCard({
    stage,
    palette,
    dark,
    t,
    onOpenDetailRef,
}: {
    stage: CollaborationMicroStage;
    palette: ThemeColors;
    dark: boolean;
    t: MicroStageTranslator;
    onOpenDetailRef?: (target: CollaborationMicroStageDetailTarget) => void;
}) {
    const tone = cueTint(stage.cue, palette);
    const stageStatusColor = statusColor(stage.status, palette);
    const active = stage.status === "active" || stage.status === "pending";
    const lastStep = stage.steps[stage.steps.length - 1];
    const workerLabel = lastStep?.actorLabel || (stage.kind === "subagent" ? "Subagent" : stage.title);
    const openStepDetail = (step?: CollaborationMicroStageStep) => {
        if (!step?.detailRef || !onOpenDetailRef) {
            return undefined;
        }
        return () => onOpenDetailRef({ detailRef: step.detailRef as string, stage, step });
    };

    return (
        <View style={[
            styles.stageCard,
            {
                backgroundColor: dark ? "rgba(15,23,42,0.52)" : "rgba(255,255,255,0.62)",
                shadowColor: tone,
            },
        ]}>
            <LinearGradient
                colors={dark ? [`${tone}26`, "rgba(15,23,42,0.05)"] : [`${tone}18`, "rgba(255,255,255,0.24)"]}
                start={{ x: 0, y: 0 }}
                end={{ x: 1, y: 1 }}
                style={styles.stageGlow}
            />
            <View style={styles.stageHeader}>
                <View style={styles.stageTitleWrap}>
                    <Text style={[styles.stageTitle, { color: palette.text }]} numberOfLines={1}>
                        {stage.title}
                    </Text>
                    <Text style={[styles.stageSubtitle, { color: palette.textMuted }]} numberOfLines={1}>
                        {stage.subtitle}
                    </Text>
                </View>
                <View style={[styles.statusPill, { backgroundColor: `${stageStatusColor}18` }]}>
                    <View style={[styles.statusDot, { backgroundColor: stageStatusColor }]} />
                    <Text style={[styles.statusText, { color: stageStatusColor }]} numberOfLines={1}>
                        {statusLabel(stage.status, t)}
                    </Text>
                </View>
            </View>

            <View style={styles.sceneCanvas}>
                <ActorSprite
                    label={t("src.components.chat.collaborationmicrostagescene.supervisor_actor")}
                    tone={palette.primary}
                    active={active && stage.kind === "subagent"}
                    variant="supervisor"
                    onPress={openStepDetail(lastStep)}
                />
                <MagicLink tone={tone} active={active} subagent={stage.kind === "subagent"} />
                <ActorSprite
                    label={workerLabel}
                    tone={tone}
                    active={active}
                    variant={stage.kind === "runtime" ? "runtime" : "worker"}
                    onPress={openStepDetail(lastStep)}
                />
            </View>

            <View style={styles.stepList}>
                {stage.steps.map((step, index) => {
                    const handleOpenDetail = openStepDetail(step);
                    const rowContent = (
                        <>
                            <View style={[styles.stepDot, { backgroundColor: statusColor(step.status, palette) }]} />
                            <Text style={[styles.stepLabel, { color: palette.text }]} numberOfLines={1}>
                                {step.label}
                            </Text>
                            {step.summary ? (
                                <Text style={[styles.stepSummary, { color: palette.textMuted }]} numberOfLines={1}>
                                    {step.summary}
                                </Text>
                            ) : null}
                            {index === stage.steps.length - 1 && step.detailRef ? (
                                <Text style={[styles.stepRef, { color: palette.textSoft }]} numberOfLines={1}>
                                    ref
                                </Text>
                            ) : null}
                        </>
                    );
                    return handleOpenDetail ? (
                        <Pressable
                            key={step.id}
                            style={({ pressed }) => [styles.stepRow, pressed && styles.stepRowPressed]}
                            accessibilityRole="button"
                            onPress={handleOpenDetail}
                        >
                            {rowContent}
                        </Pressable>
                    ) : (
                        <View key={step.id} style={styles.stepRow}>
                            {rowContent}
                        </View>
                    );
                })}
            </View>
        </View>
    );
}

export const CollaborationMicroStageLightRenderer = memo(function CollaborationMicroStageLightRenderer({
    stages,
    palette,
    dark,
    locale,
    onOpenDetailRef,
}: CollaborationMicroStageRendererProps) {
    const t = createTranslator(locale);

    if (stages.length === 0) {
        return null;
    }

    return (
        <View style={styles.wrap}>
            <ScrollView
                horizontal
                showsHorizontalScrollIndicator={false}
                bounces
                contentContainerStyle={styles.canvasScroller}
                accessibilityLabel={t("src.components.chat.collaborationmicrostagescene.accessibility_label")}
            >
                {stages.map((stage) => (
                    <StageCard
                        key={stage.id}
                        stage={stage}
                        palette={palette}
                        dark={dark}
                        t={t}
                        onOpenDetailRef={onOpenDetailRef}
                    />
                ))}
            </ScrollView>
        </View>
    );
});

export const CollaborationMicroStageScene = memo(function CollaborationMicroStageScene({
    renderer: Renderer = CollaborationMicroStageLightRenderer,
    ...props
}: CollaborationMicroStageSceneProps) {
    return <Renderer {...props} />;
});

const styles = StyleSheet.create({
    wrap: {
        width: "100%",
        marginBottom: 2,
    },
    canvasScroller: {
        gap: spacing.sm,
        paddingRight: spacing.sm,
    },
    stageCard: {
        width: 292,
        minHeight: 188,
        borderRadius: radii.lg,
        padding: 12,
        overflow: "hidden",
        shadowOpacity: 0.12,
        shadowRadius: 18,
        shadowOffset: { width: 0, height: 10 },
        elevation: 2,
    },
    stageGlow: {
        ...StyleSheet.absoluteFillObject,
    },
    stageHeader: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: 8,
        minWidth: 0,
    },
    stageTitleWrap: {
        flex: 1,
        minWidth: 0,
        gap: 2,
    },
    stageTitle: {
        fontSize: 13,
        lineHeight: 18,
        fontWeight: "900",
        letterSpacing: 0,
    },
    stageSubtitle: {
        fontSize: 10,
        lineHeight: 14,
        fontWeight: "700",
        letterSpacing: 0,
    },
    statusPill: {
        minHeight: 22,
        maxWidth: 72,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        gap: 5,
        borderRadius: radii.pill,
        paddingHorizontal: 8,
    },
    statusDot: {
        width: 6,
        height: 6,
        borderRadius: 999,
    },
    statusText: {
        fontSize: 10,
        lineHeight: 13,
        fontWeight: "900",
        letterSpacing: 0,
    },
    sceneCanvas: {
        minHeight: 76,
        marginTop: 10,
        marginBottom: 9,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
    },
    actorWrap: {
        width: 72,
        alignItems: "center",
        justifyContent: "center",
        gap: 5,
    },
    actorAura: {
        position: "absolute",
        top: 5,
        width: 48,
        height: 48,
        borderRadius: 999,
        borderWidth: 1,
    },
    actorBody: {
        width: 44,
        height: 48,
        alignItems: "center",
    },
    actorHead: {
        width: 19,
        height: 13,
        borderTopLeftRadius: 4,
        borderTopRightRadius: 4,
        borderBottomLeftRadius: 2,
        borderBottomRightRadius: 2,
    },
    actorTorso: {
        width: 34,
        height: 28,
        borderRadius: 7,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    actorEyeRow: {
        flexDirection: "row",
        gap: 5,
    },
    actorEye: {
        width: 5,
        height: 5,
        borderRadius: 999,
        backgroundColor: "#FFFFFF",
    },
    actorFoot: {
        width: 28,
        height: 5,
        borderBottomLeftRadius: 4,
        borderBottomRightRadius: 4,
    },
    actorLabel: {
        width: "100%",
        color: "rgba(100,116,139,0.92)",
        textAlign: "center",
        fontSize: 9,
        lineHeight: 12,
        fontWeight: "800",
        letterSpacing: 0,
    },
    magicLink: {
        flex: 1,
        height: 54,
        minWidth: 80,
        alignItems: "center",
        justifyContent: "center",
    },
    magicRail: {
        position: "absolute",
        left: 4,
        right: 4,
        height: 3,
        borderRadius: 999,
    },
    magicSpark: {
        position: "absolute",
        left: 4,
        width: 12,
        height: 12,
        borderRadius: 999,
        shadowOpacity: 0.42,
        shadowRadius: 10,
        shadowOffset: { width: 0, height: 2 },
    },
    magicGlyphs: {
        flexDirection: "row",
        alignItems: "center",
        gap: 7,
        opacity: 0.86,
    },
    magicGlyph: {
        width: 12,
        height: 12,
        borderRadius: 3,
        borderWidth: 1.5,
        transform: [{ rotate: "45deg" }],
    },
    magicGlyphSmall: {
        width: 5,
        height: 5,
        borderRadius: 999,
    },
    stepList: {
        gap: 6,
    },
    stepRow: {
        minHeight: 22,
        flexDirection: "row",
        alignItems: "center",
        gap: 7,
        minWidth: 0,
    },
    stepRowPressed: {
        opacity: 0.72,
    },
    stepDot: {
        width: 6,
        height: 6,
        borderRadius: 999,
    },
    stepLabel: {
        fontSize: 10,
        lineHeight: 14,
        fontWeight: "900",
        letterSpacing: 0,
    },
    stepSummary: {
        flex: 1,
        minWidth: 0,
        fontSize: 10,
        lineHeight: 14,
        fontWeight: "700",
        letterSpacing: 0,
    },
    stepRef: {
        fontSize: 9,
        lineHeight: 12,
        fontWeight: "900",
        letterSpacing: 0,
    },
});
