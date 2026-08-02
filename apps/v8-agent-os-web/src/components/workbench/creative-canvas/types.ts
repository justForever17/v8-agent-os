import type { CreativeCanvasAction, CreativeCanvasActionBinding, CreativeCanvasMediaType } from "@/lib/creative-canvas-actions";
import type { CreativeCanvasMaskState } from "../CreativeCanvasMaskEditor";
import type { CreativeCanvasMediaResource } from "../CreativeCanvasMedia";
import type { CanvasPsdComposition, CanvasPsdLayerEdit } from "../CreativeCanvasPsdEditor";

export type ResourceOrigin = "artifact" | "source" | "workspace_asset";

export type CanvasResource = CreativeCanvasMediaResource & {
    id: string;
    origin: ResourceOrigin;
    caption?: string;
    resourceRef?: Record<string, unknown>;
    workspacePath?: string;
    workspaceRelativePath?: string;
    runId?: string;
    messageId?: string;
    sourceKind?: string;
    size?: number;
    projectionRecord?: Record<string, unknown>;
    folderId?: string;
    adoptedByCurrentSession?: boolean;
};

export type CanvasNode = {
    nodeId: string;
    kind: "resource" | "input" | "action" | "result";
    origin: ResourceOrigin | "placeholder";
    resourceId?: string;
    x: number;
    y: number;
    width: number;
    height: number;
    operationId?: string;
    operationLabel?: string;
    operationState?: "reserved" | "running" | "waiting" | "ready" | "failed" | "cancelled";
    mask?: CreativeCanvasMaskState;
    title?: string;
    mediaType?: CreativeCanvasMediaType;
    acceptedMediaTypes?: CreativeCanvasMediaType[];
    actionDefinitionId?: string;
    prompt?: string;
    parameters?: Record<string, unknown>;
    configurationRevision?: number;
    producerActionNodeId?: string;
    outputSlot?: string;
};

export type CanvasPort = "left" | "right";

export type CanvasEdge = {
    edgeId: string;
    from: string;
    to: string;
    fromPort: CanvasPort;
    toPort: CanvasPort;
    fromPortId: string;
    toPortId: string;
    dataType: CreativeCanvasMediaType;
    role: "data" | "relation";
    order: number;
    note: string;
};

export type CanvasViewport = { x: number; y: number; scale: number };

export type CanvasSnapshot = {
    schema: "v8.creative_canvas_graph.v1";
    version: 3;
    graphId: string;
    nodes: CanvasNode[];
    edges: CanvasEdge[];
    viewport: CanvasViewport;
};

export type CanvasOperationRequest = {
    operationId: string;
    actionId: string;
    label: string;
    nodeIds: string[];
    edgeId?: string;
    outputKind: CreativeCanvasAction["output"]["kind"];
    outputSlot: string;
    maskRevision?: number;
    binding?: CreativeCanvasActionBinding;
    parameters?: Record<string, unknown>;
    edge?: {
        edgeId: string;
        fromNodeId: string;
        toNodeId: string;
        fromResourceId?: string;
        toResourceId?: string;
    };
};

export type CanvasTaskReference = Pick<CanvasResource,
    | "id"
    | "origin"
    | "name"
    | "mimeType"
    | "mediaType"
    | "url"
    | "caption"
    | "resourceRef"
    | "workspacePath"
    | "workspaceRelativePath"
    | "sourceKind"
    | "size"
>;

export type CanvasTaskRequest = {
    sessionId: string;
    text: string;
    refs: CanvasTaskReference[];
    operation: CanvasOperationRequest;
};

export type ContextMenuState = {
    x: number;
    y: number;
    target: import("@/lib/creative-canvas-actions").CreativeCanvasActionTarget;
    nodeIds: string[];
    edgeId?: string;
};

export type ComposerState = {
    x: number;
    y: number;
    action: CreativeCanvasAction;
    operationId: string;
    nodeIds: string[];
    edgeId?: string;
    text: string;
    timeRange?: CanvasTimeRange;
    psdComposition?: CanvasPsdComposition;
    psdEdits?: CanvasPsdLayerEdit[];
    actionNodeId?: string;
};

export type CanvasActionPort = {
    portId: string;
    mediaTypes: CreativeCanvasMediaType[];
    min: number;
    max: number;
    ordered: boolean;
};

export type CanvasActionDefinition = {
    actionId: string;
    inputs: CanvasActionPort[];
    output: { portId: string; slot: string; mediaTypes: CreativeCanvasMediaType[] };
    requiresPrompt: boolean;
    parameterEditor?: "frame_pick" | "time_range" | "psd_composition" | "psd_layers";
    networkRequired?: boolean;
    mayIncurCost?: boolean;
};

export type CanvasOutputVersion = {
    outputVersionId: string;
    version: number;
    artifactId?: string;
    jobId?: string;
    mediaType?: CreativeCanvasMediaType;
    createdAt?: string;
};

export type CanvasGraphRuntime = {
    graphRunId?: string;
    status: string;
    currentNodeId?: string;
    error?: string;
    nodeStates: Record<string, Record<string, unknown>>;
    outputs: Record<string, CanvasOutputVersion[]>;
    updatedAt?: string;
};

export type CanvasGraphHistory = {
    canUndo: boolean;
    canRedo: boolean;
    undoDepth: number;
    redoDepth: number;
    lastCommand?: {
        commandId?: string;
        direction?: "forward" | "undo" | "redo";
        kind?: string;
        createdAt?: string;
    };
};

export type CanvasWorkflowTemplate = {
    templateId: string;
    title: string;
    description?: string;
    inputCount: number;
    updatedAt?: string;
};

export type EdgeNoteState = { edgeId: string; x: number; y: number; text: string };

export type CanvasTimeRange = {
    unit?: "frame" | "sample";
    count: number;
    startIndex: number;
    endIndexExclusive: number;
    durationSeconds: string;
    timeBaseNumerator: number;
    timeBaseDenominator: number;
    averageFrameRateNumerator?: number;
    averageFrameRateDenominator?: number;
    boundaryTicks?: number[];
    probeFingerprint?: string;
    displayPrecision: number;
    exact?: boolean;
    loading: boolean;
    error?: string;
};

export type WorkspaceMediaFolder = {
    folderId: string;
    parentFolderId?: string;
    folderKind: "production" | "episode" | "sources" | "work" | "outputs" | "delivery" | "custom";
    title: string;
    assetCount: number;
};

export type SelectionRect = { startX: number; startY: number; x: number; y: number };

export type ConnectionDraft = {
    fromNodeId: string;
    fromPort: CanvasPort;
    target: { x: number; y: number };
    gesture:
        | { kind: "connect"; fromNodeId: string; fromPort: CanvasPort }
        | { kind: "reconnect"; edgeId: string; movingEnd: "from" | "to"; fixedNodeId: string; fixedPort: CanvasPort };
};

export type PendingConnectionDrop = {
    fromNodeId: string;
    fromPort: CanvasPort;
    direction: "input" | "output";
    point: { x: number; y: number };
};

export type PointerInteraction =
    | { kind: "select"; pointerId: number; start: { x: number; y: number }; additive: boolean }
    | { kind: "move"; pointerId: number; start: { x: number; y: number }; initial: Map<string, { x: number; y: number }> }
    | { kind: "pan"; pointerId: number; start: { x: number; y: number }; initial: CanvasViewport }
    | { kind: "connect"; pointerId: number; fromNodeId: string; fromPort: CanvasPort }
    | { kind: "reconnect"; pointerId: number; edgeId: string; movingEnd: "from" | "to"; fixedNodeId: string; fixedPort: CanvasPort };

export const MAX_NODES = 160;
export const MAX_EDGES = 320;
export const NODE_WIDTH = 280;
export const NODE_HEIGHT = 190;
export const MEDIA_FOOTER_HEIGHT = 36;
export const GRID_COLUMN_STEP = 452;
export const GRID_ROW_STEP = 428;
export const CANVAS_DRAG_TYPE = "application/x-v8-creative-canvas-resource";
export const MODEL_ACCEPT = "image/*,video/*,audio/*,.psd,.glb,.gltf,.obj,.fbx,.stl,.usd,.usdz,.pdf,.txt,.md,.json";
export const EMPTY_SNAPSHOT: CanvasSnapshot = {
    schema: "v8.creative_canvas_graph.v1",
    version: 3,
    graphId: "",
    nodes: [],
    edges: [],
    viewport: { x: 24, y: 24, scale: 1 },
};
export const EMPTY_GRAPH_RUNTIME: CanvasGraphRuntime = { status: "idle", nodeStates: {}, outputs: {} };
export const EMPTY_GRAPH_HISTORY: CanvasGraphHistory = { canUndo: false, canRedo: false, undoDepth: 0, redoDepth: 0 };
