"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import type {
    CollaborationMicroStage,
    CollaborationMicroStageActor,
    CollaborationMicroStageLayout,
    CollaborationMicroStageStatus,
    CollaborationMicroStageStep,
} from "@v8/session-realtime";
import { selectCollaborationMicroStageLayout } from "@v8/session-realtime";
import { useT } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";
import {
    SubagentRobotSprite,
    WorkstationDisplay,
    subagentRobotActionFor,
} from "./SubagentWorkstation";

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

type CollisionRect = { left: number; top: number; width: number; height: number };
type CollisionVolume = CollisionRect;
type SupervisorWaypoint = { x: number; y: number; targetCenterX: number };

type SupervisorAction = "idle" | "walk" | "summon" | "command" | "read" | "type" | "receive" | "celebrate" | "inspect";
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
const COLLISION_GAP = 8;
const SUPERVISOR_COLLISION: CollisionVolume = { left: 43, top: 34, width: 50, height: 92 };
const WORKSTATION_COLLISION: CollisionVolume = { left: 2, top: 6, width: 76, height: 91 };
const ROBOT_COLLISION: CollisionVolume = { left: 62, top: 42, width: 29, height: 51 };
const SUPERVISOR_SPRITE_SRC = "/supervisor_spritesheet.png";
const SUPERVISOR_FAREWELL_SPRITE_SRC = "/supervisor_farewell_spritesheet.png";
const SUPERVISOR_SHEET = {
    columns: 7,
    rows: 6,
    frameWidth: 128,
    frameHeight: 128,
};
const SUPERVISOR_FAREWELL_SHEET = {
    columns: 8,
    rows: 3,
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
    celebrate: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23],
    inspect: [35, 36, 37, 38],
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
    celebrate: [
        120, 120, 120, 120, 120, 120, 120, 120,
        130, 130, 130, 130, 130, 130, 130, 130, 130, 130,
        150, 150, 150, 150,
        180, 1960,
    ],
    inspect: [560, 900, 1000, 720],
};
const LOOPING_SUPERVISOR_ACTIONS = new Set<SupervisorAction>(["idle", "walk", "command", "type", "inspect"]);
const TURN_BRIDGE_DURATION_MS = 180;
const SUPERVISOR_TURN_DURATIONS = [TURN_BRIDGE_DURATION_MS] as const;
const SUPERVISOR_TRAVEL_DURATION_MS = 1400;
const PATROL_INTERVAL_MS = 5200;
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

function stageDepthZ(groundY: number) {
    return 40 + Math.round(groundY);
}

function isDirectionalSupervisorAction(action: SupervisorAction) {
    return action === "walk" || action === "inspect";
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
        }
        if (!executionActive && executionWasActive.current) {
            executionWasActive.current = false;
            settlementLocked.current = true;
        }
        const shouldSettleIncomplete = settlementLocked.current;
        const effectiveStages = shouldSettleIncomplete
            ? stages.map(settleIncompleteStage)
            : stages;
        const hasSettledFinal = shouldSettleIncomplete
            && effectiveStages.some((stage) => isFinalStatus(stage.status));
        const now = Date.now();
        const timer = window.setTimeout(() => {
            if (executionActive && !settlementLocked.current) setSettledOutcome(false);
            if (hasSettledFinal) setSettledOutcome(true);
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
    const [element, setElement] = useState<T | null>(null);
    const [width, setWidth] = useState(0);
    const ref = useCallback((node: T | null) => setElement(node), []);

    useEffect(() => {
        if (!element) return undefined;
        const update = () => setWidth(element.getBoundingClientRect().width || 0);
        update();
        const observer = new ResizeObserver(update);
        observer.observe(element);
        return () => observer.disconnect();
    }, [element]);

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

function statusLabel(status: CollaborationMicroStageStatus, t: (key: string) => string) {
    const labels: Record<CollaborationMicroStageStatus, string> = {
        active: t("web.collaborationMicroStage.status.active"),
        attempted: t("web.collaborationMicroStage.status.attempted"),
        completed: t("web.collaborationMicroStage.status.completed"),
        degraded: t("web.collaborationMicroStage.status.degraded"),
        failed: t("web.collaborationMicroStage.status.failed"),
        pending: t("web.collaborationMicroStage.status.pending"),
    };
    return labels[status] || status;
}

function statusIndicatorTone(status: CollaborationMicroStageStatus, familyColor: string) {
    if (status === "completed") return "#34D399";
    if (status === "failed") return "#FB7185";
    if (status === "degraded" || status === "attempted" || status === "pending") return "#FBBF24";
    return familyColor;
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

function scaleCollisionVolume(item: PositionedStageActorItem, volume: CollisionVolume): CollisionRect {
    return {
        left: item.x + volume.left * item.scale,
        top: item.y + volume.top * item.scale,
        width: volume.width * item.scale,
        height: volume.height * item.scale,
    };
}

function collisionRectsForItem(item: PositionedStageActorItem): CollisionRect[] {
    return [
        scaleCollisionVolume(item, WORKSTATION_COLLISION),
        scaleCollisionVolume(item, ROBOT_COLLISION),
    ];
}

function supervisorCollisionAt(point: { x: number; y: number }): CollisionRect {
    return {
        left: point.x + SUPERVISOR_COLLISION.left,
        top: SUPERVISOR_BASE_TOP + point.y + SUPERVISOR_COLLISION.top,
        width: SUPERVISOR_COLLISION.width,
        height: SUPERVISOR_COLLISION.height,
    };
}

function expandCollisionRect(rect: CollisionRect, gap: number): CollisionRect {
    return {
        left: rect.left - gap,
        top: rect.top - gap,
        width: rect.width + gap * 2,
        height: rect.height + gap * 2,
    };
}

function collisionOverlapArea(first: CollisionRect, second: CollisionRect) {
    const width = Math.max(0, Math.min(first.left + first.width, second.left + second.width) - Math.max(first.left, second.left));
    const height = Math.max(0, Math.min(first.top + first.height, second.top + second.height) - Math.max(first.top, second.top));
    return width * height;
}

function actorIsUnfinished(item: PositionedStageActorItem) {
    return !isFinalStatus(item.actor.status)
        && item.stage.renderPhase !== "exiting";
}

function supervisorWaypointForItem(
    item: PositionedStageActorItem,
    width: number,
    allItems: PositionedStageActorItem[] = [item],
): SupervisorWaypoint {
    const canvasWidth = Math.max(300, width || 360);
    const maxX = canvasWidth - SUPERVISOR_LAYER_WIDTH - 10;
    const y = Math.max(0, item.y - 32);
    const targetRects = collisionRectsForItem(item);
    const targetLeft = Math.min(...targetRects.map((rect) => rect.left));
    const targetRight = Math.max(...targetRects.map((rect) => rect.left + rect.width));
    const targetCenterX = item.x + WORK_CELL_WIDTH * item.scale / 2;
    const rawCandidates = [
        targetLeft - COLLISION_GAP - SUPERVISOR_COLLISION.left - SUPERVISOR_COLLISION.width,
        targetRight + COLLISION_GAP - SUPERVISOR_COLLISION.left,
        12,
        maxX,
    ];
    const candidates = rawCandidates.reduce<Array<{ x: number; y: number }>>((result, x) => {
        const candidate = { x: clamp(x, 12, maxX), y };
        if (!result.some((existing) => Math.abs(existing.x - candidate.x) < 0.5)) result.push(candidate);
        return result;
    }, []);
    const obstacles = allItems
        .filter((candidate) => candidate.stage.renderPhase !== "exiting")
        .flatMap(collisionRectsForItem)
        .map((rect) => expandCollisionRect(rect, COLLISION_GAP));
    const safeCandidate = candidates.find((candidate) => (
        obstacles.every((obstacle) => collisionOverlapArea(supervisorCollisionAt(candidate), obstacle) === 0)
    ));
    if (safeCandidate) return { ...safeCandidate, targetCenterX };
    const selected = candidates.reduce((best, candidate) => {
        const penalty = obstacles.reduce(
            (sum, obstacle) => sum + collisionOverlapArea(supervisorCollisionAt(candidate), obstacle),
            0,
        );
        return penalty < best.penalty ? { candidate, penalty } : best;
    }, { candidate: candidates[0] || { x: 12, y }, penalty: Number.POSITIVE_INFINITY }).candidate;
    return { ...selected, targetCenterX };
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
    const [waypointState, setWaypointState] = useState({ signature: "", index: 0 });
    const [moving, setMoving] = useState(false);
    const unfinishedItems = useMemo(() => items.filter(actorIsUnfinished), [items]);
    const workingWaypoints = useMemo(() => {
        const candidates = unfinishedItems.length > 0 ? unfinishedItems : items;
        const seen = new Set<string>();
        return candidates.flatMap((item) => {
            const waypoint = supervisorWaypointForItem(item, width, items);
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
        ? supervisorWaypointForItem(focusItem, width, items)
        : { x: 24, y: 0, targetCenterX: 24 };
    const waypointSignature = workingWaypoints.map((point) => `${Math.round(point.x)}:${Math.round(point.y)}`).join("|");
    const waypointIndex = waypointState.signature === waypointSignature ? waypointState.index : 0;

    useEffect(() => {
        if (mode !== "working" || workingWaypoints.length === 0) {
            const stopTimer = window.setTimeout(() => setMoving(false), 0);
            return () => window.clearTimeout(stopTimer);
        }
        const startTimer = window.setTimeout(() => setMoving(true), 0);
        const settleTimer = window.setTimeout(() => setMoving(false), SUPERVISOR_TRAVEL_DURATION_MS);
        if (workingWaypoints.length === 1) {
            return () => {
                window.clearTimeout(startTimer);
                window.clearTimeout(settleTimer);
            };
        }
        const timer = window.setInterval(() => {
            setMoving(true);
            setWaypointState((current) => ({
                signature: waypointSignature,
                index: (current.signature === waypointSignature ? current.index : 0) + 1,
            }));
        }, PATROL_INTERVAL_MS);
        return () => {
            window.clearTimeout(startTimer);
            window.clearTimeout(settleTimer);
            window.clearInterval(timer);
        };
    }, [mode, waypointSignature, workingWaypoints.length]);

    useEffect(() => {
        if (mode !== "working" || !moving) return undefined;
        const timer = window.setTimeout(() => setMoving(false), SUPERVISOR_TRAVEL_DURATION_MS);
        return () => window.clearTimeout(timer);
    }, [mode, moving, waypointIndex]);

    const safeIndex = workingWaypoints.length > 0 ? waypointIndex % workingWaypoints.length : 0;
    const position = mode === "working"
        ? (workingWaypoints[safeIndex] || focusPosition)
        : focusPosition;
    const previousWaypoint = workingWaypoints.length > 1
        ? workingWaypoints[(safeIndex - 1 + workingWaypoints.length) % workingWaypoints.length]
        : position;
    const facingLeft = moving && workingWaypoints.length > 1
        ? position.x < previousWaypoint.x
        : position.x + SUPERVISOR_CENTER_X > position.targetCenterX;
    return { position, facingLeft, moving };
}

function supervisorActionForScene(mode: SupervisorSceneMode, moving: boolean, items: StageActorItem[]): SupervisorAction {
    if (mode === "entering") return "summon";
    if (mode === "handoff") return "receive";
    if (mode === "celebrating") return "celebrate";
    if (mode === "warning") return "read";
    if (moving) return "walk";
    if (items.some((item) => (
        item.actor.status === "active"
        || item.actor.status === "pending"
        || item.actor.status === "attempted"
    ))) return "inspect";
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
        const crossesDirectionalBoundary = isDirectionalSupervisorAction(previous.action)
            !== isDirectionalSupervisorAction(action);
        const needsTurnBridge = crossesDirectionalBoundary;
        if (!needsTurnBridge) {
            const applyTimer = window.setTimeout(() => setDisplayState({ action, facingLeft }), 0);
            return () => window.clearTimeout(applyTimer);
        }

        const turnTimer = window.setTimeout(() => setDisplayState({ action: "turn", facingLeft }), 0);
        const actionTimer = window.setTimeout(() => {
            setDisplayState({ action, facingLeft });
        }, TURN_BRIDGE_DURATION_MS);
        return () => {
            window.clearTimeout(turnTimer);
            window.clearTimeout(actionTimer);
        };
    }, [action, facingLeft]);

    return displayState;
}

function useSupervisorFrame(action: SupervisorDisplayAction, facingLeft: boolean) {
    const frames = action === "turn"
        ? (facingLeft ? SUPERVISOR_TURN_FRAMES.left : SUPERVISOR_TURN_FRAMES.right)
        : SUPERVISOR_ACTION_FRAMES[action];
    const durations = action === "turn" ? SUPERVISOR_TURN_DURATIONS : SUPERVISOR_ACTION_DURATIONS[action];
    const loops = action !== "turn" && LOOPING_SUPERVISOR_ACTIONS.has(action);
    const [frameState, setFrameState] = useState<{ action: SupervisorDisplayAction; index: number }>({
        action,
        index: 0,
    });
    const frameIndex = frameState.action === action ? frameState.index : 0;

    useEffect(() => {
        let cancelled = false;
        let timer: number | undefined;
        let current = 0;
        if (frames.length <= 1) return undefined;

        const scheduleNext = () => {
            timer = window.setTimeout(() => {
                if (cancelled) return;
                if (!loops && current >= frames.length - 1) return;
                current = (current + 1) % frames.length;
                setFrameState({ action, index: current });
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
    const sheet = displayState.action === "celebrate" ? SUPERVISOR_FAREWELL_SHEET : SUPERVISOR_SHEET;
    const spriteSource = displayState.action === "celebrate"
        ? SUPERVISOR_FAREWELL_SPRITE_SRC
        : SUPERVISOR_SPRITE_SRC;
    const column = frame % sheet.columns;
    const row = Math.floor(frame / sheet.columns);
    const mirrorDirectionalFrame = (displayState.action === "walk" || displayState.action === "inspect")
        && !displayState.facingLeft;

    return (
        <div
            className="absolute bottom-0 left-0 overflow-hidden"
            data-supervisor-action={displayState.action}
            data-supervisor-facing={displayState.facingLeft ? "left" : "right"}
            style={{
                width: sheet.frameWidth,
                height: sheet.frameHeight,
                transform: mirrorDirectionalFrame ? "scaleX(-1)" : undefined,
            }}
        >
            <div
                aria-hidden="true"
                className="absolute left-0 top-0 bg-no-repeat"
                style={{
                    backgroundImage: `url(${spriteSource})`,
                    backgroundPosition: `-${column * sheet.frameWidth}px -${row * sheet.frameHeight}px`,
                    backgroundSize: `${sheet.frameWidth * sheet.columns}px ${sheet.frameHeight * sheet.rows}px`,
                    width: sheet.frameWidth * sheet.columns,
                    height: sheet.frameHeight * sheet.rows,
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
    targetCenterX,
    facingLeft,
    action,
}: {
    speech?: string;
    stageWidth: number;
    x: number;
    y: number;
    targetCenterX: number;
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
            className="absolute h-[136px] w-[136px] transition-transform will-change-transform"
            data-collision-supervisor={`${SUPERVISOR_COLLISION.left},${SUPERVISOR_COLLISION.top},${SUPERVISOR_COLLISION.width},${SUPERVISOR_COLLISION.height}`}
            data-stage-depth={stageDepthZ(SUPERVISOR_BASE_TOP + y + SUPERVISOR_SHEET.frameHeight - 8)}
            data-supervisor-target-center-x={targetCenterX}
            style={{
                transform: `translate(${x}px, ${SUPERVISOR_BASE_TOP + y}px)`,
                transitionDuration: `${SUPERVISOR_TRAVEL_DURATION_MS}ms`,
                transitionTimingFunction: "cubic-bezier(0.22, 0.74, 0.24, 1)",
                zIndex: stageDepthZ(SUPERVISOR_BASE_TOP + y + SUPERVISOR_SHEET.frameHeight - 8),
            }}
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
    const t = useT();
    const { stage, actor, x, y, scale } = item;
    const step = latestActorStep(stage, actor);
    const color = stageColor(stage, index);
    const cue = actor.cue || step?.cue || stage.cue;
    const status = actor.status || stage.status;
    const statusTone = statusIndicatorTone(status, color);
    const handoff = stage.renderPhase === "handoff" || cue === "handoff";
    const curtain = stage.renderPhase === "celebrating" && status === "completed";
    const robotAction = subagentRobotActionFor({ cue, status, phase: stage.renderPhase });
    const detailRef = actor.detailRef || step?.detailRef;
    const actorName = actor.label || step?.actorLabel || stage.title;
    const reportOriginX = 68 + 8;
    const reportOriginY = 47 + 10;
    const targetX = supervisor.x + SUPERVISOR_CENTER_X - x - reportOriginX;
    const targetY = SUPERVISOR_BASE_TOP + supervisor.y + 55 - y - reportOriginY;
    const opacity = stage.renderPhase === "exiting" ? 0 : 1;
    const handleOpen = () => {
        if (detailRef && step && onOpenDetailRef) {
            onOpenDetailRef({ detailRef, stage, step, actor });
        }
    };

    return (
        <div
            className="absolute animate-[microStageCellEnter_620ms_cubic-bezier(0.22,0.74,0.24,1)_both] transition-[opacity,transform] duration-700 ease-out"
            style={{
                left: x,
                top: y,
                width: WORK_CELL_WIDTH,
                height: WORK_CELL_HEIGHT,
                opacity,
                transform: `scale(${scale})`,
                transformOrigin: "top left",
                animationDelay: `${Math.min(index, 6) * 70}ms`,
                zIndex: stageDepthZ(y + WORK_CELL_HEIGHT * scale),
            }}
            data-stage-depth={stageDepthZ(y + WORK_CELL_HEIGHT * scale)}
            data-collision-workstation={`${WORKSTATION_COLLISION.left},${WORKSTATION_COLLISION.top},${WORKSTATION_COLLISION.width},${WORKSTATION_COLLISION.height}`}
        >
            {(stage.renderPhase === "entering" || cue === "summon") ? (
                <div className="absolute left-4 top-[58px] h-8 w-16 animate-[microStagePortal_1.2s_ease-out_forwards] rounded-[50%] border border-dashed" style={{ borderColor: color, backgroundColor: `${color}18` }} />
            ) : null}
            <div className="absolute left-0 top-1 z-[3]">
                <WorkstationDisplay cue={cue} color={color} phase={stage.renderPhase} status={status} />
            </div>
            <div
                className={cn(
                    "absolute left-[45px] top-[29px] z-[8] h-16 w-16 transition-[opacity,transform] ease-out",
                    curtain && "animate-[microStageRobotCurtain_2.05s_ease-out_forwards]",
                )}
                data-collision-agent={`${ROBOT_COLLISION.left - 45},${ROBOT_COLLISION.top - 29},${ROBOT_COLLISION.width},${ROBOT_COLLISION.height}`}
            >
                <div
                    className="absolute -top-2 left-1/2 z-20 flex max-w-[72px] -translate-x-1/2 items-center gap-1 rounded-full border bg-background/90 px-1.5 py-0.5 text-[8px] font-semibold leading-none text-foreground/90 shadow-sm backdrop-blur"
                    style={{ borderColor: `${color}80` }}
                    title={actorName}
                >
                    <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: color }} />
                    <span className="truncate">{actorName}</span>
                </div>
                <div className="absolute bottom-1 left-[18px] h-2 w-8 rounded-full bg-slate-950/20 blur-[1px] dark:bg-black/40" />
                <SubagentRobotSprite action={robotAction} color={color} />
            </div>
            {handoff ? (
                <div
                    className="absolute left-[68px] top-[47px] h-5 w-4 animate-[microStageReportTravel_1.32s_ease-out_forwards] rounded border border-slate-400/70 bg-white shadow-sm"
                    style={{
                        "--micro-stage-report-x": `${targetX + 6}px`,
                        "--micro-stage-report-y": `${targetY - 8}px`,
                    } as CSSProperties}
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
                    "absolute left-[36px] top-[83px] flex h-6 w-6 items-center justify-center rounded-full border bg-background/88 shadow-sm backdrop-blur transition-[transform,background-color,border-color] hover:scale-105 hover:bg-background",
                    !detailRef && "cursor-default",
                )}
                style={{ borderColor: `${statusTone}88`, color: statusTone }}
                title={`${actor.summary || step?.summary || stage.title} · ${statusLabel(status, t)}`}
                aria-label={`${actorName}: ${statusLabel(status, t)}`}
                data-subagent-status={status}
            >
                <span className="relative flex h-3.5 w-3.5 items-center justify-center" aria-hidden="true">
                    {status === "active" ? (
                        <span className="absolute inset-0 animate-spin rounded-full border-[1.5px] border-current border-t-transparent" />
                    ) : null}
                    {status === "pending" || status === "attempted" ? (
                        <span className="absolute inset-[2px] animate-ping rounded-full bg-current opacity-25" />
                    ) : null}
                    <span className="h-1.5 w-1.5 rounded-full bg-current shadow-[0_0_6px_currentColor]" />
                </span>
            </button>
        </div>
    );
}

export const CollaborationMicroStageScene = memo(function CollaborationMicroStageScene({
    stages,
    executionActive = false,
    supervisorSpeech,
    onOpenDetailRef,
    overviewLinkLabel,
    onOpenOverview,
}: CollaborationMicroStageSceneProps) {
    const t = useT();
    const resolvedOverviewLinkLabel = overviewLinkLabel || t("web.collaborationMicroStage.viewOverview");
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
                        <span className="underline decoration-border underline-offset-4 group-hover:decoration-current">{resolvedOverviewLinkLabel}</span>
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
                targetCenterX={position.targetCenterX}
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
                @keyframes microStageCellEnter {
                    0% { opacity: 0; filter: blur(3px); }
                    100% { opacity: 1; filter: blur(0); }
                }
                @keyframes microStageRobotCurtain {
                    0%, 72% { opacity: 1; transform: translateY(0) scale(1); }
                    100% { opacity: 0; transform: translateY(-3px) scale(0.96); }
                }
                @keyframes microStageReportTravel {
                    0% { opacity: 0.66; transform: translate(0, 0); }
                    100% { opacity: 1; transform: translate(var(--micro-stage-report-x), var(--micro-stage-report-y)); }
                }
            `}</style>
        </div>
    );
});

CollaborationMicroStageScene.displayName = "CollaborationMicroStageScene";
