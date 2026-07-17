import type {
    ArtifactWorkbenchDocumentRef,
    McpAppViewRef,
    SessionOverviewWorkbenchDocumentRef,
    SubagentActivityWorkbenchDocumentRef,
    UiAppWorkbenchDocumentRef,
    WorkbenchDocumentCapability as SharedWorkbenchDocumentCapability,
    WorkbenchDocumentLifecycle as SharedWorkbenchDocumentLifecycle,
    WorkbenchDocumentRef,
    WorkbenchDocumentStatus as SharedWorkbenchDocumentStatus,
    WorkbenchMode as SharedWorkbenchMode,
    WorkspaceFileWorkbenchDocumentRef,
} from "@v8/session-realtime";

import type { RuntimeArtifact } from "@/lib/artifacts";
import { inferArtifactCardType } from "@/lib/artifacts";

export type WorkbenchMode = SharedWorkbenchMode;
export type WorkbenchDocumentLifecycle = SharedWorkbenchDocumentLifecycle;
export type WorkbenchDocumentStatus = SharedWorkbenchDocumentStatus;
export type WorkbenchDocumentCapability = SharedWorkbenchDocumentCapability;
export type SessionOverviewWorkbenchDocument = SessionOverviewWorkbenchDocumentRef;
export type SubagentActivityWorkbenchDocument = SubagentActivityWorkbenchDocumentRef;
export type WorkspaceFileWorkbenchDocument = WorkspaceFileWorkbenchDocumentRef;
export type ArtifactWorkbenchDocument = ArtifactWorkbenchDocumentRef;
export type UiAppWorkbenchDocument = UiAppWorkbenchDocumentRef;

export type WorkbenchDocumentPayload = {
    artifact?: RuntimeArtifact;
    inlineContent?: string;
    language?: string;
    mimeType?: string;
    resourceUrl?: string;
};

const documentPayloads = new Map<string, WorkbenchDocumentPayload>();

export function setWorkbenchDocumentPayload(documentId: string, payload: WorkbenchDocumentPayload) {
    documentPayloads.set(documentId, payload);
}

export function getWorkbenchDocumentPayload(documentId: string) {
    return documentPayloads.get(documentId);
}

export function clearWorkbenchDocumentPayload(documentId: string) {
    documentPayloads.delete(documentId);
}

export type WorkbenchDocument = WorkbenchDocumentRef;

export type WorkbenchTab = {
    document: WorkbenchDocument;
    unread: boolean;
    openedAt: number;
    lastActivatedAt: number;
};

const DOCUMENT_KINDS = new Set<WorkbenchDocument["kind"]>([
    "session_overview",
    "subagent_activity",
    "workspace_file",
    "artifact",
    "ui_app",
    "browser",
]);

const DOCUMENT_STATUSES = new Set<WorkbenchDocumentStatus>([
    "available",
    "loading",
    "ready",
    "unavailable",
]);

const CAPABILITIES = new Set<WorkbenchDocumentCapability>([
    "read",
    "search",
    "copy",
    "download",
    "interact",
    "navigate",
    "control",
    "focus",
]);

function recordOf(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

function stringOf(value: unknown) {
    return typeof value === "string" ? value.trim() : "";
}

function normalizeCapabilities(value: unknown): WorkbenchDocumentCapability[] {
    if (!Array.isArray(value)) return [];
    return value
        .map((item) => stringOf(item) as WorkbenchDocumentCapability)
        .filter((item) => CAPABILITIES.has(item));
}

export function normalizeWorkbenchDocument(value: unknown): WorkbenchDocument | null {
    const record = recordOf(value);
    const kind = stringOf(record.kind) as WorkbenchDocument["kind"];
    const documentId = stringOf(record.documentId || record.document_id);
    const title = stringOf(record.title) || "web.workbench.document";
    const renderer = stringOf(record.renderer);
    const subjectRef = recordOf(record.subjectRef || record.subject_ref);
    if (!DOCUMENT_KINDS.has(kind) || !documentId || !renderer) return null;

    const lifecycle: WorkbenchDocumentLifecycle = stringOf(record.lifecycle) === "runtime" ? "runtime" : "session";
    const rawStatus = stringOf(record.status) as WorkbenchDocumentStatus;
    const status = DOCUMENT_STATUSES.has(rawStatus) ? rawStatus : "available";
    const base = {
        kind,
        documentId,
        title,
        renderer,
        lifecycle,
        status,
        capabilities: normalizeCapabilities(record.capabilities),
        subjectRef,
        createdAt: stringOf(record.createdAt || record.created_at) || undefined,
        updatedAt: stringOf(record.updatedAt || record.updated_at) || undefined,
        unavailableReason: stringOf(record.unavailableReason || record.unavailable_reason) || undefined,
    };

    if (kind === "session_overview") {
        const sessionId = stringOf(subjectRef.sessionId || subjectRef.session_id);
        if (!sessionId || renderer !== "session_overview") return null;
        return { ...base, kind, renderer: "session_overview", subjectRef: { sessionId } };
    }
    if (kind === "subagent_activity") {
        const sessionId = stringOf(subjectRef.sessionId || subjectRef.session_id);
        const delegationId = stringOf(subjectRef.delegationId || subjectRef.delegation_id);
        if (!sessionId || !delegationId || renderer !== "subagent_activity") return null;
        return { ...base, kind, renderer: "subagent_activity", subjectRef: { sessionId, delegationId } };
    }
    if (kind === "workspace_file") {
        const sessionId = stringOf(subjectRef.sessionId || subjectRef.session_id);
        const workspacePath = stringOf(subjectRef.workspacePath || subjectRef.workspace_path || subjectRef.path);
        if (!sessionId || !workspacePath || !["code", "text", "markdown", "html", "metadata"].includes(renderer)) return null;
        const line = Number(subjectRef.line);
        return {
            ...base,
            kind,
            renderer: renderer as WorkspaceFileWorkbenchDocument["renderer"],
            subjectRef: { sessionId, workspacePath, ...(Number.isFinite(line) && line > 0 ? { line } : {}) },
        };
    }
    if (kind === "artifact") {
        const artifactId = stringOf(subjectRef.artifactId || subjectRef.artifact_id || subjectRef.id);
        if (!artifactId || !["image", "video", "audio", "code", "text", "markdown", "html", "pdf", "model_3d", "download"].includes(renderer)) return null;
        return {
            ...base,
            kind,
            renderer: renderer as ArtifactWorkbenchDocument["renderer"],
            subjectRef: {
                artifactId,
                sessionId: stringOf(subjectRef.sessionId || subjectRef.session_id) || undefined,
            },
        };
    }
    if (kind === "ui_app") {
        const appRecord = recordOf(subjectRef.app);
        const appInstanceId = stringOf(appRecord.appInstanceId || appRecord.app_instance_id);
        const resourceUri = stringOf(appRecord.resourceUri || appRecord.resource_uri);
        if (!appInstanceId || !resourceUri || !["mcp_app", "figma_canvas"].includes(renderer)) return null;
        return {
            ...base,
            kind,
            renderer: renderer as UiAppWorkbenchDocument["renderer"],
            subjectRef: { app: appRecord as unknown as McpAppViewRef },
        };
    }

    const browserSessionId = stringOf(subjectRef.browserSessionId || subjectRef.browser_session_id);
    const sessionId = stringOf(subjectRef.sessionId || subjectRef.session_id);
    if (!browserSessionId || !sessionId || renderer !== "browser") return null;
    return {
        ...base,
        kind: "browser",
        renderer: "browser",
        subjectRef: {
            browserSessionId,
            sessionId,
        },
    };
}

export function createSessionOverviewDocument(sessionId: string): SessionOverviewWorkbenchDocument {
    return {
        kind: "session_overview",
        documentId: `session-overview:${sessionId}`,
        title: "web.workbench.overview",
        renderer: "session_overview",
        lifecycle: "session",
        status: "ready",
        capabilities: ["read", "focus"],
        subjectRef: { sessionId },
    };
}

export function createSubagentActivityDocument(input: {
    sessionId: string;
    delegationId: string;
    title: string;
}): SubagentActivityWorkbenchDocument {
    return {
        kind: "subagent_activity",
        documentId: `subagent-activity:${input.sessionId}:${input.delegationId}`,
        title: input.title,
        renderer: "subagent_activity",
        lifecycle: "session",
        status: "ready",
        capabilities: ["read", "focus"],
        subjectRef: { sessionId: input.sessionId, delegationId: input.delegationId },
    };
}

function artifactRenderer(artifact: RuntimeArtifact): ArtifactWorkbenchDocument["renderer"] {
    const mimeType = String(artifact.mimeType || "").toLowerCase();
    const previewKind = String(artifact.previewKind || "").toLowerCase();
    if (mimeType.includes("pdf")) return "pdf";
    if (mimeType.includes("gltf") || previewKind.includes("model") || previewKind.includes("3d")) return "model_3d";
    const cardType = inferArtifactCardType(artifact);
    if (cardType === "music") return "audio";
    if (cardType === "document" || cardType === "file") return "download";
    return cardType === "code" ? "code" : cardType;
}

export function createArtifactDocument(artifact: RuntimeArtifact): ArtifactWorkbenchDocument {
    const document: ArtifactWorkbenchDocument = {
        kind: "artifact",
        documentId: `artifact:${artifact.id}`,
        title: artifact.displayLabel || artifact.title || artifact.id,
        renderer: artifactRenderer(artifact),
        lifecycle: "session",
        status: "available",
        capabilities: ["read", "copy", "download", "focus"],
        subjectRef: {
            artifactId: artifact.id,
            sessionId: artifact.sessionId,
        },
        createdAt: artifact.createdAt,
    };
    setWorkbenchDocumentPayload(document.documentId, { artifact, mimeType: artifact.mimeType });
    return document;
}

export function createInlineArtifactDocument(input: {
    id: string;
    title: string;
    content: string;
    type?: string;
    language?: string;
}): ArtifactWorkbenchDocument {
    const type = String(input.type || "text").toLowerCase();
    const renderer: ArtifactWorkbenchDocument["renderer"] = type === "html"
        ? "html"
        : type === "markdown"
            ? "markdown"
            : type === "code"
                ? "code"
                : "text";
    const document: ArtifactWorkbenchDocument = {
        kind: "artifact",
        documentId: `inline-artifact:${input.id}`,
        title: input.title,
        renderer,
        lifecycle: "runtime",
        status: "ready",
        capabilities: ["read", "search", "copy", "download", "focus"],
        subjectRef: {
            artifactId: input.id,
        },
    };
    setWorkbenchDocumentPayload(document.documentId, {
        inlineContent: input.content,
        language: input.language,
        mimeType: renderer === "html" ? "text/html" : renderer === "markdown" ? "text/markdown" : "text/plain",
    });
    return document;
}

export function createExternalArtifactDocument(input: {
    id: string;
    title: string;
    url: string;
    renderer: ArtifactWorkbenchDocument["renderer"];
    mimeType?: string;
}): ArtifactWorkbenchDocument {
    const document: ArtifactWorkbenchDocument = {
        kind: "artifact",
        documentId: `external-artifact:${input.id}`,
        title: input.title,
        renderer: input.renderer,
        lifecycle: "runtime",
        status: "available",
        capabilities: ["read", "download", "focus"],
        subjectRef: { artifactId: input.id },
    };
    setWorkbenchDocumentPayload(document.documentId, {
        resourceUrl: input.url,
        mimeType: input.mimeType,
    });
    return document;
}

export function createUiAppDocument(app: McpAppViewRef): UiAppWorkbenchDocument {
    const isFigma = app.renderer === "figma";
    return {
        kind: "ui_app",
        documentId: `ui-app:${app.appInstanceId}`,
        title: app.title || (isFigma ? "Figma Canvas" : "UI App"),
        renderer: isFigma ? "figma_canvas" : "mcp_app",
        lifecycle: "runtime",
        status: "available",
        capabilities: ["interact", "focus"],
        subjectRef: { app },
    };
}

export function createWorkspaceFileDocument(input: {
    sessionId: string;
    workspacePath: string;
    title?: string;
    renderer?: WorkspaceFileWorkbenchDocument["renderer"];
    line?: number;
}): WorkspaceFileWorkbenchDocument {
    const workspacePath = input.workspacePath.replace(/\\/g, "/");
    const title = input.title || workspacePath.split("/").filter(Boolean).at(-1) || workspacePath;
    return {
        kind: "workspace_file",
        documentId: `workspace-file:${input.sessionId}:${workspacePath}`,
        title,
        renderer: input.renderer || "code",
        lifecycle: "session",
        status: "available",
        capabilities: ["read", "search", "copy", "download", "focus"],
        subjectRef: {
            sessionId: input.sessionId,
            workspacePath,
            ...(input.line ? { line: input.line } : {}),
        },
    };
}
