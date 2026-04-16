import type { AdminProcessRef, AdminResourceRef } from "./contract.js";

const LOOPBACK_ORIGIN_PATTERN = /^https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?/i;
const ABSOLUTE_URL_PATTERN = /^[a-z][a-z0-9+.-]*:\/\//i;
const ARTIFACT_CONTENT_PATH_PATTERN = /^\/(?:v1|api(?:\/client)?)\/artifacts\/([^/?#]+)\/content(?:[?#].*)?$/i;
const WORKSPACE_FILE_PATH_PATTERN = /^\/(?:(?:api(?:\/client)?)\/workspace\/files\/|workspace\/)(.+)$/i;
const WORKSPACE_RESOURCE_PATHNAME_PATTERN = /^\/(?:(?:api(?:\/client)?)\/workspace\/resource|v1\/workspace\/resource)$/i;
const WORKSPACE_SURFACE_PLANES = new Set(["workspace_download", "workspace_artifact"]);

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

function normalizeScopedWorkspaceRelativePath(value: string) {
  const raw = String(value || "").trim();
  if (!raw || /^[a-z]:[\\/]/i.test(raw) || raw.startsWith("/") || raw.startsWith("\\")) {
    return "";
  }
  const normalized = normalizeWorkspacePath(raw);
  if (!normalized || normalized === "." || normalized === "..") {
    return "";
  }
  const segments = normalized.split("/").filter(Boolean);
  if (!segments.length || segments.some((segment) => segment === "..")) {
    return "";
  }
  return segments.join("/");
}

function normalizeScopedIdentifier(value: unknown) {
  const normalized = String(value || "").trim();
  return normalized || undefined;
}

function normalizePathPlane(value: unknown) {
  const normalized = String(value || "").trim();
  if (!WORKSPACE_SURFACE_PLANES.has(normalized)) {
    return undefined;
  }
  return normalized as Extract<AdminResourceRef["pathPlane"], "workspace_download" | "workspace_artifact">;
}

function encodeWorkspacePath(workspacePath: string) {
  return normalizeWorkspacePath(workspacePath)
    .split("/")
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

export function buildAdminArtifactContentPath(artifactId: string) {
  const normalizedId = String(artifactId || "").trim();
  return normalizedId ? `/api/client/artifacts/${encodeURIComponent(normalizedId)}/content` : "";
}

export function buildAdminWorkspaceFilePath(workspacePath: string) {
  const encoded = encodeWorkspacePath(workspacePath);
  return encoded ? `/api/client/workspace/files/${encoded}` : "";
}

export function buildAdminScopedWorkspaceResourcePath(options: {
  workspaceRelativePath: string;
  pathPlane: Extract<AdminResourceRef["pathPlane"], "workspace_download" | "workspace_artifact">;
  workspaceId?: string;
  projectId?: string;
}) {
  const workspaceRelativePath = normalizeScopedWorkspaceRelativePath(options.workspaceRelativePath);
  const pathPlane = normalizePathPlane(options.pathPlane);
  if (!workspaceRelativePath || !pathPlane) {
    return "";
  }
  const params = new URLSearchParams();
  params.set("workspace_relative_path", workspaceRelativePath);
  params.set("path_plane", pathPlane);
  const workspaceId = normalizeScopedIdentifier(options.workspaceId);
  const projectId = normalizeScopedIdentifier(options.projectId);
  if (workspaceId) {
    params.set("workspace_id", workspaceId);
  }
  if (projectId) {
    params.set("project_id", projectId);
  }
  return `/api/client/workspace/resource?${params.toString()}`;
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
  extras: Omit<AdminResourceRef, "kind" | "workspacePath"> = {},
): AdminResourceRef | null {
  const normalizedPath = normalizeWorkspacePath(workspacePath);
  if (!normalizedPath) {
    return null;
  }
  const workspaceRelativePath = normalizeScopedWorkspaceRelativePath(
    typeof extras.workspaceRelativePath === "string" ? extras.workspaceRelativePath : normalizedPath,
  );
  const pathPlane = normalizePathPlane(extras.pathPlane);
  const workspaceId = normalizeScopedIdentifier(extras.workspaceId);
  const projectId = normalizeScopedIdentifier(extras.projectId);
  return {
    kind: "workspace_file",
    workspacePath: normalizedPath,
    workspaceRelativePath: workspaceRelativePath || extras.workspaceRelativePath,
    workspaceId,
    projectId,
    adminPath: workspaceRelativePath && pathPlane
      ? buildAdminScopedWorkspaceResourcePath({
        workspaceRelativePath,
        pathPlane,
        workspaceId,
        projectId,
      })
      : buildAdminWorkspaceFilePath(normalizedPath),
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

  try {
    const parsed = new URL(raw.startsWith("/") ? raw : `/${raw}`, "https://v8.invalid");
    if (WORKSPACE_RESOURCE_PATHNAME_PATTERN.test(parsed.pathname)) {
      const canonicalSearch = new URLSearchParams(parsed.searchParams);
      canonicalSearch.delete("v8exp");
      canonicalSearch.delete("v8sig");
      const workspaceRelativePath = normalizeScopedWorkspaceRelativePath(
        canonicalSearch.get("workspace_relative_path") || canonicalSearch.get("workspaceRelativePath") || "",
      );
      const pathPlane = normalizePathPlane(
        canonicalSearch.get("path_plane") || canonicalSearch.get("pathPlane") || "",
      );
      if (workspaceRelativePath && pathPlane) {
        return buildAdminWorkspaceFileRef(workspaceRelativePath, {
          workspaceRelativePath,
          workspaceId: normalizeScopedIdentifier(
            canonicalSearch.get("workspace_id") || canonicalSearch.get("workspaceId"),
          ),
          projectId: normalizeScopedIdentifier(
            canonicalSearch.get("project_id") || canonicalSearch.get("projectId"),
          ),
          pathPlane,
          adminPath: normalizeAdminPath(`${parsed.pathname}${canonicalSearch.toString() ? `?${canonicalSearch.toString()}` : ""}`),
        });
      }
    }
  } catch {
    // no-op: fall through to other path heuristics
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

  if (raw.startsWith("/workspace/")) {
    return buildAdminWorkspaceFileRef(raw);
  }

  if (ABSOLUTE_URL_PATTERN.test(raw)) {
    return buildAdminExternalUrlRef(raw);
  }

  return null;
}

function extractScopedWorkspaceSurfaceFields(record: Record<string, unknown>) {
  const workspaceRelativePath = normalizeScopedWorkspaceRelativePath(
    typeof record.workspaceRelativePath === "string"
      ? record.workspaceRelativePath
      : typeof record.workspace_relative_path === "string"
        ? record.workspace_relative_path
        : typeof record.workspacePath === "string"
          ? record.workspacePath
          : typeof record.workspace_path === "string"
            ? record.workspace_path
            : "",
  );
  const pathPlane = normalizePathPlane(record.pathPlane ?? record.path_plane);
  const workspaceRoot = typeof record.workspaceRoot === "string"
    ? record.workspaceRoot
    : typeof record.workspace_root === "string"
      ? record.workspace_root
      : undefined;
  const workspaceId = normalizeScopedIdentifier(record.workspaceId ?? record.workspace_id);
  const projectId = normalizeScopedIdentifier(record.projectId ?? record.project_id);
  if (!workspaceRelativePath || !pathPlane) {
    return null;
  }
  if (!workspaceRoot && !workspaceId && !projectId) {
    return null;
  }
  return {
    workspaceRelativePath,
    pathPlane,
    workspaceRoot,
    workspaceId,
    projectId,
  };
}

function deriveScopedWorkspaceFileRef(
  record: Record<string, unknown>,
  extras: Omit<AdminResourceRef, "kind" | "workspacePath" | "adminPath"> = {},
) {
  const scoped = extractScopedWorkspaceSurfaceFields(record);
  if (!scoped) {
    return null;
  }
  return buildAdminWorkspaceFileRef(scoped.workspaceRelativePath, {
    ...extras,
    workspaceRelativePath: scoped.workspaceRelativePath,
    workspaceRoot: scoped.workspaceRoot,
    workspaceId: scoped.workspaceId,
    projectId: scoped.projectId,
    pathPlane: scoped.pathPlane,
  });
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
      artifactId: typeof record.artifactId === "string"
        ? record.artifactId
        : typeof record.artifact_id === "string"
          ? record.artifact_id
          : undefined,
      workspaceId: normalizeScopedIdentifier(record.workspaceId ?? record.workspace_id),
      projectId: normalizeScopedIdentifier(record.projectId ?? record.project_id),
      workspacePath: typeof record.workspacePath === "string"
        ? normalizeWorkspacePath(record.workspacePath)
        : typeof record.workspace_path === "string"
          ? normalizeWorkspacePath(record.workspace_path)
          : undefined,
      workspaceRoot: typeof record.workspaceRoot === "string"
        ? record.workspaceRoot
        : typeof record.workspace_root === "string"
          ? record.workspace_root
          : undefined,
      workspaceRelativePath: typeof record.workspaceRelativePath === "string"
        ? normalizeScopedWorkspaceRelativePath(record.workspaceRelativePath)
        : typeof record.workspace_relative_path === "string"
          ? normalizeScopedWorkspaceRelativePath(record.workspace_relative_path)
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
      pathPlane: normalizePathPlane(record.pathPlane ?? record.path_plane)
        || (typeof record.pathPlane === "string"
          ? record.pathPlane as AdminResourceRef["pathPlane"]
          : typeof record.path_plane === "string"
            ? record.path_plane as AdminResourceRef["pathPlane"]
            : undefined),
    };
    if (!resourceRef.workspacePath && resourceRef.workspaceRelativePath) {
      resourceRef.workspacePath = resourceRef.workspaceRelativePath;
    }
    if (!resourceRef.adminPath) {
      if (
        kind === "workspace_file"
        && resourceRef.workspaceRelativePath
        && normalizePathPlane(resourceRef.pathPlane)
      ) {
        resourceRef.adminPath = buildAdminScopedWorkspaceResourcePath({
          workspaceRelativePath: resourceRef.workspaceRelativePath,
          pathPlane: normalizePathPlane(resourceRef.pathPlane)!,
          workspaceId: resourceRef.workspaceId,
          projectId: resourceRef.projectId,
        });
      }
      if (kind === "artifact_content" && resourceRef.artifactId) {
        resourceRef.adminPath = buildAdminArtifactContentPath(resourceRef.artifactId);
      }
      if (kind === "workspace_file" && resourceRef.workspacePath) {
        resourceRef.adminPath = resourceRef.adminPath || buildAdminWorkspaceFilePath(resourceRef.workspacePath);
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

  const scopedWorkspaceRef = deriveScopedWorkspaceFileRef(record, {
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
    displaySubtitle: typeof record.displaySubtitle === "string"
      ? record.displaySubtitle
      : typeof record.display_subtitle === "string"
        ? record.display_subtitle
        : undefined,
    sourcePath: typeof record.sourcePath === "string"
      ? record.sourcePath
      : typeof record.source_path === "string"
        ? record.source_path
        : undefined,
    surfaceVisible: typeof record.surfaceVisible === "boolean"
      ? record.surfaceVisible
      : typeof record.surface_visible === "boolean"
        ? record.surface_visible
        : undefined,
  });
  if (scopedWorkspaceRef) {
    return scopedWorkspaceRef;
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
      workspaceId: normalizeScopedIdentifier(record.workspaceId ?? record.workspace_id),
      projectId: normalizeScopedIdentifier(record.projectId ?? record.project_id),
      workspaceRoot: typeof record.workspaceRoot === "string"
        ? record.workspaceRoot
        : typeof record.workspace_root === "string"
          ? record.workspace_root
          : undefined,
      workspaceRelativePath: typeof record.workspaceRelativePath === "string"
        ? normalizeScopedWorkspaceRelativePath(record.workspaceRelativePath)
        : typeof record.workspace_relative_path === "string"
          ? normalizeScopedWorkspaceRelativePath(record.workspace_relative_path)
          : undefined,
      surfaceVisible: typeof record.surfaceVisible === "boolean"
        ? record.surfaceVisible
        : typeof record.surface_visible === "boolean"
          ? record.surface_visible
          : undefined,
      pathPlane: normalizePathPlane(record.pathPlane ?? record.path_plane)
        || (typeof record.pathPlane === "string"
          ? record.pathPlane as AdminResourceRef["pathPlane"]
          : typeof record.path_plane === "string"
            ? record.path_plane as AdminResourceRef["pathPlane"]
            : undefined),
    });
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
      : normalized.kind === "workspace_file" && normalized.workspaceRelativePath && normalizePathPlane(normalized.pathPlane)
        ? buildAdminScopedWorkspaceResourcePath({
          workspaceRelativePath: normalized.workspaceRelativePath,
          pathPlane: normalizePathPlane(normalized.pathPlane)!,
          workspaceId: normalized.workspaceId,
          projectId: normalized.projectId,
        })
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
    sessionId:
      typeof record.sessionId === "string"
        ? record.sessionId.trim()
        : typeof record.session_id === "string"
          ? record.session_id.trim()
          : undefined,
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
    outputAdminPath:
      typeof record.outputAdminPath === "string"
        ? normalizeAdminPath(record.outputAdminPath)
        : typeof record.output_admin_path === "string"
          ? normalizeAdminPath(record.output_admin_path)
          : undefined,
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
    completedAt:
      typeof record.completedAt === "string"
        ? record.completedAt
        : typeof record.completed_at === "string"
          ? record.completed_at
          : null,
    secondsSinceOutput: coerceNumber(record.secondsSinceOutput ?? record.seconds_since_output) ?? null,
    secondsSinceInput: coerceNumber(record.secondsSinceInput ?? record.seconds_since_input) ?? null,
    ttyMode:
      typeof record.ttyMode === "string"
        ? record.ttyMode.trim()
        : typeof record.tty_mode === "string"
          ? record.tty_mode.trim()
          : undefined,
    screenMode:
      typeof record.screenMode === "string"
        ? record.screenMode.trim()
        : typeof record.screen_mode === "string"
          ? record.screen_mode.trim()
          : undefined,
    screenSnapshot:
      typeof record.screenSnapshot === "string"
        ? record.screenSnapshot
        : typeof record.screen_snapshot === "string"
          ? record.screen_snapshot
          : undefined,
    stableScreenSnapshot:
      typeof record.stableScreenSnapshot === "string"
        ? record.stableScreenSnapshot
        : typeof record.stable_screen_snapshot === "string"
          ? record.stable_screen_snapshot
          : undefined,
    screenVersion: coerceNumber(record.screenVersion ?? record.screen_version) ?? null,
    rawFrameVersion: coerceNumber(record.rawFrameVersion ?? record.raw_frame_version) ?? null,
    rawBytes: coerceNumber(record.rawBytes ?? record.raw_bytes) ?? null,
    cursor:
      record.cursor && typeof record.cursor === "object"
        ? {
            row: coerceNumber((record.cursor as Record<string, unknown>).row) ?? undefined,
            col: coerceNumber((record.cursor as Record<string, unknown>).col) ?? undefined,
          }
        : null,
    cols: coerceNumber(record.cols) ?? null,
    rows: coerceNumber(record.rows) ?? null,
    alternateScreen: coerceBoolean(record.alternateScreen ?? record.alternate_screen),
    awaitingInput: coerceBoolean(record.awaitingInput ?? record.awaiting_input),
    observationState:
      typeof record.observationState === "string"
        ? record.observationState.trim()
        : typeof record.observation_state === "string"
          ? record.observation_state.trim()
          : undefined,
    textEncoding:
      typeof record.textEncoding === "string"
        ? record.textEncoding.trim()
        : typeof record.text_encoding === "string"
          ? record.text_encoding.trim()
          : null,
    encodingState:
      typeof record.encodingState === "string"
        ? record.encodingState.trim()
        : typeof record.encoding_state === "string"
          ? record.encoding_state.trim()
          : undefined,
    encodingNotes:
      typeof record.encodingNotes === "string"
        ? record.encodingNotes
        : typeof record.encoding_notes === "string"
          ? record.encoding_notes
          : null,
    lastScreenAt:
      typeof record.lastScreenAt === "string"
        ? record.lastScreenAt
        : typeof record.last_screen_at === "string"
          ? record.last_screen_at
          : null,
    lastRawFrameAt:
      typeof record.lastRawFrameAt === "string"
        ? record.lastRawFrameAt
        : typeof record.last_raw_frame_at === "string"
          ? record.last_raw_frame_at
          : null,
    lastRawFramePreview:
      typeof record.lastRawFramePreview === "string"
        ? record.lastRawFramePreview
        : typeof record.last_raw_frame_preview === "string"
          ? record.last_raw_frame_preview
          : null,
    commandDiagnostics:
      record.commandDiagnostics && typeof record.commandDiagnostics === "object"
        ? (record.commandDiagnostics as Record<string, unknown>)
        : record.command_diagnostics && typeof record.command_diagnostics === "object"
          ? (record.command_diagnostics as Record<string, unknown>)
          : null,
  };
  return normalized;
}

export function resolveAdminProcessHttpPath(
  surface: "web" | "phone",
  adminBaseUrl: string | undefined,
  process: AdminProcessRef | null | undefined,
  kind: "input" | "output" | "terminate",
) {
  const normalized = coerceAdminProcessRef(process);
  if (!normalized) {
    return "";
  }
  const targetPath = kind === "input"
    ? normalized.inputAdminPath
    : kind === "output"
      ? normalized.outputAdminPath
      : normalized.terminateAdminPath;
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
