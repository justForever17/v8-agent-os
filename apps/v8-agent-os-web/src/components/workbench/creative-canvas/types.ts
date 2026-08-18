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
    availability?: "available" | "unavailable";
    unavailableReason?: string;
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
    binding?: { kind: string; capability?: string };
    inputs: CanvasActionPort[];
    output: { portId: string; slot: string; mediaTypes: CreativeCanvasMediaType[] };
    requiresPrompt: boolean;
    parameterEditor?: "frame_pick" | "time_range" | "psd_composition" | "psd_layers";
    networkRequired?: boolean;
    mayIncurCost?: boolean;
    providerLabel?: string;
    modelLabel?: string;
};

export type CanvasOutputVersion = {
    outputVersionId: string;
    version: number;
    artifactId?: string;
    jobId?: string;
    mediaType?: CreativeCanvasMediaType;
    createdAt?: string;
    /** Session-authorized resource projection for this exact output version. */
    resource?: Partial<CanvasResource> & Record<string, unknown>;
    resourceRef?: Partial<CanvasResource> & Record<string, unknown>;
    resourceProjection?: Partial<CanvasResource> & Record<string, unknown>;
    /** Immutable provider/recipe/cost/QA proof for this exact output version. */
    proof?: CanvasOutputVersionProof;
    outputProof?: CanvasOutputVersionProof;
    review?: CanvasOutputVersionReview;
    status?: string;
};

export type CanvasOutputVersionReview = {
    decision?: "pending" | "approved" | "rejected" | string;
    revision?: number;
    note?: string;
    selectedForDelivery?: boolean;
    reviewedAt?: string;
    deliveryManifestArtifactId?: string;
    deliveredAt?: string;
};

export type CanvasDeliveryProjection = {
    status?: "ready" | "delivered" | "blocked" | "unavailable" | string;
    dryRun?: boolean;
    ready?: boolean;
    reason?: string;
    unavailableReason?: string;
    manifestArtifactId?: string;
    manifestDigest?: string;
    review?: CanvasOutputVersionReview;
    [key: string]: unknown;
};

export type CanvasOutputVersionProof = {
    available?: boolean;
    unavailableReason?: string;
    provider?: string;
    providerLabel?: string;
    model?: string;
    modelLabel?: string;
    recipeId?: string;
    operation?: string;
    operationKind?: string;
    elapsedMs?: number;
    durationMs?: number;
    cost?: number | string;
    currency?: string;
    quality?: Record<string, unknown>;
    qa?: Record<string, unknown>;
    status?: string;
    [key: string]: unknown;
};

export type CanvasGraphRuntime = {
    graphRunId?: string;
    canvasOperationId?: string;
    graphRevision?: number;
    targetNodeIds?: string[];
    status: string;
    currentNodeId?: string;
    error?: string;
    errorDetail?: { code?: string; message?: string };
    recovery?: { canRetry?: boolean; mode?: string; reason?: string };
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
