'use client';

import { useT } from "@/components/providers/LocaleProvider";
import { lt } from "@/lib/locale";
import React from 'react';
import { motion } from 'framer-motion';
import type { AdminProcessRef } from '@v8/session-realtime';
import { UiTimelineNode, UiExecutionNode } from '@/store/chat-types';
import { ThinkingCard } from './ThinkingCard';
import { ToolCard, ToolInvocation } from './ToolCard';
import { InteractiveTerminalCard } from './InteractiveTerminalCard';
import { GenericToolTraceCard } from './GenericToolTraceCard';
import { AskUserCard } from './AskUserCard';
import { ApprovalCard } from './ApprovalCard';
import { parseContentToBlocks } from '@/lib/chat/content-detector';
import { isCommandSessionTool } from '@/lib/chat/command-session';
import { MessageBlockItem } from './MessageBlockItem';

interface ToolRendererProps {
    toolInvocation: ToolInvocation;
    isFinished: boolean;
    process?: AdminProcessRef;
}

function CommandSessionToolRenderer({ toolInvocation, process }: ToolRendererProps) {
    return (
        <motion.div layout className="flex flex-col">
            <ToolCard toolInvocation={toolInvocation} hideResult={!!process} />
            {process && (
                <div className="mt-1 mb-2 ml-[15px] relative z-10 w-[calc(100%-15px)]">
                    <div className="absolute -left-[14px] top-0 w-3 h-4 border-l-[1.5px] border-b-[1.5px] border-zinc-300 dark:border-zinc-700 rounded-bl-xl" />
                    <InteractiveTerminalCard process={process} />
                </div>
            )}
        </motion.div>
    );
}

// Registry for tools that need specific rendering formats
const ToolRegistry: Record<string, React.FC<ToolRendererProps> | null> = {
    'start_background_command': CommandSessionToolRenderer,
    'run_system_command': CommandSessionToolRenderer,
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
    processes?: AdminProcessRef[];
}

export const ContentDispatcher = React.memo(function ContentDispatcher({ 
    node, 
    isExecuting, 
    isStreaming, 
    resultNode,
    processes = [],
}: ContentDispatcherProps) {
    const t = useT();
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

            if (node.executionType === 'tool_call' || node.executionType === 'tool_result') {
                const toolName = node.toolName || 'Unknown Tool';
                const resultExecNode = resultNode as UiExecutionNode | undefined;
                const isFinished = node.executionType === 'tool_result' || !!resultNode || !!node.result || !isExecuting;
                const result = resultExecNode?.result || node.result;
                
                const toolInvocation: ToolInvocation = {
                    toolCallId: node.toolCallId || '',
                    toolName,
                    args: node.args || {},
                    state: isFinished ? 'result' : 'call',
                    result: result
                };
                const matchedProcess = toolInvocation.toolCallId
                    ? processes.find((process) => process.toolCallId === toolInvocation.toolCallId)
                    : undefined;

                if (matchedProcess || isCommandSessionTool(toolName)) {
                    return <CommandSessionToolRenderer toolInvocation={toolInvocation} isFinished={isFinished} process={matchedProcess} />;
                }

                if (toolName in ToolRegistry) {
                    const Renderer = ToolRegistry[toolName];
                    if (Renderer === null) return null; // Explicitly hidden if mapped to null
                    return <Renderer toolInvocation={toolInvocation} isFinished={isFinished} process={matchedProcess} />;
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
            const approvalKind = String(node.approvalKind || "").trim().toLowerCase();
            const approvalLabel =
                approvalKind === "human_input_required" || approvalKind === "ask_user" || approvalKind === "waiting_input"
                    ? t(lt("等待你的输入", "Waiting for your answer"))
                    : approvalKind === "safety_review"
                      ? t(lt("安全复核", "Safety review"))
                      : approvalKind === "safety_blocked"
                        ? t(lt("安全阻断", "Safety blocked"))
                        : t(lt("系统确认", "Approval"));
            const controlLabel =
                node.governanceType === "safety_blocked"
                    ? t(lt("安全阻断", "Safety blocked"))
                    : node.governanceType === "context_governance"
                        ? t(lt("上下文治理", "Context governance"))
                        : node.governanceType === "lane_updated"
                            ? t(lt("运行调度", "Run scheduling"))
                            : t(lt("系统控制信号", "System control"));
            const question = node.question || node.reason || node.topic || node.status || "";
            const isAskUser =
                isApproval &&
                (approvalKind === "human_input_required" || approvalKind === "ask_user" || approvalKind === "waiting_input");
            if (isAskUser) {
                return <AskUserCard question={question} status={node.status} />;
            }
            return (
                <ApprovalCard
                    title={isApproval ? approvalLabel : controlLabel}
                    body={question}
                    status={node.status}
                    tone={
                        !isApproval
                            ? node.governanceType === "safety_blocked"
                                ? "safety"
                                : "control"
                            : approvalKind === "safety_blocked"
                                ? "safety"
                                : "approval"
                    }
                />
            );
        }

        default:
            return null;
    }
});
