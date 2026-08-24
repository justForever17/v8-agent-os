import {
    coerceAdminProcessRef,
    coerceAdminResourceRef,
    deriveAdminResourceRefFromArtifactLike,
    type AdminProcessRef,
    type AdminResourceRef,
} from "@v8/session-realtime";
import { buildSignedClientSurfaceUrl } from "@/lib/server/client-surface-resource";
import {
    buildRuntimeEventDedupeKey,
    shouldOmitRealtimeRawEnvelope,
} from "@/lib/server/runtime-event-delivery";

type JsonRecord = Record<string, unknown>;
type SurfaceNormalizationOptions = {
    publicBaseUrl?: string;
    compactPhone?: boolean;
    compactSurface?: boolean;
    runtimeTimelineLimit?: number;
};

const DEFAULT_PHONE_RUNTIME_TIMELINE_LIMIT = 160;
const COMPACT_SURFACE_VALUE_LIMIT = 12_000;
const COMPACT_SURFACE_STRING_LIMIT = 8_000;
const COMPACT_SURFACE_KEYS = [
    "ok", "status", "state", "kind", "mode", "summary", "message", "error", "errorCode",
    "detailRef", "resultRef", "artifactRefs", "proofRefs", "sessionId", "runId", "episodeId",
    "taskId", "specId", "workspacePath", "path", "command", "returnCode", "finalPreview",
    "stdoutPreview", "stderrPreview", "count", "items", "params", "target", "action",
];

const SURFACE_URL_PATTERN = /https?:\/\/(?:127(?:\.\d{1,3}){3}|localhost|\[::1\])(?::\d+)?\/[^\s"'<>\\]+/gi;
const SURFACE_RELATIVE_URL_PATTERN = /(?:^|[\s("'=])((?:\/)?(?:workspace\/[^\s"'<>\\]+|api\/workspace\/files\/[^\s"'<>\\]+|api\/client\/workspace\/files\/[^\s"'<>\\]+|api\/workspace\/resource\?[^\s"'<>\\]+|api\/client\/workspace\/resource\?[^\s"'<>\\]+|(?:v1|api(?:\/client)?)\/artifacts\/[^/\s"'<>\\]+\/content(?:\?[^\s"'<>\\]+)?))/gi;

function asRecord(value: unknown): JsonRecord {
    return value && typeof value === "object" ? value as JsonRecord : {};
}

function isCompactSurface(options?: SurfaceNormalizationOptions) {
    return Boolean(options?.compactPhone || options?.compactSurface);
}

function compactSurfaceValue(value: unknown, depth = 0): unknown {
    if (typeof value === "string") {
        return value.length > COMPACT_SURFACE_STRING_LIMIT
            ? `${value.slice(0, COMPACT_SURFACE_STRING_LIMIT)}\n...[surface truncated ${value.length - COMPACT_SURFACE_STRING_LIMIT} chars]`
            : value;
    }
    if (value === null || value === undefined || typeof value !== "object") {
        return value;
    }
    if (depth >= 3) {
        return Array.isArray(value)
            ? { surfaceCompacted: true, itemCount: value.length }
            : { surfaceCompacted: true, availableKeys: Object.keys(asRecord(value)).slice(0, 20) };
    }
    let serialized = "";
    try {
        serialized = JSON.stringify(value);
    } catch {
        return { surfaceCompacted: true, summary: "Structured value could not be serialized." };
    }
    if (serialized.length <= COMPACT_SURFACE_VALUE_LIMIT) {
        if (Array.isArray(value)) {
            return value.map((item) => compactSurfaceValue(item, depth + 1));
        }
        return Object.fromEntries(
            Object.entries(asRecord(value)).map(([key, item]) => [key, compactSurfaceValue(item, depth + 1)]),
        );
    }
    if (Array.isArray(value)) {
        return {
            surfaceCompacted: true,
            itemCount: value.length,
            items: value.slice(0, 8).map((item) => compactSurfaceValue(item, depth + 1)),
        };
    }
    const record = asRecord(value);
    const compact: JsonRecord = { surfaceCompacted: true };
    for (const key of COMPACT_SURFACE_KEYS) {
        if (record[key] !== undefined) {
            compact[key] = compactSurfaceValue(record[key], depth + 1);
        }
    }
    compact.availableKeys = Object.keys(record).slice(0, 40);
    return compact;
}

function normalizeRunForRealtimeSurface(raw: unknown, options?: SurfaceNormalizationOptions) {
    const record = asRecord(raw);
    if (!Object.keys(record).length || !isCompactSurface(options)) {
        return raw;
    }
    return {
        ...record,
        metadata: compactSurfaceValue(record.metadata),
    };
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
    return deriveAdminResourceRefFromArtifactLike({
        ...record,
        resourceRef: record.resourceRef || record.resource_ref,
    });
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

    if (isCompactSurface(options)) {
        const executionType = String(record.executionType || record.execution_type || "").trim();
        const toolName = String(record.toolName || record.tool_name || "").trim();
        if (executionType === "tool_result") {
            const visibleResult = record.agentVisibleResult ?? record.agent_visible_result;
            const preserveCommandResult = /(?:command|terminal|system_command)/i.test(toolName);
            nextNode.result = preserveCommandResult
                ? compactSurfaceValue(record.result)
                : compactSurfaceValue(visibleResult ?? record.result);
            if (visibleResult !== undefined) {
                nextNode.agentVisibleResult = normalizeSurfaceContent(visibleResult, options);
            }
        } else if (executionType === "tool_call" && record.args !== undefined) {
            nextNode.args = compactSurfaceValue(record.args);
        }
    }

    if (record.kind === "artifact" && record.artifact) {
        nextNode.artifact = normalizeArtifactForRealtimeSurface(record.artifact, options);
    }

    if (Object.keys(data).length) {
        const normalizedData = {
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
        nextNode.data = isCompactSurface(options)
            ? compactSurfaceValue(normalizedData)
            : normalizedData;
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

    const normalizedContent = normalizeSurfaceContent(record.content ?? record.content_text, options);
    const artifacts = Array.isArray(record.artifacts)
        ? record.artifacts.map((artifact) => normalizeArtifactForRealtimeSurface(artifact, options))
        : record.artifacts;
    const images = Array.isArray(record.images)
        ? record.images
            .map((entry) => normalizeSurfaceUrl(entry, options) || String(entry || "").trim())
            .filter(Boolean)
        : record.images;
    const hasStructuredNodes = Array.isArray(nodes) && nodes.length > 0;
    const toolInvocations = Array.isArray(record.toolInvocations)
        ? (
            isCompactSurface(options) && hasStructuredNodes
                ? undefined
                : record.toolInvocations.map((item) => {
                    const invocation = asRecord(item);
                    return {
                        ...invocation,
                        args: isCompactSurface(options) ? compactSurfaceValue(invocation.args) : invocation.args,
                        result: isCompactSurface(options) ? compactSurfaceValue(invocation.result) : invocation.result,
                    };
                })
        )
        : record.toolInvocations;

    const normalizedMessage: JsonRecord = {
        ...record,
        content: normalizedContent,
        reasoningContent: record.reasoningContent ?? record.reasoning_text,
        runId: record.runId ?? record.run_id,
        createdAt: record.createdAt ?? record.created_at,
        updatedAt: record.updatedAt ?? record.updated_at,
        nodes,
        parts,
        artifacts,
        images,
        toolInvocations,
    };
    if (isCompactSurface(options)) {
        delete normalizedMessage.nodes_json;
        delete normalizedMessage.artifacts_json;
        delete normalizedMessage.metadata_json;
        delete normalizedMessage.content_text;
        delete normalizedMessage.reasoning_text;
    }
    return normalizedMessage;
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
        metadata: isCompactSurface(options) ? compactSurfaceValue(normalizedMetadata) : normalizedMetadata,
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
        if (!isCompactSurface(options)) {
            return normalized;
        }
        const limit = Math.max(1, Number(options?.runtimeTimelineLimit || DEFAULT_PHONE_RUNTIME_TIMELINE_LIMIT) || DEFAULT_PHONE_RUNTIME_TIMELINE_LIMIT);
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
        messages: isCompactSurface(options) ? undefined : normalizeMessages(projection.messages),
        artifacts: normalizeArtifacts(projection.artifacts),
        runtimeTimeline: isCompactSurface(options) ? undefined : normalizeRuntimeTimeline(projection.runtimeTimeline),
        processes: isCompactSurface(options) ? undefined : normalizeProcesses(projection.processes),
        contextReferences: normalizeContextReferences(projection.contextReferences),
    } : record.projection;

    const normalizedWorkflowProjection = Object.keys(workflowProjection).length ? {
        ...workflowProjection,
        currentRun: normalizeRunForRealtimeSurface(workflowProjection.currentRun, options),
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
        messages: isCompactSurface(options) ? undefined : normalizeMessages(record.messages),
        artifacts: normalizeArtifacts(record.artifacts),
        runtimeTimeline: normalizeRuntimeTimeline(record.runtimeTimeline),
        processes: isCompactSurface(options) ? undefined : normalizeProcesses(record.processes),
        currentRun: normalizeRunForRealtimeSurface(record.currentRun, options),
        contextReferences: normalizeContextReferences(record.contextReferences),
        projection: normalizedProjection,
        workflowProjection: normalizedWorkflowProjection,
        snapshot: normalizedSnapshot,
    };
    if (isCompactSurface(options)) {
        const sourceWindow = asRecord(record.runtimeTimelineWindow);
        normalized.runtimeTimelineWindow = {
            limit: Math.max(1, Number(options?.runtimeTimelineLimit || DEFAULT_PHONE_RUNTIME_TIMELINE_LIMIT) || DEFAULT_PHONE_RUNTIME_TIMELINE_LIMIT),
            sourceCount: Number(sourceWindow.sourceCount || 0) || (Array.isArray(snapshot.runtimeTimeline)
                ? snapshot.runtimeTimeline.length
                : Array.isArray(record.runtimeTimeline)
                    ? record.runtimeTimeline.length
                    : 0),
            compacted: true,
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
    const omitRawEnvelope = shouldOmitRealtimeRawEnvelope(record);

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
        raw: omitRawEnvelope ? undefined : Object.keys(rawPayload).length ? {
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
