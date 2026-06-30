'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Plus, Square, TerminalSquare, X } from 'lucide-react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { cn } from '@/lib/utils';
import '@xterm/xterm/css/xterm.css';

export interface TerminalProfileView {
    id: string;
    label: string;
    command?: string;
    executable?: string;
}

export interface ManualTerminalSessionView {
    ok?: boolean;
    sessionId?: string;
    commandId?: string;
    profileId?: string;
    profileLabel?: string;
    cwd?: string;
    status?: string;
    outputDelta?: string;
    screenSnapshot?: string;
    rawScreenSnapshot?: string;
    isRunning?: boolean;
    awaitingInput?: boolean;
    usesTty?: boolean;
    returnCode?: number | string | null;
    error?: string;
    detail?: string;
}

interface ManualTerminalPanelProps {
    workspacePath?: string;
    profiles: TerminalProfileView[];
    profileId: string;
    sessions: ManualTerminalSessionView[];
    activeSessionId: string;
    busy?: boolean;
    error?: string;
    onProfileChange: (profileId: string) => void;
    onStart: () => void;
    onActivate: (sessionId: string) => void;
    onSessionSnapshot: (session: ManualTerminalSessionView) => void;
    onSendInputFallback: (sessionId: string, inputText: string) => Promise<void> | void;
    onTerminate: (sessionId: string) => Promise<void> | void;
    onCloseSession: (sessionId: string) => Promise<void> | void;
    onClosePanel: () => void;
}

function buildTerminalWsUrl(commandId?: string) {
    if (!commandId || typeof window === 'undefined') {
        return '';
    }
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/api/client/bg_processes/${encodeURIComponent(commandId)}/ws`;
}

function formatTerminalTitle(session: ManualTerminalSessionView) {
    const label = session.profileLabel || session.profileId || 'Terminal';
    const id = String(session.sessionId || session.commandId || '').trim();
    const suffix = id ? ` · ${id.slice(-6)}` : '';
    return `${label}${suffix}`;
}

function writePlainSnapshot(term: Terminal, text: string) {
    if (!text) {
        return;
    }
    term.reset();
    term.write(text.replace(/\r?\n/g, '\r\n'));
}

interface ManualTerminalXtermProps {
    session: ManualTerminalSessionView;
    error?: string;
    onSnapshot: (session: ManualTerminalSessionView) => void;
    onSendInputFallback: (sessionId: string, inputText: string) => Promise<void> | void;
    onTerminate: (sessionId: string) => Promise<void> | void;
}

function ManualTerminalXterm({
    session,
    error,
    onSnapshot,
    onSendInputFallback,
    onTerminate,
}: ManualTerminalXtermProps) {
    const terminalHostRef = useRef<HTMLDivElement | null>(null);
    const terminalRef = useRef<Terminal | null>(null);
    const fitAddonRef = useRef<FitAddon | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const lastSnapshotRef = useRef('');
    const [fallbackMode, setFallbackMode] = useState(false);

    const sessionId = String(session.sessionId || '').trim();
    const commandId = String(session.commandId || session.sessionId || '').trim();
    const snapshotText = String(session.rawScreenSnapshot || session.screenSnapshot || '').trim();

    const sendInput = useCallback((data: string) => {
        if (!data || !sessionId) {
            return;
        }
        const socket = wsRef.current;
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(data);
            return;
        }
        void onSendInputFallback(sessionId, data);
    }, [onSendInputFallback, sessionId]);

    const pasteClipboard = useCallback(async () => {
        try {
            const text = await navigator.clipboard?.readText?.();
            if (text) {
                sendInput(text);
            }
            terminalRef.current?.focus();
        } catch {
            terminalRef.current?.focus();
        }
    }, [sendInput]);

    useEffect(() => {
        const host = terminalHostRef.current;
        if (!host || !sessionId) {
            return;
        }

        host.innerHTML = '';
        const resetFallbackTimer = window.setTimeout(() => setFallbackMode(false), 0);

        const fitAddon = new FitAddon();
        const term = new Terminal({
            cursorBlink: true,
            convertEol: true,
            fontFamily: 'SF Mono, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
            fontSize: 12,
            lineHeight: 1.18,
            scrollback: 8000,
            theme: {
                background: '#05070b',
                foreground: '#e5e7eb',
                cursor: '#f8fafc',
                selectionBackground: '#334155',
                black: '#020617',
                blue: '#60a5fa',
                cyan: '#22d3ee',
                green: '#34d399',
                magenta: '#c084fc',
                red: '#fb7185',
                white: '#e5e7eb',
                yellow: '#fbbf24',
            },
        });
        term.loadAddon(fitAddon);
        term.open(host);
        terminalRef.current = term;
        fitAddonRef.current = fitAddon;
        lastSnapshotRef.current = '';

        const fit = () => {
            try {
                fitAddon.fit();
            } catch {}
        };
        window.setTimeout(fit, 30);
        const resizeObserver = new ResizeObserver(fit);
        resizeObserver.observe(host);

        const sendThroughTransport = (data: string) => {
            const socket = wsRef.current;
            if (socket && socket.readyState === WebSocket.OPEN) {
                socket.send(data);
            } else {
                void onSendInputFallback(sessionId, data);
            }
        };

        term.onData((data) => {
            sendThroughTransport(data);
        });
        term.attachCustomKeyEventHandler((event) => {
            if (
                event.type === 'keydown'
                && (event.ctrlKey || event.metaKey)
                && event.key.toLowerCase() === 'v'
            ) {
                void pasteClipboard();
                return false;
            }
            return true;
        });
        term.focus();

        const wsUrl = buildTerminalWsUrl(commandId);
        if (!wsUrl) {
            const fallbackTimer = window.setTimeout(() => setFallbackMode(true), 0);
            return () => {
                window.clearTimeout(resetFallbackTimer);
                window.clearTimeout(fallbackTimer);
                resizeObserver.disconnect();
                term.dispose();
            };
        }

        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;
        ws.onopen = () => {
            setFallbackMode(false);
        };
        ws.onmessage = (event) => {
            if (typeof event.data === 'string') {
                term.write(event.data);
            }
        };
        ws.onerror = () => {
            setFallbackMode(true);
        };
        ws.onclose = () => {
            wsRef.current = null;
            setFallbackMode(true);
        };

        return () => {
            window.clearTimeout(resetFallbackTimer);
            resizeObserver.disconnect();
            if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
                ws.close();
            }
            term.dispose();
            terminalRef.current = null;
            fitAddonRef.current = null;
        };
    }, [commandId, onSendInputFallback, pasteClipboard, sessionId]);

    useEffect(() => {
        if (!fallbackMode || !terminalRef.current || !snapshotText || snapshotText === lastSnapshotRef.current) {
            return;
        }
        lastSnapshotRef.current = snapshotText;
        writePlainSnapshot(terminalRef.current, snapshotText);
    }, [fallbackMode, snapshotText]);

    useEffect(() => {
        if (!fallbackMode || !sessionId || session.isRunning === false) {
            return;
        }
        let cancelled = false;
        const poll = async () => {
            try {
                const response = await fetch(`/api/client/terminal/sessions/${encodeURIComponent(sessionId)}`, { cache: 'no-store' });
                const payload = await response.json().catch(() => ({}));
                if (!cancelled && response.ok && payload?.ok !== false) {
                    onSnapshot(payload as ManualTerminalSessionView);
                }
            } catch {}
        };
        void poll();
        const timer = window.setInterval(() => void poll(), 1200);
        return () => {
            cancelled = true;
            window.clearInterval(timer);
        };
    }, [fallbackMode, onSnapshot, session.isRunning, sessionId]);

    return (
        <div
            className="flex min-h-0 flex-1 flex-col bg-[#05070b]"
            onContextMenu={(event) => {
                event.preventDefault();
                void pasteClipboard();
            }}
        >
            <div ref={terminalHostRef} className="min-h-0 flex-1 px-2 py-2" />
            {(error || fallbackMode) && (
                <div className="flex items-center gap-2 border-t border-white/10 bg-black/40 px-3 py-1.5 text-[11px] text-slate-300">
                    <span className={cn("h-2 w-2 rounded-full", session.isRunning ? "bg-emerald-400" : "bg-slate-500")} />
                    <span className="truncate">
                        {error || (fallbackMode ? "WebSocket 不可用，已使用 HTTP 降级输入/轮询。" : "")}
                    </span>
                    {session.isRunning && sessionId && (
                        <button
                            type="button"
                            className="ml-auto inline-flex h-6 w-6 items-center justify-center rounded hover:bg-white/10"
                            title="终止"
                            onClick={() => void onTerminate(sessionId)}
                        >
                            <Square className="h-3.5 w-3.5" />
                        </button>
                    )}
                </div>
            )}
        </div>
    );
}

export function ManualTerminalPanel({
    workspacePath,
    profiles,
    profileId,
    sessions,
    activeSessionId,
    busy,
    error,
    onProfileChange,
    onStart,
    onActivate,
    onSessionSnapshot,
    onSendInputFallback,
    onTerminate,
    onCloseSession,
    onClosePanel,
}: ManualTerminalPanelProps) {
    const activeSession = useMemo(
        () => sessions.find((item) => item.sessionId === activeSessionId) || sessions[0] || null,
        [activeSessionId, sessions],
    );

    return (
        <div className="h-72 shrink-0 border-t border-border/60 bg-background/95 shadow-sm backdrop-blur flex flex-col overflow-hidden sm:max-h-[36vh] z-30">
            <div className="flex min-h-10 items-center gap-2 border-b border-border/50 bg-muted/30 px-3 py-1.5 text-[11px] text-muted-foreground">
                <div className="flex min-w-0 items-center gap-2">
                    <TerminalSquare className="h-3.5 w-3.5 shrink-0" />
                    <span className="font-semibold text-foreground">手动终端</span>
                    <span className="max-w-[28vw] truncate font-mono text-muted-foreground/80">
                        {workspacePath || "未绑定工作区"}
                    </span>
                </div>
                <div className="ml-2 flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
                    {sessions.map((session) => {
                        const sessionId = String(session.sessionId || '');
                        const active = activeSession?.sessionId === sessionId;
                        return (
                            <div
                                key={sessionId}
                                role="button"
                                tabIndex={0}
                                className={cn(
                                    "group flex max-w-[180px] shrink-0 items-center gap-1 rounded-md border px-2 py-1 text-left transition-colors",
                                    active
                                        ? "border-primary/45 bg-primary/10 text-foreground"
                                        : "border-border/50 bg-background/80 hover:bg-muted",
                                )}
                                onClick={() => onActivate(sessionId)}
                                onKeyDown={(event) => {
                                    if (event.key === 'Enter' || event.key === ' ') {
                                        event.preventDefault();
                                        onActivate(sessionId);
                                    }
                                }}
                                title={formatTerminalTitle(session)}
                            >
                                <span className={cn("h-1.5 w-1.5 rounded-full", session.isRunning ? "bg-emerald-500" : "bg-slate-400")} />
                                <span className="truncate font-mono">{formatTerminalTitle(session)}</span>
                                <span
                                    role="button"
                                    tabIndex={0}
                                    className="ml-1 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded opacity-55 hover:bg-muted-foreground/10 hover:opacity-100"
                                    onClick={(event) => {
                                        event.stopPropagation();
                                        void onCloseSession(sessionId);
                                    }}
                                    onKeyDown={(event) => {
                                        if (event.key === 'Enter' || event.key === ' ') {
                                            event.stopPropagation();
                                            void onCloseSession(sessionId);
                                        }
                                    }}
                                    title="关闭终端"
                                >
                                    <X className="h-3 w-3" />
                                </span>
                            </div>
                        );
                    })}
                </div>
                {profiles.length > 1 && (
                    <select
                        value={profileId}
                        onChange={(event) => onProfileChange(event.target.value)}
                        className="h-7 max-w-44 shrink-0 rounded-md border border-border/50 bg-background px-2 text-[11px] text-foreground outline-none"
                    >
                        {profiles.map((profile) => (
                            <option key={profile.id} value={profile.id}>
                                {profile.label}
                            </option>
                        ))}
                    </select>
                )}
                <button
                    type="button"
                    className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-50"
                    onClick={onStart}
                    disabled={busy || !profiles.length}
                    title="新建终端"
                >
                    <Plus className="h-3.5 w-3.5" />
                    新建
                </button>
                <button
                    type="button"
                    className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                    onClick={onClosePanel}
                    title="折叠终端"
                >
                    <X className="h-3.5 w-3.5" />
                </button>
            </div>
            {activeSession ? (
                <ManualTerminalXterm
                    key={activeSession.sessionId}
                    session={activeSession}
                    error={error}
                    onSnapshot={onSessionSnapshot}
                    onSendInputFallback={onSendInputFallback}
                    onTerminate={onTerminate}
                />
            ) : (
                <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 bg-[#05070b] text-[12px] text-slate-400">
                    <TerminalSquare className="h-8 w-8 opacity-45" />
                    <button
                        type="button"
                        className="rounded-md border border-white/10 bg-white/5 px-3 py-1.5 text-slate-200 hover:bg-white/10 disabled:opacity-50"
                        onClick={onStart}
                        disabled={busy || !profiles.length}
                    >
                        新建当前工作区终端
                    </button>
                    {error && <div className="max-w-md text-center text-red-300">{error}</div>}
                </div>
            )}
        </div>
    );
}
