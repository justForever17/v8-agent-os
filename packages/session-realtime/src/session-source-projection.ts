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
  workspaceRelativePath: string | null;
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

function normalizedPath(value: unknown): string {
  const normalized = decoded(text(value)).split(/[?#]/, 1)[0].replace(/\\/g, "/").replace(/\/{2,}/g, "/").replace(/^\.\//, "");
  return /^[a-z]:\//i.test(normalized) ? normalized.toLowerCase() : normalized;
}

function relativePathFromUrl(value: unknown): string {
  const raw = text(value);
  if (!raw) return "";
  try {
    const url = new URL(raw, "http://v8os.local");
    return text(url.searchParams.get("workspace_relative_path") || url.searchParams.get("workspaceRelativePath"));
  } catch {
    return "";
  }
}

function normalizedResourceUrl(value: unknown): string {
  const raw = text(value);
  if (!raw) return "";
  try {
    const url = new URL(raw, "http://v8os.local");
    const entries = [...url.searchParams.entries()].sort(([leftKey, leftValue], [rightKey, rightValue]) => (
      leftKey.localeCompare(rightKey) || leftValue.localeCompare(rightValue)
    ));
    const query = new URLSearchParams(entries).toString();
    const origin = url.origin === "http://v8os.local" ? "" : url.origin.toLowerCase();
    return `${origin}${decoded(url.pathname).replace(/\\/g, "/")}${query ? `?${query}` : ""}`;
  } catch {
    return decoded(raw).replace(/\\/g, "/");
  }
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
  const resourceMetadata = recordOf(resourceRef.metadata);
  const id = text(item.sourceId || item.source_id || item.id);
  const url = text(item.externalUrl || item.external_url || item.publicUrl || item.public_url || item.url || resourceRef.adminPath) || null;
  const previewUrl = text(item.previewUrl || item.preview_url || item.publicUrl || item.public_url || item.url || resourceRef.adminPath) || url;
  const workspaceRelativePath = text(
    item.workspaceRelativePath
    || item.workspace_relative_path
    || resourceRef.workspaceRelativePath
    || resourceRef.workspace_relative_path
    || metadata.workspaceRelativePath
    || metadata.workspace_relative_path
    || resourceMetadata.workspaceRelativePath
    || resourceMetadata.workspace_relative_path
    || relativePathFromUrl(url),
  ) || null;
  const workspacePath = text(item.workspacePath || item.workspace_path || item.path || workspaceRelativePath) || null;
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
    workspaceRelativePath,
    resourceRef: Object.keys(resourceRef).length ? resourceRef : null,
    createdAt: text(item.createdAt || item.created_at) || null,
  };
}

function sourceFingerprints(candidate: SessionSourceProjection): string[] {
  return [
    candidate.workspaceRelativePath ? `workspace-relative:${normalizedPath(candidate.workspaceRelativePath)}` : "",
    candidate.url ? `url:${normalizedResourceUrl(candidate.url)}` : "",
    candidate.previewUrl ? `preview:${normalizedResourceUrl(candidate.previewUrl)}` : "",
    candidate.workspacePath ? `workspace:${normalizedPath(candidate.workspacePath)}` : "",
    candidate.id ? `id:${candidate.id}` : "",
  ].filter(Boolean);
}

function mergeSource(current: SessionSourceProjection, candidate: SessionSourceProjection): SessionSourceProjection {
  return {
    ...candidate,
    ...current,
    messageId: current.messageId || candidate.messageId,
    name: current.name || candidate.name,
    sourceKind: current.sourceKind || candidate.sourceKind,
    mimeType: current.mimeType || candidate.mimeType,
    mediaKind: current.mimeType ? current.mediaKind : candidate.mediaKind,
    url: current.url || candidate.url,
    previewUrl: current.previewUrl || candidate.previewUrl,
    workspacePath: current.workspacePath || candidate.workspacePath,
    workspaceRelativePath: current.workspaceRelativePath || candidate.workspaceRelativePath,
    resourceRef: current.resourceRef || candidate.resourceRef,
    createdAt: current.createdAt || candidate.createdAt,
  };
}

export function buildSessionSourceProjection(
  messages: SourceMessage[] | null | undefined,
  sessionSources: SessionSourceRef[] | unknown[] | null | undefined = [],
): SessionSourceProjection[] {
  const output: SessionSourceProjection[] = [];
  const fingerprintToIndex = new Map<string, number>();
  const append = (candidate: SessionSourceProjection | null) => {
    if (!candidate) return;
    const fingerprints = sourceFingerprints(candidate);
    const existingIndex = fingerprints
      .map((fingerprint) => fingerprintToIndex.get(fingerprint))
      .find((index): index is number => typeof index === "number");
    if (typeof existingIndex === "number") {
      output[existingIndex] = mergeSource(output[existingIndex], candidate);
      for (const fingerprint of sourceFingerprints(output[existingIndex])) fingerprintToIndex.set(fingerprint, existingIndex);
      return;
    }
    const nextIndex = output.length;
    output.push(candidate);
    for (const fingerprint of fingerprints) fingerprintToIndex.set(fingerprint, nextIndex);
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
