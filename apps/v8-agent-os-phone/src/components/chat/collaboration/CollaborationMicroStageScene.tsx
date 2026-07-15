import { memo, useEffect, useMemo, useRef, useState, type ComponentType } from "react";
import { Image, Pressable, StyleSheet, Text, View } from "react-native";
import Svg, { Circle, Defs, Ellipse, G, LinearGradient, Path, Rect, Stop } from "react-native-svg";
import Animated, {
    cancelAnimation,
    Easing,
    FadeIn,
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
    CollaborationMicroStageActor,
    CollaborationMicroStageCue,
    CollaborationMicroStageLayout,
    CollaborationMicroStageStep,
    CollaborationMicroStageStatus,
} from "@v8/session-realtime";
import { selectCollaborationMicroStageLayout } from "@v8/session-realtime";

import { createTranslator } from "@/src/lib/locale";
import type { LocaleCode } from "@/src/providers/ui-prefs";
import type { ThemeColors } from "@/src/theme/tokens";

export type CollaborationMicroStageDetailTarget = {
    detailRef: string;
    stage: CollaborationMicroStage;
    step: CollaborationMicroStageStep;
    actor?: CollaborationMicroStageActor;
};

export type CollaborationMicroStageRendererProps = {
    stages: CollaborationMicroStage[];
    executionActive?: boolean;
    palette: ThemeColors;
    dark: boolean;
    locale: LocaleCode;
    supervisorSpeech?: string;
    onOpenDetailRef?: (target: CollaborationMicroStageDetailTarget) => void;
    overviewLinkLabel?: string;
    onOpenOverview?: () => void;
};

type CollaborationMicroStageSceneProps = CollaborationMicroStageRendererProps & {
    renderer?: ComponentType<CollaborationMicroStageRendererProps>;
};

type SupervisorAction = "idle" | "walk" | "summon" | "command" | "read" | "type" | "receive" | "celebrate";
type SupervisorDisplayAction = SupervisorAction | "turn";

const SUPERVISOR_SHEET = {
    columns: 7,
    rows: 5,
    frameWidth: 128,
    frameHeight: 128,
};

const SUPERVISOR_ACTION_FRAMES: Record<SupervisorAction, readonly number[]> = {
    idle: [0, 1, 2, 3],
    walk: [4, 5, 6, 7, 8, 9],
    summon: [10, 11, 12, 13],
    command: [14, 15, 16],
    read: [17, 18, 19, 20],
    type: [21, 22, 23, 24],
    receive: [25, 26],
    celebrate: [27, 30, 31, 32, 33, 34],
};
const SUPERVISOR_TURN_FRAMES = {
    left: [28],
    right: [29],
} as const;
const SUPERVISOR_ACTION_DURATIONS: Record<SupervisorAction, readonly number[]> = {
    idle: [420, 420, 220, 420],
    walk: [110, 110, 110, 110, 110, 110],
    summon: [260, 260, 520, 300],
    command: [320, 520, 380],
    read: [420, 420, 360, 520],
    type: [180, 140, 140, 420],
    receive: [500, 1100],
    celebrate: [500, 350, 500, 1500, 1200, 950],
};
const LOOPING_SUPERVISOR_ACTIONS = new Set<SupervisorAction>(["idle", "walk", "command", "read", "type"]);
const TURN_BRIDGE_DURATION_MS = 180;
const SUPERVISOR_TURN_DURATIONS = [TURN_BRIDGE_DURATION_MS] as const;

const STAGE_HEIGHT = 176;
const OFFICE_STAGE_HEIGHT = 244;
const CLUSTER_STAGE_HEIGHT = 218;
const WORK_CELL_WIDTH = 98;
const WORK_CELL_HEIGHT = 104;
const MAX_STAGE_ACTORS = 10;
const SUPERVISOR_BASE_TOP = 18;
const SUPERVISOR_LAYER_WIDTH = 136;
const SUPERVISOR_CENTER_X = SUPERVISOR_LAYER_WIDTH / 2;

type MicroStageRenderPhase = "entering" | "working" | "handoff" | "celebrating" | "warning" | "exiting";

type RetainedMicroStage = CollaborationMicroStage & {
    renderPhase: MicroStageRenderPhase;
    phaseUntil?: number;
};

type StageActorItem = {
    id: string;
    stage: RetainedMicroStage;
    actor: CollaborationMicroStageActor;
    sourceIndex: number;
};

type PositionedStageActorItem = StageActorItem & {
    x: number;
    y: number;
    scale: number;
};

type SupervisorSceneMode = "entering" | "working" | "handoff" | "celebrating" | "warning";
const ENTER_DURATION_MS = 1100;
const HANDOFF_DURATION_MS = 1600;
const FINAL_FEEDBACK_DURATION_MS = 5000;
const EXIT_DURATION_MS = 700;
const FINAL_REPLAY_WINDOW_MS = 12_000;

function statusTone(status: CollaborationMicroStageStatus, palette: ThemeColors) {
    if (status === "completed") return palette.success;
    if (status === "failed") return palette.danger;
    if (status === "degraded" || status === "attempted") return palette.warning;
    if (status === "pending") return palette.textSoft;
    return palette.accent;
}

function isFinalStatus(status: CollaborationMicroStageStatus) {
    return status === "completed" || status === "failed" || status === "degraded";
}

function retainedStageVersion(stage: CollaborationMicroStage) {
    return `${stage.id}:${stage.status}:${stage.timestamp}`;
}

function settleIncompleteStage(stage: CollaborationMicroStage): CollaborationMicroStage {
    if (isFinalStatus(stage.status)) return stage;
    const lastStepIndex = stage.steps.length - 1;
    return {
        ...stage,
        status: "completed",
        cue: "completed",
        steps: stage.steps.map((step, index) => (
            index === lastStepIndex && !isFinalStatus(step.status)
                ? { ...step, status: "completed", cue: "completed" }
                : step
        )),
        actors: stage.actors.map((actor) => (
            isFinalStatus(actor.status)
                ? actor
                : { ...actor, status: "completed", cue: "completed" }
        )),
    };
}

function preserveMonotonicFinalStageState(
    stage: CollaborationMicroStage,
    previous?: RetainedMicroStage,
): CollaborationMicroStage {
    if (!previous || !isFinalStatus(previous.status) || isFinalStatus(stage.status)) {
        return stage;
    }
    return {
        ...stage,
        status: previous.status,
        cue: previous.cue,
        steps: stage.steps.map((step) => (
            isFinalStatus(step.status)
                ? step
                : { ...step, status: previous.status, cue: previous.cue }
        )),
        actors: stage.actors.map((actor) => (
            isFinalStatus(actor.status)
                ? actor
                : { ...actor, status: previous.status, cue: previous.cue }
        )),
    };
}

function nextPhaseForStage(stage: CollaborationMicroStage, previous?: RetainedMicroStage): MicroStageRenderPhase {
    if (previous?.renderPhase === "exiting") return "exiting";
    if (stage.status === "completed" || stage.status === "failed" || stage.status === "degraded") {
        if (!previous || !isFinalStatus(previous.status)) return "handoff";
        if (previous.renderPhase === "handoff") return "handoff";
        if (previous.renderPhase === "celebrating" || previous.renderPhase === "warning") return previous.renderPhase;
        return stage.status === "completed" ? "celebrating" : "warning";
    }
    if (!previous) return "entering";
    if (previous.renderPhase === "entering") return "entering";
    return "working";
}

function phaseUntil(phase: MicroStageRenderPhase, now: number) {
    if (phase === "entering") return now + ENTER_DURATION_MS;
    if (phase === "handoff") return now + HANDOFF_DURATION_MS;
    if (phase === "celebrating" || phase === "warning") return now + FINAL_FEEDBACK_DURATION_MS;
    if (phase === "exiting") return now + EXIT_DURATION_MS;
    return undefined;
}

function advancePhase(stage: RetainedMicroStage): MicroStageRenderPhase | null {
    const phase = stage.renderPhase;
    if (phase === "entering") return "working";
    if (phase === "handoff") return stage.status === "completed" ? "celebrating" : "warning";
    if (phase === "celebrating" || phase === "warning") return "exiting";
    if (phase === "exiting") return null;
    return phase;
}

function useRetainedMicroStages(stages: CollaborationMicroStage[], executionActive: boolean) {
    const [retained, setRetained] = useState<RetainedMicroStage[]>([]);
    const [initialized, setInitialized] = useState(false);
    const [settledOutcome, setSettledOutcome] = useState(false);
    const dismissedFinalVersions = useRef(new Set<string>());
    const executionWasActive = useRef(executionActive);
    const settlementLocked = useRef(false);

    useEffect(() => {
        if (executionActive) {
            executionWasActive.current = true;
            if (!settlementLocked.current) {
                setSettledOutcome(false);
            }
        }
        if (!executionActive && executionWasActive.current) {
            executionWasActive.current = false;
            settlementLocked.current = true;
        }
        const shouldSettleIncomplete = settlementLocked.current;
        const effectiveStages = shouldSettleIncomplete
            ? stages.map(settleIncompleteStage)
            : stages;
        if (shouldSettleIncomplete && effectiveStages.some((stage) => isFinalStatus(stage.status))) {
            setSettledOutcome(true);
        }
        const now = Date.now();
        setRetained((current) => {
            const currentById = new Map(current.map((stage) => [stage.id, stage]));
            const incomingIds = new Set(effectiveStages.map((stage) => stage.id));
                const next: RetainedMicroStage[] = effectiveStages.flatMap((stage) => {
                    const previous = currentById.get(stage.id);
                    const renderStage = preserveMonotonicFinalStageState(stage, previous);
                    const version = retainedStageVersion(renderStage);
                    if (!previous && dismissedFinalVersions.current.has(version)) {
                        return [];
                    }
                    if (
                        !previous
                        && isFinalStatus(renderStage.status)
                        && (!renderStage.timestamp || now - renderStage.timestamp > FINAL_REPLAY_WINDOW_MS)
                    ) {
                        dismissedFinalVersions.current.add(version);
                        return [];
                    }
                    if (!previous && !executionActive && !shouldSettleIncomplete && !isFinalStatus(renderStage.status)) {
                        return [];
                    }
                    const renderPhase = nextPhaseForStage(renderStage, previous);
                    const previousUntil = previous?.renderPhase === renderPhase ? previous.phaseUntil : undefined;
                    return [{
                        ...renderStage,
                        renderPhase,
                        phaseUntil: previousUntil ?? phaseUntil(renderPhase, now),
                    }];
            });

            current.forEach((stage) => {
                if (incomingIds.has(stage.id) || stage.renderPhase === "exiting") {
                    return;
                }
                next.push({
                    ...stage,
                    renderPhase: "exiting",
                    phaseUntil: phaseUntil("exiting", now),
                });
            });

            return next.sort((left, right) => left.timestamp - right.timestamp);
        });
        setInitialized(true);
    }, [executionActive, stages]);

    useEffect(() => {
        const expiring = retained
            .map((stage) => stage.phaseUntil)
            .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
        if (expiring.length === 0) {
            return undefined;
        }
        const now = Date.now();
        const delay = Math.max(80, Math.min(...expiring) - now);
        const timer = setTimeout(() => {
            const tick = Date.now();
            setRetained((current) => current.flatMap((stage) => {
                if (!stage.phaseUntil || stage.phaseUntil > tick) {
                    return [stage];
                }
                const nextPhase = advancePhase(stage);
                if (!nextPhase) {
                    if (isFinalStatus(stage.status)) {
                        dismissedFinalVersions.current.add(retainedStageVersion(stage));
                    }
                    return [];
                }
                return [{
                    ...stage,
                    renderPhase: nextPhase,
                    phaseUntil: phaseUntil(nextPhase, tick),
                }];
            }));
        }, delay);
        return () => clearTimeout(timer);
    }, [retained]);

    return { initialized, retained, settledOutcome };
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

function sceneModeForStages(stages: RetainedMicroStage[]): SupervisorSceneMode {
    const visibleStages = stages.filter((stage) => stage.renderPhase !== "exiting");
    if (visibleStages.length === 0 && stages.length > 0) {
        return stages.some((stage) => stage.status === "failed" || stage.status === "degraded")
            ? "warning"
            : "celebrating";
    }
    const unfinishedStages = visibleStages.filter((stage) => !isFinalStatus(stage.status));
    if (unfinishedStages.some((stage) => stage.renderPhase === "entering")) return "entering";
    if (visibleStages.some((stage) => stage.renderPhase === "handoff")) return "handoff";
    if (unfinishedStages.length > 0) return "working";
    if (visibleStages.some((stage) => stage.renderPhase === "warning")) return "warning";
    if (visibleStages.some((stage) => stage.renderPhase === "celebrating")) return "celebrating";
    return "working";
}

function supervisorActionForScene(mode: SupervisorSceneMode, moving: boolean, items: StageActorItem[]): SupervisorAction {
    if (mode === "entering") return "summon";
    if (mode === "handoff") return "receive";
    if (mode === "celebrating") return "celebrate";
    if (mode === "warning") return "read";
    if (moving) return "walk";
    if (items.some((item) => item.actor.status === "active")) return "command";
    if (items.some((item) => item.actor.status === "pending" || item.actor.status === "attempted")) return "read";
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

function latestActorStep(stage: CollaborationMicroStage, actor: CollaborationMicroStageActor) {
    for (let index = stage.steps.length - 1; index >= 0; index -= 1) {
        const step = stage.steps[index];
        if (actor.stepIds.includes(step.id) || actor.sourceActivityIds.includes(step.sourceActivityId)) {
            return step;
        }
    }
    return latestStep(stage);
}

function buildStageActorItems(stages: RetainedMicroStage[]): StageActorItem[] {
    const items: StageActorItem[] = [];
    stages.forEach((stage, stageIndex) => {
        const actors = stage.actors.length > 0
            ? stage.actors
            : [{
                id: `${stage.id}:actor`,
                kind: stage.kind,
                label: latestStep(stage)?.actorLabel || stage.title,
                status: stage.status,
                cue: stage.cue,
                summary: latestStep(stage)?.summary || stage.subtitle,
                timestamp: stage.timestamp,
                detailRef: latestStep(stage)?.detailRef,
                sourceActivityIds: stage.sourceActivityIds,
                stepIds: stage.steps.map((step) => step.id),
            } satisfies CollaborationMicroStageActor];

        actors.forEach((actor, actorIndex) => {
            items.push({
                id: `${stage.id}:${actor.id || actorIndex}`,
                stage,
                actor,
                sourceIndex: stageIndex + actorIndex,
            });
        });
    });
    return items.slice(Math.max(0, items.length - MAX_STAGE_ACTORS));
}

function layoutHeight(layout: CollaborationMicroStageLayout) {
    if (layout === "singleRow") return STAGE_HEIGHT;
    if (layout === "officeGrid") return OFFICE_STAGE_HEIGHT;
    return CLUSTER_STAGE_HEIGHT;
}

function positionStageActorItems(
    items: StageActorItem[],
    layout: CollaborationMicroStageLayout,
    width: number,
): PositionedStageActorItem[] {
    const canvasWidth = Math.max(300, width || 320);
    if (layout === "singleRow") {
        const count = Math.max(1, items.length);
        const supervisorLane = 112;
        const cellWidth = count <= 2 ? 100 : 88;
        const gap = count > 1 ? 12 : 0;
        const contentWidth = supervisorLane + cellWidth * count + gap * (count - 1);
        const start = Math.max(12, (canvasWidth - contentWidth) / 2) + supervisorLane;
        return items.map((item, index) => ({
            ...item,
            x: start + index * (cellWidth + gap),
            y: 33,
            scale: count >= 3 ? 0.88 : 0.96,
        }));
    }

    if (layout === "officeGrid") {
        const columns = Math.min(3, Math.max(2, items.length));
        const cellWidth = canvasWidth / columns;
        return items.map((item, index) => {
            const column = index % columns;
            const row = Math.floor(index / columns);
            const scale = 0.78;
            return {
                ...item,
                x: column * cellWidth + Math.max(0, (cellWidth - WORK_CELL_WIDTH * scale) / 2),
                y: 27 + row * 92,
                scale,
            };
        });
    }

    const columns = Math.min(5, Math.max(4, items.length));
    const cellWidth = canvasWidth / columns;
    const scale = Math.max(0.5, Math.min(0.64, (cellWidth - 2) / WORK_CELL_WIDTH));
    return items.map((item, index) => {
        const column = index % columns;
        const row = Math.floor(index / columns);
        return {
            ...item,
            x: column * cellWidth + Math.max(0, (cellWidth - WORK_CELL_WIDTH * scale) / 2),
            y: 24 + row * 76,
            scale,
        };
    });
}

function clamp(value: number, min: number, max: number) {
    return Math.min(Math.max(value, min), Math.max(min, max));
}

function actorIsUnfinished(item: PositionedStageActorItem) {
    return !isFinalStatus(item.actor.status) && item.stage.renderPhase !== "exiting";
}

function supervisorWaypointForItem(item: PositionedStageActorItem, width: number) {
    const canvasWidth = Math.max(300, width || 320);
    return {
        x: clamp(item.x - 94, 12, canvasWidth - SUPERVISOR_LAYER_WIDTH - 10),
        y: Math.max(0, item.y - 32),
    };
}

function supervisorWaypointsForItems(items: PositionedStageActorItem[], width: number) {
    const unfinished = items.filter(actorIsUnfinished);
    const candidates = unfinished.length > 0 ? unfinished : items;
    const seen = new Set<string>();
    return candidates.flatMap((item) => {
        const waypoint = supervisorWaypointForItem(item, width);
        const key = `${Math.round(waypoint.x)}:${Math.round(waypoint.y)}`;
        if (seen.has(key)) return [];
        seen.add(key);
        return [waypoint];
    });
}

function useSupervisorDisplayState(action: SupervisorAction, facingLeft: boolean) {
    const [displayState, setDisplayState] = useState<{ action: SupervisorDisplayAction; facingLeft: boolean }>(() => ({
        action,
        facingLeft,
    }));
    const previousInput = useRef({ action, facingLeft });

    useEffect(() => {
        const previous = previousInput.current;
        previousInput.current = { action, facingLeft };
        const crossesWalkBoundary = (previous.action === "walk") !== (action === "walk");
        const needsTurnBridge = previous.facingLeft !== facingLeft || crossesWalkBoundary;
        if (!needsTurnBridge) {
            setDisplayState({ action, facingLeft });
            return undefined;
        }

        setDisplayState({ action: "turn", facingLeft });
        const timer = setTimeout(() => {
            setDisplayState({ action, facingLeft });
        }, TURN_BRIDGE_DURATION_MS);
        return () => clearTimeout(timer);
    }, [action, facingLeft]);

    return displayState;
}

function useSupervisorFrame(action: SupervisorDisplayAction, facingLeft: boolean) {
    const frames = action === "turn"
        ? (facingLeft ? SUPERVISOR_TURN_FRAMES.left : SUPERVISOR_TURN_FRAMES.right)
        : SUPERVISOR_ACTION_FRAMES[action];
    const durations = action === "turn" ? SUPERVISOR_TURN_DURATIONS : SUPERVISOR_ACTION_DURATIONS[action];
    const loops = action !== "turn" && LOOPING_SUPERVISOR_ACTIONS.has(action);
    const [frameIndex, setFrameIndex] = useState(0);

    useEffect(() => {
        let cancelled = false;
        let timer: ReturnType<typeof setTimeout> | undefined;
        let current = 0;
        setFrameIndex(0);
        if (frames.length <= 1) return undefined;

        const scheduleNext = () => {
            timer = setTimeout(() => {
                if (cancelled) return;
                if (!loops && current >= frames.length - 1) return;
                current = (current + 1) % frames.length;
                setFrameIndex(current);
                scheduleNext();
            }, durations[Math.min(current, durations.length - 1)] || 180);
        };
        scheduleNext();
        return () => {
            cancelled = true;
            if (timer) clearTimeout(timer);
        };
    }, [action, durations, frames, loops]);

    return frames[frameIndex] ?? frames[0] ?? 0;
}

function SupervisorSprite({
    action,
    facingLeft,
}: {
    action: SupervisorAction;
    facingLeft: boolean;
}) {
    const displayState = useSupervisorDisplayState(action, facingLeft);
    const frame = useSupervisorFrame(displayState.action, displayState.facingLeft);
    const column = frame % SUPERVISOR_SHEET.columns;
    const row = Math.floor(frame / SUPERVISOR_SHEET.columns);
    const mirrorWalkFrame = displayState.action === "walk" && !displayState.facingLeft;

    return (
        <View
            style={[
                styles.supervisorSpriteClip,
                mirrorWalkFrame ? styles.supervisorSpriteMirrored : undefined,
            ]}
            accessibilityLabel={`${displayState.action}:${displayState.facingLeft ? "left" : "right"}`}
        >
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
        </View>
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

function Workstation({
    cue,
    color,
    status,
    active,
    isWalking,
}: {
    cue: CollaborationMicroStageCue;
    color: string;
    status: CollaborationMicroStageStatus;
    active: boolean;
    isWalking: boolean;
}) {
    let screenContent = null;
    let screenBg = "#0f172a";

    if (status === "failed") {
        screenBg = "#7f1d1d";
        screenContent = (
            <G>
                <Path d="M40 9 L33 21 H47 Z" fill="#ef4444" stroke="#ffffff" strokeWidth={1} />
                <Path d="M40 13 V17" stroke="#ffffff" strokeWidth={1.5} />
                <Circle cx="40" cy="19.5" r="0.8" fill="#ffffff" />
            </G>
        );
    } else if (status === "completed") {
        screenContent = (
            <G>
                <Path d="M34 17 L38 21 L46 12" stroke="#10b981" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" fill="none" />
                <Circle cx="40" cy="16" r="8" fill="none" stroke="#10b981" strokeWidth={0.8} opacity={0.4} />
            </G>
        );
    } else if (status === "degraded") {
        screenBg = "#7c2d12";
        screenContent = (
            <Path d="M22 17 Q27 10 32 17 T42 17 T52 17" stroke="#f97316" strokeWidth={1.5} fill="none" />
        );
    } else if (status === "pending") {
        screenContent = (
            <G>
                <Path d="M37 10 H43 L37 20 H43 Z" fill="none" stroke="#fbbf24" strokeWidth={1.2} strokeLinecap="round" strokeLinejoin="round" />
                <Circle cx="40" cy="15" r="5" fill="none" stroke="#fbbf24" strokeWidth={0.8} strokeDasharray="2,2" />
            </G>
        );
    } else if (active || status === "active") {
        screenBg = "#090d16";
        screenContent = (
            <G>
                <Path d="M22 9 H38 M24 13 H42 M22 17 H35 M26 21 H40" stroke="#00ffcc" strokeWidth={1} strokeLinecap="round" />
                <Path d="M40 9 H44" stroke="#fbbf24" strokeWidth={1} strokeLinecap="round" />
                <Path d="M44 13 H46" stroke="#ffffff" strokeWidth={1} strokeLinecap="round" />
                <Path d="M37 17 H45" stroke="#38bdf8" strokeWidth={1} strokeLinecap="round" />
            </G>
        );
    }

    const showRobotHead = !isWalking;

    return (
        <View style={styles.workstation}>
            <Svg width={86} height={64} viewBox="0 0 80 60" style={StyleSheet.absoluteFill}>
                {/* White workstation tabletop */}
                <Path d="M2 38 L78 38 L70 32 L10 32 Z" fill="#ffffff" stroke="#e2e8f0" strokeWidth={1} />
                <Rect x="2" y="38" width="76" height="3" fill="#f1f5f9" />
                
                {/* Cabinet drawers */}
                <Rect x="56" y="41" width="14" height="18" fill="#cbd5e1" stroke="#94a3b8" strokeWidth={0.8} rx={1} />
                <Path d="M59 46 H67" stroke="#475569" strokeWidth={1} />
                <Path d="M59 52 H67" stroke="#475569" strokeWidth={1} />
                
                {/* Table legs */}
                <Path d="M6 41 V59" stroke="#94a3b8" strokeWidth={1.5} />
                <Path d="M50 41 V59" stroke="#94a3b8" strokeWidth={1.5} />
                
                {/* Keyboard */}
                <Path d="M28 36.5 H52 L50 37.8 H30 Z" fill="#e2e8f0" stroke="#94a3b8" strokeWidth={0.5} />
                
                {/* Computer stand */}
                <Path d="M40 32 V28" stroke="#94a3b8" strokeWidth={2.5} />
                {/* Computer monitor outer frame */}
                <Rect x="16" y="4" width="48" height="26" rx="2" fill="#e2e8f0" stroke="#cbd5e1" strokeWidth={1} />
                {/* Computer monitor screen */}
                <Rect x="18" y="6" width="44" height="22" rx={1} fill={screenBg} />
                
                {/* Dynamic screen content */}
                {screenContent}
                
                {/* Glass reflection shine */}
                <Path d="M62 6 L38 28 H62 Z" fill="#ffffff" opacity={0.08} />

                {/* Chair (Integrated SVG) */}
                <Path d="M40 53 V58" stroke="#64748b" strokeWidth={1.5} />
                <Path d="M33 58 H47" stroke="#64748b" strokeWidth={1.5} strokeLinecap="round" />
                <Rect x="31" y="49" width="18" height="4" rx={1} fill="#e2e8f0" stroke="#cbd5e1" strokeWidth={0.8} />
                <Rect x="33" y="37" width="14" height="12" rx={3} fill="#ffffff" stroke="#cbd5e1" strokeWidth={1} />

                {/* Sitting robot peeking head */}
                {showRobotHead && (
                    <G>
                        <Circle cx="40" cy="34" r="3.5" fill="#f1f5f9" stroke="#64748b" strokeWidth={0.8} />
                        <Path d="M40 30.5 V27" stroke="#64748b" strokeWidth={0.8} />
                        <Circle cx="40" cy="26" r="1.2" fill={color} />
                        <Rect x="38" y="33.5" width="4" height="1" rx="0.3" fill={color} opacity={0.8} />
                    </G>
                )}
            </Svg>
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
    actor: CollaborationMicroStageActor;
    index: number;
    x: number;
    y: number;
    scale: number;
    phase: MicroStageRenderPhase;
    palette: ThemeColors;
    dark: boolean;
    supervisorX: SharedValue<number>;
    supervisorY: SharedValue<number>;
    onOpenDetailRef?: (target: CollaborationMicroStageDetailTarget) => void;
    t: ReturnType<typeof createTranslator>;
};

const WorkCell = memo(function WorkCell({
    stage,
    actor,
    index,
    x,
    y,
    scale,
    phase,
    palette,
    dark,
    supervisorX,
    supervisorY,
    onOpenDetailRef,
    t,
}: WorkCellProps) {
    const color = stageColor(stage, index);
    const step = latestActorStep(stage, actor);
    const cue = actor.cue || step?.cue || stage.cue;
    const actorStatus = actor.status || stage.status;
    const active = actorStatus === "active" && phase !== "exiting";
    const isHandoff = isFinalStatus(actorStatus) || cue === "handoff" || cue === "completed";
    const appeared = useSharedValue(0);
    const shake = useSharedValue(0);
    const walkBack = useSharedValue(0);
    const submit = useSharedValue(0);
    const warningPulse = useSharedValue(0);

    useEffect(() => {
        appeared.value = withSpring(1, { damping: 13, stiffness: 120 });
    }, [appeared]);

    useEffect(() => {
        if (phase === "exiting") {
            appeared.value = withTiming(0, { duration: EXIT_DURATION_MS, easing: Easing.in(Easing.cubic) });
            return;
        }
        appeared.value = withTiming(1, { duration: 220, easing: Easing.out(Easing.cubic) });
    }, [appeared, phase]);

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
        if (actorStatus === "failed" || actorStatus === "degraded") {
            warningPulse.value = withRepeat(
                withSequence(withTiming(1, { duration: 420 }), withTiming(0, { duration: 420 })),
                -1,
                true,
            );
            return;
        }
        warningPulse.value = withTiming(0, { duration: 180 });
    }, [actorStatus, warningPulse]);

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
        transform: [{ scale: scale * (0.92 + appeared.value * 0.08) }],
    }));

    const botStyle = useAnimatedStyle(() => {
        const target = supervisorX.value + SUPERVISOR_CENTER_X - 10 - x;
        const targetY = SUPERVISOR_BASE_TOP + supervisorY.value + 50 - y - 42;
        return {
            opacity: isHandoff ? (appeared.value * (1 - submit.value * 0.82)) : 0,
            transform: [
                { translateX: walkBack.value * target + (walkBack.value < 0.02 ? shake.value * 0.9 : 0) },
                { translateY: walkBack.value * targetY + (walkBack.value > 0 ? Math.sin(walkBack.value * Math.PI * 10) * 1.4 : 0) },
            ],
        };
    });

    const reportStyle = useAnimatedStyle(() => ({
        opacity: walkBack.value > 0.72 ? 1 - submit.value : 0,
        transform: [
            { translateX: walkBack.value * (supervisorX.value + SUPERVISOR_CENTER_X - x) },
            { translateY: walkBack.value * (SUPERVISOR_BASE_TOP + supervisorY.value + 40 - y - 52) - submit.value * 18 },
        ],
    }));

    const badgeStyle = useAnimatedStyle(() => ({
        opacity: 0.9 + warningPulse.value * 0.1,
        transform: [{ scale: 1 + warningPulse.value * 0.035 }],
    }));

    const handlePress = () => {
        const detailRef = actor.detailRef || step?.detailRef;
        if (detailRef && step && onOpenDetailRef) {
            onOpenDetailRef({ detailRef, stage, step, actor });
        }
    };

    return (
        <Animated.View style={[styles.workCell, { left: x, top: y }, cellStyle]}>
            {(phase === "entering" || cue === "summon") && <MagicPortal color={color} />}
            <WorkbenchShadow />
            <Workstation
                cue={cue}
                color={color}
                status={actorStatus}
                active={active}
                isWalking={isHandoff}
            />
            <Animated.View style={[styles.robotLayer, botStyle]}>
                <GroundShadow width={38} opacity={0.18} />
                <RobotActor color={color} active={active || isHandoff} status={actorStatus} />
            </Animated.View>
            {isHandoff && (
                <Animated.View style={[styles.reportLayer, reportStyle]} pointerEvents="none">
                    <ReportScroll color={color} />
                </Animated.View>
            )}
            <Animated.View style={[styles.stageBadgeWrap, badgeStyle]}>
                <Pressable
                    onPress={handlePress}
                    disabled={!(actor.detailRef || step?.detailRef)}
                    accessibilityRole={actor.detailRef || step?.detailRef ? "button" : undefined}
                    accessibilityLabel={actor.summary || step?.summary || stage.title}
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
                        {actor.label || step?.actorLabel || stage.title}
                    </Text>
                </Pressable>
            </Animated.View>
            <View style={styles.statusBadgeWrap}>
                <StatusBadge status={actorStatus} color={color} palette={palette} t={t} />
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

function SupervisorSpeechBubble({ text, palette }: { text?: string; palette: ThemeColors }) {
    if (!text) {
        return null;
    }
    return (
        <View style={[styles.supervisorSpeechBubble, { backgroundColor: palette.surfaceStrong, borderColor: palette.border }]}>
            <Text style={[styles.supervisorSpeechText, { color: palette.text }]} numberOfLines={3}>
                {text}
            </Text>
            <View style={[styles.supervisorSpeechTail, { borderTopColor: palette.surfaceStrong }]} />
        </View>
    );
}

export const CollaborationMicroStageLightRenderer = memo(function CollaborationMicroStageLightRenderer({
    stages,
    executionActive = false,
    palette,
    dark,
    locale,
    supervisorSpeech,
    onOpenDetailRef,
    overviewLinkLabel,
    onOpenOverview,
}: CollaborationMicroStageRendererProps) {
    const t = createTranslator(locale);
    const supervisorX = useSharedValue(26);
    const supervisorY = useSharedValue(0);
    const sceneOpacity = useSharedValue(1);
    const [canvasWidth, setCanvasWidth] = useState(0);
    const [supervisorMoving, setSupervisorMoving] = useState(false);
    const [supervisorFacingLeft, setSupervisorFacingLeft] = useState(false);
    const sawRenderedStage = useRef(false);
    const { initialized, retained: retainedStages, settledOutcome } = useRetainedMicroStages(stages, executionActive);
    const actorItems = useMemo(() => buildStageActorItems(retainedStages), [retainedStages]);
    const layout = useMemo(() => selectCollaborationMicroStageLayout(retainedStages), [retainedStages]);
    const stageHeight = layoutHeight(layout);
    const positionedItems = useMemo(
        () => positionStageActorItems(actorItems, layout, canvasWidth),
        [actorItems, canvasWidth, layout],
    );
    const sceneMode = useMemo(() => sceneModeForStages(retainedStages), [retainedStages]);
    const patrolWaypoints = useMemo(
        () => supervisorWaypointsForItems(positionedItems, canvasWidth),
        [canvasWidth, positionedItems],
    );
    const focusItem = useMemo(() => {
        if (sceneMode === "entering") {
            return positionedItems.find(actorIsUnfinished) || positionedItems[0];
        }
        return positionedItems.find((item) => (
            item.stage.renderPhase === sceneMode
            || (sceneMode === "warning" && (item.actor.status === "failed" || item.actor.status === "degraded"))
        )) || positionedItems[0];
    }, [positionedItems, sceneMode]);
    const focusPosition = focusItem
        ? supervisorWaypointForItem(focusItem, canvasWidth)
        : { x: 58, y: 0 };
    const patrolSignature = patrolWaypoints.map((point) => `${Math.round(point.x)}:${Math.round(point.y)}`).join("|");
    const action = useMemo(
        () => supervisorActionForScene(sceneMode, supervisorMoving, actorItems),
        [actorItems, sceneMode, supervisorMoving],
    );
    const allExiting = retainedStages.every((stage) => stage.renderPhase === "exiting");
    const hasFinalOutcome = settledOutcome || stages.some((stage) => isFinalStatus(stage.status));

    useEffect(() => {
        if (positionedItems.length === 0) return undefined;
        const settleTimers = new Set<ReturnType<typeof setTimeout>>();
        const moveTo = (next: { x: number; y: number }, duration: number, walking: boolean) => {
            setSupervisorFacingLeft(next.x < supervisorX.value);
            setSupervisorMoving(walking);
            supervisorX.value = withTiming(next.x, { duration, easing: Easing.inOut(Easing.cubic) });
            supervisorY.value = withTiming(next.y, { duration, easing: Easing.inOut(Easing.cubic) });
            if (walking) {
                const settleTimer = setTimeout(() => setSupervisorMoving(false), duration);
                settleTimers.add(settleTimer);
            }
        };

        if (sceneMode !== "working" || patrolWaypoints.length === 0) {
            moveTo(focusPosition, sceneMode === "entering" ? 360 : 520, false);
            return () => settleTimers.forEach((timer) => clearTimeout(timer));
        }

        let waypointIndex = 0;
        moveTo(patrolWaypoints[0], 1100, true);
        const timer = patrolWaypoints.length > 1
            ? setInterval(() => {
                waypointIndex = (waypointIndex + 1) % patrolWaypoints.length;
                moveTo(patrolWaypoints[waypointIndex], 1250, true);
            }, 3600)
            : undefined;

        return () => {
            if (timer) clearInterval(timer);
            settleTimers.forEach((settleTimer) => clearTimeout(settleTimer));
            cancelAnimation(supervisorX);
            cancelAnimation(supervisorY);
        };
    }, [
        focusPosition.x,
        focusPosition.y,
        patrolSignature,
        patrolWaypoints,
        positionedItems.length,
        sceneMode,
        supervisorX,
        supervisorY,
    ]);

    useEffect(() => {
        if (retainedStages.length > 0) {
            sawRenderedStage.current = true;
            return undefined;
        }
        if (!initialized || !hasFinalOutcome || !onOpenOverview || !sawRenderedStage.current) {
            return undefined;
        }
        sawRenderedStage.current = false;
        const timer = setTimeout(() => onOpenOverview(), 220);
        return () => clearTimeout(timer);
    }, [hasFinalOutcome, initialized, onOpenOverview, retainedStages.length]);

    useEffect(() => {
        sceneOpacity.value = withTiming(allExiting ? 0 : 1, {
            duration: allExiting ? EXIT_DURATION_MS : 180,
            easing: allExiting ? Easing.in(Easing.cubic) : Easing.out(Easing.cubic),
        });
    }, [allExiting, sceneOpacity]);

    const supervisorStyle = useAnimatedStyle(() => ({
        transform: [
            { translateX: supervisorX.value },
            { translateY: supervisorY.value },
        ],
    }));
    const sceneStyle = useAnimatedStyle(() => ({ opacity: sceneOpacity.value }));

    if (retainedStages.length === 0 || positionedItems.length === 0) {
        if (initialized && hasFinalOutcome && onOpenOverview) {
            return (
                <Animated.View entering={FadeIn.duration(180)} style={styles.overviewLinkWrap}>
                    <Pressable
                        accessibilityRole="button"
                        accessibilityLabel={overviewLinkLabel}
                        onPress={onOpenOverview}
                        style={({ pressed }) => [styles.overviewLink, { opacity: pressed ? 0.58 : 1 }]}
                    >
                        <Text style={[styles.overviewLinkText, { color: palette.textMuted }]}>{overviewLinkLabel}</Text>
                        <Text accessible={false} style={[styles.overviewLinkArrow, { color: palette.textMuted }]}>→</Text>
                    </Pressable>
                </Animated.View>
            );
        }
        return null;
    }

    return (
        <Animated.View style={[styles.wrap, { height: stageHeight }, sceneStyle]}>
            <View
                style={[styles.canvasScroller, { height: stageHeight }]}
                onLayout={(event) => setCanvasWidth(event.nativeEvent.layout.width)}
                accessibilityLabel={t("src.components.chat.collaborationmicrostagescene.accessibility_label")}
            >
                <StageFloor dark={dark} />
                <Animated.View style={[styles.supervisorLayer, supervisorStyle]}>
                    <SupervisorSpeechBubble text={supervisorSpeech} palette={palette} />
                    <GroundShadow width={78} opacity={0.2} />
                    <SupervisorSprite action={action} facingLeft={supervisorFacingLeft} />
                </Animated.View>
                {positionedItems.map((item, index) => (
                    <WorkCell
                        key={item.id}
                        stage={item.stage}
                        actor={item.actor}
                        index={index}
                        x={item.x}
                        y={item.y}
                        scale={item.scale}
                        phase={item.stage.renderPhase}
                        palette={palette}
                        dark={dark}
                        supervisorX={supervisorX}
                        supervisorY={supervisorY}
                        onOpenDetailRef={onOpenDetailRef}
                        t={t}
                    />
                ))}
            </View>
        </Animated.View>
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
        marginVertical: 4,
        overflow: "hidden",
    },
    overviewLinkWrap: {
        width: "100%",
        minHeight: 44,
        justifyContent: "center",
        paddingHorizontal: 4,
        marginVertical: 4,
    },
    overviewLink: {
        minHeight: 44,
        alignSelf: "flex-start",
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        paddingHorizontal: 4,
    },
    overviewLinkText: {
        fontSize: 12,
        fontWeight: "600",
        textDecorationLine: "underline",
    },
    overviewLinkArrow: {
        fontSize: 14,
        fontWeight: "700",
    },
    canvasScroller: {
        position: "relative",
        width: "100%",
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
        top: SUPERVISOR_BASE_TOP,
        width: SUPERVISOR_LAYER_WIDTH,
        height: 136,
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
    supervisorSpriteMirrored: {
        transform: [{ scaleX: -1 }],
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
        width: 98,
        height: WORK_CELL_HEIGHT,
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
    supervisorSpeechBubble: {
        position: "absolute",
        left: -20,
        top: -20,
        width: 176,
        minHeight: 34,
        borderRadius: 14,
        borderWidth: 1,
        paddingHorizontal: 10,
        paddingVertical: 7,
        shadowColor: "#020617",
        shadowOpacity: 0.12,
        shadowRadius: 10,
        shadowOffset: { width: 0, height: 3 },
        zIndex: 40,
    },
    supervisorSpeechText: {
        fontSize: 10,
        lineHeight: 14,
        fontWeight: "800",
        letterSpacing: 0,
    },
    supervisorSpeechTail: {
        position: "absolute",
        left: 88,
        bottom: -7,
        width: 0,
        height: 0,
        borderLeftWidth: 6,
        borderRightWidth: 6,
        borderTopWidth: 7,
        borderLeftColor: "transparent",
        borderRightColor: "transparent",
    },
});
