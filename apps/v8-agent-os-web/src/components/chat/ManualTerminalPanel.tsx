'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Plus, Square, TerminalSquare, X } from 'lucide-react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { isActiveCommandSessionStatus, type AdminProcessRef } from '@v8/session-realtime';
import { useT } from '@/components/providers/LocaleProvider';
import { cn } from '@/lib/utils';
import { InteractiveTerminalCard } from './InteractiveTerminalCard';
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
    processes?: AdminProcessRef[];
    activeTabId: string;
    hiddenTabCount?: number;
    busy?: boolean;
    error?: string;
    onProfileChange: (profileId: string) => void;
    onStart: () => void;
    onActivate: (tabId: string) => void;
    onHideTab: (tabId: string) => Promise<void> | void;
    onShowHidden?: () => void;
    onClosePanel: () => void;
}

function buildTerminalSessionWsUrl(sessionId: string, ticket: string) {
    if (!sessionId || typeof window === 'undefined') {
        return '';
    }
    const configured = String(
        process.env.NEXT_PUBLIC_V8_ENGINE_WS_BASE_URL
        || process.env.NEXT_PUBLIC_V8_AGENT_OS_ENGINE_WS_BASE_URL
        || '',
    ).trim();
    const query = `ticket=${encodeURIComponent(ticket)}`;
    if (configured) {
        const normalizedBase = configured.replace(/\/$/, '').endsWith('/v1') ? configured.replace(/\/$/, '') : `${configured.replace(/\/$/, '')}/v1`;
        return `${normalizedBase}/terminal/sessions/${encodeURIComponent(sessionId)}/ws?${query}`;
    }
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/api/terminal-ws/sessions/${encodeURIComponent(sessionId)}/ws?${query}`;
}

function formatTerminalTitle(session: ManualTerminalSessionView) {
    const label = session.profileLabel || session.profileId || 'Terminal';
    const id = String(session.sessionId || session.commandId || '').trim();
    const suffix = id ? ` · ${id.slice(-6)}` : '';
    return `${label}${suffix}`;
}

function isProcessRunning(process: AdminProcessRef) {
    return isActiveCommandSessionStatus(process.status);
}

function formatProcessTitle(process: AdminProcessRef) {
    const id = String(process.commandId || process.processId || '').trim();
    const shortId = id ? ` · ${id.slice(-6)}` : '';
    return `${process.title || process.commandPreview || 'Process'}${shortId}`;
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
}

function ManualTerminalXterm({ session, error }: ManualTerminalXtermProps) {
    const t = useT();
    const terminalHostRef = useRef<HTMLDivElement | null>(null);
    const terminalRef = useRef<Terminal | null>(null);
    const fitAddonRef = useRef<FitAddon | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const sessionUsesLocalEcho = session.usesTty === false || String((session as ManualTerminalSessionView & { ttyMode?: string }).ttyMode || '').toLowerCase() === 'pipe';
    const resizeTimerRef = useRef<number | null>(null);
    const wroteInitialSnapshotRef = useRef(false);
    const pendingInputRef = useRef('');
    const localEchoRef = useRef(false);
    const [connected, setConnected] = useState(false);
    const [running, setRunning] = useState(session.isRunning !== false);
    const [socketError, setSocketError] = useState('');

    const sessionId = String(session.sessionId || '').trim();

    const sendFrame = useCallback((payload: Record<string, unknown>) => {
        const socket = wsRef.current;
        if (!socket || socket.readyState !== WebSocket.OPEN) {
            return false;
        }
        socket.send(JSON.stringify(payload));
        return true;
    }, []);

    const sendTerminalInput = useCallback((data: string) => {
        const text = String(data || '');
        if (!text) {
            return;
        }
        if (localEchoRef.current) {
            const term = terminalRef.current;
            if (term) {
                if (text === '\r' || text === '\n') {
                    term.write('\r\n');
                } else if (text === '\u007f' || text === '\b') {
                    term.write('\b \b');
                } else if (!text.startsWith('\x1b')) {
                    term.write(text);
                }
            }
        }
        if (!sendFrame({ type: 'input', data: text })) {
            pendingInputRef.current += text;
        }
    }, [sendFrame]);

    const flushPendingInput = useCallback(() => {
        const buffered = pendingInputRef.current;
        if (!buffered) {
            return;
        }
        if (sendFrame({ type: 'input', data: buffered })) {
            pendingInputRef.current = '';
        }
    }, [sendFrame]);

    const sendResize = useCallback(() => {
        const term = terminalRef.current;
        const fitAddon = fitAddonRef.current;
        if (!term || !fitAddon) {
            return;
        }
        try {
            fitAddon.fit();
        } catch {}
        sendFrame({ type: 'resize', cols: term.cols, rows: term.rows });
    }, [sendFrame]);

    const scheduleResize = useCallback(() => {
        if (resizeTimerRef.current !== null) {
            window.clearTimeout(resizeTimerRef.current);
        }
        resizeTimerRef.current = window.setTimeout(() => {
            resizeTimerRef.current = null;
            sendResize();
        }, 80);
    }, [sendResize]);

    const pasteClipboard = useCallback(async () => {
        try {
            const text = await navigator.clipboard?.readText?.();
            if (text) {
                sendTerminalInput(text);
            }
            terminalRef.current?.focus();
        } catch {
            terminalRef.current?.focus();
        }
    }, [sendTerminalInput]);

    const terminate = useCallback(() => {
        if (sendFrame({ type: 'terminate' })) {
            setRunning(false);
        }
        terminalRef.current?.focus();
    }, [sendFrame]);

    useEffect(() => {
        const host = terminalHostRef.current;
        if (!host || !sessionId) {
            return;
        }

        host.innerHTML = '';
        wroteInitialSnapshotRef.current = false;
        pendingInputRef.current = '';
        localEchoRef.current = sessionUsesLocalEcho;

        const fitAddon = new FitAddon();
        const term = new Terminal({
            cursorBlink: true,
            convertEol: true,
            fontFamily: 'SF Mono, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
            fontSize: 12,
            lineHeight: 1.18,
            scrollback: 10000,
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

        window.setTimeout(scheduleResize, 30);
        const resizeObserver = new ResizeObserver(scheduleResize);
        resizeObserver.observe(host);

        const dataDisposable = term.onData((data) => {
            sendTerminalInput(data);
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

        let disposed = false;
        let ws: WebSocket | null = null;

        const connect = async () => {
            try {
                const ticketResponse = await fetch(`/api/client/terminal/sessions/${encodeURIComponent(sessionId)}/ws-ticket`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                });
                const ticketPayload = await ticketResponse.json().catch(() => ({}));
                const ticket = String(ticketPayload?.ticket || '').trim();
                if (!ticketResponse.ok || !ticket) {
                    throw new Error(String(ticketPayload?.detail || ticketPayload?.error || t('web.terminal.ticketUnavailable')));
                }
                if (disposed) {
                    return;
                }
                const wsUrl = buildTerminalSessionWsUrl(sessionId, ticket);
                if (!wsUrl) {
                    throw new Error(t('web.terminal.missingUrl'));
                }

                ws = new WebSocket(wsUrl);
                wsRef.current = ws;
                ws.onopen = () => {
                    setConnected(true);
                    setSocketError('');
                    scheduleResize();
                    flushPendingInput();
                };
                ws.onmessage = (event) => {
                    if (typeof event.data !== 'string') {
                        return;
                    }
                    let payload: Record<string, unknown> | null = null;
                    try {
                        payload = JSON.parse(event.data) as Record<string, unknown>;
                    } catch {
                        term.write(event.data);
                        wroteInitialSnapshotRef.current = true;
                        return;
                    }
                    const type = String(payload?.type || '');
                    if (type === 'output') {
                        const data = String(payload.data || '');
                        if (data) {
                            term.write(data);
                            wroteInitialSnapshotRef.current = true;
                        }
                        return;
                    }
                    if (type === 'snapshot') {
                        const nextSession = (payload.session || {}) as ManualTerminalSessionView;
                        setRunning(nextSession.isRunning !== false);
                        localEchoRef.current = nextSession.usesTty === false || String((nextSession as ManualTerminalSessionView & { ttyMode?: string }).ttyMode || '').toLowerCase() === 'pipe';
                        const delta = String(nextSession.outputDelta || '');
                        if (delta) {
                            term.write(delta);
                            wroteInitialSnapshotRef.current = true;
                            return;
                        }
                        const snapshot = String(nextSession.rawScreenSnapshot || nextSession.screenSnapshot || '');
                        if (!wroteInitialSnapshotRef.current && snapshot) {
                            writePlainSnapshot(term, snapshot);
                            wroteInitialSnapshotRef.current = true;
                        }
                        return;
                    }
                    if (type === 'status') {
                        const nextSession = (payload.session || {}) as ManualTerminalSessionView;
                        setRunning(nextSession.isRunning !== false);
                        localEchoRef.current = nextSession.usesTty === false || String((nextSession as ManualTerminalSessionView & { ttyMode?: string }).ttyMode || '').toLowerCase() === 'pipe';
                        return;
                    }
                    if (type === 'error') {
                        setSocketError(String(payload.message || t('web.terminal.connectionError')));
                    }
                };
                ws.onerror = () => {
                    setSocketError(t('web.terminal.connectionError'));
                };
                ws.onclose = () => {
                    wsRef.current = null;
                    setConnected(false);
                };
            } catch (connectError) {
                if (disposed) {
                    return;
                }
                const message = connectError instanceof Error ? connectError.message : t('web.terminal.connectionError');
                setSocketError(message);
                term.writeln(`\r\n[${message}]`);
            }
        };
        void connect();

        return () => {
            disposed = true;
            if (resizeTimerRef.current !== null) {
                window.clearTimeout(resizeTimerRef.current);
                resizeTimerRef.current = null;
            }
            resizeObserver.disconnect();
            dataDisposable.dispose();
            if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
                ws.close();
            }
            term.dispose();
            terminalRef.current = null;
            fitAddonRef.current = null;
        };
    }, [flushPendingInput, pasteClipboard, scheduleResize, sendFrame, sendTerminalInput, session.isRunning, sessionId, sessionUsesLocalEcho, t]);

    return (
        <div
            className="flex min-h-0 flex-1 flex-col bg-[#05070b]"
            tabIndex={0}
            onMouseDown={(event) => {
                event.currentTarget.focus();
                terminalRef.current?.focus();
            }}
            onPointerDownCapture={(event) => {
                event.currentTarget.focus();
                terminalRef.current?.focus();
            }}
            onContextMenu={(event) => {
                event.preventDefault();
                void pasteClipboard();
            }}
        >
            <div ref={terminalHostRef} className="min-h-0 flex-1 px-2 py-2" />
            <div className="flex items-center gap-2 border-t border-white/10 bg-black/40 px-3 py-1.5 text-[11px] text-slate-300">
                <span className={cn("h-2 w-2 rounded-full", connected ? "bg-emerald-400" : "bg-slate-500")} />
                <span className="truncate">
                    {error || socketError || (connected ? (running ? t('web.terminal.connected') : t('web.terminal.stopped')) : t('web.terminal.connecting'))}
                </span>
                {running && sessionId && (
                    <button
                        type="button"
                        className="ml-auto inline-flex h-6 w-6 items-center justify-center rounded hover:bg-white/10"
                        title={t('web.terminal.terminate')}
                        onClick={terminate}
                    >
                        <Square className="h-3.5 w-3.5" />
                    </button>
                )}
            </div>
        </div>
    );
}

export function ManualTerminalPanel({
    workspacePath,
    profiles,
    profileId,
    sessions,
    processes = [],
    activeTabId,
    hiddenTabCount = 0,
    busy,
    error,
    onProfileChange,
    onStart,
    onActivate,
    onHideTab,
    onShowHidden,
    onClosePanel,
}: ManualTerminalPanelProps) {
    const t = useT();
    const tabs = useMemo(() => {
        const manualCommandIds = new Set(
            sessions
                .map((session) => String(session.commandId || session.sessionId || '').trim())
                .filter(Boolean),
        );
        return [
            ...sessions
                .map((session) => {
                    const sessionId = String(session.sessionId || '').trim();
                    if (!sessionId) {
                        return null;
                    }
                    return {
                        id: `manual:${sessionId}`,
                        kind: 'manual' as const,
                        title: formatTerminalTitle(session),
                        isRunning: session.isRunning !== false,
                        session,
                    };
                })
                .filter((item): item is NonNullable<typeof item> => Boolean(item)),
            ...processes
                .map((process) => {
                    const processId = String(process.processId || process.commandId || '').trim();
                    if (!processId || manualCommandIds.has(processId) || manualCommandIds.has(String(process.commandId || '').trim())) {
                        return null;
                    }
                    return {
                        id: `process:${processId}`,
                        kind: 'process' as const,
                        title: formatProcessTitle(process),
                        isRunning: isProcessRunning(process),
                        process,
                    };
                })
                .filter((item): item is NonNullable<typeof item> => Boolean(item)),
        ];
    }, [processes, sessions]);

    const activeTab = useMemo(
        () => tabs.find((item) => item.id === activeTabId) || tabs[0] || null,
        [activeTabId, tabs],
    );

    return (
        <div className="z-30 flex h-72 shrink-0 flex-col overflow-hidden border-t border-border/60 bg-background/95 shadow-sm backdrop-blur sm:max-h-[36vh]">
            <div className="flex min-h-10 items-center gap-2 border-b border-border/50 bg-muted/30 px-3 py-1.5 text-[11px] text-muted-foreground">
                <div className="flex min-w-0 items-center gap-2">
                    <TerminalSquare className="h-3.5 w-3.5 shrink-0" />
                    <span className="font-semibold text-foreground">{t('web.terminal.title')}</span>
                    <span className="max-w-[28vw] truncate font-mono text-muted-foreground/80">
                        {workspacePath || t('web.terminal.unboundWorkspace')}
                    </span>
                </div>
                <div className="ml-2 flex min-w-0 flex-1 items-center gap-1 overflow-x-auto">
                    {tabs.map((tab) => {
                        const active = activeTab?.id === tab.id;
                        return (
                            <div
                                key={tab.id}
                                role="button"
                                tabIndex={0}
                                className={cn(
                                    "group flex max-w-[180px] shrink-0 items-center gap-1 rounded-md border px-2 py-1 text-left transition-colors",
                                    active
                                        ? "border-primary/45 bg-primary/10 text-foreground"
                                        : "border-border/50 bg-background/80 hover:bg-muted",
                                )}
                                onClick={() => onActivate(tab.id)}
                                onKeyDown={(event) => {
                                    if (event.key === 'Enter' || event.key === ' ') {
                                        event.preventDefault();
                                        onActivate(tab.id);
                                    }
                                }}
                                title={tab.title}
                            >
                                <span className={cn("h-1.5 w-1.5 rounded-full", tab.isRunning ? "bg-emerald-500" : "bg-slate-400")} />
                                <span className="truncate font-mono">{tab.title}</span>
                                <span
                                    role="button"
                                    tabIndex={0}
                                    className="ml-1 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded opacity-55 hover:bg-muted-foreground/10 hover:opacity-100"
                                    onClick={(event) => {
                                        event.stopPropagation();
                                        void onHideTab(tab.id);
                                    }}
                                    onKeyDown={(event) => {
                                        if (event.key === 'Enter' || event.key === ' ') {
                                            event.stopPropagation();
                                            void onHideTab(tab.id);
                                        }
                                    }}
                                    title={t('web.terminal.hideTab')}
                                >
                                    <X className="h-3 w-3" />
                                </span>
                            </div>
                        );
                    })}
                </div>
                {hiddenTabCount > 0 && onShowHidden && (
                    <button
                        type="button"
                        className="inline-flex h-7 shrink-0 items-center rounded-md px-2 text-[11px] text-muted-foreground hover:bg-muted hover:text-foreground"
                        onClick={onShowHidden}
                        title={t('web.terminal.showHidden')}
                    >
                        {t('web.terminal.showHiddenCount', { count: hiddenTabCount })}
                    </button>
                )}
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
                    title={t('web.terminal.newTerminal')}
                >
                    <Plus className="h-3.5 w-3.5" />
                    {t('web.terminal.new')}
                </button>
                <button
                    type="button"
                    className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                    onClick={onClosePanel}
                    title={t('web.terminal.collapse')}
                >
                    <X className="h-3.5 w-3.5" />
                </button>
            </div>
            {activeTab?.kind === 'manual' ? (
                <ManualTerminalXterm
                    key={activeTab.session.sessionId}
                    session={activeTab.session}
                    error={error}
                />
            ) : activeTab?.kind === 'process' ? (
                <div className="min-h-0 flex-1 overflow-auto bg-[#05070b] p-2">
                    <InteractiveTerminalCard
                        key={activeTab.process.processId}
                        process={activeTab.process}
                    />
                </div>
            ) : (
                <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 bg-[#05070b] text-[12px] text-slate-400">
                    <TerminalSquare className="h-8 w-8 opacity-45" />
                    <button
                        type="button"
                        className="rounded-md border border-white/10 bg-white/5 px-3 py-1.5 text-slate-200 hover:bg-white/10 disabled:opacity-50"
                        onClick={onStart}
                        disabled={busy || !profiles.length}
                    >
                        {t('web.terminal.createWorkspaceTerminal')}
                    </button>
                    {error && <div className="max-w-md text-center text-red-300">{error}</div>}
                </div>
            )}
        </div>
    );
}
