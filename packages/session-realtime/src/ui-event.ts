import type { AdminResourceRef, NormalizedSessionRuntimeEvent } from "./contract.js";
import {
  normalizeSessionRuntimeEvent,
  shouldForwardRuntimeEventToRealtimeSurface,
  type NormalizeRuntimeEventOptions,
} from "./event-normalizer.js";
import { deriveAdminResourceRefFromArtifactLike } from "./resources.js";
import type {
  SessionStreamArtifact,
  SessionStreamToolPayload,
  SessionStreamUiEvent,
} from "./message-lifecycle.js";

type JsonRecord = Record<string, unknown>;

export type SharedSessionStreamUiEvent<TArtifact = SessionStreamArtifact> =
  Omit<NormalizedSessionRuntimeEvent, "artifact">
  & Omit<SessionStreamUiEvent, "artifact">
  & { artifact?: TArtifact | null };

export type BuildSessionStreamUiEventOptions<TArtifact = SessionStreamArtifact> =
  NormalizeRuntimeEventOptions
  & {
    artifactResolver?: (artifact: unknown, event: NormalizedSessionRuntimeEvent) => TArtifact | null;
    filterRealtimeSurface?: boolean;
  };

function asRecord(value: unknown): JsonRecord {
  return value && typeof value === "object" ? (value as JsonRecord) : {};
}

function buildAgentProfile(event: NormalizedSessionRuntimeEvent) {
  const payload = asRecord(event.raw?.payload);
  const eventData = asRecord(event.data);
  const agent = asRecord(eventData.agent);
  const payloadAgent = asRecord(payload.agent);
  const name =
    typeof agent.name === "string" ? agent.name
      : typeof payloadAgent.name === "string" ? payloadAgent.name
        : typeof eventData.agentName === "string" ? eventData.agentName
          : typeof payload.agentName === "string" ? payload.agentName
            : undefined;
  const avatar =
    typeof agent.avatar === "string" ? agent.avatar
      : typeof payloadAgent.avatar === "string" ? payloadAgent.avatar
        : typeof eventData.agentAvatar === "string" ? eventData.agentAvatar
          : typeof payload.agentAvatar === "string" ? payload.agentAvatar
            : undefined;
  const roleLabel =
    typeof agent.roleLabel === "string" ? agent.roleLabel
      : typeof payloadAgent.roleLabel === "string" ? payloadAgent.roleLabel
        : typeof eventData.agentRoleLabel === "string" ? eventData.agentRoleLabel
          : typeof payload.agentRoleLabel === "string" ? payload.agentRoleLabel
            : undefined;

  if (!name && !avatar && !roleLabel) {
    return undefined;
  }

  return {
    id: typeof agent.id === "string"
      ? agent.id
      : typeof payloadAgent.id === "string"
        ? payloadAgent.id
        : undefined,
    name,
    avatar,
    roleLabel,
  };
}

function buildToolPayload(event: NormalizedSessionRuntimeEvent): SessionStreamToolPayload | undefined {
  const payload = asRecord(event.raw?.payload);
  const eventData = asRecord(event.data);
  const nestedTool = asRecord(eventData.tool);
  const payloadTool = asRecord(payload.tool);
  const mcpApp = event.tool?.mcpApp
    || (eventData.mcpApp && typeof eventData.mcpApp === "object" ? eventData.mcpApp as SessionStreamToolPayload["mcpApp"] : undefined)
    || (nestedTool.mcpApp && typeof nestedTool.mcpApp === "object" ? nestedTool.mcpApp as SessionStreamToolPayload["mcpApp"] : undefined)
    || (payloadTool.mcpApp && typeof payloadTool.mcpApp === "object" ? payloadTool.mcpApp as SessionStreamToolPayload["mcpApp"] : undefined);
  const toolCallId =
    typeof eventData.toolCallId === "string" ? eventData.toolCallId
      : typeof eventData.tool_call_id === "string" ? eventData.tool_call_id
        : typeof nestedTool.toolCallId === "string" ? nestedTool.toolCallId
          : typeof payloadTool.toolCallId === "string" ? payloadTool.toolCallId
            : undefined;
  const toolInvocationId =
    typeof eventData.toolInvocationId === "string" ? eventData.toolInvocationId
      : typeof eventData.tool_invocation_id === "string" ? eventData.tool_invocation_id
        : typeof nestedTool.toolInvocationId === "string" ? nestedTool.toolInvocationId
          : typeof payloadTool.toolInvocationId === "string" ? payloadTool.toolInvocationId
            : toolCallId;
  const toolName =
    typeof eventData.toolName === "string" ? eventData.toolName
      : typeof eventData.tool_name === "string" ? eventData.tool_name
        : typeof nestedTool.toolName === "string" ? nestedTool.toolName
          : typeof payloadTool.toolName === "string" ? payloadTool.toolName
            : undefined;
  const args = eventData.args ?? eventData.request ?? nestedTool.args ?? payloadTool.args;
  const result = eventData.result ?? eventData.response ?? eventData.result_preview ?? nestedTool.result ?? payloadTool.result;
  const agentVisibleResult = eventData.agentVisibleResult
    ?? eventData.agent_visible_result
    ?? nestedTool.agentVisibleResult
    ?? nestedTool.agent_visible_result
    ?? payloadTool.agentVisibleResult
    ?? payloadTool.agent_visible_result;
  const agentVisibleChars = typeof eventData.agentVisibleChars === "number"
    ? eventData.agentVisibleChars
    : typeof nestedTool.agentVisibleChars === "number"
      ? nestedTool.agentVisibleChars
      : typeof payloadTool.agentVisibleChars === "number"
        ? payloadTool.agentVisibleChars
        : undefined;

  if (!toolCallId && !toolInvocationId && !toolName && args === undefined && result === undefined && agentVisibleResult === undefined) {
    return undefined;
  }

  return {
    toolCallId: toolCallId || toolInvocationId,
    toolInvocationId,
    toolName,
    args,
    result,
    agentVisibleResult,
    agentVisibleChars,
    mcpApp,
  };
}

function buildDefaultArtifact(value: unknown): SessionStreamArtifact | null {
  const record = asRecord(value);
  const artifact: SessionStreamArtifact = {
    id: typeof record.id === "string" ? record.id : undefined,
    artifactId: typeof record.artifactId === "string"
      ? record.artifactId
      : typeof record.artifact_id === "string"
        ? record.artifact_id
        : undefined,
    title: typeof record.title === "string" ? record.title : undefined,
    displayLabel: typeof record.displayLabel === "string"
      ? record.displayLabel
      : typeof record.display_label === "string"
        ? record.display_label
        : undefined,
    displaySubtitle: typeof record.displaySubtitle === "string"
      ? record.displaySubtitle
      : typeof record.display_subtitle === "string"
        ? record.display_subtitle
        : undefined,
    kind: typeof record.kind === "string" ? record.kind : undefined,
    previewUrl: typeof record.previewUrl === "string"
      ? record.previewUrl
      : typeof record.preview_url === "string"
        ? record.preview_url
        : undefined,
    externalUrl: typeof record.externalUrl === "string"
      ? record.externalUrl
      : typeof record.external_url === "string"
        ? record.external_url
        : undefined,
    sourcePath: typeof record.sourcePath === "string"
      ? record.sourcePath
      : typeof record.source_path === "string"
        ? record.source_path
        : undefined,
    workspacePath: typeof record.workspacePath === "string"
      ? record.workspacePath
      : typeof record.workspace_path === "string"
        ? record.workspace_path
        : undefined,
    mimeType: typeof record.mimeType === "string"
      ? record.mimeType
      : typeof record.mime_type === "string"
        ? record.mime_type
        : undefined,
    resourceRef: deriveAdminResourceRefFromArtifactLike(record) as AdminResourceRef | null,
    ...record,
  };

  if (
    !artifact.id
    && !artifact.artifactId
    && !artifact.title
    && !artifact.previewUrl
    && !artifact.externalUrl
    && !artifact.resourceRef
    && !artifact.sourcePath
    && !artifact.workspacePath
  ) {
    return null;
  }

  return artifact;
}

export function buildSessionStreamUiEvent<TArtifact = SessionStreamArtifact>(
  raw: unknown,
  options: BuildSessionStreamUiEventOptions<TArtifact> = {},
): SharedSessionStreamUiEvent<TArtifact> | null {
  const normalized = normalizeSessionRuntimeEvent(raw, options);
  if (!normalized) {
    return null;
  }
  if (options.filterRealtimeSurface !== false && !shouldForwardRuntimeEventToRealtimeSurface(normalized)) {
    return null;
  }

  const artifactValue = normalized.artifact || asRecord(normalized.data).artifact || normalized.data;
  const artifact = options.artifactResolver
    ? options.artifactResolver(artifactValue, normalized)
    : (buildDefaultArtifact(artifactValue) as unknown as TArtifact | null);

  return {
    ...normalized,
    agent: buildAgentProfile(normalized),
    tool: buildToolPayload(normalized),
    artifact,
  };
}
