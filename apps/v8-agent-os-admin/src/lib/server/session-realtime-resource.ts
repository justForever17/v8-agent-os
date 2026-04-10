import {
    coerceAdminProcessRef,
    coerceAdminResourceRef,
    deriveAdminResourceRefFromArtifactLike,
    type AdminProcessRef,
    type AdminResourceRef,
} from "@v8/session-realtime";
import { buildSignedClientSurfaceUrl } from "@/lib/server/client-surface-resource";

type JsonRecord = Record<string, unknown>;

const SURFACE_URL_PATTERN = /https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\/[^\s"'<>\\]+/gi;
const SURFACE_RELATIVE_URL_PATTERN = /(?:^|[\s("'=])((?:\/)?(?:workspace\/[^\s"'<>\\]+|api\/workspace\/files\/[^\s"'<>\\]+|api\/client\/workspace\/files\/[^\s"'<>\\]+|(?:v1|api(?:\/client)?)\/artifacts\/[^/\s"'<>\\]+\/content(?:\?[^\s"'<>\\]+)?))/gi;

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
    return resourceRef.adminPath || undefined;
}

function attachSignedSurfaceUrl(resourceRef: AdminResourceRef | null) {
    if (!resourceRef || resourceRef.kind === "external_url") {
        return resourceRef;
    }
    const adminPath = String(resourceRef.adminPath || "").trim();
    if (!adminPath) {
        return resourceRef;
    }
    const signedUrl = buildSignedClientSurfaceUrl(adminPath);
    if (!signedUrl) {
        return resourceRef;
    }
    return {
        ...resourceRef,
        signedUrl,
    };
}

function normalizeSurfaceUrl(value: unknown) {
    const rawValue = String(value || "").trim();
    if (
        /^\/api\/client\/(?:workspace\/files\/|artifacts\/[^/]+\/content)/i.test(rawValue)
        && /[?&]v8(?:sig|exp)=/i.test(rawValue)
    ) {
        return rawValue;
    }
    const resourceRef = attachSignedSurfaceUrl(coerceAdminResourceRef(value));
    if (!resourceRef) {
        return rawValue || undefined;
    }
    return materializeSurfaceUrl(resourceRef);
}

function normalizeSurfaceContent(value: unknown) {
    if (typeof value !== "string") {
        return value;
    }

    const raw = value;
    if (!raw.trim()) {
        return raw;
    }

    const replaceMatch = (match: string) => normalizeSurfaceUrl(match) || match;

    return raw
        .replace(SURFACE_URL_PATTERN, replaceMatch)
        .replace(SURFACE_RELATIVE_URL_PATTERN, (match, path: string) => {
            const normalized = normalizeSurfaceUrl(path);
            if (!normalized) {
                return match;
            }
            return match.replace(path, normalized);
        });
}

function normalizeNodeForRealtimeSurface(raw: unknown) {
    const record = asRecord(raw);
    if (!Object.keys(record).length) {
        return raw;
    }

    const data = asRecord(record.data);
    const nextNode: JsonRecord = {
        ...record,
        content: normalizeSurfaceContent(record.content),
        label: normalizeSurfaceContent(record.label),
        question: normalizeSurfaceContent(record.question),
        reason: normalizeSurfaceContent(record.reason),
    };

    if (typeof record.result === "string") {
        nextNode.result = normalizeSurfaceContent(record.result);
    }

    if (record.kind === "artifact" && record.artifact) {
        nextNode.artifact = normalizeArtifactForRealtimeSurface(record.artifact);
    }

    if (Object.keys(data).length) {
        nextNode.data = {
            ...data,
            content: normalizeSurfaceContent(data.content),
            message: normalizeSurfaceContent(data.message),
            summary: normalizeSurfaceContent(data.summary),
            url: normalizeSurfaceUrl(data.url),
            src: normalizeSurfaceUrl(data.src),
            image: normalizeSurfaceUrl(data.image),
            previewUrl: normalizeSurfaceUrl(data.previewUrl),
            externalUrl: normalizeSurfaceUrl(data.externalUrl),
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

function normalizeContextReference(raw: unknown) {
    const record = asRecord(raw);
    if (!Object.keys(record).length) {
        return raw;
    }
    return {
        ...record,
        resourceRef: record.resourceRef ? attachSignedSurfaceUrl(coerceAdminResourceRef(record.resourceRef)) : record.resourceRef,
    };
}

function normalizeMessagePartForRealtimeSurface(raw: unknown) {
    const record = asRecord(raw);
    if (!Object.keys(record).length) {
        return raw;
    }

    const data = asRecord(record.data);
    const nextPart: JsonRecord = {
        ...record,
        content: normalizeSurfaceContent(record.content),
        message: normalizeSurfaceContent(record.message),
        summary: normalizeSurfaceContent(record.summary),
    };

    if (typeof record.result === "string") {
        nextPart.result = normalizeSurfaceContent(record.result);
    }

    if (Object.keys(data).length) {
        nextPart.data = {
            ...data,
            content: normalizeSurfaceContent(data.content),
            message: normalizeSurfaceContent(data.message),
            summary: normalizeSurfaceContent(data.summary),
            url: normalizeSurfaceUrl(data.url),
            src: normalizeSurfaceUrl(data.src),
            image: normalizeSurfaceUrl(data.image),
            previewUrl: normalizeSurfaceUrl(data.previewUrl),
            externalUrl: normalizeSurfaceUrl(data.externalUrl),
        };
    }

    return nextPart;
}

export function normalizeArtifactForRealtimeSurface(raw: unknown) {
    const record = asRecord(raw);
    if (!Object.keys(record).length) {
        return raw;
    }

    const resourceRef = attachSignedSurfaceUrl(deriveAdminResourceRefFromArtifactLike(record));
    const materializedUrl = materializeSurfaceUrl(resourceRef);

    return {
        ...record,
        resourceRef,
        previewUrl: materializedUrl,
        externalUrl: materializedUrl,
    };
}

export function normalizeMessageForRealtimeSurface(raw: unknown) {
    const record = asRecord(raw);
    if (!Object.keys(record).length) {
        return raw;
    }

    const nodes = Array.isArray(record.nodes)
        ? record.nodes.map((node) => normalizeNodeForRealtimeSurface(node))
        : record.nodes;
    const parts = Array.isArray(record.parts)
        ? record.parts.map((part) => normalizeMessagePartForRealtimeSurface(part))
        : record.parts;

    const artifacts = Array.isArray(record.artifacts)
        ? record.artifacts.map((artifact) => normalizeArtifactForRealtimeSurface(artifact))
        : record.artifacts;

    const images = Array.isArray(record.images)
        ? record.images.map((value) => normalizeSurfaceUrl(value) || String(value || "").trim()).filter(Boolean)
        : record.images;

    return {
        ...record,
        content: normalizeSurfaceContent(record.content),
        nodes,
        parts,
        artifacts,
        images,
    };
}

function normalizeRuntimeTimelineEntry(raw: unknown) {
    const record = asRecord(raw);
    if (!Object.keys(record).length) {
        return raw;
    }

    const metadata = asRecord(record.metadata);
    const normalizedMetadata = {
        ...metadata,
        resourceRef: metadata.resourceRef || deriveAdminResourceRefFromArtifactLike(metadata),
        content: normalizeSurfaceContent(metadata.content),
        message: normalizeSurfaceContent(metadata.message),
        summary: normalizeSurfaceContent(metadata.summary),
        previewUrl: normalizeSurfaceUrl(metadata.previewUrl),
        externalUrl: normalizeSurfaceUrl(metadata.externalUrl),
    };

    return {
        ...record,
        metadata: normalizedMetadata,
    };
}

export function normalizeSnapshotForRealtimeSurface(raw: unknown) {
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
            ? record.messages.map((message) => normalizeMessageForRealtimeSurface(message))
            : record.messages,
        artifacts: Array.isArray(record.artifacts)
            ? record.artifacts.map((artifact) => normalizeArtifactForRealtimeSurface(artifact))
            : record.artifacts,
        runtimeTimeline: Array.isArray(record.runtimeTimeline)
            ? record.runtimeTimeline.map((entry) => normalizeRuntimeTimelineEntry(entry))
            : record.runtimeTimeline,
        processes: Array.isArray(record.processes)
            ? record.processes
                .map((item) => normalizeProcessForRealtimeSurface(item))
                .filter(Boolean)
            : record.processes,
        contextReferences: Array.isArray(record.contextReferences)
            ? record.contextReferences.map((item) => normalizeContextReference(item))
            : record.contextReferences,
        projection: Object.keys(projection).length ? {
            ...projection,
            messages: Array.isArray(projection.messages)
                ? projection.messages.map((message) => normalizeMessageForRealtimeSurface(message))
                : projection.messages,
            artifacts: Array.isArray(projection.artifacts)
                ? projection.artifacts.map((artifact) => normalizeArtifactForRealtimeSurface(artifact))
                : projection.artifacts,
            runtimeTimeline: Array.isArray(projection.runtimeTimeline)
                ? projection.runtimeTimeline.map((entry) => normalizeRuntimeTimelineEntry(entry))
                : projection.runtimeTimeline,
            processes: Array.isArray(projection.processes)
                ? projection.processes
                    .map((item) => normalizeProcessForRealtimeSurface(item))
                    .filter(Boolean)
                : projection.processes,
            contextReferences: Array.isArray(projection.contextReferences)
                ? projection.contextReferences.map((item) => normalizeContextReference(item))
                : projection.contextReferences,
        } : record.projection,
        workflowProjection: Object.keys(workflowProjection).length ? {
            ...workflowProjection,
            artifacts: Array.isArray(workflowProjection.artifacts)
                ? workflowProjection.artifacts.map((artifact) => normalizeArtifactForRealtimeSurface(artifact))
                : workflowProjection.artifacts,
            runtimeTimeline: Array.isArray(workflowProjection.runtimeTimeline)
                ? workflowProjection.runtimeTimeline.map((entry) => normalizeRuntimeTimelineEntry(entry))
                : workflowProjection.runtimeTimeline,
        } : record.workflowProjection,
        snapshot: Object.keys(snapshot).length ? {
            ...snapshot,
            messages: Array.isArray(snapshot.messages)
                ? snapshot.messages.map((message) => normalizeMessageForRealtimeSurface(message))
                : snapshot.messages,
            artifacts: Array.isArray(snapshot.artifacts)
                ? snapshot.artifacts.map((artifact) => normalizeArtifactForRealtimeSurface(artifact))
                : snapshot.artifacts,
            runtimeTimeline: Array.isArray(snapshot.runtimeTimeline)
                ? snapshot.runtimeTimeline.map((entry) => normalizeRuntimeTimelineEntry(entry))
                : snapshot.runtimeTimeline,
            processes: Array.isArray(snapshot.processes)
                ? snapshot.processes
                    .map((item) => normalizeProcessForRealtimeSurface(item))
                    .filter(Boolean)
                : snapshot.processes,
            contextReferences: Array.isArray(snapshot.contextReferences)
                ? snapshot.contextReferences.map((item) => normalizeContextReference(item))
                : snapshot.contextReferences,
        } : record.snapshot,
    };
}

export function normalizeRuntimeEventForRealtimeSurface(raw: unknown) {
    const record = asRecord(raw);
    if (!Object.keys(record).length) {
        return raw;
    }

    const data = asRecord(record.data);
    const rawPayload = asRecord(record.raw);
    const payload = asRecord(rawPayload.payload);

    return {
        ...record,
        content: normalizeSurfaceContent(record.content),
        artifact: record.artifact ? normalizeArtifactForRealtimeSurface(record.artifact) : record.artifact,
        data: Object.keys(data).length ? {
            ...data,
            content: normalizeSurfaceContent(data.content),
            message: normalizeSurfaceContent(data.message),
            summary: normalizeSurfaceContent(data.summary),
            artifact: data.artifact ? normalizeArtifactForRealtimeSurface(data.artifact) : data.artifact,
            process: data.process ? normalizeProcessForRealtimeSurface(data.process) : data.process,
            processes: Array.isArray(data.processes)
                ? data.processes.map((item) => normalizeProcessForRealtimeSurface(item)).filter(Boolean)
                : data.processes,
            previewUrl: normalizeSurfaceUrl(data.previewUrl),
            externalUrl: normalizeSurfaceUrl(data.externalUrl),
            image: normalizeSurfaceUrl(data.image),
            url: normalizeSurfaceUrl(data.url),
        } : record.data,
        raw: Object.keys(rawPayload).length ? {
            ...rawPayload,
            payload: Object.keys(payload).length ? {
                ...payload,
                content: normalizeSurfaceContent(payload.content),
                message: normalizeSurfaceContent(payload.message),
                summary: normalizeSurfaceContent(payload.summary),
                artifact: payload.artifact ? normalizeArtifactForRealtimeSurface(payload.artifact) : payload.artifact,
                process: payload.process ? normalizeProcessForRealtimeSurface(payload.process) : payload.process,
                processes: Array.isArray(payload.processes)
                    ? payload.processes.map((item) => normalizeProcessForRealtimeSurface(item)).filter(Boolean)
                    : payload.processes,
                previewUrl: normalizeSurfaceUrl(payload.previewUrl),
                externalUrl: normalizeSurfaceUrl(payload.externalUrl),
                image: normalizeSurfaceUrl(payload.image),
                url: normalizeSurfaceUrl(payload.url),
            } : rawPayload.payload,
        } : record.raw,
    };
}
