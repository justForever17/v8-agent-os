"use client";

import type { CreativeCanvasMediaType } from "@/lib/creative-canvas-actions";
import type { CreativeCanvasMaskState } from "../CreativeCanvasMaskEditor";
import { creativeCanvasMediaType } from "../CreativeCanvasMedia";
import {
    EMPTY_SNAPSHOT,
    MAX_EDGES,
    MAX_NODES,
    MEDIA_FOOTER_HEIGHT,
    NODE_HEIGHT,
    NODE_WIDTH,
    type CanvasEdge,
    type CanvasNode,
    type CanvasResource,
    type CanvasSnapshot,
    type ResourceOrigin,
} from "./types";

export function recordOf(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

export function stringValue(record: Record<string, unknown>, ...keys: string[]) {
    for (const key of keys) {
        const value = record[key];
        if (typeof value === "string" && value.trim()) return value.trim();
    }
    return "";
}

export function createId(prefix: string) {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return `${prefix}-${crypto.randomUUID()}`;
    return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function toWebResourceUrl(value: unknown) {
    const url = String(value || "").trim();
    const adminPrefix = "/api/client/workspace/resource";
    return url.startsWith(adminPrefix)
        ? `/api/workspace/resource${url.slice(adminPrefix.length)}`
        : url;
}

export function mediaTypeOf(resource: Pick<CanvasResource, "name" | "mimeType" | "mediaType">): CreativeCanvasMediaType {
    const result = creativeCanvasMediaType(resource);
    if (["image", "video", "audio", "model_3d", "psd", "document", "text", "mask", "metadata"].includes(result)) {
        return result as CreativeCanvasMediaType;
    }
    return "unknown";
}

export function normalizeResource(
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
    const id = stringValue(record, "artifactId", "artifact_id", "sourceId", "source_id", "assetId", "asset_id", "id") || `${origin}-${index}`;
    const name = stringValue(record, "displayLabel", "display_label", "title", "name", "filename", "fileName") || id;
    const mimeType = stringValue(record, "mimeType", "mime_type", "contentType", "content_type", "type") || "application/octet-stream";
    const url = toWebResourceUrl(stringValue(record, "previewUrl", "preview_url", "contentUrl", "content_url", "downloadUrl", "download_url", "url", "publicUrl", "public_url", "externalUrl", "external_url")) || undefined;
    const explicitResourceRef = recordOf(record.resourceRef || record.resource_ref);
    const resourceRef = origin === "workspace_asset"
        ? { kind: "workspace_media_asset", assetId: id }
        : explicitResourceRef;
    const base: CanvasResource = {
        id,
        origin,
        name,
        mimeType,
        url,
        caption: stringValue(record, "caption", "description", "summary") || undefined,
        resourceRef: Object.keys(resourceRef).length ? resourceRef : undefined,
        workspacePath: stringValue(record, "workspacePath", "workspace_path") || undefined,
        workspaceRelativePath: stringValue(record, "workspaceRelativePath", "workspace_relative_path", "path")
            || stringValue(metadata, "workspaceRelativePath", "workspace_relative_path")
            || stringValue(explicitResourceRef, "workspaceRelativePath", "workspace_relative_path")
            || undefined,
        runId: stringValue(record, "runId", "run_id") || undefined,
        messageId: stringValue(record, "messageId", "message_id") || undefined,
        sourceKind: sourceKind || undefined,
        size: Number.isFinite(Number(record.size)) ? Number(record.size) : undefined,
        projectionRecord: { ...record, sessionId: explicitSessionId },
        folderId: stringValue(record, "folderId", "folder_id") || undefined,
        adoptedByCurrentSession: Boolean(record.adoptedByCurrentSession ?? record.adopted_by_current_session),
    };
    const mediaType = mediaTypeOf(base);
    return {
        ...base,
        mediaType,
        ...(mediaType === "psd" ? {
            previewUrl: `/api/workbench/sessions/${encodeURIComponent(sessionId)}/canvas/psd/${origin}/${encodeURIComponent(id)}/preview`,
        } : {}),
    };
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

export function normalizeSnapshot(value: unknown): CanvasSnapshot {
    const record = recordOf(value);
    const isV3 = Number(record.version) === 3;
    const rawNodes = Array.isArray(value) ? value : Array.isArray(record.nodes) ? record.nodes : [];
    const nodes = rawNodes.slice(0, MAX_NODES).flatMap((item: unknown, index: number) => {
        const node = recordOf(item);
        const resourceId = stringValue(node, "resourceId", "id");
        const storedOrigin = String(node.origin || "");
        const origin: CanvasNode["origin"] = ["source", "artifact", "workspace_asset"].includes(storedOrigin)
            ? storedOrigin as ResourceOrigin
            : "placeholder";
        const storedKind = String(node.kind);
        if (storedKind === "sink") return [];
        const kind = isV3 && ["resource", "input", "action", "result"].includes(storedKind)
            ? node.kind as CanvasNode["kind"]
            : resourceId && origin !== "placeholder" ? "resource" : "result";
        if (!isV3 && kind === "result") return [];
        const nodeId = stringValue(node, "nodeId") || (resourceId ? `${origin}:${resourceId}:${index}` : "");
        if (!nodeId || (kind === "resource" && (!resourceId || origin === "placeholder"))) return [];
        const storedWidth = Number(node.width) || NODE_WIDTH;
        const storedHeight = Number(node.height) || NODE_HEIGHT;
        const usesPreviousDefault = Math.abs(storedWidth - 248) < 1 && Math.abs(storedHeight - 184) < 1;
        return [{
            nodeId,
            kind,
            origin,
            resourceId: resourceId || undefined,
            x: Math.max(-10000, Math.min(10000, Number(node.x) || 0)),
            y: Math.max(-10000, Math.min(10000, Number(node.y) || 0)),
            width: Math.max(180, Math.min(720, usesPreviousDefault ? NODE_WIDTH : storedWidth)),
            height: Math.max(96, Math.min(720, usesPreviousDefault ? NODE_HEIGHT : storedHeight)),
            title: stringValue(node, "title") || undefined,
            mediaType: String(node.mediaType || "unknown") as CreativeCanvasMediaType,
            acceptedMediaTypes: Array.isArray(node.acceptedMediaTypes)
                ? node.acceptedMediaTypes.map(String) as CreativeCanvasMediaType[]
                : undefined,
            actionDefinitionId: stringValue(node, "actionDefinitionId") || undefined,
            prompt: typeof node.prompt === "string" ? node.prompt : undefined,
            parameters: Object.keys(recordOf(node.parameters)).length ? recordOf(node.parameters) : {},
            configurationRevision: Number(node.configurationRevision) || 1,
            producerActionNodeId: stringValue(node, "producerActionNodeId") || undefined,
            outputSlot: stringValue(node, "outputSlot") || undefined,
            operationId: stringValue(node, "operationId") || undefined,
            operationLabel: stringValue(node, "operationLabel") || undefined,
            operationState: ["reserved", "running", "waiting", "ready", "failed", "cancelled"].includes(String(node.operationState))
                ? node.operationState as CanvasNode["operationState"]
                : undefined,
            mask: sanitizeMask(node.mask),
        } satisfies CanvasNode];
    });
    const nodeIds = new Set(nodes.map((node) => node.nodeId));
    const rawEdges = !Array.isArray(value) && Array.isArray(record.edges) ? record.edges : [];
    const edges = rawEdges.slice(0, MAX_EDGES).flatMap((item: unknown, index: number) => {
        const edge = recordOf(item);
        const edgeId = stringValue(edge, "edgeId") || `canvas-edge-${index}`;
        const from = stringValue(edge, "from");
        const to = stringValue(edge, "to");
        const role: CanvasEdge["role"] = edge.role === "data" ? "data" : "relation";
        return from !== to && nodeIds.has(from) && nodeIds.has(to) ? [{
            edgeId,
            from,
            to,
            fromPort: edge.fromPort === "left" ? "left" : "right",
            toPort: edge.toPort === "right" ? "right" : "left",
            fromPortId: stringValue(edge, "fromPortId") || (role === "data" ? "output" : "relation"),
            toPortId: stringValue(edge, "toPortId") || (role === "data" ? "input" : "relation"),
            dataType: String(edge.dataType || "unknown") as CreativeCanvasMediaType,
            role,
            order: Math.max(0, Number(edge.order) || 0),
            note: typeof edge.note === "string" ? edge.note.slice(0, 2000) : "",
        } satisfies CanvasEdge] : [];
    });
    const viewport = recordOf(record.viewport);
    return {
        ...EMPTY_SNAPSHOT,
        graphId: stringValue(record, "graphId") || createId("canvas-graph"),
        nodes,
        edges,
        viewport: {
            x: Number.isFinite(Number(viewport.x)) ? Number(viewport.x) : 24,
            y: Number.isFinite(Number(viewport.y)) ? Number(viewport.y) : 24,
            scale: Math.max(0.25, Math.min(2.5, Number(viewport.scale) || 1)),
        },
    };
}

export function readSnapshot(storageKey: string): CanvasSnapshot {
    try {
        return normalizeSnapshot(JSON.parse(window.localStorage.getItem(storageKey) || "null"));
    } catch {
        return { ...EMPTY_SNAPSHOT, graphId: createId("canvas-graph") };
    }
}

export function isEditableTarget(target: EventTarget | null) {
    return target instanceof HTMLInputElement
        || target instanceof HTMLTextAreaElement
        || target instanceof HTMLSelectElement
        || (target instanceof HTMLElement && target.isContentEditable);
}

export function mediaNodeDimensions(width: number, height: number) {
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null;
    const aspect = Math.max(0.45, Math.min(3.2, width / height));
    if (aspect >= 1) {
        const previewWidth = Math.min(420, Math.max(300, 300 * Math.min(1.4, aspect)));
        return { width: previewWidth, height: previewWidth / aspect + MEDIA_FOOTER_HEIGHT };
    }
    const previewHeight = 340;
    return { width: previewHeight * aspect, height: previewHeight + MEDIA_FOOTER_HEIGHT };
}
