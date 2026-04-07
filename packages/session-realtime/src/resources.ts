import type { AdminProcessRef, AdminResourceRef } from "./contract.js";

const LOOPBACK_ORIGIN_PATTERN = /^https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?/i;
const ABSOLUTE_URL_PATTERN = /^[a-z][a-z0-9+.-]*:\/\//i;
const ARTIFACT_CONTENT_PATH_PATTERN = /^\/(?:v1|api(?:\/client)?)\/artifacts\/([^/?#]+)\/content(?:[?#].*)?$/i;
const WORKSPACE_FILE_PATH_PATTERN = /^\/(?:(?:api(?:\/client)?)\/workspace\/files\/|workspace\/)(.+)$/i;

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function normalizeAdminPath(path: string) {
  const trimmed = String(path || "").trim();
  if (!trimmed) {
    return "";
  }
  const withLeadingSlash = trimmed.startsWith("/") ? trimmed : `/${trimmed}`;
  if (withLeadingSlash.startsWith("/api/client/")) {
    return withLeadingSlash;
  }
  if (withLeadingSlash.startsWith("/api/")) {
    return withLeadingSlash.replace(/^\/api\//, "/api/client/");
  }
  return withLeadingSlash;
}

function normalizeWorkspacePath(value: string) {
  return String(value || "")
    .replace(/\\/g, "/")
    .replace(/^\/+/, "")
    .replace(/^workspace\//i, "")
    .replace(/^api\/workspace\/files\//i, "")
    .replace(/^api\/client\/workspace\/files\//i, "")
    .trim();
}

function encodeWorkspacePath(workspacePath: string) {
  return normalizeWorkspacePath(workspacePath)
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

function extractWorkspacePathFromLocalPath(value: string) {
  const normalized = String(value || "").trim();
  if (!normalized) {
    return "";
  }
  const workspaceMarker = normalized.toLowerCase().lastIndexOf("\\workspace\\");
  if (workspaceMarker >= 0) {
    return normalizeWorkspacePath(normalized.slice(workspaceMarker + "\\workspace\\".length));
  }
  const unixMarker = normalized.toLowerCase().lastIndexOf("/workspace/");
  if (unixMarker >= 0) {
    return normalizeWorkspacePath(normalized.slice(unixMarker + "/workspace/".length));
  }
  return "";
}

export function buildAdminArtifactContentPath(artifactId: string) {
  const normalizedId = String(artifactId || "").trim();
  return normalizedId ? `/api/client/artifacts/${encodeURIComponent(normalizedId)}/content` : "";
}

export function buildAdminWorkspaceFilePath(workspacePath: string) {
  const encoded = encodeWorkspacePath(workspacePath);
  return encoded ? `/api/client/workspace/files/${encoded}` : "";
}

export function buildAdminArtifactContentRef(
  artifactId: string,
  extras: Omit<AdminResourceRef, "kind" | "artifactId" | "adminPath"> = {},
): AdminResourceRef | null {
  const normalizedId = String(artifactId || "").trim();
  if (!normalizedId) {
    return null;
  }
  return {
    kind: "artifact_content",
    artifactId: normalizedId,
    adminPath: buildAdminArtifactContentPath(normalizedId),
    ...extras,
  };
}

export function buildAdminWorkspaceFileRef(
  workspacePath: string,
  extras: Omit<AdminResourceRef, "kind" | "workspacePath" | "adminPath"> = {},
): AdminResourceRef | null {
  const normalizedPath = normalizeWorkspacePath(workspacePath);
  if (!normalizedPath) {
    return null;
  }
  return {
    kind: "workspace_file",
    workspacePath: normalizedPath,
    adminPath: buildAdminWorkspaceFilePath(normalizedPath),
    ...extras,
  };
}

export function buildAdminExternalUrlRef(
  url: string,
  extras: Omit<AdminResourceRef, "kind" | "url"> = {},
): AdminResourceRef | null {
  const normalizedUrl = String(url || "").trim();
  if (!normalizedUrl) {
    return null;
  }
  return {
    kind: "external_url",
    url: normalizedUrl,
    ...extras,
  };
}

function deriveAdminResourceRefFromUrl(value: string): AdminResourceRef | null {
  const raw = String(value || "").trim();
  if (!raw) {
    return null;
  }

  if (LOOPBACK_ORIGIN_PATTERN.test(raw)) {
    return deriveAdminResourceRefFromUrl(raw.replace(LOOPBACK_ORIGIN_PATTERN, ""));
  }

  const artifactMatch = raw.match(ARTIFACT_CONTENT_PATH_PATTERN);
  if (artifactMatch?.[1]) {
    return buildAdminArtifactContentRef(artifactMatch[1]);
  }

  const workspaceMatch = raw.match(WORKSPACE_FILE_PATH_PATTERN);
  if (workspaceMatch?.[1]) {
    return buildAdminWorkspaceFileRef(workspaceMatch[1]);
  }

  if (raw.startsWith("/api/client/")) {
    return {
      kind: "admin_api",
      adminPath: normalizeAdminPath(raw),
    };
  }

  if (raw.startsWith("/api/")) {
    return {
      kind: "admin_api",
      adminPath: normalizeAdminPath(raw),
    };
  }

  const localWorkspacePath = extractWorkspacePathFromLocalPath(raw);
  if (localWorkspacePath) {
    return buildAdminWorkspaceFileRef(localWorkspacePath);
  }

  if (raw.startsWith("/workspace/")) {
    return buildAdminWorkspaceFileRef(raw);
  }

  if (ABSOLUTE_URL_PATTERN.test(raw)) {
    return buildAdminExternalUrlRef(raw);
  }

  return null;
}

export function coerceAdminResourceRef(value: unknown): AdminResourceRef | null {
  if (!value) {
    return null;
  }

  if (typeof value === "string") {
    return deriveAdminResourceRefFromUrl(value);
  }

  const record = asRecord(value);
  if (typeof record.kind === "string") {
    const kind = String(record.kind).trim() as AdminResourceRef["kind"];
    const resourceRef: AdminResourceRef = {
      kind,
      adminPath: typeof record.adminPath === "string" ? normalizeAdminPath(record.adminPath) : undefined,
      signedUrl: typeof record.signedUrl === "string"
        ? record.signedUrl
        : typeof record.signed_url === "string"
          ? record.signed_url
          : undefined,
      artifactId: typeof record.artifactId === "string" ? record.artifactId : undefined,
      workspacePath: typeof record.workspacePath === "string" ? normalizeWorkspacePath(record.workspacePath) : undefined,
      workspaceRoot: typeof record.workspaceRoot === "string" ? record.workspaceRoot : undefined,
      workspaceRelativePath: typeof record.workspaceRelativePath === "string"
        ? normalizeWorkspacePath(record.workspaceRelativePath)
        : undefined,
      url: typeof record.url === "string" ? record.url : undefined,
      mimeType: typeof record.mimeType === "string" ? record.mimeType : undefined,
      displayLabel: typeof record.displayLabel === "string" ? record.displayLabel : undefined,
      displaySubtitle: typeof record.displaySubtitle === "string" ? record.displaySubtitle : undefined,
      previewable: typeof record.previewable === "boolean" ? record.previewable : undefined,
      downloadable: typeof record.downloadable === "boolean" ? record.downloadable : undefined,
      sourcePath: typeof record.sourcePath === "string" ? record.sourcePath : undefined,
      surfaceVisible: typeof record.surfaceVisible === "boolean"
        ? record.surfaceVisible
        : typeof record.surface_visible === "boolean"
          ? record.surface_visible
          : undefined,
      pathPlane: typeof record.pathPlane === "string"
        ? record.pathPlane as AdminResourceRef["pathPlane"]
        : typeof record.path_plane === "string"
          ? record.path_plane as AdminResourceRef["pathPlane"]
          : undefined,
    };
    if (!resourceRef.workspacePath && resourceRef.workspaceRelativePath) {
      resourceRef.workspacePath = resourceRef.workspaceRelativePath;
    }
    if (!resourceRef.adminPath) {
      if (kind === "artifact_content" && resourceRef.artifactId) {
        resourceRef.adminPath = buildAdminArtifactContentPath(resourceRef.artifactId);
      }
      if (kind === "workspace_file" && resourceRef.workspacePath) {
        resourceRef.adminPath = buildAdminWorkspaceFilePath(resourceRef.workspacePath);
      }
    }
    return resourceRef;
  }

  return deriveAdminResourceRefFromArtifactLike(record);
}

export function deriveAdminResourceRefFromArtifactLike(value: unknown): AdminResourceRef | null {
  const record = asRecord(value);
  const existing = coerceAdminResourceRef(record.resourceRef);
  if (existing) {
    return existing;
  }

  const artifactId =
    typeof record.artifactId === "string"
      ? record.artifactId
      : typeof record.artifact_id === "string"
        ? record.artifact_id
        : typeof record.id === "string"
          ? record.id
          : "";
  if (artifactId.trim()) {
    return buildAdminArtifactContentRef(artifactId, {
      mimeType: typeof record.mimeType === "string"
        ? record.mimeType
        : typeof record.mime_type === "string"
          ? record.mime_type
          : undefined,
      displayLabel: typeof record.displayLabel === "string"
        ? record.displayLabel
        : typeof record.title === "string"
          ? record.title
          : undefined,
      displaySubtitle: typeof record.displaySubtitle === "string" ? record.displaySubtitle : undefined,
      sourcePath: typeof record.sourcePath === "string"
        ? record.sourcePath
        : typeof record.source_path === "string"
          ? record.source_path
          : undefined,
      workspaceRoot: typeof record.workspaceRoot === "string"
        ? record.workspaceRoot
        : typeof record.workspace_root === "string"
          ? record.workspace_root
          : undefined,
      workspaceRelativePath: typeof record.workspaceRelativePath === "string"
        ? normalizeWorkspacePath(record.workspaceRelativePath)
        : typeof record.workspace_relative_path === "string"
          ? normalizeWorkspacePath(record.workspace_relative_path)
          : undefined,
      surfaceVisible: typeof record.surfaceVisible === "boolean"
        ? record.surfaceVisible
        : typeof record.surface_visible === "boolean"
          ? record.surface_visible
          : undefined,
      pathPlane: typeof record.pathPlane === "string"
        ? record.pathPlane as AdminResourceRef["pathPlane"]
        : typeof record.path_plane === "string"
          ? record.path_plane as AdminResourceRef["pathPlane"]
          : undefined,
    });
  }

  const workspacePath =
    typeof record.workspacePath === "string"
      ? record.workspacePath
      : typeof record.workspace_path === "string"
        ? record.workspace_path
        : typeof record.workspaceRelativePath === "string"
          ? record.workspaceRelativePath
          : typeof record.workspace_relative_path === "string"
            ? record.workspace_relative_path
            : typeof record.canonicalPath === "string"
              ? record.canonicalPath
              : typeof record.canonical_path === "string"
                ? record.canonical_path
                : "";
  const workspaceRef = buildAdminWorkspaceFileRef(workspacePath, {
    mimeType: typeof record.mimeType === "string"
      ? record.mimeType
      : typeof record.mime_type === "string"
        ? record.mime_type
        : undefined,
    displayLabel: typeof record.displayLabel === "string"
      ? record.displayLabel
      : typeof record.title === "string"
        ? record.title
        : undefined,
    displaySubtitle: typeof record.displaySubtitle === "string" ? record.displaySubtitle : undefined,
    sourcePath: typeof record.sourcePath === "string"
      ? record.sourcePath
      : typeof record.source_path === "string"
        ? record.source_path
        : undefined,
    workspaceRoot: typeof record.workspaceRoot === "string"
      ? record.workspaceRoot
      : typeof record.workspace_root === "string"
        ? record.workspace_root
        : undefined,
    workspaceRelativePath: typeof record.workspaceRelativePath === "string"
      ? normalizeWorkspacePath(record.workspaceRelativePath)
      : typeof record.workspace_relative_path === "string"
        ? normalizeWorkspacePath(record.workspace_relative_path)
        : undefined,
    surfaceVisible: typeof record.surfaceVisible === "boolean"
      ? record.surfaceVisible
      : typeof record.surface_visible === "boolean"
        ? record.surface_visible
        : undefined,
    pathPlane: typeof record.pathPlane === "string"
      ? record.pathPlane as AdminResourceRef["pathPlane"]
      : typeof record.path_plane === "string"
        ? record.path_plane as AdminResourceRef["pathPlane"]
        : undefined,
  });
  if (workspaceRef) {
    return workspaceRef;
  }

  const previewCandidate =
    typeof record.previewUrl === "string"
      ? record.previewUrl
      : typeof record.preview_url === "string"
        ? record.preview_url
        : typeof record.externalUrl === "string"
          ? record.externalUrl
          : typeof record.external_url === "string"
            ? record.external_url
            : typeof record.url === "string"
              ? record.url
              : "";
  return deriveAdminResourceRefFromUrl(previewCandidate);
}

function joinAdminBaseUrl(adminBaseUrl: string, adminPath: string) {
  const normalizedBase = String(adminBaseUrl || "").trim().replace(/\/+$/, "");
  const normalizedPath = normalizeAdminPath(adminPath);
  if (!normalizedBase || !normalizedPath) {
    return normalizedPath;
  }
  const publicBase = normalizedBase.endsWith("/api") ? normalizedBase.slice(0, -4) : normalizedBase;
  return `${publicBase}${normalizedPath}`;
}

export function mapAdminPathToSurface(surface: "web" | "phone", adminPath: string) {
  const normalized = normalizeAdminPath(adminPath);
  if (!normalized) {
    return "";
  }
  if (surface === "web") {
    return normalized.replace(/^\/api\/client\b/i, "/api");
  }
  return normalized;
}

export function resolveAdminResourceUrl(
  surface: "web" | "phone",
  adminBaseUrl: string | undefined,
  resource: AdminResourceRef | null | undefined,
) {
  const normalized = coerceAdminResourceRef(resource);
  if (!normalized) {
    return "";
  }
  if (normalized.signedUrl) {
    return normalized.signedUrl;
  }
  if (normalized.kind === "external_url") {
    return normalized.url || "";
  }

  const path = normalized.adminPath
    || (normalized.kind === "artifact_content" && normalized.artifactId
      ? buildAdminArtifactContentPath(normalized.artifactId)
      : normalized.kind === "workspace_file" && normalized.workspacePath
        ? buildAdminWorkspaceFilePath(normalized.workspacePath)
        : "");
  if (!path) {
    return "";
  }

  if (surface === "web") {
    return mapAdminPathToSurface("web", path);
  }

  return joinAdminBaseUrl(adminBaseUrl || "", path);
}

function coerceBoolean(value: unknown) {
  return typeof value === "boolean" ? value : undefined;
}

function coerceNumber(value: unknown) {
  const normalized = Number(value);
  return Number.isFinite(normalized) ? normalized : undefined;
}

export function coerceAdminProcessRef(value: unknown): AdminProcessRef | null {
  const record = asRecord(value);
  const processId =
    typeof record.processId === "string"
      ? record.processId.trim()
      : typeof record.process_id === "string"
        ? record.process_id.trim()
        : typeof record.commandId === "string"
          ? record.commandId.trim()
          : typeof record.command_id === "string"
            ? record.command_id.trim()
            : "";
  if (!processId) {
    return null;
  }

  const commandId =
    typeof record.commandId === "string"
      ? record.commandId.trim()
      : typeof record.command_id === "string"
        ? record.command_id.trim()
        : processId;
  const normalized: AdminProcessRef = {
    processId,
    commandId: commandId || processId,
    runId:
      typeof record.runId === "string"
        ? record.runId.trim()
        : typeof record.run_id === "string"
          ? record.run_id.trim()
          : undefined,
    title: typeof record.title === "string" ? record.title.trim() : undefined,
    commandPreview:
      typeof record.commandPreview === "string"
        ? record.commandPreview.trim()
        : typeof record.command_preview === "string"
          ? record.command_preview.trim()
          : undefined,
    status: typeof record.status === "string" ? record.status.trim() : undefined,
    interactive: coerceBoolean(record.interactive),
    usesTty: coerceBoolean(record.usesTty ?? record.uses_tty),
    canTerminate: coerceBoolean(record.canTerminate ?? record.can_terminate),
    canInput: coerceBoolean(record.canInput ?? record.can_input),
    streamAdminPath:
      typeof record.streamAdminPath === "string"
        ? normalizeAdminPath(record.streamAdminPath)
        : typeof record.stream_admin_path === "string"
          ? normalizeAdminPath(record.stream_admin_path)
          : undefined,
    inputAdminPath:
      typeof record.inputAdminPath === "string"
        ? normalizeAdminPath(record.inputAdminPath)
        : typeof record.input_admin_path === "string"
          ? normalizeAdminPath(record.input_admin_path)
          : undefined,
    terminateAdminPath:
      typeof record.terminateAdminPath === "string"
        ? normalizeAdminPath(record.terminateAdminPath)
        : typeof record.terminate_admin_path === "string"
          ? normalizeAdminPath(record.terminate_admin_path)
          : undefined,
    sourceMessageId:
      typeof record.sourceMessageId === "string"
        ? record.sourceMessageId.trim()
        : typeof record.source_message_id === "string"
          ? record.source_message_id.trim()
          : undefined,
    toolCallId:
      typeof record.toolCallId === "string"
        ? record.toolCallId.trim()
        : typeof record.tool_call_id === "string"
          ? record.tool_call_id.trim()
          : undefined,
    startedAt:
      typeof record.startedAt === "string"
        ? record.startedAt
        : typeof record.started_at === "string"
          ? record.started_at
          : undefined,
    secondsSinceOutput: coerceNumber(record.secondsSinceOutput ?? record.seconds_since_output) ?? null,
    secondsSinceInput: coerceNumber(record.secondsSinceInput ?? record.seconds_since_input) ?? null,
  };
  return normalized;
}

export function resolveAdminProcessHttpPath(
  surface: "web" | "phone",
  adminBaseUrl: string | undefined,
  process: AdminProcessRef | null | undefined,
  kind: "input" | "terminate",
) {
  const normalized = coerceAdminProcessRef(process);
  if (!normalized) {
    return "";
  }
  const targetPath = kind === "input" ? normalized.inputAdminPath : normalized.terminateAdminPath;
  if (!targetPath) {
    return "";
  }
  if (surface === "web") {
    return mapAdminPathToSurface("web", targetPath);
  }
  return joinAdminBaseUrl(adminBaseUrl || "", targetPath);
}

export function resolveAdminProcessWsUrl(
  surface: "web" | "phone",
  adminBaseUrl: string | undefined,
  process: AdminProcessRef | null | undefined,
) {
  const normalized = coerceAdminProcessRef(process);
  if (!normalized?.streamAdminPath) {
    return "";
  }
  const httpPath = surface === "web"
    ? mapAdminPathToSurface("web", normalized.streamAdminPath)
    : joinAdminBaseUrl(adminBaseUrl || "", normalized.streamAdminPath);
  if (!httpPath) {
    return "";
  }
  if (surface === "web") {
    if (ABSOLUTE_URL_PATTERN.test(httpPath)) {
      return httpPath.replace(/^http:/i, "ws:").replace(/^https:/i, "wss:");
    }
    if (typeof window !== "undefined") {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      return `${protocol}//${window.location.host}${httpPath}`;
    }
    return httpPath;
  }
  return httpPath.replace(/^http:/i, "ws:").replace(/^https:/i, "wss:");
}
