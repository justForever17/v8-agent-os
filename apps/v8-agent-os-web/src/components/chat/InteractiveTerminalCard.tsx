'use client';

import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Square, TerminalSquare, ChevronDown, LockKeyhole } from 'lucide-react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { motion, AnimatePresence } from 'framer-motion';
import {
    resolveAdminProcessHttpPath,
    resolveAdminProcessWsUrl,
    type AdminProcessRef,
} from '@v8/session-realtime';
import { cn } from '@/lib/utils';
import '@xterm/xterm/css/xterm.css';

interface InteractiveTerminalCardProps {
    process: AdminProcessRef;
    compact?: boolean;
    onTerminated?: (processId: string) => void;
}

export function InteractiveTerminalCard({ process, compact = false, onTerminated }: InteractiveTerminalCardProps) {
    const processRecord = process as AdminProcessRef & { stableScreenSnapshot?: string };
    const processStatusRunning = useMemo(
        () => String(process.status || '').trim().toLowerCase() !== 'stopped',
        [process.status],
    );
    const streamUnavailable = useMemo(
        () => !compact && !resolveAdminProcessWsUrl('web', undefined, process),
        [compact, process],
    );
    const [observedRunning, setObservedRunning] = useState<boolean | null>(null);
    const [isCollapsed, setIsCollapsed] = useState(compact);
    const [sensitiveMode, setSensitiveMode] = useState(false);
    const processSnapshot = useMemo(() =>
        String(processRecord.stableScreenSnapshot || process.screenSnapshot || '').trim(),
    [process.screenSnapshot, processRecord.stableScreenSnapshot]);
    const [polledCompactSnapshot, setPolledCompactSnapshot] = useState<string>('');
    const compactSnapshot = polledCompactSnapshot || processSnapshot;
    const isRunning = !streamUnavailable && (processStatusRunning ? (observedRunning ?? true) : false);
    const terminalRef = useRef<HTMLDivElement>(null);
    const xtermRef = useRef<Terminal | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const fitAddonRef = useRef<FitAddon | null>(null);
    const initRef = useRef(false);
    const sensitiveModeRef = useRef(false);

    useEffect(() => {
        sensitiveModeRef.current = sensitiveMode;
    }, [sensitiveMode]);

    useEffect(() => {
        if (!isCollapsed && fitAddonRef.current) {
            setTimeout(() => fitAddonRef.current?.fit(), 50);
        }
    }, [isCollapsed]);

    const handleTerminate = useCallback(async () => {
        const terminatePath = resolveAdminProcessHttpPath('web', undefined, process, 'terminate');
        if (!terminatePath) {
            return;
        }
        try {
            await fetch(terminatePath, { method: 'POST' });
            setObservedRunning(false);
            onTerminated?.(process.processId);
        } catch (err) {
            console.error('Termination error:', err);
        }
    }, [onTerminated, process]);

    useEffect(() => {
        if (compact || initRef.current || !terminalRef.current) return;
        initRef.current = true;

        const fitAddon = new FitAddon();
        const term = new Terminal({
            cursorBlink: true,
            fontFamily: 'SF Mono, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
            fontSize: compact ? 11 : 12,
            rows: compact ? 10 : 16,
            theme: {
                background: 'transparent',
                foreground: '#e4e4e7',
                cursor: '#e4e4e7'
            },
            convertEol: true
        });

        term.loadAddon(fitAddon);
        term.open(terminalRef.current);

        setTimeout(() => fitAddon.fit(), 10);

        xtermRef.current = term;
        fitAddonRef.current = fitAddon;

        const handleResize = () => fitAddonRef.current?.fit();
        window.addEventListener('resize', handleResize);

        // WebSocket — goes through the Next.js → Admin → Engine proxy chain
        const wsUrl = resolveAdminProcessWsUrl('web', undefined, process);
        if (!wsUrl) {
            term.writeln('\r\n[Missing process stream]');
            return () => {
                window.removeEventListener('resize', handleResize);
                term.dispose();
            };
        }
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onmessage = (event) => {
            if (term) term.write(event.data);
        };

        ws.onclose = () => {
            setObservedRunning(false);
            if (term) term.writeln('\r\n[Process Terminated]');
            onTerminated?.(process.processId);
        };

        ws.onerror = () => {
            setObservedRunning(false);
            if (term) term.writeln('\r\n[Connection Error]');
            onTerminated?.(process.processId);
        };

        term.onData((data) => {
            const inputPath = resolveAdminProcessHttpPath('web', undefined, process, 'input');
            if (!process.canInput || !inputPath || !data) {
                return;
            }
            const sendAsSensitive = sensitiveModeRef.current;
            const targetPath = sendAsSensitive
                ? inputPath.replace(/\/input(?:\?.*)?$/i, '/sensitive-input')
                : inputPath;
            void fetch(targetPath, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(sendAsSensitive
                    ? { input_text: data, secret_type: 'terminal_secret' }
                    : { input_text: data }),
            }).catch((error) => {
                console.error('Terminal input error:', error);
            });
            if (sendAsSensitive && /[\r\n]/.test(data)) {
                setSensitiveMode(false);
            }
        });

        return () => {
            window.removeEventListener('resize', handleResize);
            if (ws.readyState === WebSocket.OPEN) ws.close();
            term.dispose();
        };
    }, [compact, onTerminated, process]);

    useEffect(() => {
        if (!compact) {
            return;
        }
        const outputPath = String(process.outputAdminPath || '').trim();
        if (!outputPath) {
            return;
        }
        let cancelled = false;
        const poll = async () => {
            try {
                const response = await fetch(outputPath);
                if (!response.ok) {
                    return;
                }
                const payload = await response.json() as {
                    is_running?: boolean;
                    isRunning?: boolean;
                    stableScreenSnapshot?: string;
                    screenSnapshot?: string;
                    process?: {
                        status?: string;
                        is_running?: boolean;
                        stable_screen_snapshot?: string;
                        screen_snapshot?: string;
                    };
                };
                if (cancelled) {
                    return;
                }
                const nextSnapshot = String(
                    payload.stableScreenSnapshot
                    || payload.screenSnapshot
                    || payload.process?.stable_screen_snapshot
                    || payload.process?.screen_snapshot
                    || '',
                ).trim();
                if (nextSnapshot) {
                    setPolledCompactSnapshot(nextSnapshot);
                }
                const stillRunning = typeof payload.process?.status === 'string'
                    ? String(payload.process.status || '').trim().toLowerCase() !== 'stopped'
                    : Boolean(payload.is_running ?? payload.isRunning ?? payload.process?.is_running);
                setObservedRunning(stillRunning);
            } catch (error) {
                console.error('Compact terminal polling error:', error);
            }
        };
        void poll();
        const timer = window.setInterval(() => void poll(), 1200);
        return () => {
            cancelled = true;
            window.clearInterval(timer);
        };
    }, [compact, process]);

    const shortId = (process.commandId || process.processId).length > 12
        ? `…${(process.commandId || process.processId).slice(-8)}`
        : (process.commandId || process.processId);
    const title = process.title || process.commandPreview || shortId;
    const encodingState = String(process.encodingState || '').trim().toLowerCase();
    const encodingWarning = encodingState && encodingState !== 'clean'
        ? (String(process.encodingNotes || '').trim() || '终端编码异常，内容可能失真。')
        : '';

    return (
        <div className="flex flex-col w-full rounded-lg overflow-hidden border border-zinc-200 dark:border-zinc-800 shadow-sm transition-all">
            {/* macOS-style Header */}
            <div
                className={cn(
                    "flex items-center justify-between px-3 py-2 sm:py-2.5 bg-zinc-100 dark:bg-[#1C1C1E] border-b border-zinc-200 dark:border-zinc-800 cursor-pointer select-none",
                    isCollapsed && "border-b-0"
                )}
                onClick={() => setIsCollapsed(c => !c)}
            >
                <div className="flex items-center gap-2 min-w-0">
                    <TerminalSquare className="w-3.5 h-3.5 text-zinc-500 dark:text-zinc-400 shrink-0" />
                    <span className="text-[11px] font-mono font-medium text-zinc-500 dark:text-zinc-400 truncate">
                        {title}
                    </span>
                    {isRunning ? (
                        <span className="relative flex h-2 w-2 shrink-0">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
                        </span>
                    ) : (
                        <span className="h-2 w-2 rounded-full bg-zinc-400 dark:bg-zinc-600 shrink-0" />
                    )}
                </div>

                <div className="flex items-center gap-1.5 shrink-0">
                    {isRunning && process.canInput && (
                        <button
                            onClick={(e) => {
                                e.stopPropagation();
                                setSensitiveMode((current) => !current);
                            }}
                            className={cn(
                                "flex items-center justify-center px-2 py-0.5 gap-1 text-[10px] font-semibold rounded-full transition-colors active:scale-95",
                                sensitiveMode
                                    ? "bg-amber-500/90 text-white hover:bg-amber-600"
                                    : "bg-zinc-200 text-zinc-700 hover:bg-zinc-300 dark:bg-zinc-800 dark:text-zinc-200 dark:hover:bg-zinc-700",
                            )}
                            title="一次性敏感输入：不会通过普通输入响应回显"
                        >
                            <LockKeyhole className="w-2.5 h-2.5" />
                            <span>敏感</span>
                        </button>
                    )}
                    {isRunning && process.canTerminate && (
                        <button
                            onClick={(e) => { e.stopPropagation(); handleTerminate(); }}
                            className="flex items-center justify-center px-2 py-0.5 gap-1 text-[10px] font-semibold text-white bg-red-500/90 hover:bg-red-600 rounded-full transition-colors active:scale-95"
                            title="终止进程"
                        >
                            <Square className="w-2.5 h-2.5 fill-current" />
                            <span>停止</span>
                        </button>
                    )}
                    <motion.div
                        animate={{ rotate: isCollapsed ? -90 : 0 }}
                        className="text-zinc-400"
                    >
                        <ChevronDown className="w-3.5 h-3.5" />
                    </motion.div>
                </div>
            </div>

            {/* Terminal Body */}
            <AnimatePresence initial={false}>
                {!isCollapsed && (
                    <motion.div
                        initial={{ height: 0 }}
                        animate={{ height: 'auto' }}
                        exit={{ height: 0 }}
                        transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
                        className="overflow-hidden"
                    >
                        {encodingWarning ? (
                            <div className="mx-2 mt-2 rounded-md border border-amber-500/50 bg-amber-50 px-3 py-2 text-[11px] font-medium text-amber-900 dark:bg-amber-500/10 dark:text-amber-200">
                                {encodingWarning}
                            </div>
                        ) : null}
                        {compact ? (
                            <div className="max-h-[160px] overflow-auto bg-[#000000] p-3">
                                <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-zinc-200">
                                    {compactSnapshot || '[No terminal output yet]'}
                                </pre>
                            </div>
                        ) : (
                            <div className={cn("p-2 bg-[#000000]", "h-[240px] sm:h-[280px]")}>
                                <div ref={terminalRef} className="h-full w-full" />
                            </div>
                        )}
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
