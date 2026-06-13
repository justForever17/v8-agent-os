import { memo, useEffect, useMemo, useState, type ComponentType } from "react";
import { Image, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import Svg, { Circle, Defs, Ellipse, G, LinearGradient, Path, Rect, Stop } from "react-native-svg";
import Animated, {
    Easing,
    useAnimatedStyle,
    useSharedValue,
    withDelay,
    withRepeat,
    withSequence,
    withSpring,
    withTiming,
    type SharedValue,
} from "react-native-reanimated";
import type {
    CollaborationMicroStage,
    CollaborationMicroStageCue,
    CollaborationMicroStageStep,
    CollaborationMicroStageStatus,
} from "@v8/session-realtime";

import { createTranslator } from "@/src/lib/locale";
import type { LocaleCode } from "@/src/providers/ui-prefs";
import type { ThemeColors } from "@/src/theme/tokens";

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

type SupervisorAction = "idle" | "walk" | "summon" | "command" | "read" | "type" | "receive" | "celebrate";

const SUPERVISOR_SHEET = {
    columns: 7,
    rows: 5,
    frameWidth: 68,
    frameHeight: 68,
};

const SUPERVISOR_ACTION_FRAMES: Record<SupervisorAction, number[]> = {
    idle: [0, 1, 2, 3],
    walk: [4, 5, 6, 7, 8, 9],
    summon: [10, 11, 12, 13],
    command: [14, 15, 16],
    read: [17, 18, 19, 20],
    type: [21, 22, 23, 24],
    receive: [25, 26],
    celebrate: [27, 30, 31, 32, 33, 34],
};

const STAGE_SPACING = 116;
const STAGE_START_X = 126;
const STAGE_HEIGHT = 156;

function statusTone(status: CollaborationMicroStageStatus, palette: ThemeColors) {
    if (status === "completed") return palette.success;
    if (status === "failed") return palette.danger;
    if (status === "degraded" || status === "attempted") return palette.warning;
    if (status === "pending") return palette.textSoft;
    return palette.accent;
}

function stageColor(stage: CollaborationMicroStage, index: number) {
    if (stage.kind === "subagent") return ["#8B5CF6", "#D946EF", "#A855F7"][index % 3];
    const runtimeId = (stage.runtimeId || "").toLowerCase();
    if (runtimeId.includes("research")) return "#0EA5E9";
    if (runtimeId.includes("engineering")) return "#14B8A6";
    if (runtimeId.includes("creative")) return "#EC4899";
    if (runtimeId.includes("computer") || runtimeId.includes("desktop")) return "#6366F1";
    if (runtimeId.includes("rpa")) return "#F97316";
    return "#38BDF8";
}

function cueToSupervisorAction(stages: CollaborationMicroStage[]): SupervisorAction {
    if (stages.some((stage) => stage.status === "failed" || stage.status === "degraded")) return "read";
    if (stages.some((stage) => stage.cue === "handoff" || stage.cue === "completed")) return "receive";
    if (stages.some((stage) => stage.status === "completed")) return "celebrate";
    if (stages.some((stage) => stage.kind === "subagent" && stage.status === "active")) return "summon";
    if (stages.some((stage) => stage.status === "active")) return "command";
    if (stages.some((stage) => stage.status === "pending")) return "walk";
    return "idle";
}

function cueLabel(cue: CollaborationMicroStageCue, t: ReturnType<typeof createTranslator>) {
    const keys: Record<CollaborationMicroStageCue, Parameters<ReturnType<typeof createTranslator>>[0]> = {
        summon: "src.components.chat.collaborationmicrostagescene.cue_summon",
        dispatch: "src.components.chat.collaborationmicrostagescene.cue_dispatch",
        child_agent: "src.components.chat.collaborationmicrostagescene.cue_child_agent",
        route: "src.components.chat.collaborationmicrostagescene.cue_route",
        research: "src.components.chat.collaborationmicrostagescene.cue_research",
        engineering: "src.components.chat.collaborationmicrostagescene.cue_engineering",
        creative: "src.components.chat.collaborationmicrostagescene.cue_creative",
        desktop: "src.components.chat.collaborationmicrostagescene.cue_desktop",
        rpa: "src.components.chat.collaborationmicrostagescene.cue_rpa",
        waiting: "src.components.chat.collaborationmicrostagescene.cue_waiting",
        handoff: "src.components.chat.collaborationmicrostagescene.cue_handoff",
        completed: "src.components.chat.collaborationmicrostagescene.cue_completed",
        degraded: "src.components.chat.collaborationmicrostagescene.cue_degraded",
        failed: "src.components.chat.collaborationmicrostagescene.cue_failed",
    };
    return t(keys[cue]);
}

function latestStep(stage: CollaborationMicroStage) {
    return stage.steps[stage.steps.length - 1];
}

function isHandoffStage(stage: CollaborationMicroStage) {
    return stage.cue === "handoff" || stage.cue === "completed" || stage.status === "completed" || stage.status === "degraded";
}

function SupervisorSprite({
    action,
    mirrored,
}: {
    action: SupervisorAction;
    mirrored: SharedValue<number>;
}) {
    const frames = SUPERVISOR_ACTION_FRAMES[action] || SUPERVISOR_ACTION_FRAMES.idle;
    const [frameIndex, setFrameIndex] = useState(0);

    useEffect(() => {
        setFrameIndex(0);
        const duration = action === "walk" ? 120 : action === "summon" ? 150 : 180;
        const timer = setInterval(() => {
            setFrameIndex((current) => (current + 1) % frames.length);
        }, duration);
        return () => clearInterval(timer);
    }, [action, frames.length]);

    const facingStyle = useAnimatedStyle(() => ({
        transform: [{ scaleX: mirrored.value }],
    }));

    const frame = frames[frameIndex] ?? 0;
    const column = frame % SUPERVISOR_SHEET.columns;
    const row = Math.floor(frame / SUPERVISOR_SHEET.columns);

    return (
        <Animated.View style={[styles.supervisorSpriteClip, facingStyle]}>
            <Image
                source={require("../../../../assets/images/supervisor_spritesheet.png")}
                style={[
                    styles.supervisorSpriteSheet,
                    {
                        left: -column * SUPERVISOR_SHEET.frameWidth,
                        top: -row * SUPERVISOR_SHEET.frameHeight,
                    },
                ]}
                resizeMode="stretch"
            />
        </Animated.View>
    );
}

function GroundShadow({ width = 54, opacity = 0.16 }: { width?: number; opacity?: number }) {
    return (
        <Svg width={width} height={10} viewBox={`0 0 ${width} 10`} style={styles.groundShadow}>
            <Ellipse cx={width / 2} cy={5} rx={width * 0.38} ry={3.2} fill="#020617" opacity={opacity} />
        </Svg>
    );
}

function MagicPortal({ color }: { color: string }) {
    const spin = useSharedValue(0);
    const glow = useSharedValue(0);

    useEffect(() => {
        spin.value = withRepeat(withTiming(1, { duration: 980, easing: Easing.linear }), -1, false);
        glow.value = withSequence(
            withTiming(1, { duration: 420, easing: Easing.out(Easing.cubic) }),
            withDelay(620, withTiming(0, { duration: 360, easing: Easing.in(Easing.cubic) })),
        );
    }, [glow, spin]);

    const animatedStyle = useAnimatedStyle(() => ({
        opacity: glow.value,
        transform: [
            { scale: 0.72 + glow.value * 0.34 },
            { rotate: `${spin.value * 360}deg` },
        ],
    }));

    return (
        <Animated.View style={[styles.portal, animatedStyle]} pointerEvents="none">
            <Svg width={78} height={42} viewBox="0 0 78 42">
                <Ellipse cx={39} cy={22} rx={31} ry={9} fill={`${color}22`} />
                <Ellipse cx={39} cy={22} rx={31} ry={9} fill="none" stroke={color} strokeWidth={1.6} strokeDasharray="6 5" />
                <Ellipse cx={39} cy={22} rx={18} ry={5.5} fill="none" stroke={color} strokeWidth={1} opacity={0.75} />
                <Path d="M39 11 L48 27 L30 27 Z" fill="none" stroke={color} strokeWidth={0.9} opacity={0.8} />
                <Circle cx={39} cy={22} r={2.2} fill={color} opacity={0.86} />
            </Svg>
        </Animated.View>
    );
}

function ScreenGlyph({ cue, color, status }: { cue: CollaborationMicroStageCue; color: string; status: CollaborationMicroStageStatus }) {
    if (status === "failed") {
        return (
            <G stroke="#F87171" strokeWidth={2.2} strokeLinecap="round">
                <Path d="M18 8 L30 20 M30 8 L18 20" />
                <Path d="M10 28 H38" opacity={0.45} />
                <Path d="M13 32 H35" opacity={0.24} />
            </G>
        );
    }
    if (status === "completed") {
        return (
            <G stroke="#34D399" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round">
                <Path d="M11 17 L19 25 L37 8" />
                <Path d="M10 30 H38" opacity={0.42} />
                <Circle cx={37} cy={29} r={2.2} fill="#34D399" opacity={0.5} />
            </G>
        );
    }
    if (status === "degraded") {
        return (
            <G stroke="#FBBF24" strokeWidth={2} strokeLinecap="round">
                <Path d="M24 7 L37 31 H11 Z" fill="#F59E0B22" />
                <Path d="M24 15 V23 M24 27 V27.4" />
            </G>
        );
    }
    if (cue === "waiting") {
        return (
            <G stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" fill="none">
                <Path d="M16 7 H32 M16 29 H32" />
                <Path d="M18 8 C18 15 30 15 30 22 C30 25 27 28 24 28 C21 28 18 25 18 22 C18 15 30 15 30 8" opacity={0.82} />
                <Circle cx={24} cy={21} r={1.8} fill={color} />
            </G>
        );
    }
    if (cue === "handoff") {
        return (
            <G stroke={color} strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" fill="none">
                <Rect x={12} y={8} width={18} height={22} rx={2} fill={`${color}16`} />
                <Path d="M16 15 H27 M16 20 H25 M16 25 H23" />
                <Path d="M32 14 L39 19 L32 24" />
                <Path d="M29 19 H39" />
            </G>
        );
    }
    if (cue === "research") {
        return (
            <G stroke={color} strokeWidth={1.8} strokeLinecap="round" fill="none">
                <Circle cx={20} cy={15} r={7} fill={`${color}12`} />
                <Path d="M25 20 L35 29" />
                <Circle cx={36} cy={11} r={2.4} fill={color} opacity={0.9} />
                <Circle cx={12} cy={28} r={2.1} fill={color} opacity={0.62} />
                <Path d="M12 32 H36" opacity={0.38} />
                <Path d="M34 12 L28 15 M15 27 L20 22" opacity={0.38} />
            </G>
        );
    }
    if (cue === "engineering") {
        return (
            <G stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" fill="none">
                <Rect x={8} y={8} width={32} height={22} rx={2} opacity={0.46} />
                <Path d="M17 14 L11 19 L17 24" />
                <Path d="M31 14 L37 19 L31 24" />
                <Path d="M25 12 L21 26" opacity={0.76} />
                <Path d="M12 32 H36" opacity={0.34} />
            </G>
        );
    }
    if (cue === "creative") {
        return (
            <G stroke={color} strokeWidth={1.9} strokeLinecap="round" fill="none">
                <Path d="M12 26 C17 10 31 10 36 26" fill={`${color}10`} />
                <Circle cx={17} cy={25} r={2.5} fill={color} />
                <Circle cx={25} cy={17} r={2.5} fill={color} opacity={0.78} />
                <Circle cx={33} cy={25} r={2.5} fill={color} opacity={0.62} />
                <Path d="M37 10 L39 13 L42 14 L39 16 L37 19 L35 16 L32 14 L35 13 Z" fill={color} opacity={0.72} />
            </G>
        );
    }
    if (cue === "child_agent" || cue === "dispatch" || cue === "summon") {
        return (
            <G stroke={color} strokeWidth={2} strokeLinecap="round" fill="none">
                <Circle cx={14} cy={14} r={4} fill={`${color}18`} />
                <Circle cx={34} cy={14} r={4} fill={`${color}18`} />
                <Circle cx={24} cy={25} r={4.4} fill={`${color}18`} />
                <Path d="M18 15 H30 M17 17 L22 23 M31 17 L26 23" opacity={0.78} />
                <Path d="M10 31 C16 28 32 28 38 31" opacity={0.38} />
            </G>
        );
    }
    if (cue === "desktop") {
        return (
            <G stroke={color} strokeWidth={1.9} strokeLinecap="round" fill="none">
                <Rect x={10} y={10} width={28} height={17} rx={2} />
                <Path d="M18 31 H30 M24 27 V31" />
                <Path d="M15 16 H33 M15 21 H26" opacity={0.5} />
                <Path d="M32 20 L38 25 L34 26 L36 31 L33 32 L31 27 L28 30 Z" fill={`${color}22`} />
            </G>
        );
    }
    if (cue === "rpa") {
        return (
            <G stroke={color} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" fill="none">
                <Rect x={9} y={8} width={9} height={7} rx={2} fill={`${color}16`} />
                <Rect x={30} y={8} width={9} height={7} rx={2} fill={`${color}16`} />
                <Rect x={18} y={23} width={12} height={8} rx={2} fill={`${color}16`} />
                <Path d="M18 11 H30 M24 15 V23" />
                <Path d="M14 20 L18 24 M34 20 L30 24" opacity={0.7} />
            </G>
        );
    }
    return (
        <G stroke={color} strokeWidth={2} strokeLinecap="round" fill="none">
            <Path d="M12 12 H28 L36 20 L28 28 H12" />
            <Path d="M28 12 V28" opacity={0.46} />
            <Circle cx={15} cy={20} r={2} fill={color} />
            <Circle cx={23} cy={20} r={2} fill={color} opacity={0.7} />
            <Circle cx={31} cy={20} r={2} fill={color} opacity={0.5} />
        </G>
    );
}

function Workstation({
    cue,
    color,
    status,
    active,
}: {
    cue: CollaborationMicroStageCue;
    color: string;
    status: CollaborationMicroStageStatus;
    active: boolean;
}) {
    const scan = useSharedValue(0);

    useEffect(() => {
        if (!active) {
            scan.value = withTiming(0, { duration: 160 });
            return;
        }
        scan.value = withRepeat(withTiming(1, { duration: 1250, easing: Easing.linear }), -1, false);
    }, [active, scan]);

    const screenLineStyle = useAnimatedStyle(() => ({
        opacity: active ? 0.35 + scan.value * 0.45 : 0.26,
        transform: [{ translateX: -16 + scan.value * 32 }],
    }));

    return (
        <View style={styles.workstation}>
            <Svg width={86} height={64} viewBox="0 0 86 64" style={StyleSheet.absoluteFill}>
                <Defs>
                    <LinearGradient id="deskTop" x1="0" y1="0" x2="1" y2="0">
                        <Stop offset="0" stopColor="#FFFFFF" />
                        <Stop offset="0.48" stopColor="#E2E8F0" />
                        <Stop offset="1" stopColor="#CBD5E1" />
                    </LinearGradient>
                    <LinearGradient id="monitorFrame" x1="0" y1="0" x2="0" y2="1">
                        <Stop offset="0" stopColor="#334155" />
                        <Stop offset="1" stopColor="#0F172A" />
                    </LinearGradient>
                    <LinearGradient id="deskCabinet" x1="0" y1="0" x2="0" y2="1">
                        <Stop offset="0" stopColor="#F8FAFC" />
                        <Stop offset="1" stopColor="#CBD5E1" />
                    </LinearGradient>
                </Defs>
                <Path d="M15 48 C18 41 30 40 33 48" fill="#E2E8F0" stroke="#CBD5E1" strokeWidth={0.8} opacity={0.86} />
                <Rect x={7} y={40} width={72} height={5} rx={2.5} fill="url(#deskTop)" stroke="#CBD5E1" strokeWidth={0.9} />
                <Path d="M13 45 L11 62 M43 45 L42 62 M71 45 L74 62" stroke="#CBD5E1" strokeWidth={1.6} strokeLinecap="round" />
                <Rect x={53} y={45} width={22} height={16} rx={2.4} fill="url(#deskCabinet)" stroke="#CBD5E1" strokeWidth={0.9} />
                <Path d="M57 50 H71 M57 55 H69" stroke="#94A3B8" strokeWidth={1.2} strokeLinecap="round" />
                <Rect x={18} y={45} width={23} height={4} rx={1.8} fill="#E2E8F0" stroke="#CBD5E1" strokeWidth={0.8} />
                <Circle cx={47} cy={42.5} r={2.2} fill={color} opacity={active ? 0.75 : 0.36} />
                <Path d="M38 40 L42 31 L46 40" stroke="#94A3B8" strokeWidth={2.2} strokeLinecap="round" fill="none" />
                <Rect x={12} y={4} width={62} height={35} rx={5} fill="url(#monitorFrame)" stroke="#64748B" strokeWidth={1.4} />
                <Rect x={15} y={7} width={56} height={29} rx={2.6} fill="#020617" />
                <Path d="M70 7 L49 36 H71 V7 Z" fill="#FFFFFF" opacity={0.07} />
                <Path d="M20 38 H46" stroke="#CBD5E1" strokeWidth={2.6} strokeLinecap="round" />
            </Svg>
            <View style={styles.screenSurface} pointerEvents="none">
                <Svg width={48} height={36} viewBox="0 0 48 36">
                    <Rect x={1} y={2} width={46} height={32} rx={3} fill={`${color}09`} />
                    <Path d="M4 7 H44" stroke={color} strokeWidth={0.8} opacity={0.22} />
                    <Path d="M4 29 H44" stroke={color} strokeWidth={0.8} opacity={0.16} />
                    <ScreenGlyph cue={cue} color={color} status={status} />
                </Svg>
                <Animated.View style={[styles.screenScanLine, { backgroundColor: color }, screenLineStyle]} />
            </View>
        </View>
    );
}

function RobotActor({
    color,
    active,
    status,
}: {
    color: string;
    active: boolean;
    status: CollaborationMicroStageStatus;
}) {
    const tread = useSharedValue(0);
    const bob = useSharedValue(0);

    useEffect(() => {
        if (active || status === "completed" || status === "degraded") {
            tread.value = withRepeat(withTiming(1, { duration: 520, easing: Easing.linear }), -1, false);
            bob.value = withRepeat(
                withSequence(
                    withTiming(1, { duration: 520, easing: Easing.inOut(Easing.ease) }),
                    withTiming(0, { duration: 520, easing: Easing.inOut(Easing.ease) }),
                ),
                -1,
                true,
            );
            return;
        }
        tread.value = withTiming(0, { duration: 160 });
        bob.value = withTiming(0, { duration: 160 });
    }, [active, bob, status, tread]);

    const actorStyle = useAnimatedStyle(() => ({
        transform: [{ translateY: -bob.value * 2 }],
    }));

    const treadStyle = useAnimatedStyle(() => ({
        transform: [{ translateX: -tread.value * 7 }],
    }));

    return (
        <Animated.View style={[styles.robotActor, actorStyle]}>
            <Svg width={38} height={46} viewBox="0 0 38 46">
                <Defs>
                    <LinearGradient id="robotBody" x1="0" y1="0" x2="1" y2="1">
                        <Stop offset="0" stopColor="#FFFFFF" />
                        <Stop offset="0.55" stopColor="#E2E8F0" />
                        <Stop offset="1" stopColor="#94A3B8" />
                    </LinearGradient>
                    <LinearGradient id="robotTrack" x1="0" y1="0" x2="0" y2="1">
                        <Stop offset="0" stopColor="#334155" />
                        <Stop offset="1" stopColor="#0F172A" />
                    </LinearGradient>
                </Defs>
                <Path d="M19 7 V2.5" stroke="#64748B" strokeWidth={1.5} strokeLinecap="round" />
                <Circle cx={19} cy={2.6} r={2.2} fill={color} />
                <Rect x={8} y={7} width={22} height={13} rx={5.5} fill="url(#robotBody)" stroke="#64748B" strokeWidth={1.2} />
                <Path d="M12 11 H26" stroke="#FFFFFF" strokeWidth={1.2} opacity={0.46} />
                <Circle cx={15} cy={14.2} r={2.1} fill="#0F172A" />
                <Circle cx={23} cy={14.2} r={2.1} fill="#0F172A" />
                <Circle cx={15.7} cy={13.4} r={0.7} fill="#FFFFFF" opacity={0.9} />
                <Circle cx={23.7} cy={13.4} r={0.7} fill="#FFFFFF" opacity={0.9} />
                <Rect x={14} y={17.5} width={10} height={1.5} rx={0.75} fill={color} opacity={0.8} />
                <Path d="M9 22 C9 19 12 18 19 18 C26 18 30 19 30 22 V33 C30 36 27 37 19 37 C11 37 8 36 8 33 Z" fill="url(#robotBody)" stroke="#64748B" strokeWidth={1.2} />
                <Rect x={12} y={23} width={14} height={7} rx={2.4} fill={color} opacity={0.22} />
                <Path d="M13 27 H25" stroke={color} strokeWidth={1.4} strokeLinecap="round" opacity={0.8} />
                <Path d="M8 25 L3 30 M30 25 L35 30" stroke="#94A3B8" strokeWidth={2} strokeLinecap="round" />
                <Circle cx={3} cy={30} r={2} fill={color} opacity={0.72} />
                <Circle cx={35} cy={30} r={2} fill={color} opacity={0.72} />
                <Rect x={4} y={35} width={30} height={8.5} rx={4.2} fill="url(#robotTrack)" stroke="#0F172A" strokeWidth={1} />
                <Circle cx={11} cy={39.2} r={2.2} fill="#CBD5E1" opacity={0.78} />
                <Circle cx={19} cy={39.2} r={2.2} fill="#CBD5E1" opacity={0.64} />
                <Circle cx={27} cy={39.2} r={2.2} fill="#CBD5E1" opacity={0.78} />
                <Path d="M7 35.6 H31" stroke="#64748B" strokeWidth={1.1} strokeLinecap="round" opacity={0.7} />
            </Svg>
            <Animated.View style={[styles.treadMarks, treadStyle]} pointerEvents="none">
                {Array.from({ length: 7 }).map((_, index) => (
                    <View key={index} style={styles.treadDot} />
                ))}
            </Animated.View>
        </Animated.View>
    );
}

function ReportScroll({ color }: { color: string }) {
    return (
        <Svg width={20} height={20} viewBox="0 0 20 20">
            <Rect x={3} y={3} width={14} height={14} rx={2} fill="#FFFFFF" stroke="#64748B" strokeWidth={1.2} />
            <Rect x={6} y={7} width={8} height={1.4} rx={0.7} fill={color} opacity={0.75} />
            <Rect x={6} y={10} width={6} height={1.4} rx={0.7} fill="#94A3B8" />
            <Rect x={6} y={13} width={7} height={1.4} rx={0.7} fill="#CBD5E1" />
        </Svg>
    );
}

function StatusBadge({
    status,
    color,
    palette,
    t,
}: {
    status: CollaborationMicroStageStatus;
    color: string;
    palette: ThemeColors;
    t: ReturnType<typeof createTranslator>;
}) {
    const keys: Record<CollaborationMicroStageStatus, Parameters<ReturnType<typeof createTranslator>>[0]> = {
        active: "src.components.chat.collaborationmicrostagescene.status_active",
        attempted: "src.components.chat.collaborationmicrostagescene.status_attempted",
        completed: "src.components.chat.collaborationmicrostagescene.status_completed",
        degraded: "src.components.chat.collaborationmicrostagescene.status_degraded",
        failed: "src.components.chat.collaborationmicrostagescene.status_failed",
        pending: "src.components.chat.collaborationmicrostagescene.status_pending",
    };
    return (
        <View style={[styles.statusBadge, { borderColor: `${color}55`, backgroundColor: `${color}18` }]}>
            <View style={[styles.statusDot, { backgroundColor: statusTone(status, palette) }]} />
            <Text style={[styles.statusText, { color: palette.text }]} numberOfLines={1}>
                {t(keys[status])}
            </Text>
        </View>
    );
}

type WorkCellProps = {
    stage: CollaborationMicroStage;
    index: number;
    palette: ThemeColors;
    dark: boolean;
    supervisorX: SharedValue<number>;
    onOpenDetailRef?: (target: CollaborationMicroStageDetailTarget) => void;
    t: ReturnType<typeof createTranslator>;
};

const WorkCell = memo(function WorkCell({
    stage,
    index,
    palette,
    dark,
    supervisorX,
    onOpenDetailRef,
    t,
}: WorkCellProps) {
    const color = stageColor(stage, index);
    const x = STAGE_START_X + index * STAGE_SPACING;
    const step = latestStep(stage);
    const cue = step?.cue || stage.cue;
    const active = stage.status === "active";
    const isHandoff = isHandoffStage(stage);
    const appeared = useSharedValue(0);
    const shake = useSharedValue(0);
    const walkBack = useSharedValue(0);
    const submit = useSharedValue(0);
    const warningPulse = useSharedValue(0);

    useEffect(() => {
        appeared.value = withSpring(1, { damping: 13, stiffness: 120 });
    }, [appeared]);

    useEffect(() => {
        if (active) {
            shake.value = withRepeat(
                withSequence(withTiming(-1, { duration: 76 }), withTiming(1, { duration: 76 })),
                -1,
                true,
            );
            return;
        }
        shake.value = withTiming(0, { duration: 140 });
    }, [active, shake]);

    useEffect(() => {
        if (stage.status === "failed" || stage.status === "degraded") {
            warningPulse.value = withRepeat(
                withSequence(withTiming(1, { duration: 420 }), withTiming(0, { duration: 420 })),
                -1,
                true,
            );
            return;
        }
        warningPulse.value = withTiming(0, { duration: 180 });
    }, [stage.status, warningPulse]);

    useEffect(() => {
        if (isHandoff) {
            walkBack.value = withDelay(260, withTiming(1, { duration: 1320, easing: Easing.out(Easing.cubic) }));
            submit.value = withDelay(1420, withTiming(1, { duration: 480, easing: Easing.out(Easing.cubic) }));
            return;
        }
        walkBack.value = withTiming(0, { duration: 160 });
        submit.value = withTiming(0, { duration: 160 });
    }, [isHandoff, submit, walkBack]);

    const cellStyle = useAnimatedStyle(() => ({
        opacity: appeared.value,
        transform: [{ scale: 0.92 + appeared.value * 0.08 }],
    }));

    const botStyle = useAnimatedStyle(() => {
        const target = supervisorX.value + 24 - x;
        return {
            opacity: appeared.value * (1 - submit.value * 0.82),
            transform: [
                { translateX: walkBack.value * target + (walkBack.value < 0.02 ? shake.value * 0.9 : 0) },
                { translateY: walkBack.value > 0 ? Math.sin(walkBack.value * Math.PI * 10) * 1.4 : 0 },
            ],
        };
    });

    const reportStyle = useAnimatedStyle(() => ({
        opacity: walkBack.value > 0.72 ? 1 - submit.value : 0,
        transform: [
            { translateX: walkBack.value * (supervisorX.value + 35 - x) },
            { translateY: -10 - submit.value * 18 },
        ],
    }));

    const badgeStyle = useAnimatedStyle(() => ({
        opacity: 0.9 + warningPulse.value * 0.1,
        transform: [{ scale: 1 + warningPulse.value * 0.035 }],
    }));

    const handlePress = () => {
        if (step?.detailRef && onOpenDetailRef) {
            onOpenDetailRef({ detailRef: step.detailRef, stage, step });
        }
    };

    return (
        <Animated.View style={[styles.workCell, { left: x }, cellStyle]}>
            {(active || stage.cue === "summon") && <MagicPortal color={color} />}
            <WorkbenchShadow />
            <Workstation cue={cue} color={color} status={stage.status} active={active} />
            <Animated.View style={[styles.robotLayer, botStyle]}>
                <GroundShadow width={38} opacity={0.18} />
                <RobotActor color={color} active={active || isHandoff} status={stage.status} />
            </Animated.View>
            {isHandoff && (
                <Animated.View style={[styles.reportLayer, reportStyle]} pointerEvents="none">
                    <ReportScroll color={color} />
                </Animated.View>
            )}
            <Animated.View style={[styles.stageBadgeWrap, badgeStyle]}>
                <Pressable
                    onPress={handlePress}
                    disabled={!step?.detailRef}
                    accessibilityRole={step?.detailRef ? "button" : undefined}
                    accessibilityLabel={step?.summary || stage.title}
                    style={[
                        styles.stageBadge,
                        {
                            backgroundColor: dark ? "rgba(15,23,42,0.82)" : "rgba(255,255,255,0.9)",
                            borderColor: `${color}66`,
                        },
                    ]}
                >
                    <Text style={[styles.stageBadgeTitle, { color: palette.text }]} numberOfLines={1}>
                        {cueLabel(cue, t)}
                    </Text>
                    <Text style={[styles.stageBadgeSummary, { color: palette.textMuted }]} numberOfLines={1}>
                        {step?.actorLabel || stage.title}
                    </Text>
                </Pressable>
            </Animated.View>
            <View style={styles.statusBadgeWrap}>
                <StatusBadge status={stage.status} color={color} palette={palette} t={t} />
            </View>
        </Animated.View>
    );
});

function WorkbenchShadow() {
    return (
        <Svg width={90} height={14} viewBox="0 0 90 14" style={styles.workbenchShadow}>
            <Ellipse cx={45} cy={8} rx={36} ry={4} fill="#020617" opacity={0.1} />
        </Svg>
    );
}

function StageFloor({ dark }: { dark: boolean }) {
    const line = dark ? "rgba(148,163,184,0.14)" : "rgba(100,116,139,0.14)";
    return (
        <Svg width="100%" height={34} viewBox="0 0 560 34" style={styles.floor}>
            <Path d="M0 20 H560" stroke={line} strokeWidth={1.2} />
            <Path d="M42 24 H118 M170 24 H246 M298 24 H374 M426 24 H502" stroke={line} strokeWidth={0.7} opacity={0.72} />
            <Path d="M76 18 V28 M206 18 V28 M336 18 V28 M466 18 V28" stroke={line} strokeWidth={0.7} opacity={0.38} />
        </Svg>
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
    const supervisorX = useSharedValue(26);
    const supervisorY = useSharedValue(0);
    const supervisorFacing = useSharedValue(1);

    const activeIndex = stages.findIndex((stage) => stage.status === "active");
    const action = useMemo(() => cueToSupervisorAction(stages), [stages]);

    useEffect(() => {
        if (activeIndex >= 0) {
            const target = STAGE_START_X + activeIndex * STAGE_SPACING - 58;
            supervisorFacing.value = target >= supervisorX.value ? 1 : -1;
            supervisorX.value = withTiming(target, { duration: 1360, easing: Easing.inOut(Easing.cubic) });
            supervisorY.value = withSequence(withTiming(-2, { duration: 180 }), withTiming(0, { duration: 220 }));
            return;
        }
        supervisorFacing.value = 1;
        supervisorX.value = withRepeat(
            withSequence(
                withTiming(36, { duration: 3000, easing: Easing.inOut(Easing.ease) }),
                withTiming(18, { duration: 3000, easing: Easing.inOut(Easing.ease) }),
            ),
            -1,
            true,
        );
    }, [activeIndex, supervisorFacing, supervisorX, supervisorY]);

    const supervisorStyle = useAnimatedStyle(() => ({
        transform: [
            { translateX: supervisorX.value },
            { translateY: supervisorY.value },
        ],
    }));

    if (stages.length === 0) {
        return null;
    }

    const scrollerWidth = Math.max(310, STAGE_START_X + stages.length * STAGE_SPACING + 26);

    return (
        <View style={styles.wrap}>
            <ScrollView
                horizontal
                showsHorizontalScrollIndicator={false}
                bounces
                contentContainerStyle={[styles.canvasScroller, { width: scrollerWidth }]}
                accessibilityLabel={t("src.components.chat.collaborationmicrostagescene.accessibility_label")}
            >
                <StageFloor dark={dark} />
                <Animated.View style={[styles.supervisorLayer, supervisorStyle]}>
                    <GroundShadow width={52} opacity={0.2} />
                    <SupervisorSprite action={action} mirrored={supervisorFacing} />
                </Animated.View>
                {stages.map((stage, index) => (
                    <WorkCell
                        key={stage.id}
                        stage={stage}
                        index={index}
                        palette={palette}
                        dark={dark}
                        supervisorX={supervisorX}
                        onOpenDetailRef={onOpenDetailRef}
                        t={t}
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
        height: STAGE_HEIGHT,
        marginVertical: 4,
        overflow: "hidden",
    },
    canvasScroller: {
        height: STAGE_HEIGHT,
        position: "relative",
    },
    floor: {
        position: "absolute",
        left: 0,
        right: 0,
        bottom: 0,
        zIndex: 0,
    },
    supervisorLayer: {
        position: "absolute",
        left: 0,
        bottom: 21,
        width: 68,
        height: 70,
        alignItems: "center",
        justifyContent: "flex-end",
        zIndex: 24,
    },
    supervisorSpriteClip: {
        width: SUPERVISOR_SHEET.frameWidth,
        height: SUPERVISOR_SHEET.frameHeight,
        overflow: "hidden",
        position: "relative",
    },
    supervisorSpriteSheet: {
        position: "absolute",
        width: SUPERVISOR_SHEET.frameWidth * SUPERVISOR_SHEET.columns,
        height: SUPERVISOR_SHEET.frameHeight * SUPERVISOR_SHEET.rows,
    },
    groundShadow: {
        position: "absolute",
        bottom: -2,
    },
    workCell: {
        position: "absolute",
        bottom: 19,
        width: 98,
        height: 104,
        alignItems: "center",
        zIndex: 10,
    },
    workstation: {
        position: "absolute",
        bottom: 8,
        width: 86,
        height: 64,
        zIndex: 3,
    },
    workbenchShadow: {
        position: "absolute",
        bottom: 6,
        zIndex: 1,
    },
    screenSurface: {
        position: "absolute",
        left: 19,
        top: 8,
        width: 48,
        height: 28,
        overflow: "hidden",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 4,
    },
    screenScanLine: {
        position: "absolute",
        top: 4,
        left: 0,
        width: 16,
        height: 22,
        borderRadius: 8,
        opacity: 0.3,
    },
    robotLayer: {
        position: "absolute",
        bottom: 6,
        left: 8,
        width: 42,
        height: 50,
        alignItems: "center",
        justifyContent: "flex-end",
        zIndex: 8,
    },
    robotActor: {
        width: 38,
        height: 46,
        alignItems: "center",
        overflow: "hidden",
    },
    treadMarks: {
        position: "absolute",
        bottom: 3,
        left: 7,
        width: 32,
        height: 2,
        flexDirection: "row",
        gap: 4,
    },
    treadDot: {
        width: 3,
        height: 2,
        borderRadius: 1,
        backgroundColor: "#94A3B8",
        opacity: 0.72,
    },
    portal: {
        position: "absolute",
        bottom: 0,
        left: 10,
        width: 78,
        height: 42,
        zIndex: 2,
    },
    reportLayer: {
        position: "absolute",
        left: 18,
        bottom: 49,
        width: 20,
        height: 20,
        zIndex: 16,
    },
    stageBadgeWrap: {
        position: "absolute",
        top: -25,
        left: 0,
        right: 0,
        alignItems: "center",
        zIndex: 30,
    },
    stageBadge: {
        minWidth: 74,
        maxWidth: 96,
        borderRadius: 10,
        borderWidth: 1,
        paddingHorizontal: 7,
        paddingVertical: 4,
        shadowColor: "#020617",
        shadowOpacity: 0.08,
        shadowRadius: 6,
        shadowOffset: { width: 0, height: 2 },
        elevation: 1,
    },
    stageBadgeTitle: {
        fontSize: 9,
        fontWeight: "900",
        lineHeight: 11,
    },
    stageBadgeSummary: {
        fontSize: 7.5,
        fontWeight: "700",
        lineHeight: 10,
    },
    statusBadgeWrap: {
        position: "absolute",
        bottom: -13,
        left: 7,
        right: 7,
        alignItems: "center",
        zIndex: 28,
    },
    statusBadge: {
        flexDirection: "row",
        alignItems: "center",
        gap: 4,
        paddingHorizontal: 6,
        paddingVertical: 2,
        borderRadius: 999,
        borderWidth: 1,
        maxWidth: 90,
    },
    statusDot: {
        width: 5,
        height: 5,
        borderRadius: 999,
    },
    statusText: {
        fontSize: 7.5,
        fontWeight: "800",
        lineHeight: 10,
    },
});
