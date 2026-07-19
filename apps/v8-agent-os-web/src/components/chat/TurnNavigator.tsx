"use client";

import { useMemo, useState } from "react";

const TURN_MARKER_GAP_PX = 9;
const MAX_RENDERED_MARKERS = 241;

export type ChatTurnIndexEntry = {
    turnId: string;
    position: number;
    preview?: string;
    state?: string;
    createdAt?: string;
};

function formatTurnTimestamp(value?: string) {
    const timestamp = Date.parse(String(value || ""));
    if (!Number.isFinite(timestamp)) return "";
    return new Intl.DateTimeFormat(undefined, {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hourCycle: "h23",
    }).format(timestamp);
}

type TurnNavigatorProps = {
    turns: ChatTurnIndexEntry[];
    totalTurnCount: number;
    activeTurnId?: string | null;
    onSelectPosition: (position: number) => void;
};

export function TurnNavigator({
    turns,
    totalTurnCount,
    activeTurnId,
    onSelectPosition,
}: TurnNavigatorProps) {
    const [hoveredMarker, setHoveredMarker] = useState<{ position: number } | null>(null);
    const total = Math.max(0, Number(totalTurnCount || 0));
    const activePosition = turns.find((entry) => entry.turnId === activeTurnId)?.position || total;
    const hoveredEntry = useMemo(
        () => hoveredMarker == null
            ? null
            : turns.find((entry) => entry.position === hoveredMarker.position) || null,
        [hoveredMarker, turns],
    );
    const hoveredTimestampLabel = useMemo(
        () => formatTurnTimestamp(hoveredEntry?.createdAt),
        [hoveredEntry?.createdAt],
    );

    if (total <= 1) {
        return null;
    }

    const safeActivePosition = Math.max(1, Math.min(total, activePosition));
    const markerRadius = Math.floor(MAX_RENDERED_MARKERS / 2);
    const firstRenderedPosition = Math.max(1, safeActivePosition - markerRadius);
    const lastRenderedPosition = Math.min(total, safeActivePosition + markerRadius);
    const renderedPositions = Array.from(
        { length: Math.max(0, lastRenderedPosition - firstRenderedPosition + 1) },
        (_, index) => firstRenderedPosition + index,
    );

    const positionFromPointer = (clientY: number, top: number, height: number) => {
        const offsetFromCenter = clientY - (top + (height / 2));
        return Math.max(1, Math.min(total, safeActivePosition + Math.round(offsetFromCenter / TURN_MARKER_GAP_PX)));
    };

    const topForPosition = (position: number) => `calc(50% + ${(position - safeActivePosition) * TURN_MARKER_GAP_PX}px)`;
    const markerClassForPosition = (position: number) => {
        const hoverDistance = hoveredMarker == null
            ? Number.POSITIVE_INFINITY
            : Math.abs(position - hoveredMarker.position);
        if (hoverDistance === 0) {
            return "w-1.5";
        }
        if (hoverDistance === 1) {
            return "w-2.5 lg:w-4";
        }
        if (hoverDistance === 2) {
            return "w-2 lg:w-3";
        }
        if (hoverDistance === 3) {
            return "w-1.5 lg:w-2.5";
        }
        if (position === safeActivePosition) {
            return "w-2.5 lg:w-3";
        }
        return "w-1.5";
    };
    const markerAlphaForPosition = (position: number) => {
        const hoverDistance = hoveredMarker == null
            ? Number.POSITIVE_INFINITY
            : Math.abs(position - hoveredMarker.position);
        if (hoverDistance === 0) return 0.82;
        if (hoverDistance === 1) return 0.66;
        if (hoverDistance === 2) return 0.54;
        if (hoverDistance === 3) return 0.44;
        if (position === safeActivePosition) return 0.68;
        return 0.34;
    };

    return (
        <div className="pointer-events-none absolute inset-y-5 left-[-14px] z-[8] hidden w-8 md:block lg:left-[-22px]" data-testid="chat-turn-navigator">
            <button
                type="button"
                className="group pointer-events-auto absolute inset-y-0 left-0 w-8 cursor-pointer focus-visible:outline-none"
                aria-label="Conversation turn navigator"
                onMouseMove={(event) => {
                    const rect = event.currentTarget.getBoundingClientRect();
                    const position = positionFromPointer(event.clientY, rect.top, rect.height);
                    setHoveredMarker((current) => current?.position === position ? current : { position });
                }}
                onMouseLeave={() => setHoveredMarker(null)}
                onClick={(event) => {
                    const rect = event.currentTarget.getBoundingClientRect();
                    onSelectPosition(positionFromPointer(event.clientY, rect.top, rect.height));
                }}
            >
                <span
                    data-turn-rail
                    className="pointer-events-none absolute inset-y-0 left-0 w-8 overflow-hidden opacity-65 transition-opacity duration-150 group-hover:opacity-90"
                    style={{
                        maskImage: "linear-gradient(to bottom, transparent 0%, black 12%, black 88%, transparent 100%)",
                        WebkitMaskImage: "linear-gradient(to bottom, transparent 0%, black 12%, black 88%, transparent 100%)",
                    }}
                >
                    {renderedPositions.map((position) => (
                        <span
                            key={position}
                            data-turn-position={position}
                            data-testid={position === safeActivePosition ? "chat-turn-active-marker" : undefined}
                            className={`absolute left-0 h-px -translate-y-1/2 rounded-full transition-[width,opacity,background-color] duration-150 ease-out ${markerClassForPosition(position)}`}
                            style={{
                                top: topForPosition(position),
                                backgroundColor: `hsl(var(--foreground) / ${markerAlphaForPosition(position)})`,
                            }}
                        />
                    ))}
                </span>
                <span
                    data-testid="chat-turn-hover-marker"
                    className={`absolute left-0 h-px -translate-y-1/2 rounded-full transition-[top,width,opacity] duration-100 ease-out ${hoveredMarker == null ? "w-1.5 opacity-0" : "w-3 opacity-100 lg:w-5"}`}
                    style={{
                        top: topForPosition(hoveredMarker?.position || safeActivePosition),
                        backgroundColor: "hsl(var(--foreground) / 0.86)",
                    }}
                />
            </button>
            {hoveredMarker != null ? (
                <div
                    data-testid="chat-turn-tooltip"
                    className="pointer-events-none absolute left-9 w-64 -translate-y-1/2 rounded-xl border border-border/65 bg-popover/95 px-3 py-2 text-xs leading-5 text-popover-foreground shadow-lg backdrop-blur"
                    style={{ top: `clamp(38px, ${topForPosition(hoveredMarker.position)}, calc(100% - 38px))` }}
                >
                    {hoveredTimestampLabel ? (
                        <time className="font-medium tabular-nums" dateTime={hoveredEntry?.createdAt}>
                            {hoveredTimestampLabel}
                        </time>
                    ) : null}
                    {hoveredEntry?.preview ? (
                        <div className="mt-0.5 line-clamp-2 text-muted-foreground">{hoveredEntry.preview}</div>
                    ) : null}
                </div>
            ) : null}
        </div>
    );
}
