export type TextLayoutEngineKind = "dom" | "pretext";

export type TextLayoutEngineCapabilities = {
    kind: TextLayoutEngineKind;
    frameBatched: boolean;
    supportsPrecomputedLayout: boolean;
    supportsCanvasMeasurement: boolean;
};

const DEFAULT_ENGINE: TextLayoutEngineCapabilities = {
    kind: "dom",
    frameBatched: true,
    supportsPrecomputedLayout: false,
    supportsCanvasMeasurement: true,
};

export function resolveTextLayoutEngine(): TextLayoutEngineCapabilities {
    return DEFAULT_ENGINE;
}

export function shouldUseStreamingPlainTextRenderer(engine: TextLayoutEngineCapabilities, isStreaming: boolean) {
    return isStreaming && engine.kind === "dom";
}
