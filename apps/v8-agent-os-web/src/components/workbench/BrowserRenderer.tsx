"use client";

import { FormEvent, PointerEvent as ReactPointerEvent, WheelEvent as ReactWheelEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ArrowLeft, ArrowRight, Bot, Hand, LoaderCircle, Plus, RefreshCw, RotateCcw, X } from "lucide-react";

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
    const textInputRef = useRef<HTMLTextAreaElement | null>(null);
    const pointerFrameRef = useRef<number | null>(null);
    const pendingPointerRef = useRef<Record<string, unknown> | null>(null);
    const requestCounterRef = useRef(0);
    const composingRef = useRef(false);
    const ignoreNextInputRef = useRef(false);
    const [status, setStatus] = useState<BrowserStatus | null>(null);
    const [connected, setConnected] = useState(false);
    const [hasControl, setHasControl] = useState(false);
    const [streamMode, setStreamMode] = useState("connecting");
    const [frameUrl, setFrameUrl] = useState("");
    const [frameMetadata, setFrameMetadata] = useState<Record<string, unknown>>({});
    const [address, setAddress] = useState("about:blank");
    const [error, setError] = useState("");
    const [revision, setRevision] = useState(0);
    const [zoom, setZoom] = useState(100);
    const [inputBuffer, setInputBuffer] = useState("");
    const markDocumentUnavailable = useWorkbenchStore((state) => state.markDocumentUnavailable);

    const currentPage = useMemo(
        () => status?.pages.find((page) => page.pageId === status.currentPageId)
            || status?.pages.find((page) => page.active)
            || status?.pages[0]
            || null,
        [status],
    );

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

        const connect = async () => {
            try {
                const statusResponse = await fetch(`/api/workbench/browser-sessions/${encodeURIComponent(browserSessionId)}`, { cache: "no-store" });
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

                const ticketResponse = await fetch(`/api/workbench/browser-sessions/${encodeURIComponent(browserSessionId)}/ws-ticket`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                });
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
    }, [browserSessionId, document.documentId, markDocumentUnavailable, revision]);

    useEffect(() => {
        if (!hasControl || !connected) return;
        const interval = window.setInterval(() => sendCommand("heartbeat"), 5_000);
        return () => window.clearInterval(interval);
    }, [connected, hasControl, sendCommand]);

    const pagePayload = useCallback(() => currentPage ? { pageId: currentPage.pageId } : {}, [currentPage]);

    const pointForEvent = useCallback((event: { clientX: number; clientY: number }) => {
        const image = imageRef.current;
        if (!image) return null;
        const rect = image.getBoundingClientRect();
        if (!rect.width || !rect.height) return null;
        const naturalWidth = image.naturalWidth || Number(frameMetadata.deviceWidth || 0) || rect.width;
        const naturalHeight = image.naturalHeight || Number(frameMetadata.deviceHeight || 0) || rect.height;
        return {
            x: Math.max(0, Math.min(naturalWidth, (event.clientX - rect.left) * naturalWidth / rect.width)),
            y: Math.max(0, Math.min(naturalHeight, (event.clientY - rect.top) * naturalHeight / rect.height)),
        };
    }, [frameMetadata.deviceHeight, frameMetadata.deviceWidth]);

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
        <div className="flex h-full min-h-0 flex-col bg-background">
            <div className="flex h-8 shrink-0 items-center gap-1 border-b border-border/60 px-1.5">
                <button type="button" disabled={!hasControl} onClick={() => sendCommand("back", pagePayload())} className="rounded-sm p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label="后退"><ArrowLeft className="h-3.5 w-3.5" /></button>
                <button type="button" disabled={!hasControl} onClick={() => sendCommand("forward", pagePayload())} className="rounded-sm p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label="前进"><ArrowRight className="h-3.5 w-3.5" /></button>
                <button type="button" disabled={!hasControl} onClick={() => sendCommand("reload", pagePayload())} className="rounded-sm p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label="刷新"><RefreshCw className="h-3.5 w-3.5" /></button>
                <form onSubmit={submitAddress} className="mx-1 flex min-w-0 flex-1 items-center border-b border-border/80 focus-within:border-primary">
                    <input value={address} onChange={(event) => setAddress(event.target.value)} className="h-6 min-w-0 flex-1 bg-transparent px-1.5 text-[11px] outline-none" aria-label="浏览器地址" spellCheck={false} />
                </form>
                <select value={zoom} onChange={(event) => setZoom(Number(event.target.value))} className="h-6 bg-transparent text-[10px] text-muted-foreground outline-none" aria-label="显示缩放">
                    <option value={80}>80%</option><option value={100}>100%</option><option value={125}>125%</option><option value={150}>150%</option>
                </select>
                <button
                    type="button"
                    onClick={() => {
                        if (hasControl) sendCommand("release_control");
                        else sendCommand("take_control");
                    }}
                    disabled={!connected || unavailable}
                    className={cn("inline-flex h-6 items-center gap-1 rounded-sm px-2 text-[10px] disabled:opacity-40", hasControl ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground")}
                >
                    {hasControl ? <Hand className="h-3 w-3" /> : <Bot className="h-3 w-3" />}
                    {hasControl ? "交还 Agent" : "接管"}
                </button>
            </div>

            <div className="scrollbar-none flex h-8 shrink-0 items-stretch overflow-x-auto border-b border-border/60 bg-muted/10">
                {(status?.pages || []).map((page) => (
                    <div key={page.pageId} className={cn("flex min-w-[120px] max-w-[210px] items-center border-r border-border/50", page.pageId === currentPage?.pageId && "bg-background")}>
                        <button type="button" disabled={!hasControl} onClick={() => sendCommand("activate", { pageId: page.pageId })} className="min-w-0 flex-1 truncate px-2 text-left text-[10px] disabled:cursor-default" title={page.title || page.url}>{page.title || "新标签页"}</button>
                        <button type="button" disabled={!hasControl || (status?.pages.length || 0) <= 1} onClick={() => sendCommand("close_page", { pageId: page.pageId })} className="mr-1 rounded-sm p-1 text-muted-foreground hover:bg-muted disabled:hidden" aria-label={`关闭 ${page.title || "标签页"}`}><X className="h-3 w-3" /></button>
                    </div>
                ))}
                <button type="button" disabled={!hasControl} onClick={() => sendCommand("new_tab", { url: "about:blank" })} className="px-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label="新建标签页"><Plus className="h-3.5 w-3.5" /></button>
            </div>

            {status?.externalWindow ? <div className="flex h-7 shrink-0 items-center gap-1.5 border-b border-amber-500/25 bg-amber-500/8 px-2 text-[10px] text-amber-700 dark:text-amber-300"><AlertTriangle className="h-3 w-3" />附着的是已有可见浏览器；外部窗口仍在运行。</div> : null}
            {streamMode === "screenshot_fallback" ? <div className="h-6 shrink-0 border-b border-border/60 px-2 text-[10px] leading-6 text-muted-foreground">CDP Screencast 不可用，已降级为约 2fps 截图流。</div> : null}
            {error ? <div className="flex min-h-7 shrink-0 items-center gap-2 border-b border-destructive/20 bg-destructive/5 px-2 text-[10px] text-destructive"><span className="min-w-0 flex-1 truncate">{error}</span><button type="button" onClick={() => setRevision((value) => value + 1)} className="rounded-sm p-1 hover:bg-destructive/10" aria-label="重新连接"><RotateCcw className="h-3 w-3" /></button></div> : null}

            <div className="relative min-h-0 flex-1 overflow-auto bg-[#111]">
                {frameUrl ? (
                    <div className="flex min-h-full min-w-full items-center justify-center">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                            ref={imageRef}
                            src={frameUrl}
                            alt="浏览器实时画面"
                            draggable={false}
                            className={cn("max-h-full max-w-full select-none", hasControl ? "cursor-default" : "cursor-not-allowed")}
                            style={{ transform: `scale(${zoom / 100})`, transformOrigin: "center" }}
                            onPointerMove={queuePointerMove}
                            onPointerDown={(event) => sendPointerButton("mousePressed", event)}
                            onPointerUp={(event) => sendPointerButton("mouseReleased", event)}
                            onContextMenu={(event) => event.preventDefault()}
                            onWheel={sendWheel}
                        />
                    </div>
                ) : (
                    <div className="flex h-full items-center justify-center gap-2 text-xs text-white/55"><LoaderCircle className="h-4 w-4 animate-spin" />正在等待浏览器画面…</div>
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
            </div>
            <div className="flex h-7 shrink-0 items-center justify-between gap-2 border-t border-border/60 px-2 text-[9px] text-muted-foreground">
                <span>{connected ? (hasControl ? "你正在控制；5 秒心跳，15 秒租约" : "Agent 控制；点击“接管”后可交互") : "未连接"}</span>
                <span className="truncate">首期不支持文件选择器、下载管理器、Passkey、硬件密钥、媒体权限、扩展、DevTools 与 DRM。</span>
            </div>
        </div>
    );
}
