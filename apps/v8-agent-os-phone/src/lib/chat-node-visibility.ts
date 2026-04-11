import type { PhoneUiExecutionNode, PhoneUiTimelineNode } from "@/src/types/admin";

const HIDDEN_RUNTIME_PROGRESS_TOPICS = new Set([
    "session.connected",
    "session.subscribed",
]);

export function isHiddenPhoneTimelineNode(node: PhoneUiTimelineNode | null | undefined) {
    if (!node) {
        return false;
    }

    if (node.kind === "artifact") {
        return true;
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

export function getRenderablePhoneTimelineNodes(nodes: PhoneUiTimelineNode[] | null | undefined) {
    if (!Array.isArray(nodes) || nodes.length === 0) {
        return [];
    }
    const visible = nodes.filter((node) => !isHiddenPhoneTimelineNode(node));
    const toolCallIds = new Set(
        visible
            .filter((node): node is PhoneUiExecutionNode & { toolCallId: string } =>
                node.kind === "execution"
                && node.executionType === "tool_call"
                && typeof node.toolCallId === "string"
                && node.toolCallId.trim().length > 0,
            )
            .map((node) => node.toolCallId.trim()),
    );
    return visible.filter((node) => {
        if (
            node.kind !== "execution"
            || node.executionType !== "tool_result"
            || typeof node.toolCallId !== "string"
        ) {
            return true;
        }
        return !toolCallIds.has(node.toolCallId.trim());
    });
}

export function hasRenderablePhoneTimelineNodes(nodes: PhoneUiTimelineNode[] | null | undefined) {
    return getRenderablePhoneTimelineNodes(nodes).length > 0;
}
