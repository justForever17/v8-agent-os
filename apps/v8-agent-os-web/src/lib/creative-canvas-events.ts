export type CreativeCanvasOperationState =
    | "reserved"
    | "running"
    | "waiting"
    | "ready"
    | "failed"
    | "cancelled";

export type CreativeCanvasProjectedArtifact = {
    artifactId: string;
    resourceRef?: Record<string, unknown>;
    title: string;
    mimeType: string;
    sessionId: string;
    runId?: string;
    messageId?: string;
    toolCallId?: string;
    jobId?: string;
    canvasOperationId?: string;
    outputRole?: string;
    outputIndex?: number;
};

export type CreativeCanvasAgentOperation = {
    operationKey: string;
    canvasOperationId: string;
    actionId: string;
    label: string;
    state: CreativeCanvasOperationState;
    runId?: string;
    toolCallIds: string[];
    jobIds: string[];
    artifacts: CreativeCanvasProjectedArtifact[];
};

export type CreativeCanvasEventProjection = {
    operations: CreativeCanvasAgentOperation[];
    unplacedArtifacts: CreativeCanvasProjectedArtifact[];
};

type ProjectionInput = {
    sessionId: string;
    messages: readonly unknown[];
    artifacts: readonly unknown[];
};

const UNSAFE_RESOURCE_VALUE = /^(?:https?:|data:|blob:|file:|[a-z]:[\\/]|\\\\|\/)/i;

function recordOf(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

function stringOf(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

function numberOf(value: unknown): number | undefined {
    const result = Number(value);
    return Number.isFinite(result) && result >= 0 ? result : undefined;
}

function nestedRecords(value: unknown, depth = 0): Record<string, unknown>[] {
    if (depth > 3) return [];
    const record = recordOf(value);
    if (!Object.keys(record).length) return [];
    const children = [
        record.metadata,
        record.data,
        record.args,
        record.input,
        record.output,
        record.result,
        record.payload,
        record.lineage,
        record.provenance,
    ];
    return [record, ...children.flatMap((item) => nestedRecords(item, depth + 1))];
}

function firstString(records: readonly Record<string, unknown>[], ...keys: string[]): string {
    for (const record of records) {
        for (const key of keys) {
            const result = stringOf(record[key]);
            if (result) return result;
        }
    }
    return "";
}

function safeResourceRef(value: unknown): Record<string, unknown> | undefined {
    const source = recordOf(value);
    if (!Object.keys(source).length) return undefined;
    const safe: Record<string, unknown> = {};
    for (const [key, entry] of Object.entries(source)) {
        if (typeof entry === "string") {
            if (!UNSAFE_RESOURCE_VALUE.test(entry.trim())) safe[key] = entry;
            continue;
        }
        if (typeof entry === "boolean" || typeof entry === "number") safe[key] = entry;
    }
    return Object.keys(safe).length ? safe : undefined;
}

function artifactMediaType(records: readonly Record<string, unknown>[]): string {
    const explicitMime = firstString(records, "mimeType", "mime_type", "contentType", "content_type");
    if (explicitMime) return explicitMime;
    const kind = firstString(records, "mediaType", "media_type", "kind", "outputKind", "output_kind").toLowerCase();
    if (kind.includes("image")) return "image/*";
    if (kind.includes("video")) return "video/*";
    if (kind.includes("audio") || kind.includes("music") || kind.includes("voice")) return "audio/*";
    if (kind.includes("model") || kind.includes("3d")) return "model/gltf-binary";
    if (kind.includes("text")) return "text/plain";
    return "application/octet-stream";
}

function normalizeArtifact(raw: unknown, sessionId: string): CreativeCanvasProjectedArtifact | null {
    const records = nestedRecords(raw);
    if (!records.length) return null;
    const resourceRole = firstString(records, "resourceRole", "resource_role").toLowerCase();
    if (resourceRole && resourceRole !== "artifact") return null;
    const artifactId = firstString(records, "artifactId", "artifact_id", "id");
    if (!artifactId) return null;
    const explicitSessionId = firstString(records, "sessionId", "session_id");
    if (!explicitSessionId || explicitSessionId !== sessionId) return null;
    const outputIndex = numberOf(records.flatMap((record) => [record.outputIndex, record.output_index]).find((item) => item !== undefined));
    const resourceCandidate = records.map((record) => record.resourceRef || record.resource_ref).find(Boolean);
    return {
        artifactId,
        resourceRef: safeResourceRef(resourceCandidate),
        title: firstString(records, "displayLabel", "display_label", "title", "name") || "未命名产物",
        mimeType: artifactMediaType(records),
        sessionId,
        runId: firstString(records, "runId", "run_id") || undefined,
        messageId: firstString(records, "messageId", "message_id") || undefined,
        toolCallId: firstString(records, "toolCallId", "tool_call_id", "toolInvocationId", "tool_invocation_id") || undefined,
        jobId: firstString(records, "jobId", "job_id") || undefined,
        canvasOperationId: firstString(records, "canvasOperationId", "canvas_operation_id") || undefined,
        outputRole: firstString(records, "outputRole", "output_role", "outputSlot", "output_slot", "slot") || undefined,
        outputIndex,
    };
}

function operationKey(sessionId: string, operation: Pick<CreativeCanvasAgentOperation, "canvasOperationId" | "runId">): string {
    return [sessionId, operation.runId || "no-run", operation.canvasOperationId].join(":");
}

function operationStateFromTool(node: Record<string, unknown>): CreativeCanvasOperationState | null {
    const records = nestedRecords(node);
    const state = firstString(records, "status", "state", "phase").toLowerCase();
    const executionType = stringOf(node.executionType || node.execution_type).toLowerCase();
    if (["cancelled", "canceled", "interrupted", "aborted"].includes(state)) return "cancelled";
    if (["failed", "error", "rejected"].includes(state) || executionType === "tool_error") return "failed";
    if (["completed", "succeeded", "success", "ready", "done"].includes(state)) return "waiting";
    if (executionType === "tool_result") return "waiting";
    if (executionType === "tool_call" || state === "running" || state === "in_progress") return "running";
    return null;
}

function canvasMentions(message: Record<string, unknown>): Array<Record<string, unknown>> {
    const metadata = recordOf(message.metadata);
    const mentions = Array.isArray(metadata.contextMentions)
        ? metadata.contextMentions
        : Array.isArray(metadata.context_mentions)
            ? metadata.context_mentions
            : [];
    return mentions
        .map(recordOf)
        .filter((item) => stringOf(item.kind).toLowerCase() === "canvas_operation");
}

function messageNodes(message: Record<string, unknown>): Record<string, unknown>[] {
    return Array.isArray(message.nodes) ? message.nodes.map(recordOf) : [];
}

function artifactIdentity(artifact: CreativeCanvasProjectedArtifact): string {
    return [
        artifact.sessionId,
        artifact.runId || "",
        artifact.toolCallId || "",
        artifact.canvasOperationId || "",
        artifact.jobId || "",
        artifact.artifactId,
        artifact.outputRole || "",
        artifact.outputIndex ?? "",
    ].join(":");
}

function bindMessageArtifact(raw: unknown, sessionId: string): Record<string, unknown> | null {
    const records = nestedRecords(raw);
    if (!records.length) return null;
    const explicitSessionId = firstString(records, "sessionId", "session_id");
    if (explicitSessionId && explicitSessionId !== sessionId) return null;
    return { ...recordOf(raw), sessionId };
}

/**
 * Conservatively projects chat/runtime evidence onto the canvas. Artifacts are
 * attached only when an explicit canvas operation, tool call or job identifier
 * proves the relationship. A shared run id alone is deliberately insufficient.
 */
export function buildCreativeCanvasEventProjection(input: ProjectionInput): CreativeCanvasEventProjection {
    const sessionId = stringOf(input.sessionId);
    if (!sessionId) return { operations: [], unplacedArtifacts: [] };

    const operations = new Map<string, CreativeCanvasAgentOperation>();
    const operationById = new Map<string, CreativeCanvasAgentOperation>();
    const operationByToolCall = new Map<string, CreativeCanvasAgentOperation>();
    const operationByJob = new Map<string, CreativeCanvasAgentOperation>();
    const artifactCandidates: unknown[] = [...input.artifacts];

    for (const rawMessage of input.messages) {
        const message = recordOf(rawMessage);
        const messageSessionId = firstString(nestedRecords(message), "sessionId", "session_id");
        if (messageSessionId && messageSessionId !== sessionId) continue;
        const runId = stringOf(message.runId || message.run_id) || undefined;
        for (const mention of canvasMentions(message)) {
            const canvasOperationId = stringOf(mention.id || mention.canvasOperationId || mention.canvas_operation_id);
            if (!canvasOperationId) continue;
            const current: CreativeCanvasAgentOperation = {
                operationKey: "",
                canvasOperationId,
                actionId: stringOf(mention.sourceType || mention.source_type) || "message.submit_selection",
                label: stringOf(mention.label || mention.name) || "画布任务",
                state: "reserved",
                runId,
                toolCallIds: [],
                jobIds: [],
                artifacts: [],
            };
            current.operationKey = operationKey(sessionId, current);
            operations.set(current.operationKey, current);
            operationById.set(canvasOperationId, current);
        }

        for (const node of messageNodes(message)) {
            if (stringOf(node.kind) === "artifact") {
                const candidate = bindMessageArtifact(node.artifact || node, sessionId);
                if (candidate) artifactCandidates.push(candidate);
            }
            if (stringOf(node.kind) !== "execution") continue;
            const records = nestedRecords(node);
            const canvasOperationId = firstString(records, "canvasOperationId", "canvas_operation_id");
            const toolCallId = firstString(records, "toolCallId", "tool_call_id", "toolInvocationId", "tool_invocation_id");
            const jobId = firstString(records, "jobId", "job_id");
            const operation = (canvasOperationId && operationById.get(canvasOperationId))
                || (toolCallId && operationByToolCall.get(toolCallId))
                || (jobId && operationByJob.get(jobId));
            if (!operation) continue;
            if (toolCallId && !operation.toolCallIds.includes(toolCallId)) {
                operation.toolCallIds.push(toolCallId);
                operationByToolCall.set(toolCallId, operation);
            }
            if (jobId && !operation.jobIds.includes(jobId)) {
                operation.jobIds.push(jobId);
                operationByJob.set(jobId, operation);
            }
            const projectedState = operationStateFromTool(node);
            if (projectedState) operation.state = projectedState;
        }

        if (Array.isArray(message.artifacts)) {
            for (const rawArtifact of message.artifacts) {
                const candidate = bindMessageArtifact(rawArtifact, sessionId);
                if (candidate) artifactCandidates.push(candidate);
            }
        }
    }

    const normalizedArtifacts = new Map<string, CreativeCanvasProjectedArtifact>();
    for (const rawArtifact of artifactCandidates) {
        const artifact = normalizeArtifact(rawArtifact, sessionId);
        if (artifact) normalizedArtifacts.set(artifactIdentity(artifact), artifact);
    }

    const unplacedArtifacts: CreativeCanvasProjectedArtifact[] = [];
    for (const artifact of normalizedArtifacts.values()) {
        const operation = (artifact.canvasOperationId && operationById.get(artifact.canvasOperationId))
            || (artifact.toolCallId && operationByToolCall.get(artifact.toolCallId))
            || (artifact.jobId && operationByJob.get(artifact.jobId));
        if (!operation) {
            unplacedArtifacts.push(artifact);
            continue;
        }
        operation.artifacts.push(artifact);
        operation.state = "ready";
    }

    return {
        operations: [...operations.values()],
        unplacedArtifacts,
    };
}
