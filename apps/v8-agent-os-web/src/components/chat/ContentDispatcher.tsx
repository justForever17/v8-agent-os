import React from 'react';
import { motion } from 'framer-motion';
import { UiTimelineNode, UiExecutionNode } from '@/store/chat-types';
import { ThinkingCard } from './ThinkingCard';
import { ToolCard, ToolInvocation } from './ToolCard';
import { InteractiveTerminalCard } from './InteractiveTerminalCard';
import { GenericToolTraceCard } from './GenericToolTraceCard';
import { parseContentToBlocks } from '@/lib/chat/content-detector';
import { MessageBlockItem } from './MessageBlockItem';

interface ToolRendererProps {
    toolInvocation: ToolInvocation;
    isFinished: boolean;
}

// Registry for tools that need specific rendering formats
const ToolRegistry: Record<string, React.FC<ToolRendererProps> | null> = {
    'start_background_command': ({ toolInvocation }) => {
        let bgCommandId = null;
        const result = toolInvocation.result;
        if (result && typeof result === 'string') {
            const match = result.match(/ID:\s*([a-f0-9-]+)/i);
            if (match) bgCommandId = match[1];
        }
        return (
            <motion.div layout className="flex flex-col">
                <ToolCard toolInvocation={toolInvocation} hideResult={!!bgCommandId} />
                {bgCommandId && (
                    <div className="mt-1 mb-2 ml-[15px] relative z-10 w-[calc(100%-15px)]">
                        <div className="absolute -left-[14px] top-0 w-3 h-4 border-l-[1.5px] border-b-[1.5px] border-zinc-300 dark:border-zinc-700 rounded-bl-xl" />
                        <InteractiveTerminalCard commandId={bgCommandId} />
                    </div>
                )}
            </motion.div>
        );
    },
    
    // Tools that we want to render subtly using GenericToolTraceCard (previously blacklisted)
    'write_todos': ({ toolInvocation }) => <GenericToolTraceCard toolInvocation={toolInvocation} />,
    'update_todo': ({ toolInvocation }) => <GenericToolTraceCard toolInvocation={toolInvocation} />,
    'read_background_output': ({ toolInvocation }) => <GenericToolTraceCard toolInvocation={toolInvocation} />,
    'send_background_input': ({ toolInvocation }) => <GenericToolTraceCard toolInvocation={toolInvocation} />,
    'terminate_background_command': ({ toolInvocation }) => <GenericToolTraceCard toolInvocation={toolInvocation} />,
};

interface ContentDispatcherProps {
    node: UiTimelineNode;
    isExecuting: boolean;
    isStreaming: boolean;
    resultNode?: UiTimelineNode;
}

export const ContentDispatcher = React.memo(function ContentDispatcher({ 
    node, 
    isExecuting, 
    isStreaming, 
    resultNode 
}: ContentDispatcherProps) {
    switch (node.kind) {
        case 'execution': {
            if (node.executionType === 'reasoning') {
                return (
                    <ThinkingCard 
                        content={node.content || ''}
                        elapsedTime={node.time}
                        isStreaming={isStreaming}
                    />
                );
            }

            if (node.executionType === 'tool_call') {
                const toolName = node.toolName || 'Unknown Tool';
                const resultExecNode = resultNode as UiExecutionNode | undefined;
                const isFinished = !!resultNode || !!node.result || !isExecuting;
                const result = resultExecNode?.result || node.result;
                
                const toolInvocation: ToolInvocation = {
                    toolCallId: node.toolCallId || '',
                    toolName,
                    args: node.args || {},
                    state: isFinished ? 'result' : 'call',
                    result: result
                };

                if (toolName in ToolRegistry) {
                    const Renderer = ToolRegistry[toolName];
                    if (Renderer === null) return null; // Explicitly hidden if mapped to null
                    return <Renderer toolInvocation={toolInvocation} isFinished={isFinished} />;
                }

                // Fallback standard ToolCard for unrecognized/normal tools
                return <ToolCard toolInvocation={toolInvocation} />;
            }

            if (node.executionType === 'runtime_progress') {
                const showTopicPrefix = node.topic && !String(node.topic).startsWith("extension.");
                return (
                    <div className="flex w-full min-w-0 items-start gap-1.5 rounded-md border border-border/50 bg-foreground/5 px-2.5 py-1 text-[11px] text-muted-foreground/80 shadow-sm dark:bg-foreground/10">
                        <span className="mt-[0.28rem] h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-violet-400"></span>
                        <span className="min-w-0 break-all leading-5">
                            {showTopicPrefix ? `[${node.topic}] ` : ''}
                            {node.label || 'Running...'}
                        </span>
                    </div>
                );
            }
            return null;
        }

        case 'narrative': {
            if (!node.content) return null;
            
            // Parse the text part into its sub-blocks (markdown, ppt, html, artifacts, code, mermaid)
            // using the Unified Content Detector
            const blocks = parseContentToBlocks(node.content, isStreaming, 0);

            return (
                <div className="mb-1.5 mt-0.5 flex flex-col gap-2.5">
                    {blocks.map(block => (
                        <MessageBlockItem 
                            key={block.id} 
                            block={block} 
                        />
                    ))}
                </div>
            );
        }

        case 'governance': {
            const isApproval = node.governanceType === 'approval_request';
            return (
                <div className="my-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 p-2.5 text-sm text-amber-700 dark:text-amber-400">
                    <strong className="mb-1 block text-[12px] font-semibold">{isApproval ? 'Approval Required:' : 'System Control:'}</strong>
                    {node.question || node.reason || node.topic || node.status}
                </div>
            );
        }

        default:
            return null;
    }
});
