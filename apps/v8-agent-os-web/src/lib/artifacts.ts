import type { AdminResourceRef } from "@v8/session-realtime";
import {
    deriveAdminResourceRefFromArtifactLike,
    resolveAdminResourceUrl,
} from "@v8/session-realtime";

export interface RuntimeArtifact {
    id: string;
    artifactId: string;
    kind: string;
    mimeType: string;
    title: string;
    displayLabel: string;
    displaySubtitle: string;
    sessionId?: string;
    runId?: string;
    messageId?: string;
    sourcePath?: string;
    workspacePath?: string;
    workspaceRoot?: string;
    workspaceRelativePath?: string;
    canonicalPath?: string;
    projectId?: string;
    workspaceId?: string;
    pathPlane?: "runtime_private" | "workspace_download" | "workspace_artifact";
    storageClass?: string;
    surfaceVisible?: boolean;
    externalUrl?: string;
    previewUrl?: string;
    resourceRef?: AdminResourceRef | null;
    hasPreview?: boolean;
    supportsInlinePreview?: boolean;
    previewKind?: string;
    sourceComponent?: string;
    metadata?: Record<string, unknown>;
    createdAt?: string;
}

type ArtifactRecord = Record<string, unknown>;

function resolveRuntimeArtifactResource(record: ArtifactRecord) {
    return deriveAdminResourceRefFromArtifactLike(record);
}

export function resolveRuntimeArtifactUrl(artifact: Pick<RuntimeArtifact, "resourceRef" | "previewUrl" | "externalUrl">) {
    const resolved = resolveAdminResourceUrl("web", undefined, artifact.resourceRef || null);
    if (resolved) {
        return resolved;
    }
    return String(artifact.previewUrl || artifact.externalUrl || "").trim() || undefined;
}

export function normalizeRuntimeArtifact(raw: unknown): RuntimeArtifact | null {
    if (!raw || typeof raw !== "object") {
        return null;
    }

    const record = raw as ArtifactRecord;
    const artifactId = String(record.artifactId || record.id || "");
    if (!artifactId) {
        return null;
    }

    const resourceRef = resolveRuntimeArtifactResource(record);
    const resolvedUrl = resolveAdminResourceUrl("web", undefined, resourceRef);
    const title = String(record.displayLabel || record.title || artifactId);
    const displaySubtitle = String(
        record.displaySubtitle
            || record.canonicalPath
            || record.canonical_path
            || record.workspaceRelativePath
            || record.workspace_relative_path
            || record.workspacePath
            || record.workspace_path
            || resolvedUrl
            || "暂无路径信息",
    );

    return {
        id: artifactId,
        artifactId,
        kind: String(record.kind || record.artifact_kind || "file"),
        mimeType: String(record.mimeType || record.mime_type || "application/octet-stream"),
        title,
        displayLabel: title,
        displaySubtitle,
        sessionId: typeof (record.sessionId || record.session_id) === "string" ? String(record.sessionId || record.session_id) : undefined,
        runId: typeof (record.runId || record.run_id) === "string" ? String(record.runId || record.run_id) : undefined,
        messageId: typeof (record.messageId || record.message_id) === "string" ? String(record.messageId || record.message_id) : undefined,
        sourcePath: typeof (record.sourcePath || record.source_path) === "string" ? String(record.sourcePath || record.source_path) : undefined,
        workspacePath: typeof (record.workspacePath || record.workspace_path) === "string" ? String(record.workspacePath || record.workspace_path) : undefined,
        workspaceRoot: typeof (record.workspaceRoot || record.workspace_root) === "string" ? String(record.workspaceRoot || record.workspace_root) : undefined,
        workspaceRelativePath: typeof (record.workspaceRelativePath || record.workspace_relative_path) === "string" ? String(record.workspaceRelativePath || record.workspace_relative_path) : undefined,
        canonicalPath: typeof (record.canonicalPath || record.canonical_path) === "string" ? String(record.canonicalPath || record.canonical_path) : undefined,
        projectId: typeof (record.projectId || record.project_id) === "string" ? String(record.projectId || record.project_id) : undefined,
        workspaceId: typeof (record.workspaceId || record.workspace_id) === "string" ? String(record.workspaceId || record.workspace_id) : undefined,
        pathPlane: typeof (record.pathPlane || record.path_plane) === "string"
            ? String(record.pathPlane || record.path_plane) as RuntimeArtifact["pathPlane"]
            : undefined,
        storageClass: typeof (record.storageClass || record.storage_class) === "string" ? String(record.storageClass || record.storage_class) : undefined,
        surfaceVisible: typeof (record.surfaceVisible ?? record.surface_visible) === "boolean" ? Boolean(record.surfaceVisible ?? record.surface_visible) : undefined,
        externalUrl: resolvedUrl,
        previewUrl: resolvedUrl,
        resourceRef,
        hasPreview: Boolean(record.hasPreview ?? resolvedUrl ?? record.contentUrl ?? record.content_url),
        supportsInlinePreview: Boolean(record.supportsInlinePreview ?? record.supports_inline_preview),
        previewKind: typeof (record.previewKind || record.preview_kind) === "string" ? String(record.previewKind || record.preview_kind) : undefined,
        sourceComponent: typeof (record.sourceComponent || record.source_component) === "string" ? String(record.sourceComponent || record.source_component) : undefined,
        metadata: typeof record.metadata === "object" && record.metadata ? record.metadata as Record<string, unknown> : {},
        createdAt: typeof (record.createdAt || record.created_at) === "string" ? String(record.createdAt || record.created_at) : undefined,
    };
}

export function normalizeRuntimeArtifacts(input: unknown): RuntimeArtifact[] {
    if (!Array.isArray(input)) {
        return [];
    }
    return input
        .map((artifact) => normalizeRuntimeArtifact(artifact))
        .filter((artifact): artifact is RuntimeArtifact => artifact !== null);
}

export function inferArtifactCardType(artifact: RuntimeArtifact): "code" | "markdown" | "html" | "image" | "video" | "audio" | "document" | "file" {
    const kind = artifact.kind.toLowerCase();
    if (kind === "image" || kind === "video" || kind === "audio" || kind === "document") {
        return kind;
    }
    const mime = artifact.mimeType.toLowerCase();
    if (mime.includes("html")) return "html";
    if (mime.includes("markdown")) return "markdown";
    if (mime.startsWith("text/")) return "code";
    return "file";
}
