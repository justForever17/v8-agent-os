'use client';

import { useChatStore } from '@/store/chat-store';
import { useMemo, useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { TerminalSquare, ChevronDown } from 'lucide-react';
import { InteractiveTerminalCard } from './InteractiveTerminalCard';
import { extractCommandSessionPayload, isCommandSessionTool } from '@/lib/chat/command-session';

/**
 * ProcessesHUD — persistent HUD that displays active background processes.
 * Positioned alongside TodosHUD above the input area.
 * Auto-appears when a command session tool returns a command ID,
 * auto-disappears when all processes terminate.
 */

export function useProcessesState() {
    const messages = useChatStore(state => state.messages);
    return useMemo(() => {
        const commandIds: string[] = [];
        const extractId = (text: unknown) => {
            if (typeof text !== 'string') return null;
            const match = text.match(/ID:\s*([a-f0-9-]+)/i);
            return match ? match[1] : null;
        };
        // Scan all messages for canonical / legacy command-session tool results
        for (const msg of messages) {
            if (msg.role !== 'assistant') continue;
            const nodes = msg.nodes || [];
            // Collect tool call IDs for command-session capable tools
            const bgToolCallIds = new Set<string>();
            for (const node of nodes) {
                if (node.kind === 'execution' && node.executionType === 'tool_call' && isCommandSessionTool(node.toolName)) {
                    const id = extractCommandSessionPayload(node.result)?.commandId || extractId(node.result);
                    if (id && !commandIds.includes(id)) commandIds.push(id);
                    if (node.toolCallId) bgToolCallIds.add(node.toolCallId);
                }
            }
            // Case 2: separate tool_result part (live streaming)
            for (const node of nodes) {
                if (node.kind === 'execution' && node.executionType === 'tool_result' && node.toolCallId && bgToolCallIds.has(node.toolCallId)) {
                    const id = extractCommandSessionPayload(node.result)?.commandId || extractId(node.result);
                    if (id && !commandIds.includes(id)) commandIds.push(id);
                }
            }
        }
        return commandIds;
    }, [messages]);
}

export function ProcessesHUD() {
    const allCommandIds = useProcessesState();
    const [terminatedIds, setTerminatedIds] = useState<Set<string>>(new Set());
    const [isCollapsed, setIsCollapsed] = useState(false);

    const handleTerminated = useCallback((cmdId: string) => {
        setTerminatedIds(prev => new Set(prev).add(cmdId));
    }, []);

    // Only show processes that haven't been terminated (in this session)
    const activeIds = allCommandIds.filter(id => !terminatedIds.has(id));
    const hasActive = activeIds.length > 0;

    if (!hasActive) return null;

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
                    {/* Header */}
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
                            {activeIds.length}
                        </span>
                        <motion.div
                            animate={{ rotate: isCollapsed ? -90 : 0 }}
                            className="ml-1 text-muted-foreground/70"
                        >
                            <ChevronDown className="w-3.5 h-3.5" />
                        </motion.div>
                    </div>

                    {/* Process Cards */}
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
                                    {activeIds.map(cmdId => (
                                        <InteractiveTerminalCard
                                            key={cmdId}
                                            commandId={cmdId}
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
