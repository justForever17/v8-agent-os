export type TimelineSegmentTraceExecutionType =
  | "reasoning"
  | "tool_call"
  | "tool_result"
  | "runtime_progress";

export type TimelineSegmentNode = {
  id?: string;
  kind?: string;
  executionType?: string;
  traceGroupId?: string;
  timestamp?: number;
  finalized?: boolean;
  [key: string]: unknown;
};

export type MessageTimelineNodeSegment<TNode extends TimelineSegmentNode = TimelineSegmentNode> = {
  kind: "node";
  id: string;
  node: TNode;
};

export type MessageTimelineTraceGroupSegment<TNode extends TimelineSegmentNode = TimelineSegmentNode> = {
  kind: "trace_group";
  id: string;
  nodes: TNode[];
  collapsedByDefault: boolean;
  active: boolean;
};

export type MessageTimelineSegment<TNode extends TimelineSegmentNode = TimelineSegmentNode> =
  | MessageTimelineNodeSegment<TNode>
  | MessageTimelineTraceGroupSegment<TNode>;

export type BuildMessageTimelineSegmentsOptions = {
  active?: boolean;
};

const TRACE_EXECUTION_TYPES = new Set<string>([
  "reasoning",
  "tool_call",
  "tool_result",
  "runtime_progress",
]);

function nodeKey(node: TimelineSegmentNode, index: number) {
  return String(node.id || `node-${index}`).trim() || `node-${index}`;
}

export function isTraceTimelineNode(node: TimelineSegmentNode | null | undefined) {
  return Boolean(
    node
    && node.kind === "execution"
    && TRACE_EXECUTION_TYPES.has(String(node.executionType || "").trim()),
  );
}

function hasNarrativeAfter<TNode extends TimelineSegmentNode>(nodes: TNode[], startIndex: number) {
  for (let index = startIndex + 1; index < nodes.length; index += 1) {
    if (nodes[index]?.kind === "narrative") {
      return true;
    }
  }
  return false;
}

export function buildMessageTimelineSegments<TNode extends TimelineSegmentNode>(
  nodes: TNode[] | null | undefined,
  options?: BuildMessageTimelineSegmentsOptions,
): MessageTimelineSegment<TNode>[] {
  if (!Array.isArray(nodes) || nodes.length === 0) {
    return [];
  }

  const segments: MessageTimelineSegment<TNode>[] = [];
  let traceBuffer: TNode[] = [];
  let traceStartIndex = -1;
  let traceGroupId = "";

  const flushTrace = (endIndex: number) => {
    if (traceBuffer.length === 0) {
      return;
    }
    const groupIndex = segments.length;
    const groupId = traceGroupId || `trace-${nodeKey(traceBuffer[0], traceStartIndex)}-${groupIndex}`;
    const followedByNarrative = hasNarrativeAfter(nodes, endIndex);
    const active = Boolean(options?.active) && !followedByNarrative;
    segments.push({
      kind: "trace_group",
      id: groupId,
      nodes: traceBuffer,
      collapsedByDefault: followedByNarrative && !active,
      active,
    });
    traceBuffer = [];
    traceStartIndex = -1;
    traceGroupId = "";
  };

  nodes.forEach((node, index) => {
    if (isTraceTimelineNode(node)) {
      const incomingGroupId = typeof node.traceGroupId === "string" ? node.traceGroupId.trim() : "";
      if (traceBuffer.length > 0 && incomingGroupId && traceGroupId && incomingGroupId !== traceGroupId) {
        flushTrace(index - 1);
      }
      if (traceBuffer.length === 0) {
        traceStartIndex = index;
        traceGroupId = incomingGroupId;
      }
      traceBuffer.push(node);
      return;
    }

    flushTrace(index - 1);
    segments.push({
      kind: "node",
      id: `node-${nodeKey(node, index)}-${segments.length}`,
      node,
    });
  });

  flushTrace(nodes.length - 1);
  return segments;
}
