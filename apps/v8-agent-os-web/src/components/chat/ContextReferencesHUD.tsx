import React, { useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { FileCode, Database, Brain, Search } from 'lucide-react';
import { useChatStore } from '@/store/chat-store';

interface ContextReference {
    id: string;
    type: 'file' | 'memory' | 'search' | 'web';
    label: string;
    details?: string;
}

export function ContextReferencesHUD() {
    const messages = useChatStore(state => state.messages);

    const contextRefs = useMemo(() => {
        const refs = new Map<string, ContextReference>();
        
        messages.forEach(msg => {
            // We only care about tools invoked by the assistant
            if (msg.role === 'assistant' && msg.nodes) {
                msg.nodes.forEach(node => {
                    if (node.kind === 'execution' && node.executionType === 'tool_call') {
                        const args = node.args || {};
                        
                        // Local File Editing / Reading
                        if (['read_file', 'view_file', 'replace_file_content', 'multi_replace_file_content', 'write_to_file'].includes(node.toolName!)) {
                            const path = args.AbsolutePath || args.TargetFile || args.filePath || '';
                            if (path) {
                                // Extract just the file name
                                const filename = path.split(/[\/\\]/).pop();
                                if (filename) {
                                    refs.set(`file-${filename}`, {
                                        id: `file-${filename}`,
                                        type: 'file',
                                        label: filename,
                                        details: path
                                    });
                                }
                            }
                        }
                        
                        // Workspace Search
                        if (['find_by_name', 'grep_search', 'list_dir'].includes(node.toolName!)) {
                            const query = args.Pattern || args.Query || args.SearchDirectory || '';
                            if (query) {
                                const shortQuery = query.length > 15 ? query.substring(0, 15) + '...' : query;
                                refs.set(`search-${shortQuery}`, {
                                    id: `search-${shortQuery}`,
                                    type: 'search',
                                    label: `搜索: ${shortQuery}`,
                                    details: `Tool: ${node.toolName}`
                                });
                            }
                        }

                        // Web Search 
                        if (node.toolName === 'search_web' || node.toolName === 'read_url_content') {
                            const query = args.query || args.Url || '';
                            if (query) {
                                const shortQuery = query.length > 20 ? query.substring(0, 20) + '...' : query;
                                refs.set(`web-${shortQuery}`, {
                                    id: `web-${shortQuery}`,
                                    type: 'web',
                                    label: `网页: ${shortQuery}`
                                });
                            }
                        }

                        // Memory & Knowledge
                        if (node.toolName === 'memory_recall') {
                            const query = args.query || 'knowledge';
                            const shortQuery = query.length > 15 ? query.substring(0, 15) + '...' : query;
                            refs.set(`memory-${shortQuery}`, {
                                id: `memory-${shortQuery}`,
                                type: 'memory',
                                label: `记忆: ${shortQuery}`
                            });
                        }
                    }
                });
            }
        });

        // Limit to the most recent 10 references to not clutter the UI
        return Array.from(refs.values()).slice(-10);
    }, [messages]);

    if (contextRefs.length === 0) return null;

    const getIcon = (type: string) => {
        switch (type) {
            case 'file': return <FileCode className="w-3.5 h-3.5 text-blue-500" />;
            case 'search': return <Search className="w-3.5 h-3.5 text-amber-500" />;
            case 'memory': return <Brain className="w-3.5 h-3.5 text-violet-500" />;
            case 'web': return <Database className="w-3.5 h-3.5 text-emerald-500" />;
            default: return <FileCode className="w-3.5 h-3.5" />;
        }
    };

    return (
        <div className="w-full flex justify-center sticky top-4 z-40 pointer-events-none mb-4">
            <motion.div 
                initial={{ opacity: 0, y: -20 }}
                animate={{ opacity: 1, y: 0 }}
                className="max-w-[80%] flex flex-wrap justify-center gap-2 pointer-events-auto"
            >
                <AnimatePresence>
                    {contextRefs.map((ref) => (
                        <motion.div
                            key={ref.id}
                            initial={{ opacity: 0, scale: 0.8 }}
                            animate={{ opacity: 1, scale: 1 }}
                            exit={{ opacity: 0, scale: 0.8 }}
                            className="flex items-center gap-1.5 px-3 py-1.5 bg-background/80 hover:bg-background/95 backdrop-blur-md rounded-full border border-border/50 shadow-sm cursor-help transition-colors group"
                            title={ref.details || ref.label}
                        >
                            {getIcon(ref.type)}
                            <span className="text-xs font-medium text-muted-foreground group-hover:text-foreground transition-colors max-w-[150px] truncate">
                                {ref.label}
                            </span>
                        </motion.div>
                    ))}
                </AnimatePresence>
            </motion.div>
        </div>
    );
}
