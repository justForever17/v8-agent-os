"use client";

import {
    useCallback,
    useEffect,
    useMemo,
    useRef,
    useState,
    type ChangeEvent,
    type DragEvent,
    type PointerEvent as ReactPointerEvent,
    type WheelEvent,
} from "react";
import {
    Archive,
    Box,
    Check,
    Download,
    Focus,
    Hand,
    ImagePlus,
    Link2,
    Loader2,
    Lock,
    MessageSquare,
    MousePointer2,
    PackageOpen,
    Plus,
    Send,
    Sparkles,
    Trash2,
    Upload,
    X,
    ZoomIn,
    ZoomOut,
} from "lucide-react";

import { useT } from "@/components/providers/LocaleProvider";
import { cn } from "@/lib/utils";
import { isTranslationKey } from "@/lib/locale";
import type { CreativeCanvasWorkbenchDocument } from "@/lib/workbench";
import type { Message } from "@/store/chat-types";
import {
    CREATIVE_CANVAS_ACTIONS,
    getCreativeCanvasActions,
    type CreativeCanvasAction,
    type CreativeCanvasActionBinding,
    type CreativeCanvasActionTarget,
    type CreativeCanvasMediaType,
} from "@/lib/creative-canvas-actions";
import { buildCreativeCanvasEventProjection } from "@/lib/creative-canvas-events";
import {
    CreativeCanvasMaskEditor,
    CreativeCanvasMaskOverlay,
    rasterizeCreativeCanvasMask,
    type CreativeCanvasMaskState,
} from "./CreativeCanvasMaskEditor";
import {
    CreativeCanvasMedia,
    creativeCanvasMediaType,
    type CreativeCanvasMediaResource,
} from "./CreativeCanvasMedia";

type ResourceOrigin = "artifact" | "source";

type CanvasResource = CreativeCanvasMediaResource & {
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
};

type CanvasNode = {
    nodeId: string;
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
};

type CanvasEdge = {
    edgeId: string;
    from: string;
    to: string;
    fromPort: CanvasPort;
    toPort: CanvasPort;
};

type CanvasPort = "left" | "right";

type CanvasViewport = { x: number; y: number; scale: number };

type CanvasSnapshot = {
    version: 2;
    nodes: CanvasNode[];
    edges: CanvasEdge[];
    viewport: CanvasViewport;
};

type CanvasOperationRequest = {
    operationId: string;
    actionId: string;
    label: string;
    nodeIds: string[];
    edgeId?: string;
    outputKind: CreativeCanvasAction["output"]["kind"];
    outputSlot: string;
    maskRevision?: number;
    binding?: CreativeCanvasActionBinding;
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
    text: string;
    refs: CanvasTaskReference[];
    operation: CanvasOperationRequest;
};

type ContextMenuState = {
    x: number;
    y: number;
    target: CreativeCanvasActionTarget;
    nodeIds: string[];
    edgeId?: string;
};

type ComposerState = {
    x: number;
    y: number;
    action: CreativeCanvasAction;
    operationId: string;
    nodeIds: string[];
    edgeId?: string;
    text: string;
};

type SelectionRect = { startX: number; startY: number; x: number; y: number };

type ConnectionDraft = {
    fromNodeId: string;
    fromPort: CanvasPort;
    target: { x: number; y: number };
};

type PendingConnectionDrop = {
    fromNodeId: string;
    fromPort: CanvasPort;
    point: { x: number; y: number };
};

type PointerInteraction =
    | { kind: "select"; pointerId: number; start: { x: number; y: number }; additive: boolean }
    | { kind: "move"; pointerId: number; start: { x: number; y: number }; initial: Map<string, { x: number; y: number }> }
    | { kind: "pan"; pointerId: number; start: { x: number; y: number }; initial: CanvasViewport }
    | { kind: "connect"; pointerId: number; fromNodeId: string; fromPort: CanvasPort };

const MAX_NODES = 100;
const MAX_EDGES = 200;
const AUTO_SUBMIT_ACTION_IDS = new Set([
    "mediakit.audio.probe-audio-metadata",
    "mediakit.video.probe-video-metadata",
]);
const NODE_WIDTH = 280;
const NODE_HEIGHT = 190;
const MEDIA_FOOTER_HEIGHT = 36;
const GRID_COLUMN_STEP = 452;
const GRID_ROW_STEP = 428;
const CANVAS_DRAG_TYPE = "application/x-v8-creative-canvas-resource";
const MODEL_ACCEPT = "image/*,video/*,audio/*,.glb,.gltf,.obj,.fbx,.stl,.usd,.usdz,.pdf,.txt,.md,.json";
const EMPTY_SNAPSHOT: CanvasSnapshot = { version: 2, nodes: [], edges: [], viewport: { x: 24, y: 24, scale: 1 } };

function recordOf(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

function stringValue(record: Record<string, unknown>, ...keys: string[]) {
    for (const key of keys) {
        const value = record[key];
        if (typeof value === "string" && value.trim()) return value.trim();
    }
    return "";
}

function createId(prefix: string) {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return `${prefix}-${crypto.randomUUID()}`;
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function toWebResourceUrl(value: unknown) {
    const url = String(value || "").trim();
    const adminPrefix = "/api/client/workspace/resource";
    return url.startsWith(adminPrefix)
        ? `/api/workspace/resource${url.slice(adminPrefix.length)}`
        : url;
}

function mediaTypeOf(resource: Pick<CanvasResource, "name" | "mimeType" | "mediaType">): CreativeCanvasMediaType {
    const result = creativeCanvasMediaType(resource);
    if (["image", "video", "audio", "model_3d", "document", "text", "mask", "metadata"].includes(result)) {
        return result as CreativeCanvasMediaType;
    }
    return "unknown";
}

function normalizeResource(
    raw: unknown,
    origin: ResourceOrigin,
    sessionId: string,
    index: number,
    includeInternal = false,
): CanvasResource | null {
    const record = recordOf(raw);
    const sourceKind = stringValue(record, "sourceKind", "source_kind");
    if (origin === "source" && sourceKind === "canvas_mask" && !includeInternal) return null;
    if (!Object.keys(record).length) return null;
    const metadata = recordOf(record.metadata);
    const explicitSessionId = stringValue(record, "sessionId", "session_id") || stringValue(metadata, "sessionId", "session_id");
    if (!explicitSessionId || explicitSessionId !== sessionId) return null;
    const id = stringValue(record, "artifactId", "artifact_id", "sourceId", "source_id", "id") || `${origin}-${index}`;
    const name = stringValue(record, "displayLabel", "display_label", "title", "name", "filename", "fileName") || id;
    const mimeType = stringValue(record, "mimeType", "mime_type", "contentType", "content_type", "type") || "application/octet-stream";
    const url = toWebResourceUrl(stringValue(record, "previewUrl", "preview_url", "downloadUrl", "download_url", "url", "publicUrl", "public_url", "externalUrl", "external_url")) || undefined;
    const resourceRef = recordOf(record.resourceRef || record.resource_ref);
    const base: CanvasResource = {
        id,
        origin,
        name,
        mimeType,
        url,
        caption: stringValue(record, "caption", "description", "summary") || undefined,
        resourceRef: Object.keys(resourceRef).length ? resourceRef : undefined,
        workspacePath: stringValue(record, "workspacePath", "workspace_path") || undefined,
        workspaceRelativePath: stringValue(record, "workspaceRelativePath", "workspace_relative_path", "path") || undefined,
        runId: stringValue(record, "runId", "run_id") || undefined,
        messageId: stringValue(record, "messageId", "message_id") || undefined,
        sourceKind: sourceKind || undefined,
        size: Number.isFinite(Number(record.size)) ? Number(record.size) : undefined,
        projectionRecord: { ...record, sessionId: explicitSessionId },
    };
    return { ...base, mediaType: mediaTypeOf(base) };
}

function sanitizeMask(value: unknown): CreativeCanvasMaskState | undefined {
    const record = recordOf(value);
    if (!Array.isArray(record.strokes)) return undefined;
    const strokes = record.strokes.slice(-64).flatMap((rawStroke) => {
        const stroke = recordOf(rawStroke);
        const mode: "paint" | "erase" = stroke.mode === "erase" ? "erase" : "paint";
        const size = Math.min(0.2, Math.max(0.005, Number(stroke.size) || 0.045));
        const points = Array.isArray(stroke.points)
            ? stroke.points.slice(-512).flatMap((rawPoint) => {
                const point = recordOf(rawPoint);
                const x = Number(point.x);
                const y = Number(point.y);
                return Number.isFinite(x) && Number.isFinite(y)
                    ? [{ x: Math.min(1, Math.max(0, x)), y: Math.min(1, Math.max(0, y)) }]
                    : [];
            })
            : [];
        return points.length ? [{ id: stringValue(stroke, "id") || createId("stroke"), mode, size, points }] : [];
    });
    return {
        revision: Math.max(0, Number(record.revision) || 0),
        strokes,
        frozenSourceIds: Array.isArray(record.frozenSourceIds) ? record.frozenSourceIds.map(String).slice(-20) : undefined,
        sourceWidth: Number.isFinite(Number(record.sourceWidth)) ? Number(record.sourceWidth) : undefined,
        sourceHeight: Number.isFinite(Number(record.sourceHeight)) ? Number(record.sourceHeight) : undefined,
    };
}

function readSnapshot(storageKey: string): CanvasSnapshot {
    try {
        const raw = JSON.parse(window.localStorage.getItem(storageKey) || "null");
        if (Array.isArray(raw)) {
            const nodes = raw.slice(0, MAX_NODES).flatMap((item: unknown, index: number) => {
                const record = recordOf(item);
                const resourceId = stringValue(record, "id", "resourceId");
                const origin = record.origin === "source" ? "source" : "artifact";
                if (!resourceId) return [];
                return [{
                    nodeId: stringValue(record, "nodeId") || `${origin}:${resourceId}:${index}`,
                    origin,
                    resourceId,
                    x: Math.max(-10000, Math.min(10000, Number(record.x) || 0)),
                    y: Math.max(-10000, Math.min(10000, Number(record.y) || 0)),
                    width: NODE_WIDTH,
                    height: NODE_HEIGHT,
                } satisfies CanvasNode];
            });
            return { ...EMPTY_SNAPSHOT, nodes };
        }
        const record = recordOf(raw);
        if (record.version !== 2) return EMPTY_SNAPSHOT;
        const nodes = Array.isArray(record.nodes) ? record.nodes.slice(0, MAX_NODES).flatMap((item: unknown) => {
            const node = recordOf(item);
            const origin = node.origin === "source" || node.origin === "artifact" || node.origin === "placeholder" ? node.origin : "placeholder";
            const nodeId = stringValue(node, "nodeId");
            if (!nodeId) return [];
            const storedWidth = Number(node.width) || NODE_WIDTH;
            const storedHeight = Number(node.height) || NODE_HEIGHT;
            const usesPreviousDefault = Math.abs(storedWidth - 248) < 1 && Math.abs(storedHeight - 184) < 1;
            return [{
                nodeId,
                origin,
                resourceId: stringValue(node, "resourceId") || undefined,
                x: Math.max(-10000, Math.min(10000, Number(node.x) || 0)),
                y: Math.max(-10000, Math.min(10000, Number(node.y) || 0)),
                width: Math.max(160, Math.min(720, usesPreviousDefault ? NODE_WIDTH : storedWidth)),
                height: Math.max(120, Math.min(560, usesPreviousDefault ? NODE_HEIGHT : storedHeight)),
                operationId: stringValue(node, "operationId") || undefined,
                operationLabel: stringValue(node, "operationLabel") || undefined,
                operationState: ["reserved", "running", "waiting", "ready", "failed", "cancelled"].includes(String(node.operationState))
                    ? node.operationState as CanvasNode["operationState"]
                    : undefined,
                mask: sanitizeMask(node.mask),
            } satisfies CanvasNode];
        }) : [];
        const nodeIds = new Set(nodes.map((node) => node.nodeId));
        const edges = Array.isArray(record.edges) ? record.edges.slice(0, MAX_EDGES).flatMap((item: unknown) => {
            const edge = recordOf(item);
            const edgeId = stringValue(edge, "edgeId");
            const from = stringValue(edge, "from");
            const to = stringValue(edge, "to");
            const fromPort: CanvasPort = edge.fromPort === "left" ? "left" : "right";
            const toPort: CanvasPort = edge.toPort === "right" ? "right" : "left";
            return edgeId && from !== to && nodeIds.has(from) && nodeIds.has(to) ? [{ edgeId, from, to, fromPort, toPort }] : [];
        }) : [];
        const viewport = recordOf(record.viewport);
        return {
            version: 2,
            nodes,
            edges,
            viewport: {
                x: Number.isFinite(Number(viewport.x)) ? Number(viewport.x) : 24,
                y: Number.isFinite(Number(viewport.y)) ? Number(viewport.y) : 24,
                scale: Math.max(0.25, Math.min(2.5, Number(viewport.scale) || 1)),
            },
        };
    } catch {
        return EMPTY_SNAPSHOT;
    }
}

function isEditableTarget(target: EventTarget | null) {
    return target instanceof HTMLInputElement
        || target instanceof HTMLTextAreaElement
        || target instanceof HTMLSelectElement
        || (target instanceof HTMLElement && target.isContentEditable);
}

function portPoint(node: CanvasNode, port: CanvasPort) {
    return {
        x: port === "left" ? node.x : node.x + node.width,
        y: node.y + node.height / 2,
    };
}

function connectionPath(start: { x: number; y: number }, startPort: CanvasPort, end: { x: number; y: number }, endPort: CanvasPort) {
    const bend = Math.max(70, Math.abs(end.x - start.x) * 0.42);
    const startControl = start.x + (startPort === "right" ? bend : -bend);
    const endControl = end.x + (endPort === "right" ? bend : -bend);
    return `M ${start.x} ${start.y} C ${startControl} ${start.y}, ${endControl} ${end.y}, ${end.x} ${end.y}`;
}

function edgePath(from: CanvasNode, to: CanvasNode, edge: Pick<CanvasEdge, "fromPort" | "toPort">) {
    return connectionPath(portPoint(from, edge.fromPort), edge.fromPort, portPoint(to, edge.toPort), edge.toPort);
}

function mediaNodeDimensions(width: number, height: number) {
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null;
    const aspect = Math.max(0.45, Math.min(3.2, width / height));
    if (aspect >= 1) {
        const previewWidth = Math.min(420, Math.max(300, 300 * Math.min(1.4, aspect)));
        return { width: previewWidth, height: previewWidth / aspect + MEDIA_FOOTER_HEIGHT };
    }
    const previewHeight = 340;
    return { width: previewHeight * aspect, height: previewHeight + MEDIA_FOOTER_HEIGHT };
}

function groupTitle(action: CreativeCanvasAction) {
    if (action.binding?.kind === "mediakit") return "专业媒体处理";
    if (action.binding?.kind === "creative_media") return "AI 创作";
    if (action.scope === "local") return "画布操作";
    return "发送给主理人";
}

export function CreativeArtifactCanvas({
    document,
    messages = [],
    sessionRunning = false,
    onSubmitTask,
}: {
    document: CreativeCanvasWorkbenchDocument;
    messages?: Message[];
    sessionRunning?: boolean;
    onSubmitTask?: (request: CanvasTaskRequest) => Promise<boolean> | boolean;
}) {
    const t = useT();
    const sessionId = document.subjectRef.sessionId;
    const storageKey = `v8-web-creative-canvas:v2:${sessionId}`;
    const boardRef = useRef<HTMLDivElement | null>(null);
    const fileInputRef = useRef<HTMLInputElement | null>(null);
    const sessionRunningRef = useRef(sessionRunning);
    const interactionRef = useRef<PointerInteraction | null>(null);
    const pointerFrameRef = useRef<number | null>(null);
    const autoSubmittedOperationIdsRef = useRef(new Set<string>());
    const pendingPointerRef = useRef<{ clientX: number; clientY: number } | null>(null);
    const [snapshot, setSnapshot] = useState<CanvasSnapshot>(EMPTY_SNAPSHOT);
    const [hydratedKey, setHydratedKey] = useState("");
    const [resources, setResources] = useState<CanvasResource[]>([]);
    const [selectedIds, setSelectedIds] = useState<string[]>([]);
    const [selectionRect, setSelectionRect] = useState<SelectionRect | null>(null);
    const [tool, setTool] = useState<"select" | "pan">("select");
    const [trayOpen, setTrayOpen] = useState(false);
    const [loading, setLoading] = useState(true);
    const [uploading, setUploading] = useState(false);
    const [error, setError] = useState("");
    const [mediaKitStatus, setMediaKitStatus] = useState<"loading" | "ready" | "unavailable">("loading");
    const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
    const [composer, setComposer] = useState<ComposerState | null>(null);
    const [submitting, setSubmitting] = useState(false);
    const [connectionSourceId, setConnectionSourceId] = useState<string | null>(null);
    const [pendingConnectionDrop, setPendingConnectionDrop] = useState<PendingConnectionDrop | null>(null);
    const [connectionDraft, setConnectionDraft] = useState<ConnectionDraft | null>(null);
    const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);
    const [inspectNodeId, setInspectNodeId] = useState<string | null>(null);
    const [maskNodeId, setMaskNodeId] = useState<string | null>(null);
    sessionRunningRef.current = sessionRunning;

    useEffect(() => {
        setHydratedKey("");
        setSnapshot(readSnapshot(storageKey));
        setSelectedIds([]);
        setContextMenu(null);
        setComposer(null);
        setInspectNodeId(null);
        setMaskNodeId(null);
        setConnectionSourceId(null);
        setConnectionDraft(null);
        interactionRef.current = null;
        setHydratedKey(storageKey);
    }, [storageKey]);

    useEffect(() => {
        if (hydratedKey !== storageKey) return;
        const timeout = window.setTimeout(() => {
            try {
                window.localStorage.setItem(storageKey, JSON.stringify(snapshot));
            } catch {
                // Local canvas persistence is best effort; runtime/source truth remains server-side.
            }
        }, 160);
        return () => window.clearTimeout(timeout);
    }, [hydratedKey, snapshot, storageKey]);

    const loadCatalog = useCallback(async (silent = false) => {
        const controller = new AbortController();
        if (!silent) setLoading(true);
        const load = async (path: string, key: "artifacts" | "sources", origin: ResourceOrigin) => {
            const params = new URLSearchParams({ sessionId, limit: "100" });
            if (origin === "source") params.set("includeUnbound", "true");
            const response = await fetch(`${path}?${params.toString()}`, { cache: "no-store", signal: controller.signal });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
            return (Array.isArray(payload?.[key]) ? payload[key] : [])
                .map((entry: unknown, index: number) => normalizeResource(entry, origin, sessionId, index))
                .filter((item: CanvasResource | null): item is CanvasResource => Boolean(item));
        };
        try {
            const [artifacts, sources] = await Promise.all([
                load("/api/artifacts", "artifacts", "artifact"),
                load("/api/sources", "sources", "source"),
            ]);
            const merged = [...artifacts, ...sources];
            setResources((current) => {
                const byKey = new Map(current.map((item) => [`${item.origin}:${item.id}`, item]));
                for (const item of merged) byKey.set(`${item.origin}:${item.id}`, item);
                return [...byKey.values()];
            });
            setError("");
        } catch (reason) {
            if (!controller.signal.aborted && !silent) setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            if (!silent && !controller.signal.aborted) setLoading(false);
        }
        return () => controller.abort();
    }, [sessionId]);

    useEffect(() => {
        void loadCatalog();
    }, [loadCatalog]);

    useEffect(() => {
        let cancelled = false;
        void fetch("/api/plugins/mentions", { cache: "no-store" })
            .then(async (response) => {
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error("plugin catalog unavailable");
                const mediaKit = (Array.isArray(payload?.items) ? payload.items : []).find((item: unknown) => {
                    const record = recordOf(item);
                    return stringValue(record, "pluginId", "plugin_id") === "volcengine-mediakit";
                });
                if (!cancelled) setMediaKitStatus(recordOf(mediaKit).status === "ready" ? "ready" : "unavailable");
            })
            .catch(() => { if (!cancelled) setMediaKitStatus("unavailable"); });
        return () => { cancelled = true; };
    }, [sessionId]);

    const hasPendingPlaceholder = snapshot.nodes.some((node) => node.origin === "placeholder" && !["failed", "cancelled"].includes(String(node.operationState)));
    useEffect(() => {
        if (!sessionRunning && !hasPendingPlaceholder) return;
        const interval = window.setInterval(() => void loadCatalog(true), 3500);
        return () => window.clearInterval(interval);
    }, [hasPendingPlaceholder, loadCatalog, sessionRunning]);

    const resourceMap = useMemo(
        () => new Map(resources.map((item) => [`${item.origin}:${item.id}`, item])),
        [resources],
    );
    const resourceForNode = useCallback((node: CanvasNode) => {
        if (!node.resourceId || node.origin === "placeholder") return null;
        return resourceMap.get(`${node.origin}:${node.resourceId}`) || null;
    }, [resourceMap]);

    const projection = useMemo(() => buildCreativeCanvasEventProjection({
        sessionId,
        messages,
        artifacts: resources.filter((item) => item.origin === "artifact").map((item) => item.projectionRecord || item),
    }), [messages, resources, sessionId]);

    useEffect(() => {
        if (!projection.operations.length) return;
        setSnapshot((current) => {
            let changed = false;
            const nextNodes = [...current.nodes];
            for (const operation of projection.operations) {
                let matching = nextNodes.filter((node) => node.operationId === operation.canvasOperationId);
                if (!matching.length && nextNodes.length < MAX_NODES) {
                    const availableWidth = ((boardRef.current?.clientWidth || 920) - 96) / current.viewport.scale;
                    const columns = Math.max(1, Math.min(4, Math.floor(availableWidth / GRID_COLUMN_STEP)));
                    const offsetColumn = nextNodes.length % columns;
                    const offsetRow = Math.floor(nextNodes.length / columns);
                    const created: CanvasNode = {
                        nodeId: `operation:${operation.canvasOperationId}:0`,
                        origin: "placeholder",
                        x: 80 + offsetColumn * GRID_COLUMN_STEP,
                        y: 90 + offsetRow * GRID_ROW_STEP,
                        width: NODE_WIDTH,
                        height: NODE_HEIGHT,
                        operationId: operation.canvasOperationId,
                        operationLabel: operation.label,
                        operationState: operation.state,
                    };
                    nextNodes.push(created);
                    matching = [created];
                    changed = true;
                }
                matching.forEach((node, index) => {
                    const nodeIndex = nextNodes.findIndex((candidate) => candidate.nodeId === node.nodeId);
                    const artifact = operation.artifacts[index];
                    const catalogArtifact = artifact
                        ? resources.find((item) => item.origin === "artifact" && item.id === artifact.artifactId)
                        : undefined;
                    const patch: Partial<CanvasNode> = {
                        operationState: artifact ? "ready" : operation.state,
                        ...(catalogArtifact ? { origin: "artifact", resourceId: catalogArtifact.id } : {}),
                    };
                    const updated = { ...nextNodes[nodeIndex], ...patch };
                    if (JSON.stringify(updated) !== JSON.stringify(nextNodes[nodeIndex])) {
                        nextNodes[nodeIndex] = updated;
                        changed = true;
                    }
                });
                for (let index = matching.length; index < operation.artifacts.length && nextNodes.length < MAX_NODES; index += 1) {
                    const artifact = operation.artifacts[index];
                    const catalogArtifact = resources.find((item) => item.origin === "artifact" && item.id === artifact.artifactId);
                    if (!catalogArtifact) continue;
                    const anchor = matching[0] || nextNodes.at(-1);
                    nextNodes.push({
                        nodeId: `operation:${operation.canvasOperationId}:${index}`,
                        origin: "artifact",
                        resourceId: catalogArtifact.id,
                        x: (anchor?.x || 80) + index * 34,
                        y: (anchor?.y || 90) + index * 28,
                        width: NODE_WIDTH,
                        height: NODE_HEIGHT,
                        operationId: operation.canvasOperationId,
                        operationLabel: operation.label,
                        operationState: "ready",
                    });
                    changed = true;
                }
            }
            return changed ? { ...current, nodes: nextNodes } : current;
        });
    }, [projection.operations, resources]);

    useEffect(() => {
        if (!sessionRunning) return;
        setComposer(null);
        setContextMenu(null);
        setMaskNodeId(null);
        setConnectionSourceId(null);
        setConnectionDraft(null);
        if (interactionRef.current?.kind === "connect") interactionRef.current = null;
    }, [sessionRunning]);

    const boardPoint = useCallback((clientX: number, clientY: number) => {
        const rect = boardRef.current?.getBoundingClientRect();
        if (!rect) return { x: 0, y: 0 };
        return { x: clientX - rect.left, y: clientY - rect.top };
    }, []);

    const worldPoint = useCallback((clientX: number, clientY: number) => {
        const point = boardPoint(clientX, clientY);
        return {
            x: (point.x - snapshot.viewport.x) / snapshot.viewport.scale,
            y: (point.y - snapshot.viewport.y) / snapshot.viewport.scale,
        };
    }, [boardPoint, snapshot.viewport]);

    const placeResource = useCallback((resource: CanvasResource, point?: { x: number; y: number }, connectFrom?: Pick<PendingConnectionDrop, "fromNodeId" | "fromPort">) => {
        if (sessionRunningRef.current) return;
        setSnapshot((current) => {
            if (current.nodes.length >= MAX_NODES) return current;
            const availableWidth = ((boardRef.current?.clientWidth || 920) - 96) / current.viewport.scale;
            const columns = Math.max(1, Math.min(4, Math.floor(availableWidth / GRID_COLUMN_STEP)));
            const offsetColumn = current.nodes.length % columns;
            const offsetRow = Math.floor(current.nodes.length / columns);
            const node: CanvasNode = {
                nodeId: createId("canvas-node"),
                origin: resource.origin,
                resourceId: resource.id,
                x: point?.x ?? (60 - current.viewport.x) / current.viewport.scale + offsetColumn * GRID_COLUMN_STEP,
                y: point?.y ?? (72 - current.viewport.y) / current.viewport.scale + offsetRow * GRID_ROW_STEP,
                width: NODE_WIDTH,
                height: mediaTypeOf(resource) === "audio" ? 142 : NODE_HEIGHT,
            };
            setSelectedIds([node.nodeId]);
            const edges = connectFrom && connectFrom.fromNodeId !== node.nodeId && current.edges.length < MAX_EDGES
                ? [...current.edges, { edgeId: createId("canvas-edge"), from: connectFrom.fromNodeId, to: node.nodeId, fromPort: connectFrom.fromPort, toPort: "left" as const }]
                : current.edges;
            return { ...current, nodes: [...current.nodes, node], edges };
        });
    }, []);

    const updateNodeDimensions = useCallback((nodeId: string, dimensions: { width: number; height: number }) => {
        const next = mediaNodeDimensions(dimensions.width, dimensions.height);
        if (!next) return;
        setSnapshot((current) => {
            let changed = false;
            const nodes = current.nodes.map((node) => {
                if (node.nodeId !== nodeId || (Math.abs(node.width - next.width) < 1 && Math.abs(node.height - next.height) < 1)) return node;
                changed = true;
                return { ...node, width: next.width, height: next.height };
            });
            return changed ? { ...current, nodes } : current;
        });
    }, []);

    const removeNodes = useCallback((nodeIds: string[]) => {
        if (sessionRunning || !nodeIds.length) return;
        const removing = new Set(nodeIds);
        setSnapshot((current) => ({
            ...current,
            nodes: current.nodes.filter((node) => !removing.has(node.nodeId)),
            edges: current.edges.filter((edge) => !removing.has(edge.from) && !removing.has(edge.to)),
        }));
        setSelectedIds((current) => current.filter((id) => !removing.has(id)));
        if (inspectNodeId && removing.has(inspectNodeId)) setInspectNodeId(null);
        if (connectionDraft && removing.has(connectionDraft.fromNodeId)) {
            setConnectionDraft(null);
            setConnectionSourceId(null);
            if (interactionRef.current?.kind === "connect") interactionRef.current = null;
        }
    }, [connectionDraft, inspectNodeId, sessionRunning]);

    const addEdge = useCallback((from: string, to: string, fromPort: CanvasPort = "right", toPort: CanvasPort = "left") => {
        if (sessionRunning || !from || !to || from === to) return;
        setSnapshot((current) => {
            if (current.edges.length >= MAX_EDGES || current.edges.some((edge) => edge.from === from && edge.to === to && edge.fromPort === fromPort && edge.toPort === toPort)) return current;
            return { ...current, edges: [...current.edges, { edgeId: createId("canvas-edge"), from, to, fromPort, toPort }] };
        });
    }, [sessionRunning]);

    const connectSelection = useCallback((ids: string[]) => {
        if (sessionRunning || ids.length < 2) return;
        const ordered = snapshot.nodes
            .filter((node) => ids.includes(node.nodeId))
            .sort((left, right) => left.x - right.x || left.y - right.y);
        setSnapshot((current) => {
            const edges = [...current.edges];
            for (let index = 0; index < ordered.length - 1; index += 1) {
                const from = ordered[index].nodeId;
                const to = ordered[index + 1].nodeId;
                if (!edges.some((edge) => edge.from === from && edge.to === to && edge.fromPort === "right" && edge.toPort === "left") && edges.length < MAX_EDGES) {
                    edges.push({ edgeId: createId("canvas-edge"), from, to, fromPort: "right", toPort: "left" });
                }
            }
            return { ...current, edges };
        });
    }, [sessionRunning, snapshot.nodes]);

    const fitView = useCallback(() => {
        const rect = boardRef.current?.getBoundingClientRect();
        if (!rect || !snapshot.nodes.length) {
            setSnapshot((current) => ({ ...current, viewport: { x: 24, y: 24, scale: 1 } }));
            return;
        }
        const minX = Math.min(...snapshot.nodes.map((node) => node.x));
        const minY = Math.min(...snapshot.nodes.map((node) => node.y));
        const maxX = Math.max(...snapshot.nodes.map((node) => node.x + node.width));
        const maxY = Math.max(...snapshot.nodes.map((node) => node.y + node.height));
        const scale = Math.max(0.25, Math.min(1.35, Math.min((rect.width - 96) / Math.max(1, maxX - minX), (rect.height - 96) / Math.max(1, maxY - minY))));
        setSnapshot((current) => ({
            ...current,
            viewport: {
                scale,
                x: (rect.width - (maxX - minX) * scale) / 2 - minX * scale,
                y: (rect.height - (maxY - minY) * scale) / 2 - minY * scale,
            },
        }));
    }, [snapshot.nodes]);

    const zoomAtCenter = useCallback((delta: number) => {
        const rect = boardRef.current?.getBoundingClientRect();
        if (!rect) return;
        const center = { x: rect.width / 2, y: rect.height / 2 };
        setSnapshot((current) => {
            const nextScale = Math.max(0.25, Math.min(2.5, current.viewport.scale + delta));
            const worldX = (center.x - current.viewport.x) / current.viewport.scale;
            const worldY = (center.y - current.viewport.y) / current.viewport.scale;
            return {
                ...current,
                viewport: {
                    scale: nextScale,
                    x: center.x - worldX * nextScale,
                    y: center.y - worldY * nextScale,
                },
            };
        });
    }, []);

    const handleWheel = useCallback((event: WheelEvent<HTMLDivElement>) => {
        event.preventDefault();
        const point = boardPoint(event.clientX, event.clientY);
        setSnapshot((current) => {
            if (event.ctrlKey || event.metaKey) {
                const factor = Math.exp(-event.deltaY * 0.002);
                const scale = Math.max(0.25, Math.min(2.5, current.viewport.scale * factor));
                const worldX = (point.x - current.viewport.x) / current.viewport.scale;
                const worldY = (point.y - current.viewport.y) / current.viewport.scale;
                return { ...current, viewport: { scale, x: point.x - worldX * scale, y: point.y - worldY * scale } };
            }
            return { ...current, viewport: { ...current.viewport, x: current.viewport.x - event.deltaX, y: current.viewport.y - event.deltaY } };
        });
    }, [boardPoint]);

    const processPointerMove = useCallback(() => {
        pointerFrameRef.current = null;
        const interaction = interactionRef.current;
        const pointer = pendingPointerRef.current;
        if (!interaction || !pointer) return;
        if (interaction.kind === "pan") {
            setSnapshot((current) => ({
                ...current,
                viewport: {
                    ...current.viewport,
                    x: interaction.initial.x + pointer.clientX - interaction.start.x,
                    y: interaction.initial.y + pointer.clientY - interaction.start.y,
                },
            }));
            return;
        }
        const world = worldPoint(pointer.clientX, pointer.clientY);
        if (interaction.kind === "connect") {
            if (sessionRunningRef.current) return;
            setConnectionDraft((current) => current ? { ...current, target: world } : current);
            return;
        }
        if (interaction.kind === "select") {
            setSelectionRect({ startX: interaction.start.x, startY: interaction.start.y, x: world.x, y: world.y });
            return;
        }
        if (sessionRunning) return;
        const dx = world.x - interaction.start.x;
        const dy = world.y - interaction.start.y;
        setSnapshot((current) => ({
            ...current,
            nodes: current.nodes.map((node) => {
                const initial = interaction.initial.get(node.nodeId);
                return initial ? { ...node, x: initial.x + dx, y: initial.y + dy } : node;
            }),
        }));
    }, [sessionRunning, worldPoint]);

    const handlePointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
        if (!interactionRef.current) return;
        pendingPointerRef.current = { clientX: event.clientX, clientY: event.clientY };
        if (pointerFrameRef.current === null) pointerFrameRef.current = window.requestAnimationFrame(processPointerMove);
    }, [processPointerMove]);

    const finishPointerInteraction = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
        const interaction = interactionRef.current;
        if (!interaction || interaction.pointerId !== event.pointerId) return;
        if (pointerFrameRef.current !== null) {
            window.cancelAnimationFrame(pointerFrameRef.current);
            pointerFrameRef.current = null;
            pendingPointerRef.current = { clientX: event.clientX, clientY: event.clientY };
            processPointerMove();
        }
        if (interaction.kind === "select") {
            const end = worldPoint(event.clientX, event.clientY);
            const minimumX = Math.min(interaction.start.x, end.x);
            const minimumY = Math.min(interaction.start.y, end.y);
            const maximumX = Math.max(interaction.start.x, end.x);
            const maximumY = Math.max(interaction.start.y, end.y);
            const isClick = Math.abs(maximumX - minimumX) < 3 && Math.abs(maximumY - minimumY) < 3;
            const inside = isClick ? [] : snapshot.nodes
                .filter((node) => node.x < maximumX && node.x + node.width > minimumX && node.y < maximumY && node.y + node.height > minimumY)
                .map((node) => node.nodeId);
            setSelectedIds((current) => interaction.additive ? Array.from(new Set([...current, ...inside])) : inside);
        }
        if (interaction.kind === "connect") {
            if (event.type !== "pointercancel") {
                const end = worldPoint(event.clientX, event.clientY);
                const threshold = 34 / snapshot.viewport.scale;
                let nearest: { nodeId: string; port: CanvasPort; distance: number } | null = null;
                for (const node of snapshot.nodes) {
                    if (node.nodeId === interaction.fromNodeId) continue;
                    for (const port of ["left", "right"] as const) {
                        const point = portPoint(node, port);
                        const distance = Math.hypot(point.x - end.x, point.y - end.y);
                        if (distance <= threshold && (!nearest || distance < nearest.distance)) nearest = { nodeId: node.nodeId, port, distance };
                    }
                }
                if (nearest && !sessionRunningRef.current) {
                    addEdge(interaction.fromNodeId, nearest.nodeId, interaction.fromPort, nearest.port);
                    setSelectedIds([interaction.fromNodeId, nearest.nodeId]);
                    setPendingConnectionDrop(null);
                } else if (!sessionRunningRef.current) {
                    const menuPoint = boardPoint(event.clientX, event.clientY);
                    setPendingConnectionDrop({ fromNodeId: interaction.fromNodeId, fromPort: interaction.fromPort, point: end });
                    setSelectedIds([interaction.fromNodeId]);
                    setContextMenu({ x: menuPoint.x, y: menuPoint.y, target: "node", nodeIds: [interaction.fromNodeId] });
                }
            }
            setConnectionDraft(null);
            setConnectionSourceId(null);
        }
        interactionRef.current = null;
        pendingPointerRef.current = null;
        setSelectionRect(null);
        if (boardRef.current?.hasPointerCapture(event.pointerId)) boardRef.current.releasePointerCapture(event.pointerId);
    }, [addEdge, boardPoint, processPointerMove, snapshot.nodes, snapshot.viewport.scale, worldPoint]);

    const startBoardInteraction = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
        if (event.button !== 0 && event.button !== 1) return;
        if (event.target !== event.currentTarget) return;
        setContextMenu(null);
        const usePan = tool === "pan" || event.button === 1;
        interactionRef.current = usePan
            ? { kind: "pan", pointerId: event.pointerId, start: { x: event.clientX, y: event.clientY }, initial: snapshot.viewport }
            : { kind: "select", pointerId: event.pointerId, start: worldPoint(event.clientX, event.clientY), additive: event.shiftKey };
        event.currentTarget.setPointerCapture(event.pointerId);
        if (!usePan) setSelectionRect({ ...worldPoint(event.clientX, event.clientY), startX: worldPoint(event.clientX, event.clientY).x, startY: worldPoint(event.clientX, event.clientY).y });
    }, [snapshot.viewport, tool, worldPoint]);

    const handleNodePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>, node: CanvasNode) => {
        if (event.button !== 0 || (event.target as HTMLElement).closest("audio,video,button,a,input,textarea")) return;
        event.stopPropagation();
        setContextMenu(null);
        const nextSelection = event.shiftKey
            ? (selectedIds.includes(node.nodeId) ? selectedIds.filter((id) => id !== node.nodeId) : [...selectedIds, node.nodeId])
            : (selectedIds.includes(node.nodeId) ? selectedIds : [node.nodeId]);
        setSelectedIds(nextSelection);
        if (sessionRunning) return;
        const start = worldPoint(event.clientX, event.clientY);
        const selectedSet = new Set(nextSelection.length ? nextSelection : [node.nodeId]);
        interactionRef.current = {
            kind: "move",
            pointerId: event.pointerId,
            start,
            initial: new Map(snapshot.nodes.filter((item) => selectedSet.has(item.nodeId)).map((item) => [item.nodeId, { x: item.x, y: item.y }])),
        };
        event.currentTarget.setPointerCapture(event.pointerId);
    }, [selectedIds, sessionRunning, snapshot.nodes, worldPoint]);

    const handlePortPointerDown = useCallback((event: ReactPointerEvent<HTMLButtonElement>, node: CanvasNode, port: CanvasPort) => {
        if (event.button !== 0 || sessionRunningRef.current) return;
        event.preventDefault();
        event.stopPropagation();
        setContextMenu(null);
        setComposer(null);
        setSelectedIds([node.nodeId]);
        setConnectionSourceId(node.nodeId);
        setConnectionDraft({ fromNodeId: node.nodeId, fromPort: port, target: portPoint(node, port) });
        interactionRef.current = { kind: "connect", pointerId: event.pointerId, fromNodeId: node.nodeId, fromPort: port };
        boardRef.current?.setPointerCapture(event.pointerId);
    }, []);

    useEffect(() => () => {
        if (pointerFrameRef.current !== null) window.cancelAnimationFrame(pointerFrameRef.current);
    }, []);

    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === "Escape") {
                setContextMenu(null);
                setComposer(null);
                setInspectNodeId(null);
                setMaskNodeId(null);
                setConnectionSourceId(null);
                setConnectionDraft(null);
                if (interactionRef.current?.kind === "connect") interactionRef.current = null;
                return;
            }
            if ((event.key === "Delete" || event.key === "Backspace") && selectedIds.length && !sessionRunning && !isEditableTarget(event.target)) {
                event.preventDefault();
                removeNodes(selectedIds);
            }
        };
        window.addEventListener("keydown", onKeyDown);
        return () => window.removeEventListener("keydown", onKeyDown);
    }, [removeNodes, selectedIds, sessionRunning]);

    const selectedNodes = useMemo(
        () => snapshot.nodes.filter((node) => selectedIds.includes(node.nodeId)),
        [selectedIds, snapshot.nodes],
    );
    const selectedBounds = useMemo(() => {
        if (!selectedNodes.length) return null;
        const minX = Math.min(...selectedNodes.map((node) => node.x));
        const minY = Math.min(...selectedNodes.map((node) => node.y));
        const maxX = Math.max(...selectedNodes.map((node) => node.x + node.width));
        return {
            left: snapshot.viewport.x + ((minX + maxX) / 2) * snapshot.viewport.scale,
            top: snapshot.viewport.y + minY * snapshot.viewport.scale,
        };
    }, [selectedNodes, snapshot.viewport]);

    const actionLabel = useCallback((action: CreativeCanvasAction) => (
        isTranslationKey(action.labelKey) ? t(action.labelKey) : action.actionId
    ), [t]);

    const menuActions = useMemo(() => {
        if (!contextMenu) return [];
        const nodes = snapshot.nodes.filter((node) => contextMenu.nodeIds.includes(node.nodeId));
        const actions = getCreativeCanvasActions({
            target: contextMenu.target,
            selection: nodes.map((node, index) => ({
                id: node.nodeId,
                mediaType: resourceForNode(node) ? mediaTypeOf(resourceForNode(node)!) : "unknown",
                order: index,
            })),
            sessionRunning,
            pluginAvailable: mediaKitStatus === "ready",
            pluginGranted: false,
            allowPluginGrantRequest: true,
        });
        if (!pendingConnectionDrop || contextMenu.nodeIds[0] !== pendingConnectionDrop.fromNodeId) return actions;
        const quickIds = new Set(["local.upload_sources", "local.open_artifact_tray"]);
        const quickActions = CREATIVE_CANVAS_ACTIONS.filter((action) => quickIds.has(action.actionId));
        return [...quickActions, ...actions];
    }, [contextMenu, mediaKitStatus, pendingConnectionDrop, resourceForNode, sessionRunning, snapshot.nodes]);

    const openComposerForAction = useCallback((action: CreativeCanvasAction, input: { x: number; y: number; nodeIds: string[]; edgeId?: string }) => {
        if (sessionRunning || action.executionClass !== "chat_task") return;
        setComposer({
            ...input,
            action,
            operationId: createId("canvas-operation"),
            text: "",
        });
        setContextMenu(null);
    }, [sessionRunning]);

    const executeLocalAction = useCallback((action: CreativeCanvasAction, menu: ContextMenuState) => {
        const ids = menu.nodeIds;
        if (sessionRunning && !action.availableWhileRunning) return;
        switch (action.actionId) {
            case "local.view":
                if (ids[0]) setInspectNodeId(ids[0]);
                break;
            case "local.download":
                ids.map((id) => snapshot.nodes.find((node) => node.nodeId === id)).filter(Boolean).forEach((node) => {
                    const resource = resourceForNode(node!);
                    if (!resource?.url) return;
                    const anchor = window.document.createElement("a");
                    anchor.href = resource.url;
                    anchor.download = resource.name;
                    anchor.rel = "noreferrer";
                    anchor.click();
                });
                break;
            case "local.upload_sources":
                fileInputRef.current?.click();
                break;
            case "local.open_artifact_tray":
            case "local.pull_artifact_to_canvas":
                setTrayOpen(true);
                break;
            case "local.fit_view":
                fitView();
                break;
            case "local.start_connection":
                if (ids[0]) setConnectionSourceId(ids[0]);
                break;
            case "local.connect_selection":
                connectSelection(ids);
                break;
            case "local.create_mask":
            case "local.edit_mask":
                if (ids[0]) setMaskNodeId(ids[0]);
                break;
            case "local.delete_selection":
                removeNodes(ids);
                break;
            case "local.delete_connection":
                if (menu.edgeId) setSnapshot((current) => ({ ...current, edges: current.edges.filter((edge) => edge.edgeId !== menu.edgeId) }));
                break;
            case "local.clear_canvas":
                if (!sessionRunning) {
                    setSnapshot((current) => ({ ...current, nodes: [], edges: [] }));
                    setSelectedIds([]);
                }
                break;
            default:
                break;
        }
        setContextMenu(null);
    }, [connectSelection, fitView, removeNodes, resourceForNode, sessionRunning, snapshot.nodes]);

    const handleAction = useCallback((action: CreativeCanvasAction, menu: ContextMenuState) => {
        if (action.executionClass === "local_read" || action.executionClass === "local_mutation") {
            executeLocalAction(action, menu);
            return;
        }
        openComposerForAction(action, menu);
    }, [executeLocalAction, openComposerForAction]);

    const uploadFiles = useCallback(async (files: File[], point?: { x: number; y: number }) => {
        if (!files.length || uploading || sessionRunning) return;
        setUploading(true);
        setError("");
        try {
            const uploaded: CanvasResource[] = [];
            for (let index = 0; index < files.length; index += 1) {
                const formData = new FormData();
                formData.set("file", files[index]);
                formData.set("sessionId", sessionId);
                formData.set("sourceKind", "web_upload");
                const response = await fetch("/api/upload", { method: "POST", body: formData });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
                const resource = normalizeResource(payload, "source", sessionId, index);
                if (resource) uploaded.push(resource);
            }
            setResources((current) => {
                const map = new Map(current.map((item) => [`${item.origin}:${item.id}`, item]));
                uploaded.forEach((item) => map.set(`${item.origin}:${item.id}`, item));
                return [...map.values()];
            });
            if (!sessionRunningRef.current) {
                const connection = pendingConnectionDrop ? { fromNodeId: pendingConnectionDrop.fromNodeId, fromPort: pendingConnectionDrop.fromPort } : undefined;
                const origin = point || pendingConnectionDrop?.point;
                uploaded.forEach((resource, index) => placeResource(resource, origin ? {
                    x: origin.x + (index % 2) * GRID_COLUMN_STEP,
                    y: origin.y + Math.floor(index / 2) * GRID_ROW_STEP,
                } : undefined, connection));
                setPendingConnectionDrop(null);
            }
            setTrayOpen(false);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setUploading(false);
        }
    }, [pendingConnectionDrop, placeResource, sessionId, sessionRunning, uploading]);

    const handleFileChange = useCallback((event: ChangeEvent<HTMLInputElement>) => {
        const files = Array.from(event.target.files || []);
        event.target.value = "";
        void uploadFiles(files);
    }, [uploadFiles]);

    const freezeMask = useCallback(async (node: CanvasNode, resource: CanvasResource) => {
        if (!node.mask?.strokes.length) return null;
        const blob = await rasterizeCreativeCanvasMask(node.mask, {
            width: node.mask.sourceWidth || 1024,
            height: node.mask.sourceHeight || 1024,
        });
        const formData = new FormData();
        formData.set("file", new File([blob], `${resource.name.replace(/\.[^.]+$/, "")}-mask-r${node.mask.revision}.png`, { type: "image/png" }));
        formData.set("sessionId", sessionId);
        formData.set("sourceKind", "canvas_mask");
        const response = await fetch("/api/upload", { method: "POST", body: formData });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
        const normalized = normalizeResource(payload, "source", sessionId, 0, true);
        if (!normalized) throw new Error("蒙版来源登记失败。");
        const maskResource = { ...normalized, mediaType: "mask" } satisfies CanvasResource;
        setSnapshot((current) => ({
            ...current,
            nodes: current.nodes.map((item) => item.nodeId === node.nodeId && item.mask
                ? { ...item, mask: { ...item.mask, frozenSourceIds: [...(item.mask.frozenSourceIds || []), maskResource.id].slice(-20) } }
                : item),
        }));
        return maskResource;
    }, [sessionId]);

    const submitComposer = useCallback(async () => {
        if (!composer || submitting || sessionRunning || !onSubmitTask) return;
        const label = actionLabel(composer.action);
        const instruction = composer.text.trim() || label;
        if (composer.action.requiresPrompt && !composer.text.trim()) return;
        setSubmitting(true);
        setError("");
        const nodes = snapshot.nodes.filter((node) => composer.nodeIds.includes(node.nodeId));
        let refs = nodes.flatMap((node) => {
            const resource = resourceForNode(node);
            return resource ? [resource] : [];
        });
        if (composer.edgeId) {
            const edge = snapshot.edges.find((item) => item.edgeId === composer.edgeId);
            if (edge) refs = [edge.from, edge.to].flatMap((id) => {
                const node = snapshot.nodes.find((item) => item.nodeId === id);
                const resource = node ? resourceForNode(node) : null;
                return resource ? [resource] : [];
            });
        }
        let maskRevision: number | undefined;
        let operationEdge: CanvasOperationRequest["edge"];
        try {
            if (composer.action.requiresMask) {
                const maskedNode = nodes.find((node) => node.mask?.strokes.length);
                const maskedResource = maskedNode ? resourceForNode(maskedNode) : null;
                if (!maskedNode || !maskedResource) throw new Error("请先在一张图片上绘制蒙版。");
                const maskResource = await freezeMask(maskedNode, maskedResource);
                if (!maskResource) throw new Error("蒙版快照生成失败。");
                refs = [...refs, maskResource];
                maskRevision = maskedNode.mask?.revision;
            }
            const reservesOutput = ["artifact", "artifacts"].includes(composer.action.output.kind);
            if (reservesOutput) {
                const anchor = nodes[0];
                setSnapshot((current) => ({
                    ...current,
                    nodes: current.nodes.length >= MAX_NODES ? current.nodes : [...current.nodes, {
                        nodeId: `operation:${composer.operationId}:0`,
                        origin: "placeholder",
                        x: anchor ? anchor.x + anchor.width + 64 : (100 - current.viewport.x) / current.viewport.scale,
                        y: anchor ? anchor.y : (110 - current.viewport.y) / current.viewport.scale,
                        width: NODE_WIDTH,
                        height: NODE_HEIGHT,
                        operationId: composer.operationId,
                        operationLabel: label,
                        operationState: "reserved",
                    }],
                }));
            }
            if (sessionRunningRef.current) throw new Error("主理人已开始运行，画布提交已取消。");
            if (composer.edgeId) {
                const edge = snapshot.edges.find((item) => item.edgeId === composer.edgeId);
                if (edge) {
                    const fromNode = snapshot.nodes.find((item) => item.nodeId === edge.from);
                    const toNode = snapshot.nodes.find((item) => item.nodeId === edge.to);
                    operationEdge = {
                        edgeId: edge.edgeId,
                        fromNodeId: edge.from,
                        toNodeId: edge.to,
                        ...(fromNode?.resourceId ? { fromResourceId: fromNode.resourceId } : {}),
                        ...(toNode?.resourceId ? { toResourceId: toNode.resourceId } : {}),
                    };
                }
            }
            const accepted = await onSubmitTask({
                text: instruction,
                refs: refs.slice(0, 100).map((resource) => ({
                    id: resource.id,
                    origin: resource.origin,
                    name: resource.name,
                    mimeType: resource.mimeType,
                    mediaType: resource.mediaType,
                    url: resource.url,
                    caption: resource.caption,
                    resourceRef: resource.resourceRef,
                    workspacePath: resource.workspacePath,
                    workspaceRelativePath: resource.workspaceRelativePath,
                    sourceKind: resource.sourceKind,
                    size: resource.size,
                })),
                operation: {
                    operationId: composer.operationId,
                    actionId: composer.action.actionId,
                    label,
                    nodeIds: composer.nodeIds,
                    edgeId: composer.edgeId,
                    outputKind: composer.action.output.kind,
                    outputSlot: composer.action.output.slot,
                    maskRevision,
                    binding: composer.action.binding,
                    edge: operationEdge,
                },
            });
            setSnapshot((current) => ({
                ...current,
                nodes: current.nodes.map((node) => node.operationId === composer.operationId
                    ? { ...node, operationState: accepted === false ? "failed" : "waiting" }
                    : node),
            }));
            if (accepted !== false) setComposer(null);
        } catch (reason) {
            setSnapshot((current) => ({
                ...current,
                nodes: current.nodes.map((node) => node.operationId === composer.operationId ? { ...node, operationState: "failed" } : node),
            }));
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setSubmitting(false);
        }
    }, [actionLabel, composer, freezeMask, onSubmitTask, resourceForNode, sessionRunning, snapshot.edges, snapshot.nodes, submitting]);

    useEffect(() => {
        if (!composer || !AUTO_SUBMIT_ACTION_IDS.has(composer.action.actionId) || submitting || autoSubmittedOperationIdsRef.current.has(composer.operationId)) return;
        autoSubmittedOperationIdsRef.current.add(composer.operationId);
        void submitComposer();
    }, [composer, submitComposer, submitting]);

    const selectedImageNode = selectedNodes.length === 1 && mediaTypeOf(resourceForNode(selectedNodes[0]) || { name: "", mimeType: "", mediaType: "unknown" }) === "image"
        ? selectedNodes[0]
        : null;
    const inspectNode = inspectNodeId ? snapshot.nodes.find((node) => node.nodeId === inspectNodeId) || null : null;
    const inspectResource = inspectNode ? resourceForNode(inspectNode) : null;
    const maskNode = maskNodeId ? snapshot.nodes.find((node) => node.nodeId === maskNodeId) || null : null;
    const maskResource = maskNode ? resourceForNode(maskNode) : null;

    const updateNodeMask = useCallback((nodeId: string, mask: CreativeCanvasMaskState) => {
        if (sessionRunning) return;
        setSnapshot((current) => ({
            ...current,
            nodes: current.nodes.map((node) => node.nodeId === nodeId ? { ...node, mask } : node),
        }));
    }, [sessionRunning]);

    const openSelectionComposer = useCallback((actionId = "message.submit_selection") => {
        const action = CREATIVE_CANVAS_ACTIONS.find((item) => item.actionId === actionId);
        if (!action || !selectedBounds) return;
        openComposerForAction(action, {
            x: selectedBounds.left,
            y: Math.max(70, selectedBounds.top - 8),
            nodeIds: selectedIds,
        });
    }, [openComposerForAction, selectedBounds, selectedIds]);

    const groupedMenuActions = useMemo(() => {
        const groups = new Map<string, CreativeCanvasAction[]>();
        for (const action of menuActions) {
            const title = groupTitle(action);
            groups.set(title, [...(groups.get(title) || []), action]);
        }
        return [...groups.entries()];
    }, [menuActions]);
    const connectionDraftSource = connectionDraft
        ? snapshot.nodes.find((node) => node.nodeId === connectionDraft.fromNodeId) || null
        : null;

    return (
        <div
            ref={boardRef}
            data-testid="creative-artifact-canvas"
            onPointerDown={startBoardInteraction}
            onPointerMove={handlePointerMove}
            onPointerUp={finishPointerInteraction}
            onPointerCancel={finishPointerInteraction}
            onWheel={handleWheel}
            onContextMenu={(event) => {
                if (event.target !== event.currentTarget) return;
                event.preventDefault();
                const point = boardPoint(event.clientX, event.clientY);
                setSelectedIds([]);
                setContextMenu({ x: point.x, y: point.y, target: "canvas", nodeIds: [] });
            }}
            onDragOver={(event: DragEvent<HTMLDivElement>) => event.preventDefault()}
            onDrop={(event: DragEvent<HTMLDivElement>) => {
                event.preventDefault();
                if (sessionRunning) return;
                const key = event.dataTransfer.getData(CANVAS_DRAG_TYPE);
                const resource = resources.find((item) => `${item.origin}:${item.id}` === key);
                if (resource) {
                    placeResource(resource, worldPoint(event.clientX, event.clientY));
                    setTrayOpen(false);
                    return;
                }
                const files = Array.from(event.dataTransfer.files || []);
                if (files.length) void uploadFiles(files, worldPoint(event.clientX, event.clientY));
            }}
            className={cn(
                "relative h-full min-h-0 w-full overflow-hidden bg-[#f5f6f8] text-foreground outline-none dark:bg-[#111315]",
                tool === "pan" ? "cursor-grab active:cursor-grabbing" : "cursor-default",
            )}
        >
            <div
                aria-hidden="true"
                className="pointer-events-none absolute inset-0 opacity-70 dark:opacity-35"
                style={{
                    backgroundImage: "radial-gradient(circle, rgb(148 163 184 / .62) 1px, transparent 1.2px)",
                    backgroundPosition: `${snapshot.viewport.x}px ${snapshot.viewport.y}px`,
                    backgroundSize: `${20 * snapshot.viewport.scale}px ${20 * snapshot.viewport.scale}px`,
                }}
            />

            <div
                className="pointer-events-none absolute inset-0 origin-top-left"
                style={{ transform: `translate(${snapshot.viewport.x}px, ${snapshot.viewport.y}px) scale(${snapshot.viewport.scale})` }}
            >
                <svg className="pointer-events-none absolute left-0 top-0 h-[10000px] w-[10000px] overflow-visible" aria-hidden="true">
                    <defs>
                        <marker id={`canvas-arrow-${sessionId.replace(/[^a-z0-9]/gi, "")}`} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="strokeWidth">
                            <path d="M 0 0 L 8 4 L 0 8 z" className="fill-slate-400 dark:fill-slate-500" />
                        </marker>
                    </defs>
                    {snapshot.edges.map((edge) => {
                        const from = snapshot.nodes.find((node) => node.nodeId === edge.from);
                        const to = snapshot.nodes.find((node) => node.nodeId === edge.to);
                        if (!from || !to) return null;
                        const path = edgePath(from, to, edge);
                        return (
                            <g key={edge.edgeId} data-canvas-edge={edge.edgeId}>
                                <path d={path} fill="none" stroke="transparent" strokeWidth="18" className="pointer-events-auto cursor-pointer" onPointerEnter={() => setHoveredEdgeId(edge.edgeId)} onPointerLeave={(event) => {
                                    const related = event.relatedTarget;
                                    if (related instanceof HTMLElement && related.dataset.canvasEdgeComment === edge.edgeId) return;
                                    setHoveredEdgeId((current) => current === edge.edgeId ? null : current);
                                }} onContextMenu={(event) => {
                                    event.preventDefault();
                                    event.stopPropagation();
                                    const point = boardPoint(event.clientX, event.clientY);
                                    setContextMenu({ x: point.x, y: point.y, target: "edge", nodeIds: [edge.from, edge.to], edgeId: edge.edgeId });
                                }} />
                                <path d={path} fill="none" stroke={hoveredEdgeId === edge.edgeId ? "rgb(124 58 237)" : "rgb(148 163 184)"} strokeWidth="2" markerEnd={`url(#canvas-arrow-${sessionId.replace(/[^a-z0-9]/gi, "")})`} />
                            </g>
                        );
                    })}
                    {connectionDraft && connectionDraftSource ? (
                        <path
                            d={connectionPath(
                                portPoint(connectionDraftSource, connectionDraft.fromPort),
                                connectionDraft.fromPort,
                                connectionDraft.target,
                                connectionDraft.fromPort === "right" ? "left" : "right",
                            )}
                            fill="none"
                            stroke="rgb(124 58 237)"
                            strokeWidth="2"
                            strokeDasharray="7 6"
                        />
                    ) : null}
                </svg>

                {snapshot.edges.map((edge) => {
                    if (hoveredEdgeId !== edge.edgeId) return null;
                    const from = snapshot.nodes.find((node) => node.nodeId === edge.from);
                    const to = snapshot.nodes.find((node) => node.nodeId === edge.to);
                    if (!from || !to) return null;
                    const start = portPoint(from, edge.fromPort);
                    const end = portPoint(to, edge.toPort);
                    return (
                        <button
                            key={`comment:${edge.edgeId}`}
                            type="button"
                            data-canvas-edge-comment={edge.edgeId}
                            onPointerEnter={() => setHoveredEdgeId(edge.edgeId)}
                            onPointerLeave={() => setHoveredEdgeId((current) => current === edge.edgeId ? null : current)}
                            onClick={(event) => {
                                event.stopPropagation();
                                const action = CREATIVE_CANVAS_ACTIONS.find((item) => item.actionId === "message.comment_connection");
                                if (action) openComposerForAction(action, { x: snapshot.viewport.x + ((start.x + end.x) / 2) * snapshot.viewport.scale, y: snapshot.viewport.y + ((start.y + end.y) / 2) * snapshot.viewport.scale, nodeIds: [edge.from, edge.to], edgeId: edge.edgeId });
                            }}
                            disabled={sessionRunning}
                            style={{ left: (start.x + end.x) / 2, top: (start.y + end.y) / 2 }}
                            className="pointer-events-auto absolute z-20 grid h-8 w-8 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-white/80 bg-background text-muted-foreground shadow-lg hover:text-primary disabled:opacity-35 dark:border-white/10"
                            aria-label="评论这条连线"
                        >
                            <MessageSquare className="h-3.5 w-3.5" />
                        </button>
                    );
                })}

                {snapshot.nodes.map((node) => {
                    const resource = resourceForNode(node);
                    const selected = selectedIds.includes(node.nodeId);
                    const placeholder = node.origin === "placeholder" || !resource;
                    return (
                        <div
                            key={node.nodeId}
                            data-canvas-node={node.nodeId}
                            onPointerDown={(event) => handleNodePointerDown(event, node)}
                            onDoubleClick={(event) => { event.stopPropagation(); if (resource) setInspectNodeId(node.nodeId); }}
                            onContextMenu={(event) => {
                                event.preventDefault();
                                event.stopPropagation();
                                const point = boardPoint(event.clientX, event.clientY);
                                const nodeIds = selectedIds.includes(node.nodeId) ? selectedIds : [node.nodeId];
                                setSelectedIds(nodeIds);
                                setContextMenu({ x: point.x, y: point.y, target: nodeIds.length > 1 ? "selection" : "node", nodeIds });
                            }}
                            onDragOver={(event) => { if (placeholder && !sessionRunning) event.preventDefault(); }}
                            onDrop={(event) => {
                                if (!placeholder || sessionRunning) return;
                                event.preventDefault();
                                event.stopPropagation();
                                const key = event.dataTransfer.getData(CANVAS_DRAG_TYPE);
                                const dropped = resources.find((item) => `${item.origin}:${item.id}` === key);
                                if (!dropped) return;
                                setSnapshot((current) => ({
                                    ...current,
                                    nodes: current.nodes.map((item) => item.nodeId === node.nodeId
                                        ? { ...item, origin: dropped.origin, resourceId: dropped.id, operationState: "ready" }
                                        : item),
                                }));
                                setTrayOpen(false);
                            }}
                            style={{ width: node.width, height: node.height, transform: `translate(${node.x}px, ${node.y}px)` }}
                            className="pointer-events-auto absolute left-0 top-0 touch-none select-none overflow-visible"
                        >
                            <div className={cn(
                                "flex h-full w-full flex-col overflow-hidden rounded-2xl border bg-background/96 shadow-[0_12px_34px_rgba(15,23,42,.11)] backdrop-blur-sm transition-[border-color,box-shadow] dark:bg-[#1b1e22]/96",
                                selected ? "border-violet-500 ring-2 ring-violet-500/20 shadow-[0_18px_48px_rgba(124,58,237,.18)]" : "border-white/80 hover:border-slate-300 dark:border-white/10 dark:hover:border-white/20",
                                connectionSourceId === node.nodeId && "border-emerald-500 ring-2 ring-emerald-500/20",
                                placeholder && "border-dashed",
                            )}>
                                <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden bg-slate-100/75 text-muted-foreground dark:bg-black/25">
                                    {placeholder ? (
                                        <div className="flex flex-col items-center gap-3 px-5 text-center">
                                            {node.operationState === "failed" || node.operationState === "cancelled" ? <X className="h-6 w-6 text-destructive" /> : <Loader2 className={cn("h-6 w-6 text-violet-500", ["running", "waiting"].includes(String(node.operationState)) && "animate-spin")} />}
                                            <div className="text-[11px] font-medium text-foreground">{node.operationLabel || "等待产物"}</div>
                                            <div className="text-[10px] text-muted-foreground">
                                                {node.operationState === "failed" ? "任务未完成，可删除此卡片后重试" : node.operationState === "cancelled" ? "任务已取消" : sessionRunning ? "主理人正在处理，产物会在这里出现" : "等待产物登记；也可从素材抽屉拖入"}
                                            </div>
                                        </div>
                                    ) : (
                                        <>
                                            <CreativeCanvasMedia resource={resource} onDimensions={(dimensions) => updateNodeDimensions(node.nodeId, dimensions)} />
                                            <CreativeCanvasMaskOverlay mask={node.mask} />
                                        </>
                                    )}
                                </div>
                                <div
                                    data-canvas-title={node.nodeId}
                                    title={resource?.name || node.operationLabel || "产物占位"}
                                    className="group/title flex h-9 shrink-0 items-center gap-2 border-t border-border/50 px-3 transition-colors hover:bg-violet-500/[.06]"
                                >
                                    <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", node.origin === "source" ? "bg-cyan-500" : node.origin === "artifact" ? "bg-violet-500" : "bg-slate-400")} />
                                    <span className="min-w-0 flex-1 truncate text-[11px] font-medium transition-colors group-hover/title:text-violet-700 dark:group-hover/title:text-violet-300">{resource?.name || node.operationLabel || "产物占位"}</span>
                                    {node.mask?.strokes.length ? <span className="rounded-full bg-rose-500/10 px-1.5 py-0.5 text-[9px] font-semibold text-rose-600">蒙版 {node.mask.revision}</span> : null}
                                </div>
                            </div>
                            {(["left", "right"] as const).map((port) => (
                                <button
                                    key={port}
                                    type="button"
                                    data-canvas-port={port}
                                    data-canvas-node-id={node.nodeId}
                                    disabled={sessionRunning}
                                    onPointerDown={(event) => handlePortPointerDown(event, node, port)}
                                    title={port === "left" ? "从左侧连接" : "从右侧连接"}
                                    aria-label={port === "left" ? "从左侧连接" : "从右侧连接"}
                                    style={{ left: port === "left" ? 0 : node.width, top: node.height / 2 }}
                                    className={cn(
                                        "group/port absolute z-30 grid h-7 w-7 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full outline-none transition-transform hover:scale-110 focus-visible:ring-2 focus-visible:ring-violet-500 disabled:cursor-not-allowed disabled:opacity-35",
                                        selected || connectionSourceId === node.nodeId ? "opacity-100" : "opacity-70 hover:opacity-100",
                                    )}
                                >
                                    <span className="h-3 w-3 rounded-full border-2 border-background bg-slate-400 shadow-[0_0_0_1px_rgba(100,116,139,.45)] transition-colors group-hover/port:bg-violet-500" />
                                </button>
                            ))}
                        </div>
                    );
                })}
            </div>

            {!snapshot.nodes.length && !loading ? (
                <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-3 text-center">
                    <div className="grid h-14 w-14 place-items-center rounded-2xl border border-white/80 bg-background/80 shadow-xl backdrop-blur dark:border-white/10"><ImagePlus className="h-6 w-6 text-muted-foreground" /></div>
                    <div className="text-sm font-semibold">把来源和产物放到画布</div>
                    <div className="max-w-sm text-xs leading-5 text-muted-foreground">上传图片、视频、音频或 3D 文件，或从右上角素材抽屉拖出已有内容。左键框选后会出现操作按钮。</div>
                </div>
            ) : null}

            {selectionRect ? (
                <div
                    className="pointer-events-none absolute z-20 border border-violet-500 bg-violet-500/10"
                    style={{
                        left: snapshot.viewport.x + Math.min(selectionRect.startX, selectionRect.x) * snapshot.viewport.scale,
                        top: snapshot.viewport.y + Math.min(selectionRect.startY, selectionRect.y) * snapshot.viewport.scale,
                        width: Math.abs(selectionRect.x - selectionRect.startX) * snapshot.viewport.scale,
                        height: Math.abs(selectionRect.y - selectionRect.startY) * snapshot.viewport.scale,
                    }}
                />
            ) : null}

            <div className="absolute left-3 top-3 z-30 flex items-center gap-1 rounded-2xl border border-white/80 bg-background/88 p-1.5 shadow-[0_12px_36px_rgba(15,23,42,.12)] backdrop-blur-xl dark:border-white/10">
                <button type="button" onClick={() => setTool("select")} className={cn("rounded-xl p-2", tool === "select" ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted hover:text-foreground")} aria-label="框选"><MousePointer2 className="h-4 w-4" /></button>
                <button type="button" onClick={() => setTool("pan")} className={cn("rounded-xl p-2", tool === "pan" ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted hover:text-foreground")} aria-label="移动画布"><Hand className="h-4 w-4" /></button>
                <span className="mx-0.5 h-5 w-px bg-border" />
                <button type="button" disabled={sessionRunning || uploading} onClick={() => fileInputRef.current?.click()} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-35" aria-label="上传">{uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}</button>
                <button type="button" onClick={fitView} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="适合画布"><Focus className="h-4 w-4" /></button>
                <button type="button" onClick={() => zoomAtCenter(-0.15)} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="缩小"><ZoomOut className="h-4 w-4" /></button>
                <span className="w-10 text-center text-[10px] tabular-nums text-muted-foreground">{Math.round(snapshot.viewport.scale * 100)}%</span>
                <button type="button" onClick={() => zoomAtCenter(0.15)} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="放大"><ZoomIn className="h-4 w-4" /></button>
            </div>

            {sessionRunning ? (
                <div className="absolute left-1/2 top-3 z-30 flex -translate-x-1/2 items-center gap-2 rounded-full border border-amber-300/70 bg-amber-50/92 px-3 py-2 text-[11px] font-medium text-amber-900 shadow-lg backdrop-blur dark:border-amber-500/25 dark:bg-amber-950/80 dark:text-amber-100">
                    <Lock className="h-3.5 w-3.5" />主理人运行中 · 画布已锁定，可查看、播放、缩放和下载
                </div>
            ) : connectionSourceId ? (
                <div className="absolute left-1/2 top-3 z-30 flex -translate-x-1/2 items-center gap-2 rounded-full border border-emerald-300/70 bg-emerald-50/92 px-3 py-2 text-[11px] font-medium text-emerald-900 shadow-lg backdrop-blur dark:border-emerald-500/25 dark:bg-emerald-950/80 dark:text-emerald-100">
                    <Link2 className="h-3.5 w-3.5" />请选择另一个卡片完成连线<button type="button" onClick={() => setConnectionSourceId(null)} className="ml-1 rounded-full p-0.5 hover:bg-black/10"><X className="h-3 w-3" /></button>
                </div>
            ) : null}

            <button type="button" onClick={() => setTrayOpen((current) => !current)} className="absolute right-3 top-3 z-30 flex h-10 items-center gap-2 rounded-2xl border border-white/80 bg-background/88 px-3 text-[11px] font-semibold shadow-[0_12px_36px_rgba(15,23,42,.12)] backdrop-blur-xl hover:bg-background dark:border-white/10">
                <Archive className="h-4 w-4" />素材 <span className="rounded-full bg-muted px-1.5 py-0.5 text-[9px] text-muted-foreground">{resources.length}</span>
            </button>

            {trayOpen ? (
                <div className="absolute right-3 top-14 z-40 flex max-h-[min(560px,calc(100%-72px))] w-[310px] flex-col overflow-hidden rounded-[20px] border border-white/80 bg-background/94 shadow-[0_24px_72px_rgba(15,23,42,.2)] backdrop-blur-xl dark:border-white/10">
                    <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border/60 px-3">
                        <PackageOpen className="h-4 w-4 text-muted-foreground" />
                        <span className="flex-1 text-xs font-semibold">来源与产物</span>
                        <button type="button" disabled={sessionRunning || uploading} onClick={() => fileInputRef.current?.click()} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-35" aria-label="上传"><Plus className="h-4 w-4" /></button>
                        <button type="button" onClick={() => setTrayOpen(false)} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="关闭素材抽屉"><X className="h-4 w-4" /></button>
                    </div>
                    <div className="custom-scrollbar min-h-0 flex-1 overflow-y-auto p-2.5">
                        {loading ? <div className="flex justify-center py-10"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div> : null}
                        {!loading && !resources.length ? <div className="px-5 py-10 text-center text-xs leading-5 text-muted-foreground">当前会话还没有来源或产物。上传文件后会登记为来源。</div> : null}
                        <div className="grid grid-cols-3 gap-2">
                            {resources.map((resource) => (
                                <button
                                    key={`${resource.origin}:${resource.id}`}
                                    type="button"
                                    draggable={!sessionRunning}
                                    onDragStart={(event) => event.dataTransfer.setData(CANVAS_DRAG_TYPE, `${resource.origin}:${resource.id}`)}
                                    onClick={() => {
                                        placeResource(resource, pendingConnectionDrop?.point, pendingConnectionDrop ? { fromNodeId: pendingConnectionDrop.fromNodeId, fromPort: pendingConnectionDrop.fromPort } : undefined);
                                        setPendingConnectionDrop(null);
                                    }}
                                    disabled={sessionRunning}
                                    title={resource.name}
                                    className="group overflow-hidden rounded-xl border border-border/60 bg-muted/25 text-left hover:border-violet-400 hover:bg-muted/45 disabled:cursor-not-allowed disabled:opacity-55"
                                >
                                    <span className="flex h-[66px] items-center justify-center overflow-hidden bg-background/60"><CreativeCanvasMedia resource={resource} compact /></span>
                                    <span className="flex items-center gap-1 border-t border-border/50 px-1.5 py-1.5"><span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", resource.origin === "source" ? "bg-cyan-500" : "bg-violet-500")} /><span className="truncate text-[9px] font-medium">{resource.name}</span></span>
                                </button>
                            ))}
                        </div>
                    </div>
                    <div className="shrink-0 border-t border-border/60 px-3 py-2 text-[9px] text-muted-foreground">
                        {mediaKitStatus === "ready" ? "专业媒体处理工具可用；选择动作时仅申请本次任务所需权限。" : mediaKitStatus === "loading" ? "正在核对专业媒体处理工具…" : "专业媒体处理工具当前不可用；AI 创作与本地画布操作仍可使用。"}
                    </div>
                </div>
            ) : null}

            {selectedBounds && !selectionRect && !maskNodeId && !inspectNodeId ? (
                <div
                    style={{ left: selectedBounds.left, top: selectedBounds.top }}
                    className="absolute z-30 flex -translate-x-1/2 -translate-y-[calc(100%+10px)] items-center gap-1 rounded-2xl border border-white/80 bg-background/92 p-1.5 shadow-[0_14px_42px_rgba(15,23,42,.16)] backdrop-blur-xl dark:border-white/10"
                >
                    <span className="px-2 text-[10px] font-semibold text-muted-foreground">{selectedIds.length} 项</span>
                    <button type="button" disabled={sessionRunning} onClick={() => openSelectionComposer()} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-primary disabled:opacity-30" aria-label="基于所选内容发送消息"><MessageSquare className="h-4 w-4" /></button>
                    <button type="button" disabled={sessionRunning} onClick={() => selectedIds.length > 1 ? connectSelection(selectedIds) : setConnectionSourceId(selectedIds[0])} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-emerald-600 disabled:opacity-30" aria-label={selectedIds.length > 1 ? "连接所选内容" : "连接节点"}><Link2 className="h-4 w-4" /></button>
                    {selectedImageNode ? <button type="button" disabled={sessionRunning} onClick={() => setMaskNodeId(selectedImageNode.nodeId)} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-rose-600 disabled:opacity-30" aria-label="绘制蒙版"><Sparkles className="h-4 w-4" /></button> : null}
                    <button type="button" disabled={sessionRunning} onClick={() => removeNodes(selectedIds)} className="rounded-xl p-2 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-30" aria-label="从画布移除"><Trash2 className="h-4 w-4" /></button>
                </div>
            ) : null}

            {contextMenu ? (
                <div
                    style={{ left: Math.min(contextMenu.x, Math.max(12, (boardRef.current?.clientWidth || 360) - 292)), top: Math.min(contextMenu.y, Math.max(12, (boardRef.current?.clientHeight || 480) - 440)) }}
                    className="absolute z-[60] max-h-[430px] w-[280px] overflow-y-auto rounded-2xl border border-white/80 bg-background/96 p-1.5 shadow-[0_22px_64px_rgba(15,23,42,.22)] backdrop-blur-xl dark:border-white/10"
                    onPointerDown={(event) => event.stopPropagation()}
                    onWheel={(event) => event.stopPropagation()}
                >
                    {groupedMenuActions.length ? groupedMenuActions.map(([title, actions]) => (
                        <div key={title} className="py-1">
                            <div className="px-2 pb-1 pt-1 text-[9px] font-semibold uppercase tracking-[.14em] text-muted-foreground">{title}</div>
                            {actions.map((action) => (
                                <button key={action.actionId} type="button" onClick={() => handleAction(action, contextMenu)} className="flex w-full items-center gap-2 rounded-xl px-2 py-2 text-left text-[11px] hover:bg-muted">
                                    {action.binding?.kind === "mediakit" ? <Box className="h-3.5 w-3.5 text-cyan-600" /> : action.binding?.kind === "creative_media" ? <Sparkles className="h-3.5 w-3.5 text-violet-600" /> : action.executionClass === "chat_task" ? <MessageSquare className="h-3.5 w-3.5 text-amber-600" /> : <Check className="h-3.5 w-3.5 text-muted-foreground" />}
                                    <span className="min-w-0 flex-1">{actionLabel(action)}</span>
                                    {action.mayIncurCost ? <span className="text-[8px] text-muted-foreground">可能计费</span> : null}
                                </button>
                            ))}
                        </div>
                    )) : <div className="px-3 py-6 text-center text-xs text-muted-foreground">当前没有可用操作</div>}
                </div>
            ) : null}

            {composer && !AUTO_SUBMIT_ACTION_IDS.has(composer.action.actionId) ? (
                <div
                    style={{ left: Math.min(Math.max(176, composer.x), Math.max(176, (boardRef.current?.clientWidth || 380) - 176)), top: Math.min(Math.max(84, composer.y), Math.max(84, (boardRef.current?.clientHeight || 420) - 150)) }}
                    className="absolute z-[70] w-[340px] -translate-x-1/2 rounded-[18px] border border-white/80 bg-background/96 p-2 shadow-[0_24px_72px_rgba(15,23,42,.25)] backdrop-blur-xl dark:border-white/10"
                    onPointerDown={(event) => event.stopPropagation()}
                >
                    <div className="flex items-center gap-2 px-1 pb-2">
                        <span className="grid h-7 w-7 place-items-center rounded-lg bg-violet-500/10 text-violet-600"><Sparkles className="h-3.5 w-3.5" /></span>
                        <div className="min-w-0 flex-1"><div className="truncate text-[11px] font-semibold">{actionLabel(composer.action)}</div><div className="text-[9px] text-muted-foreground">将作为一条正常聊天消息发送，进度仍显示在消息区</div></div>
                        <button type="button" onClick={() => setComposer(null)} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted"><X className="h-3.5 w-3.5" /></button>
                    </div>
                    <textarea
                        autoFocus
                        rows={3}
                        value={composer.text}
                        onChange={(event) => setComposer((current) => current ? { ...current, text: event.target.value } : current)}
                        onKeyDown={(event) => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") void submitComposer(); }}
                        placeholder={composer.action.requiresPrompt ? "描述具体要求…" : "补充参数或要求（可选）…"}
                        className="w-full resize-none rounded-xl border border-border/70 bg-muted/25 px-3 py-2 text-xs leading-5 outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-400/15"
                    />
                    <div className="mt-2 flex items-center gap-2">
                        <span className="min-w-0 flex-1 truncate px-1 text-[9px] text-muted-foreground">引用 {composer.nodeIds.length} 项{composer.action.requiresMask ? " · 发送时冻结蒙版快照" : ""}</span>
                        <button type="button" disabled={submitting || (composer.action.requiresPrompt && !composer.text.trim())} onClick={() => void submitComposer()} className="flex h-8 items-center gap-1.5 rounded-xl bg-primary px-3 text-[11px] font-semibold text-primary-foreground disabled:opacity-35">{submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Send className="h-3.5 w-3.5" />}发送</button>
                    </div>
                </div>
            ) : null}

            {inspectNode && inspectResource ? (
                <div className="absolute inset-6 z-50 flex min-h-0 flex-col overflow-hidden rounded-[22px] border border-white/80 bg-background/96 shadow-[0_30px_100px_rgba(15,23,42,.28)] backdrop-blur-xl dark:border-white/10">
                    <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border/60 px-3"><span className="min-w-0 flex-1 truncate text-xs font-semibold">{inspectResource.name}</span>{inspectResource.url ? <a href={inspectResource.url} download={inspectResource.name} className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="下载"><Download className="h-4 w-4" /></a> : null}<button type="button" onClick={() => setInspectNodeId(null)} className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="关闭预览"><X className="h-4 w-4" /></button></div>
                    <div className="min-h-0 flex-1 bg-black/5"><CreativeCanvasMedia resource={inspectResource} inspect /></div>
                </div>
            ) : null}

            {maskNode && maskResource?.url && mediaTypeOf(maskResource) === "image" ? (
                <CreativeCanvasMaskEditor
                    src={maskResource.url}
                    title={maskResource.name}
                    value={maskNode.mask}
                    disabled={sessionRunning}
                    onChange={(value) => updateNodeMask(maskNode.nodeId, value)}
                    onClose={() => setMaskNodeId(null)}
                    onUse={(value) => {
                        updateNodeMask(maskNode.nodeId, value);
                        setMaskNodeId(null);
                        setSelectedIds([maskNode.nodeId]);
                        const action = CREATIVE_CANVAS_ACTIONS.find((item) => item.actionId === "creative_media.edit_image_region");
                        if (action) openComposerForAction(action, { x: (boardRef.current?.clientWidth || 600) / 2, y: 92, nodeIds: [maskNode.nodeId] });
                    }}
                />
            ) : null}

            {error ? <div className="absolute bottom-3 left-1/2 z-[80] flex max-w-lg -translate-x-1/2 items-center gap-2 rounded-xl border border-red-300/60 bg-red-50/95 px-3 py-2 text-[11px] text-red-800 shadow-lg backdrop-blur dark:border-red-500/20 dark:bg-red-950/90 dark:text-red-100"><span className="min-w-0 flex-1">{error}</span><button type="button" onClick={() => setError("")} className="rounded p-1 hover:bg-black/5"><X className="h-3.5 w-3.5" /></button></div> : null}

            <input ref={fileInputRef} type="file" multiple accept={MODEL_ACCEPT} onChange={handleFileChange} className="hidden" />
        </div>
    );
}
