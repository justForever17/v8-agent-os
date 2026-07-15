"use client";

import { memo, useEffect, useMemo, useRef, useState } from "react";
import type {
    CollaborationMicroStage,
    CollaborationMicroStageActor,
    CollaborationMicroStageCue,
    CollaborationMicroStageLayout,
    CollaborationMicroStageStatus,
    CollaborationMicroStageStep,
} from "@v8/session-realtime";
import { selectCollaborationMicroStageLayout } from "@v8/session-realtime";
import { cn } from "@/lib/utils";

export type CollaborationMicroStageDetailTarget = {
    detailRef: string;
    stage: CollaborationMicroStage;
    step: CollaborationMicroStageStep;
    actor?: CollaborationMicroStageActor;
};

interface CollaborationMicroStageSceneProps {
    stages: CollaborationMicroStage[];
    executionActive?: boolean;
    supervisorSpeech?: string;
    onOpenDetailRef?: (target: CollaborationMicroStageDetailTarget) => void;
    overviewLinkLabel?: string;
    onOpenOverview?: () => void;
}

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

type SupervisorAction = "idle" | "walk" | "summon" | "command" | "read" | "type" | "receive" | "celebrate";
type SupervisorDisplayAction = SupervisorAction | "turn";
type SupervisorSceneMode = "entering" | "working" | "handoff" | "celebrating" | "warning";

const MAX_STAGE_ACTORS = 10;
const STAGE_HEIGHTS: Record<CollaborationMicroStageLayout, number> = {
    singleRow: 176,
    officeGrid: 244,
    clusteredGrid: 218,
};
const WORK_CELL_WIDTH = 98;
const WORK_CELL_HEIGHT = 106;
const SUPERVISOR_BASE_TOP = 18;
const SUPERVISOR_LAYER_WIDTH = 136;
const SUPERVISOR_CENTER_X = SUPERVISOR_LAYER_WIDTH / 2;
const SUPERVISOR_SPRITE_SRC = "/supervisor_spritesheet.png";
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
const ENTER_DURATION_MS = 1100;
const HANDOFF_DURATION_MS = 1600;
const FINAL_FEEDBACK_DURATION_MS = 5000;
const EXIT_DURATION_MS = 700;
const FINAL_REPLAY_WINDOW_MS = 12_000;

function readLatestStep(stage: CollaborationMicroStage) {
    return stage.steps[stage.steps.length - 1];
}

function latestActorStep(stage: CollaborationMicroStage, actor: CollaborationMicroStageActor) {
    for (let index = stage.steps.length - 1; index >= 0; index -= 1) {
        const step = stage.steps[index];
        if (actor.stepIds.includes(step.id) || actor.sourceActivityIds.includes(step.sourceActivityId)) {
            return step;
        }
    }
    return readLatestStep(stage);
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
    if (isFinalStatus(stage.status)) {
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
        const timer = window.setTimeout(() => {
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
        }, 0);
        return () => window.clearTimeout(timer);
    }, [executionActive, stages]);

    useEffect(() => {
        const expiring = retained
            .map((stage) => stage.phaseUntil)
            .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
        if (expiring.length === 0) return undefined;
        const delay = Math.max(80, Math.min(...expiring) - Date.now());
        const timer = window.setTimeout(() => {
            const tick = Date.now();
            setRetained((current) => current.flatMap((stage) => {
                if (!stage.phaseUntil || stage.phaseUntil > tick) return [stage];
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
        return () => window.clearTimeout(timer);
    }, [retained]);

    return { initialized, retained, settledOutcome };
}

function buildStageActorItems(stages: RetainedMicroStage[]): StageActorItem[] {
    const items: StageActorItem[] = [];
    stages.forEach((stage, stageIndex) => {
        const fallbackStep = readLatestStep(stage);
        const actors = stage.actors.length > 0
            ? stage.actors
            : [{
                id: `${stage.id}:actor`,
                kind: stage.kind,
                label: fallbackStep?.actorLabel || stage.title,
                status: stage.status,
                cue: stage.cue,
                summary: fallbackStep?.summary || stage.subtitle,
                timestamp: stage.timestamp,
                detailRef: fallbackStep?.detailRef,
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

function useElementWidth<T extends HTMLElement>() {
    const ref = useRef<T | null>(null);
    const [width, setWidth] = useState(0);

    useEffect(() => {
        const element = ref.current;
        if (!element) return undefined;
        const update = () => setWidth(element.getBoundingClientRect().width || 0);
        update();
        const observer = new ResizeObserver(update);
        observer.observe(element);
        return () => observer.disconnect();
    }, []);

    return [ref, width] as const;
}

function stageColor(stage: CollaborationMicroStage, index: number) {
    if (stage.kind === "subagent") return ["#8B5CF6", "#D946EF", "#A855F7", "#6366F1"][index % 4];
    const runtimeId = (stage.runtimeId || "").toLowerCase();
    if (runtimeId.includes("research")) return "#0EA5E9";
    if (runtimeId.includes("engineering")) return "#14B8A6";
    if (runtimeId.includes("creative")) return "#EC4899";
    if (runtimeId.includes("computer") || runtimeId.includes("desktop")) return "#6366F1";
    if (runtimeId.includes("rpa")) return "#F97316";
    return "#38BDF8";
}

function statusLabel(status: CollaborationMicroStageStatus) {
    const labels: Record<CollaborationMicroStageStatus, string> = {
        active: "运行中",
        attempted: "已尝试",
        completed: "完成",
        degraded: "降级",
        failed: "失败",
        pending: "等待",
    };
    return labels[status] || status;
}

function screenIcon(cue: CollaborationMicroStageCue, status: CollaborationMicroStageStatus) {
    if (status === "failed") return "!";
    if (status === "completed") return "✓";
    if (status === "degraded") return "△";
    if (cue === "research") return "⌕";
    if (cue === "engineering") return "</>";
    if (cue === "creative") return "✦";
    if (cue === "desktop") return "▣";
    if (cue === "rpa") return "↻";
    if (cue === "handoff") return "⇢";
    if (cue === "child_agent" || cue === "dispatch" || cue === "summon") return "⟡";
    return "…";
}

function positionStageActorItems(items: StageActorItem[], layout: CollaborationMicroStageLayout, width: number): PositionedStageActorItem[] {
    const canvasWidth = Math.max(300, width || 360);
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
            y: 32,
            scale: count >= 3 ? 0.88 : 0.96,
        }));
    }

    if (layout === "officeGrid") {
        const columns = Math.min(3, Math.max(2, items.length));
        const cellWidth = canvasWidth / columns;
        return items.map((item, index) => {
            const column = index % columns;
            const row = Math.floor(index / columns);
            const scale = 0.8;
            return {
                ...item,
                x: column * cellWidth + Math.max(0, (cellWidth - WORK_CELL_WIDTH * scale) / 2),
                y: 28 + row * 90,
                scale,
            };
        });
    }

    const columns = Math.min(5, Math.max(4, items.length));
    const cellWidth = canvasWidth / columns;
    const scale = Math.max(0.52, Math.min(0.66, (cellWidth - 2) / WORK_CELL_WIDTH));
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
    return !isFinalStatus(item.actor.status)
        && item.stage.renderPhase !== "exiting";
}

function supervisorWaypointForItem(item: PositionedStageActorItem, width: number) {
    const canvasWidth = Math.max(300, width || 360);
    return {
        x: clamp(item.x - 94, 12, canvasWidth - SUPERVISOR_LAYER_WIDTH - 10),
        y: Math.max(0, item.y - 32),
    };
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

function useSupervisorPatrol(items: PositionedStageActorItem[], width: number, mode: SupervisorSceneMode) {
    const [waypointIndex, setWaypointIndex] = useState(0);
    const [moving, setMoving] = useState(false);
    const previousX = useRef(24);
    const unfinishedItems = useMemo(() => items.filter(actorIsUnfinished), [items]);
    const workingWaypoints = useMemo(() => {
        const candidates = unfinishedItems.length > 0 ? unfinishedItems : items;
        const seen = new Set<string>();
        return candidates.flatMap((item) => {
            const waypoint = supervisorWaypointForItem(item, width);
            const key = `${Math.round(waypoint.x)}:${Math.round(waypoint.y)}`;
            if (seen.has(key)) return [];
            seen.add(key);
            return [waypoint];
        });
    }, [items, unfinishedItems, width]);
    const focusItem = useMemo(() => {
        if (mode === "entering") return unfinishedItems[0] || items[0];
        return items.find((item) => (
            item.stage.renderPhase === mode
            || (mode === "warning" && (item.actor.status === "failed" || item.actor.status === "degraded"))
        )) || items[0];
    }, [items, mode, unfinishedItems]);
    const focusPosition = focusItem
        ? supervisorWaypointForItem(focusItem, width)
        : { x: 24, y: 0 };
    const waypointSignature = workingWaypoints.map((point) => `${Math.round(point.x)}:${Math.round(point.y)}`).join("|");

    useEffect(() => {
        setWaypointIndex(0);
    }, [waypointSignature]);

    useEffect(() => {
        if (mode !== "working" || workingWaypoints.length === 0) {
            setMoving(false);
            return undefined;
        }
        setMoving(true);
        const settleTimer = window.setTimeout(() => setMoving(false), 1250);
        if (workingWaypoints.length === 1) {
            return () => window.clearTimeout(settleTimer);
        }
        const timer = window.setInterval(() => {
            setMoving(true);
            setWaypointIndex((current) => current + 1);
        }, 3600);
        return () => {
            window.clearTimeout(settleTimer);
            window.clearInterval(timer);
        };
    }, [mode, waypointSignature, workingWaypoints.length]);

    useEffect(() => {
        if (mode !== "working" || !moving) return undefined;
        const timer = window.setTimeout(() => setMoving(false), 1250);
        return () => window.clearTimeout(timer);
    }, [mode, moving, waypointIndex]);

    const safeIndex = workingWaypoints.length > 0 ? waypointIndex % workingWaypoints.length : 0;
    const position = mode === "working"
        ? (workingWaypoints[safeIndex] || focusPosition)
        : focusPosition;
    const facingLeft = position.x < previousX.current;
    useEffect(() => {
        previousX.current = position.x;
    }, [position.x]);
    return { position, facingLeft, moving };
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
        const timer = window.setTimeout(() => {
            setDisplayState({ action, facingLeft });
        }, TURN_BRIDGE_DURATION_MS);
        return () => window.clearTimeout(timer);
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
        let timer: number | undefined;
        let current = 0;
        setFrameIndex(0);
        if (frames.length <= 1) return undefined;

        const scheduleNext = () => {
            timer = window.setTimeout(() => {
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
            if (timer !== undefined) window.clearTimeout(timer);
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
        <div
            className="absolute bottom-0 left-0 overflow-hidden"
            data-supervisor-action={displayState.action}
            data-supervisor-facing={displayState.facingLeft ? "left" : "right"}
            style={{
                width: SUPERVISOR_SHEET.frameWidth,
                height: SUPERVISOR_SHEET.frameHeight,
                transform: mirrorWalkFrame ? "scaleX(-1)" : undefined,
            }}
        >
            <div
                aria-hidden="true"
                className="absolute left-0 top-0 bg-no-repeat"
                style={{
                    backgroundImage: `url(${SUPERVISOR_SPRITE_SRC})`,
                    backgroundPosition: `-${column * SUPERVISOR_SHEET.frameWidth}px -${row * SUPERVISOR_SHEET.frameHeight}px`,
                    backgroundSize: `${SUPERVISOR_SHEET.frameWidth * SUPERVISOR_SHEET.columns}px ${SUPERVISOR_SHEET.frameHeight * SUPERVISOR_SHEET.rows}px`,
                    width: SUPERVISOR_SHEET.frameWidth * SUPERVISOR_SHEET.columns,
                    height: SUPERVISOR_SHEET.frameHeight * SUPERVISOR_SHEET.rows,
                    imageRendering: "auto",
                }}
            />
        </div>
    );
}

function SupervisorAvatar({
    speech,
    stageWidth,
    x,
    y,
    facingLeft,
    action,
}: {
    speech?: string;
    stageWidth: number;
    x: number;
    y: number;
    facingLeft: boolean;
    action: SupervisorAction;
}) {
    const speechWidth = Math.min(240, Math.max(168, stageWidth - 24));
    const idealSpeechLeft = SUPERVISOR_CENTER_X - speechWidth / 2;
    const speechLeft = clamp(
        idealSpeechLeft,
        12 - x,
        stageWidth - 12 - x - speechWidth,
    );
    const speechTailLeft = clamp(SUPERVISOR_CENTER_X - speechLeft - 5, 16, speechWidth - 26);
    return (
        <div
            className="absolute z-30 h-[136px] w-[136px] transition-transform [transition-duration:1250ms] ease-in-out"
            style={{ transform: `translate(${x}px, ${SUPERVISOR_BASE_TOP + y}px)` }}
        >
            {speech ? (
                <div
                    className="absolute bottom-[110px] rounded-2xl border border-border/70 bg-background/94 px-3 py-2 text-[11px] leading-[1.45] text-foreground shadow-md backdrop-blur"
                    style={{ left: speechLeft, width: speechWidth }}
                >
                    <span className="line-clamp-3">{speech}</span>
                    <span
                        aria-hidden="true"
                        className="absolute -bottom-[5px] h-2.5 w-2.5 rotate-45 border-b border-r border-border/70 bg-background/94"
                        style={{ left: speechTailLeft }}
                    />
                </div>
            ) : null}
            <div className="absolute bottom-1 left-[34px] h-2 w-[68px] rounded-full bg-slate-900/15 blur-[1px] dark:bg-black/35" />
            <SupervisorSprite action={action} facingLeft={facingLeft} />
        </div>
    );
}

function WorkCell({
    item,
    index,
    supervisor,
    onOpenDetailRef,
}: {
    item: PositionedStageActorItem;
    index: number;
    supervisor: { x: number; y: number };
    onOpenDetailRef?: (target: CollaborationMicroStageDetailTarget) => void;
}) {
    const { stage, actor, x, y, scale } = item;
    const step = latestActorStep(stage, actor);
    const color = stageColor(stage, index);
    const cue = actor.cue || step?.cue || stage.cue;
    const status = actor.status || stage.status;
    const active = status === "active" && stage.renderPhase !== "exiting";
    const handoff = isFinalStatus(status) || cue === "handoff" || cue === "completed";
    const detailRef = actor.detailRef || step?.detailRef;
    const targetX = supervisor.x + SUPERVISOR_CENTER_X - x;
    const targetY = SUPERVISOR_BASE_TOP + supervisor.y + 55 - y - 48;
    const opacity = stage.renderPhase === "exiting" ? 0 : 1;
    const handleOpen = () => {
        if (detailRef && step && onOpenDetailRef) {
            onOpenDetailRef({ detailRef, stage, step, actor });
        }
    };

    return (
        <div
            className="absolute z-20 transition-[opacity,transform] duration-700 ease-out"
            style={{
                left: x,
                top: y,
                width: WORK_CELL_WIDTH,
                height: WORK_CELL_HEIGHT,
                opacity,
                transform: `scale(${scale})`,
                transformOrigin: "top left",
            }}
        >
            {(stage.renderPhase === "entering" || cue === "summon") ? (
                <div className="absolute left-4 top-[58px] h-8 w-16 animate-[microStagePortal_1.2s_ease-out_forwards] rounded-[50%] border border-dashed" style={{ borderColor: color, backgroundColor: `${color}18` }} />
            ) : null}
            <div className="absolute left-1 top-3 h-[50px] w-[88px]">
                <div className="absolute left-3 top-0 h-9 w-16 rounded-md border border-slate-500/40 bg-slate-950 shadow-sm">
                    <div className="absolute inset-[3px] overflow-hidden rounded bg-slate-900" style={{ boxShadow: `inset 0 0 18px ${color}22` }}>
                        <div className="absolute inset-x-1 top-1 h-px bg-white/15" />
                        <div className="absolute inset-x-2 bottom-1 h-px bg-white/10" />
                        <div className="absolute inset-0 grid place-items-center text-[13px] font-black" style={{ color }}>
                            {screenIcon(cue, status)}
                        </div>
                        {active ? <div className="absolute top-0 h-full w-4 animate-[microStageScan_1.1s_linear_infinite] bg-white/10" /> : null}
                    </div>
                </div>
                <div className="absolute left-[39px] top-9 h-3 w-2 rounded-sm bg-slate-400/80" />
                <div className="absolute left-0 top-[43px] h-2 w-[88px] rounded-full border border-slate-300/50 bg-gradient-to-r from-white via-slate-100 to-slate-300 shadow-sm dark:border-slate-500/40 dark:from-slate-200 dark:to-slate-500" />
                <div className="absolute left-4 top-[48px] h-11 w-7 rounded-b-xl border border-slate-300/60 bg-white/80 dark:border-slate-500/40 dark:bg-slate-300/80" />
                <div className="absolute right-2 top-[48px] h-9 w-6 rounded-sm border border-slate-300/60 bg-slate-100/90 dark:border-slate-500/40 dark:bg-slate-400/80" />
            </div>
            <div
                className={cn(
                    "absolute left-7 top-[54px] h-10 w-10 transition-transform [transition-duration:1320ms] ease-out",
                    active && "animate-[microStageBotBob_1s_ease-in-out_infinite]",
                )}
                style={{
                    transform: handoff ? `translate(${targetX}px, ${targetY}px)` : "translate(0, 0)",
                }}
            >
                <div className="absolute left-1 top-1 h-5 w-8 rounded-[10px] border border-slate-600/40 bg-gradient-to-b from-white to-slate-300 shadow-sm">
                    <span className="absolute left-2 top-2 h-1.5 w-1.5 rounded-full bg-slate-950" />
                    <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-slate-950" />
                    <span className="absolute bottom-1 left-3 h-1 w-4 rounded-full" style={{ backgroundColor: color }} />
                </div>
                <div className="absolute left-0 top-[19px] h-5 w-10 rounded-t-lg border border-slate-600/40 bg-gradient-to-b from-slate-100 to-slate-400" />
                <div className="absolute bottom-0 left-1 h-2.5 w-8 rounded-full bg-slate-900 shadow-inner">
                    <span className="absolute left-1 top-1 h-1 w-1 rounded-full bg-slate-200/80" />
                    <span className="absolute left-[14px] top-1 h-1 w-1 rounded-full bg-slate-200/70" />
                    <span className="absolute right-1 top-1 h-1 w-1 rounded-full bg-slate-200/80" />
                </div>
            </div>
            {handoff ? (
                <div
                    className="absolute left-10 top-[49px] h-5 w-4 rounded border border-slate-400/70 bg-white shadow-sm transition-transform [transition-duration:1320ms] ease-out"
                    style={{ transform: `translate(${targetX + 10}px, ${targetY - 6}px)` }}
                >
                    <span className="absolute left-1 top-1 h-px w-2 bg-slate-400" />
                    <span className="absolute left-1 top-2.5 h-px w-2 bg-slate-300" />
                </div>
            ) : null}
            <button
                type="button"
                disabled={!detailRef}
                onClick={handleOpen}
                className={cn(
                    "absolute left-0 top-[82px] flex max-w-[96px] items-center gap-1 rounded-full border bg-background/85 px-2 py-1 text-[10px] leading-none shadow-sm backdrop-blur transition hover:bg-background",
                    !detailRef && "cursor-default",
                )}
                style={{ borderColor: `${color}66` }}
                title={actor.summary || step?.summary || stage.title}
            >
                <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: color }} />
                <span className="min-w-0 truncate font-semibold text-foreground/85">{actor.label || stage.title}</span>
                <span className="text-muted-foreground">{statusLabel(status)}</span>
            </button>
        </div>
    );
}

export const CollaborationMicroStageScene = memo(function CollaborationMicroStageScene({
    stages,
    executionActive = false,
    supervisorSpeech,
    onOpenDetailRef,
    overviewLinkLabel = "查看概览",
    onOpenOverview,
}: CollaborationMicroStageSceneProps) {
    const { initialized, retained: retainedStages, settledOutcome } = useRetainedMicroStages(stages, executionActive);
    const actorItems = useMemo(() => buildStageActorItems(retainedStages), [retainedStages]);
    const layout = useMemo(() => selectCollaborationMicroStageLayout(retainedStages), [retainedStages]);
    const [rootRef, width] = useElementWidth<HTMLDivElement>();
    const positionedItems = useMemo(() => positionStageActorItems(actorItems, layout, width), [actorItems, layout, width]);
    const sceneMode = useMemo(() => sceneModeForStages(retainedStages), [retainedStages]);
    const { position, facingLeft, moving } = useSupervisorPatrol(positionedItems, width, sceneMode);
    const action = useMemo(
        () => supervisorActionForScene(sceneMode, moving, actorItems),
        [actorItems, moving, sceneMode],
    );
    const allExiting = retainedStages.every((stage) => stage.renderPhase === "exiting");
    const height = STAGE_HEIGHTS[layout];
    const hasFinalOutcome = settledOutcome || stages.some((stage) => isFinalStatus(stage.status));

    if (retainedStages.length === 0 || positionedItems.length === 0) {
        if (initialized && hasFinalOutcome && onOpenOverview) {
            return (
                <div className="my-1.5 flex min-h-10 items-center px-1">
                    <button
                        type="button"
                        onClick={onOpenOverview}
                        className="group inline-flex items-center gap-1.5 text-xs font-medium text-muted-foreground transition-colors duration-200 hover:text-foreground focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
                    >
                        <span className="underline decoration-border underline-offset-4 group-hover:decoration-current">{overviewLinkLabel}</span>
                        <span aria-hidden="true" className="transition-transform duration-200 group-hover:translate-x-0.5">→</span>
                    </button>
                </div>
            );
        }
        return null;
    }

    return (
        <div
            ref={rootRef}
            className={cn(
                "relative my-1.5 w-full overflow-hidden rounded-[22px] border border-white/30 bg-white/35 shadow-inner backdrop-blur-md transition-opacity duration-700 dark:border-white/10 dark:bg-white/[0.03]",
                allExiting && "opacity-0",
            )}
            style={{ height }}
            data-collaboration-layout={layout}
            data-collaboration-phase={sceneMode}
        >
            <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_right,rgba(148,163,184,0.10)_1px,transparent_1px),linear-gradient(to_bottom,rgba(148,163,184,0.10)_1px,transparent_1px)] bg-[size:42px_42px] opacity-60" />
            <div className="pointer-events-none absolute inset-x-0 bottom-5 h-px bg-border/40" />
            <div className="pointer-events-none absolute inset-x-8 bottom-3 h-px bg-border/25" />
            <SupervisorAvatar
                speech={supervisorSpeech}
                stageWidth={width}
                x={position.x}
                y={position.y}
                facingLeft={facingLeft}
                action={action}
            />
            {positionedItems.map((item, index) => (
                <WorkCell
                    key={item.id}
                    item={item}
                    index={index}
                    supervisor={position}
                    onOpenDetailRef={onOpenDetailRef}
                />
            ))}
            <style jsx>{`
                @keyframes microStagePortal {
                    0% { opacity: 0; transform: scale(0.65) rotate(0deg); }
                    42% { opacity: 1; transform: scale(1.05) rotate(18deg); }
                    100% { opacity: 0; transform: scale(1.15) rotate(38deg); }
                }
                @keyframes microStageScan {
                    0% { transform: translateX(-22px); opacity: 0; }
                    20% { opacity: 0.7; }
                    100% { transform: translateX(58px); opacity: 0; }
                }
                @keyframes microStageBotBob {
                    0%, 100% { margin-top: 0; }
                    50% { margin-top: -2px; }
                }
            `}</style>
        </div>
    );
});

CollaborationMicroStageScene.displayName = "CollaborationMicroStageScene";
