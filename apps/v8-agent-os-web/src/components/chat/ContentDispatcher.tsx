'use client';

import { useT } from "@/components/providers/LocaleProvider";
import React from 'react';
import { motion } from 'framer-motion';
import { buildClientToolSurface, normalizeV8ActionRequest, type AdminProcessRef } from '@v8/session-realtime';
import { UiTimelineNode, UiExecutionNode } from '@/store/chat-types';
import { ThinkingCard } from './ThinkingCard';
import { McpAppFrame } from './McpAppFrame';
import { ToolCard, ToolInvocation } from './ToolCard';
import { GenericToolTraceCard } from './GenericToolTraceCard';
import { ApprovalCard } from './ApprovalCard';
import { parseContentToBlocks } from '@/lib/chat/content-detector';
import { isCommandSessionTool } from '@/lib/chat/command-session';
import { MessageBlockItem } from './MessageBlockItem';

const TODO_MUTATION_PATTERNS = [
    /command\s*\(\s*update\s*=\s*\{[^)]*todos/i,
    /\bpersistent task plan\b/i,
    /\btodo\s*#?\d+\b.*\b(marked|updated|done|in_progress|pending|skipped|created)\b/i,
    /\bcreated with\s+\d+\s+items\b/i,
];

function tryParseJsonRecord(value: unknown): Record<string, unknown> | null {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
        return value as Record<string, unknown>;
    }
    if (typeof value !== 'string') {
        return null;
    }
    try {
        const parsed = JSON.parse(value);
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
            ? parsed as Record<string, unknown>
            : null;
    } catch {
        return null;
    }
}

function extractSafetyEventSummary(value: unknown): Record<string, unknown> | undefined {
    const request = tryParseJsonRecord(value);
    if (!request) return undefined;
    const direct = tryParseJsonRecord(request.eventSummary);
    if (direct) return direct;
    const safety = tryParseJsonRecord(request.safety);
    const safetySummary = tryParseJsonRecord(safety?.eventSummary);
    if (safetySummary) return safetySummary;
    const details = tryParseJsonRecord(safety?.details);
    const nested = tryParseJsonRecord(details?.eventSummary);
    return nested || undefined;
}

function SessionCoordinationCard({ node }: { node: Extract<UiTimelineNode, { kind: 'governance' }> }) {
    const t = useT();
    const info = tryParseJsonRecord(node.requestInfo) || {};
    const direction = String(info.direction || node.reason || "incoming").trim().toLowerCase();
    const incoming = direction !== "outgoing";
    const status = String(info.state || node.status || "queued").trim().toLowerCase();
    const statusLabel = t(`web.sessionCoordination.status.${status}`);
    const intent = String(info.intent || "inform").trim().toLowerCase();
    const sessionLabel = String(
        incoming
            ? info.sourceSessionTitle || info.sourceSessionId || ""
            : info.targetSessionTitle || info.targetSessionId || "",
    ).trim();
    const body = String(node.question || info.summary || node.topic || "").trim();
    const replyStatus = String(info.replyStatus || "").trim().toLowerCase();
    return (
        <motion.div
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.18, ease: 'easeOut' }}
            className={`my-2 w-full max-w-[42rem] rounded-2xl border px-4 py-3 shadow-sm ${
                incoming
                    ? 'mr-auto border-cyan-500/20 bg-cyan-500/[0.055]'
                    : 'ml-auto border-primary/25 bg-primary/[0.07]'
            }`}
        >
            <div className="flex items-center gap-2 text-xs">
                <span className={`h-2 w-2 shrink-0 rounded-full ${incoming ? 'bg-cyan-400' : 'bg-primary'}`} />
                <span className="font-semibold text-foreground">
                    {t(incoming ? 'web.sessionCoordination.incoming' : 'web.sessionCoordination.outgoing')}
                </span>
                <span className="rounded-full border border-border/70 bg-background/65 px-2 py-0.5 text-[10px] text-muted-foreground">
                    {t(`web.sessionCoordination.intent.${intent}`)}
                </span>
                <span className="rounded-full border border-border/70 bg-background/65 px-2 py-0.5 text-[10px] text-muted-foreground">
                    {statusLabel}
                </span>
            </div>
            {sessionLabel ? (
                <div className="mt-1 truncate text-[11px] text-muted-foreground" title={sessionLabel}>
                    {sessionLabel}
                </div>
            ) : null}
            {body ? (
                <div className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-foreground/90">
                    {body}
                </div>
            ) : null}
            {replyStatus ? (
                <div className="mt-2 text-[11px] text-muted-foreground">
                    {t('web.sessionCoordination.reply')}: {t(`web.sessionCoordination.replyStatus.${replyStatus}`)}
                </div>
            ) : null}
        </motion.div>
    );
}

function compactToolResult(toolName: string, value: unknown) {
    if (toolName !== 'download_media_for_vision') {
        return value;
    }
    const record = tryParseJsonRecord(value);
    if (!record) {
        return value;
    }
    return {
        ok: record.ok,
        artifactId: record.artifactId ?? record.primaryArtifactId,
        kind: record.kind ?? record.primaryKind,
        mimeType: record.mimeType,
        fileName: record.fileName,
        workspacePath: record.workspacePath ?? record.canonicalPath ?? record.userVisiblePath ?? record.primaryFile,
        workspaceRelativePath: record.workspaceRelativePath,
        message: record.message ?? record.statusMessage ?? record.error,
    };
}

function extractMcpAppRef(...values: unknown[]) {
    for (const value of values) {
        if (!value || typeof value !== "object" || Array.isArray(value)) {
            continue;
        }
        const record = value as Record<string, unknown>;
        const appInstanceId = String(record.appInstanceId || record.app_instance_id || "").trim();
        const resourceUri = String(record.resourceUri || record.resource_uri || "").trim();
        if (!appInstanceId || !resourceUri) {
            continue;
        }
        return {
            appInstanceId,
            serverName: String(record.serverName || record.server_name || "").trim() || undefined,
            resourceUri,
            toolInvocationId: String(record.toolInvocationId || record.tool_invocation_id || "").trim() || undefined,
            status: String(record.status || "").trim() || undefined,
            renderer: String(record.renderer || "").trim() || undefined,
            title: String(record.title || "").trim() || undefined,
            externalUrl: String(record.externalUrl || record.external_url || "").trim() || undefined,
            thumbnailUrl: String(record.thumbnailUrl || record.thumbnail_url || "").trim() || undefined,
            fileKey: String(record.fileKey || record.file_key || "").trim() || undefined,
            nodeId: String(record.nodeId || record.node_id || "").trim() || undefined,
            presentation: record.presentation && typeof record.presentation === "object" ? record.presentation as { web?: "inline" | "edge_to_edge"; phone?: "inline" | "modal" } : undefined,
            allowedFrameOrigins: Array.isArray(record.allowedFrameOrigins) ? record.allowedFrameOrigins.map((item) => String(item || "")).filter(Boolean) : undefined,
            actionRequest: normalizeV8ActionRequest(record.actionRequest ?? record.action_request),
        };
    }
    return null;
}

function isProcessStillRunning(process: AdminProcessRef | undefined) {
    if (!process) {
        return false;
    }
    const status = String(process.status || '').trim().toLowerCase();
    if (process.completedAt) {
        return false;
    }
    return [
        'queued',
        'pending',
        'starting',
        'running',
        'streaming',
        'waiting_input',
        'waiting_approval',
    ].includes(status);
}

function looksLikeTodoMutationText(value: unknown) {
    const normalized = String(value || '').trim();
    if (!normalized) {
        return false;
    }
    return TODO_MUTATION_PATTERNS.some((pattern) => pattern.test(normalized));
}

function containsTodoMutationHint(value: unknown, depth = 0): boolean {
    if (depth > 4 || value === null || value === undefined) {
        return false;
    }
    if (typeof value === 'string') {
        return looksLikeTodoMutationText(value);
    }
    if (Array.isArray(value)) {
        return value.some((item) => containsTodoMutationHint(item, depth + 1));
    }
    if (typeof value !== 'object') {
        return false;
    }
    const record = value as Record<string, unknown>;
    const update = tryParseJsonRecord(record.update);
    const request = tryParseJsonRecord(record.request);
    if ('todos' in record || 'todo' in record) {
        return true;
    }
    if (update && ('todos' in update || 'todo' in update)) {
        return true;
    }
    if (request && ('todos' in request || 'todo' in request)) {
        return true;
    }
    return Object.values(record).some((nested) => containsTodoMutationHint(nested, depth + 1));
}

function isTodoLikeExecutionNode(node: UiExecutionNode) {
    const toolName = String(node.toolName || '').trim();
    if (toolName === 'write_todos' || toolName === 'update_todo') {
        return true;
    }
    return (
        looksLikeTodoMutationText(node.label)
        || looksLikeTodoMutationText(node.content)
        || containsTodoMutationHint(node.args)
        || containsTodoMutationHint(node.result)
    );
}

function isAskUserExecutionNode(node: UiExecutionNode) {
    return String(node.toolName || '').trim() === 'ask_user';
}

function isNoticeableContextGovernance(requestInfo: unknown) {
    if (!requestInfo || typeof requestInfo !== "object" || Array.isArray(requestInfo)) {
        return false;
    }
    const record = requestInfo as Record<string, unknown>;
    return Boolean(record.noticeable_latency);
}

interface ToolRendererProps {
    toolInvocation: ToolInvocation;
    isFinished: boolean;
    process?: AdminProcessRef;
}

function CommandSessionToolRenderer({ toolInvocation, process }: ToolRendererProps) {
    return (
        <motion.div layout className="flex flex-col">
            <ToolCard toolInvocation={toolInvocation} hideResult={isProcessStillRunning(process)} />
        </motion.div>
    );
}

// Registry for tools that need specific rendering formats
const ToolRegistry: Record<string, React.FC<ToolRendererProps> | null> = {
    'start_background_command': CommandSessionToolRenderer,
    'run_system_command': CommandSessionToolRenderer,
    // Tools that we want to render subtly using GenericToolTraceCard (previously blacklisted)
    'write_todos': null,
    'update_todo': null,
    'inspect_and_move_media': null,
    'download_media_for_vision': ({ toolInvocation }) => <GenericToolTraceCard toolInvocation={toolInvocation} />,
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
                        reasoningKind={node.reasoningKind || node.data?.reasoningKind}
                        reasoningSurface={node.data?.reasoningSurface}
                    />
                );
            }

            if (node.executionType === 'tool_call' || node.executionType === 'tool_result') {
                if (isTodoLikeExecutionNode(node) || isAskUserExecutionNode(node)) {
                    return null;
                }
                const resultExecNode = resultNode as UiExecutionNode | undefined;
                const toolName = node.toolName || resultExecNode?.toolName || t('web.toolCard.defaultName');
                const isFinished = node.executionType === 'tool_result' || !!resultNode || !!node.result || !isExecuting;
                const result = resultExecNode?.agentVisibleResult
                    ?? resultExecNode?.data?.agentVisibleResult
                    ?? resultExecNode?.data?.agent_visible_result
                    ?? node.agentVisibleResult
                    ?? node.data?.agentVisibleResult
                    ?? node.data?.agent_visible_result
                    ?? resultExecNode?.result
                    ?? node.result;
                
                const toolInvocation: ToolInvocation = {
                    toolCallId: node.toolCallId || '',
                    toolName,
                    args: node.args || {},
                    state: isFinished ? 'result' : 'call',
                    result: compactToolResult(toolName, result),
                    clientSurface: buildClientToolSurface({ toolName, result, state: isFinished ? 'result' : 'call' })
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
                const mcpApp = extractMcpAppRef(
                    node.mcpApp,
                    node.data?.mcpApp,
                    node.data?.mcp_app,
                    resultExecNode?.mcpApp,
                    resultExecNode?.data?.mcpApp,
                    resultExecNode?.data?.mcp_app,
                );
                return (
                    <div className="flex flex-col gap-1">
                        <ToolCard toolInvocation={toolInvocation} />
                        {mcpApp ? <McpAppFrame mcpApp={mcpApp} /> : null}
                    </div>
                );
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
            const blocks = parseContentToBlocks(node.content, isStreaming, 0, false);

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
                    ? t("web.generated.e75707ccab")
                    : approvalKind === "safety_review"
                      ? t("web.generated.92f73fc6ad")
                      : approvalKind === "safety_blocked"
                        ? t("web.generated.a5dd088a32")
                        : t("web.generated.6257968f39");
            const controlLabel =
                node.governanceType === "safety_blocked"
                    ? t("web.generated.a5dd088a32")
                    : node.governanceType === "context_governance"
                        ? t("web.generated.a85ae899a5")
                        : node.governanceType === "lane_updated"
                            ? t("web.generated.41dd0117b1")
                            : t("web.generated.db074da77e");
            const question = node.question || node.reason || node.topic || node.status || "";
            const eventSummary = extractSafetyEventSummary(node.requestInfo);
            if (node.governanceType === "ask_user") {
                return null;
            }
            if (node.governanceType === "session_coordination") {
                return <SessionCoordinationCard node={node} />;
            }
            if (node.governanceType === "context_governance") {
                if (!isNoticeableContextGovernance(node.requestInfo)) {
                    return null;
                }
                return (
                    <motion.div
                        initial={{ opacity: 0, y: 4 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.2, ease: 'easeOut' }}
                        className="my-1.5 flex w-full items-center gap-3"
                    >
                        <div className="h-px flex-1 bg-border/70" />
                        <div className="flex max-w-[78%] items-center gap-2 rounded-full border border-border/80 bg-background/80 px-3 py-1.5 text-[11px] shadow-[0_0_20px_rgba(148,163,184,0.08)] backdrop-blur-sm">
                            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-cyan-400/90 shadow-[0_0_10px_rgba(34,211,238,0.35)]" />
                            <span className="truncate font-semibold text-foreground/90">
                                {t("web.generated.a85ae899a5")}
                            </span>
                            {question ? (
                                <span className="truncate text-muted-foreground">
                                    {question}
                                </span>
                            ) : null}
                        </div>
                        <div className="h-px flex-1 bg-border/70" />
                    </motion.div>
                );
            }
            return (
                <ApprovalCard
                    title={isApproval ? approvalLabel : controlLabel}
                    body={question}
                    status={node.status}
                    eventSummary={eventSummary}
                    tone={
                        !isApproval
                            ? node.governanceType === "safety_blocked"
                                ? "safety"
                                : "control"
                            : approvalKind === "safety_blocked" || approvalKind === "safety_review"
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
