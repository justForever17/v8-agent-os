"use client";

import { useMemo, useState } from "react";
import {
    AlertCircle,
    Box,
    Check,
    ChevronRight,
    MessageSquare,
    Play,
    Search,
    Sparkles,
    X,
} from "lucide-react";

import type { CreativeCanvasAction } from "@/lib/creative-canvas-actions";
import { cn } from "@/lib/utils";
import { getCanvasBounds, type CanvasPreflightIssue } from "./graph-operations";
import type { CanvasSnapshot, ContextMenuState } from "./types";

function ActionIcon({ action }: { action: CreativeCanvasAction }) {
    if (action.binding?.kind === "mediakit") return <Box className="h-3.5 w-3.5 text-cyan-600" />;
    if (action.binding?.kind === "creative_media") return <Sparkles className="h-3.5 w-3.5 text-violet-600" />;
    if (action.executionClass === "chat_task") return <MessageSquare className="h-3.5 w-3.5 text-amber-600" />;
    return <Check className="h-3.5 w-3.5 text-muted-foreground" />;
}

export function CanvasActionMenu({
    menu,
    actions,
    boardWidth,
    boardHeight,
    actionLabel,
    costLabel,
    moreLabel,
    searchLabel,
    emptyLabel,
    onSelect,
}: {
    menu: ContextMenuState;
    actions: CreativeCanvasAction[];
    boardWidth: number;
    boardHeight: number;
    actionLabel: (action: CreativeCanvasAction) => string;
    costLabel: string;
    moreLabel: string;
    searchLabel: string;
    emptyLabel: string;
    onSelect: (action: CreativeCanvasAction) => void;
}) {
    const [expanded, setExpanded] = useState(false);
    const [query, setQuery] = useState("");
    const visible = useMemo(() => {
        const normalized = query.trim().toLocaleLowerCase();
        const filtered = normalized
            ? actions.filter((action) => `${actionLabel(action)} ${action.actionId}`.toLocaleLowerCase().includes(normalized))
            : actions;
        return expanded ? filtered : filtered.slice(0, 5);
    }, [actionLabel, actions, expanded, query]);
    const width = expanded ? 320 : 280;
    const height = expanded ? 430 : Math.min(390, 72 + visible.length * 42);

    return (
        <div
            data-canvas-wheel-isolation
            role="menu"
            aria-label={moreLabel}
            style={{
                left: Math.min(menu.x, Math.max(12, boardWidth - width - 12)),
                top: Math.min(menu.y, Math.max(12, boardHeight - height - 12)),
            }}
            className={cn(
                "absolute z-[60] overflow-hidden rounded-2xl border border-white/80 bg-background/96 p-1.5 shadow-[0_22px_64px_rgba(15,23,42,.22)] backdrop-blur-xl dark:border-white/10",
                expanded ? "w-[320px]" : "w-[280px]",
            )}
            onPointerDown={(event) => event.stopPropagation()}
            onWheel={(event) => event.stopPropagation()}
        >
            {expanded ? (
                <label className="mb-1 flex h-9 items-center gap-2 rounded-xl border border-border/60 bg-muted/25 px-2.5">
                    <Search className="h-3.5 w-3.5 text-muted-foreground" />
                    <input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder={searchLabel} className="min-w-0 flex-1 bg-transparent text-[11px] outline-none placeholder:text-muted-foreground" />
                    <button type="button" onClick={() => { setExpanded(false); setQuery(""); }} className="rounded-md p-1 text-muted-foreground hover:bg-muted" aria-label={moreLabel}><X className="h-3 w-3" /></button>
                </label>
            ) : null}
            <div className={cn("custom-scrollbar", expanded && "max-h-[370px] overflow-y-auto")}>
                {visible.map((action) => (
                    <button key={action.actionId} type="button" role="menuitem" onClick={() => onSelect(action)} className="flex min-h-10 w-full items-center gap-2 rounded-xl px-2 py-2 text-left text-[11px] hover:bg-muted focus-visible:bg-muted focus-visible:outline-none">
                        <ActionIcon action={action} />
                        <span className="min-w-0 flex-1 truncate">{actionLabel(action)}</span>
                        {action.mayIncurCost ? <span className="text-[8px] text-muted-foreground">{costLabel}</span> : null}
                    </button>
                ))}
                {!visible.length ? <div className="px-3 py-6 text-center text-xs text-muted-foreground">{emptyLabel}</div> : null}
            </div>
            {!expanded && actions.length > 5 ? (
                <button type="button" onClick={() => setExpanded(true)} className="mt-1 flex h-9 w-full items-center gap-2 rounded-xl border-t border-border/50 px-2 text-[11px] font-medium text-muted-foreground hover:bg-muted hover:text-foreground">
                    <Search className="h-3.5 w-3.5" /><span className="flex-1 text-left">{moreLabel}</span><ChevronRight className="h-3.5 w-3.5" />
                </button>
            ) : null}
        </div>
    );
}

export function CanvasMiniMap({
    snapshot,
    boardWidth,
    boardHeight,
    selectedIds,
    label,
    onNavigate,
}: {
    snapshot: CanvasSnapshot;
    boardWidth: number;
    boardHeight: number;
    selectedIds: string[];
    label: string;
    onNavigate: (worldX: number, worldY: number) => void;
}) {
    const bounds = getCanvasBounds(snapshot.nodes);
    if (!bounds || snapshot.nodes.length < 3) return null;
    const padding = 28;
    const mapWidth = 168;
    const mapHeight = 108;
    const scale = Math.min((mapWidth - padding) / Math.max(bounds.width, 1), (mapHeight - padding) / Math.max(bounds.height, 1));
    const offsetX = (mapWidth - bounds.width * scale) / 2 - bounds.minX * scale;
    const offsetY = (mapHeight - bounds.height * scale) / 2 - bounds.minY * scale;
    const worldLeft = -snapshot.viewport.x / snapshot.viewport.scale;
    const worldTop = -snapshot.viewport.y / snapshot.viewport.scale;

    return (
        <button
            type="button"
            aria-label={label}
            title={label}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={(event) => {
                const rect = event.currentTarget.getBoundingClientRect();
                onNavigate((event.clientX - rect.left - offsetX) / scale, (event.clientY - rect.top - offsetY) / scale);
            }}
            className="absolute bottom-3 right-3 z-20 h-[108px] w-[168px] overflow-hidden rounded-xl border border-white/80 bg-background/86 shadow-[0_10px_28px_rgba(15,23,42,.13)] backdrop-blur-lg dark:border-white/10"
        >
            <svg viewBox={`0 0 ${mapWidth} ${mapHeight}`} className="h-full w-full" aria-hidden="true">
                {snapshot.edges.map((edge) => {
                    const from = snapshot.nodes.find((node) => node.nodeId === edge.from);
                    const to = snapshot.nodes.find((node) => node.nodeId === edge.to);
                    if (!from || !to) return null;
                    return <line key={edge.edgeId} x1={(from.x + from.width / 2) * scale + offsetX} y1={(from.y + from.height / 2) * scale + offsetY} x2={(to.x + to.width / 2) * scale + offsetX} y2={(to.y + to.height / 2) * scale + offsetY} className="stroke-slate-300 dark:stroke-slate-700" strokeWidth="1" />;
                })}
                {snapshot.nodes.map((node) => (
                    <rect key={node.nodeId} x={node.x * scale + offsetX} y={node.y * scale + offsetY} width={Math.max(3, node.width * scale)} height={Math.max(2, node.height * scale)} rx="1.5" className={cn(selectedIds.includes(node.nodeId) ? "fill-violet-500" : node.kind === "action" ? "fill-violet-300 dark:fill-violet-700" : node.kind === "result" ? "fill-emerald-300 dark:fill-emerald-700" : "fill-slate-400 dark:fill-slate-500")} />
                ))}
                <rect x={worldLeft * scale + offsetX} y={worldTop * scale + offsetY} width={boardWidth / snapshot.viewport.scale * scale} height={boardHeight / snapshot.viewport.scale * scale} rx="2" fill="transparent" className="stroke-foreground/65" strokeWidth="1.25" />
            </svg>
        </button>
    );
}

export function CanvasPreflightPanel({
    issues,
    actionCount,
    title,
    readyLabel,
    runLabel,
    closeLabel,
    issueLabel,
    onFocus,
    onRun,
    onClose,
}: {
    issues: CanvasPreflightIssue[];
    actionCount: number;
    title: string;
    readyLabel: string;
    runLabel: string;
    closeLabel: string;
    issueLabel: (issue: CanvasPreflightIssue) => string;
    onFocus: (nodeId: string) => void;
    onRun: () => void;
    onClose: () => void;
}) {
    const blocked = issues.some((issue) => issue.severity === "error");
    return (
        <div data-canvas-wheel-isolation className="absolute left-3 top-14 z-50 w-[340px] max-w-[calc(100%-24px)] rounded-2xl border border-white/80 bg-background/96 p-2 shadow-[0_20px_64px_rgba(15,23,42,.2)] backdrop-blur-xl dark:border-white/10" onPointerDown={(event) => event.stopPropagation()} onWheel={(event) => event.stopPropagation()}>
            <div className="flex h-9 items-center gap-2 px-1">
                {blocked ? <AlertCircle className="h-4 w-4 text-red-500" /> : <Check className="h-4 w-4 text-emerald-600" />}
                <span className="min-w-0 flex-1 text-[11px] font-semibold">{title}</span>
                <button type="button" onClick={onClose} aria-label={closeLabel} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted"><X className="h-3.5 w-3.5" /></button>
            </div>
            <div className="px-1 pb-1 text-[9px] text-muted-foreground">{readyLabel.replace("{count}", String(actionCount))}</div>
            {issues.length ? (
                <div className="custom-scrollbar mt-1 max-h-52 space-y-1 overflow-y-auto">
                    {issues.map((issue, index) => (
                        <button key={`${issue.nodeId}:${issue.code}:${index}`} type="button" onClick={() => onFocus(issue.nodeId)} className={cn("flex w-full items-start gap-2 rounded-xl px-2 py-2 text-left text-[10px]", issue.severity === "error" ? "bg-red-500/[.07] text-red-700 dark:text-red-300" : "bg-amber-500/[.08] text-amber-700 dark:text-amber-300")}>
                            <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-current" /><span>{issueLabel(issue)}</span>
                        </button>
                    ))}
                </div>
            ) : null}
            <button type="button" disabled={blocked || actionCount === 0} onClick={onRun} className="mt-2 flex h-9 w-full items-center justify-center gap-1.5 rounded-xl bg-violet-600 text-[11px] font-semibold text-white hover:bg-violet-700 disabled:opacity-35"><Play className="h-3.5 w-3.5" />{runLabel}</button>
        </div>
    );
}
