import type { PhoneUiExecutionNode, PhoneUiTimelineNode } from "@/src/types/admin";

const HIDDEN_RUNTIME_PROGRESS_TOPICS = new Set([
    "session.connected",
    "session.subscribed",
]);

export function isHiddenPhoneTimelineNode(node: PhoneUiTimelineNode | null | undefined) {
    if (!node) {
        return false;
    }

    if (node.kind !== "execution") {
        return false;
    }

    const executionNode = node as PhoneUiExecutionNode;
    if (executionNode.executionType === "agent_start") {
        return true;
    }

    if (executionNode.executionType === "runtime_progress") {
        const topic = String(
            executionNode.topic
            || executionNode.data?.topic
            || "",
        ).trim();
        return HIDDEN_RUNTIME_PROGRESS_TOPICS.has(topic);
    }

    return false;
}

function isExecutionNode(node: PhoneUiTimelineNode): node is PhoneUiExecutionNode {
    return node.kind === "execution";
}

function getNormalizedToolCallId(node: PhoneUiTimelineNode | null | undefined) {
    if (!node || !isExecutionNode(node) || typeof node.toolCallId !== "string") {
        return "";
    }
    return node.toolCallId.trim();
}

function getNormalizedToolName(node: PhoneUiTimelineNode | null | undefined) {
    if (!node || !isExecutionNode(node)) {
        return "";
    }
    return String(
        node.toolName
        || node.data?.toolName
        || node.data?.tool_name
        || "",
    ).trim().toLowerCase();
}

function isToolCallNode(node: PhoneUiTimelineNode): node is PhoneUiExecutionNode & { toolCallId: string } {
    return isExecutionNode(node)
        && node.executionType === "tool_call"
        && getNormalizedToolCallId(node).length > 0;
}

function isToolResultNode(node: PhoneUiTimelineNode): node is PhoneUiExecutionNode {
    return isExecutionNode(node) && node.executionType === "tool_result";
}

export function buildPhoneToolExecutionView(nodes: PhoneUiTimelineNode[] | null | undefined) {
    if (!Array.isArray(nodes) || nodes.length === 0) {
        return {
            renderableNodes: [] as PhoneUiTimelineNode[],
            resultNodesByToolCallId: new Map<string, PhoneUiExecutionNode>(),
        };
    }

    const visible = nodes.filter((node) => !isHiddenPhoneTimelineNode(node));
    const exactToolCallIds = new Set(
        visible
            .filter((node): node is PhoneUiExecutionNode & { toolCallId: string } => isToolCallNode(node))
            .map((node) => getNormalizedToolCallId(node)),
    );
    const resultNodesByToolCallId = new Map<string, PhoneUiExecutionNode>();
    const matchedResultNodeKeys = new Set<string>();
    const exactMatchedToolCallIds = new Set<string>();
    const getNodeKey = (node: PhoneUiTimelineNode, index: number) => `${node.id || "node"}:${index}`;

    visible.forEach((node, index) => {
        if (!isToolResultNode(node)) {
            return;
        }
        const toolCallId = getNormalizedToolCallId(node);
        if (!toolCallId || !exactToolCallIds.has(toolCallId)) {
            return;
        }
        resultNodesByToolCallId.set(toolCallId, node);
        matchedResultNodeKeys.add(getNodeKey(node, index));
        exactMatchedToolCallIds.add(toolCallId);
    });

    visible.forEach((node, index) => {
        if (!isToolResultNode(node)) {
            return;
        }
        const resultToolCallId = getNormalizedToolCallId(node);
        if (resultToolCallId && exactMatchedToolCallIds.has(resultToolCallId)) {
            return;
        }
        const toolName = getNormalizedToolName(node);
        if (!toolName) {
            return;
        }
        const candidateToolCalls = visible
            .slice(0, index)
            .filter((candidate): candidate is PhoneUiExecutionNode & { toolCallId: string } => isToolCallNode(candidate))
            .filter((candidate) => getNormalizedToolName(candidate) === toolName)
            .filter((candidate) => !resultNodesByToolCallId.has(getNormalizedToolCallId(candidate)));
        if (candidateToolCalls.length !== 1) {
            return;
        }
        const matchedToolCallId = getNormalizedToolCallId(candidateToolCalls[0]);
        if (!matchedToolCallId) {
            return;
        }
        resultNodesByToolCallId.set(matchedToolCallId, node);
        matchedResultNodeKeys.add(getNodeKey(node, index));
    });

    const renderableNodes = visible.filter((node, index) => {
        if (!isToolResultNode(node)) {
            return true;
        }
        return !matchedResultNodeKeys.has(getNodeKey(node, index));
    });

    return { renderableNodes, resultNodesByToolCallId };
}

export function getRenderablePhoneTimelineNodes(nodes: PhoneUiTimelineNode[] | null | undefined) {
    return buildPhoneToolExecutionView(nodes).renderableNodes;
}

export function hasRenderablePhoneTimelineNodes(nodes: PhoneUiTimelineNode[] | null | undefined) {
    return getRenderablePhoneTimelineNodes(nodes).length > 0;
}
