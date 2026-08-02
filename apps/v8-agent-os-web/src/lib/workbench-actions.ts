import { normalizeWorkbenchDocument, type WorkspaceFileWorkbenchDocument } from "@/lib/workbench";
import { useWorkbenchStore } from "@/store/workbench-store";


type ResolveWorkspaceFileOptions = {
    sessionId?: string;
    line?: number;
    activate?: boolean;
};

export type WorkbenchFileCatalogItem = {
    sessionId: string;
    workspacePath: string;
    name: string;
    mimeType: string;
    language?: string | null;
    kind: string;
    previewable: boolean;
    size: number;
    mtime: string;
};

export type WorkbenchFileCatalogPage = {
    sessionId: string;
    workspaceId?: string | null;
    projectId?: string | null;
    items: WorkbenchFileCatalogItem[];
    nextCursor?: string | null;
    hasMore: boolean;
    truncated: boolean;
};

const WORKBENCH_FILE_CACHE_TTL_MS = 15_000;
const WORKBENCH_FILE_CACHE_LIMIT = 64;
const workspaceFileCatalogCache = new Map<string, {
    expiresAt: number;
    promise: Promise<WorkbenchFileCatalogPage>;
}>();

function workspaceFileCatalogKey(sessionId: string, query: string, cursor: string, limit: number) {
    return `${sessionId}\u0000${query}\u0000${cursor}\u0000${limit}`;
}

export function invalidateWorkbenchFileCatalog(sessionId: string) {
    const prefix = `${String(sessionId || "").trim()}\u0000`;
    for (const key of workspaceFileCatalogCache.keys()) {
        if (key.startsWith(prefix)) workspaceFileCatalogCache.delete(key);
    }
}

export async function listWorkspaceFiles(
    sessionId: string,
    options: { query?: string; cursor?: string | null; limit?: number; signal?: AbortSignal } = {},
): Promise<WorkbenchFileCatalogPage> {
    const normalizedSessionId = String(sessionId || "").trim();
    if (!normalizedSessionId) throw new Error("缺少会话。");
    const params = new URLSearchParams({
        q: String(options.query || "").trim(),
        cursor: String(options.cursor || "0"),
        limit: String(options.limit || 60),
    });
    if (options.signal?.aborted) throw new DOMException("Aborted", "AbortError");
    const key = workspaceFileCatalogKey(
        normalizedSessionId,
        params.get("q") || "",
        params.get("cursor") || "0",
        Number(params.get("limit") || 60),
    );
    const now = Date.now();
    let entry = workspaceFileCatalogCache.get(key);
    if (!entry || entry.expiresAt <= now) {
        for (const [cachedKey, cachedEntry] of workspaceFileCatalogCache) {
            if (cachedEntry.expiresAt <= now) workspaceFileCatalogCache.delete(cachedKey);
        }
        while (workspaceFileCatalogCache.size >= WORKBENCH_FILE_CACHE_LIMIT) {
            const oldest = workspaceFileCatalogCache.keys().next().value;
            if (typeof oldest !== "string") break;
            workspaceFileCatalogCache.delete(oldest);
        }
        const promise = fetch(
            `/api/workbench/sessions/${encodeURIComponent(normalizedSessionId)}/files?${params.toString()}`,
            { cache: "no-store" },
        ).then(async (response) => {
            const payload = await response.json().catch(() => ({})) as Partial<WorkbenchFileCatalogPage> & { detail?: unknown; error?: unknown };
            if (!response.ok) throw new Error(String(payload.detail || payload.error || `HTTP ${response.status}`));
            if (payload.sessionId !== normalizedSessionId) throw new Error("文件目录已不属于当前任务。");
            return {
                sessionId: normalizedSessionId,
                workspaceId: payload.workspaceId,
                projectId: payload.projectId,
                items: Array.isArray(payload.items) ? payload.items : [],
                nextCursor: payload.nextCursor,
                hasMore: Boolean(payload.hasMore),
                truncated: Boolean(payload.truncated),
            };
        });
        entry = { expiresAt: now + WORKBENCH_FILE_CACHE_TTL_MS, promise };
        workspaceFileCatalogCache.set(key, entry);
        void promise.catch(() => {
            if (workspaceFileCatalogCache.get(key)?.promise === promise) workspaceFileCatalogCache.delete(key);
        });
    }
    const page = await entry.promise;
    if (options.signal?.aborted) throw new DOMException("Aborted", "AbortError");
    return page;
}

export function prefetchWorkspaceFiles(sessionId: string) {
    return listWorkspaceFiles(sessionId).then(() => undefined);
}

function workspaceRenderer(payload: Record<string, unknown>): WorkspaceFileWorkbenchDocument["renderer"] {
    const language = String(payload.language || "").toLowerCase();
    const mimeType = String(payload.mimeType || "").toLowerCase();
    if (language === "markdown" || mimeType.includes("markdown")) return "markdown";
    if (language === "html" || mimeType.includes("html")) return "html";
    if (payload.binary === true) return "metadata";
    if (language && language !== "text") return "code";
    return "text";
}
export async function resolveAndOpenWorkspaceFile(path: string, options: ResolveWorkspaceFileOptions = {}) {
    const store = useWorkbenchStore.getState();
    const sessionId = String(options.sessionId || store.sessionId || "").trim();
    const requestedPath = String(path || "").trim();
    if (!sessionId || !requestedPath) {
        throw new Error("缺少会话或文件路径。");
    }
    const response = await fetch(`/api/workbench/sessions/${encodeURIComponent(sessionId)}/files/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            path: requestedPath,
            focusRequested: options.activate !== false,
            userInitiated: true,
        }),
        cache: "no-store",
    });
    const payload = await response.json().catch(() => ({})) as Record<string, unknown>;
    if (!response.ok) {
        const detail = payload.detail ?? payload.error ?? `HTTP ${response.status}`;
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    const serverDocument = normalizeWorkbenchDocument(payload.document);
    const workspacePath = String(payload.workspacePath || requestedPath).replace(/\\/g, "/");
    const document = serverDocument?.kind === "workspace_file"
        ? {
            ...serverDocument,
            renderer: workspaceRenderer(payload),
            subjectRef: {
                ...serverDocument.subjectRef,
                ...(options.line ? { line: options.line } : {}),
            },
        } as WorkspaceFileWorkbenchDocument
        : {
            kind: "workspace_file" as const,
            documentId: `workspace-file:${sessionId}:${workspacePath}`,
            title: String(payload.name || workspacePath.split("/").at(-1) || workspacePath),
            renderer: workspaceRenderer(payload),
            lifecycle: "session" as const,
            status: "available" as const,
            capabilities: payload.binary === true
                ? ["read", "download", "focus"] as const
                : ["read", "search", "copy", "download", "focus"] as const,
            subjectRef: { sessionId, workspacePath, ...(options.line ? { line: options.line } : {}) },
        };
    store.openDocument(document as WorkspaceFileWorkbenchDocument, {
        activate: options.activate !== false,
        mode: "split",
    });
    return document;
}

export function decodeWorkbenchFileHref(href: string) {
    const raw = String(href || "").trim();
    if (!raw) return "";
    if (raw.startsWith("#v8-workbench-file=")) {
        try {
            return decodeURIComponent(raw.slice("#v8-workbench-file=".length));
        } catch {
            return raw.slice("#v8-workbench-file=".length);
        }
    }
    if (/^workspace:\/\//i.test(raw)) {
        return raw.replace(/^workspace:\/\//i, "");
    }
    if (/^file:\/\//i.test(raw)) {
        try {
            const url = new URL(raw);
            return decodeURIComponent(url.pathname.replace(/^\/(?:([A-Za-z]:))/i, "$1"));
        } catch {
            return raw.replace(/^file:\/+/i, "");
        }
    }
    if (/^[A-Za-z]:[\\/]/.test(raw) || /^workspace[\\/]/i.test(raw)) {
        return raw;
    }
    return "";
}
