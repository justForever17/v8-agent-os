import {
    coerceAdminProcessRef,
    coerceAdminResourceRef,
    deriveAdminResourceRefFromArtifactLike,
    type AdminProcessRef,
    type AdminResourceRef,
} from "@v8/session-realtime";
import { buildSignedClientSurfaceUrl } from "@/lib/server/client-surface-resource";

type JsonRecord = Record<string, unknown>;
type SurfaceNormalizationOptions = {
    publicBaseUrl?: string;
};

const SURFACE_URL_PATTERN = /https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\/[^\s"'<>\\]+/gi;
const SURFACE_RELATIVE_URL_PATTERN = /(?:^|[\s("'=])((?:\/)?(?:workspace\/[^\s"'<>\\]+|api\/workspace\/files\/[^\s"'<>\\]+|api\/client\/workspace\/files\/[^\s"'<>\\]+|(?:v1|api(?:\/client)?)\/artifacts\/[^/\s"'<>\\]+\/content(?:\?[^\s"'<>\\]+)?))/gi;
const WORKSPACE_MEDIA_PATH_PATTERN = /(?:[a-zA-Z]:\\[^\s"'<>`]+|\/?(?:workspace[\\/]|downloaded_media\/|uploads\/)[^\s"'<>`]+)/g;
const MEDIA_EXTENSION_TO_KIND = new Map<string, "image" | "video" | "audio">([
    [".png", "image"],
    [".jpg", "image"],
    [".jpeg", "image"],
    [".gif", "image"],
    [".webp", "image"],
    [".bmp", "image"],
    [".svg", "image"],
    [".mp4", "video"],
    [".webm", "video"],
    [".mov", "video"],
    [".m4v", "video"],
    [".mp3", "audio"],
    [".wav", "audio"],
    [".m4a", "audio"],
    [".ogg", "audio"],
    [".aac", "audio"],
    [".flac", "audio"],
]);

function normalizePathCandidate(raw: string) {
    return String(raw || "")
        .trim()
        .replace(/^[`"'([{<]+/, "")
        .replace(/[`"')\]}>]+$/, "")
        .replace(/[.,;:!?]+$/, "")
        .trim();
}

function inferMediaExtension(value: string) {
    const normalized = String(value || "").trim().replace(/[?#].*$/, "");
    const match = normalized.match(/(\.[a-z0-9]+)$/i);
    return match?.[1]?.toLowerCase() || "";
}

function inferDerivedMediaKind(value: string) {
    return MEDIA_EXTENSION_TO_KIND.get(inferMediaExtension(value)) || null;
}

function inferDerivedMediaMimeType(value: string) {
    const extension = inferMediaExtension(value);
    const kind = inferDerivedMediaKind(value);
    if (!extension || !kind) {
        return undefined;
    }
    const suffix = extension.slice(1).toLowerCase();
    if (kind === "image" && suffix === "jpg") {
        return "image/jpeg";
    }
    return `${kind}/${suffix}`;
}

function extractNarrativeMediaPaths(value: unknown) {
    if (typeof value !== "string" || !value.trim()) {
        return [];
    }
    const matches = value.match(WORKSPACE_MEDIA_PATH_PATTERN) || [];
    const deduped = new Set<string>();
    for (const match of matches) {
        const candidate = normalizePathCandidate(match);
        if (!candidate || !inferDerivedMediaKind(candidate)) {
            continue;
        }
        const resourceRef = coerceAdminResourceRef(candidate);
        if (!resourceRef || resourceRef.kind !== "workspace_file") {
            continue;
        }
        deduped.add(candidate);
    }
    return Array.from(deduped);
}

function artifactKey(value: unknown) {
    const record = asRecord(value);
    const resourceRef = coerceAdminResourceRef(record.resourceRef);
    return String(
        record.id
        || record.artifactId
        || resourceRef?.adminPath
        || resourceRef?.workspacePath
        || resourceRef?.url
        || record.previewUrl
        || record.externalUrl
        || record.sourcePath
        || "",
    ).trim();
}

function mergeArtifacts(existing: unknown, derived: JsonRecord[], options?: SurfaceNormalizationOptions) {
    const normalizedExisting = Array.isArray(existing)
        ? existing.map((artifact) => normalizeArtifactForRealtimeSurface(artifact, options))
        : [];
    const seen = new Set(normalizedExisting.map((artifact) => artifactKey(artifact)).filter(Boolean));
    for (const artifact of derived) {
        const key = artifactKey(artifact);
        if (!key || seen.has(key)) {
            continue;
        }
        seen.add(key);
        normalizedExisting.push(artifact);
    }
    return Array.isArray(existing) || normalizedExisting.length > 0 ? normalizedExisting : existing;
}

function mergeMediaUrls(existing: unknown, derivedArtifacts: JsonRecord[], options?: SurfaceNormalizationOptions) {
    const values = new Set<string>();
    if (Array.isArray(existing)) {
        for (const entry of existing) {
            const normalized = normalizeSurfaceUrl(entry, options) || String(entry || "").trim();
            if (normalized) {
                values.add(normalized);
            }
        }
    }
    for (const artifact of derivedArtifacts) {
        const previewUrl = String(artifact.previewUrl || artifact.externalUrl || "").trim();
        if (previewUrl) {
            values.add(previewUrl);
        }
    }
    return Array.isArray(existing) || values.size > 0 ? Array.from(values) : existing;
}

function deriveNarrativeMediaArtifacts(values: unknown[], options?: SurfaceNormalizationOptions) {
    const derived: JsonRecord[] = [];
    const seen = new Set<string>();
    for (const value of values) {
        for (const rawPath of extractNarrativeMediaPaths(value)) {
            const kind = inferDerivedMediaKind(rawPath);
            if (!kind) {
                continue;
            }
            const resourceRef = attachSignedSurfaceUrl(coerceAdminResourceRef(rawPath), options);
            if (!resourceRef || resourceRef.kind !== "workspace_file") {
                continue;
            }
            const key = String(resourceRef.adminPath || resourceRef.workspacePath || rawPath).trim();
            if (!key || seen.has(key)) {
                continue;
            }
            seen.add(key);
            const previewUrl = materializeSurfaceUrl(resourceRef);
            const workspacePath = String(resourceRef.workspacePath || "").trim();
            const title = workspacePath.split("/").filter(Boolean).pop() || workspacePath || rawPath;
            derived.push({
                id: `workspace-media:${key}`,
                kind,
                title,
                displayLabel: title,
                displaySubtitle: String(resourceRef.displaySubtitle || "").trim() || undefined,
                workspacePath: workspacePath || undefined,
                sourcePath: rawPath,
                mimeType: inferDerivedMediaMimeType(rawPath),
                previewUrl,
                externalUrl: previewUrl,
                resourceRef,
                derivedFromNarrative: true,
            });
        }
    }
    return derived;
}

function asRecord(value: unknown): JsonRecord {
    return value && typeof value === "object" ? value as JsonRecord : {};
}

function asRecordArray(value: unknown) {
    return Array.isArray(value)
        ? value.filter((item): item is JsonRecord => Boolean(item) && typeof item === "object")
        : [];
}

function materializeSurfaceUrl(resourceRef: AdminResourceRef | null) {
    if (!resourceRef) {
        return undefined;
    }
    if (resourceRef.signedUrl) {
        return resourceRef.signedUrl;
    }
    if (resourceRef.kind === "external_url") {
        return resourceRef.url || undefined;
    }
    return undefined;
}

function attachSignedSurfaceUrl(resourceRef: AdminResourceRef | null, options?: SurfaceNormalizationOptions) {
    if (!resourceRef || resourceRef.kind === "external_url") {
        return resourceRef;
    }
    const adminPath = String(resourceRef.adminPath || "").trim();
    if (!adminPath) {
        return resourceRef;
    }
    const signedUrl = buildSignedClientSurfaceUrl(adminPath, { publicBaseUrl: options?.publicBaseUrl });
    if (!signedUrl) {
        return {
            ...resourceRef,
            previewable: false,
            displaySubtitle: String(resourceRef.displaySubtitle || "").trim()
                || "Admin public base 不可达，手机端预览需要可访问的局域网或公网地址。",
        };
    }
    return {
        ...resourceRef,
        signedUrl,
    };
}

function normalizeSurfaceUrl(value: unknown, options?: SurfaceNormalizationOptions) {
    const rawValue = String(value || "").trim();
    if (
        /^\/api\/client\/(?:workspace\/files\/|artifacts\/[^/]+\/content)/i.test(rawValue)
        && /[?&]v8(?:sig|exp)=/i.test(rawValue)
    ) {
        return rawValue;
    }
    const resourceRef = attachSignedSurfaceUrl(coerceAdminResourceRef(value), options);
    if (!resourceRef) {
        return rawValue || undefined;
    }
    return materializeSurfaceUrl(resourceRef);
}

function normalizeSurfaceContent(value: unknown, options?: SurfaceNormalizationOptions) {
    if (typeof value !== "string") {
        return value;
    }

    const raw = value;
    if (!raw.trim()) {
        return raw;
    }

    const replaceMatch = (match: string) => normalizeSurfaceUrl(match, options) || match;

    return raw
        .replace(SURFACE_URL_PATTERN, replaceMatch)
        .replace(SURFACE_RELATIVE_URL_PATTERN, (match, path: string) => {
            const normalized = normalizeSurfaceUrl(path, options);
            if (!normalized) {
                return match;
            }
            return match.replace(path, normalized);
        });
}

function normalizeNodeForRealtimeSurface(raw: unknown, options?: SurfaceNormalizationOptions) {
    const record = asRecord(raw);
    if (!Object.keys(record).length) {
        return raw;
    }

    const data = asRecord(record.data);
    const nextNode: JsonRecord = {
        ...record,
        content: normalizeSurfaceContent(record.content, options),
        label: normalizeSurfaceContent(record.label, options),
        question: normalizeSurfaceContent(record.question, options),
        reason: normalizeSurfaceContent(record.reason, options),
    };

    if (typeof record.result === "string") {
        nextNode.result = normalizeSurfaceContent(record.result, options);
    }

    if (record.kind === "artifact" && record.artifact) {
        nextNode.artifact = normalizeArtifactForRealtimeSurface(record.artifact, options);
    }

    if (Object.keys(data).length) {
        nextNode.data = {
            ...data,
            content: normalizeSurfaceContent(data.content, options),
            message: normalizeSurfaceContent(data.message, options),
            summary: normalizeSurfaceContent(data.summary, options),
            url: normalizeSurfaceUrl(data.url, options),
            src: normalizeSurfaceUrl(data.src, options),
            image: normalizeSurfaceUrl(data.image, options),
            previewUrl: normalizeSurfaceUrl(data.previewUrl, options),
            externalUrl: normalizeSurfaceUrl(data.externalUrl, options),
        };
    }

    return nextNode;
}

export function normalizeProcessForRealtimeSurface(raw: unknown): AdminProcessRef | null {
    const normalized = coerceAdminProcessRef(raw);
    if (!normalized) {
        return null;
    }
    const encodedProcessId = encodeURIComponent(String(normalized.processId || normalized.commandId || "").trim());
    const baseAdminPath = encodedProcessId ? `/api/client/bg_processes/${encodedProcessId}` : "";
    return {
        ...normalized,
        outputAdminPath: normalized.outputAdminPath || baseAdminPath || undefined,
        streamAdminPath: normalized.streamAdminPath || (baseAdminPath ? `${baseAdminPath}/ws` : undefined),
        inputAdminPath: normalized.inputAdminPath || (baseAdminPath ? `${baseAdminPath}/input` : undefined),
        terminateAdminPath: normalized.terminateAdminPath || (baseAdminPath ? `${baseAdminPath}/terminate` : undefined),
    };
}

function normalizeContextReference(raw: unknown, options?: SurfaceNormalizationOptions) {
    const record = asRecord(raw);
    if (!Object.keys(record).length) {
        return raw;
    }
    return {
        ...record,
        resourceRef: record.resourceRef
            ? attachSignedSurfaceUrl(coerceAdminResourceRef(record.resourceRef), options)
            : record.resourceRef,
    };
}

function normalizeMessagePartForRealtimeSurface(raw: unknown, options?: SurfaceNormalizationOptions) {
    const record = asRecord(raw);
    if (!Object.keys(record).length) {
        return raw;
    }

    const data = asRecord(record.data);
    const nextPart: JsonRecord = {
        ...record,
        content: normalizeSurfaceContent(record.content, options),
        message: normalizeSurfaceContent(record.message, options),
        summary: normalizeSurfaceContent(record.summary, options),
    };

    if (typeof record.result === "string") {
        nextPart.result = normalizeSurfaceContent(record.result, options);
    }

    if (Object.keys(data).length) {
        nextPart.data = {
            ...data,
            content: normalizeSurfaceContent(data.content, options),
            message: normalizeSurfaceContent(data.message, options),
            summary: normalizeSurfaceContent(data.summary, options),
            url: normalizeSurfaceUrl(data.url, options),
            src: normalizeSurfaceUrl(data.src, options),
            image: normalizeSurfaceUrl(data.image, options),
            previewUrl: normalizeSurfaceUrl(data.previewUrl, options),
            externalUrl: normalizeSurfaceUrl(data.externalUrl, options),
        };
    }

    return nextPart;
}

export function normalizeArtifactForRealtimeSurface(raw: unknown, options?: SurfaceNormalizationOptions) {
    const record = asRecord(raw);
    if (!Object.keys(record).length) {
        return raw;
    }

    const resourceRef = attachSignedSurfaceUrl(deriveAdminResourceRefFromArtifactLike(record), options);
    const materializedUrl = materializeSurfaceUrl(resourceRef);

    return {
        ...record,
        resourceRef,
        previewUrl: materializedUrl,
        externalUrl: materializedUrl,
    };
}

export function normalizeMessageForRealtimeSurface(raw: unknown, options?: SurfaceNormalizationOptions) {
    const record = asRecord(raw);
    if (!Object.keys(record).length) {
        return raw;
    }

    const nodes = Array.isArray(record.nodes)
        ? record.nodes.map((node) => normalizeNodeForRealtimeSurface(node, options))
        : record.nodes;
    const parts = Array.isArray(record.parts)
        ? record.parts.map((part) => normalizeMessagePartForRealtimeSurface(part, options))
        : record.parts;

    const normalizedContent = normalizeSurfaceContent(record.content, options);
    const derivedArtifacts = deriveNarrativeMediaArtifacts([
        normalizedContent,
        ...asRecordArray(nodes).map((node) => node.content),
        ...asRecordArray(parts).flatMap((part) => [part.content, asRecord(part.data).content]),
    ], options);
    const artifacts = mergeArtifacts(record.artifacts, derivedArtifacts, options);
    const images = mergeMediaUrls(record.images, derivedArtifacts, options);

    return {
        ...record,
        content: normalizedContent,
        nodes,
        parts,
        artifacts,
        images,
    };
}

function normalizeRuntimeTimelineEntry(raw: unknown, options?: SurfaceNormalizationOptions) {
    const record = asRecord(raw);
    if (!Object.keys(record).length) {
        return raw;
    }

    const metadata = asRecord(record.metadata);
    const metadataResourceRef = attachSignedSurfaceUrl(
        coerceAdminResourceRef(metadata.resourceRef || deriveAdminResourceRefFromArtifactLike(metadata)),
        options,
    );
    const normalizedMetadata = {
        ...metadata,
        resourceRef: metadataResourceRef || metadata.resourceRef || deriveAdminResourceRefFromArtifactLike(metadata),
        content: normalizeSurfaceContent(metadata.content, options),
        message: normalizeSurfaceContent(metadata.message, options),
        summary: normalizeSurfaceContent(metadata.summary, options),
        previewUrl: normalizeSurfaceUrl(metadata.previewUrl, options),
        externalUrl: normalizeSurfaceUrl(metadata.externalUrl, options),
    };

    return {
        ...record,
        metadata: normalizedMetadata,
    };
}

export function normalizeSnapshotForRealtimeSurface(raw: unknown, options?: SurfaceNormalizationOptions) {
    const record = asRecord(raw);
    if (!Object.keys(record).length) {
        return raw;
    }

    const snapshot = asRecord(record.snapshot);
    const projection = asRecord(record.projection);
    const workflowProjection = asRecord(record.workflowProjection);

    return {
        ...record,
        messages: Array.isArray(record.messages)
            ? record.messages.map((message) => normalizeMessageForRealtimeSurface(message, options))
            : record.messages,
        artifacts: Array.isArray(record.artifacts)
            ? record.artifacts.map((artifact) => normalizeArtifactForRealtimeSurface(artifact, options))
            : record.artifacts,
        runtimeTimeline: Array.isArray(record.runtimeTimeline)
            ? record.runtimeTimeline.map((entry) => normalizeRuntimeTimelineEntry(entry, options))
            : record.runtimeTimeline,
        processes: Array.isArray(record.processes)
            ? record.processes
                .map((item) => normalizeProcessForRealtimeSurface(item))
                .filter(Boolean)
            : record.processes,
        contextReferences: Array.isArray(record.contextReferences)
            ? record.contextReferences.map((item) => normalizeContextReference(item, options))
            : record.contextReferences,
        projection: Object.keys(projection).length ? {
            ...projection,
            messages: Array.isArray(projection.messages)
                ? projection.messages.map((message) => normalizeMessageForRealtimeSurface(message, options))
                : projection.messages,
            artifacts: Array.isArray(projection.artifacts)
                ? projection.artifacts.map((artifact) => normalizeArtifactForRealtimeSurface(artifact, options))
                : projection.artifacts,
            runtimeTimeline: Array.isArray(projection.runtimeTimeline)
                ? projection.runtimeTimeline.map((entry) => normalizeRuntimeTimelineEntry(entry, options))
                : projection.runtimeTimeline,
            processes: Array.isArray(projection.processes)
                ? projection.processes
                    .map((item) => normalizeProcessForRealtimeSurface(item))
                    .filter(Boolean)
                : projection.processes,
            contextReferences: Array.isArray(projection.contextReferences)
                ? projection.contextReferences.map((item) => normalizeContextReference(item, options))
                : projection.contextReferences,
        } : record.projection,
        workflowProjection: Object.keys(workflowProjection).length ? {
            ...workflowProjection,
            artifacts: Array.isArray(workflowProjection.artifacts)
                ? workflowProjection.artifacts.map((artifact) => normalizeArtifactForRealtimeSurface(artifact, options))
                : workflowProjection.artifacts,
            runtimeTimeline: Array.isArray(workflowProjection.runtimeTimeline)
                ? workflowProjection.runtimeTimeline.map((entry) => normalizeRuntimeTimelineEntry(entry, options))
                : workflowProjection.runtimeTimeline,
        } : record.workflowProjection,
        snapshot: Object.keys(snapshot).length ? {
            ...snapshot,
            messages: Array.isArray(snapshot.messages)
                ? snapshot.messages.map((message) => normalizeMessageForRealtimeSurface(message, options))
                : snapshot.messages,
            artifacts: Array.isArray(snapshot.artifacts)
                ? snapshot.artifacts.map((artifact) => normalizeArtifactForRealtimeSurface(artifact, options))
                : snapshot.artifacts,
            runtimeTimeline: Array.isArray(snapshot.runtimeTimeline)
                ? snapshot.runtimeTimeline.map((entry) => normalizeRuntimeTimelineEntry(entry, options))
                : snapshot.runtimeTimeline,
            processes: Array.isArray(snapshot.processes)
                ? snapshot.processes
                    .map((item) => normalizeProcessForRealtimeSurface(item))
                    .filter(Boolean)
                : snapshot.processes,
            contextReferences: Array.isArray(snapshot.contextReferences)
                ? snapshot.contextReferences.map((item) => normalizeContextReference(item, options))
                : snapshot.contextReferences,
        } : record.snapshot,
    };
}

export function normalizeRuntimeEventForRealtimeSurface(raw: unknown, options?: SurfaceNormalizationOptions) {
    const record = asRecord(raw);
    if (!Object.keys(record).length) {
        return raw;
    }

    const data = asRecord(record.data);
    const rawPayload = asRecord(record.raw);
    const payload = asRecord(rawPayload.payload);

    const normalizedContent = normalizeSurfaceContent(record.content, options);
    const derivedArtifacts = deriveNarrativeMediaArtifacts([
        normalizedContent,
        data.content,
        data.message,
        payload.content,
        payload.message,
    ], options);
    const derivedArtifact = derivedArtifacts[0];

    return {
        ...record,
        content: normalizedContent,
        artifact: record.artifact
            ? normalizeArtifactForRealtimeSurface(record.artifact, options)
            : derivedArtifact || record.artifact,
        data: Object.keys(data).length ? {
            ...data,
            content: normalizeSurfaceContent(data.content, options),
            message: normalizeSurfaceContent(data.message, options),
            summary: normalizeSurfaceContent(data.summary, options),
            artifact: data.artifact
                ? normalizeArtifactForRealtimeSurface(data.artifact, options)
                : derivedArtifact || data.artifact,
            process: data.process ? normalizeProcessForRealtimeSurface(data.process) : data.process,
            processes: Array.isArray(data.processes)
                ? data.processes.map((item) => normalizeProcessForRealtimeSurface(item)).filter(Boolean)
                : data.processes,
            previewUrl: normalizeSurfaceUrl(data.previewUrl, options),
            externalUrl: normalizeSurfaceUrl(data.externalUrl, options),
            image: normalizeSurfaceUrl(data.image, options),
            url: normalizeSurfaceUrl(data.url, options),
        } : record.data,
        raw: Object.keys(rawPayload).length ? {
            ...rawPayload,
            payload: Object.keys(payload).length ? {
                ...payload,
                content: normalizeSurfaceContent(payload.content, options),
                message: normalizeSurfaceContent(payload.message, options),
                summary: normalizeSurfaceContent(payload.summary, options),
                artifact: payload.artifact
                    ? normalizeArtifactForRealtimeSurface(payload.artifact, options)
                    : derivedArtifact || payload.artifact,
                process: payload.process ? normalizeProcessForRealtimeSurface(payload.process) : payload.process,
                processes: Array.isArray(payload.processes)
                    ? payload.processes.map((item) => normalizeProcessForRealtimeSurface(item)).filter(Boolean)
                    : payload.processes,
                previewUrl: normalizeSurfaceUrl(payload.previewUrl, options),
                externalUrl: normalizeSurfaceUrl(payload.externalUrl, options),
                image: normalizeSurfaceUrl(payload.image, options),
                url: normalizeSurfaceUrl(payload.url, options),
            } : rawPayload.payload,
        } : record.raw,
    };
}
