"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Box, ChevronLeft, ChevronRight, FileCode2, LayoutPanelTop, Maximize2, Minimize2, X } from "lucide-react";

import { cn } from "@/lib/utils";
import type { RuntimeStageModel } from "@/lib/runtime-stage";
import type { Message } from "@/store/chat-types";
import { useWorkbenchStore } from "@/store/workbench-store";
import type { AdminProcessRef } from "@v8/session-realtime";
import type { TodoHudItem } from "@/components/chat/TodosHUD";
import { WorkspaceWorkbenchPanel } from "@/components/chat/WorkspaceWorkbenchPanel";
import { McpAppRenderer } from "@/components/chat/McpAppFrame";
import { ArtifactRenderer } from "./ArtifactRenderer";
import { WorkspaceFileRenderer, type WorkspaceFileLineComment } from "./WorkspaceFileRenderer";
import type { WorkbenchTab } from "@/lib/workbench";

type WorkbenchShellProps = {
    sessionId: string;
    messages: Message[];
    processes: AdminProcessRef[];
    todos: TodoHudItem[];
    todoStale?: boolean;
    runtimeModel: RuntimeStageModel;
    workspacePath?: string;
    onSendFileLineComment?: (comment: WorkspaceFileLineComment) => Promise<boolean> | boolean;
};

function documentIcon(kind: string) {
    if (kind === "workspace_file") return FileCode2;
    if (kind === "artifact") return Box;
    return LayoutPanelTop;
}

type WorkbenchTabStripProps = {
    tabs: WorkbenchTab[];
    activeDocumentId: string | null;
    activateDocument: (documentId: string) => void;
    closeDocument: (documentId: string) => void;
};

function WorkbenchTabStrip({ tabs, activeDocumentId, activateDocument, closeDocument }: WorkbenchTabStripProps) {
    const scrollerRef = useRef<HTMLDivElement | null>(null);
    const autoScrollFrameRef = useRef<number | null>(null);
    const lastFrameAtRef = useRef(0);
    const [scrollState, setScrollState] = useState({ left: false, right: false });

    const updateScrollState = useCallback(() => {
        const scroller = scrollerRef.current;
        if (!scroller) return;
        const maxScrollLeft = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
        setScrollState({
            left: scroller.scrollLeft > 1,
            right: scroller.scrollLeft < maxScrollLeft - 1,
        });
    }, []);

    const stopAutoScroll = useCallback(() => {
        if (autoScrollFrameRef.current !== null) cancelAnimationFrame(autoScrollFrameRef.current);
        autoScrollFrameRef.current = null;
        lastFrameAtRef.current = 0;
    }, []);

    const startAutoScroll = useCallback((direction: -1 | 1) => {
        stopAutoScroll();
        const tick = (timestamp: number) => {
            const scroller = scrollerRef.current;
            if (!scroller) return stopAutoScroll();
            const elapsed = lastFrameAtRef.current ? Math.min(32, timestamp - lastFrameAtRef.current) : 16;
            lastFrameAtRef.current = timestamp;
            scroller.scrollLeft += direction * elapsed * 0.24;
            updateScrollState();
            const maxScrollLeft = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
            const atBoundary = direction < 0 ? scroller.scrollLeft <= 0 : scroller.scrollLeft >= maxScrollLeft - 1;
            if (atBoundary) return stopAutoScroll();
            autoScrollFrameRef.current = requestAnimationFrame(tick);
        };
        autoScrollFrameRef.current = requestAnimationFrame(tick);
    }, [stopAutoScroll, updateScrollState]);

    const scrollByPage = useCallback((direction: -1 | 1) => {
        const scroller = scrollerRef.current;
        if (!scroller) return;
        scroller.scrollBy({ left: direction * Math.max(120, scroller.clientWidth * 0.6), behavior: "smooth" });
    }, []);

    useEffect(() => {
        const scroller = scrollerRef.current;
        if (!scroller) return;
        updateScrollState();
        const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updateScrollState);
        observer?.observe(scroller);
        const content = scroller.firstElementChild;
        if (content instanceof HTMLElement) observer?.observe(content);
        scroller.addEventListener("scroll", updateScrollState, { passive: true });
        return () => {
            observer?.disconnect();
            scroller.removeEventListener("scroll", updateScrollState);
        };
    }, [tabs, updateScrollState]);

    useEffect(() => {
        const activeTab = scrollerRef.current?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]');
        activeTab?.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
        const timeout = window.setTimeout(updateScrollState, 180);
        return () => window.clearTimeout(timeout);
    }, [activeDocumentId, updateScrollState]);

    useEffect(() => stopAutoScroll, [stopAutoScroll]);

    const edgeButton = (direction: -1 | 1) => {
        const visible = direction < 0 ? scrollState.left : scrollState.right;
        const Icon = direction < 0 ? ChevronLeft : ChevronRight;
        return (
            <button
                type="button"
                onPointerEnter={() => startAutoScroll(direction)}
                onPointerLeave={stopAutoScroll}
                onBlur={stopAutoScroll}
                onClick={() => scrollByPage(direction)}
                className={cn(
                    "absolute inset-y-0 z-10 flex w-6 items-center justify-center text-muted-foreground opacity-0 transition-opacity duration-150 focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary group-hover/tabstrip:opacity-100 group-focus-within/tabstrip:opacity-100",
                    direction < 0 ? "left-0 bg-gradient-to-r from-background via-background/95 to-transparent" : "right-0 bg-gradient-to-l from-background via-background/95 to-transparent",
                    visible ? "pointer-events-auto" : "pointer-events-none !opacity-0",
                )}
                aria-label={direction < 0 ? "向左浏览标签" : "向右浏览标签"}
            >
                <Icon className="h-3.5 w-3.5" />
            </button>
        );
    };

    return (
        <div className="group/tabstrip relative min-w-0 flex-1 overflow-hidden" data-workbench-tab-strip>
            {edgeButton(-1)}
            <div
                ref={scrollerRef}
                role="tablist"
                aria-label="工作台文档"
                className="scrollbar-hide min-w-0 overflow-x-auto overflow-y-hidden [&::-webkit-scrollbar]:hidden"
                style={{ scrollbarWidth: "none" }}
                data-workbench-tab-scroller
            >
                <div className="flex w-max min-w-full items-center gap-1 px-0.5">
                    {tabs.map((tab) => {
                        const Icon = documentIcon(tab.document.kind);
                        const active = tab.document.documentId === activeDocumentId;
                        return (
                            <div key={tab.document.documentId} className={cn("group flex h-8 min-w-[112px] max-w-[220px] items-center rounded-xl", active ? "bg-muted/70 text-foreground" : "text-muted-foreground hover:bg-muted/35")}>
                                <button
                                    type="button"
                                    role="tab"
                                    aria-selected={active}
                                    onClick={() => activateDocument(tab.document.documentId)}
                                    className="flex h-full min-w-0 flex-1 items-center gap-1.5 rounded-xl px-2 text-left text-[11px] focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary"
                                >
                                    <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                    <span className="min-w-0 flex-1 truncate">{tab.document.title}</span>
                                    {tab.unread ? <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" aria-label="未读" /> : null}
                                </button>
                                <button type="button" onClick={() => closeDocument(tab.document.documentId)} className="mr-1 hidden rounded-md p-1 text-muted-foreground hover:bg-background/80 hover:text-foreground focus:block group-hover:block" aria-label={`关闭 ${tab.document.title}`}><X className="h-3 w-3" /></button>
                            </div>
                        );
                    })}
                </div>
            </div>
            {edgeButton(1)}
        </div>
    );
}

export function WorkbenchShell(props: WorkbenchShellProps) {
    const boundSessionId = useWorkbenchStore((state) => state.sessionId);
    const mode = useWorkbenchStore((state) => state.mode);
    const width = useWorkbenchStore((state) => state.width);
    const tabs = useWorkbenchStore((state) => state.tabs);
    const activeDocumentId = useWorkbenchStore((state) => state.activeDocumentId);
    const activateDocument = useWorkbenchStore((state) => state.activateDocument);
    const closeDocument = useWorkbenchStore((state) => state.closeDocument);
    const setMode = useWorkbenchStore((state) => state.setMode);
    const setWidth = useWorkbenchStore((state) => state.setWidth);
    const panelRef = useRef<HTMLElement | null>(null);
    const [containerWidth, setContainerWidth] = useState(() => typeof window === "undefined" ? 1200 : window.innerWidth);

    useEffect(() => {
        const parent = panelRef.current?.parentElement;
        if (!parent || typeof ResizeObserver === "undefined") return;
        const observer = new ResizeObserver(([entry]) => setContainerWidth(entry.contentRect.width));
        observer.observe(parent);
        setContainerWidth(parent.getBoundingClientRect().width);
        return () => observer.disconnect();
    }, [mode]);

    const activeTab = useMemo(
        () => tabs.find((tab) => tab.document.documentId === activeDocumentId) || tabs.at(-1) || null,
        [activeDocumentId, tabs],
    );
    if (mode === "closed" || !activeTab || boundSessionId !== props.sessionId) return null;

    const effectiveMode = mode === "focus" ? "focus" : "split";
    const desiredPanelWidth = width > 0 ? width : containerWidth / 3;
    const minimumPanelWidth = Math.min(280, Math.max(200, containerWidth * 0.28));
    const maximumPanelWidth = Math.max(200, containerWidth - 420);
    const panelWidth = Math.min(maximumPanelWidth, Math.max(minimumPanelWidth, desiredPanelWidth));
    const document = activeTab.document;

    const content = (() => {
        if (document.status === "unavailable") {
            return <div className="flex h-full items-center justify-center px-8 text-center text-sm text-muted-foreground">{document.unavailableReason || "该内容当前不可用。"}</div>;
        }
        if (document.kind === "session_overview") return <WorkspaceWorkbenchPanel {...props} />;
        if (document.kind === "workspace_file") return <WorkspaceFileRenderer document={document} onSendLineComment={props.onSendFileLineComment} />;
        if (document.kind === "artifact") return <ArtifactRenderer document={document} />;
        if (document.kind === "ui_app") return <McpAppRenderer mcpApp={document.subjectRef.app} />;
        return <div className="flex h-full items-center justify-center px-8 text-center text-sm text-muted-foreground">Agent 浏览器已在独立窗口中运行。</div>;
    })();

    const panel = (
        <aside
            ref={panelRef}
            className={cn(
                "z-[70] flex min-h-0 flex-col overflow-hidden border-l border-border/70 bg-background",
                effectiveMode === "focus" ? "absolute inset-0 border-l-0" : "relative h-full shrink-0",
            )}
            style={effectiveMode === "split" ? { width: panelWidth } : undefined}
            aria-label="工作台"
        >
            <div className="flex h-10 shrink-0 items-center gap-1.5 border-b border-border/60 px-1.5">
                <WorkbenchTabStrip
                    tabs={tabs}
                    activeDocumentId={document.documentId}
                    activateDocument={activateDocument}
                    closeDocument={closeDocument}
                />
                <button
                    type="button"
                    onClick={() => setMode(mode === "focus" ? "split" : "focus")}
                    className="rounded-lg p-2 text-muted-foreground hover:bg-muted/60 hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary"
                    aria-label={mode === "focus" ? "退出聚焦" : "聚焦工作台"}
                >
                    {mode === "focus" ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
                </button>
            </div>
            <div role="tabpanel" className="min-h-0 flex-1 overflow-hidden">{content}</div>
        </aside>
    );

    if (effectiveMode === "focus") return panel;
    return (
        <>
            <div
                role="separator"
                aria-orientation="vertical"
                aria-label="调整工作台宽度"
                className="relative z-[71] w-1.5 shrink-0 cursor-col-resize bg-border/15 before:absolute before:inset-y-0 before:left-1/2 before:w-px before:bg-border hover:bg-primary/5 hover:before:bg-primary/70 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary"
                tabIndex={0}
                onKeyDown={(event) => {
                    if (event.key === "ArrowLeft") setWidth(panelWidth + 20);
                    if (event.key === "ArrowRight") setWidth(panelWidth - 20);
                }}
                onPointerDown={(event) => {
                    event.currentTarget.setPointerCapture(event.pointerId);
                    const parentRight = event.currentTarget.parentElement?.getBoundingClientRect().right || window.innerWidth;
                    const handleMove = (moveEvent: PointerEvent) => setWidth(parentRight - moveEvent.clientX);
                    const handleUp = () => {
                        window.removeEventListener("pointermove", handleMove);
                        window.removeEventListener("pointerup", handleUp);
                    };
                    window.addEventListener("pointermove", handleMove);
                    window.addEventListener("pointerup", handleUp, { once: true });
                }}
            />
            {panel}
        </>
    );
}
