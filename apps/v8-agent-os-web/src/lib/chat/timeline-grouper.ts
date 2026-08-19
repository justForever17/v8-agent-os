import { UiTimelineNode, UiExecutionNode } from "@/store/chat-types";

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

// 判定节点是否属于普通运行轨迹节点（需合并折叠）
export function isCollapsibleTraceNode(node: UiTimelineNode): boolean {
    if (node.kind !== "execution") {
        return false;
    }
    
    const execNode = node as UiExecutionNode;
    if (String(execNode.toolCallId || "").startsWith("call_v8_attachment_preflight_")) {
        return false;
    }
    
    // 1. 推理思考过程 (ThinkingCard)
    if (execNode.executionType === "reasoning") {
        return true;
    }
    
    if (execNode.executionType === "tool_call" || execNode.executionType === "tool_result") {
        const toolName = String(execNode.toolName || "").trim();
        
        // 2.1 排除：ask_user（已由输入框上方 AskUserModal 弹窗承载，气泡内返回 null）
        if (toolName === "ask_user") {
            return false;
        }
        
        // 2.2 排除：任务进度 Todo 工具（已由右侧工作台 Todos HUD 承载，气泡内返回 null）
        if (toolName === "write_todos" || toolName === "update_todo") {
            return false;
        }
        
        // 2.3 Mcp 网页 App 独立框架 (McpAppFrame)
        const hasMcp = !!(execNode.mcpApp || execNode.data?.mcpApp || execNode.data?.mcp_app);
        if (hasMcp) {
            return false;
        }
        
        // 2.4 排除：其他在气泡中渲染为 null 的隐藏工具
        if (toolName === "inspect_and_move_media") {
            return false;
        }
        
        return true; // 剩下的常规普通工具（如 grep_search、view_file 等）参与合并折叠
    }
    
    if (execNode.executionType === "runtime_progress") {
        return true; // 运行时微型进度条，参与折叠
    }
    
    return false;
}

export type TimelineNodeSegment = {
    kind: "node";
    id: string;
    node: UiTimelineNode;
};

export type TimelineTraceGroupSegment = {
    kind: "trace_group";
    id: string;
    nodes: UiTimelineNode[];
    reasoningCount: number;
    toolCount: number;
    totalDuration: number; // 秒数
    isStreaming: boolean;
};

export type TimelineSegment = TimelineNodeSegment | TimelineTraceGroupSegment;

function extractNodeDuration(node: UiExecutionNode, resultNode?: UiExecutionNode): number {
    const data = asRecord(node.data);
    const resultData = asRecord(resultNode?.data);
    
    const seconds = Number(
        data.durationSeconds ||
        data.duration_seconds ||
        data.elapsedSeconds ||
        data.elapsed_seconds ||
        resultData.durationSeconds ||
        resultData.duration_seconds ||
        resultData.elapsedSeconds ||
        resultData.elapsed_seconds ||
        0
    );
    if (seconds > 0) {
        return seconds;
    }
    
    const ms = Number(
        data.durationMs ||
        data.duration_ms ||
        resultData.durationMs ||
        resultData.duration_ms ||
        resultData.elapsedMs ||
        resultData.elapsed_ms ||
        0
    );
    if (ms > 0) {
        return ms / 1000;
    }
    
    const nodeTime = Number(node.time || 0);
    if (nodeTime > 0) {
        return nodeTime / 1000;
    }

    const resultTime = Number(resultNode?.time || 0);
    if (resultTime > 0) {
        return resultTime / 1000;
    }
    
    return 0;
}

function toolInvocationCountKey(node: UiExecutionNode, fallbackIndex: number): string {
    const toolIdentity = String(node.toolCallId || "").trim();
    if (!toolIdentity) {
        return `node:${String(node.id || fallbackIndex)}`;
    }
    const runId = String(node.runId || "").trim();
    const ownerStreamKey = String(node.ownerStreamKey || "").trim();
    return `tool:${runId}:${ownerStreamKey}:${toolIdentity}`;
}

// 客户端自适应聚类核心逻辑
export function groupTimelineNodes(
    nodes: UiTimelineNode[],
    resultNodesByToolCallId: Map<string, UiExecutionNode>
): TimelineSegment[] {
    const segments: TimelineSegment[] = [];
    let traceBuffer: UiTimelineNode[] = [];
    let groupCounter = 0;

    const flushTrace = () => {
        if (traceBuffer.length === 0) return;
        
        let reasoningCount = 0;
        let toolCount = 0;
        let totalDuration = 0;
        let isStreaming = false;
        const countedToolKeys = new Set<string>();

        traceBuffer.forEach((node, nodeIndex) => {
            const execNode = node as UiExecutionNode;
            if (execNode.executionType === "reasoning") {
                reasoningCount++;
                totalDuration += extractNodeDuration(execNode);
            } else if (execNode.executionType === "tool_call") {
                const toolKey = toolInvocationCountKey(execNode, nodeIndex);
                if (countedToolKeys.has(toolKey)) {
                    return;
                }
                countedToolKeys.add(toolKey);
                toolCount++;
                const toolCallId = execNode.toolCallId?.trim();
                const resultNode = toolCallId ? resultNodesByToolCallId.get(toolCallId) : undefined;
                totalDuration += extractNodeDuration(execNode, resultNode);
            } else if (execNode.executionType === "runtime_progress") {
                isStreaming = true;
            }
        });

        segments.push({
            kind: "trace_group",
            id: `trace-group-${groupCounter++}`,
            nodes: [...traceBuffer],
            reasoningCount,
            toolCount,
            totalDuration: Number(totalDuration.toFixed(1)),
            isStreaming
        });
        traceBuffer = [];
    };

    nodes.forEach((node, index) => {
        if (isCollapsibleTraceNode(node)) {
            traceBuffer.push(node);
        } else {
            flushTrace();
            segments.push({
                kind: "node",
                id: `node-${node.id || index}-${segments.length}`,
                node
            });
        }
    });

    flushTrace();
    return segments;
}
