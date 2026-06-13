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
    supervisorSpeech?: string;
    onOpenDetailRef?: (target: CollaborationMicroStageDetailTarget) => void;
}

type MicroStageRenderPhase = "opening" | "active" | "handoff" | "settled" | "collapsed" | "exiting";

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

const MAX_STAGE_ACTORS = 10;
const STAGE_HEIGHTS: Record<CollaborationMicroStageLayout, number> = {
    singleRow: 150,
    officeGrid: 232,
    clusteredGrid: 206,
};
const WORK_CELL_WIDTH = 98;
const WORK_CELL_HEIGHT = 106;
const SUPERVISOR_BASE_TOP = 52;

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

function nextPhaseForStage(stage: CollaborationMicroStage, previous?: RetainedMicroStage): MicroStageRenderPhase {
    if (previous?.renderPhase === "exiting") return "exiting";
    if (isFinalStatus(stage.status)) {
        if (!previous || !isFinalStatus(previous.status)) return "handoff";
        if (previous.renderPhase === "handoff") return "handoff";
        if (previous.renderPhase === "collapsed") return "collapsed";
        return "settled";
    }
    if (!previous) return "opening";
    if (previous.renderPhase === "opening") return "opening";
    return "active";
}

function phaseUntil(phase: MicroStageRenderPhase, now: number) {
    if (phase === "opening") return now + 900;
    if (phase === "handoff") return now + 1500;
    if (phase === "settled") return now + 5200;
    if (phase === "exiting") return now + 1800;
    return undefined;
}

function advancePhase(phase: MicroStageRenderPhase): MicroStageRenderPhase | null {
    if (phase === "opening") return "active";
    if (phase === "handoff") return "settled";
    if (phase === "settled") return "collapsed";
    if (phase === "exiting") return null;
    return phase;
}

function useRetainedMicroStages(stages: CollaborationMicroStage[]) {
    const [retained, setRetained] = useState<RetainedMicroStage[]>([]);

    useEffect(() => {
        const now = Date.now();
        const timer = window.setTimeout(() => {
            setRetained((current) => {
                const currentById = new Map(current.map((stage) => [stage.id, stage]));
                const incomingIds = new Set(stages.map((stage) => stage.id));
                const next: RetainedMicroStage[] = stages.map((stage) => {
                    const previous = currentById.get(stage.id);
                    const renderPhase = nextPhaseForStage(stage, previous);
                    const previousUntil = previous?.renderPhase === renderPhase ? previous.phaseUntil : undefined;
                    return {
                        ...stage,
                        renderPhase,
                        phaseUntil: previousUntil ?? phaseUntil(renderPhase, now),
                    };
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
        }, 0);
        return () => window.clearTimeout(timer);
    }, [stages]);

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
                const nextPhase = advancePhase(stage.renderPhase);
                if (!nextPhase) return [];
                return [{
                    ...stage,
                    renderPhase: nextPhase,
                    phaseUntil: phaseUntil(nextPhase, tick),
                }];
            }));
        }, delay);
        return () => window.clearTimeout(timer);
    }, [retained]);

    return retained;
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
        const reservedSupervisorWidth = 78;
        const cellWidth = count <= 2 ? 100 : 88;
        const availableWidth = Math.max(cellWidth * count, canvasWidth - reservedSupervisorWidth - 8);
        const gap = count > 1 ? Math.max(4, Math.min(20, (availableWidth - cellWidth * count) / (count - 1))) : 0;
        const start = reservedSupervisorWidth + Math.max(0, (availableWidth - (cellWidth * count + gap * (count - 1))) / 2);
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

function supervisorPatrolWaypoints(layout: CollaborationMicroStageLayout, width: number) {
    const canvasWidth = Math.max(300, width || 360);
    if (layout === "singleRow") {
        return [
            { x: 20, y: 0 },
            { x: Math.max(20, Math.min(canvasWidth - 88, 138)), y: 0 },
            { x: Math.max(20, canvasWidth - 104), y: 0 },
        ];
    }
    if (layout === "officeGrid") {
        const middleX = Math.max(24, canvasWidth / 2 - 34);
        return [
            { x: 18, y: 4 },
            { x: middleX, y: 4 },
            { x: Math.max(18, canvasWidth - 96), y: 4 },
            { x: middleX, y: 92 },
            { x: 18, y: 92 },
            { x: Math.max(18, canvasWidth - 96), y: 92 },
        ];
    }
    return [
        { x: 12, y: 0 },
        { x: Math.max(12, canvasWidth * 0.32 - 34), y: 0 },
        { x: Math.max(12, canvasWidth * 0.64 - 34), y: 0 },
        { x: Math.max(12, canvasWidth - 88), y: 0 },
        { x: Math.max(12, canvasWidth - 88), y: 78 },
        { x: Math.max(12, canvasWidth * 0.52 - 34), y: 78 },
        { x: 12, y: 78 },
    ];
}

function useSupervisorPatrol(layout: CollaborationMicroStageLayout, width: number, active: boolean) {
    const [waypointIndex, setWaypointIndex] = useState(0);
    const waypoints = useMemo(() => supervisorPatrolWaypoints(layout, width), [layout, width]);

    useEffect(() => {
        if (!active) return undefined;
        if (waypoints.length === 0) return undefined;
        const timer = window.setInterval(() => {
            setWaypointIndex((current) => current + 1);
        }, layout === "singleRow" ? 3200 : 2600);
        return () => window.clearInterval(timer);
    }, [active, layout, waypoints.length]);

    const safeIndex = waypoints.length > 0 ? waypointIndex % waypoints.length : 0;
    const previousIndex = waypoints.length > 0 ? (safeIndex - 1 + waypoints.length) % waypoints.length : 0;
    const position = waypoints[safeIndex] || { x: 24, y: 0 };
    const previous = waypoints[previousIndex] || position;
    return { position, facingLeft: position.x < previous.x };
}

function cueToSupervisorAction(items: StageActorItem[]) {
    if (items.some((item) => item.actor.status === "active" && item.actor.kind === "subagent")) return "summoning";
    if (items.some((item) => item.actor.status === "active")) return "working";
    if (items.some((item) => item.actor.status === "failed" || item.actor.status === "degraded")) return "reading";
    if (items.some((item) => isFinalStatus(item.actor.status) || item.actor.cue === "handoff" || item.actor.cue === "completed")) return "receiving";
    return "patrolling";
}

function SupervisorAvatar({
    speech,
    x,
    y,
    facingLeft,
    action,
}: {
    speech?: string;
    x: number;
    y: number;
    facingLeft: boolean;
    action: string;
}) {
    return (
        <div
            className="absolute z-30 h-[70px] w-[72px] transition-transform duration-[1450ms] ease-in-out"
            style={{ transform: `translate(${x}px, ${SUPERVISOR_BASE_TOP + y}px)` }}
        >
            {speech ? (
                <div className="absolute -top-9 left-4 max-w-[138px] rounded-2xl border border-border/70 bg-background/90 px-2.5 py-1.5 text-[10px] leading-4 text-foreground shadow-sm backdrop-blur">
                    {speech}
                </div>
            ) : null}
            <div className="absolute bottom-1 left-3 h-2 w-12 rounded-full bg-slate-900/15 blur-[1px] dark:bg-black/35" />
            <div
                className={cn(
                    "absolute bottom-1 left-1 grid h-14 w-14 place-items-center rounded-[22px] border border-white/60 bg-gradient-to-br from-violet-500 via-fuchsia-400 to-cyan-300 text-lg font-black text-white shadow-[0_10px_26px_rgba(124,58,237,0.28)] transition-transform duration-300 dark:border-white/20",
                    action === "summoning" && "animate-[microStagePulse_1.2s_ease-in-out_infinite]",
                    action === "receiving" && "animate-[microStageHop_1.1s_ease-in-out_infinite]",
                )}
                style={{ transform: facingLeft ? "scaleX(-1)" : "scaleX(1)" }}
            >
                <span className="drop-shadow-sm">主</span>
                <span className="absolute -right-1 top-2 h-2.5 w-2.5 rounded-full bg-white/90 shadow-[0_0_12px_rgba(255,255,255,0.9)]" />
            </div>
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
    const active = status === "active" && stage.renderPhase !== "collapsed" && stage.renderPhase !== "exiting";
    const handoff = isFinalStatus(status) || cue === "handoff" || cue === "completed";
    const detailRef = actor.detailRef || step?.detailRef;
    const targetX = supervisor.x + 34 - x;
    const targetY = SUPERVISOR_BASE_TOP + supervisor.y + 20 - y - 48;
    const opacity = stage.renderPhase === "exiting" ? 0 : stage.renderPhase === "collapsed" ? 0.72 : 1;
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
            {(stage.renderPhase === "opening" || active || cue === "summon") ? (
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
                    "absolute left-7 top-[54px] h-10 w-10 transition-transform duration-[1320ms] ease-out",
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
                    className="absolute left-10 top-[49px] h-5 w-4 rounded border border-slate-400/70 bg-white shadow-sm transition-transform duration-[1320ms] ease-out"
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
    supervisorSpeech,
    onOpenDetailRef,
}: CollaborationMicroStageSceneProps) {
    const retainedStages = useRetainedMicroStages(stages);
    const actorItems = useMemo(() => buildStageActorItems(retainedStages), [retainedStages]);
    const layout = useMemo(() => selectCollaborationMicroStageLayout(retainedStages), [retainedStages]);
    const [rootRef, width] = useElementWidth<HTMLDivElement>();
    const positionedItems = useMemo(() => positionStageActorItems(actorItems, layout, width), [actorItems, layout, width]);
    const { position, facingLeft } = useSupervisorPatrol(layout, width, positionedItems.length > 0);
    const action = useMemo(() => cueToSupervisorAction(actorItems), [actorItems]);
    const height = STAGE_HEIGHTS[layout];

    if (retainedStages.length === 0 || positionedItems.length === 0) {
        return null;
    }

    return (
        <div
            ref={rootRef}
            className="relative my-1.5 w-full overflow-hidden rounded-[22px] border border-white/30 bg-white/35 shadow-inner backdrop-blur-md dark:border-white/10 dark:bg-white/[0.03]"
            style={{ height }}
            data-collaboration-layout={layout}
        >
            <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_right,rgba(148,163,184,0.10)_1px,transparent_1px),linear-gradient(to_bottom,rgba(148,163,184,0.10)_1px,transparent_1px)] bg-[size:42px_42px] opacity-60" />
            <div className="pointer-events-none absolute inset-x-0 bottom-5 h-px bg-border/40" />
            <div className="pointer-events-none absolute inset-x-8 bottom-3 h-px bg-border/25" />
            <SupervisorAvatar
                speech={supervisorSpeech}
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
                @keyframes microStagePulse {
                    0%, 100% { filter: drop-shadow(0 0 0 rgba(217, 70, 239, 0)); }
                    50% { filter: drop-shadow(0 0 16px rgba(217, 70, 239, 0.55)); }
                }
                @keyframes microStageHop {
                    0%, 100% { transform: translateY(0); }
                    50% { transform: translateY(-2px); }
                }
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
