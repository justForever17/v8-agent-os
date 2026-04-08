'use client';

import { useMemo, useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { TerminalSquare, ChevronDown } from 'lucide-react';
import type { AdminProcessRef } from '@v8/session-realtime';

import { InteractiveTerminalCard } from './InteractiveTerminalCard';

type ProcessesHUDProps = {
    processes: AdminProcessRef[];
};

const PROCESS_FINISHED_GRACE_SECONDS = 3;

function isActiveProcess(process: AdminProcessRef) {
    const status = String(process.status || '').trim().toLowerCase();
    return status !== 'stopped' && status !== 'terminated' && status !== 'completed' && status !== 'failed';
}

function isRecentlyFinishedProcess(process: AdminProcessRef) {
    const status = String(process.status || '').trim().toLowerCase();
    if (!status || !['stopped', 'terminated', 'completed', 'failed'].includes(status)) {
        return false;
    }
    const secondsSinceOutput = Number(process.secondsSinceOutput);
    if (Number.isFinite(secondsSinceOutput)) {
        return secondsSinceOutput <= PROCESS_FINISHED_GRACE_SECONDS;
    }
    const completedAt = String(process.completedAt || '').trim();
    if (!completedAt) {
        return false;
    }
    const completedMs = Date.parse(completedAt);
    return Number.isFinite(completedMs) && (Date.now() - completedMs) <= PROCESS_FINISHED_GRACE_SECONDS * 1000;
}

export function ProcessesHUD({ processes }: ProcessesHUDProps) {
    const [terminatedIds, setTerminatedIds] = useState<Set<string>>(new Set());
    const [isCollapsed, setIsCollapsed] = useState(false);
    const visibleProcesses = useMemo(
        () => processes.filter((process) => (isActiveProcess(process) || isRecentlyFinishedProcess(process)) && !terminatedIds.has(process.processId)),
        [processes, terminatedIds],
    );

    const handleTerminated = useCallback((processId: string) => {
        setTerminatedIds((prev) => new Set(prev).add(processId));
    }, []);

    if (visibleProcesses.length === 0) return null;

    return (
        <AnimatePresence>
            <motion.div
                initial={{ opacity: 0, y: 20, scale: 0.95 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, scale: 0.95, y: 20 }}
                transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                className="w-[min(19rem,calc(100vw-1.5rem))] max-w-full pointer-events-auto select-none"
                layout
            >
                <div className="flex flex-col overflow-hidden rounded-2xl border border-white/30 bg-background/46 shadow-[0_18px_48px_rgba(15,23,42,0.12)] backdrop-blur-2xl dark:border-white/10 dark:bg-stone-950/42">
                    <div
                        className="flex min-h-[36px] cursor-pointer items-center gap-2 border-b border-white/15 bg-gradient-to-r from-emerald-500/10 via-emerald-500/5 to-transparent px-3 py-1.5 sm:min-h-[40px] sm:px-4 sm:py-2 transition-colors hover:bg-emerald-500/5"
                        onClick={() => setIsCollapsed(!isCollapsed)}
                    >
                        <div className="rounded-md bg-emerald-500/18 p-1.5 text-emerald-500 backdrop-blur-sm">
                            <TerminalSquare className="w-4 h-4" />
                        </div>
                        <span className="font-semibold text-sm tracking-tight text-foreground/90">
                            Processes
                        </span>
                        <span className="ml-auto flex items-center gap-1.5 rounded-full bg-white/35 px-2 py-0.5 text-xs font-mono text-muted-foreground dark:bg-white/10">
                            <span className="relative flex h-1.5 w-1.5">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                                <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-emerald-500" />
                            </span>
                            {visibleProcesses.length}
                        </span>
                        <motion.div
                            animate={{ rotate: isCollapsed ? -90 : 0 }}
                            className="ml-1 text-muted-foreground/70"
                        >
                            <ChevronDown className="w-3.5 h-3.5" />
                        </motion.div>
                    </div>

                    <AnimatePresence initial={false}>
                        {!isCollapsed && (
                            <motion.div
                                initial={{ height: 0 }}
                                animate={{ height: 'auto' }}
                                exit={{ height: 0 }}
                                transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
                                className="overflow-hidden"
                            >
                                <div className="flex max-h-[132px] sm:max-h-[208px] flex-col gap-2 overflow-y-auto p-2 sm:p-2.5">
                                    {visibleProcesses.map((process) => (
                                        <InteractiveTerminalCard
                                            key={process.processId}
                                            process={process}
                                            compact
                                            onTerminated={handleTerminated}
                                        />
                                    ))}
                                </div>
                            </motion.div>
                        )}
                    </AnimatePresence>
                </div>
            </motion.div>
        </AnimatePresence>
    );
}
