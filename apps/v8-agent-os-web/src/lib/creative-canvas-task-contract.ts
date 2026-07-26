export const CREATIVE_CANVAS_CONTRACT_START = "[CANVAS EXECUTION CONTRACT v1]";
export const CREATIVE_CANVAS_CONTRACT_END = "[/CANVAS EXECUTION CONTRACT]";

type CanvasContractReference = {
    id: string;
    origin: "source" | "artifact";
    mediaType?: string;
};

type CanvasContractBinding =
    | { kind: "canvas_message"; action: string }
    | { kind: "creative_media"; capability: string }
    | { kind: "mediakit"; pluginId: string; componentId: string; domain: string; action: string }
    | { kind: "runtime_event"; action: string };

type CanvasContractEdge = {
    edgeId: string;
    fromNodeId: string;
    toNodeId: string;
    fromResourceId?: string;
    toResourceId?: string;
};

type CanvasContractOperation = {
    operationId: string;
    actionId: string;
    outputKind: string;
    outputSlot: string;
    maskRevision?: number;
    binding?: CanvasContractBinding;
    edgeId?: string;
    edge?: CanvasContractEdge;
};

function unique(values: Array<string | undefined>) {
    return Array.from(new Set(values.map((value) => String(value || "").trim()).filter(Boolean)));
}

function modalityForOperation(operationKind: string) {
    const prefix = String(operationKind || "").split(".", 1)[0].trim().toLowerCase();
    return prefix === "audio" ? "voice" : prefix;
}

function canonicalCreativeMediaOperation(capability: string) {
    const value = String(capability || "").trim();
    if (value === "voice.generate") return "voice.tts";
    if (value === "video.keyframe_to_video") return "video.first_last_frame";
    return value;
}

/**
 * Build the compact canonical contract sent to Supervisor. It carries stable
 * ledger ids only; paths and preview URLs remain in governed attachments.
 */
export function buildCreativeCanvasExecutionContract(input: {
    instruction: string;
    refs: readonly CanvasContractReference[];
    operation: CanvasContractOperation;
}): Record<string, unknown> {
    const sourceRefs = input.refs.filter((reference) => reference.origin === "source");
    const artifactRefs = input.refs.filter((reference) => reference.origin === "artifact");
    const maskSourceId = sourceRefs.find((reference) => reference.mediaType === "mask")?.id;
    const sourceIds = unique(sourceRefs.filter((reference) => reference.mediaType !== "mask").map((reference) => reference.id));
    const artifactIds = unique(artifactRefs.map((reference) => reference.id));
    const contract: Record<string, unknown> = {
        schema: "v8.creative_canvas_task.v1",
        canvasOperationId: input.operation.operationId,
        actionId: input.operation.actionId,
        output: {
            kind: input.operation.outputKind,
            slot: input.operation.outputSlot,
        },
    };

    if (sourceIds.length || artifactIds.length || maskSourceId) {
        contract.resources = {
            ...(sourceIds.length ? { sourceIds } : {}),
            ...(artifactIds.length ? { artifactIds } : {}),
            ...(maskSourceId ? { maskSourceId } : {}),
        };
    }
    if (input.operation.maskRevision !== undefined) {
        contract.maskRevision = input.operation.maskRevision;
    }
    if (input.operation.edge) {
        contract.edge = input.operation.edge;
    } else if (input.operation.edgeId) {
        contract.edge = { edgeId: input.operation.edgeId };
    }

    const binding = input.operation.binding;
    if (binding?.kind === "creative_media") {
        const operationKind = canonicalCreativeMediaOperation(binding.capability);
        contract.execution = {
            tool: "creative_media_jobs",
            arguments: {
                action: "create",
                request: {
                    modality: modalityForOperation(operationKind),
                    operationKind,
                    prompt: input.instruction.trim(),
                    canvasOperationId: input.operation.operationId,
                    ...(sourceIds[0] ? { sourceId: sourceIds[0] } : {}),
                    ...(sourceIds.length > 1 ? { sourceIds } : {}),
                    ...(artifactIds[0] ? { artifactId: artifactIds[0] } : {}),
                    ...(artifactIds.length > 1 ? { artifactIds } : {}),
                    ...(maskSourceId ? { maskSourceId } : {}),
                },
            },
        };
    } else if (binding?.kind === "mediakit") {
        contract.execution = {
            tool: "plugin_cli",
            pluginId: binding.pluginId,
            profileId: binding.componentId,
            actionId: binding.action,
            domain: binding.domain,
            instruction: input.instruction.trim(),
            resourceBindings: {
                ...(sourceIds.length ? { sourceIds } : {}),
                ...(artifactIds.length ? { artifactIds } : {}),
            },
        };
    } else if (binding?.kind === "canvas_message") {
        contract.intent = binding.action;
        contract.instruction = input.instruction.trim();
    }

    return contract;
}

function recordOf(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

/**
 * composerPresentation is optimistic-only today. Recognize the authoritative
 * replay by its canonical envelope or attachment lineage so Human Surface never
 * exposes ids, paths, bindings, or source previews after a reload.
 */
export function isCreativeCanvasCanonicalMessage(
    content: unknown,
    metadata: unknown,
): boolean {
    const text = String(content || "");
    if (text.includes(CREATIVE_CANVAS_CONTRACT_START) || text.includes("[CANVAS OPERATION]")) {
        return true;
    }
    const meta = recordOf(metadata);
    const contextMentions = Array.isArray(meta.contextMentions) ? meta.contextMentions : [];
    if (contextMentions.some((item) => recordOf(item).kind === "canvas_operation")) {
        return true;
    }
    const attachments = Array.isArray(meta.attachments) ? meta.attachments : [];
    return attachments.some((item) => {
        const attachment = recordOf(item);
        const attachmentMetadata = recordOf(attachment.metadata);
        return Boolean(attachmentMetadata.canvasOperationId || attachmentMetadata.canvas_operation_id);
    });
}
