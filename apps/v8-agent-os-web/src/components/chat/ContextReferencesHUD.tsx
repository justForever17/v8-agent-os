'use client';

import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { FileCode, Database, Brain, Search } from 'lucide-react';
import type { ContextReferenceItem } from '@v8/session-realtime';

type ContextReferencesHUDProps = {
    contextReferences: ContextReferenceItem[];
};

export function ContextReferencesHUD({ contextReferences }: ContextReferencesHUDProps) {
    const getIcon = (type: ContextReferenceItem['type']) => {
        switch (type) {
            case 'file': return <FileCode className="w-3.5 h-3.5 text-blue-500" />;
            case 'search': return <Search className="w-3.5 h-3.5 text-amber-500" />;
            case 'memory': return <Brain className="w-3.5 h-3.5 text-violet-500" />;
            case 'web': return <Database className="w-3.5 h-3.5 text-emerald-500" />;
            default: return <FileCode className="w-3.5 h-3.5" />;
        }
    };

    return (
        <AnimatePresence initial={false}>
            {contextReferences.length > 0 ? (
                <motion.div
                    key="context-references-hud"
                    initial={{ opacity: 0, y: -20 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -8 }}
                    className="mb-2 flex w-full justify-center pointer-events-none sm:sticky sm:top-4 sm:z-40 sm:mb-4"
                >
                    <div className="flex max-w-[92%] flex-wrap justify-center gap-2 pointer-events-auto sm:max-w-[80%]">
                        <AnimatePresence initial={false}>
                            {contextReferences.slice(-10).map((ref) => (
                                <motion.div
                                    key={ref.id}
                                    initial={{ opacity: 0, scale: 0.8 }}
                                    animate={{ opacity: 1, scale: 1 }}
                                    exit={{ opacity: 0, scale: 0.8 }}
                                    className="group flex items-center gap-1.5 rounded-full border border-border/50 bg-background/92 px-3 py-1.5 shadow-sm transition-colors cursor-help hover:bg-background/95 backdrop-blur-sm sm:backdrop-blur-md"
                                    title={ref.details || ref.label}
                                >
                                    {getIcon(ref.type)}
                                    <span className="text-xs font-medium text-muted-foreground group-hover:text-foreground transition-colors max-w-[150px] truncate">
                                        {ref.label}
                                    </span>
                                </motion.div>
                            ))}
                        </AnimatePresence>
                    </div>
                </motion.div>
            ) : null}
        </AnimatePresence>
    );
}
