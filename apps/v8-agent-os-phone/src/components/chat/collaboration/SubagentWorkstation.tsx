import { memo, useEffect, useState } from "react";
import { Image, StyleSheet, View } from "react-native";
import Svg, { Circle, Path, Rect } from "react-native-svg";
import Animated, {
    cancelAnimation,
    Easing,
    useAnimatedStyle,
    useSharedValue,
    withRepeat,
    withTiming,
} from "react-native-reanimated";
import type {
    CollaborationMicroStageCue,
    CollaborationMicroStageStatus,
} from "@v8/session-realtime";
import { nextMotionFrameIndex } from "@/src/lib/motion-policy";

export type SubagentRobotPhase =
    | "entering"
    | "working"
    | "handoff"
    | "celebrating"
    | "warning"
    | "exiting";

export type SubagentRobotAction =
    | "idle"
    | "walk"
    | "boot"
    | "work"
    | "wait"
    | "handoff"
    | "success"
    | "failure"
    | "curtain";

const ROBOT_SHEET = {
    columns: 7,
    rows: 6,
    frameSize: 64,
};

const ROBOT_ACTION_FRAMES: Record<SubagentRobotAction, readonly number[]> = {
    idle: [0, 1, 2, 3],
    walk: [4, 5, 6, 7, 8, 9],
    boot: [10, 11, 12, 13],
    work: [14, 15, 16, 17],
    wait: [18, 19, 20, 21],
    handoff: [22, 23, 24, 25],
    success: [26, 27, 28, 29, 30, 31],
    failure: [32, 33, 34],
    curtain: [35, 36, 37, 38],
};

const ROBOT_ACTION_DURATIONS: Record<SubagentRobotAction, readonly number[]> = {
    idle: [520, 520, 260, 520],
    walk: [115, 115, 115, 115, 115, 115],
    boot: [220, 240, 320, 480],
    work: [360, 320, 360, 760],
    wait: [560, 520, 560, 720],
    handoff: [280, 340, 420, 680],
    success: [260, 260, 300, 620, 820, 920],
    failure: [380, 420, 760],
    curtain: [420, 480, 560, 420],
};

const LOOPING_ROBOT_ACTIONS = new Set<SubagentRobotAction>([
    "idle",
    "walk",
    "work",
    "wait",
    "success",
]);

type ScreenPattern =
    | "network"
    | "route"
    | "research"
    | "engineering"
    | "creative"
    | "desktop"
    | "rpa"
    | "waiting"
    | "completed"
    | "degraded"
    | "failed";

export function subagentRobotActionFor({
    cue,
    status,
    phase,
}: {
    cue: CollaborationMicroStageCue;
    status: CollaborationMicroStageStatus;
    phase: SubagentRobotPhase;
}): SubagentRobotAction {
    if (phase === "warning" || status === "failed" || status === "degraded") return "failure";
    if (phase === "handoff" || cue === "handoff") return "handoff";
    if (phase === "celebrating" || status === "completed") return "curtain";
    if (phase === "entering" || cue === "summon") return "boot";
    if (status === "pending" || status === "attempted" || cue === "waiting") return "wait";
    if (status === "active") return "work";
    return "idle";
}

function useSubagentRobotFrame(
    action: SubagentRobotAction,
    motionEnabled: boolean,
    continuousMotionEnabled: boolean,
) {
    const frames = ROBOT_ACTION_FRAMES[action];
    const durations = ROBOT_ACTION_DURATIONS[action];
    const loops = LOOPING_ROBOT_ACTIONS.has(action);
    const [frameState, setFrameState] = useState<{ action: SubagentRobotAction; index: number }>({
        action,
        index: 0,
    });
    const frameIndex = frameState.action === action ? frameState.index : 0;

    useEffect(() => {
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout> | undefined;
        let current = 0;
        if (!motionEnabled || (loops && !continuousMotionEnabled) || frames.length <= 1) return undefined;

        const scheduleNext = () => {
            timer = setTimeout(() => {
                if (cancelled) return;
                const next = nextMotionFrameIndex(current, frames.length, loops);
                if (next === null) return;
                current = next;
                setFrameState({ action, index: current });
                scheduleNext();
            }, durations[Math.min(current, durations.length - 1)] || 240);
        };
        scheduleNext();
        return () => {
            cancelled = true;
            if (timer) clearTimeout(timer);
        };
    }, [action, continuousMotionEnabled, durations, frames, loops, motionEnabled]);

    return frames[frameIndex] ?? frames[0] ?? 0;
}

export const SubagentRobotSprite = memo(function SubagentRobotSprite({
    action,
    color,
    motionEnabled = true,
    continuousMotionEnabled = true,
}: {
    action: SubagentRobotAction;
    color: string;
    motionEnabled?: boolean;
    continuousMotionEnabled?: boolean;
}) {
    const frame = useSubagentRobotFrame(action, motionEnabled, continuousMotionEnabled);
    const column = frame % ROBOT_SHEET.columns;
    const row = Math.floor(frame / ROBOT_SHEET.columns);
    const sheetWidth = ROBOT_SHEET.frameSize * ROBOT_SHEET.columns;
    const sheetHeight = ROBOT_SHEET.frameSize * ROBOT_SHEET.rows;
    const sheetPosition = {
        left: -column * ROBOT_SHEET.frameSize,
        top: -row * ROBOT_SHEET.frameSize,
        width: sheetWidth,
        height: sheetHeight,
    };

    return (
        <View style={styles.robotClip} accessibilityLabel={`subagent:${action}`}>
            <Image
                source={require("../../../../assets/images/subagent_robot_neutral.png")}
                style={[styles.robotSheet, sheetPosition]}
                resizeMode="stretch"
                fadeDuration={0}
            />
            <Image
                source={require("../../../../assets/images/subagent_robot_emissive_mask.png")}
                style={[styles.robotSheet, sheetPosition, { tintColor: color }]}
                resizeMode="stretch"
                fadeDuration={0}
            />
        </View>
    );
});

function screenPatternFor(
    cue: CollaborationMicroStageCue,
    status: CollaborationMicroStageStatus,
    phase: SubagentRobotPhase,
): ScreenPattern {
    if (status === "failed" || cue === "failed") return "failed";
    if (status === "degraded" || cue === "degraded") return "degraded";
    if (phase === "handoff" || cue === "handoff") return "route";
    if (phase === "celebrating" || status === "completed" || cue === "completed") return "completed";
    if (status === "pending" || status === "attempted" || cue === "waiting") return "waiting";
    if (cue === "research") return "research";
    if (cue === "engineering") return "engineering";
    if (cue === "creative") return "creative";
    if (cue === "desktop") return "desktop";
    if (cue === "rpa") return "rpa";
    if (cue === "route") return "route";
    return "network";
}

function EventGlyph({ pattern, color }: { pattern: ScreenPattern; color: string }) {
    const tone = pattern === "failed"
        ? "#FB7185"
        : pattern === "degraded"
            ? "#FBBF24"
            : pattern === "completed"
                ? "#34D399"
                : color;
    const dim = `${tone}66`;

    if (pattern === "research") {
        return (
            <>
                <Rect x={12} y={17} width={28} height={40} rx={4} fill="none" stroke={dim} strokeWidth={3} />
                <Path d="M19 29 H34 M19 37 H31 M19 45 H29" stroke={tone} strokeWidth={3} strokeLinecap="round" />
                <Circle cx={65} cy={38} r={13} fill="none" stroke={tone} strokeWidth={3} />
                <Path d="M74 47 L85 58" stroke={tone} strokeWidth={4} strokeLinecap="round" />
            </>
        );
    }
    if (pattern === "engineering") {
        return (
            <>
                <Path d="M12 22 H37 M12 31 H31 M12 40 H39 M12 49 H28" stroke={tone} strokeWidth={3} strokeLinecap="round" />
                <Path d="M44 27 L55 38 L44 49" fill="none" stroke={dim} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" />
                <Path d="M65 29 L84 38 L65 47 L56 38 Z" fill={`${tone}22`} stroke={tone} strokeWidth={3} strokeLinejoin="round" />
                <Path d="M65 47 V58 L84 48 V38" fill="none" stroke={tone} strokeWidth={3} strokeLinejoin="round" />
            </>
        );
    }
    if (pattern === "creative") {
        return (
            <>
                <Path d="M12 54 C28 12 58 68 87 24" fill="none" stroke={tone} strokeWidth={3} strokeLinecap="round" />
                <Path d="M12 54 L30 20 M30 20 L58 51 M58 51 L87 24" stroke={dim} strokeWidth={2} strokeDasharray="4 5" />
                <Circle cx={12} cy={54} r={4} fill={tone} />
                <Circle cx={30} cy={20} r={4} fill={tone} />
                <Circle cx={58} cy={51} r={4} fill={tone} />
                <Circle cx={87} cy={24} r={4} fill={tone} />
                <Path d="M72 14 V26 M66 20 H78" stroke="#FFFFFF" strokeWidth={3} strokeLinecap="round" />
            </>
        );
    }
    if (pattern === "desktop") {
        return (
            <>
                <Rect x={13} y={16} width={69} height={45} rx={5} fill={`${tone}12`} stroke={tone} strokeWidth={3} />
                <Path d="M13 27 H82" stroke={dim} strokeWidth={3} />
                <Circle cx={20} cy={22} r={2} fill={tone} />
                <Circle cx={27} cy={22} r={2} fill={tone} opacity={0.65} />
                <Path d="M44 34 L61 51 L52 52 L48 60 Z" fill="#FFFFFF" stroke={tone} strokeWidth={2} strokeLinejoin="round" />
            </>
        );
    }
    if (pattern === "rpa") {
        return (
            <>
                <Rect x={10} y={30} width={20} height={17} rx={4} fill={`${tone}24`} stroke={tone} strokeWidth={2.5} />
                <Rect x={40} y={30} width={20} height={17} rx={4} fill={`${tone}24`} stroke={tone} strokeWidth={2.5} />
                <Rect x={70} y={30} width={20} height={17} rx={4} fill={`${tone}24`} stroke={tone} strokeWidth={2.5} />
                <Path d="M30 38 H40 M60 38 H70" stroke={tone} strokeWidth={3} strokeLinecap="round" />
                <Path d="M75 18 C59 5 33 8 23 21 M23 21 L24 12 M23 21 L32 20" fill="none" stroke={tone} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" />
            </>
        );
    }
    if (pattern === "waiting") {
        return (
            <>
                <Circle cx={32} cy={39} r={6} fill={tone} opacity={0.45} />
                <Circle cx={50} cy={39} r={6} fill={tone} opacity={0.72} />
                <Circle cx={68} cy={39} r={6} fill={tone} />
                <Path d="M22 57 H78" stroke={dim} strokeWidth={2} strokeLinecap="round" />
            </>
        );
    }
    if (pattern === "completed") {
        return (
            <>
                <Circle cx={50} cy={39} r={24} fill={`${tone}12`} stroke={tone} strokeWidth={4} />
                <Path d="M37 39 L47 49 L66 29" fill="none" stroke="#FFFFFF" strokeWidth={5} strokeLinecap="round" strokeLinejoin="round" />
            </>
        );
    }
    if (pattern === "degraded") {
        return (
            <>
                <Path d="M12 39 H38 M62 39 H88" stroke={tone} strokeWidth={4} strokeLinecap="round" />
                <Path d="M38 39 Q49 15 62 26 M38 39 Q50 63 62 52" fill="none" stroke={tone} strokeWidth={3} strokeDasharray="5 4" />
                <Path d="M50 30 V43 M50 49 V50" stroke="#FFFFFF" strokeWidth={4} strokeLinecap="round" />
            </>
        );
    }
    if (pattern === "failed") {
        return (
            <>
                <Path d="M10 39 H38 M62 39 H90" stroke={tone} strokeWidth={4} strokeLinecap="round" />
                <Path d="M41 29 L59 49 M59 29 L41 49" stroke="#FFFFFF" strokeWidth={4} strokeLinecap="round" />
                <Path d="M75 20 C89 27 91 46 78 56 M78 56 L88 55 M78 56 L81 47" fill="none" stroke={tone} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round" />
            </>
        );
    }
    if (pattern === "route") {
        return (
            <>
                <Path d="M10 51 C25 51 24 24 42 24 H59 C75 24 72 49 90 49" fill="none" stroke={dim} strokeWidth={4} strokeLinecap="round" />
                <Circle cx={10} cy={51} r={5} fill={tone} />
                <Circle cx={42} cy={24} r={5} fill={tone} />
                <Circle cx={90} cy={49} r={5} fill={tone} />
            </>
        );
    }
    return (
        <>
            <Path d="M18 39 H50 M50 39 L75 21 M50 39 L78 56" stroke={dim} strokeWidth={3} strokeLinecap="round" />
            <Circle cx={18} cy={39} r={7} fill={tone} />
            <Circle cx={50} cy={39} r={7} fill={tone} />
            <Circle cx={75} cy={21} r={7} fill={tone} />
            <Circle cx={78} cy={56} r={7} fill={tone} />
        </>
    );
}

const EventScreen = memo(function EventScreen({
    cue,
    color,
    phase,
    status,
    continuousMotionEnabled,
}: {
    cue: CollaborationMicroStageCue;
    color: string;
    phase: SubagentRobotPhase;
    status: CollaborationMicroStageStatus;
    continuousMotionEnabled: boolean;
}) {
    const pattern = screenPatternFor(cue, status, phase);
    const progress = useSharedValue(0);

    useEffect(() => {
        cancelAnimation(progress);
        if (!continuousMotionEnabled) {
            progress.value = 0;
            return undefined;
        }
        progress.value = withRepeat(
            withTiming(1, { duration: pattern === "waiting" ? 1350 : 1600, easing: Easing.inOut(Easing.ease) }),
            -1,
            false,
        );
        return () => cancelAnimation(progress);
    }, [continuousMotionEnabled, pattern, progress]);

    const markerStyle = useAnimatedStyle(() => {
        let translateX = 0;
        let translateY = 0;
        let scale = 1;
        let rotate = 0;
        let opacity = 0.82;
        if (pattern === "research") translateX = progress.value * 34;
        else if (pattern === "route" || pattern === "network" || pattern === "rpa" || pattern === "degraded") translateX = progress.value * 30;
        else if (pattern === "engineering") scale = 0.25 + progress.value * 0.75;
        else if (pattern === "desktop") {
            translateX = progress.value * 9;
            translateY = progress.value * 6;
        } else if (pattern === "creative" || pattern === "completed" || pattern === "waiting") {
            scale = 0.72 + Math.sin(progress.value * Math.PI) * 0.42;
            opacity = 0.4 + Math.sin(progress.value * Math.PI) * 0.6;
        } else if (pattern === "failed") {
            rotate = -8 + progress.value * 16;
            opacity = 0.45 + progress.value * 0.5;
        }
        return {
            opacity,
            transform: [
                { translateX },
                { translateY },
                { scaleX: pattern === "engineering" ? scale : 1 },
                { scale: pattern === "engineering" ? 1 : scale },
                { rotate: `${rotate}deg` },
            ],
        };
    });

    const tone = pattern === "failed"
        ? "#FB7185"
        : pattern === "degraded"
            ? "#FBBF24"
            : pattern === "completed"
                ? "#34D399"
                : color;
    const markerIsLine = pattern === "research";
    const markerIsBar = pattern === "engineering";

    return (
        <View style={styles.screenSurface} pointerEvents="none">
            <Svg width="100%" height="100%" viewBox="0 0 100 78">
                <EventGlyph pattern={pattern} color={color} />
            </Svg>
            <Animated.View
                style={[
                    styles.screenMarker,
                    markerIsLine ? styles.screenMarkerLine : undefined,
                    markerIsBar ? styles.screenMarkerBar : undefined,
                    { backgroundColor: markerIsLine ? "#FFFFFF" : tone },
                    markerStyle,
                ]}
            />
        </View>
    );
});

export const WorkstationDisplay = memo(function WorkstationDisplay({
    cue,
    color,
    phase,
    status,
    continuousMotionEnabled = true,
}: {
    cue: CollaborationMicroStageCue;
    color: string;
    phase: SubagentRobotPhase;
    status: CollaborationMicroStageStatus;
    continuousMotionEnabled?: boolean;
}) {
    return (
        <View style={styles.workstation} pointerEvents="none">
            <Image
                source={require("../../../../assets/images/subagent_workstation.png")}
                style={styles.workstationImage}
                resizeMode="contain"
                fadeDuration={0}
            />
            <EventScreen
                cue={cue}
                color={color}
                phase={phase}
                status={status}
                continuousMotionEnabled={continuousMotionEnabled}
            />
        </View>
    );
});

const styles = StyleSheet.create({
    workstation: {
        width: 88,
        height: 88,
        position: "relative",
    },
    workstationImage: {
        position: "absolute",
        inset: 0,
        width: 88,
        height: 88,
    },
    screenSurface: {
        position: "absolute",
        left: 16.75,
        top: 4.07,
        width: 50.15,
        height: 40.03,
        borderRadius: 4,
        overflow: "hidden",
        backgroundColor: "rgba(2,6,23,0.08)",
    },
    screenMarker: {
        position: "absolute",
        left: 8,
        top: 18,
        width: 5,
        height: 5,
        borderRadius: 999,
        shadowColor: "#FFFFFF",
        shadowOpacity: 0.5,
        shadowRadius: 3,
    },
    screenMarkerLine: {
        left: 4,
        top: 5,
        width: 1.5,
        height: 30,
        borderRadius: 1,
    },
    screenMarkerBar: {
        left: 5,
        top: 34,
        width: 39,
        height: 2,
        borderRadius: 2,
        transformOrigin: "left center",
    },
    robotClip: {
        width: ROBOT_SHEET.frameSize,
        height: ROBOT_SHEET.frameSize,
        overflow: "hidden",
        position: "relative",
    },
    robotSheet: {
        position: "absolute",
    },
});
