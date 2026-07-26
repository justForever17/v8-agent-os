export type SessionOutputNode = {
  id?: string | null;
  kind?: string | null;
  executionType?: string | null;
  toolName?: string | null;
  toolCallId?: string | null;
  args?: unknown;
  result?: unknown;
  data?: Record<string, unknown> | null;
  artifact?: unknown;
};

export type SessionOutputMessage = {
  id?: string | null;
  role?: string | null;
  metadata?: unknown;
  artifacts?: unknown[] | null;
  nodes?: SessionOutputNode[] | null;
};

export type SessionOutputProjection = {
  id: string;
  path: string | null;
  name: string;
  source: "spec" | "artifact" | "write";
  artifactId: string | null;
  mimeType: string | null;
  kind: string | null;
  statusLabel: string | null;
  rawArtifact: Record<string, unknown> | null;
};

type BuildSessionOutputOptions = {
  sessionId?: string | null;
  limit?: number;
  evidence?: unknown[] | null;
};

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function normalizedPath(value: unknown): string {
  return text(value).replace(/\\/g, "/");
}

function fileNameOf(value: string): string {
  const parts = normalizedPath(value).split("/").filter(Boolean);
  return parts[parts.length - 1] || value;
}

function isFilePath(value: string): boolean {
  const path = normalizedPath(value).replace(/[?#].*$/, "");
  if (!path || path.endsWith("/")) return false;
  const name = fileNameOf(path);
  if (!name || name === "." || name === "..") return false;
  return /\.[A-Za-z0-9][A-Za-z0-9._-]{0,15}$/.test(name)
    || /^(?:Dockerfile|Makefile|Procfile|LICENSE|README|CHANGELOG|NOTICE)$/i.test(name);
}

function safeRelativeFilePath(value: unknown): string {
  const path = normalizedPath(value).replace(/[?#].*$/, "");
  if (
    !path
    || path.startsWith("/")
    || /^[A-Za-z]:\//.test(path)
    || /^[A-Za-z][A-Za-z0-9+.-]*:\/\//.test(path)
    || /^(?:artifact|source|creative-media-job|toolobs|research|engineering):\/\//i.test(path)
  ) return "";
  return isFilePath(path) ? path : "";
}

function parseJsonCandidate(value: string): unknown {
  const candidate = value.trim();
  if (!(candidate.startsWith("{") || candidate.startsWith("[")) || candidate.length > 2_000_000) return value;
  try {
    return JSON.parse(candidate);
  } catch {
    return value;
  }
}

function visitStructured(value: unknown, visitor: (record: Record<string, unknown>) => void, depth = 0): void {
  if (depth > 8 || value == null) return;
  if (typeof value === "string") {
    const parsed = parseJsonCandidate(value);
    if (parsed !== value) visitStructured(parsed, visitor, depth + 1);
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value.slice(0, 160)) visitStructured(item, visitor, depth + 1);
    return;
  }
  const record = recordOf(value);
  if (!Object.keys(record).length) return;
  visitor(record);
  for (const nested of Object.values(record)) visitStructured(nested, visitor, depth + 1);
}

function firstPath(value: unknown): string {
  let found = "";
  visitStructured(value, (record) => {
    if (found) return;
    for (const key of [
      "workspaceRelativePath",
      "workspace_relative_path",
      "sourcePath",
      "source_path",
      "canonicalPath",
      "canonical_path",
      "targetPath",
      "target_path",
      "workspacePath",
      "workspace_path",
      "file",
      "path",
    ]) {
      const candidate = safeRelativeFilePath(record[key]);
      if (candidate) {
        found = candidate;
        return;
      }
    }
  });
  return found;
}

function artifactOrigin(record: Record<string, unknown>): string {
  const metadata = recordOf(record.metadata);
  return text(record.origin || record.artifactOrigin || record.artifact_origin || metadata.origin || metadata.source).toLowerCase();
}

function isUserSuppliedArtifact(record: Record<string, unknown>): boolean {
  const metadata = recordOf(record.metadata);
  const resourceRole = text(record.resourceRole || record.resource_role || metadata.resourceRole || metadata.resource_role || "artifact");
  if (resourceRole !== "artifact") return true;
  const origin = artifactOrigin(record);
  const component = text(record.sourceComponent || record.source_component).toLowerCase();
  return origin === "workspace_adopted"
    || origin === "vision_media_analyzer"
    || /(user|web|phone|composer|attachment).*upload|upload.*(user|web|phone|composer|attachment)/.test(origin)
    || /(user|web|phone|composer|attachment).*upload|upload.*(user|web|phone|composer|attachment)/.test(component);
}

function artifactProjection(
  raw: Record<string, unknown>,
  expectedSessionId: string,
  fallbackId: string,
): SessionOutputProjection | null {
  const artifactSessionId = text(raw.sessionId || raw.session_id);
  if (expectedSessionId && artifactSessionId && artifactSessionId !== expectedSessionId) return null;
  if (raw.surfaceVisible === false || raw.surface_visible === false || isUserSuppliedArtifact(raw)) return null;
  const kind = text(raw.kind || raw.artifact_kind).toLowerCase();
  if (kind === "directory" || kind === "folder") return null;
  const metadata = recordOf(raw.metadata);
  const storageClass = text(raw.storageClass || raw.storage_class || metadata.storageClass || metadata.storage_class).toLowerCase();
  const pathPlane = text(raw.pathPlane || raw.path_plane || metadata.pathPlane || metadata.path_plane).toLowerCase();
  const runtimePrivate = storageClass === "runtime_artifact" || pathPlane === "runtime" || pathPlane === "runtime_private";
  const path = runtimePrivate ? null : firstPath(raw) || null;
  const artifactId = text(raw.artifactId || raw.artifact_id || raw.id) || null;
  const title = text(raw.displayLabel || raw.title);
  if (!path && !artifactId) return null;
  return {
    id: `artifact:${artifactId || path || fallbackId}`,
    path,
    name: title || (path ? fileNameOf(path) : artifactId || "产物"),
    source: "artifact",
    artifactId,
    mimeType: text(raw.mimeType || raw.mime_type) || null,
    kind: kind || null,
    statusLabel: null,
    rawArtifact: raw,
  };
}

function specStatusLabel(spec: Record<string, unknown>, stage: string): string {
  const pipeline = recordOf(spec.pipelineControl || spec.pipeline_control);
  const approved = Array.isArray(pipeline.approvedStages)
    ? pipeline.approvedStages.map((item) => text(item).toLowerCase())
    : Array.isArray(pipeline.approved_stages)
      ? pipeline.approved_stages.map((item) => text(item).toLowerCase())
      : [];
  const blocked = text(pipeline.blockedByApproval || pipeline.blocked_by_approval).toLowerCase();
  if (approved.includes(stage.toLowerCase())) return "已同意";
  if (blocked === stage.toLowerCase()) return "待确认";
  return "可查看";
}

function collectSpecOutputs(value: unknown, target: Map<string, SessionOutputProjection>): void {
  visitStructured(value, (record) => {
    const spec = recordOf(record.specBrief || record.spec_brief);
    if (!Object.keys(spec).length) return;
    const specId = text(spec.specId || spec.spec_id) || "spec";
    const linkedSections = Array.isArray(spec.linkedSections)
      ? spec.linkedSections
      : Array.isArray(spec.linked_sections)
        ? spec.linked_sections
        : [];
    for (const rawSection of linkedSections) {
      const section = recordOf(rawSection);
      const path = normalizedPath(section.relativePath || section.relative_path);
      if (!isFilePath(path)) continue;
      const stage = text(section.stage || section.kind || fileNameOf(path));
      target.set(path.toLowerCase(), {
        id: `spec:${specId}:${stage}:${path}`,
        path,
        name: fileNameOf(path),
        source: "spec",
        artifactId: null,
        mimeType: "text/markdown",
        kind: "markdown",
        statusLabel: specStatusLabel(spec, stage),
        rawArtifact: null,
      });
    }
    const documents = recordOf(spec.documents);
    for (const [stage, rawDocument] of Object.entries(documents)) {
      const document = recordOf(rawDocument);
      const path = normalizedPath(document.relativePath || document.relative_path);
      if (!isFilePath(path)) continue;
      target.set(path.toLowerCase(), {
        id: `spec:${specId}:${stage}:${path}`,
        path,
        name: fileNameOf(path),
        source: "spec",
        artifactId: null,
        mimeType: "text/markdown",
        kind: "markdown",
        statusLabel: specStatusLabel(spec, stage),
        rawArtifact: null,
      });
    }
  });
}

function toolResultSucceeded(value: unknown): boolean {
  if (typeof value === "string") {
    const normalized = value.trim();
    const parsed = parseJsonCandidate(normalized);
    if (parsed !== normalized) return toolResultSucceeded(parsed);
    if (!normalized) return false;
    if (
      /^(?:error|failed|blocked)\b/i.test(normalized)
      || /\b(?:status|state)\s*[:=]\s*(?:error|failed|blocked|cancelled|rejected)\b/i.test(normalized)
      || /["']?(?:ok|success)["']?\s*[:=]\s*false\b/i.test(normalized)
    ) return false;
    return true;
  }
  if (Array.isArray(value)) return value.some(toolResultSucceeded);
  const record = recordOf(value);
  if (!Object.keys(record).length) return value != null;
  if (record.ok === false || record.success === false) return false;
  const status = text(record.status || record.state).toLowerCase();
  if (["error", "failed", "blocked", "cancelled", "rejected"].includes(status)) return false;
  return true;
}

const FILE_PRODUCING_TOOLS = /^(?:write_native_file|apply_patch|download_media_for_vision|creative_media_(?:assets|jobs|edit|quality))$/i;

function collectToolOutputs(messages: SessionOutputMessage[], target: Map<string, SessionOutputProjection>): void {
  const calls = new Map<string, { toolName: string; args: unknown }>();
  for (const message of messages) {
    for (const node of message.nodes || []) {
      if (node.kind !== "execution") continue;
      const callId = text(node.toolCallId);
      const toolName = text(node.toolName);
      if (node.executionType === "tool_call" && callId) calls.set(callId, { toolName, args: node.args });
      if (node.executionType !== "tool_result") continue;
      const call = callId ? calls.get(callId) : undefined;
      const effectiveTool = toolName || call?.toolName || "";
      if (/^spec_broker$/i.test(effectiveTool)) {
        collectSpecOutputs(node.result ?? node.data, target);
        continue;
      }
      if (!FILE_PRODUCING_TOOLS.test(effectiveTool) || !toolResultSucceeded(node.result ?? node.data)) continue;
      const path = firstPath(node.result) || firstPath(node.data) || firstPath(call?.args) || firstPath(node.args);
      if (!isFilePath(path)) continue;
      target.set(path.toLowerCase(), {
        id: `write:${callId || node.id || path}:${path}`,
        path,
        name: fileNameOf(path),
        source: "write",
        artifactId: null,
        mimeType: null,
        kind: null,
        statusLabel: "已生成",
        rawArtifact: null,
      });
    }
  }
}

export function buildSessionOutputProjection(
  messages: SessionOutputMessage[] | null | undefined,
  runtimeArtifacts: unknown[] | null | undefined = [],
  options: BuildSessionOutputOptions = {},
): SessionOutputProjection[] {
  const expectedSessionId = text(options.sessionId);
  const projected = new Map<string, SessionOutputProjection>();
  for (const evidence of options.evidence || []) collectSpecOutputs(evidence, projected);
  const sourceMessages = messages || [];
  for (const message of sourceMessages) {
    collectSpecOutputs(message.metadata, projected);
    if (text(message.role).toLowerCase() === "user") continue;
    for (const rawArtifact of message.artifacts || []) {
      const item = artifactProjection(recordOf(rawArtifact), expectedSessionId, text(message.id));
      if (item) projected.set((item.path || item.artifactId || item.id).toLowerCase(), item);
    }
    for (const node of message.nodes || []) {
      if (node.kind !== "artifact") continue;
      const item = artifactProjection(recordOf(node.artifact), expectedSessionId, text(node.id));
      if (item) projected.set((item.path || item.artifactId || item.id).toLowerCase(), item);
    }
  }
  for (const rawArtifact of runtimeArtifacts || []) {
    const item = artifactProjection(recordOf(rawArtifact), expectedSessionId, "runtime");
    if (item) projected.set((item.path || item.artifactId || item.id).toLowerCase(), item);
  }
  collectToolOutputs(sourceMessages, projected);
  const limit = Math.max(1, Math.min(160, Number(options.limit || 80) || 80));
  return Array.from(projected.values()).slice(-limit).reverse();
}
