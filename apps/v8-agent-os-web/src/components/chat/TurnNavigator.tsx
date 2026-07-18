"use client";

import { useMemo, useState } from "react";

export type ChatTurnIndexEntry = {
    turnId: string;
    position: number;
    preview?: string;
    state?: string;
};

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
    const [hoveredMarker, setHoveredMarker] = useState<{ position: number; ratio: number } | null>(null);
    const total = Math.max(0, Number(totalTurnCount || 0));
    const activePosition = turns.find((entry) => entry.turnId === activeTurnId)?.position || total;
    const hoveredEntry = useMemo(
        () => hoveredMarker == null
            ? null
            : turns.find((entry) => entry.position === hoveredMarker.position) || null,
        [hoveredMarker, turns],
    );

    if (total <= 1) {
        return null;
    }

    const activeRatio = Math.max(0, Math.min(1, (activePosition - 1) / Math.max(1, total - 1)));

    return (
        <div className="pointer-events-none absolute inset-y-5 left-0 z-[3] hidden w-7 md:block" data-testid="chat-turn-navigator">
            <button
                type="button"
                className="pointer-events-auto absolute inset-y-0 left-1 w-4 cursor-pointer rounded-full opacity-70 transition-opacity hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
                style={{
                    backgroundImage: "repeating-linear-gradient(to bottom, hsl(var(--border) / 0.9) 0, hsl(var(--border) / 0.9) 1px, transparent 1px, transparent 9px)",
                }}
                aria-label="Conversation turn navigator"
                onMouseMove={(event) => {
                    const rect = event.currentTarget.getBoundingClientRect();
                    const ratio = Math.max(0, Math.min(1, rect.height > 0 ? (event.clientY - rect.top) / rect.height : 0));
                    setHoveredMarker({
                        position: Math.max(1, Math.min(total, Math.round(ratio * (total - 1)) + 1)),
                        ratio,
                    });
                }}
                onMouseLeave={() => setHoveredMarker(null)}
                onClick={(event) => {
                    const rect = event.currentTarget.getBoundingClientRect();
                    const ratio = rect.height > 0 ? (event.clientY - rect.top) / rect.height : 0;
                    onSelectPosition(Math.max(1, Math.min(total, Math.round(ratio * (total - 1)) + 1)));
                }}
            >
                <span
                    className="absolute left-[-2px] h-5 w-5 -translate-y-1/2 rounded-full border-2 border-primary bg-background shadow-sm"
                    style={{ top: `${activeRatio * 100}%` }}
                />
            </button>
            {hoveredMarker != null ? (
                <div
                    className="pointer-events-none absolute left-7 max-w-64 -translate-y-1/2 rounded-lg border border-border/70 bg-popover/95 px-2.5 py-1.5 text-[11px] text-popover-foreground shadow-lg backdrop-blur"
                    style={{ top: `${hoveredMarker.ratio * 100}%` }}
                >
                    <div className="font-medium">#{hoveredMarker.position}</div>
                    {hoveredEntry?.preview ? (
                        <div className="mt-0.5 line-clamp-2 text-muted-foreground">{hoveredEntry.preview}</div>
                    ) : null}
                </div>
            ) : null}
        </div>
    );
}
