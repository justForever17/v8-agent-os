"use client";

import { useEffect, useState } from "react";
import type {
    CollaborationMicroStageCue,
    CollaborationMicroStageStatus,
} from "@v8/session-realtime";

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

const ROBOT_BASE_SRC = "/subagent_robot_neutral.png";
const ROBOT_MASK_SRC = "/subagent_robot_emissive_mask.png";

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

function useSubagentRobotFrame(action: SubagentRobotAction) {
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
        if (frames.length <= 1) return undefined;

        const scheduleNext = () => {
            timer = setTimeout(() => {
                if (cancelled) return;
                if (!loops && current >= frames.length - 1) return;
                current = (current + 1) % frames.length;
                setFrameState({ action, index: current });
                scheduleNext();
            }, durations[Math.min(current, durations.length - 1)] || 240);
        };
        scheduleNext();
        return () => {
            cancelled = true;
            if (timer) clearTimeout(timer);
        };
    }, [action, durations, frames, loops]);

    return frames[frameIndex] ?? frames[0] ?? 0;
}

export function SubagentRobotSprite({
    action,
    color,
}: {
    action: SubagentRobotAction;
    color: string;
}) {
    const frame = useSubagentRobotFrame(action);
    const column = frame % ROBOT_SHEET.columns;
    const row = Math.floor(frame / ROBOT_SHEET.columns);
    const sheetWidth = ROBOT_SHEET.frameSize * ROBOT_SHEET.columns;
    const sheetHeight = ROBOT_SHEET.frameSize * ROBOT_SHEET.rows;
    const left = -column * ROBOT_SHEET.frameSize;
    const top = -row * ROBOT_SHEET.frameSize;

    return (
        <div
            aria-hidden="true"
            className="relative h-16 w-16 overflow-hidden"
            data-subagent-robot-action={action}
        >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
                src={ROBOT_BASE_SRC}
                alt=""
                draggable={false}
                className="pointer-events-none absolute max-w-none select-none"
                style={{
                    left,
                    top,
                    width: sheetWidth,
                    height: sheetHeight,
                }}
            />
            <span
                className="pointer-events-none absolute inset-0"
                style={{
                    backgroundColor: color,
                    maskImage: `url(${ROBOT_MASK_SRC})`,
                    maskPosition: `${left}px ${top}px`,
                    maskRepeat: "no-repeat",
                    maskSize: `${sheetWidth}px ${sheetHeight}px`,
                    WebkitMaskImage: `url(${ROBOT_MASK_SRC})`,
                    WebkitMaskPosition: `${left}px ${top}px`,
                    WebkitMaskRepeat: "no-repeat",
                    WebkitMaskSize: `${sheetWidth}px ${sheetHeight}px`,
                }}
            />
        </div>
    );
}

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

function WorkstationEventGlyph({
    pattern,
    color,
}: {
    pattern: ScreenPattern;
    color: string;
}) {
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
                <rect x="13" y="18" width="27" height="39" rx="4" fill="none" stroke={dim} strokeWidth="3" />
                <path d="M20 29 H34 M20 37 H32 M20 45 H29" stroke={tone} strokeWidth="3" strokeLinecap="round" />
                <circle cx="65" cy="39" r="13" fill="none" stroke={tone} strokeWidth="3" />
                <path d="M74 48 L85 59" stroke={tone} strokeWidth="4" strokeLinecap="round" />
                <path className="micro-screen-scan" d="M9 13 V63" stroke="#FFFFFF" strokeWidth="3" opacity="0.66" />
                <circle className="micro-screen-pulse" cx="65" cy="39" r="4" fill="#FFFFFF" />
            </>
        );
    }
    if (pattern === "engineering") {
        return (
            <>
                <path d="M13 22 H38 M13 31 H32 M13 40 H40 M13 49 H29" stroke={tone} strokeWidth="3" strokeLinecap="round" />
                <path d="M45 27 L56 38 L45 49" fill="none" stroke={dim} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
                <path d="M65 29 L84 38 L65 47 L56 38 Z" fill={`${tone}22`} stroke={tone} strokeWidth="3" strokeLinejoin="round" />
                <path d="M65 47 V58 L84 48 V38" fill="none" stroke={tone} strokeWidth="3" strokeLinejoin="round" />
                <rect className="micro-screen-build" x="10" y="60" width="76" height="4" rx="2" fill={tone} />
            </>
        );
    }
    if (pattern === "creative") {
        return (
            <>
                <path d="M12 54 C28 12 58 68 87 24" fill="none" stroke={tone} strokeWidth="3" strokeLinecap="round" />
                <path d="M12 54 L30 20 M30 20 L58 51 M58 51 L87 24" stroke={dim} strokeWidth="2" strokeDasharray="4 5" />
                <circle cx="12" cy="54" r="4" fill={tone} />
                <circle cx="30" cy="20" r="4" fill={tone} />
                <circle cx="58" cy="51" r="4" fill={tone} />
                <circle cx="87" cy="24" r="4" fill={tone} />
                <path className="micro-screen-spark" d="M72 14 V26 M66 20 H78" stroke="#FFFFFF" strokeWidth="3" strokeLinecap="round" />
            </>
        );
    }
    if (pattern === "desktop") {
        return (
            <>
                <rect x="13" y="16" width="69" height="45" rx="5" fill={`${tone}12`} stroke={tone} strokeWidth="3" />
                <path d="M13 27 H82" stroke={dim} strokeWidth="3" />
                <circle cx="20" cy="22" r="2" fill={tone} />
                <circle cx="27" cy="22" r="2" fill={tone} opacity="0.65" />
                <path className="micro-screen-pointer" d="M44 34 L61 51 L52 52 L48 60 Z" fill="#FFFFFF" stroke={tone} strokeWidth="2" strokeLinejoin="round" />
                <circle className="micro-screen-pulse" cx="61" cy="51" r="8" fill="none" stroke={tone} strokeWidth="2" />
            </>
        );
    }
    if (pattern === "rpa") {
        return (
            <>
                <rect x="10" y="30" width="20" height="17" rx="4" fill={`${tone}24`} stroke={tone} strokeWidth="2.5" />
                <rect x="40" y="30" width="20" height="17" rx="4" fill={`${tone}24`} stroke={tone} strokeWidth="2.5" />
                <rect x="70" y="30" width="20" height="17" rx="4" fill={`${tone}24`} stroke={tone} strokeWidth="2.5" />
                <path d="M30 38 H40 M60 38 H70" stroke={tone} strokeWidth="3" strokeLinecap="round" />
                <path className="micro-screen-orbit" d="M75 18 C59 5 33 8 23 21 M23 21 L24 12 M23 21 L32 20" fill="none" stroke={tone} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
                <circle className="micro-screen-travel" cx="18" cy="38" r="4" fill="#FFFFFF" />
            </>
        );
    }
    if (pattern === "waiting") {
        return (
            <>
                <circle className="micro-screen-dot micro-screen-dot-a" cx="32" cy="39" r="6" fill={tone} />
                <circle className="micro-screen-dot micro-screen-dot-b" cx="50" cy="39" r="6" fill={tone} />
                <circle className="micro-screen-dot micro-screen-dot-c" cx="68" cy="39" r="6" fill={tone} />
                <path d="M22 57 H78" stroke={dim} strokeWidth="2" strokeLinecap="round" />
            </>
        );
    }
    if (pattern === "completed") {
        return (
            <>
                <circle className="micro-screen-complete" cx="50" cy="39" r="24" fill={`${tone}12`} stroke={tone} strokeWidth="4" />
                <path d="M37 39 L47 49 L66 29" fill="none" stroke="#FFFFFF" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
            </>
        );
    }
    if (pattern === "degraded") {
        return (
            <>
                <path d="M12 39 H38 M62 39 H88" stroke={tone} strokeWidth="4" strokeLinecap="round" />
                <path d="M38 39 Q49 15 62 26 M38 39 Q50 63 62 52" fill="none" stroke={tone} strokeWidth="3" strokeDasharray="5 4" />
                <circle className="micro-screen-travel" cx="18" cy="39" r="5" fill="#FFFFFF" />
                <path d="M50 30 V43 M50 49 V50" stroke="#FFFFFF" strokeWidth="4" strokeLinecap="round" />
            </>
        );
    }
    if (pattern === "failed") {
        return (
            <>
                <path d="M10 39 H38 M62 39 H90" stroke={tone} strokeWidth="4" strokeLinecap="round" />
                <path d="M41 29 L59 49 M59 29 L41 49" stroke="#FFFFFF" strokeWidth="4" strokeLinecap="round" />
                <path className="micro-screen-retry" d="M75 20 C89 27 91 46 78 56 M78 56 L88 55 M78 56 L81 47" fill="none" stroke={tone} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
            </>
        );
    }
    if (pattern === "route") {
        return (
            <>
                <path d="M10 51 C25 51 24 24 42 24 H59 C75 24 72 49 90 49" fill="none" stroke={dim} strokeWidth="4" strokeLinecap="round" />
                <circle cx="10" cy="51" r="5" fill={tone} />
                <circle cx="42" cy="24" r="5" fill={tone} />
                <circle cx="90" cy="49" r="5" fill={tone} />
                <rect className="micro-screen-travel" x="13" y="46" width="14" height="10" rx="5" fill="#FFFFFF" />
            </>
        );
    }
    return (
        <>
            <path d="M18 39 H50 M50 39 L75 21 M50 39 L78 56" stroke={dim} strokeWidth="3" strokeLinecap="round" />
            <circle className="micro-screen-pulse" cx="18" cy="39" r="7" fill={tone} />
            <circle cx="50" cy="39" r="7" fill={tone} />
            <circle cx="75" cy="21" r="7" fill={tone} />
            <circle cx="78" cy="56" r="7" fill={tone} />
            <circle className="micro-screen-travel" cx="18" cy="39" r="3" fill="#FFFFFF" />
        </>
    );
}

export function WorkstationDisplay({
    cue,
    color,
    phase,
    status,
}: {
    cue: CollaborationMicroStageCue;
    color: string;
    phase: SubagentRobotPhase;
    status: CollaborationMicroStageStatus;
}) {
    const pattern = screenPatternFor(cue, status, phase);

    return (
        <div aria-hidden="true" className="pointer-events-none relative h-[88px] w-[88px]">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
                src="/subagent_workstation.png"
                alt=""
                draggable={false}
                className="absolute inset-0 h-full w-full select-none object-contain"
            />
            <div
                className="absolute overflow-hidden rounded-[8%] bg-[radial-gradient(circle_at_50%_45%,rgba(59,130,246,0.12),rgba(2,6,23,0.02)_68%)]"
                style={{
                    left: "19.0312%",
                    top: "4.623%",
                    width: "56.9864%",
                    height: "45.4844%",
                }}
                data-workstation-screen-pattern={pattern}
            >
                <svg viewBox="0 0 100 78" className="h-full w-full overflow-visible">
                    <WorkstationEventGlyph pattern={pattern} color={color} />
                </svg>
            </div>
            <style jsx>{`
                .micro-screen-travel { animation: microScreenTravel 1.45s ease-in-out infinite; transform-origin: center; }
                .micro-screen-pulse { animation: microScreenPulse 1.35s ease-in-out infinite; transform-origin: center; transform-box: fill-box; }
                .micro-screen-scan { animation: microScreenScan 1.6s ease-in-out infinite; }
                .micro-screen-build { animation: microScreenBuild 1.55s ease-in-out infinite; transform-origin: left center; transform-box: fill-box; }
                .micro-screen-spark { animation: microScreenSpark 1.25s ease-in-out infinite; transform-origin: center; transform-box: fill-box; }
                .micro-screen-pointer { animation: microScreenPointer 1.7s ease-in-out infinite; }
                .micro-screen-orbit { animation: microScreenOrbit 1.8s ease-in-out infinite; transform-origin: center; transform-box: fill-box; }
                .micro-screen-retry { animation: microScreenRetry 1.6s ease-in-out infinite; transform-origin: center; transform-box: fill-box; }
                .micro-screen-complete { animation: microScreenComplete 1.8s ease-out infinite; transform-origin: center; transform-box: fill-box; }
                .micro-screen-dot { animation: microScreenDot 1.35s ease-in-out infinite; transform-origin: center; transform-box: fill-box; }
                .micro-screen-dot-b { animation-delay: 150ms; }
                .micro-screen-dot-c { animation-delay: 300ms; }
                @keyframes microScreenTravel {
                    0% { transform: translateX(0); opacity: 0.2; }
                    18% { opacity: 1; }
                    82% { opacity: 1; }
                    100% { transform: translateX(58px); opacity: 0.15; }
                }
                @keyframes microScreenPulse {
                    0%, 100% { transform: scale(0.78); opacity: 0.45; }
                    50% { transform: scale(1.18); opacity: 1; }
                }
                @keyframes microScreenScan {
                    0% { transform: translateX(0); opacity: 0; }
                    18% { opacity: 0.8; }
                    82% { opacity: 0.8; }
                    100% { transform: translateX(76px); opacity: 0; }
                }
                @keyframes microScreenBuild {
                    0%, 18% { transform: scaleX(0.08); opacity: 0.45; }
                    78%, 100% { transform: scaleX(1); opacity: 1; }
                }
                @keyframes microScreenSpark {
                    0%, 100% { transform: scale(0.7) rotate(0deg); opacity: 0.35; }
                    50% { transform: scale(1.2) rotate(18deg); opacity: 1; }
                }
                @keyframes microScreenPointer {
                    0%, 100% { transform: translate(-5px, -4px); }
                    48%, 62% { transform: translate(3px, 4px); }
                }
                @keyframes microScreenOrbit {
                    0%, 100% { transform: rotate(-4deg); opacity: 0.55; }
                    50% { transform: rotate(5deg); opacity: 1; }
                }
                @keyframes microScreenRetry {
                    0%, 100% { transform: rotate(-8deg); opacity: 0.45; }
                    50% { transform: rotate(8deg); opacity: 1; }
                }
                @keyframes microScreenComplete {
                    0% { transform: scale(0.72); opacity: 0; }
                    38% { transform: scale(1.08); opacity: 1; }
                    70%, 100% { transform: scale(1); opacity: 0.92; }
                }
                @keyframes microScreenDot {
                    0%, 100% { transform: translateY(3px) scale(0.82); opacity: 0.35; }
                    50% { transform: translateY(-3px) scale(1.08); opacity: 1; }
                }
                @media (prefers-reduced-motion: reduce) {
                    .micro-screen-travel,
                    .micro-screen-pulse,
                    .micro-screen-scan,
                    .micro-screen-build,
                    .micro-screen-spark,
                    .micro-screen-pointer,
                    .micro-screen-orbit,
                    .micro-screen-retry,
                    .micro-screen-complete,
                    .micro-screen-dot { animation: none !important; }
                }
            `}</style>
        </div>
    );
}
