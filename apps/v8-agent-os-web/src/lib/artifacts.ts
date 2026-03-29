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
    externalUrl?: string;
    previewUrl?: string;
    hasPreview?: boolean;
    supportsInlinePreview?: boolean;
    previewKind?: string;
    sourceComponent?: string;
    metadata?: Record<string, unknown>;
    createdAt?: string;
}

type ArtifactRecord = Record<string, unknown>;

function normalizeArtifactPreviewUrl(record: ArtifactRecord, artifactId: string): string | undefined {
    const raw = record.previewUrl || record.preview_url || record.externalUrl || record.external_url || record.contentUrl || record.content_url;
    if (typeof raw !== "string" || !raw) {
        return undefined;
    }
    if (raw.startsWith("/v1/artifacts/")) {
        return `/api/artifacts/${encodeURIComponent(artifactId)}/content`;
    }
    return raw;
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

    const title = String(record.displayLabel || record.title || artifactId);
    const displaySubtitle = String(
        record.displaySubtitle
            || record.workspacePath
            || record.workspace_path
            || record.sourcePath
            || record.source_path
            || record.previewUrl
            || record.preview_url
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
        externalUrl: typeof (record.externalUrl || record.external_url) === "string" ? String(record.externalUrl || record.external_url) : undefined,
        previewUrl: normalizeArtifactPreviewUrl(record, artifactId),
        hasPreview: Boolean(record.hasPreview ?? record.previewUrl ?? record.preview_url ?? record.externalUrl ?? record.external_url ?? record.contentUrl ?? record.content_url),
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
