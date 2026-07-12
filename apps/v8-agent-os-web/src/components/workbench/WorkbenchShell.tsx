"use client";

import dynamic from "next/dynamic";
import { useEffect, useMemo, useRef, useState } from "react";
import { Box, FileCode2, Globe2, LayoutPanelTop, LoaderCircle, Maximize2, Minimize2, Plus, X } from "lucide-react";

import { cn } from "@/lib/utils";
import type { RuntimeStageModel } from "@/lib/runtime-stage";
import type { Message } from "@/store/chat-types";
import { useWorkbenchStore } from "@/store/workbench-store";
import type { AdminProcessRef } from "@v8/session-realtime";
import type { TodoHudItem } from "@/components/chat/TodosHUD";
import { WorkspaceWorkbenchPanel } from "@/components/chat/WorkspaceWorkbenchPanel";
import { McpAppRenderer } from "@/components/chat/McpAppFrame";
import { ArtifactRenderer } from "./ArtifactRenderer";
import { WorkspaceFileRenderer } from "./WorkspaceFileRenderer";
import { createBrowserDocument } from "@/lib/workbench";

const BrowserRenderer = dynamic(() => import("./BrowserRenderer").then((mod) => mod.BrowserRenderer), {
    ssr: false,
    loading: () => <div className="flex h-full items-center justify-center text-xs text-muted-foreground">正在连接浏览器画面…</div>,
});

type WorkbenchShellProps = {
    sessionId: string;
    messages: Message[];
    processes: AdminProcessRef[];
    todos: TodoHudItem[];
    todoStale?: boolean;
    runtimeModel: RuntimeStageModel;
};

function documentIcon(kind: string) {
    if (kind === "workspace_file") return FileCode2;
    if (kind === "artifact") return Box;
    if (kind === "browser") return Globe2;
    return LayoutPanelTop;
}

export function WorkbenchShell(props: WorkbenchShellProps) {
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
    const panelRef = useRef<HTMLElement | null>(null);
    const preparedSessionRef = useRef("");
    const [containerWidth, setContainerWidth] = useState(() => typeof window === "undefined" ? 1200 : window.innerWidth);
    const [creatingBrowser, setCreatingBrowser] = useState(false);
    const [browserError, setBrowserError] = useState("");

    useEffect(() => {
        const parent = panelRef.current?.parentElement;
        if (!parent || typeof ResizeObserver === "undefined") return;
        const observer = new ResizeObserver(([entry]) => setContainerWidth(entry.contentRect.width));
        observer.observe(parent);
        setContainerWidth(parent.getBoundingClientRect().width);
        return () => observer.disconnect();
    }, [mode]);

    useEffect(() => {
        if (mode === "closed" || boundSessionId !== props.sessionId || preparedSessionRef.current === props.sessionId) return;
        preparedSessionRef.current = props.sessionId;
        const controller = new AbortController();
        void fetch(`/api/workbench/sessions/${encodeURIComponent(props.sessionId)}/browser/prepare`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ browserKind: "chrome" }),
            signal: controller.signal,
        }).catch(() => {
            if (!controller.signal.aborted) preparedSessionRef.current = "";
        });
        return () => controller.abort();
    }, [boundSessionId, mode, props.sessionId]);

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

    const createBrowser = async () => {
        if (creatingBrowser) return;
        setCreatingBrowser(true);
        setBrowserError("");
        try {
            const response = await fetch(`/api/workbench/sessions/${encodeURIComponent(props.sessionId)}/browser-sessions`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url: "about:blank", focusRequested: true, userInitiated: true }),
            });
            const payload = await response.json().catch(() => ({})) as Record<string, unknown>;
            const browserSessionId = String(payload.browserSessionId || "").trim();
            if (!response.ok || !browserSessionId) {
                const detail = payload.detail && typeof payload.detail === "object" ? payload.detail as Record<string, unknown> : {};
                throw new Error(String(detail.message || payload.error || "无法创建浏览器会话。"));
            }
            openDocument(createBrowserDocument({ browserSessionId, sessionId: props.sessionId }), { activate: true, mode: "split" });
        } catch (reason) {
            setBrowserError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setCreatingBrowser(false);
        }
    };

    const content = (() => {
        if (creatingBrowser) {
            return (
                <div className="flex h-full min-h-0 flex-col bg-background">
                    <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border/60 px-2">
                        <button type="button" disabled className="rounded-lg p-1.5 text-muted-foreground/35" aria-label="后退">←</button>
                        <div className="mx-1 h-7 min-w-0 flex-1 rounded-lg bg-muted/55" />
                        <LoaderCircle className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                    </div>
                    <div className="flex min-h-0 flex-1 items-center justify-center bg-background">
                        <div className="text-center text-muted-foreground">
                            <Globe2 className="mx-auto h-8 w-8 opacity-55" />
                            <div className="mt-3 text-sm text-foreground">正在打开浏览器</div>
                            <div className="mt-1 text-xs">浏览器就绪后会自动进入可控制页面。</div>
                        </div>
                    </div>
                </div>
            );
        }
        if (document.status === "unavailable") {
            return <div className="flex h-full items-center justify-center px-8 text-center text-sm text-muted-foreground">{document.unavailableReason || "该内容当前不可用。"}</div>;
        }
        if (document.kind === "session_overview") return <WorkspaceWorkbenchPanel {...props} />;
        if (document.kind === "workspace_file") return <WorkspaceFileRenderer document={document} />;
        if (document.kind === "artifact") return <ArtifactRenderer document={document} />;
        if (document.kind === "ui_app") return <McpAppRenderer mcpApp={document.subjectRef.app} />;
        return <BrowserRenderer document={document} />;
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
                <div role="tablist" aria-label="工作台文档" className="scrollbar-none flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
                    {tabs.map((tab) => {
                    const Icon = documentIcon(tab.document.kind);
                    const active = tab.document.documentId === document.documentId;
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
                <button
                    type="button"
                    onClick={() => void createBrowser()}
                    className={cn("inline-flex h-8 shrink-0 items-center gap-1.5 rounded-xl px-2.5 text-[11px] text-muted-foreground hover:bg-muted/60 hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary", browserError && "text-destructive")}
                    aria-label="打开浏览器"
                    title={browserError || "打开浏览器"}
                    disabled={creatingBrowser}
                >
                    {creatingBrowser ? <LoaderCircle className="h-3.5 w-3.5 animate-spin" /> : <Globe2 className="h-3.5 w-3.5" />}
                    <span>浏览器</span>
                    <Plus className="h-3 w-3" />
                </button>
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
