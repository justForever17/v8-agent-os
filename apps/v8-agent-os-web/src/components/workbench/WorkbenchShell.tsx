"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { Activity, Box, ChevronLeft, ChevronRight, FileCode2, LayoutPanelTop, Maximize2, Minimize2, Palette, Plus, X } from "lucide-react";
import { motion, useReducedMotion } from "framer-motion";

import { cn } from "@/lib/utils";
import type { RuntimeStageModel } from "@/lib/runtime-stage";
import type { Message } from "@/store/chat-types";
import { useWorkbenchStore } from "@/store/workbench-store";
import type { AdminProcessRef } from "@v8/session-realtime";
import type { TodoHudItem } from "@/components/chat/TodosHUD";
import { WorkspaceWorkbenchPanel } from "@/components/chat/WorkspaceWorkbenchPanel";
import { useT } from "@/components/providers/LocaleProvider";
import { McpAppRenderer } from "@/components/chat/McpAppFrame";
import { ArtifactRenderer } from "./ArtifactRenderer";
import { WorkspaceFileRenderer, type WorkspaceFileLineComment } from "./WorkspaceFileRenderer";
import { SubagentActivityRenderer } from "./SubagentActivityRenderer";
import { RuntimeActivityRenderer } from "./RuntimeActivityRenderer";
import type { WorkbenchTab } from "@/lib/workbench";
import { isTranslationKey } from "@/lib/locale";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { WorkbenchFilePicker } from "./WorkbenchFilePicker";
import type { CanvasTaskRequest } from "./CreativeArtifactCanvas";
import { createCreativeCanvasDocument } from "@/lib/workbench";
import { prefetchWorkspaceFiles } from "@/lib/workbench-actions";
import { createWorkbenchResizeSession } from "./workbench-motion-behavior";

const CreativeArtifactCanvas = dynamic(
    () => import("./CreativeArtifactCanvas").then((module) => module.CreativeArtifactCanvas),
    {
        ssr: false,
        loading: CreativeCanvasLoading,
    },
);

function CreativeCanvasLoading() {
    const t = useT();
    return (
        <div className="flex h-full min-h-0 items-center justify-center bg-[#f5f6f8] text-xs text-muted-foreground dark:bg-[#111315]">
            <Palette className="mr-2 h-4 w-4 animate-pulse" />{t("web.workbench.canvas.loading")}
        </div>
    );
}

type WorkbenchShellProps = {
    sessionId: string;
    messages: Message[];
    outputEvidence?: unknown[];
    processes: AdminProcessRef[];
    todos: TodoHudItem[];
    todoStale?: boolean;
    runtimeModel: RuntimeStageModel;
    workspacePath?: string;
    sessionRunning?: boolean;
    onSendFileLineComment?: (comment: WorkspaceFileLineComment) => Promise<boolean> | boolean;
    onSubmitCanvasTask?: (request: CanvasTaskRequest) => Promise<boolean> | boolean;
    pendingConfirmation?: boolean;
    onOpenPendingConfirmation?: () => void;
};

function documentIcon(kind: string) {
    if (kind === "subagent_activity") return LayoutPanelTop;
    if (kind === "runtime_activity") return Activity;
    if (kind === "workspace_file") return FileCode2;
    if (kind === "artifact") return Box;
    return LayoutPanelTop;
}

type WorkbenchTabStripProps = {
    tabs: WorkbenchTab[];
    activeDocumentId: string | null;
    activateDocument: (documentId: string) => void;
    closeDocument: (documentId: string) => void;
    onAddFile: () => void;
    onAddCanvas: () => void;
};

function WorkbenchTabStrip({ tabs, activeDocumentId, activateDocument, closeDocument, onAddFile, onAddCanvas }: WorkbenchTabStripProps) {
    const t = useT();
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
                aria-label={direction < 0 ? t("web.workbench.tabs.previous") : t("web.workbench.tabs.next")}
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
                aria-label={t("web.workbench.tabs.label")}
                className="scrollbar-hide min-w-0 overflow-x-auto overflow-y-hidden [&::-webkit-scrollbar]:hidden"
                style={{ scrollbarWidth: "none" }}
                data-workbench-tab-scroller
            >
                <div className="flex w-max min-w-full items-center gap-1 px-0.5">
                    {tabs.map((tab) => {
                        const Icon = documentIcon(tab.document.kind);
                        const active = tab.document.documentId === activeDocumentId;
                        const title = isTranslationKey(tab.document.title) ? t(tab.document.title) : tab.document.title;
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
                                    <span className="min-w-0 flex-1 truncate">{title}</span>
                                    {tab.unread ? <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" aria-label={t("web.workbench.tabs.unread")} /> : null}
                                </button>
                                <button type="button" onClick={() => closeDocument(tab.document.documentId)} className="mr-1 hidden rounded-md p-1 text-muted-foreground hover:bg-background/80 hover:text-foreground focus:block group-hover:block" aria-label={t("web.workbench.tabs.close", { title })}><X className="h-3 w-3" /></button>
                            </div>
                        );
                    })}
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <button type="button" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted/55 hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary" aria-label={t("web.workbench.add")}>
                                <Plus className="h-4 w-4" />
                            </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start" sideOffset={5} className="z-[110] w-36">
                            <DropdownMenuItem onSelect={onAddFile} className="gap-2 text-xs"><FileCode2 className="h-3.5 w-3.5" />{t("web.workbench.add.file")}</DropdownMenuItem>
                            <DropdownMenuItem onSelect={onAddCanvas} className="gap-2 text-xs"><Palette className="h-3.5 w-3.5" />{t("web.workbench.add.canvas")}</DropdownMenuItem>
                        </DropdownMenuContent>
                    </DropdownMenu>
                </div>
            </div>
            {edgeButton(1)}
        </div>
    );
}

export function WorkbenchShell(props: WorkbenchShellProps) {
    const t = useT();
    const boundSessionId = useWorkbenchStore((state) => state.sessionId);
    const mode = useWorkbenchStore((state) => state.mode);
    const width = useWorkbenchStore((state) => state.width);
    const tabs = useWorkbenchStore((state) => state.tabs);
    const activeDocumentId = useWorkbenchStore((state) => state.activeDocumentId);
    const activateDocument = useWorkbenchStore((state) => state.activateDocument);
    const closeDocument = useWorkbenchStore((state) => state.closeDocument);
    const openDocument = useWorkbenchStore((state) => state.openDocument);
    const setMode = useWorkbenchStore((state) => state.setMode);
    const setWidth = useWorkbenchStore((state) => state.setWidth);
    const shouldReduceMotion = useReducedMotion();
    const panelRef = useRef<HTMLElement | null>(null);
    const resizeCleanupRef = useRef<(() => void) | null>(null);
    const [containerWidth, setContainerWidth] = useState(() => typeof window === "undefined" ? 1200 : window.innerWidth);
    const [isResizing, setIsResizing] = useState(false);
    const [transientPanelWidth, setTransientPanelWidth] = useState<number | null>(null);
    const [filePickerOpen, setFilePickerOpen] = useState(false);

    useEffect(() => {
        const immediateParent = panelRef.current?.parentElement;
        const parent = immediateParent?.hasAttribute("data-workbench-motion-shell")
            ? immediateParent.parentElement
            : immediateParent;
        if (!parent || typeof ResizeObserver === "undefined") return;
        const observer = new ResizeObserver(([entry]) => setContainerWidth(entry.contentRect.width));
        observer.observe(parent);
        const frame = window.requestAnimationFrame(() => setContainerWidth(parent.getBoundingClientRect().width));
        return () => {
            window.cancelAnimationFrame(frame);
            observer.disconnect();
        };
    }, [mode]);

    useEffect(() => {
        const timeout = window.setTimeout(() => {
            void prefetchWorkspaceFiles(props.sessionId).catch(() => undefined);
            void import("./CreativeArtifactCanvas").catch(() => undefined);
        }, 180);
        return () => window.clearTimeout(timeout);
    }, [props.sessionId]);

    useEffect(() => () => {
        resizeCleanupRef.current?.();
        resizeCleanupRef.current = null;
    }, []);

    useEffect(() => {
        if (mode === "split" && containerWidth >= 760) return;
        resizeCleanupRef.current?.();
        resizeCleanupRef.current = null;
        setIsResizing(false);
        setTransientPanelWidth(null);
    }, [containerWidth, mode]);

    const activeTab = useMemo(
        () => tabs.find((tab) => tab.document.documentId === activeDocumentId) || tabs.at(-1) || null,
        [activeDocumentId, tabs],
    );
    const canvasTab = useMemo(
        () => tabs.find((tab) => tab.document.kind === "creative_canvas") || null,
        [tabs],
    );
    if (!activeTab || boundSessionId !== props.sessionId) return null;

    const shouldShow = mode !== "closed";
    const compactWorkbench = containerWidth > 0 && containerWidth < 760;
    const effectiveMode = mode === "focus" || compactWorkbench ? "focus" : "split";
    const desiredPanelWidth = transientPanelWidth ?? (width > 0 ? width : containerWidth / 3);
    const minimumPanelWidth = Math.min(280, Math.max(200, containerWidth * 0.28));
    const maximumPanelWidth = Math.max(200, containerWidth - 420);
    const panelWidth = Math.min(maximumPanelWidth, Math.max(minimumPanelWidth, desiredPanelWidth));
    const animatedShellWidth = effectiveMode === "focus" ? "100%" : shouldShow ? panelWidth + 6 : 0;
    const document = activeTab.document;

    const content = (() => {
        if (document.status === "unavailable") {
            const reason = document.unavailableReason && isTranslationKey(document.unavailableReason)
                ? t(document.unavailableReason)
                : document.unavailableReason;
            return <div className="flex h-full items-center justify-center px-8 text-center text-sm text-muted-foreground">{reason || t("web.workbench.unavailable")}</div>;
        }
        if (document.kind === "session_overview") return <WorkspaceWorkbenchPanel {...props} />;
        if (document.kind === "subagent_activity") return <SubagentActivityRenderer document={document} messages={props.messages} runtimeModel={props.runtimeModel} processes={props.processes} />;
        if (document.kind === "runtime_activity") return <RuntimeActivityRenderer document={document} runtimeModel={props.runtimeModel} />;
        if (document.kind === "workspace_file") return <WorkspaceFileRenderer document={document} onSendLineComment={props.onSendFileLineComment} />;
        if (document.kind === "artifact") return <ArtifactRenderer key={document.documentId} document={document} onSendLineComment={props.onSendFileLineComment} />;
        if (document.kind === "ui_app") return <McpAppRenderer mcpApp={document.subjectRef.app} />;
        if (document.kind === "creative_canvas") return null;
        return <div className="flex h-full items-center justify-center px-8 text-center text-sm text-muted-foreground">{t("web.workbench.browserExternal")}</div>;
    })();

    const panel = (
        <aside
            ref={panelRef}
            className={cn(
                "v8-workbench-surface z-[70] flex min-h-0 flex-col overflow-hidden bg-background",
                effectiveMode === "focus" ? "absolute inset-0" : "relative h-full shrink-0",
            )}
            style={effectiveMode === "split" ? { width: panelWidth } : undefined}
            aria-label={t("web.workbench.label")}
        >
            <div className="flex h-10 shrink-0 items-center gap-1.5 border-b border-border/60 px-1.5">
                <WorkbenchTabStrip
                    tabs={tabs}
                    activeDocumentId={document.documentId}
                    activateDocument={activateDocument}
                    closeDocument={closeDocument}
                    onAddFile={() => setFilePickerOpen(true)}
                    onAddCanvas={() => openDocument(createCreativeCanvasDocument(props.sessionId), { activate: true, mode: "split" })}
                />
                {!compactWorkbench ? <button
                    type="button"
                    onClick={() => setMode(mode === "focus" ? "split" : "focus")}
                    className="rounded-lg p-2 text-muted-foreground hover:bg-muted/60 hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary"
                    aria-label={mode === "focus" ? t("web.workbench.exitFocus") : t("web.workbench.focus")}
                >
                    {mode === "focus" ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
                </button> : null}
            </div>
            <div role="tabpanel" className="relative min-h-0 flex-1 overflow-hidden">
                <div className={cn("absolute inset-0", document.kind === "creative_canvas" && "invisible pointer-events-none")}>{content}</div>
                {canvasTab?.document.kind === "creative_canvas" ? (
                    <div className={cn("absolute inset-0", document.kind !== "creative_canvas" && "invisible pointer-events-none")}>
                        <CreativeArtifactCanvas
                            key={canvasTab.document.subjectRef.sessionId}
                            document={canvasTab.document}
                            messages={props.messages}
                            workspacePath={props.workspacePath}
                            sessionRunning={props.sessionRunning}
                            visible={shouldShow && document.kind === "creative_canvas"}
                            onSubmitTask={props.onSubmitCanvasTask}
                        />
                    </div>
                ) : null}
            </div>
        </aside>
    );

    return (
        <>
        <motion.div
            data-workbench-motion-shell
            aria-hidden={!shouldShow}
            inert={!shouldShow}
            className={cn(
                "z-[70] overflow-hidden",
                effectiveMode === "focus" ? "absolute inset-0" : "relative flex h-full shrink-0",
                !shouldShow && "pointer-events-none",
            )}
            initial={false}
            animate={{
                width: animatedShellWidth,
                opacity: shouldShow ? 1 : 0,
                transform: shouldShow || shouldReduceMotion ? "translateX(0)" : "translateX(12px)",
            }}
            transition={{
                width: { duration: isResizing ? 0 : shouldReduceMotion ? 0.12 : 0.22, ease: [0.32, 0.72, 0, 1] },
                opacity: { duration: shouldReduceMotion ? 0.12 : 0.2, ease: [0.32, 0.72, 0, 1] },
                transform: { duration: shouldReduceMotion ? 0.12 : 0.2, ease: [0.32, 0.72, 0, 1] },
            }}
        >
            {effectiveMode === "split" ? (
                <div
                        role="separator"
                        aria-orientation="vertical"
                        aria-label={t("web.workbench.resize")}
                        className="relative z-[71] w-1.5 shrink-0 cursor-col-resize bg-transparent before:absolute before:inset-y-0 before:left-1/2 before:w-px before:bg-border/80 hover:bg-primary/[0.025] hover:before:bg-primary/70 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary"
                        tabIndex={0}
                        onKeyDown={(event) => {
                            if (event.key === "ArrowLeft") setWidth(panelWidth + 20);
                            if (event.key === "ArrowRight") setWidth(panelWidth - 20);
                        }}
                        onPointerDown={(event) => {
                            if (event.button !== 0) return;
                            resizeCleanupRef.current?.();
                            event.currentTarget.setPointerCapture(event.pointerId);
                            setIsResizing(true);
                            setTransientPanelWidth(panelWidth);
                            const parentRight = event.currentTarget.parentElement?.getBoundingClientRect().right || window.innerWidth;
                            const session = createWorkbenchResizeSession({
                                pointerId: event.pointerId,
                                parentRight,
                                initialWidth: panelWidth,
                                minimumWidth: minimumPanelWidth,
                                maximumWidth: Math.min(960, maximumPanelWidth),
                                onPreview: setTransientPanelWidth,
                                onCommit: setWidth,
                            });
                            let ended = false;
                            const removeListeners = () => {
                                window.removeEventListener("pointermove", handleMove);
                                window.removeEventListener("pointerup", handleEnd);
                                window.removeEventListener("pointercancel", handleEnd);
                            };
                            const handleMove = (moveEvent: PointerEvent) => session.move(moveEvent.pointerId, moveEvent.clientX);
                            const handleEnd = (endEvent: PointerEvent) => {
                                if (ended) return;
                                if (!session.finish(endEvent.pointerId)) return;
                                ended = true;
                                removeListeners();
                                resizeCleanupRef.current = null;
                                setIsResizing(false);
                                setTransientPanelWidth(null);
                            };
                            resizeCleanupRef.current = () => {
                                if (ended) return;
                                ended = true;
                                removeListeners();
                                session.dispose();
                            };
                            window.addEventListener("pointermove", handleMove);
                            window.addEventListener("pointerup", handleEnd);
                            window.addEventListener("pointercancel", handleEnd);
                        }}
                />
            ) : null}
            {panel}
        </motion.div>
        <WorkbenchFilePicker sessionId={props.sessionId} open={filePickerOpen} onOpenChange={setFilePickerOpen} />
        </>
    );
}
