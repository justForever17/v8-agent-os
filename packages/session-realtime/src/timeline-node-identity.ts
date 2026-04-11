type TimelineLikeArtifact = {
  id?: string | null;
  artifactId?: string | null;
  workspacePath?: string | null;
  sourcePath?: string | null;
  previewUrl?: string | null;
  externalUrl?: string | null;
  title?: string | null;
  kind?: string | null;
};

export type TimelineLikeNode = {
  id?: string | null;
  kind?: string | null;
  executionType?: string | null;
  role?: string | null;
  agentName?: string | null;
  agentRoleLabel?: string | null;
  content?: string | null;
  toolCallId?: string | null;
  toolName?: string | null;
  topic?: string | null;
  label?: string | null;
  governanceType?: string | null;
  reason?: string | null;
  status?: string | null;
  question?: string | null;
  artifact?: TimelineLikeArtifact | null;
};

function normalizeText(value: unknown) {
  return String(value || "").trim().replace(/\s+/g, " ").toLowerCase();
}

function longestOverlapSuffixPrefix(current: string, incoming: string) {
  const limit = Math.min(current.length, incoming.length);
  for (let size = limit; size > 0; size -= 1) {
    if (current.slice(-size) === incoming.slice(0, size)) {
      return size;
    }
  }
  return 0;
}

function mergeNarrativeContent(current: unknown, incoming: unknown) {
  const existing = String(current || "");
  const next = String(incoming || "");
  if (!existing) {
    return next;
  }
  if (!next || next === existing) {
    return existing;
  }
  if (next.startsWith(existing) || next.includes(existing)) {
    return next;
  }
  if (existing.startsWith(next) || existing.includes(next)) {
    return existing;
  }
  const overlap = longestOverlapSuffixPrefix(existing, next);
  if (overlap > 0) {
    return `${existing}${next.slice(overlap)}`;
  }
  return next.length >= existing.length ? next : existing;
}

function isAssistantNarrativeNode(node: TimelineLikeNode | null | undefined) {
  return normalizeText(node?.kind) === "narrative" && normalizeText(node?.role) === "assistant";
}

function buildArtifactKey(artifact: TimelineLikeArtifact | null | undefined) {
  if (!artifact || typeof artifact !== "object") {
    return "";
  }
  return String(
    artifact.id
      || artifact.artifactId
      || artifact.workspacePath
      || artifact.sourcePath
      || artifact.previewUrl
      || artifact.externalUrl
      || `${artifact.kind || "artifact"}:${artifact.title || ""}`,
  ).trim();
}

export function buildSemanticTimelineNodeKey(node: TimelineLikeNode | null | undefined) {
  if (!node || typeof node !== "object") {
    return "";
  }

  const kind = normalizeText(node.kind);
  if (!kind) {
    return "";
  }

  if (kind === "execution") {
    const executionType = normalizeText(node.executionType);
    const toolCallId = normalizeText(node.toolCallId);
    if (toolCallId && (executionType === "tool_call" || executionType === "tool_result")) {
      return `execution:${executionType}:${toolCallId}`;
    }
    if (executionType === "reasoning") {
      const content = normalizeText(node.content);
      return content ? `execution:reasoning:${content}` : "";
    }
    if (executionType === "runtime_progress") {
      const topic = normalizeText(node.topic);
      const label = normalizeText(node.label || node.content);
      return topic || label ? `execution:runtime_progress:${topic}:${label}` : "";
    }
    if (executionType === "agent_start") {
      const label = normalizeText(node.label || node.toolName || node.topic);
      return label ? `execution:agent_start:${label}` : "execution:agent_start";
    }
  }

  if (kind === "narrative") {
    const role = normalizeText(node.role);
    if (!role) {
      return "";
    }
    if (role === "assistant") {
      const lane = normalizeText(node.agentName || node.agentRoleLabel || "assistant");
      return `narrative:${role}:${lane}`;
    }
    const content = normalizeText(node.content);
    return content ? `narrative:${role}:${content}` : `narrative:${role}`;
  }

  if (kind === "artifact") {
    const artifactKey = buildArtifactKey(node.artifact || null);
    return artifactKey ? `artifact:${artifactKey}` : "";
  }

  if (kind === "governance") {
    const governanceType = normalizeText(node.governanceType);
    const topic = normalizeText(node.topic);
    const summary = normalizeText(node.reason || node.status || node.question || node.content);
    return governanceType || topic || summary
      ? `governance:${governanceType}:${topic}:${summary}`
      : "";
  }

  return "";
}

export function mergeTimelineNodesByIdentity<TNode extends TimelineLikeNode>(base: TNode[] = [], incoming: TNode[] = []): TNode[] {
  const merged: TNode[] = [];
  const indexByKey = new Map<string, number>();

  for (const node of [...base, ...incoming]) {
    const nodeId = String(node.id || "").trim();
    const semanticKey = buildSemanticTimelineNodeKey(node);
    const identityKeys = [nodeId ? `id:${nodeId}` : "", semanticKey ? `semantic:${semanticKey}` : ""].filter(Boolean);
    const existingIndex = identityKeys
      .map((key) => indexByKey.get(key))
      .find((value): value is number => value !== undefined);

    if (existingIndex === undefined) {
      const nextIndex = merged.length;
      merged.push({ ...node });
      identityKeys.forEach((key) => indexByKey.set(key, nextIndex));
      continue;
    }

    const existingNode = merged[existingIndex];
    merged[existingIndex] = {
      ...existingNode,
      ...node,
    };
    if (isAssistantNarrativeNode(existingNode) && isAssistantNarrativeNode(node)) {
      merged[existingIndex].content = mergeNarrativeContent(existingNode.content, node.content) as TNode["content"];
    }
    identityKeys.forEach((key) => indexByKey.set(key, existingIndex));
  }

  return merged;
}
