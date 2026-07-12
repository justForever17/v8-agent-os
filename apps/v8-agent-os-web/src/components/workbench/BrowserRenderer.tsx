"use client";

import { FormEvent, MouseEvent as ReactMouseEvent, PointerEvent as ReactPointerEvent, WheelEvent as ReactWheelEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Globe2, LoaderCircle, MoreHorizontal, Minus, Plus, RefreshCw, RotateCcw, X } from "lucide-react";

import { cn } from "@/lib/utils";
import type { BrowserWorkbenchDocument } from "@/lib/workbench";
import { useWorkbenchStore } from "@/store/workbench-store";


type BrowserPage = {
    pageId: string;
    title: string;
    url: string;
    active: boolean;
};

type BrowserStatus = {
    browserSessionId: string;
    status: string;
    currentPageId?: string | null;
    pages: BrowserPage[];
    managedHeadless?: boolean;
    externalWindow?: boolean;
    unavailableReason?: string | null;
    stream?: { mode?: string };
    control?: {
        state?: "agent" | "user";
        leaseTtlSeconds?: number;
        heartbeatSeconds?: number;
        agentReobserveRequired?: boolean;
    };
    limitations?: string[];
};

type BrowserViewportMode = "adaptive" | "desktop" | "mobile";
type BrowserContextMenuState = { x: number; y: number };

function recordOf(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function errorText(value: unknown, fallback: string) {
    const root = recordOf(value);
    const detail = recordOf(root.detail);
    return String(detail.message || root.message || root.error || fallback);
}

function buttonName(button: number) {
    if (button === 1) return "middle";
    if (button === 2) return "right";
    return "left";
}

function buttonFromButtons(buttons: number) {
    if (buttons & 2) return "right";
    if (buttons & 4) return "middle";
    if (buttons & 1) return "left";
    return "none";
}

function modifiers(event: { altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean }) {
    return [
        event.altKey ? "alt" : "",
        event.ctrlKey ? "control" : "",
        event.metaKey ? "meta" : "",
        event.shiftKey ? "shift" : "",
    ].filter(Boolean);
}

export function BrowserRenderer({ document }: { document: BrowserWorkbenchDocument }) {
    const browserSessionId = document.subjectRef.browserSessionId;
    const socketRef = useRef<WebSocket | null>(null);
    const frameUrlRef = useRef("");
    const imageRef = useRef<HTMLImageElement | null>(null);
    const viewportRef = useRef<HTMLDivElement | null>(null);
    const textInputRef = useRef<HTMLTextAreaElement | null>(null);
    const pointerFrameRef = useRef<number | null>(null);
    const pendingPointerRef = useRef<Record<string, unknown> | null>(null);
    const requestCounterRef = useRef(0);
    const composingRef = useRef(false);
    const ignoreNextInputRef = useRef(false);
    const autoControlRequestedRef = useRef(false);
    const viewportTimerRef = useRef<number | null>(null);
    const lastViewportRef = useRef("");
    const [status, setStatus] = useState<BrowserStatus | null>(null);
    const [connected, setConnected] = useState(false);
    const [hasControl, setHasControl] = useState(false);
    const [, setStreamMode] = useState("connecting");
    const [frameUrl, setFrameUrl] = useState("");
    const [frameMetadata, setFrameMetadata] = useState<Record<string, unknown>>({});
    const [address, setAddress] = useState("about:blank");
    const [error, setError] = useState("");
    const [revision, setRevision] = useState(0);
    const [zoom, setZoom] = useState(100);
    const [inputBuffer, setInputBuffer] = useState("");
    const [menuOpen, setMenuOpen] = useState(false);
    const [viewportMode, setViewportMode] = useState<BrowserViewportMode>("adaptive");
    const [contextMenu, setContextMenu] = useState<BrowserContextMenuState | null>(null);
    const markDocumentUnavailable = useWorkbenchStore((state) => state.markDocumentUnavailable);

    const currentPage = useMemo(
        () => status?.pages.find((page) => page.pageId === status.currentPageId)
            || status?.pages.find((page) => page.active)
            || status?.pages[0]
            || null,
        [status],
    );
    const pagePayload = useCallback(() => currentPage ? { pageId: currentPage.pageId } : {}, [currentPage]);

    useEffect(() => {
        if (currentPage?.url) setAddress(currentPage.url);
    }, [currentPage?.url]);

    const sendCommand = useCallback((action: string, payload: Record<string, unknown> = {}) => {
        const socket = socketRef.current;
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            setError("浏览器连接尚未就绪。");
            return "";
        }
        const requestId = `${action}-${++requestCounterRef.current}`;
        socket.send(JSON.stringify({ action, requestId, ...payload }));
        return requestId;
    }, []);

    useEffect(() => {
        let cancelled = false;
        let socket: WebSocket | null = null;
        setError("");
        setConnected(false);
        setHasControl(false);
        setStreamMode("connecting");
        autoControlRequestedRef.current = false;

        const connect = async () => {
            try {
                const [statusResponse, ticketResponse] = await Promise.all([
                    fetch(`/api/workbench/browser-sessions/${encodeURIComponent(browserSessionId)}`, { cache: "no-store" }),
                    fetch(`/api/workbench/browser-sessions/${encodeURIComponent(browserSessionId)}/ws-ticket`, {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                    }),
                ]);
                const initialStatus = await statusResponse.json().catch(() => ({})) as BrowserStatus;
                if (!statusResponse.ok) {
                    const message = errorText(initialStatus, "无法读取浏览器会话。");
                    if (statusResponse.status === 404 || statusResponse.status === 410) {
                        markDocumentUnavailable(document.documentId, message);
                    }
                    throw new Error(message);
                }
                if (cancelled) return;
                setStatus(initialStatus);
                const ticketPayload = await ticketResponse.json().catch(() => ({})) as Record<string, unknown>;
                const ticket = String(ticketPayload.ticket || "").trim();
                if (!ticketResponse.ok || !ticket) throw new Error(errorText(ticketPayload, "无法取得浏览器连接票据。"));
                if (cancelled) return;

                const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
                const wsUrl = `${protocol}//${window.location.host}/api/workbench-browser-ws/browser-sessions/${encodeURIComponent(browserSessionId)}/ws?ticket=${encodeURIComponent(ticket)}`;
                socket = new WebSocket(wsUrl);
                socket.binaryType = "blob";
                socketRef.current = socket;
                socket.onopen = () => {
                    if (!cancelled) setConnected(true);
                };
                socket.onmessage = (event) => {
                    if (cancelled) return;
                    if (event.data instanceof Blob) {
                        const nextUrl = URL.createObjectURL(event.data);
                        const previousUrl = frameUrlRef.current;
                        frameUrlRef.current = nextUrl;
                        setFrameUrl(nextUrl);
                        if (previousUrl) URL.revokeObjectURL(previousUrl);
                        return;
                    }
                    if (typeof event.data !== "string") return;
                    try {
                        const payload = JSON.parse(event.data) as Record<string, unknown>;
                        const type = String(payload.type || "");
                        if (type === "hello") {
                            setStatus(recordOf(payload.status) as BrowserStatus);
                            if (!autoControlRequestedRef.current) {
                                autoControlRequestedRef.current = true;
                                sendCommand("take_control");
                            }
                        } else if (type === "status") {
                            setStatus(recordOf(payload.status) as BrowserStatus);
                        } else if (type === "stream_status") {
                            setStreamMode(String(payload.mode || "unknown"));
                        } else if (type === "frame_meta") {
                            setFrameMetadata(recordOf(payload.metadata));
                        } else if (type === "unavailable") {
                            setError(String(payload.reason || "浏览器会话已不可用。"));
                        } else if (type === "command_result") {
                            const requestId = String(payload.requestId || "");
                            if (payload.ok === true) {
                                const result = recordOf(payload.result);
                                if (result.browserSessionId) setStatus(result as BrowserStatus);
                                if (requestId.startsWith("take_control-")) setHasControl(true);
                                if (requestId.startsWith("release_control-")) setHasControl(false);
                                setError("");
                            } else {
                                const rpcError = recordOf(payload.error);
                                if (requestId.startsWith("heartbeat-") || requestId.startsWith("release_control-")) setHasControl(false);
                                setError(String(rpcError.message || "浏览器操作未完成。"));
                            }
                        }
                    } catch {
                        setError("浏览器返回了无法识别的状态消息。");
                    }
                };
                socket.onerror = () => {
                    if (!cancelled) setError("浏览器画面连接失败。");
                };
                socket.onclose = () => {
                    if (!cancelled) {
                        setConnected(false);
                        setHasControl(false);
                    }
                };
            } catch (reason) {
                if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
            }
        };
        void connect();
        return () => {
            cancelled = true;
            if (pointerFrameRef.current !== null) cancelAnimationFrame(pointerFrameRef.current);
            pointerFrameRef.current = null;
            pendingPointerRef.current = null;
            socket?.close(1000, "Workbench renderer unmounted");
            socketRef.current = null;
            if (frameUrlRef.current) URL.revokeObjectURL(frameUrlRef.current);
            frameUrlRef.current = "";
        };
    }, [browserSessionId, document.documentId, markDocumentUnavailable, revision, sendCommand]);

    useEffect(() => {
        if (!hasControl || !connected) return;
        const interval = window.setInterval(() => sendCommand("heartbeat"), 5_000);
        return () => window.clearInterval(interval);
    }, [connected, hasControl, sendCommand]);

    useEffect(() => {
        if (!connected || !hasControl) return;
        const viewport = viewportRef.current;
        if (!viewport) return;

        const syncViewport = () => {
            if (viewportTimerRef.current !== null) window.clearTimeout(viewportTimerRef.current);
            viewportTimerRef.current = window.setTimeout(() => {
                const rect = viewport.getBoundingClientRect();
                const target = viewportMode === "desktop"
                    ? { width: 1440, height: 900 }
                    : viewportMode === "mobile"
                        ? { width: 390, height: 844 }
                        : {
                            width: Math.max(320, Math.min(1920, Math.round(rect.width))),
                            height: Math.max(360, Math.min(1400, Math.round(rect.height))),
                        };
                const key = `${currentPage?.pageId || "page"}:${target.width}x${target.height}`;
                if (lastViewportRef.current === key) return;
                lastViewportRef.current = key;
                sendCommand("set_viewport", { ...pagePayload(), ...target });
            }, 120);
        };

        syncViewport();
        const observer = new ResizeObserver(syncViewport);
        if (viewportMode === "adaptive") observer.observe(viewport);
        return () => {
            observer.disconnect();
            if (viewportTimerRef.current !== null) window.clearTimeout(viewportTimerRef.current);
            viewportTimerRef.current = null;
        };
    }, [connected, currentPage?.pageId, hasControl, pagePayload, sendCommand, viewportMode]);

    useEffect(() => {
        if (!contextMenu) return;
        const closeOnEscape = (event: KeyboardEvent) => {
            if (event.key === "Escape") setContextMenu(null);
        };
        window.addEventListener("keydown", closeOnEscape);
        return () => window.removeEventListener("keydown", closeOnEscape);
    }, [contextMenu]);

    const pointForEvent = useCallback((event: { clientX: number; clientY: number }) => {
        const image = imageRef.current;
        if (!image) return null;
        const rect = image.getBoundingClientRect();
        if (!rect.width || !rect.height) return null;
        const pageScaleFactor = Math.max(0.01, Number(frameMetadata.pageScaleFactor || 1));
        const viewportWidth = (Number(frameMetadata.deviceWidth || 0) || image.naturalWidth || rect.width) / pageScaleFactor;
        const viewportHeight = (Number(frameMetadata.deviceHeight || 0) || image.naturalHeight || rect.height) / pageScaleFactor;
        return {
            x: Math.max(0, Math.min(viewportWidth, (event.clientX - rect.left) * viewportWidth / rect.width)),
            y: Math.max(0, Math.min(viewportHeight, (event.clientY - rect.top) * viewportHeight / rect.height)),
        };
    }, [frameMetadata.deviceHeight, frameMetadata.deviceWidth, frameMetadata.pageScaleFactor]);

    const queuePointerMove = useCallback((event: ReactPointerEvent<HTMLImageElement>) => {
        if (!hasControl) return;
        const point = pointForEvent(event);
        if (!point) return;
        pendingPointerRef.current = {
            ...pagePayload(),
            ...point,
            buttons: event.buttons,
            button: buttonFromButtons(event.buttons),
            modifiers: modifiers(event),
        };
        if (pointerFrameRef.current !== null) return;
        pointerFrameRef.current = requestAnimationFrame(() => {
            pointerFrameRef.current = null;
            const payload = pendingPointerRef.current;
            pendingPointerRef.current = null;
            if (payload) sendCommand("mouseMoved", payload);
        });
    }, [hasControl, pagePayload, pointForEvent, sendCommand]);

    const sendPointerButton = useCallback((action: "mousePressed" | "mouseReleased", event: ReactPointerEvent<HTMLImageElement>) => {
        if (!hasControl) return;
        const point = pointForEvent(event);
        if (!point) return;
        if (action === "mousePressed") {
            event.currentTarget.setPointerCapture(event.pointerId);
            textInputRef.current?.focus();
        } else if (event.currentTarget.hasPointerCapture(event.pointerId)) {
            event.currentTarget.releasePointerCapture(event.pointerId);
        }
        sendCommand(action, {
            ...pagePayload(),
            ...point,
            button: buttonName(event.button),
            buttons: event.buttons,
            clickCount: action === "mousePressed" ? 1 : 0,
            modifiers: modifiers(event),
        });
    }, [hasControl, pagePayload, pointForEvent, sendCommand]);

    const sendWheel = useCallback((event: ReactWheelEvent<HTMLImageElement>) => {
        if (!hasControl) return;
        event.preventDefault();
        const point = pointForEvent(event);
        if (!point) return;
        sendCommand("mouseWheel", {
            ...pagePayload(),
            ...point,
            deltaX: event.deltaX,
            deltaY: event.deltaY,
            modifiers: modifiers(event),
        });
    }, [hasControl, pagePayload, pointForEvent, sendCommand]);

    const openContextMenu = useCallback((event: ReactMouseEvent<HTMLImageElement>) => {
        event.preventDefault();
        if (!hasControl) return;
        const viewport = viewportRef.current;
        if (!viewport) return;
        const rect = viewport.getBoundingClientRect();
        const menuWidth = 176;
        const menuHeight = 184;
        setContextMenu({
            x: Math.max(8, Math.min(rect.width - menuWidth - 8, event.clientX - rect.left + viewport.scrollLeft)),
            y: Math.max(8, Math.min(rect.height - menuHeight - 8, event.clientY - rect.top + viewport.scrollTop)),
        });
    }, [hasControl]);

    const submitAddress = (event: FormEvent) => {
        event.preventDefault();
        if (!hasControl) {
            setError("请先接管浏览器，再执行导航。");
            return;
        }
        sendCommand("navigate", { ...pagePayload(), url: address });
    };

    const unavailable = status?.status === "unavailable" || document.status === "unavailable";

    return (
        <div className="relative flex h-full min-h-0 flex-col bg-background">
            <div className="scrollbar-none flex h-9 shrink-0 items-center gap-1 overflow-x-auto border-b border-border/60 px-1.5">
                {(status?.pages || []).map((page) => (
                    <div key={page.pageId} className={cn("group flex h-7 min-w-[118px] max-w-[210px] items-center rounded-xl", page.pageId === currentPage?.pageId ? "bg-muted/70 text-foreground" : "text-muted-foreground hover:bg-muted/35")}>
                        <button type="button" disabled={!hasControl} onClick={() => sendCommand("activate", { pageId: page.pageId })} className="min-w-0 flex-1 truncate px-2 text-left text-[10px] disabled:cursor-default" title={page.title || page.url}>{page.title || "新标签页"}</button>
                        <button type="button" disabled={!hasControl || (status?.pages.length || 0) <= 1} onClick={() => sendCommand("close_page", { pageId: page.pageId })} className="mr-1 hidden rounded-md p-1 text-muted-foreground hover:bg-background disabled:hidden group-hover:block" aria-label={`关闭 ${page.title || "标签页"}`}><X className="h-3 w-3" /></button>
                    </div>
                ))}
                <button type="button" disabled={!hasControl} onClick={() => sendCommand("new_tab", { url: "about:blank" })} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label="新建标签页"><Plus className="h-3.5 w-3.5" /></button>
            </div>

            <div className="flex h-10 shrink-0 items-center gap-1 border-b border-border/60 px-1.5">
                <button type="button" disabled={!hasControl} onClick={() => sendCommand("back", pagePayload())} className="rounded-sm p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label="后退"><ArrowLeft className="h-3.5 w-3.5" /></button>
                <button type="button" disabled={!hasControl} onClick={() => sendCommand("forward", pagePayload())} className="rounded-sm p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label="前进"><ArrowRight className="h-3.5 w-3.5" /></button>
                <button type="button" disabled={!hasControl} onClick={() => sendCommand("reload", pagePayload())} className="rounded-sm p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label="刷新"><RefreshCw className="h-3.5 w-3.5" /></button>
                <form onSubmit={submitAddress} className="mx-1 flex min-w-0 flex-1 items-center rounded-xl bg-muted/55 focus-within:ring-2 focus-within:ring-primary/20">
                    <input value={address} onChange={(event) => setAddress(event.target.value)} placeholder="输入 URL" className="h-7 min-w-0 flex-1 bg-transparent px-2.5 text-[11px] outline-none" aria-label="浏览器地址" spellCheck={false} />
                </form>
                <button
                    type="button"
                    onClick={() => setMenuOpen((value) => !value)}
                    className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
                    aria-label="浏览器菜单"
                >
                    <MoreHorizontal className="h-4 w-4" />
                </button>
            </div>
            {error ? <div className="flex min-h-7 shrink-0 items-center gap-2 border-b border-destructive/20 bg-destructive/5 px-2 text-[10px] text-destructive"><span className="min-w-0 flex-1 truncate">{error}</span><button type="button" onClick={() => setRevision((value) => value + 1)} className="rounded-sm p-1 hover:bg-destructive/10" aria-label="重新连接"><RotateCcw className="h-3 w-3" /></button></div> : null}

            {menuOpen ? (
                <div className="absolute right-2 top-[76px] z-20 w-52 rounded-xl border border-border bg-popover p-2 text-xs text-popover-foreground shadow-lg">
                    <div className="flex items-center justify-between gap-2 px-2 py-1.5">
                        <span>显示比例</span>
                        <div className="flex items-center rounded-lg bg-muted/60">
                            <button type="button" className="p-1.5" onClick={() => setZoom((value) => Math.max(50, value - 10))} aria-label="缩小"><Minus className="h-3 w-3" /></button>
                            <span className="w-10 text-center tabular-nums">{zoom}%</span>
                            <button type="button" className="p-1.5" onClick={() => setZoom((value) => Math.min(200, value + 10))} aria-label="放大"><Plus className="h-3 w-3" /></button>
                        </div>
                    </div>
                    <button type="button" className="w-full rounded-lg px-2 py-2 text-left hover:bg-muted" onClick={() => { setMenuOpen(false); setRevision((value) => value + 1); }}>重新连接</button>
                    <button type="button" disabled={!connected || unavailable} className="w-full rounded-lg px-2 py-2 text-left hover:bg-muted disabled:opacity-40" onClick={() => { setMenuOpen(false); sendCommand(hasControl ? "release_control" : "take_control"); }}>{hasControl ? "交还 Agent" : "重新接管"}</button>
                    <div className="mt-1 border-t border-border/60 pt-1.5">
                        <div className="px-2 py-1 text-[10px] text-muted-foreground">页面尺寸</div>
                        <div className="grid grid-cols-3 gap-1">
                            {([
                                ["adaptive", "跟随侧栏"],
                                ["desktop", "桌面"],
                                ["mobile", "手机"],
                            ] as const).map(([mode, label]) => (
                                <button
                                    key={mode}
                                    type="button"
                                    aria-pressed={viewportMode === mode}
                                    className={cn(
                                        "rounded-md px-1.5 py-1.5 text-[10px] transition-colors hover:bg-muted",
                                        viewportMode === mode && "bg-muted text-foreground",
                                    )}
                                    onClick={() => {
                                        lastViewportRef.current = "";
                                        setViewportMode(mode);
                                        setZoom(100);
                                    }}
                                >
                                    {label}
                                </button>
                            ))}
                        </div>
                    </div>
                </div>
            ) : null}

            <div ref={viewportRef} className="relative min-h-0 flex-1 overflow-auto bg-white dark:bg-zinc-950">
                {frameUrl ? (
                    <div className="flex min-h-full min-w-full items-center justify-center">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                            ref={imageRef}
                            src={frameUrl}
                            alt="浏览器实时画面"
                            draggable={false}
                            className={cn("max-h-full max-w-full select-none", hasControl ? "cursor-default" : "cursor-not-allowed")}
                            style={{ transform: `scale(${zoom / 100})`, transformOrigin: "center", touchAction: "none" }}
                            onPointerMove={queuePointerMove}
                            onPointerDown={(event) => {
                                if (event.button !== 2) setContextMenu(null);
                                sendPointerButton("mousePressed", event);
                            }}
                            onPointerUp={(event) => sendPointerButton("mouseReleased", event)}
                            onContextMenu={openContextMenu}
                            onWheel={sendWheel}
                        />
                    </div>
                ) : (
                    <div className="flex h-full items-center justify-center">
                        <div className="text-center text-muted-foreground">
                            {connected ? <Globe2 className="mx-auto h-8 w-8 opacity-55" /> : <LoaderCircle className="mx-auto h-5 w-5 animate-spin" />}
                            <div className="mt-3 text-sm text-foreground">{connected ? "开始浏览" : "正在连接浏览器"}</div>
                            <div className="mt-1 text-xs">{connected ? "在上方输入 URL 以打开页面" : "连接完成后即可直接控制"}</div>
                        </div>
                    </div>
                )}
                <textarea
                    ref={textInputRef}
                    value={inputBuffer}
                    onChange={(event) => {
                        const value = event.target.value;
                        if (ignoreNextInputRef.current) {
                            ignoreNextInputRef.current = false;
                            setInputBuffer("");
                            return;
                        }
                        setInputBuffer(value);
                        if (!composingRef.current && value) {
                            sendCommand("insertText", { ...pagePayload(), text: value });
                            setInputBuffer("");
                        }
                    }}
                    onCompositionStart={() => { composingRef.current = true; }}
                    onCompositionEnd={(event) => {
                        composingRef.current = false;
                        ignoreNextInputRef.current = true;
                        window.setTimeout(() => { ignoreNextInputRef.current = false; }, 0);
                        const value = event.data || inputBuffer;
                        if (value) sendCommand("insertText", { ...pagePayload(), text: value });
                        setInputBuffer("");
                    }}
                    onPaste={(event) => {
                        event.preventDefault();
                        const value = event.clipboardData.getData("text");
                        if (value) sendCommand("insertText", { ...pagePayload(), text: value });
                    }}
                    onKeyDown={(event) => {
                        if (!hasControl || event.nativeEvent.isComposing) return;
                        if (event.key === "Tab") event.preventDefault();
                        sendCommand("rawKeyDown", {
                            ...pagePayload(), key: event.key, code: event.code,
                            modifiers: modifiers(event), autoRepeat: event.repeat,
                        });
                    }}
                    onKeyUp={(event) => {
                        if (!hasControl || event.nativeEvent.isComposing) return;
                        sendCommand("keyUp", { ...pagePayload(), key: event.key, code: event.code, modifiers: modifiers(event) });
                    }}
                    className="pointer-events-none absolute bottom-0 left-0 h-px w-px resize-none opacity-0"
                    aria-label="浏览器键盘输入"
                    autoCapitalize="none"
                    autoCorrect="off"
                    spellCheck={false}
                />
                {contextMenu ? (
                    <div
                        role="menu"
                        aria-label="浏览器右键菜单"
                        className="absolute z-30 w-44 overflow-hidden rounded-xl border border-border/70 bg-popover/98 p-1 text-xs text-popover-foreground shadow-xl backdrop-blur"
                        style={{ left: contextMenu.x, top: contextMenu.y }}
                        onPointerDown={(event) => event.stopPropagation()}
                    >
                        <button type="button" role="menuitem" className="w-full rounded-lg px-3 py-2 text-left hover:bg-muted" onClick={() => { setContextMenu(null); sendCommand("back", pagePayload()); }}>后退</button>
                        <button type="button" role="menuitem" className="w-full rounded-lg px-3 py-2 text-left hover:bg-muted" onClick={() => { setContextMenu(null); sendCommand("forward", pagePayload()); }}>前进</button>
                        <button type="button" role="menuitem" className="w-full rounded-lg px-3 py-2 text-left hover:bg-muted" onClick={() => { setContextMenu(null); sendCommand("reload", pagePayload()); }}>刷新</button>
                        <button type="button" role="menuitem" className="w-full rounded-lg px-3 py-2 text-left hover:bg-muted" onClick={() => { setContextMenu(null); sendCommand("new_tab", { url: "about:blank" }); }}>新建标签页</button>
                        <button
                            type="button"
                            role="menuitem"
                            className="w-full rounded-lg px-3 py-2 text-left hover:bg-muted"
                            onClick={() => {
                                setContextMenu(null);
                                void navigator.clipboard?.writeText(currentPage?.url || address);
                            }}
                        >
                            复制页面地址
                        </button>
                    </div>
                ) : null}
            </div>
        </div>
    );
}
