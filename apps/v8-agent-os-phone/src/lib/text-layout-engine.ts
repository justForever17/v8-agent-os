export type TextLayoutEngineKind = "native" | "pretext" | "skia";

export type TextLayoutEngineCapabilities = {
    kind: TextLayoutEngineKind;
    frameBatched: boolean;
    supportsPrecomputedLayout: boolean;
    supportsCanvasMeasurement: boolean;
};

const DEFAULT_ENGINE: TextLayoutEngineCapabilities = {
    kind: "native",
    frameBatched: true,
    supportsPrecomputedLayout: false,
    supportsCanvasMeasurement: false,
};

export function resolveTextLayoutEngine(): TextLayoutEngineCapabilities {
    return DEFAULT_ENGINE;
}

export function shouldUseStreamingPlainTextRenderer(engine: TextLayoutEngineCapabilities, isStreaming: boolean) {
    return isStreaming && engine.kind === "native";
}
