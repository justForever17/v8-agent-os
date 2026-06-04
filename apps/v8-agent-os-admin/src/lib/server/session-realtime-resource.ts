import {
    coerceAdminProcessRef,
    coerceAdminResourceRef,
    type AdminProcessRef,
    type AdminResourceRef,
} from "@v8/session-realtime";
import { buildSignedClientSurfaceUrl } from "@/lib/server/client-surface-resource";

type JsonRecord = Record<string, unknown>;
type SurfaceNormalizationOptions = {
    publicBaseUrl?: string;
    compactPhone?: boolean;
    runtimeTimelineLimit?: number;
};

const DEFAULT_PHONE_RUNTIME_TIMELINE_LIMIT = 160;

const SURFACE_URL_PATTERN = /https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\/[^\s"'<>\\]+/gi;
const SURFACE_RELATIVE_URL_PATTERN = /(?:^|[\s("'=])((?:\/)?(?:workspace\/[^\s"'<>\\]+|api\/workspace\/files\/[^\s"'<>\\]+|api\/client\/workspace\/files\/[^\s"'<>\\]+|api\/workspace\/resource\?[^\s"'<>\\]+|api\/client\/workspace\/resource\?[^\s"'<>\\]+|(?:v1|api(?:\/client)?)\/artifacts\/[^/\s"'<>\\]+\/content(?:\?[^\s"'<>\\]+)?))/gi;

function asRecord(value: unknown): JsonRecord {
    return value && typeof value === "object" ? value as JsonRecord : {};
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

function canonicalResourceRefFromArtifactRecord(record: JsonRecord) {
    const explicit = coerceAdminResourceRef(record.resourceRef || record.resource_ref);
    if (explicit) {
        return explicit;
    }
    const artifactId = String(record.artifactId || record.artifact_id || "").trim();
    if (artifactId) {
        return coerceAdminResourceRef({
            kind: "artifact_content",
            artifactId,
            mimeType: record.mimeType,
            displayLabel: record.displayLabel || record.title,
            displaySubtitle: record.displaySubtitle,
            surfaceVisible: record.surfaceVisible,
        });
    }
    const externalUrl = String(record.previewUrl || record.preview_url || record.externalUrl || record.external_url || record.url || "").trim();
    return externalUrl ? coerceAdminResourceRef(externalUrl) : null;
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
            previewBlockedReason: "surface_unreachable",
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

function readString(...values: unknown[]) {
    for (const value of values) {
        if (typeof value === "string" && value.trim()) {
            return value.trim();
        }
        if (typeof value === "number" && Number.isFinite(value)) {
            return String(value);
        }
    }
    return "";
}

function readNestedString(record: JsonRecord, path: string) {
    let current: unknown = record;
    for (const part of path.split(".")) {
        current = asRecord(current)[part];
    }
    return readString(current);
}

function buildRuntimeEventDedupeKey(record: JsonRecord, data: JsonRecord, payload: JsonRecord) {
    const topic = readString(record.topic, data.topic, payload.topic, record.name).toLowerCase();
    const runId = readString(record.run_id, record.runId, data.run_id, data.runId, payload.run_id, payload.runId, "run");
    const runtimeId = readString(record.runtimeId, record.runtime_id, data.runtimeId, data.runtime_id, payload.runtimeId, payload.runtime_id, "runtime");
    const status = readString(record.status, data.status, payload.status, "state");
    const explicit = readString(record.dedupeKey, data.dedupeKey, data.dedupe_key, payload.dedupeKey, payload.dedupe_key);
    if (explicit) {
        return explicit;
    }
    if (topic.startsWith("runtime.episode.")) {
        const episodeId = readString(
            readNestedString(data, "episode.episodeId"),
            readNestedString(data, "episode.episode_id"),
            readNestedString(data, "episode.id"),
            readNestedString(payload, "episode.episodeId"),
            readNestedString(payload, "episode.episode_id"),
            readNestedString(payload, "episode.id"),
            data.episodeId,
            data.episode_id,
            payload.episodeId,
            payload.episode_id,
            record.event_id,
        );
        return `runtime-episode:${runId}:${episodeId || topic}:${topic}:${status}`;
    }
    if (topic.startsWith("delegation.") || topic.startsWith("subagent.")) {
        const dispatchGroup = readString(data.dispatchGroup, data.dispatch_group, payload.dispatchGroup, payload.dispatch_group, data.episodeId, payload.episodeId);
        const summary = readString(data.summary, data.message, payload.summary, payload.message, record.content);
        if (dispatchGroup || /missing|未形成|未确认/i.test(summary)) {
            return `delegation:${runId}:${dispatchGroup || topic}:${topic}:${status}`;
        }
    }
    if (topic.endsWith(".delta") || topic === "run.text.delta" || topic === "run.reasoning.delta") {
        return `stream:${runId}:${runtimeId}:${topic}`;
    }
    return "";
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

    const resourceRef = attachSignedSurfaceUrl(canonicalResourceRefFromArtifactRecord(record), options);
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
    const artifacts = Array.isArray(record.artifacts)
        ? record.artifacts.map((artifact) => normalizeArtifactForRealtimeSurface(artifact, options))
        : record.artifacts;
    const images = Array.isArray(record.images)
        ? record.images
            .map((entry) => normalizeSurfaceUrl(entry, options) || String(entry || "").trim())
            .filter(Boolean)
        : record.images;

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
    const metadataResourceRef = attachSignedSurfaceUrl(coerceAdminResourceRef(metadata.resourceRef || metadata.resource_ref), options);
    const normalizedMetadata = {
        ...metadata,
        resourceRef: metadataResourceRef || metadata.resourceRef || metadata.resource_ref,
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

    const normalizeRuntimeTimeline = (value: unknown) => {
        if (!Array.isArray(value)) {
            return value;
        }
        const normalized = value.map((entry) => normalizeRuntimeTimelineEntry(entry, options));
        if (!options?.compactPhone) {
            return normalized;
        }
        const limit = Math.max(1, Number(options.runtimeTimelineLimit || DEFAULT_PHONE_RUNTIME_TIMELINE_LIMIT) || DEFAULT_PHONE_RUNTIME_TIMELINE_LIMIT);
        return normalized
            .slice()
            .sort((left, right) => {
                const leftRecord = asRecord(left);
                const rightRecord = asRecord(right);
                const leftSeq = Number(leftRecord.seq || 0) || 0;
                const rightSeq = Number(rightRecord.seq || 0) || 0;
                if (leftSeq !== rightSeq) {
                    return rightSeq - leftSeq;
                }
                const leftTime = Date.parse(String(leftRecord.timestamp || ""));
                const rightTime = Date.parse(String(rightRecord.timestamp || ""));
                return (Number.isFinite(rightTime) ? rightTime : 0) - (Number.isFinite(leftTime) ? leftTime : 0);
            })
            .slice(0, limit);
    };
    const normalizeProcesses = (value: unknown) => Array.isArray(value)
        ? value
            .map((item) => normalizeProcessForRealtimeSurface(item))
            .filter(Boolean)
        : value;
    const normalizeMessages = (value: unknown) => Array.isArray(value)
        ? value.map((message) => normalizeMessageForRealtimeSurface(message, options))
        : value;
    const normalizeArtifacts = (value: unknown) => Array.isArray(value)
        ? value.map((artifact) => normalizeArtifactForRealtimeSurface(artifact, options))
        : value;
    const normalizeContextReferences = (value: unknown) => Array.isArray(value)
        ? value.map((item) => normalizeContextReference(item, options))
        : value;

    const normalizedProjection = Object.keys(projection).length ? {
        ...projection,
        messages: options?.compactPhone ? undefined : normalizeMessages(projection.messages),
        artifacts: normalizeArtifacts(projection.artifacts),
        runtimeTimeline: options?.compactPhone ? undefined : normalizeRuntimeTimeline(projection.runtimeTimeline),
        processes: options?.compactPhone ? undefined : normalizeProcesses(projection.processes),
        contextReferences: normalizeContextReferences(projection.contextReferences),
    } : record.projection;

    const normalizedWorkflowProjection = Object.keys(workflowProjection).length ? {
        ...workflowProjection,
        artifacts: normalizeArtifacts(workflowProjection.artifacts),
        runtimeTimeline: normalizeRuntimeTimeline(workflowProjection.runtimeTimeline),
    } : record.workflowProjection;

    const normalizedSnapshot = Object.keys(snapshot).length ? {
        ...snapshot,
        messages: normalizeMessages(snapshot.messages),
        artifacts: normalizeArtifacts(snapshot.artifacts),
        runtimeTimeline: normalizeRuntimeTimeline(snapshot.runtimeTimeline),
        processes: normalizeProcesses(snapshot.processes),
        contextReferences: normalizeContextReferences(snapshot.contextReferences),
    } : record.snapshot;

    const normalized: JsonRecord = {
        ...record,
        messages: options?.compactPhone ? undefined : normalizeMessages(record.messages),
        artifacts: normalizeArtifacts(record.artifacts),
        runtimeTimeline: options?.compactPhone ? undefined : normalizeRuntimeTimeline(record.runtimeTimeline),
        processes: options?.compactPhone ? undefined : normalizeProcesses(record.processes),
        contextReferences: normalizeContextReferences(record.contextReferences),
        projection: normalizedProjection,
        workflowProjection: normalizedWorkflowProjection,
        snapshot: normalizedSnapshot,
    };
    if (options?.compactPhone) {
        normalized.runtimeTimelineWindow = {
            limit: Math.max(1, Number(options.runtimeTimelineLimit || DEFAULT_PHONE_RUNTIME_TIMELINE_LIMIT) || DEFAULT_PHONE_RUNTIME_TIMELINE_LIMIT),
            sourceCount: Array.isArray(snapshot.runtimeTimeline)
                ? snapshot.runtimeTimeline.length
                : Array.isArray(record.runtimeTimeline)
                    ? record.runtimeTimeline.length
                    : 0,
        };
    }
    return normalized;
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
    const dedupeKey = buildRuntimeEventDedupeKey(record, data, payload);

    return {
        ...record,
        dedupeKey: dedupeKey || record.dedupeKey,
        content: normalizedContent,
        artifact: record.artifact
            ? normalizeArtifactForRealtimeSurface(record.artifact, options)
            : record.artifact,
        data: Object.keys(data).length ? {
            ...data,
            content: normalizeSurfaceContent(data.content, options),
            message: normalizeSurfaceContent(data.message, options),
            summary: normalizeSurfaceContent(data.summary, options),
            artifact: data.artifact
                ? normalizeArtifactForRealtimeSurface(data.artifact, options)
                : data.artifact,
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
                    : payload.artifact,
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
