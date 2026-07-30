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
} from "react";
import {
    Archive,
    AlignLeft,
    Check,
    Copy,
    Download,
    Folder,
    FolderPlus,
    Focus,
    Hand,
    ImagePlus,
    Link2,
    LayoutGrid,
    Loader2,
    Lock,
    MessageSquare,
    MousePointer2,
    MoveUp,
    PackageOpen,
    Play,
    Plus,
    Redo2,
    Rows3,
    Save,
    Sparkles,
    Trash2,
    Undo2,
    Columns3,
    Upload,
    Workflow,
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
    type CreativeCanvasMediaType,
} from "@/lib/creative-canvas-actions";
import {
    CreativeCanvasMaskEditor,
    CreativeCanvasMaskOverlay,
    rasterizeCreativeCanvasMask,
    type CreativeCanvasMaskState,
} from "./CreativeCanvasMaskEditor";
import {
    CreativeCanvasMedia,
} from "./CreativeCanvasMedia";
import {
    CreativeCanvasPsdCompositionEditor,
    CreativeCanvasPsdLayerEditor,
    type CanvasPsdComposition,
    type CanvasPsdLayerEdit,
} from "./CreativeCanvasPsdEditor";
import { useCanvasHistory } from "./creative-canvas/history";
import { CanvasActionMenu, CanvasMiniMap, CanvasPreflightPanel } from "./creative-canvas/overlays";
import { CanvasTimeRangeEditor } from "./creative-canvas/time-range-editor";
import {
    isValidCanvasFramePick,
    isValidCanvasTimeRange,
} from "./creative-canvas/timeline";
import {
    alignCanvasNodes,
    appendCanvasEdge,
    canvasOutputNode,
    canvasPortsForNode,
    canvasTargetHasAction,
    copyCanvasSubgraph,
    connectionPath,
    edgePath,
    getCanvasBounds,
    getCanvasPreflight,
    getConnectedNodeIds,
    getConnectionVerdict,
    getExecutionActionIds,
    isCanvasActionConfigured,
    layoutCanvasGraph,
    pasteCanvasSubgraph,
    portPoint,
    viewportForBounds,
    type CanvasPreflightIssue,
    type CanvasClipboard,
    type ConnectionIssue,
} from "./creative-canvas/graph-operations";
import {
    createId,
    isEditableTarget,
    mediaNodeDimensions,
    mediaTypeOf,
    normalizeResource,
    normalizeSnapshot,
    readSnapshot,
    recordOf,
    stringValue,
    toWebResourceUrl,
} from "./creative-canvas/serialization";
import {
    CANVAS_DRAG_TYPE,
    EMPTY_GRAPH_RUNTIME,
    EMPTY_SNAPSHOT,
    GRID_COLUMN_STEP,
    GRID_ROW_STEP,
    MAX_EDGES,
    MAX_NODES,
    MODEL_ACCEPT,
    NODE_HEIGHT,
    NODE_WIDTH,
    type CanvasActionDefinition,
    type CanvasEdge,
    type CanvasGraphRuntime,
    type CanvasNode,
    type CanvasPort,
    type CanvasResource,
    type CanvasSnapshot,
    type CanvasTaskRequest,
    type CanvasWorkflowTemplate,
    type ComposerState,
    type ConnectionDraft,
    type ContextMenuState,
    type EdgeNoteState,
    type PendingConnectionDrop,
    type PointerInteraction,
    type ResourceOrigin,
    type SelectionRect,
    type WorkspaceMediaFolder,
} from "./creative-canvas/types";

export type { CanvasTaskReference, CanvasTaskRequest } from "./creative-canvas/types";

export function CreativeArtifactCanvas({
    document,
    workspacePath,
    sessionRunning: upstreamSessionRunning = false,
    onSubmitTask,
}: {
    document: CreativeCanvasWorkbenchDocument;
    workspacePath?: string;
    messages?: Message[];
    sessionRunning?: boolean;
    onSubmitTask?: (request: CanvasTaskRequest) => Promise<boolean> | boolean;
}) {
    const t = useT();
    const sessionId = document.subjectRef.sessionId;
    const storageKey = `v8-web-creative-canvas:v3:${sessionId}`;
    const legacyStorageKey = `v8-web-creative-canvas:v2:${sessionId}`;
    const boardRef = useRef<HTMLDivElement | null>(null);
    const fileInputRef = useRef<HTMLInputElement | null>(null);
    const sessionRunningRef = useRef(upstreamSessionRunning);
    const sessionIdRef = useRef(sessionId);
    const catalogAbortRef = useRef<AbortController | null>(null);
    const interactionRef = useRef<PointerInteraction | null>(null);
    const pointerFrameRef = useRef<number | null>(null);
    const pendingPointerRef = useRef<{ clientX: number; clientY: number } | null>(null);
    const graphRevisionRef = useRef(0);
    const graphSavingRef = useRef(false);
    const graphSubmittingRef = useRef(false);
    const clipboardRef = useRef<CanvasClipboard | null>(null);
    const pendingGraphRef = useRef<CanvasSnapshot | null>(null);
    const lastSavedGraphRef = useRef("");
    const {
        value: snapshot,
        replace: setSnapshot,
        commit: commitSnapshot,
        reset: resetSnapshot,
        beginTransaction,
        finishTransaction,
        cancelTransaction,
        undo,
        redo,
        canUndo,
        canRedo,
    } = useCanvasHistory<CanvasSnapshot>(EMPTY_SNAPSHOT);
    const [graphRevision, setGraphRevision] = useState(0);
    const [graphRuntime, setGraphRuntime] = useState<CanvasGraphRuntime>(EMPTY_GRAPH_RUNTIME);
    const sessionRunning = upstreamSessionRunning || ["queued", "running"].includes(graphRuntime.status);
    const [graphSaving, setGraphSaving] = useState(false);
    const [actionDefinitions, setActionDefinitions] = useState<CanvasActionDefinition[]>([]);
    const [templates, setTemplates] = useState<CanvasWorkflowTemplate[]>([]);
    const [templateOpen, setTemplateOpen] = useState(false);
    const [templateTitle, setTemplateTitle] = useState("");
    const [savingTemplate, setSavingTemplate] = useState(false);
    const [edgeNote, setEdgeNote] = useState<EdgeNoteState | null>(null);
    const [hydratedKey, setHydratedKey] = useState("");
    const [resources, setResources] = useState<CanvasResource[]>([]);
    const [workspaceFolders, setWorkspaceFolders] = useState<WorkspaceMediaFolder[]>([]);
    const [activeFolderId, setActiveFolderId] = useState("");
    const [newFolderTitle, setNewFolderTitle] = useState("");
    const [newFolderKind, setNewFolderKind] = useState<WorkspaceMediaFolder["folderKind"]>("custom");
    const [creatingFolder, setCreatingFolder] = useState(false);
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
    const [reconnectingEdgeId, setReconnectingEdgeId] = useState<string | null>(null);
    const [connectionDraft, setConnectionDraft] = useState<ConnectionDraft | null>(null);
    const [hoveredEdgeId, setHoveredEdgeId] = useState<string | null>(null);
    const [inspectNodeId, setInspectNodeId] = useState<string | null>(null);
    const [inspectResourceOverride, setInspectResourceOverride] = useState<CanvasResource | null>(null);
    const [maskNodeId, setMaskNodeId] = useState<string | null>(null);
    const [spacePanning, setSpacePanning] = useState(false);
    const [preflightOpen, setPreflightOpen] = useState(false);
    const [preflightTargets, setPreflightTargets] = useState<string[]>([]);
    const [connectionIssue, setConnectionIssue] = useState<ConnectionIssue | null>(null);
    const [boardSize, setBoardSize] = useState({ width: 0, height: 0 });
    sessionRunningRef.current = sessionRunning;
    sessionIdRef.current = sessionId;

    useEffect(() => {
        const board = boardRef.current;
        if (!board) return;
        const update = () => setBoardSize({ width: board.clientWidth, height: board.clientHeight });
        update();
        const observer = new ResizeObserver(update);
        observer.observe(board);
        return () => observer.disconnect();
    }, []);

    const persistGraph = useCallback(async (candidate: CanvasSnapshot): Promise<boolean> => {
        pendingGraphRef.current = candidate;
        if (graphSavingRef.current) {
            while (graphSavingRef.current) {
                await new Promise((resolve) => window.setTimeout(resolve, 25));
            }
            return lastSavedGraphRef.current === JSON.stringify(candidate)
                ? true
                : persistGraph(candidate);
        }
        graphSavingRef.current = true;
        setGraphSaving(true);
        try {
            while (pendingGraphRef.current) {
                const graph = pendingGraphRef.current;
                pendingGraphRef.current = null;
                const serialized = JSON.stringify(graph);
                if (serialized === lastSavedGraphRef.current) continue;
                const response = await fetch(`/api/workbench/sessions/${encodeURIComponent(sessionId)}/canvas/graph`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ graph, expectedRevision: graphRevisionRef.current }),
                    cache: "no-store",
                });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) {
                    if (response.status === 409) {
                        const latest = await fetch(`/api/workbench/sessions/${encodeURIComponent(sessionId)}/canvas/graph`, { cache: "no-store" });
                        const latestPayload = await latest.json().catch(() => ({}));
                        if (latest.ok && sessionIdRef.current === sessionId && latestPayload?.graph) {
                            const recovered = normalizeSnapshot(latestPayload.graph);
                            graphRevisionRef.current = Number(latestPayload.revision || 0);
                            setGraphRevision(graphRevisionRef.current);
                            setGraphRuntime(recordOf(latestPayload.runtime) as CanvasGraphRuntime);
                            lastSavedGraphRef.current = JSON.stringify(recovered);
                            resetSnapshot(recovered);
                        }
                    }
                    throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
                }
                if (sessionIdRef.current !== sessionId) return false;
                graphRevisionRef.current = Number(payload.revision || graphRevisionRef.current + 1);
                setGraphRevision(graphRevisionRef.current);
                setGraphRuntime({ ...EMPTY_GRAPH_RUNTIME, ...recordOf(payload.runtime) } as CanvasGraphRuntime);
                lastSavedGraphRef.current = serialized;
                window.localStorage.removeItem(legacyStorageKey);
            }
            return true;
        } finally {
            graphSavingRef.current = false;
            setGraphSaving(false);
        }
    }, [legacyStorageKey, sessionId]);

    useEffect(() => {
        let cancelled = false;
        setHydratedKey("");
        const cached = window.localStorage.getItem(storageKey)
            ? readSnapshot(storageKey)
            : readSnapshot(legacyStorageKey);
        resetSnapshot(cached);
        graphRevisionRef.current = 0;
        setGraphRevision(0);
        setGraphRuntime(EMPTY_GRAPH_RUNTIME);
        lastSavedGraphRef.current = "";
        pendingGraphRef.current = null;
        setResources([]);
        setWorkspaceFolders([]);
        setActiveFolderId("");
        setSelectedIds([]);
        setContextMenu(null);
        setComposer(null);
        setInspectNodeId(null);
        setInspectResourceOverride(null);
        setMaskNodeId(null);
        setConnectionSourceId(null);
        setConnectionDraft(null);
        setEdgeNote(null);
        setTemplateOpen(false);
        interactionRef.current = null;
        const base = `/api/workbench/sessions/${encodeURIComponent(sessionId)}/canvas`;
        void Promise.all([
            fetch(`${base}/graph`, { cache: "no-store" }),
            fetch(`${base}/actions`, { cache: "no-store" }),
            fetch(`${base}/templates`, { cache: "no-store" }),
        ]).then(async ([graphResponse, actionResponse, templateResponse]) => {
            const [graphPayload, actionPayload, templatePayload] = await Promise.all([
                graphResponse.json().catch(() => ({})),
                actionResponse.json().catch(() => ({})),
                templateResponse.json().catch(() => ({})),
            ]);
            if (!graphResponse.ok) throw new Error(String(graphPayload?.detail || graphPayload?.error || `HTTP ${graphResponse.status}`));
            if (!actionResponse.ok) throw new Error(String(actionPayload?.detail || actionPayload?.error || `HTTP ${actionResponse.status}`));
            if (!templateResponse.ok) throw new Error(String(templatePayload?.detail || templatePayload?.error || `HTTP ${templateResponse.status}`));
            if (cancelled || sessionIdRef.current !== sessionId) return;
            const recovered = graphPayload?.graph ? normalizeSnapshot(graphPayload.graph) : cached;
            if (graphPayload?.graph) window.localStorage.removeItem(legacyStorageKey);
            resetSnapshot(recovered);
            graphRevisionRef.current = Number(graphPayload?.revision || 0);
            setGraphRevision(graphRevisionRef.current);
            setGraphRuntime({ ...EMPTY_GRAPH_RUNTIME, ...recordOf(graphPayload?.runtime) } as CanvasGraphRuntime);
            lastSavedGraphRef.current = graphPayload?.graph ? JSON.stringify(recovered) : "";
            setActionDefinitions((Array.isArray(actionPayload?.actions) ? actionPayload.actions : []).map((item: unknown) => {
                const action = recordOf(item);
                const output = recordOf(action.output);
                return {
                    actionId: stringValue(action, "actionId"),
                    inputs: (Array.isArray(action.inputs) ? action.inputs : []).map((raw: unknown) => {
                        const port = recordOf(raw);
                        return {
                            portId: stringValue(port, "portId"),
                            mediaTypes: (Array.isArray(port.mediaTypes) ? port.mediaTypes : []).map(String) as CreativeCanvasMediaType[],
                            min: Number(port.min || 0),
                            max: Number(port.max || 1),
                            ordered: Boolean(port.ordered),
                        };
                    }),
                    output: {
                        portId: stringValue(output, "portId") || "output",
                        slot: stringValue(output, "slot") || "output",
                        mediaTypes: (Array.isArray(output.mediaTypes) ? output.mediaTypes : ["unknown"]).map(String) as CreativeCanvasMediaType[],
                    },
                    requiresPrompt: Boolean(action.requiresPrompt),
                    parameterEditor: ["frame_pick", "time_range", "psd_composition", "psd_layers"].includes(String(action.parameterEditor))
                        ? action.parameterEditor as CanvasActionDefinition["parameterEditor"]
                        : undefined,
                    networkRequired: Boolean(action.networkRequired),
                    mayIncurCost: Boolean(action.mayIncurCost),
                } satisfies CanvasActionDefinition;
            }).filter((item: CanvasActionDefinition) => Boolean(item.actionId)));
            setTemplates((Array.isArray(templatePayload?.templates) ? templatePayload.templates : []) as CanvasWorkflowTemplate[]);
            setHydratedKey(storageKey);
        }).catch((reason) => {
            if (!cancelled) {
                setError(reason instanceof Error ? reason.message : String(reason));
                setHydratedKey(storageKey);
            }
        });
        return () => { cancelled = true; };
    }, [legacyStorageKey, sessionId, storageKey]);

    useEffect(() => {
        if (hydratedKey !== storageKey) return;
        const timeout = window.setTimeout(() => {
            try {
                window.localStorage.setItem(storageKey, JSON.stringify(snapshot));
            } catch {
                // Browser cache is best effort; the Session graph remains authoritative in Engine.
            }
            if (!sessionRunningRef.current) {
                void persistGraph(snapshot).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
            }
        }, 420);
        return () => window.clearTimeout(timeout);
    }, [hydratedKey, persistGraph, snapshot, storageKey]);

    const loadCatalog = useCallback(async (silent = false) => {
        catalogAbortRef.current?.abort();
        const controller = new AbortController();
        catalogAbortRef.current = controller;
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
            if (controller.signal.aborted || sessionIdRef.current !== sessionId) return;
            const merged = [...artifacts, ...sources];
            setResources((current) => {
                const byKey = new Map(current.filter((item) => item.origin === "workspace_asset").map((item) => [`${item.origin}:${item.id}`, item]));
                for (const item of merged) byKey.set(`${item.origin}:${item.id}`, item);
                return [...byKey.values()];
            });
            const mediaBase = `/api/workbench/sessions/${encodeURIComponent(sessionId)}/media`;
            if (!silent) {
                const reconcileResponse = await fetch(`${mediaBase}/reconcile`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: "{}",
                    cache: "no-store",
                    signal: controller.signal,
                });
                const reconcilePayload = await reconcileResponse.json().catch(() => ({}));
                if (!reconcileResponse.ok) throw new Error(String(reconcilePayload?.detail || reconcilePayload?.error || `HTTP ${reconcileResponse.status}`));
            }
            const [assetResponse, folderResponse] = await Promise.all([
                fetch(`${mediaBase}/assets?limit=500`, { cache: "no-store", signal: controller.signal }),
                fetch(`${mediaBase}/folders`, { cache: "no-store", signal: controller.signal }),
            ]);
            const [assetPayload, folderPayload] = await Promise.all([
                assetResponse.json().catch(() => ({})),
                folderResponse.json().catch(() => ({})),
            ]);
            if (controller.signal.aborted || sessionIdRef.current !== sessionId) return;
            if (!assetResponse.ok) throw new Error(String(assetPayload?.detail || assetPayload?.error || `HTTP ${assetResponse.status}`));
            if (!folderResponse.ok) throw new Error(String(folderPayload?.detail || folderPayload?.error || `HTTP ${folderResponse.status}`));
            const workspaceAssets = (Array.isArray(assetPayload?.assets) ? assetPayload.assets : [])
                .map((entry: unknown, index: number) => normalizeResource(entry, "workspace_asset", sessionId, index))
                .filter((item: CanvasResource | null): item is CanvasResource => Boolean(item));
            setResources((current) => [
                ...current.filter((item) => item.origin !== "workspace_asset"),
                ...workspaceAssets,
            ]);
            setWorkspaceFolders((Array.isArray(folderPayload?.folders) ? folderPayload.folders : []).flatMap((raw: unknown) => {
                const folder = recordOf(raw);
                const folderId = stringValue(folder, "folderId", "folder_id");
                if (!folderId) return [];
                const folderKind = String(folder.folderKind || folder.folder_kind || "custom") as WorkspaceMediaFolder["folderKind"];
                return [{
                    folderId,
                    parentFolderId: stringValue(folder, "parentFolderId", "parent_folder_id") || undefined,
                    folderKind,
                    title: stringValue(folder, "title") || folderId,
                    assetCount: Number(folder.assetCount || folder.asset_count || 0),
                }];
            }));
            setError("");
        } catch (reason) {
            if (!controller.signal.aborted && sessionIdRef.current === sessionId && !silent) setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            if (catalogAbortRef.current === controller) {
                catalogAbortRef.current = null;
                if (!silent && !controller.signal.aborted && sessionIdRef.current === sessionId) setLoading(false);
            }
        }
    }, [sessionId]);

    useEffect(() => {
        void loadCatalog();
        return () => catalogAbortRef.current?.abort();
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

    const hasPendingResult = snapshot.nodes.some((node) => (
        node.kind === "result"
        && ["reserved", "running", "waiting"].includes(String(node.operationState))
    ));
    useEffect(() => {
        if (!sessionRunning && !hasPendingResult) return;
        const interval = window.setInterval(() => {
            if (!catalogAbortRef.current) void loadCatalog(true);
        }, 3500);
        return () => window.clearInterval(interval);
    }, [hasPendingResult, loadCatalog, sessionRunning]);

    const resourceMap = useMemo(
        () => new Map(resources.map((item) => [`${item.origin}:${item.id}`, item])),
        [resources],
    );
    const resourceForNode = useCallback((node: CanvasNode) => {
        if (node.kind !== "resource" || !node.resourceId || node.origin === "placeholder") return null;
        return resourceMap.get(`${node.origin}:${node.resourceId}`) || null;
    }, [resourceMap]);
    const displayResourceForNode = useCallback((node: CanvasNode): CanvasResource | null => {
        const direct = resourceForNode(node);
        if (direct) return direct;
        if (node.kind !== "result") return null;
        const artifactId = graphRuntime.outputs[node.nodeId]?.[0]?.artifactId;
        return artifactId ? resourceMap.get(`artifact:${artifactId}`) || null : null;
    }, [graphRuntime.outputs, resourceForNode, resourceMap]);
    const mediaTypeForNode = useCallback((node: CanvasNode): CreativeCanvasMediaType => {
        const resource = displayResourceForNode(node);
        return resource ? mediaTypeOf(resource) : node.mediaType || "unknown";
    }, [displayResourceForNode]);

    useEffect(() => {
        if (!sessionRunning && !["queued", "running"].includes(graphRuntime.status)) return;
        const refresh = async () => {
            try {
                const response = await fetch(`/api/workbench/sessions/${encodeURIComponent(sessionId)}/canvas/graph`, { cache: "no-store" });
                const payload = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
                if (sessionIdRef.current !== sessionId) return;
                setGraphRuntime({ ...EMPTY_GRAPH_RUNTIME, ...recordOf(payload.runtime) } as CanvasGraphRuntime);
                if (String(payload?.runtime?.status || "") === "succeeded") void loadCatalog(true);
            } catch (reason) {
                setError(reason instanceof Error ? reason.message : String(reason));
            }
        };
        void refresh();
        const interval = window.setInterval(() => void refresh(), 2200);
        return () => window.clearInterval(interval);
    }, [graphRuntime.status, loadCatalog, sessionId, sessionRunning]);

    useEffect(() => {
        if (!sessionRunning) return;
        setComposer(null);
        setContextMenu(null);
        setPendingConnectionDrop(null);
        setConnectionIssue(null);
        setMaskNodeId(null);
        setConnectionSourceId(null);
        setConnectionDraft(null);
        setReconnectingEdgeId(null);
        if (interactionRef.current?.kind === "connect" || interactionRef.current?.kind === "reconnect") interactionRef.current = null;
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

    const placeResource = useCallback((resource: CanvasResource, point?: { x: number; y: number }, connectFrom?: Pick<PendingConnectionDrop, "fromNodeId" | "fromPort" | "direction">) => {
        if (sessionRunningRef.current) return "";
        const nodeId = createId("canvas-node");
        commitSnapshot((current) => {
            if (current.nodes.length >= MAX_NODES) return current;
            const availableWidth = ((boardRef.current?.clientWidth || 920) - 96) / current.viewport.scale;
            const columns = Math.max(1, Math.min(4, Math.floor(availableWidth / GRID_COLUMN_STEP)));
            const offsetColumn = current.nodes.length % columns;
            const offsetRow = Math.floor(current.nodes.length / columns);
            const node: CanvasNode = {
                nodeId,
                kind: "resource",
                origin: resource.origin,
                resourceId: resource.id,
                title: resource.name,
                mediaType: mediaTypeOf(resource),
                x: point?.x ?? (60 - current.viewport.x) / current.viewport.scale + offsetColumn * GRID_COLUMN_STEP,
                y: point?.y ?? (72 - current.viewport.y) / current.viewport.scale + offsetRow * GRID_ROW_STEP,
                width: NODE_WIDTH,
                height: mediaTypeOf(resource) === "audio" ? 142 : NODE_HEIGHT,
            };
            setSelectedIds([node.nodeId]);
            const withNode = { ...current, nodes: [...current.nodes, node] };
            return connectFrom && connectFrom.fromNodeId !== node.nodeId
                ? appendCanvasEdge(withNode, actionDefinitions, connectFrom.direction === "input"
                    ? { from: node.nodeId, to: connectFrom.fromNodeId }
                    : { from: connectFrom.fromNodeId, to: node.nodeId })
                : withNode;
        });
        return nodeId;
    }, [actionDefinitions]);

    const adoptWorkspaceResource = useCallback(async (resource: CanvasResource) => {
        let adopted = resource;
        if (resource.origin === "workspace_asset" && !resource.adoptedByCurrentSession) {
            const response = await fetch(
                `/api/workbench/sessions/${encodeURIComponent(sessionId)}/media/assets/${encodeURIComponent(resource.id)}/use`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ context: { surface: "creative_canvas" } }),
                },
            );
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
            if (sessionIdRef.current !== sessionId) throw new Error("Canvas session changed while adopting workspace media");
            adopted = { ...resource, adoptedByCurrentSession: true };
            setResources((current) => current.map((item) => item.origin === resource.origin && item.id === resource.id ? adopted : item));
        }
        return adopted;
    }, [sessionId]);

    const adoptAndPlaceResource = useCallback(async (
        resource: CanvasResource,
        point?: { x: number; y: number },
        connectFrom?: Pick<PendingConnectionDrop, "fromNodeId" | "fromPort" | "direction">,
    ) => {
        const adopted = await adoptWorkspaceResource(resource);
        if (sessionIdRef.current !== sessionId) return;
        placeResource(adopted, point, connectFrom);
    }, [adoptWorkspaceResource, placeResource, sessionId]);

    const createWorkspaceFolder = useCallback(async () => {
        const title = newFolderTitle.trim();
        if (!title || creatingFolder || sessionRunning) return;
        setCreatingFolder(true);
        try {
            const response = await fetch(`/api/workbench/sessions/${encodeURIComponent(sessionId)}/media/folders`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    title,
                    folderKind: newFolderKind,
                    ...(activeFolderId ? { parentFolderId: activeFolderId } : {}),
                }),
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
            if (sessionIdRef.current !== sessionId) return;
            const folder = recordOf(payload?.folder);
            const folderId = stringValue(folder, "folderId", "folder_id");
            if (!folderId) throw new Error("Folder response is missing folderId");
            setWorkspaceFolders((current) => [...current, {
                folderId,
                parentFolderId: stringValue(folder, "parentFolderId", "parent_folder_id") || undefined,
                folderKind: String(folder.folderKind || "custom") as WorkspaceMediaFolder["folderKind"],
                title: stringValue(folder, "title") || title,
                assetCount: Number(folder.assetCount || 0),
            }]);
            setNewFolderTitle("");
            setError("");
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setCreatingFolder(false);
        }
    }, [activeFolderId, creatingFolder, newFolderKind, newFolderTitle, sessionId, sessionRunning]);

    const moveWorkspaceAsset = useCallback(async (assetId: string, folderId: string) => {
        if (sessionRunning) return;
        try {
            const response = await fetch(
                `/api/workbench/sessions/${encodeURIComponent(sessionId)}/media/assets/${encodeURIComponent(assetId)}/placement`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ folderId: folderId || null }),
                },
            );
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
            if (sessionIdRef.current !== sessionId) return;
            setResources((current) => current.map((item) => item.origin === "workspace_asset" && item.id === assetId
                ? { ...item, folderId: folderId || undefined }
                : item));
            setError("");
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        }
    }, [sessionId, sessionRunning]);

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
        for (const node of snapshot.nodes) {
            if (node.kind === "result" && (removing.has(node.nodeId) || removing.has(String(node.producerActionNodeId || "")))) {
                removing.add(node.nodeId);
                if (node.producerActionNodeId) removing.add(node.producerActionNodeId);
            }
        }
        commitSnapshot((current) => ({
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
    }, [connectionDraft, inspectNodeId, sessionRunning, snapshot.nodes]);

    const addEdge = useCallback((from: string, to: string, fromPort: CanvasPort = "right", toPort: CanvasPort = "left") => {
        if (sessionRunning || !from || !to || from === to) return;
        commitSnapshot((current) => appendCanvasEdge(current, actionDefinitions, { from, to }));
    }, [actionDefinitions, commitSnapshot, sessionRunning]);

    const connectSelection = useCallback((ids: string[]) => {
        if (sessionRunning || ids.length < 2) return;
        const ordered = snapshot.nodes
            .filter((node) => ids.includes(node.nodeId))
            .sort((left, right) => left.x - right.x || left.y - right.y);
        commitSnapshot((current) => {
            const edges = [...current.edges];
            for (let index = 0; index < ordered.length - 1; index += 1) {
                const from = ordered[index].nodeId;
                const to = ordered[index + 1].nodeId;
                if (!edges.some((edge) => edge.from === from && edge.to === to && edge.fromPort === "right" && edge.toPort === "left") && edges.length < MAX_EDGES) {
                    const source = current.nodes.find((node) => node.nodeId === from);
                    edges.push({
                        edgeId: createId("canvas-edge"),
                        from,
                        to,
                        fromPort: "right",
                        toPort: "left",
                        fromPortId: "relation",
                        toPortId: "relation",
                        dataType: source?.mediaType || "unknown",
                        role: "relation",
                        order: 0,
                        note: "",
                    });
                }
            }
            return { ...current, edges };
        });
    }, [sessionRunning, snapshot.nodes]);

    const fitView = useCallback(() => {
        const rect = boardRef.current?.getBoundingClientRect();
        if (!rect) return;
        const bounds = getCanvasBounds(snapshot.nodes);
        if (!bounds) {
            setSnapshot((current) => ({ ...current, viewport: { x: 24, y: 24, scale: 1 } }));
            return;
        }
        setSnapshot((current) => ({ ...current, viewport: viewportForBounds(bounds, rect.width, rect.height) }));
    }, [snapshot.nodes]);

    const focusNodes = useCallback((nodeIds: string[]) => {
        const rect = boardRef.current?.getBoundingClientRect();
        if (!rect) return;
        const bounds = getCanvasBounds(snapshot.nodes.filter((node) => nodeIds.includes(node.nodeId)));
        if (!bounds) return;
        setSnapshot((current) => ({ ...current, viewport: viewportForBounds(bounds, rect.width, rect.height, 1.8) }));
    }, [snapshot.nodes]);

    const navigateMiniMap = useCallback((worldX: number, worldY: number) => {
        setSnapshot((current) => ({
            ...current,
            viewport: {
                ...current.viewport,
                x: boardSize.width / 2 - worldX * current.viewport.scale,
                y: boardSize.height / 2 - worldY * current.viewport.scale,
            },
        }));
    }, [boardSize.height, boardSize.width]);

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

    const handleWheel = useCallback((event: globalThis.WheelEvent) => {
        if ((event.target as HTMLElement | null)?.closest("[data-canvas-wheel-isolation]")) return;
        event.preventDefault();
        const deltaY = event.deltaY;
        if (!deltaY) return;
        const point = boardPoint(event.clientX, event.clientY);
        setSnapshot((current) => {
            const factor = Math.exp(-deltaY * 0.002);
            const scale = Math.max(0.25, Math.min(2.5, current.viewport.scale * factor));
            const worldX = (point.x - current.viewport.x) / current.viewport.scale;
            const worldY = (point.y - current.viewport.y) / current.viewport.scale;
            return { ...current, viewport: { scale, x: point.x - worldX * scale, y: point.y - worldY * scale } };
        });
    }, [boardPoint]);

    useEffect(() => {
        const board = boardRef.current;
        if (!board) return;
        board.addEventListener("wheel", handleWheel, { passive: false });
        return () => board.removeEventListener("wheel", handleWheel);
    }, [handleWheel]);

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
        if (interaction.kind === "connect" || interaction.kind === "reconnect") {
            if (sessionRunningRef.current) return;
            setConnectionDraft((current) => current ? { ...current, target: world } : current);
            const candidatePort: CanvasPort = interaction.kind === "connect"
                ? (interaction.fromPort === "right" ? "left" : "right")
                : (interaction.movingEnd === "from" ? "right" : "left");
            const candidates = snapshot.nodes.flatMap((node) => canvasPortsForNode(node).includes(candidatePort)
                ? [{ node, distance: Math.hypot(portPoint(node, candidatePort).x - world.x, portPoint(node, candidatePort).y - world.y) }]
                : []);
            const nearest = candidates.sort((left, right) => left.distance - right.distance)[0];
            if (!nearest || nearest.distance > 46 / snapshot.viewport.scale) setConnectionIssue(null);
            else {
                const base = interaction.kind === "reconnect"
                    ? { ...snapshot, edges: snapshot.edges.filter((edge) => edge.edgeId !== interaction.edgeId) }
                    : snapshot;
                const from = interaction.kind === "connect"
                    ? (interaction.fromPort === "right" ? interaction.fromNodeId : nearest.node.nodeId)
                    : (interaction.movingEnd === "to" ? interaction.fixedNodeId : nearest.node.nodeId);
                const to = interaction.kind === "connect"
                    ? (interaction.fromPort === "right" ? nearest.node.nodeId : interaction.fromNodeId)
                    : (interaction.movingEnd === "to" ? nearest.node.nodeId : interaction.fixedNodeId);
                const verdict = getConnectionVerdict(base, actionDefinitions, from, to);
                setConnectionIssue(verdict.valid ? null : verdict.issue || "incompatible-type");
            }
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
    }, [actionDefinitions, sessionRunning, snapshot, worldPoint]);

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
                const candidatePort: CanvasPort = interaction.fromPort === "right" ? "left" : "right";
                for (const node of snapshot.nodes) {
                    if (node.nodeId === interaction.fromNodeId) continue;
                    for (const port of canvasPortsForNode(node).filter((item) => item === candidatePort)) {
                        const point = portPoint(node, port);
                        const distance = Math.hypot(point.x - end.x, point.y - end.y);
                        if (distance <= threshold && (!nearest || distance < nearest.distance)) nearest = { nodeId: node.nodeId, port, distance };
                    }
                }
                if (nearest && !sessionRunningRef.current) {
                    const from = interaction.fromPort === "left" ? nearest.nodeId : interaction.fromNodeId;
                    const to = interaction.fromPort === "left" ? interaction.fromNodeId : nearest.nodeId;
                    const verdict = getConnectionVerdict(snapshot, actionDefinitions, from, to);
                    if (verdict.valid) {
                        addEdge(from, to);
                        setSelectedIds([interaction.fromNodeId, nearest.nodeId]);
                        setPendingConnectionDrop(null);
                        setConnectionIssue(null);
                    } else {
                        setConnectionIssue(verdict.issue || "incompatible-type");
                    }
                } else if (!sessionRunningRef.current) {
                    const menuPoint = boardPoint(event.clientX, event.clientY);
                    setPendingConnectionDrop({
                        fromNodeId: interaction.fromNodeId,
                        fromPort: interaction.fromPort,
                        direction: interaction.fromPort === "left" ? "input" : "output",
                        point: end,
                    });
                    setSelectedIds([interaction.fromNodeId]);
                    setContextMenu({ x: menuPoint.x, y: menuPoint.y, target: "node", nodeIds: [interaction.fromNodeId] });
                }
            }
            setConnectionDraft(null);
            setConnectionSourceId(null);
        }
        if (interaction.kind === "reconnect") {
            if (event.type !== "pointercancel" && !sessionRunningRef.current) {
                const end = worldPoint(event.clientX, event.clientY);
                const threshold = 34 / snapshot.viewport.scale;
                let nearest: { nodeId: string; port: CanvasPort; distance: number } | null = null;
                const candidatePort: CanvasPort = interaction.movingEnd === "from" ? "right" : "left";
                for (const node of snapshot.nodes) {
                    if (node.nodeId === interaction.fixedNodeId) continue;
                    for (const port of canvasPortsForNode(node).filter((item) => item === candidatePort)) {
                        const point = portPoint(node, port);
                        const distance = Math.hypot(point.x - end.x, point.y - end.y);
                        if (distance <= threshold && (!nearest || distance < nearest.distance)) nearest = { nodeId: node.nodeId, port, distance };
                    }
                }
                const original = snapshot.edges.find((edge) => edge.edgeId === interaction.edgeId);
                const withoutOriginal = { ...snapshot, edges: snapshot.edges.filter((edge) => edge.edgeId !== interaction.edgeId) };
                const verdict = nearest && original
                    ? getConnectionVerdict(
                        withoutOriginal,
                        actionDefinitions,
                        interaction.movingEnd === "to" ? interaction.fixedNodeId : nearest.nodeId,
                        interaction.movingEnd === "to" ? nearest.nodeId : interaction.fixedNodeId,
                    )
                    : null;
                commitSnapshot((current) => {
                    const currentOriginal = current.edges.find((edge) => edge.edgeId === interaction.edgeId);
                    const currentWithoutOriginal = { ...current, edges: current.edges.filter((edge) => edge.edgeId !== interaction.edgeId) };
                    if (!nearest || !currentOriginal) return currentWithoutOriginal;
                    if (!verdict?.valid) return current;
                    return appendCanvasEdge(currentWithoutOriginal, actionDefinitions, interaction.movingEnd === "to"
                        ? { from: interaction.fixedNodeId, to: nearest.nodeId, edgeId: currentOriginal.edgeId, note: currentOriginal.note }
                        : { from: nearest.nodeId, to: interaction.fixedNodeId, edgeId: currentOriginal.edgeId, note: currentOriginal.note });
                });
                setConnectionIssue(verdict?.valid || !nearest ? null : verdict?.issue || "incompatible-type");
            }
            setConnectionDraft(null);
            setReconnectingEdgeId(null);
        }
        if (interaction.kind === "move") {
            if (event.type === "pointercancel") cancelTransaction();
            else finishTransaction();
        }
        interactionRef.current = null;
        pendingPointerRef.current = null;
        setSelectionRect(null);
        if (boardRef.current?.hasPointerCapture(event.pointerId)) boardRef.current.releasePointerCapture(event.pointerId);
    }, [actionDefinitions, addEdge, boardPoint, cancelTransaction, finishTransaction, processPointerMove, snapshot, worldPoint]);

    const startBoardInteraction = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
        if (event.button !== 0 && event.button !== 1) return;
        if (event.target !== event.currentTarget) return;
        setContextMenu(null);
        setPendingConnectionDrop(null);
        setConnectionIssue(null);
        const usePan = tool === "pan" || spacePanning || event.button === 1;
        interactionRef.current = usePan
            ? { kind: "pan", pointerId: event.pointerId, start: { x: event.clientX, y: event.clientY }, initial: snapshot.viewport }
            : { kind: "select", pointerId: event.pointerId, start: worldPoint(event.clientX, event.clientY), additive: event.shiftKey };
        event.currentTarget.setPointerCapture(event.pointerId);
        if (!usePan) setSelectionRect({ ...worldPoint(event.clientX, event.clientY), startX: worldPoint(event.clientX, event.clientY).x, startY: worldPoint(event.clientX, event.clientY).y });
    }, [snapshot.viewport, spacePanning, tool, worldPoint]);

    const handleNodePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>, node: CanvasNode) => {
        if (event.button !== 0 || (event.target as HTMLElement).closest("audio,video,button,a,input,textarea")) return;
        event.stopPropagation();
        setContextMenu(null);
        setPendingConnectionDrop(null);
        const nextSelection = event.shiftKey
            ? (selectedIds.includes(node.nodeId) ? selectedIds.filter((id) => id !== node.nodeId) : [...selectedIds, node.nodeId])
            : (selectedIds.includes(node.nodeId) ? selectedIds : [node.nodeId]);
        setSelectedIds(nextSelection);
        if (sessionRunning) return;
        beginTransaction();
        const start = worldPoint(event.clientX, event.clientY);
        const selectedSet = new Set(nextSelection.length ? nextSelection : [node.nodeId]);
        interactionRef.current = {
            kind: "move",
            pointerId: event.pointerId,
            start,
            initial: new Map(snapshot.nodes.filter((item) => selectedSet.has(item.nodeId)).map((item) => [item.nodeId, { x: item.x, y: item.y }])),
        };
        event.currentTarget.setPointerCapture(event.pointerId);
    }, [beginTransaction, selectedIds, sessionRunning, snapshot.nodes, worldPoint]);

    const handlePortPointerDown = useCallback((event: ReactPointerEvent<HTMLButtonElement>, node: CanvasNode, port: CanvasPort) => {
        if (event.button !== 0 || sessionRunningRef.current) return;
        event.preventDefault();
        event.stopPropagation();
        setContextMenu(null);
        setComposer(null);
        setSelectedIds([node.nodeId]);
        setConnectionSourceId(node.nodeId);
        setConnectionIssue(null);
        setConnectionDraft({ fromNodeId: node.nodeId, fromPort: port, target: portPoint(node, port) });
        interactionRef.current = { kind: "connect", pointerId: event.pointerId, fromNodeId: node.nodeId, fromPort: port };
        boardRef.current?.setPointerCapture(event.pointerId);
    }, []);

    const handleEdgePointerDown = useCallback((event: ReactPointerEvent<SVGPathElement>, edge: CanvasEdge, from: CanvasNode, to: CanvasNode) => {
        if (event.button !== 0 || sessionRunningRef.current) return;
        if (from.kind === "action" && to.kind === "result" && to.producerActionNodeId === from.nodeId) return;
        const pointer = worldPoint(event.clientX, event.clientY);
        const fromPoint = portPoint(from, edge.fromPort);
        const toPoint = portPoint(to, edge.toPort);
        const fromDistance = Math.hypot(pointer.x - fromPoint.x, pointer.y - fromPoint.y);
        const toDistance = Math.hypot(pointer.x - toPoint.x, pointer.y - toPoint.y);
        if (Math.min(fromDistance, toDistance) > 56 / snapshot.viewport.scale) return;
        event.preventDefault();
        event.stopPropagation();
        const movingEnd = fromDistance <= toDistance ? "from" : "to";
        const fixedNode = movingEnd === "from" ? to : from;
        const fixedPort = movingEnd === "from" ? edge.toPort : edge.fromPort;
        setContextMenu(null);
        setComposer(null);
        setSelectedIds([edge.from, edge.to]);
        setReconnectingEdgeId(edge.edgeId);
        setConnectionIssue(null);
        setConnectionDraft({ fromNodeId: fixedNode.nodeId, fromPort: fixedPort, target: pointer });
        interactionRef.current = { kind: "reconnect", pointerId: event.pointerId, edgeId: edge.edgeId, movingEnd, fixedNodeId: fixedNode.nodeId, fixedPort };
        boardRef.current?.setPointerCapture(event.pointerId);
    }, [snapshot.viewport.scale, worldPoint]);

    useEffect(() => () => {
        if (pointerFrameRef.current !== null) window.cancelAnimationFrame(pointerFrameRef.current);
    }, []);

    const copySelection = useCallback(() => {
        const clipboard = copyCanvasSubgraph(snapshot, selectedIds);
        if (!clipboard) return false;
        clipboardRef.current = clipboard;
        return true;
    }, [selectedIds, snapshot]);

    const pasteSelection = useCallback(() => {
        const clipboard = clipboardRef.current;
        if (!clipboard || sessionRunning) return;
        let pastedIds: string[] = [];
        commitSnapshot((current) => {
            if (current.nodes.length + clipboard.nodes.length > MAX_NODES || current.edges.length + clipboard.edges.length > MAX_EDGES) return current;
            const pasted = pasteCanvasSubgraph(current, clipboard, createId);
            pastedIds = pasted.nodeIds;
            return pasted.snapshot;
        });
        if (pastedIds.length) setSelectedIds(pastedIds);
    }, [commitSnapshot, sessionRunning]);

    const duplicateSelection = useCallback(() => {
        if (!copySelection()) return;
        pasteSelection();
    }, [copySelection, pasteSelection]);

    const alignSelection = useCallback((mode: "left" | "top" | "horizontal" | "vertical") => {
        if (sessionRunning || selectedIds.length < 2) return;
        commitSnapshot((current) => alignCanvasNodes(current, selectedIds, mode));
    }, [commitSnapshot, selectedIds, sessionRunning]);

    const organizeLayout = useCallback((nodeIds: string[] = []) => {
        if (sessionRunning) return;
        commitSnapshot((current) => layoutCanvasGraph(current, nodeIds));
    }, [commitSnapshot, sessionRunning]);

    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => {
            const editable = isEditableTarget(event.target);
            if (event.code === "Space" && !editable) {
                event.preventDefault();
                setSpacePanning(true);
            }
            if (event.key === "Escape") {
                setContextMenu(null);
                setComposer(null);
                setPreflightOpen(false);
                setTemplateOpen(false);
                setTrayOpen(false);
                setEdgeNote(null);
                setPendingConnectionDrop(null);
                setInspectNodeId(null);
                setMaskNodeId(null);
                setConnectionSourceId(null);
                setConnectionDraft(null);
                setReconnectingEdgeId(null);
                setConnectionIssue(null);
                if (interactionRef.current?.kind === "move") cancelTransaction();
                interactionRef.current = null;
                return;
            }
            if (editable) return;
            const command = event.metaKey || event.ctrlKey;
            if (command && event.key.toLowerCase() === "z" && !sessionRunning) {
                event.preventDefault();
                if (event.shiftKey) redo();
                else undo();
                return;
            }
            if (command && event.key.toLowerCase() === "y" && !sessionRunning) {
                event.preventDefault();
                redo();
                return;
            }
            if (command && event.key.toLowerCase() === "c") {
                event.preventDefault();
                copySelection();
                return;
            }
            if (command && event.key.toLowerCase() === "v" && !sessionRunning) {
                event.preventDefault();
                pasteSelection();
                return;
            }
            if (command && event.key.toLowerCase() === "d" && !sessionRunning) {
                event.preventDefault();
                duplicateSelection();
                return;
            }
            if (event.key.toLowerCase() === "f" && selectedIds.length) {
                event.preventDefault();
                focusNodes(selectedIds);
                return;
            }
            if ((event.key === "Delete" || event.key === "Backspace") && selectedIds.length && !sessionRunning) {
                event.preventDefault();
                removeNodes(selectedIds);
            }
        };
        const onKeyUp = (event: KeyboardEvent) => {
            if (event.code === "Space") setSpacePanning(false);
        };
        window.addEventListener("keydown", onKeyDown);
        window.addEventListener("keyup", onKeyUp);
        return () => {
            window.removeEventListener("keydown", onKeyDown);
            window.removeEventListener("keyup", onKeyUp);
        };
    }, [cancelTransaction, copySelection, duplicateSelection, focusNodes, pasteSelection, redo, removeNodes, selectedIds, sessionRunning, undo]);

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
    const selectedExecutableTargetIds = useMemo(
        () => selectedIds.filter((nodeId) => canvasTargetHasAction(snapshot, nodeId)),
        [selectedIds, snapshot],
    );
    const preflightIssues = useMemo(
        () => getCanvasPreflight(snapshot, actionDefinitions, graphRuntime),
        [actionDefinitions, graphRuntime, snapshot],
    );
    const executionActionIds = useMemo(
        () => getExecutionActionIds(snapshot, preflightTargets),
        [preflightTargets, snapshot],
    );
    const visiblePreflightIssues = useMemo(
        () => preflightIssues.filter((issue) => executionActionIds.has(issue.nodeId)),
        [executionActionIds, preflightIssues],
    );
    const emphasizedNodeIds = useMemo(() => {
        const related = getConnectedNodeIds(snapshot, selectedIds);
        const hovered = snapshot.edges.find((edge) => edge.edgeId === hoveredEdgeId);
        if (hovered) {
            related.add(hovered.from);
            related.add(hovered.to);
        }
        return related;
    }, [hoveredEdgeId, selectedIds, snapshot]);
    const connectionVerdicts = useMemo(() => {
        const verdicts = new Map<string, ReturnType<typeof getConnectionVerdict>>();
        const interaction = interactionRef.current;
        if (!connectionDraft || !interaction || (interaction.kind !== "connect" && interaction.kind !== "reconnect")) return verdicts;
        const base = interaction.kind === "reconnect"
            ? { ...snapshot, edges: snapshot.edges.filter((edge) => edge.edgeId !== interaction.edgeId) }
            : snapshot;
        const candidatePort: CanvasPort = interaction.kind === "connect"
            ? (interaction.fromPort === "right" ? "left" : "right")
            : (interaction.movingEnd === "from" ? "right" : "left");
        for (const node of snapshot.nodes) {
            if (!canvasPortsForNode(node).includes(candidatePort)) continue;
            const from = interaction.kind === "connect"
                ? (interaction.fromPort === "right" ? interaction.fromNodeId : node.nodeId)
                : (interaction.movingEnd === "to" ? interaction.fixedNodeId : node.nodeId);
            const to = interaction.kind === "connect"
                ? (interaction.fromPort === "right" ? node.nodeId : interaction.fromNodeId)
                : (interaction.movingEnd === "to" ? node.nodeId : interaction.fixedNodeId);
            verdicts.set(node.nodeId, getConnectionVerdict(base, actionDefinitions, from, to));
        }
        return verdicts;
    }, [actionDefinitions, connectionDraft, snapshot]);
    const visibleNodeIds = useMemo(() => {
        if (!boardSize.width || !boardSize.height) return new Set(snapshot.nodes.map((node) => node.nodeId));
        const padding = 220 / snapshot.viewport.scale;
        const left = -snapshot.viewport.x / snapshot.viewport.scale - padding;
        const top = -snapshot.viewport.y / snapshot.viewport.scale - padding;
        const right = left + boardSize.width / snapshot.viewport.scale + padding * 2;
        const bottom = top + boardSize.height / snapshot.viewport.scale + padding * 2;
        return new Set(snapshot.nodes.filter((node) => node.x + node.width >= left && node.x <= right && node.y + node.height >= top && node.y <= bottom).map((node) => node.nodeId));
    }, [boardSize.height, boardSize.width, snapshot.nodes, snapshot.viewport]);
    const actionRunSummary = useMemo(() => {
        const actions = snapshot.nodes.filter((node) => node.kind === "action");
        const completed = actions.filter((node) => graphRuntime.nodeStates[node.nodeId]?.state === "succeeded").length;
        return { completed, total: actions.length };
    }, [graphRuntime.nodeStates, snapshot.nodes]);

    useEffect(() => {
        const ids = new Set(snapshot.nodes.map((node) => node.nodeId));
        setSelectedIds((current) => current.filter((nodeId) => ids.has(nodeId)));
    }, [snapshot.nodes]);

    const actionLabel = useCallback((action: CreativeCanvasAction) => (
        isTranslationKey(action.labelKey) ? t(action.labelKey) : action.actionId
    ), [t]);
    const connectionIssueLabel = useCallback((issue: ConnectionIssue) => (
        t(`web.workbench.canvas.graph.connection.${issue}` as Parameters<typeof t>[0])
    ), [t]);
    const preflightIssueLabel = useCallback((issue: CanvasPreflightIssue) => {
        const node = snapshot.nodes.find((candidate) => candidate.nodeId === issue.nodeId);
        return t(`web.workbench.canvas.graph.preflight.${issue.code}` as Parameters<typeof t>[0], {
            node: node?.title || issue.nodeId,
            detail: issue.detail || "-",
        });
    }, [snapshot.nodes, t]);

    const buildPsdComposition = useCallback((nodeIds: string[], parameters: Record<string, unknown> = {}): CanvasPsdComposition => {
        const storedCanvas = recordOf(parameters.canvas);
        const canvas = {
            width: Math.max(1, Math.min(32768, Number(storedCanvas.width) || 1920)),
            height: Math.max(1, Math.min(32768, Number(storedCanvas.height) || 1080)),
            background: String(storedCanvas.background || "transparent"),
        };
        const sourceNodes = Array.from(new Map(nodeIds.flatMap((nodeId) => {
            const candidate = snapshot.nodes.find((item) => item.nodeId === nodeId);
            const output = canvasOutputNode(snapshot, candidate);
            return output ? [[output.nodeId, output] as const] : [];
        })).values());
        const minimumX = sourceNodes.length ? Math.min(...sourceNodes.map((item) => item.x)) : 0;
        const minimumY = sourceNodes.length ? Math.min(...sourceNodes.map((item) => item.y)) : 0;
        const maximumX = sourceNodes.length ? Math.max(...sourceNodes.map((item) => item.x + item.width)) : 1;
        const maximumY = sourceNodes.length ? Math.max(...sourceNodes.map((item) => item.y + item.height)) : 1;
        const layoutScale = Math.min(
            canvas.width * 0.82 / Math.max(1, maximumX - minimumX),
            canvas.height * 0.82 / Math.max(1, maximumY - minimumY),
        );
        const storedLayers = (Array.isArray(parameters.layers) ? parameters.layers : []).map(recordOf);
        const storedByNode = new Map(storedLayers.flatMap((layer) => {
            const sourceNodeId = String(layer.sourceNodeId || "");
            return sourceNodeId ? [[sourceNodeId, layer] as const] : [];
        }));
        return {
            canvas,
            layers: sourceNodes.map((node, index) => {
                const resource = displayResourceForNode(node);
                const stored = storedByNode.get(node.nodeId);
                return {
                    sourceNodeId: node.nodeId,
                    name: String(stored?.name || resource?.name || node.title || `Layer ${index + 1}`),
                    x: Number(stored?.x ?? Math.round((node.x - minimumX) * layoutScale + canvas.width * 0.09)),
                    y: Number(stored?.y ?? Math.round((node.y - minimumY) * layoutScale + canvas.height * 0.09)),
                    scalePercent: Number(stored?.scalePercent ?? 100),
                    opacityPercent: Number(stored?.opacityPercent ?? 100),
                    visible: stored?.visible !== false,
                    order: Number(stored?.order ?? index),
                    ...(stored?.width ? { width: Number(stored.width) } : {}),
                    ...(stored?.height ? { height: Number(stored.height) } : {}),
                };
            }),
        };
    }, [displayResourceForNode, snapshot]);

    const openActionEditor = useCallback((node: CanvasNode) => {
        if (sessionRunning || node.kind !== "action" || !node.actionDefinitionId) return;
        const action = CREATIVE_CANVAS_ACTIONS.find((item) => item.actionId === node.actionDefinitionId);
        if (!action) return;
        const parameters = node.parameters || {};
        const incoming = snapshot.edges
            .filter((edge) => edge.role === "data" && edge.to === node.nodeId)
            .sort((left, right) => left.order - right.order);
        setPreflightOpen(false);
        setTemplateOpen(false);
        setTrayOpen(false);
        setContextMenu(null);
        setComposer({
            x: snapshot.viewport.x + (node.x + node.width / 2) * snapshot.viewport.scale,
            y: snapshot.viewport.y + node.y * snapshot.viewport.scale,
            action,
            actionNodeId: node.nodeId,
            operationId: createId("canvas-config"),
            nodeIds: incoming.map((edge) => edge.from),
            text: node.prompt || "",
            ...(["frame_pick", "time_range"].includes(String(action.parameterEditor)) ? {
                timeRange: {
                    count: 0,
                    startIndex: Number(parameters.frameIndex ?? parameters.startFrameIndex ?? parameters.startSampleIndex ?? 0),
                    endIndexExclusive: Number(parameters.endFrameIndexExclusive ?? parameters.endSampleIndexExclusive ?? 0),
                    durationSeconds: "0",
                    timeBaseNumerator: 1,
                    timeBaseDenominator: 1,
                    displayPrecision: 6,
                    loading: true,
                },
            } : {}),
            ...(action.parameterEditor === "psd_composition" ? {
                psdComposition: buildPsdComposition(incoming.map((edge) => edge.from), parameters),
            } : {}),
            ...(action.parameterEditor === "psd_layers" ? {
                psdEdits: (Array.isArray(parameters.edits) ? parameters.edits : []).map((item) => recordOf(item) as CanvasPsdLayerEdit),
            } : {}),
        });
    }, [buildPsdComposition, sessionRunning, snapshot.edges, snapshot.viewport]);

    const menuActions = useMemo(() => {
        if (!contextMenu) return [];
        const nodes = snapshot.nodes.filter((node) => contextMenu.nodeIds.includes(node.nodeId));
        const actions = getCreativeCanvasActions({
            target: contextMenu.target,
            selection: nodes.map((node, index) => ({
                id: node.nodeId,
                mediaType: mediaTypeForNode(node),
                order: index,
            })),
            sessionRunning,
            pluginAvailable: mediaKitStatus === "ready",
            pluginGranted: false,
            allowPluginGrantRequest: true,
        });
        const graphActions = actions.filter((action) => (
            action.executionClass === "local_read"
            || action.executionClass === "local_mutation"
            || action.actionId === "message.comment_connection"
            || actionDefinitions.some((definition) => definition.actionId === action.actionId)
        ));
        if (!pendingConnectionDrop || contextMenu.nodeIds[0] !== pendingConnectionDrop.fromNodeId) return graphActions;
        const quickIds = new Set(["local.upload_sources", "local.open_artifact_tray"]);
        const quickActions = CREATIVE_CANVAS_ACTIONS.filter((action) => quickIds.has(action.actionId));
        const anchor = snapshot.nodes.find((node) => node.nodeId === pendingConnectionDrop.fromNodeId);
        if (!anchor) return quickActions;
        if (pendingConnectionDrop.direction === "input") {
            const targetDefinition = actionDefinitions.find((definition) => definition.actionId === anchor.actionDefinitionId);
            const accepted = new Set((targetDefinition?.inputs || []).flatMap((port) => port.mediaTypes));
            const upstreamActions = CREATIVE_CANVAS_ACTIONS.filter((action) => {
                const definition = actionDefinitions.find((candidate) => candidate.actionId === action.actionId);
                return Boolean(definition?.output.mediaTypes.some((mediaType) => accepted.has(mediaType)));
            });
            return [...quickActions, ...upstreamActions];
        }
        const output = canvasOutputNode(snapshot, anchor);
        if (!output) return quickActions;
        const outputType = output.mediaType || "unknown";
        const downstreamActions = CREATIVE_CANVAS_ACTIONS.filter((action) => {
            const definition = actionDefinitions.find((candidate) => candidate.actionId === action.actionId);
            return Boolean(definition?.inputs.some((port) => port.mediaTypes.includes(outputType)));
        });
        return [...quickActions, ...downstreamActions];
    }, [actionDefinitions, contextMenu, mediaKitStatus, mediaTypeForNode, pendingConnectionDrop, sessionRunning, snapshot.nodes]);

    function createActionCard(action: CreativeCanvasAction, menu: ContextMenuState, extraNodeIds: string[] = []) {
        const definition = actionDefinitions.find((item) => item.actionId === action.actionId);
        if (!definition || sessionRunningRef.current) return;
        const selectedNodeIds = Array.from(new Set([...menu.nodeIds, ...extraNodeIds]));
        const actionNodeId = createId("canvas-action");
        const resultNodeId = createId("canvas-result");
        const title = actionLabel(action);
        commitSnapshot((current) => {
            if (current.nodes.length > MAX_NODES - 2 || current.edges.length >= MAX_EDGES) return current;
            const selected = Array.from(new Map(
                current.nodes
                    .filter((node) => selectedNodeIds.includes(node.nodeId))
                    .flatMap((node) => {
                        const output = canvasOutputNode(current, node);
                        return output ? [[output.nodeId, output] as const] : [];
                    }),
            ).values());
            const pendingInputTargetId = pendingConnectionDrop?.direction === "input"
                ? pendingConnectionDrop.fromNodeId
                : "";
            const selectedInputs = pendingInputTargetId ? [] : selected;
            const anchor = current.nodes.find((node) => node.nodeId === pendingConnectionDrop?.fromNodeId) || selected[0];
            const drop = pendingConnectionDrop?.point;
            const actionX = drop?.x ?? (anchor ? anchor.x + anchor.width + 76 : (menu.x - current.viewport.x) / current.viewport.scale);
            const actionY = drop?.y ?? (anchor ? anchor.y : (menu.y - current.viewport.y) / current.viewport.scale);
            const outputMediaType = definition.output.mediaTypes[0] || "unknown";
            const actionNode: CanvasNode = {
                nodeId: actionNodeId,
                kind: "action",
                origin: "placeholder",
                x: actionX,
                y: actionY,
                width: 300,
                height: definition.parameterEditor ? 236 : 214,
                title,
                mediaType: outputMediaType,
                actionDefinitionId: definition.actionId,
                prompt: "",
                parameters: {},
                configurationRevision: 1,
            };
            const resultNode: CanvasNode = {
                nodeId: resultNodeId,
                kind: "result",
                origin: "placeholder",
                x: actionX + 380,
                y: actionY,
                width: NODE_WIDTH,
                height: NODE_HEIGHT,
                title: `${title} · ${t("web.workbench.canvas.graph.result")}`,
                mediaType: outputMediaType,
                producerActionNodeId: actionNodeId,
                outputSlot: definition.output.slot,
            };
            const portCounts = new Map<string, number>();
            const inputEdges = selectedInputs.flatMap((node) => {
                if (!["resource", "result", "input"].includes(node.kind)) return [];
                const mediaType = mediaTypeForNode(node);
                const port = definition.inputs.find((candidate) => (
                    candidate.mediaTypes.includes(mediaType)
                    && (portCounts.get(candidate.portId) || 0) < candidate.max
                ));
                if (!port) return [];
                const order = portCounts.get(port.portId) || 0;
                portCounts.set(port.portId, order + 1);
                return [{
                    edgeId: createId("canvas-edge"),
                    from: node.nodeId,
                    to: actionNodeId,
                    fromPort: "right" as const,
                    toPort: "left" as const,
                    fromPortId: "output",
                    toPortId: port.portId,
                    dataType: mediaType,
                    role: "data" as const,
                    order,
                    note: "",
                } satisfies CanvasEdge];
            });
            const outputEdge: CanvasEdge = {
                edgeId: createId("canvas-edge"),
                from: actionNodeId,
                to: resultNodeId,
                fromPort: "right",
                toPort: "left",
                fromPortId: definition.output.portId || "output",
                toPortId: "input",
                dataType: outputMediaType,
                role: "data",
                order: 0,
                note: "",
            };
            const withCards = {
                ...current,
                nodes: [...current.nodes, actionNode, resultNode],
                edges: [...current.edges, ...inputEdges, outputEdge],
            };
            return pendingInputTargetId
                ? appendCanvasEdge(withCards, actionDefinitions, { from: resultNodeId, to: pendingInputTargetId })
                : withCards;
        });
        setSelectedIds([actionNodeId]);
        setComposer({
            x: menu.x,
            y: menu.y,
            action,
            actionNodeId,
            operationId: createId("canvas-config"),
            nodeIds: pendingConnectionDrop?.direction === "input" ? [] : selectedNodeIds,
            text: "",
            ...(["frame_pick", "time_range"].includes(String(definition.parameterEditor)) ? {
                timeRange: {
                    count: 0,
                    startIndex: 0,
                    endIndexExclusive: 0,
                    durationSeconds: "0",
                    timeBaseNumerator: 1,
                    timeBaseDenominator: 1,
                    displayPrecision: 6,
                    loading: true,
                },
            } : {}),
            ...(definition.parameterEditor === "psd_composition" ? {
                psdComposition: buildPsdComposition(selectedNodeIds),
            } : {}),
            ...(definition.parameterEditor === "psd_layers" ? { psdEdits: [] } : {}),
        });
        setPendingConnectionDrop(null);
        setContextMenu(null);
    }

    const executeLocalAction = useCallback((action: CreativeCanvasAction, menu: ContextMenuState) => {
        const ids = menu.nodeIds;
        if (sessionRunning && !action.availableWhileRunning) return;
        switch (action.actionId) {
            case "local.view":
                if (ids[0]) { setInspectResourceOverride(null); setInspectNodeId(ids[0]); }
                break;
            case "local.download":
                ids.map((id) => snapshot.nodes.find((node) => node.nodeId === id)).filter(Boolean).forEach((node) => {
                    const resource = displayResourceForNode(node!);
                    if (!resource?.url) return;
                    const anchor = window.document.createElement("a");
                    anchor.href = resource.url;
                    anchor.download = resource.name;
                    anchor.rel = "noreferrer";
                    anchor.click();
                });
                break;
            case "local.open_in_file_manager": {
                const node = snapshot.nodes.find((candidate) => candidate.nodeId === ids[0]);
                const resource = node ? displayResourceForNode(node) : null;
                if (!resource?.workspaceRelativePath || !workspacePath || !window.v8osShell?.revealWorkspaceFile) {
                    setError(t("web.workbench.canvas.fileManagerUnavailable"));
                    break;
                }
                void window.v8osShell.revealWorkspaceFile(resource.workspaceRelativePath, workspacePath)
                    .then((result) => { if (!result?.ok) setError(t("web.workbench.canvas.fileManagerFailed")); })
                    .catch(() => setError(t("web.workbench.canvas.fileManagerFailed")));
                break;
            }
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
                if (menu.edgeId) commitSnapshot((current) => ({ ...current, edges: current.edges.filter((edge) => edge.edgeId !== menu.edgeId) }));
                break;
            case "local.clear_canvas":
                if (!sessionRunning) {
                    commitSnapshot((current) => ({ ...current, nodes: [], edges: [] }));
                    setSelectedIds([]);
                }
                break;
            default:
                break;
        }
        setContextMenu(null);
    }, [connectSelection, displayResourceForNode, fitView, removeNodes, sessionRunning, snapshot.nodes, t, workspacePath]);

    const handleAction = (action: CreativeCanvasAction, menu: ContextMenuState) => {
        if (action.executionClass === "local_read" || action.executionClass === "local_mutation") {
            executeLocalAction(action, menu);
            return;
        }
        if (action.actionId === "message.comment_connection" && menu.edgeId) {
            const edge = snapshot.edges.find((item) => item.edgeId === menu.edgeId);
            setEdgeNote({ edgeId: menu.edgeId, x: menu.x, y: menu.y, text: edge?.note || "" });
            setContextMenu(null);
            return;
        }
        createActionCard(action, menu);
    };

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
                if (sessionIdRef.current !== sessionId) throw new Error(t("web.workbench.canvas.graph.sessionChanged"));
                const resource = normalizeResource(payload, "source", sessionId, index);
                if (resource) uploaded.push(resource);
            }
            if (sessionIdRef.current !== sessionId) throw new Error(t("web.workbench.canvas.graph.sessionChanged"));
            setResources((current) => {
                const map = new Map(current.map((item) => [`${item.origin}:${item.id}`, item]));
                uploaded.forEach((item) => map.set(`${item.origin}:${item.id}`, item));
                return [...map.values()];
            });
            if (!sessionRunningRef.current) {
                const connection = pendingConnectionDrop ? {
                    fromNodeId: pendingConnectionDrop.fromNodeId,
                    fromPort: pendingConnectionDrop.fromPort,
                    direction: pendingConnectionDrop.direction,
                } : undefined;
                const origin = point || pendingConnectionDrop?.point;
                uploaded.forEach((resource, index) => placeResource(resource, origin ? {
                    x: origin.x + (index % 2) * GRID_COLUMN_STEP,
                    y: origin.y + Math.floor(index / 2) * GRID_ROW_STEP,
                } : undefined, connection));
                setPendingConnectionDrop(null);
            }
            void loadCatalog();
            setTrayOpen(false);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setUploading(false);
        }
    }, [loadCatalog, pendingConnectionDrop, placeResource, sessionId, sessionRunning, t, uploading]);

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
        if (sessionIdRef.current !== sessionId) throw new Error(t("web.workbench.canvas.graph.sessionChanged"));
        const normalized = normalizeResource(payload, "source", sessionId, 0, true);
        if (!normalized) throw new Error(t("web.workbench.canvas.graph.maskRegistrationFailed"));
        const maskResource = { ...normalized, mediaType: "mask" } satisfies CanvasResource;
        setResources((current) => {
            const byKey = new Map(current.map((item) => [`${item.origin}:${item.id}`, item]));
            byKey.set(`${maskResource.origin}:${maskResource.id}`, maskResource);
            return [...byKey.values()];
        });
        commitSnapshot((current) => ({
            ...current,
            nodes: current.nodes.map((item) => item.nodeId === node.nodeId && item.mask
                ? { ...item, mask: { ...item.mask, frozenSourceIds: [...(item.mask.frozenSourceIds || []), maskResource.id].slice(-20) } }
                : item),
        }));
        return maskResource;
    }, [sessionId, t]);

    const submitComposer = useCallback(async () => {
        if (!composer?.actionNodeId || submitting || sessionRunning) return;
        if (composer.action.requiresPrompt && !composer.text.trim()) return;
        if (composer.action.parameterEditor === "time_range" && !isValidCanvasTimeRange(composer.timeRange)) return;
        if (composer.action.parameterEditor === "frame_pick" && !isValidCanvasFramePick(composer.timeRange)) return;
        if (composer.action.parameterEditor === "psd_composition" && !composer.psdComposition?.layers.length) return;
        if (composer.action.parameterEditor === "psd_layers" && !composer.psdEdits?.length) return;
        setSubmitting(true);
        setError("");
        try {
            commitSnapshot((current) => ({
                ...current,
                nodes: current.nodes.map((node) => node.nodeId === composer.actionNodeId ? {
                    ...node,
                    prompt: composer.text.trim(),
                    parameters: composer.action.parameterEditor === "psd_composition" && composer.psdComposition
                        ? composer.psdComposition
                        : composer.action.parameterEditor === "psd_layers"
                            ? { edits: composer.psdEdits || [] }
                            : composer.action.parameterEditor === "frame_pick" && composer.timeRange ? {
                        probeFingerprint: composer.timeRange.probeFingerprint,
                        frameIndex: composer.timeRange.startIndex,
                    } : composer.action.parameterEditor === "time_range" && composer.timeRange ? (
                        composer.timeRange.unit === "frame" ? {
                            probeFingerprint: composer.timeRange.probeFingerprint,
                            startFrameIndex: composer.timeRange.startIndex,
                            endFrameIndexExclusive: composer.timeRange.endIndexExclusive,
                        } : {
                            probeFingerprint: composer.timeRange.probeFingerprint,
                            startSampleIndex: composer.timeRange.startIndex,
                            endSampleIndexExclusive: composer.timeRange.endIndexExclusive,
                        }
                    ) : node.parameters || {},
                    configurationRevision: Number(node.configurationRevision || 1) + 1,
                } : node),
            }));
            setComposer(null);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setSubmitting(false);
        }
    }, [composer, sessionRunning, submitting]);

    const runGraph = useCallback(async (targetNodeIds: string[]) => {
        if (sessionRunning || submitting || graphSubmittingRef.current || !onSubmitTask) return;
        const targetIds = Array.from(new Set(targetNodeIds.filter(Boolean)));
        graphSubmittingRef.current = true;
        setSubmitting(true);
        setError("");
        try {
            const persisted = await persistGraph(snapshot);
            if (!persisted || sessionIdRef.current !== sessionId) throw new Error(t("web.workbench.canvas.graph.sessionChanged"));
            const validationResponse = await fetch(`/api/workbench/sessions/${encodeURIComponent(sessionId)}/canvas/graph/validate`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    graphId: snapshot.graphId,
                    graphRevision: graphRevisionRef.current,
                    targetNodeIds: targetIds,
                }),
                cache: "no-store",
            });
            const validationPayload = await validationResponse.json().catch(() => ({}));
            if (!validationResponse.ok) throw new Error(String(validationPayload?.detail || validationPayload?.error || `HTTP ${validationResponse.status}`));
            if (sessionIdRef.current !== sessionId) throw new Error(t("web.workbench.canvas.graph.sessionChanged"));
            const plan = recordOf(validationPayload?.plan);
            const refs = (Array.isArray(plan.resourceRefs) ? plan.resourceRefs : []).flatMap((raw: unknown) => {
                const reference = recordOf(raw);
                const origin = String(reference.origin || "") as ResourceOrigin;
                const id = String(reference.id || "");
                const resource = resources.find((item) => item.origin === origin && item.id === id);
                return resource ? [resource] : [];
            });
            if (refs.length !== (Array.isArray(plan.resourceRefs) ? plan.resourceRefs.length : 0)) {
                throw new Error(t("web.workbench.canvas.graph.resourceUnavailable"));
            }
            const runToHere = targetIds.length > 0;
            const label = t(runToHere ? "web.workbench.canvas.graph.runToHere" : "web.workbench.canvas.graph.runAll");
            const accepted = await onSubmitTask({
                sessionId,
                text: label,
                refs: refs.map((resource) => ({
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
                    operationId: createId("canvas-operation"),
                    actionId: runToHere ? "canvas.graph.run_to_here" : "canvas.graph.run_all",
                    label,
                    nodeIds: targetIds,
                    outputKind: "artifacts",
                    outputSlot: "canvas_graph",
                    binding: { kind: "creative_media", capability: "canvas.graph.execute" },
                    parameters: {
                        graphId: snapshot.graphId,
                        graphRevision: graphRevisionRef.current,
                        targetNodeIds: targetIds,
                    },
                },
            });
            if (accepted === false) throw new Error(t("web.workbench.canvas.graph.submitRejected"));
            setGraphRuntime((current) => ({ ...current, status: "queued" }));
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            graphSubmittingRef.current = false;
            setSubmitting(false);
        }
    }, [onSubmitTask, persistGraph, resources, sessionId, sessionRunning, snapshot, submitting, t]);

    const requestGraphRun = useCallback((targetNodeIds: string[]) => {
        const targets = Array.from(new Set(targetNodeIds.filter(Boolean)));
        const actionIds = getExecutionActionIds(snapshot, targets);
        const issues = getCanvasPreflight(snapshot, actionDefinitions, graphRuntime).filter((issue) => actionIds.has(issue.nodeId));
        setPreflightTargets(targets);
        if (issues.length) {
            setPreflightOpen(true);
            return;
        }
        void runGraph(targets);
    }, [actionDefinitions, graphRuntime, runGraph, snapshot]);

    const refreshTemplates = useCallback(async () => {
        const response = await fetch(`/api/workbench/sessions/${encodeURIComponent(sessionId)}/canvas/templates`, { cache: "no-store" });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
        if (sessionIdRef.current !== sessionId) return;
        setTemplates((Array.isArray(payload?.templates) ? payload.templates : []) as CanvasWorkflowTemplate[]);
    }, [sessionId]);

    const saveWorkflowTemplate = useCallback(async () => {
        if (!templateTitle.trim() || savingTemplate || sessionRunning) return;
        setSavingTemplate(true);
        setError("");
        try {
            const persisted = await persistGraph(snapshot);
            if (!persisted || sessionIdRef.current !== sessionId) throw new Error(t("web.workbench.canvas.graph.sessionChanged"));
            const response = await fetch(`/api/workbench/sessions/${encodeURIComponent(sessionId)}/canvas/templates`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ title: templateTitle.trim() }),
                cache: "no-store",
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
            if (sessionIdRef.current !== sessionId) return;
            setTemplateTitle("");
            await refreshTemplates();
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setSavingTemplate(false);
        }
    }, [persistGraph, refreshTemplates, savingTemplate, sessionId, sessionRunning, snapshot, t, templateTitle]);

    const instantiateWorkflowTemplate = useCallback(async (templateId: string) => {
        if (sessionRunning) return;
        setSavingTemplate(true);
        setError("");
        try {
            const persisted = await persistGraph(snapshot);
            if (!persisted || sessionIdRef.current !== sessionId) throw new Error(t("web.workbench.canvas.graph.sessionChanged"));
            const response = await fetch(`/api/workbench/sessions/${encodeURIComponent(sessionId)}/canvas/templates/${encodeURIComponent(templateId)}/instantiate`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ expectedRevision: graphRevisionRef.current, mode: "append" }),
                cache: "no-store",
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
            if (sessionIdRef.current !== sessionId) return;
            const graph = normalizeSnapshot(payload.graph);
            graphRevisionRef.current = Number(payload.revision || 0);
            setGraphRevision(graphRevisionRef.current);
            setGraphRuntime({ ...EMPTY_GRAPH_RUNTIME, ...recordOf(payload.runtime) } as CanvasGraphRuntime);
            lastSavedGraphRef.current = JSON.stringify(graph);
            commitSnapshot(graph);
            setTemplateOpen(false);
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setSavingTemplate(false);
        }
    }, [persistGraph, sessionId, sessionRunning, snapshot, t]);

    const deleteWorkflowTemplate = useCallback(async (template: CanvasWorkflowTemplate) => {
        if (savingTemplate || sessionRunning) return;
        if (!window.confirm(t("web.workbench.canvas.graph.confirmDeleteTemplate", { title: template.title }))) return;
        setSavingTemplate(true);
        setError("");
        try {
            const response = await fetch(`/api/workbench/sessions/${encodeURIComponent(sessionId)}/canvas/templates/${encodeURIComponent(template.templateId)}`, {
                method: "DELETE",
                cache: "no-store",
            });
            const payload = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(String(payload?.detail || payload?.error || `HTTP ${response.status}`));
            if (sessionIdRef.current !== sessionId) return;
            await refreshTemplates();
        } catch (reason) {
            setError(reason instanceof Error ? reason.message : String(reason));
        } finally {
            setSavingTemplate(false);
        }
    }, [refreshTemplates, savingTemplate, sessionId, sessionRunning, t]);

    const selectedImageNode = selectedNodes.length === 1 && mediaTypeOf(displayResourceForNode(selectedNodes[0]) || { name: "", mimeType: "", mediaType: "unknown" }) === "image"
        ? selectedNodes[0]
        : null;
    const composerNode = composer?.nodeIds.length === 1
        ? snapshot.nodes.find((node) => node.nodeId === composer.nodeIds[0]) || null
        : null;
    const composerResource = composerNode ? displayResourceForNode(composerNode) : null;
    const composerUsesFramePick = composer?.action.parameterEditor === "frame_pick";
    const composerUsesTimeRange = composer?.action.parameterEditor === "time_range";
    const composerUsesTimeline = composerUsesFramePick || composerUsesTimeRange;
    const composerUsesPsdComposition = composer?.action.parameterEditor === "psd_composition";
    const composerUsesPsdLayers = composer?.action.parameterEditor === "psd_layers";
    const composerUsesPsd = composerUsesPsdComposition || composerUsesPsdLayers;
    const composerPreferredWidth = composerUsesTimeline || composerUsesPsd ? 620 : 340;
    const composerBoardWidth = boardSize.width || boardRef.current?.clientWidth || 380;
    const composerPanelWidth = Math.min(composerPreferredWidth, Math.max(280, composerBoardWidth - 24));
    const composerHalfWidth = composerPanelWidth / 2;
    const composerLeft = composer
        ? Math.min(
            Math.max(12 + composerHalfWidth, composer.x),
            Math.max(12 + composerHalfWidth, composerBoardWidth - 12 - composerHalfWidth),
        )
        : composerBoardWidth / 2;
    const composerPsdSources = useMemo(() => (composer?.psdComposition?.layers || []).flatMap((layer) => {
        const node = snapshot.nodes.find((candidate) => candidate.nodeId === layer.sourceNodeId);
        const resource = node ? displayResourceForNode(node) : null;
        return node && resource ? [{ nodeId: node.nodeId, resource }] : [];
    }), [composer?.psdComposition?.layers, displayResourceForNode, snapshot.nodes]);
    const composerSubmitDisabled = Boolean(
        submitting
        || (composer?.action.requiresPrompt && !composer.text.trim())
        || (composerUsesTimeRange && !isValidCanvasTimeRange(composer?.timeRange))
        || (composerUsesFramePick && !isValidCanvasFramePick(composer?.timeRange))
        || (composerUsesPsdComposition && !composer?.psdComposition?.layers.length)
        || (composerUsesPsdLayers && !composer?.psdEdits?.length),
    );
    const inspectNode = inspectNodeId ? snapshot.nodes.find((node) => node.nodeId === inspectNodeId) || null : null;
    const inspectResource = inspectResourceOverride || (inspectNode ? displayResourceForNode(inspectNode) : null);
    const maskNode = maskNodeId ? snapshot.nodes.find((node) => node.nodeId === maskNodeId) || null : null;
    const maskResource = maskNode ? displayResourceForNode(maskNode) : null;

    const updateNodeMask = useCallback((nodeId: string, mask: CreativeCanvasMaskState) => {
        if (sessionRunning) return;
        setSnapshot((current) => ({
            ...current,
            nodes: current.nodes.map((node) => node.nodeId === nodeId ? { ...node, mask } : node),
        }));
    }, [sessionRunning]);

    const openSelectionInteraction = useCallback(() => {
        if (!selectedBounds || !selectedIds.length) return;
        if (selectedIds.length === 1) {
            setContextMenu({
                x: selectedBounds.left,
                y: Math.max(70, selectedBounds.top - 8),
                target: "node",
                nodeIds: selectedIds,
            });
            return;
        }
        const ordered = snapshot.nodes
            .filter((node) => selectedIds.includes(node.nodeId))
            .sort((left, right) => left.x - right.x || left.y - right.y);
        const existing = snapshot.edges.find((edge) => edge.from === ordered[0]?.nodeId && edge.to === ordered[1]?.nodeId);
        const edgeId = existing?.edgeId || createId("canvas-edge");
        if (!existing && ordered.length >= 2) {
            commitSnapshot((current) => ({
                ...current,
                edges: [...current.edges, {
                    edgeId,
                    from: ordered[0].nodeId,
                    to: ordered[1].nodeId,
                    fromPort: "right",
                    toPort: "left",
                    fromPortId: "relation",
                    toPortId: "relation",
                    dataType: mediaTypeForNode(ordered[0]),
                    role: "relation",
                    order: 0,
                    note: "",
                }],
            }));
        }
        setEdgeNote({
            edgeId,
            x: selectedBounds.left,
            y: Math.max(70, selectedBounds.top - 8),
            text: existing?.note || "",
        });
    }, [mediaTypeForNode, selectedBounds, selectedIds, snapshot.edges, snapshot.nodes]);

    const connectionDraftSource = connectionDraft
        ? snapshot.nodes.find((node) => node.nodeId === connectionDraft.fromNodeId) || null
        : null;
    const workspaceResources = resources.filter((item) => item.origin === "workspace_asset");
    const visibleWorkspaceResources = activeFolderId
        ? workspaceResources.filter((item) => item.folderId === activeFolderId)
        : workspaceResources;
    const folderRows = (() => {
        const rows: Array<WorkspaceMediaFolder & { depth: number }> = [];
        const visit = (parentFolderId: string | undefined, depth: number) => {
            workspaceFolders
                .filter((folder) => (folder.parentFolderId || undefined) === parentFolderId)
                .sort((left, right) => left.title.localeCompare(right.title))
                .forEach((folder) => {
                    rows.push({ ...folder, depth });
                    visit(folder.folderId, depth + 1);
                });
        };
        visit(undefined, 0);
        return rows;
    })();

    return (
        <div
            ref={boardRef}
            data-testid="creative-artifact-canvas"
            onPointerDown={startBoardInteraction}
            onPointerMove={handlePointerMove}
            onPointerUp={finishPointerInteraction}
            onPointerCancel={finishPointerInteraction}
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
                    void adoptAndPlaceResource(resource, worldPoint(event.clientX, event.clientY)).catch((reason) => {
                        setError(reason instanceof Error ? reason.message : String(reason));
                    });
                    setTrayOpen(false);
                    return;
                }
                const files = Array.from(event.dataTransfer.files || []);
                if (files.length) void uploadFiles(files, worldPoint(event.clientX, event.clientY));
            }}
            className={cn(
                "relative h-full min-h-0 w-full touch-none overflow-hidden bg-[#f5f6f8] text-foreground outline-none dark:bg-[#111315]",
                tool === "pan" || spacePanning ? "cursor-grab active:cursor-grabbing" : "cursor-default",
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
                        const fixedResultEdge = from.kind === "action" && to.kind === "result" && to.producerActionNodeId === from.nodeId;
                        const highlighted = hoveredEdgeId === edge.edgeId || selectedIds.includes(edge.from) || selectedIds.includes(edge.to);
                        const semanticStroke = edge.role === "relation"
                            ? "rgb(148 163 184)"
                            : edge.dataType === "image" || edge.dataType === "psd"
                                ? "rgb(244 63 94)"
                                : edge.dataType === "video"
                                    ? "rgb(6 182 212)"
                                    : edge.dataType === "audio"
                                        ? "rgb(245 158 11)"
                                        : edge.dataType === "model_3d"
                                            ? "rgb(16 185 129)"
                                            : "rgb(124 58 237)";
                        return (
                            <g key={edge.edgeId} data-canvas-edge={edge.edgeId}>
                                <path d={path} fill="none" stroke="transparent" strokeWidth="18" className={cn("pointer-events-auto", fixedResultEdge ? "cursor-default" : "cursor-pointer")} onPointerDown={(event) => handleEdgePointerDown(event, edge, from, to)} onPointerEnter={() => { if (!fixedResultEdge) setHoveredEdgeId(edge.edgeId); }} onPointerLeave={(event) => {
                                    const related = event.relatedTarget;
                                    if (related instanceof HTMLElement && related.dataset.canvasEdgeComment === edge.edgeId) return;
                                    setHoveredEdgeId((current) => current === edge.edgeId ? null : current);
                                }} onContextMenu={(event) => {
                                    if (fixedResultEdge) return;
                                    event.preventDefault();
                                    event.stopPropagation();
                                    const point = boardPoint(event.clientX, event.clientY);
                                    setContextMenu({ x: point.x, y: point.y, target: "edge", nodeIds: [edge.from, edge.to], edgeId: edge.edgeId });
                                }} />
                                <path d={path} fill="none" stroke={highlighted ? semanticStroke : "rgb(148 163 184)"} strokeWidth={highlighted ? "2.75" : "2"} strokeDasharray={edge.role === "relation" ? "6 5" : undefined} markerEnd={`url(#canvas-arrow-${sessionId.replace(/[^a-z0-9]/gi, "")})`} opacity={reconnectingEdgeId === edge.edgeId ? 0.2 : selectedIds.length && !highlighted ? 0.28 : 1} className="transition-[stroke,opacity] motion-reduce:transition-none" />
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
                            stroke={connectionIssue ? "rgb(239 68 68)" : "rgb(124 58 237)"}
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
                                setEdgeNote({
                                    edgeId: edge.edgeId,
                                    x: snapshot.viewport.x + ((start.x + end.x) / 2) * snapshot.viewport.scale,
                                    y: snapshot.viewport.y + ((start.y + end.y) / 2) * snapshot.viewport.scale,
                                    text: edge.note || "",
                                });
                            }}
                            disabled={sessionRunning}
                            style={{ left: (start.x + end.x) / 2, top: (start.y + end.y) / 2 }}
                            className="pointer-events-auto absolute z-20 grid h-8 w-8 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border border-white/80 bg-background text-muted-foreground shadow-lg hover:text-primary disabled:opacity-35 dark:border-white/10"
                            aria-label={t("web.workbench.canvas.graph.relationshipNote")}
                        >
                            <MessageSquare className="h-3.5 w-3.5" />
                        </button>
                    );
                })}

                {snapshot.nodes.map((node) => {
                    const resource = displayResourceForNode(node);
                    const selected = selectedIds.includes(node.nodeId);
                    const inputPlaceholder = node.kind === "input";
                    const actionState = String(graphRuntime.nodeStates[node.nodeId]?.state || "idle");
                    const actionDefinition = actionDefinitions.find((item) => item.actionId === node.actionDefinitionId);
                    const actionConfigured = Boolean(actionDefinition && isCanvasActionConfigured(node, actionDefinition));
                    const actionRuntime = graphRuntime.nodeStates[node.nodeId] || {};
                    const stale = actionState === "succeeded"
                        && Number(actionRuntime.configurationRevision || 0) > 0
                        && Number(actionRuntime.configurationRevision) < Number(node.configurationRevision || 1);
                    const versions = graphRuntime.outputs[node.nodeId] || [];
                    return (
                        <div
                            key={node.nodeId}
                            data-canvas-node={node.nodeId}
                            onPointerDown={(event) => handleNodePointerDown(event, node)}
                            onDoubleClick={(event) => {
                                event.stopPropagation();
                                if (node.kind === "action") openActionEditor(node);
                                else if (resource) { setInspectResourceOverride(null); setInspectNodeId(node.nodeId); }
                            }}
                            onContextMenu={(event) => {
                                event.preventDefault();
                                event.stopPropagation();
                                const point = boardPoint(event.clientX, event.clientY);
                                const nodeIds = selectedIds.includes(node.nodeId) ? selectedIds : [node.nodeId];
                                setSelectedIds(nodeIds);
                                setContextMenu({ x: point.x, y: point.y, target: nodeIds.length > 1 ? "selection" : "node", nodeIds });
                            }}
                            onDragOver={(event) => { if (inputPlaceholder && !sessionRunning) event.preventDefault(); }}
                            onDrop={(event) => {
                                if (!inputPlaceholder || sessionRunning) return;
                                event.preventDefault();
                                event.stopPropagation();
                                const key = event.dataTransfer.getData(CANVAS_DRAG_TYPE);
                                const dropped = resources.find((item) => `${item.origin}:${item.id}` === key);
                                if (!dropped) return;
                                void adoptWorkspaceResource(dropped).then((adopted) => {
                                    commitSnapshot((current) => ({
                                        ...current,
                                        nodes: current.nodes.map((item) => item.nodeId === node.nodeId
                                            ? {
                                                ...item,
                                                kind: "resource",
                                                origin: adopted.origin,
                                                resourceId: adopted.id,
                                                title: adopted.name,
                                                mediaType: mediaTypeOf(adopted),
                                                acceptedMediaTypes: undefined,
                                            }
                                            : item),
                                    }));
                                }).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
                                setTrayOpen(false);
                            }}
                            style={{ width: node.width, height: node.height, transform: `translate(${node.x}px, ${node.y}px)` }}
                            className={cn(
                                "pointer-events-auto absolute left-0 top-0 touch-none select-none overflow-visible transition-opacity motion-reduce:transition-none",
                                emphasizedNodeIds.size && !emphasizedNodeIds.has(node.nodeId) && "opacity-40",
                            )}
                        >
                            <div className={cn(
                                "flex h-full w-full flex-col overflow-hidden rounded-2xl border bg-background/96 shadow-[0_12px_34px_rgba(15,23,42,.11)] backdrop-blur-sm transition-[border-color,box-shadow] dark:bg-[#1b1e22]/96",
                                selected ? "border-violet-500 ring-2 ring-violet-500/20 shadow-[0_18px_48px_rgba(124,58,237,.18)]" : "border-white/80 hover:border-slate-300 dark:border-white/10 dark:hover:border-white/20",
                                connectionSourceId === node.nodeId && "border-emerald-500 ring-2 ring-emerald-500/20",
                                inputPlaceholder && "border-dashed",
                                node.kind === "action" && "border-violet-300/80 bg-violet-50/85 dark:border-violet-500/25 dark:bg-violet-950/20",
                                node.kind === "result" && "border-emerald-300/80 dark:border-emerald-500/25",
                            )}>
                                <div className="relative flex min-h-0 flex-1 items-center justify-center overflow-hidden bg-slate-100/75 text-muted-foreground dark:bg-black/25">
                                    {inputPlaceholder ? (
                                        <div className="flex flex-col items-center gap-3 px-5 text-center">
                                            <Upload className="h-6 w-6 text-violet-500" />
                                            <div className="text-[11px] font-medium text-foreground">{node.title || t("web.workbench.canvas.graph.input")}</div>
                                            <div className="text-[10px] text-muted-foreground">{t("web.workbench.canvas.graph.bindInput")}</div>
                                        </div>
                                    ) : node.kind === "action" ? (
                                        <div className="flex h-full w-full flex-col p-4 text-left">
                                            <div className="flex items-center gap-2">
                                                <span className="grid h-8 w-8 place-items-center rounded-lg bg-violet-500/12 text-violet-600"><Workflow className="h-4 w-4" /></span>
                                                <div className="min-w-0 flex-1">
                                                    <div className="truncate text-[11px] font-semibold text-foreground">{node.title}</div>
                                                    <div className={cn("text-[9px]", actionState === "failed" ? "text-red-500" : actionState === "running" ? "text-violet-600" : "text-muted-foreground")}>{t(`web.workbench.canvas.graph.state.${actionState}` as Parameters<typeof t>[0])}</div>
                                                </div>
                                                {actionState === "running" ? <Loader2 className="h-4 w-4 animate-spin text-violet-500" /> : null}
                                            </div>
                                            <div className="mt-3 line-clamp-3 min-h-[42px] rounded-lg border border-violet-200/60 bg-background/70 px-2.5 py-2 text-[10px] leading-4 text-foreground dark:border-violet-500/15">
                                                {node.prompt || t(actionConfigured ? "web.workbench.canvas.graph.configured" : "web.workbench.canvas.graph.unconfigured")}
                                            </div>
                                            <div className="mt-auto flex flex-wrap items-center gap-1.5 pt-2">
                                                {(actionDefinition?.inputs || []).map((port) => (
                                                    <span key={port.portId} className="rounded-md bg-background/75 px-1.5 py-1 text-[8px] text-muted-foreground">{port.portId} {snapshot.edges.filter((edge) => edge.role === "data" && edge.to === node.nodeId && edge.toPortId === port.portId).length}/{port.max}</span>
                                                ))}
                                                <span className="rounded-md bg-emerald-500/10 px-1.5 py-1 text-[8px] font-medium text-emerald-700 dark:text-emerald-300">{t("web.workbench.canvas.graph.governed")}</span>
                                                <span className="rounded-md bg-background/75 px-1.5 py-1 text-[8px] text-muted-foreground">{t(actionDefinition?.networkRequired ? "web.workbench.canvas.graph.network" : "web.workbench.canvas.graph.local")}</span>
                                                {actionDefinition?.mayIncurCost ? <span className="rounded-md bg-amber-500/10 px-1.5 py-1 text-[8px] text-amber-700 dark:text-amber-300">{t("web.workbench.canvas.graph.cost")}</span> : null}
                                                {stale ? <span className="rounded-md bg-amber-500/10 px-1.5 py-1 text-[8px] text-amber-700 dark:text-amber-300">{t("web.workbench.canvas.graph.stale")}</span> : null}
                                                {actionState === "failed" ? <button type="button" disabled={sessionRunning || submitting} onClick={(event) => { event.stopPropagation(); requestGraphRun([node.nodeId]); }} className="rounded-lg border border-red-300/60 bg-background px-2 py-1 text-[9px] font-medium text-red-600 hover:bg-red-50 disabled:opacity-35">{t("web.workbench.canvas.graph.retry")}</button> : null}
                                                <button type="button" disabled={sessionRunning} onClick={(event) => { event.stopPropagation(); openActionEditor(node); }} className="ml-auto rounded-lg border border-border/60 bg-background px-2 py-1 text-[9px] font-medium text-foreground hover:bg-muted disabled:opacity-35">{t("web.workbench.canvas.graph.configure")}</button>
                                            </div>
                                        </div>
                                    ) : resource ? (
                                        <>
                                            <CreativeCanvasMedia resource={resource} active={selected} visible={visibleNodeIds.has(node.nodeId)} onDimensions={node.kind === "resource" ? (dimensions) => updateNodeDimensions(node.nodeId, dimensions) : undefined} />
                                            <CreativeCanvasMaskOverlay mask={node.mask} />
                                            {node.kind === "result" && versions.length > 1 ? (
                                                <div className="absolute bottom-2 right-2 flex max-w-[70%] flex-row-reverse gap-1 rounded-lg border border-white/70 bg-background/80 p-1 shadow-lg backdrop-blur dark:border-white/10">
                                                    {versions.slice(1, 6).map((version) => {
                                                        const previous = version.artifactId ? resourceMap.get(`artifact:${version.artifactId}`) : null;
                                                        return <button key={version.outputVersionId} type="button" onClick={(event) => { event.stopPropagation(); if (previous) { setInspectResourceOverride(previous); setInspectNodeId(node.nodeId); } }} title={`v${version.version}`} className="grid h-8 w-8 place-items-center overflow-hidden rounded border border-border/60 bg-muted text-[8px] font-semibold text-muted-foreground">{previous?.url && mediaTypeOf(previous) === "image" ? <img src={previous.url} alt="" className="h-full w-full object-cover" /> : `v${version.version}`}</button>;
                                                    })}
                                                </div>
                                            ) : null}
                                        </>
                                    ) : (
                                        <div className="flex flex-col items-center gap-3 px-5 text-center">
                                            {node.kind === "result" && actionState === "running" ? <Loader2 className="h-6 w-6 animate-spin text-violet-500" /> : <PackageOpen className="h-6 w-6" />}
                                            <div className="text-[11px] font-medium text-foreground">{node.title || t("web.workbench.canvas.graph.result")}</div>
                                            <div className="text-[10px] text-muted-foreground">{node.kind === "result" ? t("web.workbench.canvas.graph.awaitingResult") : t("web.workbench.canvas.graph.awaitingInput")}</div>
                                        </div>
                                    )}
                                </div>
                                <div
                                    data-canvas-title={node.nodeId}
                                    title={resource?.name || node.title || t("web.workbench.canvas.graph.result")}
                                    className="group/title flex h-9 shrink-0 items-center gap-2 border-t border-border/50 px-3 transition-colors hover:bg-violet-500/[.06]"
                                >
                                    <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", node.kind === "action" ? "bg-violet-500" : node.kind === "result" ? "bg-emerald-500" : node.origin === "source" ? "bg-cyan-500" : node.origin === "artifact" ? "bg-violet-500" : "bg-slate-400")} />
                                    <span className="min-w-0 flex-1 truncate text-[11px] font-medium transition-colors group-hover/title:text-violet-700 dark:group-hover/title:text-violet-300">{resource?.name || node.title || t("web.workbench.canvas.graph.result")}</span>
                                    {node.kind === "result" && versions[0] ? <span className="rounded-full bg-emerald-500/10 px-1.5 py-0.5 text-[9px] font-semibold text-emerald-700">v{versions[0].version}</span> : null}
                                    {node.mask?.strokes.length ? <span className="rounded-full bg-rose-500/10 px-1.5 py-0.5 text-[9px] font-semibold text-rose-600">{t("web.workbench.canvas.graph.maskRevision", { revision: node.mask.revision })}</span> : null}
                                </div>
                            </div>
                            {canvasPortsForNode(node).map((port) => {
                                const fixedResultInput = node.kind === "result" && port === "left";
                                const portDisabled = sessionRunning || fixedResultInput;
                                const verdict = connectionVerdicts.get(node.nodeId);
                                const candidatePort = connectionDraft
                                    ? (interactionRef.current?.kind === "connect"
                                        ? (interactionRef.current.fromPort === "right" ? "left" : "right")
                                        : interactionRef.current?.kind === "reconnect"
                                            ? (interactionRef.current.movingEnd === "from" ? "right" : "left")
                                            : null)
                                    : null;
                                const candidate = candidatePort === port && node.nodeId !== connectionDraft?.fromNodeId;
                                const portLabel = candidate && verdict?.issue ? connectionIssueLabel(verdict.issue) : fixedResultInput ? t("web.workbench.canvas.graph.resultInputFixed") : port === "left" ? t("web.workbench.canvas.graph.connectUpstream") : t("web.workbench.canvas.graph.connectDownstream");
                                return (
                                <button
                                    key={port}
                                    type="button"
                                    data-canvas-port={port}
                                    data-canvas-node-id={node.nodeId}
                                    disabled={portDisabled}
                                    onPointerDown={(event) => handlePortPointerDown(event, node, port)}
                                    title={portLabel}
                                    aria-label={portLabel}
                                    data-connection-valid={candidate ? String(Boolean(verdict?.valid)) : undefined}
                                    style={{ left: port === "left" ? 0 : node.width, top: node.height / 2 }}
                                    className={cn(
                                        "group/port absolute z-30 grid h-7 w-7 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full outline-none transition-transform hover:scale-110 focus-visible:ring-2 focus-visible:ring-violet-500 disabled:cursor-not-allowed disabled:opacity-35",
                                        selected || connectionSourceId === node.nodeId || candidate ? "opacity-100" : "opacity-70 hover:opacity-100",
                                    )}
                                >
                                    <span className={cn(
                                        "h-3 w-3 rounded-full border-2 border-background bg-slate-400 shadow-[0_0_0_1px_rgba(100,116,139,.45)] transition-colors group-hover/port:bg-violet-500 motion-reduce:transition-none",
                                        candidate && verdict?.valid && "bg-emerald-500 shadow-[0_0_0_4px_rgba(16,185,129,.18)]",
                                        candidate && !verdict?.valid && "bg-red-500 shadow-[0_0_0_4px_rgba(239,68,68,.14)]",
                                    )} />
                                </button>
                                );
                            })}
                        </div>
                    );
                })}
            </div>

            {!snapshot.nodes.length && !loading ? (
                <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-3 text-center">
                    <div className="grid h-14 w-14 place-items-center rounded-2xl border border-white/80 bg-background/80 shadow-xl backdrop-blur dark:border-white/10"><ImagePlus className="h-6 w-6 text-muted-foreground" /></div>
                    <div className="text-sm font-semibold">{t("web.workbench.canvas.emptyTitle")}</div>
                    <div className="max-w-sm text-xs leading-5 text-muted-foreground">{t("web.workbench.canvas.emptyHint")}</div>
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

            <div data-canvas-wheel-isolation className="custom-scrollbar absolute left-3 top-3 z-30 flex max-w-[calc(100%-176px)] items-center gap-1 overflow-x-auto rounded-2xl border border-white/80 bg-background/88 p-1.5 shadow-[0_12px_36px_rgba(15,23,42,.12)] backdrop-blur-xl dark:border-white/10" onWheel={(event) => event.stopPropagation()}>
                <button type="button" onClick={() => setTool("select")} className={cn("rounded-xl p-2", tool === "select" ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted hover:text-foreground")} aria-label={t("web.workbench.canvas.graph.selectTool")}><MousePointer2 className="h-4 w-4" /></button>
                <button type="button" onClick={() => setTool("pan")} className={cn("rounded-xl p-2", tool === "pan" ? "bg-foreground text-background" : "text-muted-foreground hover:bg-muted hover:text-foreground")} aria-label={t("web.workbench.canvas.graph.panTool")}><Hand className="h-4 w-4" /></button>
                <button type="button" disabled={sessionRunning || !canUndo} onClick={() => undo()} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label={t("web.workbench.canvas.graph.undo")} title={`${t("web.workbench.canvas.graph.undo")} Ctrl+Z`}><Undo2 className="h-4 w-4" /></button>
                <button type="button" disabled={sessionRunning || !canRedo} onClick={() => redo()} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label={t("web.workbench.canvas.graph.redo")} title={`${t("web.workbench.canvas.graph.redo")} Ctrl+Shift+Z`}><Redo2 className="h-4 w-4" /></button>
                <span className="mx-0.5 h-5 w-px bg-border" />
                <button type="button" disabled={sessionRunning || uploading} onClick={() => fileInputRef.current?.click()} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-35" aria-label={t("web.workbench.canvas.graph.upload")}>{uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}</button>
                <button type="button" onClick={() => selectedIds.length ? focusNodes(selectedIds) : fitView()} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={selectedIds.length ? t("web.workbench.canvas.graph.focusSelection") : t("web.workbench.canvas.graph.fit")}><Focus className="h-4 w-4" /></button>
                <button type="button" disabled={sessionRunning || snapshot.nodes.length < 2} onClick={() => organizeLayout(selectedIds.length > 1 ? selectedIds : [])} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label={t("web.workbench.canvas.graph.organizeLayout")} title={t("web.workbench.canvas.graph.organizeLayout")}><LayoutGrid className="h-4 w-4" /></button>
                <button type="button" onClick={() => zoomAtCenter(-0.15)} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={t("web.workbench.canvas.graph.zoomOut")}><ZoomOut className="h-4 w-4" /></button>
                <span className="w-10 text-center text-[10px] tabular-nums text-muted-foreground">{Math.round(snapshot.viewport.scale * 100)}%</span>
                <button type="button" onClick={() => zoomAtCenter(0.15)} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={t("web.workbench.canvas.graph.zoomIn")}><ZoomIn className="h-4 w-4" /></button>
                <span className="mx-0.5 h-5 w-px bg-border" />
                <button type="button" disabled={!actionRunSummary.total} onClick={() => { setPreflightTargets([]); setPreflightOpen((current) => !current); setTemplateOpen(false); setComposer(null); setContextMenu(null); setTrayOpen(false); }} className={cn("relative rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30", preflightOpen && "bg-muted text-foreground")} aria-label={t("web.workbench.canvas.graph.preflight")} title={t("web.workbench.canvas.graph.preflight")}><Check className="h-4 w-4" />{preflightIssues.length ? <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-red-500" /> : null}</button>
                <button type="button" disabled={sessionRunning || submitting || graphSaving || !actionRunSummary.total} onClick={() => requestGraphRun([])} className="flex h-8 items-center gap-1.5 rounded-xl bg-violet-600 px-3 text-[10px] font-semibold text-white hover:bg-violet-700 disabled:opacity-35"><Play className="h-3.5 w-3.5" />{t("web.workbench.canvas.graph.runAll")}</button>
                <button type="button" disabled={sessionRunning} onClick={() => { setTemplateOpen((current) => !current); setPreflightOpen(false); setComposer(null); setContextMenu(null); setTrayOpen(false); }} className={cn("rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-35", templateOpen && "bg-muted text-foreground")} aria-label={t("web.workbench.canvas.graph.templates")}><Workflow className="h-4 w-4" /></button>
                <span className="px-1 text-[8px] tabular-nums text-muted-foreground">{sessionRunning ? t("web.workbench.canvas.graph.progress", actionRunSummary) : graphSaving ? t("web.workbench.canvas.graph.saving") : `r${graphRevision}`}</span>
            </div>

            {templateOpen ? (
                <div data-canvas-wheel-isolation className="absolute left-3 top-14 z-40 w-[340px] max-w-[calc(100%-24px)] rounded-[18px] border border-white/80 bg-background/96 p-2 shadow-[0_22px_64px_rgba(15,23,42,.22)] backdrop-blur-xl dark:border-white/10" onPointerDown={(event) => event.stopPropagation()} onWheel={(event) => event.stopPropagation()}>
                    <div className="flex items-center gap-2 px-1 pb-2"><Workflow className="h-4 w-4 text-violet-600" /><span className="flex-1 text-[11px] font-semibold">{t("web.workbench.canvas.graph.templates")}</span><button type="button" onClick={() => setTemplateOpen(false)} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted"><X className="h-3.5 w-3.5" /></button></div>
                    <div className="flex gap-1.5"><input value={templateTitle} onChange={(event) => setTemplateTitle(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void saveWorkflowTemplate(); }} disabled={savingTemplate || sessionRunning} placeholder={t("web.workbench.canvas.graph.templateName")} className="h-8 min-w-0 flex-1 rounded-lg border border-border/70 bg-background px-2 text-[10px] outline-none focus:border-violet-400" /><button type="button" disabled={!templateTitle.trim() || savingTemplate || sessionRunning || !snapshot.nodes.some((node) => node.kind === "action")} onClick={() => void saveWorkflowTemplate()} className="grid h-8 w-8 place-items-center rounded-lg bg-primary text-primary-foreground disabled:opacity-35">{savingTemplate ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}</button></div>
                    <div className="mt-2 max-h-64 space-y-1 overflow-y-auto">
                        {templates.map((template) => <div key={template.templateId} className="flex items-center rounded-xl border border-transparent hover:border-border hover:bg-muted"><button type="button" disabled={savingTemplate || sessionRunning} onClick={() => void instantiateWorkflowTemplate(template.templateId)} className="flex min-w-0 flex-1 items-center gap-2 px-2 py-2 text-left disabled:opacity-35"><span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-violet-500/10 text-violet-600"><Workflow className="h-3.5 w-3.5" /></span><span className="min-w-0 flex-1"><span className="block truncate text-[10px] font-semibold">{template.title}</span><span className="block text-[8px] text-muted-foreground">{t("web.workbench.canvas.graph.templateInputs", { count: template.inputCount })}</span></span><Plus className="h-3.5 w-3.5 shrink-0 text-muted-foreground" /></button><button type="button" disabled={savingTemplate || sessionRunning} onClick={() => void deleteWorkflowTemplate(template)} className="mr-1 grid h-7 w-7 shrink-0 place-items-center rounded-lg text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-35" aria-label={t("web.workbench.canvas.graph.deleteTemplate")} title={t("web.workbench.canvas.graph.deleteTemplate")}><Trash2 className="h-3.5 w-3.5" /></button></div>)}
                        {!templates.length ? <div className="px-3 py-6 text-center text-[10px] text-muted-foreground">{t("web.workbench.canvas.graph.noTemplates")}</div> : null}
                    </div>
                </div>
            ) : null}

            {preflightOpen ? (
                <CanvasPreflightPanel
                    issues={visiblePreflightIssues}
                    actionCount={executionActionIds.size}
                    title={t("web.workbench.canvas.graph.preflight")}
                    readyLabel={t("web.workbench.canvas.graph.preflightReady", { count: executionActionIds.size })}
                    runLabel={preflightTargets.length ? t("web.workbench.canvas.graph.runToHere") : t("web.workbench.canvas.graph.runAll")}
                    closeLabel={t("web.workbench.canvas.graph.close")}
                    issueLabel={preflightIssueLabel}
                    onFocus={(nodeId) => { setSelectedIds([nodeId]); focusNodes([nodeId]); }}
                    onRun={() => { setPreflightOpen(false); void runGraph(preflightTargets); }}
                    onClose={() => setPreflightOpen(false)}
                />
            ) : null}

            {sessionRunning ? (
                <div className="absolute left-1/2 top-3 z-30 flex -translate-x-1/2 items-center gap-2 rounded-full border border-amber-300/70 bg-amber-50/92 px-3 py-2 text-[11px] font-medium text-amber-900 shadow-lg backdrop-blur dark:border-amber-500/25 dark:bg-amber-950/80 dark:text-amber-100">
                    <Lock className="h-3.5 w-3.5" />{t("web.workbench.canvas.graph.locked")}
                </div>
            ) : connectionSourceId || connectionIssue ? (
                <div className={cn("absolute left-1/2 top-3 z-30 flex -translate-x-1/2 items-center gap-2 rounded-full border px-3 py-2 text-[11px] font-medium shadow-lg backdrop-blur", connectionIssue ? "border-red-300/70 bg-red-50/92 text-red-900 dark:border-red-500/25 dark:bg-red-950/80 dark:text-red-100" : "border-emerald-300/70 bg-emerald-50/92 text-emerald-900 dark:border-emerald-500/25 dark:bg-emerald-950/80 dark:text-emerald-100")}>
                    <Link2 className="h-3.5 w-3.5" />{connectionIssue ? connectionIssueLabel(connectionIssue) : t("web.workbench.canvas.graph.connecting")}<button type="button" onClick={() => { setConnectionSourceId(null); setConnectionDraft(null); setConnectionIssue(null); }} className="ml-1 rounded-full p-0.5 hover:bg-black/10"><X className="h-3 w-3" /></button>
                </div>
            ) : null}

            <button type="button" onClick={() => { setTrayOpen((current) => !current); setPreflightOpen(false); setTemplateOpen(false); setComposer(null); setContextMenu(null); }} className="absolute right-3 top-3 z-30 flex h-10 items-center gap-2 rounded-2xl border border-white/80 bg-background/88 px-3 text-[11px] font-semibold shadow-[0_12px_36px_rgba(15,23,42,.12)] backdrop-blur-xl hover:bg-background dark:border-white/10">
                <Archive className="h-4 w-4" />{t("web.workbench.canvas.library.materials")} <span className="rounded-full bg-muted px-1.5 py-0.5 text-[9px] text-muted-foreground">{workspaceResources.length}</span>
            </button>

            {trayOpen ? (
                <div data-canvas-wheel-isolation className="absolute right-3 top-14 z-40 flex max-h-[min(620px,calc(100%-72px))] w-[380px] max-w-[calc(100%-24px)] flex-col overflow-hidden rounded-[20px] border border-white/80 bg-background/94 shadow-[0_24px_72px_rgba(15,23,42,.2)] backdrop-blur-xl dark:border-white/10">
                    <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border/60 px-3">
                        <PackageOpen className="h-4 w-4 text-muted-foreground" />
                        <span className="flex-1 text-xs font-semibold">{t("web.workbench.canvas.library.title")}</span>
                        <button type="button" disabled={sessionRunning || uploading} onClick={() => fileInputRef.current?.click()} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-35" aria-label={t("web.workbench.canvas.graph.upload")}><Plus className="h-4 w-4" /></button>
                        <button type="button" onClick={() => setTrayOpen(false)} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={t("web.workbench.canvas.library.close")}><X className="h-4 w-4" /></button>
                    </div>
                    <div className="custom-scrollbar min-h-0 flex-1 overflow-y-auto p-2.5">
                        <div className="mb-2 space-y-1 rounded-xl border border-border/60 bg-muted/15 p-1.5">
                            <button type="button" onClick={() => setActiveFolderId("")} className={cn("flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-[10px] font-medium hover:bg-muted", !activeFolderId && "bg-muted text-primary")}>
                                <Archive className="h-3.5 w-3.5" /><span className="flex-1">{t("web.workbench.canvas.library.all")}</span><span className="text-[9px] text-muted-foreground">{workspaceResources.length}</span>
                            </button>
                            {folderRows.map((folder) => (
                                <button
                                    key={folder.folderId}
                                    type="button"
                                    onClick={() => setActiveFolderId(folder.folderId)}
                                    className={cn("flex h-8 w-full items-center gap-2 rounded-lg pr-2 text-left text-[10px] font-medium hover:bg-muted", activeFolderId === folder.folderId && "bg-muted text-primary")}
                                    style={{ paddingLeft: 8 + folder.depth * 14 }}
                                >
                                    <Folder className="h-3.5 w-3.5 shrink-0" /><span className="min-w-0 flex-1 truncate">{folder.title}</span><span className="text-[9px] text-muted-foreground">{workspaceResources.filter((resource) => resource.folderId === folder.folderId).length}</span>
                                </button>
                            ))}
                            <div className="grid grid-cols-[1fr_94px_32px] gap-1 pt-1">
                                <input value={newFolderTitle} onChange={(event) => setNewFolderTitle(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void createWorkspaceFolder(); }} disabled={sessionRunning || creatingFolder} placeholder={t("web.workbench.canvas.library.newFolder")} className="h-8 min-w-0 rounded-lg border border-border/60 bg-background px-2 text-[10px] outline-none focus:border-violet-400" />
                                <select value={newFolderKind} onChange={(event) => setNewFolderKind(event.target.value as WorkspaceMediaFolder["folderKind"])} disabled={sessionRunning || creatingFolder} className="h-8 rounded-lg border border-border/60 bg-background px-1 text-[9px] outline-none">
                                    <option value="production">{t("web.workbench.canvas.library.folderKind.production")}</option><option value="episode">{t("web.workbench.canvas.library.folderKind.episode")}</option><option value="sources">{t("web.workbench.canvas.library.folderKind.sources")}</option><option value="work">{t("web.workbench.canvas.library.folderKind.work")}</option><option value="outputs">{t("web.workbench.canvas.library.folderKind.outputs")}</option><option value="delivery">{t("web.workbench.canvas.library.folderKind.delivery")}</option><option value="custom">{t("web.workbench.canvas.library.folderKind.custom")}</option>
                                </select>
                                <button type="button" onClick={() => void createWorkspaceFolder()} disabled={!newFolderTitle.trim() || sessionRunning || creatingFolder} className="grid h-8 w-8 place-items-center rounded-lg border border-border/60 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-35" aria-label={t("web.workbench.canvas.library.createFolder")}>{creatingFolder ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FolderPlus className="h-3.5 w-3.5" />}</button>
                            </div>
                        </div>
                        {loading ? <div className="flex justify-center py-10"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div> : null}
                        {!loading && !visibleWorkspaceResources.length ? <div className="px-5 py-10 text-center text-xs leading-5 text-muted-foreground">{t("web.workbench.canvas.library.emptyFolder")}</div> : null}
                        <div className="grid grid-cols-3 gap-2">
                            {visibleWorkspaceResources.map((resource) => (
                                <div
                                    key={`${resource.origin}:${resource.id}`}
                                    draggable={!sessionRunning}
                                    onDragStart={(event) => event.dataTransfer.setData(CANVAS_DRAG_TYPE, `${resource.origin}:${resource.id}`)}
                                    title={resource.name}
                                    className="group overflow-hidden rounded-xl border border-border/60 bg-muted/25 text-left hover:border-violet-400 hover:bg-muted/45"
                                >
                                    <button
                                        type="button"
                                        disabled={sessionRunning}
                                        onClick={() => {
                                            void adoptAndPlaceResource(resource, pendingConnectionDrop?.point, pendingConnectionDrop ? {
                                                fromNodeId: pendingConnectionDrop.fromNodeId,
                                                fromPort: pendingConnectionDrop.fromPort,
                                                direction: pendingConnectionDrop.direction,
                                            } : undefined).then(() => {
                                                setPendingConnectionDrop(null);
                                            }).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
                                        }}
                                        className="block w-full disabled:cursor-not-allowed disabled:opacity-55"
                                    >
                                        <span className="flex h-[66px] items-center justify-center overflow-hidden bg-background/60"><CreativeCanvasMedia resource={resource} compact /></span>
                                        <span className="flex items-center gap-1 border-t border-border/50 px-1.5 py-1.5"><span className="h-1.5 w-1.5 shrink-0 rounded-full bg-violet-500" /><span className="truncate text-[9px] font-medium">{resource.name}</span></span>
                                    </button>
                                    <select value={resource.folderId || ""} onChange={(event) => void moveWorkspaceAsset(resource.id, event.target.value)} disabled={sessionRunning} aria-label={t("web.workbench.canvas.library.organize", { name: resource.name })} className="h-6 w-full border-0 border-t border-border/50 bg-transparent px-1 text-[8px] text-muted-foreground outline-none">
                                        <option value="">{t("web.workbench.canvas.library.uncategorized")}</option>
                                        {folderRows.map((folder) => <option key={folder.folderId} value={folder.folderId}>{`${"· ".repeat(folder.depth)}${folder.title}`}</option>)}
                                    </select>
                                </div>
                            ))}
                        </div>
                    </div>
                    <div className="shrink-0 border-t border-border/60 px-3 py-2 text-[9px] text-muted-foreground">
                        {t(mediaKitStatus === "ready" ? "web.workbench.canvas.library.toolReady" : mediaKitStatus === "loading" ? "web.workbench.canvas.library.toolLoading" : "web.workbench.canvas.library.toolUnavailable")}
                    </div>
                </div>
            ) : null}

            {!trayOpen && !inspectNodeId && !maskNodeId ? (
                <CanvasMiniMap
                    snapshot={snapshot}
                    boardWidth={boardSize.width}
                    boardHeight={boardSize.height}
                    selectedIds={selectedIds}
                    label={t("web.workbench.canvas.graph.minimap")}
                    onNavigate={navigateMiniMap}
                />
            ) : null}

            {selectedBounds && !selectionRect && !maskNodeId && !inspectNodeId ? (
                <div
                    data-canvas-wheel-isolation
                    style={{ left: selectedBounds.left, top: selectedBounds.top }}
                    className="custom-scrollbar absolute z-30 flex max-w-[calc(100%-24px)] -translate-x-1/2 -translate-y-[calc(100%+10px)] items-center gap-1 overflow-x-auto rounded-2xl border border-white/80 bg-background/92 p-1.5 shadow-[0_14px_42px_rgba(15,23,42,.16)] backdrop-blur-xl dark:border-white/10"
                >
                    <span className="px-2 text-[10px] font-semibold text-muted-foreground">{t("web.workbench.canvas.graph.selectedCount", { count: selectedIds.length })}</span>
                    <button type="button" disabled={sessionRunning} onClick={openSelectionInteraction} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-primary disabled:opacity-30" aria-label={t("web.workbench.canvas.graph.relationshipNote")}><MessageSquare className="h-4 w-4" /></button>
                    {selectedExecutableTargetIds.length ? <button type="button" disabled={sessionRunning || submitting} onClick={() => requestGraphRun(selectedExecutableTargetIds)} className="flex h-8 items-center gap-1.5 rounded-xl bg-violet-600 px-2.5 text-[10px] font-semibold text-white hover:bg-violet-700 disabled:opacity-30" aria-label={t("web.workbench.canvas.graph.runToHere")}><Play className="h-3.5 w-3.5" />{t("web.workbench.canvas.graph.runToHere")}</button> : null}
                    <button type="button" disabled={sessionRunning} onClick={() => selectedIds.length > 1 ? connectSelection(selectedIds) : setConnectionSourceId(selectedIds[0])} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-emerald-600 disabled:opacity-30" aria-label={selectedIds.length > 1 ? t("web.workbench.canvas.graph.connectSelection") : t("web.workbench.canvas.graph.connectNode")}><Link2 className="h-4 w-4" /></button>
                    {selectedImageNode ? <button type="button" disabled={sessionRunning} onClick={() => setMaskNodeId(selectedImageNode.nodeId)} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-rose-600 disabled:opacity-30" aria-label={t("web.workbench.canvas.graph.drawMask")}><Sparkles className="h-4 w-4" /></button> : null}
                    <button type="button" disabled={sessionRunning} onClick={duplicateSelection} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label={t("web.workbench.canvas.graph.duplicate")} title={`${t("web.workbench.canvas.graph.duplicate")} Ctrl+D`}><Copy className="h-4 w-4" /></button>
                    <button type="button" onClick={() => focusNodes(selectedIds)} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={t("web.workbench.canvas.graph.focusSelection")}><Focus className="h-4 w-4" /></button>
                    {selectedIds.length > 1 ? <>
                        <button type="button" disabled={sessionRunning} onClick={() => alignSelection("left")} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label={t("web.workbench.canvas.graph.alignLeft")} title={t("web.workbench.canvas.graph.alignLeft")}><AlignLeft className="h-4 w-4" /></button>
                        <button type="button" disabled={sessionRunning} onClick={() => alignSelection("top")} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label={t("web.workbench.canvas.graph.alignTop")} title={t("web.workbench.canvas.graph.alignTop")}><MoveUp className="h-4 w-4" /></button>
                        <button type="button" disabled={sessionRunning} onClick={() => alignSelection("horizontal")} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label={t("web.workbench.canvas.graph.distributeHorizontal")} title={t("web.workbench.canvas.graph.distributeHorizontal")}><Columns3 className="h-4 w-4" /></button>
                        <button type="button" disabled={sessionRunning} onClick={() => alignSelection("vertical")} className="rounded-xl p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-30" aria-label={t("web.workbench.canvas.graph.distributeVertical")} title={t("web.workbench.canvas.graph.distributeVertical")}><Rows3 className="h-4 w-4" /></button>
                    </> : null}
                    <button type="button" disabled={sessionRunning} onClick={() => removeNodes(selectedIds)} className="rounded-xl p-2 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-30" aria-label={t("web.workbench.canvas.graph.removeSelection")}><Trash2 className="h-4 w-4" /></button>
                </div>
            ) : null}

            {contextMenu ? (
                <CanvasActionMenu
                    key={`${contextMenu.target}:${contextMenu.edgeId || ""}:${contextMenu.nodeIds.join(",")}`}
                    menu={contextMenu}
                    actions={menuActions}
                    boardWidth={boardSize.width || 360}
                    boardHeight={boardSize.height || 480}
                    actionLabel={actionLabel}
                    costLabel={t("web.workbench.canvas.graph.possibleCost")}
                    moreLabel={t("web.workbench.canvas.graph.moreActions")}
                    searchLabel={t("web.workbench.canvas.graph.searchActions")}
                    emptyLabel={t("web.workbench.canvas.graph.noActions")}
                    onSelect={(action) => handleAction(action, contextMenu)}
                />
            ) : null}

            {edgeNote ? (
                <div
                    data-canvas-wheel-isolation
                    style={{ left: Math.min(Math.max(176, edgeNote.x), Math.max(176, (boardRef.current?.clientWidth || 380) - 176)), top: Math.min(Math.max(84, edgeNote.y), Math.max(84, (boardRef.current?.clientHeight || 420) - 150)) }}
                    className="absolute z-[70] w-[340px] -translate-x-1/2 rounded-[18px] border border-white/80 bg-background/96 p-2 shadow-[0_24px_72px_rgba(15,23,42,.25)] backdrop-blur-xl dark:border-white/10"
                    onPointerDown={(event) => event.stopPropagation()}
                    onWheel={(event) => event.stopPropagation()}
                >
                    <div className="flex items-center gap-2 px-1 pb-2"><MessageSquare className="h-4 w-4 text-violet-600" /><span className="flex-1 text-[11px] font-semibold">{t("web.workbench.canvas.graph.relationshipNote")}</span><button type="button" onClick={() => setEdgeNote(null)} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted"><X className="h-3.5 w-3.5" /></button></div>
                    <textarea autoFocus rows={3} value={edgeNote.text} onChange={(event) => setEdgeNote((current) => current ? { ...current, text: event.target.value } : current)} placeholder={t("web.workbench.canvas.graph.relationshipPlaceholder")} className="w-full resize-none rounded-xl border border-border/70 bg-muted/25 px-3 py-2 text-xs leading-5 outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-400/15" />
                    <div className="mt-2 flex justify-end"><button type="button" disabled={sessionRunning} onClick={() => { commitSnapshot((current) => ({ ...current, edges: current.edges.map((edge) => edge.edgeId === edgeNote.edgeId ? { ...edge, note: edgeNote.text.trim() } : edge) })); setEdgeNote(null); }} className="flex h-8 items-center gap-1.5 rounded-xl bg-primary px-3 text-[11px] font-semibold text-primary-foreground disabled:opacity-35"><Save className="h-3.5 w-3.5" />{t("web.workbench.canvas.graph.saveNote")}</button></div>
                </div>
            ) : null}

            {composer ? (
                <div
                    data-canvas-wheel-isolation
                    style={{
                        left: composerLeft,
                        width: composerPanelWidth,
                        top: composerUsesTimeline || composerUsesPsd
                            ? Math.min(Math.max(12, composer.y), Math.max(12, (boardRef.current?.clientHeight || 520) - 430))
                            : Math.min(Math.max(84, composer.y), Math.max(84, (boardRef.current?.clientHeight || 420) - 150)),
                    }}
                    className="absolute z-[70] max-h-[calc(100%-24px)] -translate-x-1/2 overflow-y-auto rounded-[18px] border border-white/80 bg-background/96 p-2 shadow-[0_24px_72px_rgba(15,23,42,.25)] backdrop-blur-xl dark:border-white/10"
                    onPointerDown={(event) => event.stopPropagation()}
                    onWheel={(event) => event.stopPropagation()}
                >
                    <div className="flex items-center gap-2 px-1 pb-2">
                        <span className="grid h-7 w-7 place-items-center rounded-lg bg-violet-500/10 text-violet-600"><Sparkles className="h-3.5 w-3.5" /></span>
                        <div className="min-w-0 flex-1"><div className="truncate text-[11px] font-semibold">{actionLabel(composer.action)}</div><div className="text-[9px] text-muted-foreground">{t("web.workbench.canvas.graph.configHint")}</div></div>
                        <button type="button" onClick={() => setComposer(null)} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted"><X className="h-3.5 w-3.5" /></button>
                    </div>
                    {composerUsesTimeline && composer.timeRange ? (
                        <CanvasTimeRangeEditor
                            sessionId={sessionId}
                            resource={composerResource}
                            range={composer.timeRange}
                            mode={composerUsesFramePick ? "frame" : "range"}
                            onChange={(timeRange) => setComposer((current) => current?.operationId === composer.operationId ? { ...current, timeRange } : current)}
                        />
                    ) : composerUsesPsdComposition && composer.psdComposition ? (
                        <CreativeCanvasPsdCompositionEditor
                            sources={composerPsdSources}
                            value={composer.psdComposition}
                            onChange={(psdComposition) => setComposer((current) => current?.operationId === composer.operationId ? { ...current, psdComposition } : current)}
                        />
                    ) : composerUsesPsdLayers && composerResource ? (
                        <CreativeCanvasPsdLayerEditor
                            sessionId={sessionId}
                            resource={composerResource}
                            edits={composer.psdEdits || []}
                            onChange={(psdEdits) => setComposer((current) => current?.operationId === composer.operationId ? { ...current, psdEdits } : current)}
                        />
                    ) : (
                        <textarea
                            autoFocus
                            rows={3}
                            value={composer.text}
                            onChange={(event) => setComposer((current) => current ? { ...current, text: event.target.value } : current)}
                            onKeyDown={(event) => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") void submitComposer(); }}
                            placeholder={t(composer.action.requiresPrompt ? "web.workbench.canvas.graph.promptRequired" : "web.workbench.canvas.graph.promptOptional")}
                            className="w-full resize-none rounded-xl border border-border/70 bg-muted/25 px-3 py-2 text-xs leading-5 outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-400/15"
                        />
                    )}
                    <div className="mt-2 flex items-center gap-2">
                        <span className="min-w-0 flex-1 truncate px-1 text-[9px] text-muted-foreground">{t("web.workbench.canvas.graph.referenceCount", { count: composer.nodeIds.length })}{composer.action.requiresMask ? ` · ${t("web.workbench.canvas.graph.freezeMask")}` : ""}</span>
                        <button type="button" disabled={composerSubmitDisabled} onClick={() => void submitComposer()} className="flex h-8 items-center gap-1.5 rounded-xl bg-primary px-3 text-[11px] font-semibold text-primary-foreground disabled:opacity-35">{submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}{t("web.workbench.canvas.graph.saveConfig")}</button>
                    </div>
                </div>
            ) : null}

            {inspectNode && inspectResource ? (
                <div data-canvas-wheel-isolation className="absolute inset-6 z-50 flex min-h-0 flex-col overflow-hidden rounded-[22px] border border-white/80 bg-background/96 shadow-[0_30px_100px_rgba(15,23,42,.28)] backdrop-blur-xl dark:border-white/10">
                    <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border/60 px-3"><span className="min-w-0 flex-1 truncate text-xs font-semibold">{inspectResource.name}</span>{inspectResource.url ? <a href={inspectResource.url} download={inspectResource.name} className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={t("web.workbench.canvas.preview.download")}><Download className="h-4 w-4" /></a> : null}<button type="button" onClick={() => { setInspectNodeId(null); setInspectResourceOverride(null); }} className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground" aria-label={t("web.workbench.canvas.preview.close")}><X className="h-4 w-4" /></button></div>
                    <div className="min-h-0 flex-1 overflow-auto bg-black/5 p-2">
                        {mediaTypeOf(inspectResource) === "psd" ? (
                            <CreativeCanvasPsdLayerEditor sessionId={sessionId} resource={inspectResource} edits={[]} onChange={() => undefined} readOnly />
                        ) : <CreativeCanvasMedia resource={inspectResource} inspect />}
                    </div>
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
                        void freezeMask({ ...maskNode, mask: value }, maskResource).then((frozen) => {
                            if (!frozen || sessionRunningRef.current) return;
                            const frozenNodeId = placeResource(frozen, {
                                x: maskNode.x,
                                y: maskNode.y + maskNode.height + 56,
                            });
                            const action = CREATIVE_CANVAS_ACTIONS.find((item) => item.actionId === "creative_media.edit_image_region");
                            if (action && frozenNodeId) createActionCard(action, {
                                x: (boardRef.current?.clientWidth || 600) / 2,
                                y: 92,
                                target: "selection",
                                nodeIds: [maskNode.nodeId, frozenNodeId],
                            });
                        }).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
                    }}
                />
            ) : null}

            {error ? <div className="absolute bottom-3 left-1/2 z-[80] flex max-w-lg -translate-x-1/2 items-center gap-2 rounded-xl border border-red-300/60 bg-red-50/95 px-3 py-2 text-[11px] text-red-800 shadow-lg backdrop-blur dark:border-red-500/20 dark:bg-red-950/90 dark:text-red-100"><span className="min-w-0 flex-1">{error}</span><button type="button" onClick={() => setError("")} className="rounded p-1 hover:bg-black/5"><X className="h-3.5 w-3.5" /></button></div> : null}

            <input ref={fileInputRef} type="file" multiple accept={MODEL_ACCEPT} onChange={handleFileChange} className="hidden" />
        </div>
    );
}
