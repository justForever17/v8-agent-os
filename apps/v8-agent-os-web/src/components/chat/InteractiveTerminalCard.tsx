'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Square, TerminalSquare, ChevronDown } from 'lucide-react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils';
import '@xterm/xterm/css/xterm.css';

interface InteractiveTerminalCardProps {
    commandId: string;
    compact?: boolean;
    onTerminated?: (commandId: string) => void;
}

export function InteractiveTerminalCard({ commandId, compact = false, onTerminated }: InteractiveTerminalCardProps) {
    const [isRunning, setIsRunning] = useState<boolean>(true);
    const [isCollapsed, setIsCollapsed] = useState(compact);
    const terminalRef = useRef<HTMLDivElement>(null);
    const xtermRef = useRef<Terminal | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const fitAddonRef = useRef<FitAddon | null>(null);
    const initRef = useRef(false);

    // Fit terminal when expanding
    useEffect(() => {
        if (!isCollapsed && fitAddonRef.current) {
            setTimeout(() => fitAddonRef.current?.fit(), 50);
        }
    }, [isCollapsed]);

    useEffect(() => {
        if (initRef.current || !terminalRef.current) return;
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
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/api/bg_processes/${commandId}/ws`;
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onmessage = (event) => {
            if (term) term.write(event.data);
        };

        ws.onclose = () => {
            setIsRunning(false);
            if (term) term.writeln('\r\n[Process Terminated]');
            onTerminated?.(commandId);
        };

        ws.onerror = () => {
            setIsRunning(false);
            if (term) term.writeln('\r\n[Connection Error]');
            onTerminated?.(commandId);
        };

        term.onData((data) => {
            if (ws.readyState === WebSocket.OPEN) ws.send(data);
        });

        return () => {
            window.removeEventListener('resize', handleResize);
            if (ws.readyState === WebSocket.OPEN) ws.close();
            term.dispose();
        };
    }, [commandId, compact, onTerminated]);

    const handleTerminate = useCallback(async () => {
        try {
            await fetch(`/api/bg_processes/${commandId}/terminate`, { method: 'POST' });
            setIsRunning(false);
            onTerminated?.(commandId);
        } catch (err) {
            console.error('Termination error:', err);
        }
    }, [commandId, onTerminated]);

    const shortId = commandId.length > 12 ? `…${commandId.slice(-8)}` : commandId;

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
                        {shortId}
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
                    {isRunning && (
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
                        <div className={cn("p-2 bg-[#000000]", compact ? "h-[132px] sm:h-[160px]" : "h-[240px] sm:h-[280px]")}>
                            <div ref={terminalRef} className="h-full w-full" />
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
}
