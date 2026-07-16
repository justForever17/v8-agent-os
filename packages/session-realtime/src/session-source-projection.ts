import type { SessionSourceRef } from "./contract.js";

export type SessionSourceProjection = {
  id: string;
  messageId: string | null;
  name: string;
  sourceKind: string;
  mimeType: string | null;
  mediaKind: "image" | "video" | "audio" | "file";
  url: string | null;
  previewUrl: string | null;
  workspacePath: string | null;
  resourceRef: Record<string, unknown> | null;
  createdAt: string | null;
};

type SourceMessage = {
  id?: string | null;
  role?: string | null;
  metadata?: unknown;
};

function recordOf(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function decoded(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function fileName(value: string): string {
  const clean = decoded(value).split(/[?#]/, 1)[0].replace(/\\/g, "/");
  const parts = clean.split("/").filter(Boolean);
  return parts[parts.length - 1] || value;
}

function inferMime(name: string, explicit: string): string | null {
  if (explicit) return explicit.split(";", 1)[0].trim().toLowerCase();
  const lower = decoded(name).toLowerCase().split(/[?#]/, 1)[0];
  if (/\.(?:mp3|mpeg)$/.test(lower)) return "audio/mpeg";
  if (/\.(?:m4a|mp4a)$/.test(lower)) return "audio/mp4";
  if (/\.wav$/.test(lower)) return "audio/wav";
  if (/\.ogg$/.test(lower)) return "audio/ogg";
  const extension = lower.split(".").pop() || "";
  if (/\.(?:png|jpe?g|webp|gif|avif)$/.test(lower)) return `image/${lower.endsWith(".jpg") || lower.endsWith(".jpeg") ? "jpeg" : extension}`;
  if (/\.(?:mp4|webm|mov)$/.test(lower)) return `video/${extension === "mov" ? "quicktime" : extension}`;
  return null;
}

function mediaKind(mimeType: string | null): SessionSourceProjection["mediaKind"] {
  if (mimeType?.startsWith("image/")) return "image";
  if (mimeType?.startsWith("video/")) return "video";
  if (mimeType?.startsWith("audio/")) return "audio";
  return "file";
}

function normalizeSource(raw: unknown, fallbackMessageId: string | null = null): SessionSourceProjection | null {
  const item = recordOf(raw);
  const metadata = recordOf(item.metadata);
  const resourceRef = recordOf(item.resourceRef || item.resource_ref);
  const id = text(item.sourceId || item.source_id || item.id);
  const url = text(item.externalUrl || item.external_url || item.publicUrl || item.public_url || item.url || resourceRef.adminPath) || null;
  const previewUrl = text(item.previewUrl || item.preview_url || item.publicUrl || item.public_url || item.url || resourceRef.adminPath) || url;
  const workspacePath = text(item.workspacePath || item.workspace_path || item.workspaceRelativePath || item.workspace_relative_path || item.path) || null;
  const name = text(item.title || item.name || item.displayLabel || resourceRef.displayLabel)
    || fileName(workspacePath || url || id || "来源");
  const mimeType = inferMime(name || url || workspacePath || "", text(item.mimeType || item.mime_type || item.type));
  const stableId = id || text(url) || text(workspacePath);
  if (!stableId) return null;
  return {
    id: stableId,
    messageId: text(item.messageId || item.message_id) || fallbackMessageId,
    name,
    sourceKind: text(item.sourceKind || item.source_kind || item.source || metadata.source) || "upload",
    mimeType,
    mediaKind: mediaKind(mimeType),
    url,
    previewUrl,
    workspacePath,
    resourceRef: Object.keys(resourceRef).length ? resourceRef : null,
    createdAt: text(item.createdAt || item.created_at) || null,
  };
}

export function buildSessionSourceProjection(
  messages: SourceMessage[] | null | undefined,
  sessionSources: SessionSourceRef[] | unknown[] | null | undefined = [],
): SessionSourceProjection[] {
  const output: SessionSourceProjection[] = [];
  const seen = new Set<string>();
  const append = (candidate: SessionSourceProjection | null) => {
    if (!candidate) return;
    const key = candidate.id || candidate.url || candidate.workspacePath;
    if (!key || seen.has(key)) return;
    seen.add(key);
    output.push(candidate);
  };

  for (const raw of sessionSources || []) append(normalizeSource(raw));
  for (const message of messages || []) {
    if (text(message.role).toLowerCase() !== "user") continue;
    const metadata = recordOf(message.metadata);
    const attachments = Array.isArray(metadata.attachments) ? metadata.attachments : [];
    for (const attachment of attachments) append(normalizeSource(attachment, text(message.id) || null));
  }
  return output;
}
