import { type AIModel } from "@/lib/model-hub/types";

export type ComfyWorkflowDraft = {
    promptJson: string;
    imageNodeId: string;
    imageInputName: string;
    videoNodeId: string;
    videoInputName: string;
    outputNodeId: string;
    outputField: string;
};

export const EMPTY_COMFY_WORKFLOW: ComfyWorkflowDraft = {
    promptJson: "",
    imageNodeId: "",
    imageInputName: "",
    videoNodeId: "",
    videoInputName: "",
    outputNodeId: "",
    outputField: "",
};

export function comfyWorkflowDraft(mediaLimits: Record<string, unknown> | null | undefined): ComfyWorkflowDraft {
    const workflow = mediaLimits?.comfyuiWorkflow && typeof mediaLimits.comfyuiWorkflow === "object"
        ? mediaLimits.comfyuiWorkflow as Record<string, unknown>
        : {};
    const bindings = workflow.bindings && typeof workflow.bindings === "object"
        ? workflow.bindings as Record<string, Record<string, unknown>>
        : {};
    const output = workflow.output && typeof workflow.output === "object"
        ? workflow.output as Record<string, unknown>
        : {};
    const prompt = workflow.prompt && typeof workflow.prompt === "object" ? workflow.prompt : null;
    return {
        promptJson: prompt ? JSON.stringify(prompt, null, 2) : "",
        imageNodeId: String(bindings.image?.nodeId || ""),
        imageInputName: String(bindings.image?.inputName || ""),
        videoNodeId: String(bindings.video?.nodeId || ""),
        videoInputName: String(bindings.video?.inputName || ""),
        outputNodeId: String(output.nodeId || ""),
        outputField: String(output.field || ""),
    };
}

export const MEDIA_MODEL_TYPES = new Set<string>(["MEDIA", "IMAGE", "VIDEO", "AUDIO", "VOICE", "MUSIC", "WORKFLOW", "MODEL3D"]);

export const RETRIEVAL_MODEL_TYPES = new Set<string>(["EMBEDDING", "RERANK", "RERANKER"]);

export function modelRefFor(model: AIModel): string {
    return model.modelRef || `${model.providerId}::${model.modelId || model.id}`;
}

export function modelLabel(model: AIModel): string {
    return `${model.provider?.name || model.providerId} · ${model.modelId || model.id}`;
}

export function normalizeModelType(value: string | null | undefined) {
    return String(value || "").trim().toUpperCase();
}

export function modelMatchesTab(model: AIModel, tab: string) {
    if (tab === "all") return true;
    const type = normalizeModelType(model.type);
    if (tab === "media") return MEDIA_MODEL_TYPES.has(type);
    if (tab === "voice") return type === "VOICE" || type === "AUDIO";
    if (tab === "model3d") return type === "MODEL3D";
    return type.toLowerCase() === tab;
}

export function preserveModelOrder(current: AIModel[], incoming: AIModel[]): AIModel[] {
    if (!current.length || !incoming.length) return incoming;
    const identity = (model: AIModel) => model.modelRef || `${model.providerId}:${model.id}`;
    const incomingByIdentity = new Map(incoming.map((model) => [identity(model), model]));
    const ordered = current
        .map((model) => incomingByIdentity.get(identity(model)))
        .filter((model): model is AIModel => Boolean(model));
    const known = new Set(ordered.map(identity));
    for (const model of incoming) {
        if (!known.has(identity(model))) ordered.push(model);
    }
    return ordered;
}
