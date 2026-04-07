import type { PhoneUiExecutionNode, PhoneUiTimelineNode } from "@/src/types/admin";

const HIDDEN_RUNTIME_PROGRESS_TOPICS = new Set([
    "session.connected",
    "session.subscribed",
]);

export function isHiddenPhoneTimelineNode(node: PhoneUiTimelineNode | null | undefined) {
    if (!node || node.kind !== "execution") {
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

export function getRenderablePhoneTimelineNodes(nodes: PhoneUiTimelineNode[] | null | undefined) {
    if (!Array.isArray(nodes) || nodes.length === 0) {
        return [];
    }
    return nodes.filter((node) => !isHiddenPhoneTimelineNode(node));
}

export function hasRenderablePhoneTimelineNodes(nodes: PhoneUiTimelineNode[] | null | undefined) {
    return getRenderablePhoneTimelineNodes(nodes).length > 0;
}
