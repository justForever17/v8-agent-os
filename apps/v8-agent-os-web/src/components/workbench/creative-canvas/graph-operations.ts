import type { CreativeCanvasMediaType } from "@/lib/creative-canvas-actions";
import {
    MAX_EDGES,
    type CanvasActionDefinition,
    type CanvasEdge,
    type CanvasGraphRuntime,
    type CanvasNode,
    type CanvasPort,
    type CanvasSnapshot,
    type CanvasViewport,
} from "./types";

export type ConnectionIssue =
    | "missing-node"
    | "same-node"
    | "duplicate"
    | "fixed-result"
    | "incompatible-type"
    | "input-full"
    | "cycle";

export type ConnectionVerdict = {
    valid: boolean;
    issue?: ConnectionIssue;
    source?: CanvasNode;
    target?: CanvasNode;
    role?: CanvasEdge["role"];
    fromPortId?: string;
    toPortId?: string;
    dataType?: CreativeCanvasMediaType;
    order?: number;
};

export type CanvasPreflightIssue = {
    severity: "error" | "warning";
    code:
        | "missing-input"
        | "missing-prompt"
        | "missing-configuration"
        | "failed"
        | "stale"
        | "unbound-input"
        | "incompatible-media"
        | "resource-unavailable"
        | "cycle"
        | "result-slot"
        | "invalid-graph"
        | "provider-unconfigured"
        | "local-runtime-unavailable"
        | "network-required"
        | "possible-cost";
    nodeId: string;
    detail?: string;
    capability?: string;
    remediation?: string;
};

export type CanvasBounds = {
    minX: number;
    minY: number;
    maxX: number;
    maxY: number;
    width: number;
    height: number;
};

export type CanvasClipboard = {
    nodes: CanvasNode[];
    edges: CanvasEdge[];
};

function createEdgeId() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return `canvas-edge-${crypto.randomUUID()}`;
    return `canvas-edge-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function canvasPortsForNode(node: CanvasNode): CanvasPort[] {
    if (node.kind === "resource" || node.kind === "input") return ["right"];
    return ["left", "right"];
}

export function canvasOutputNode(snapshot: CanvasSnapshot, node: CanvasNode | undefined): CanvasNode | null {
    if (!node) return null;
    if (node.kind !== "action") return node;
    return snapshot.nodes.find((candidate) => (
        candidate.kind === "result" && candidate.producerActionNodeId === node.nodeId
    )) || null;
}

export function canvasTargetHasAction(snapshot: CanvasSnapshot, nodeId: string, seen = new Set<string>()): boolean {
    if (seen.has(nodeId)) return false;
    seen.add(nodeId);
    const node = snapshot.nodes.find((candidate) => candidate.nodeId === nodeId);
    if (!node) return false;
    if (node.kind === "action") return true;
    if (node.kind === "result") return Boolean(
        node.producerActionNodeId
        && snapshot.nodes.some((candidate) => candidate.nodeId === node.producerActionNodeId && candidate.kind === "action"),
    );
    return false;
}

export function portPoint(node: CanvasNode, port: CanvasPort) {
    return {
        x: port === "left" ? node.x : node.x + node.width,
        y: node.y + node.height / 2,
    };
}

export function connectionPath(start: { x: number; y: number }, startPort: CanvasPort, end: { x: number; y: number }, endPort: CanvasPort) {
    const bend = Math.max(70, Math.abs(end.x - start.x) * 0.42);
    const startControl = start.x + (startPort === "right" ? bend : -bend);
    const endControl = end.x + (endPort === "right" ? bend : -bend);
    return `M ${start.x} ${start.y} C ${startControl} ${start.y}, ${endControl} ${end.y}, ${end.x} ${end.y}`;
}

export function edgePath(from: CanvasNode, to: CanvasNode, edge: Pick<CanvasEdge, "fromPort" | "toPort">) {
    return connectionPath(portPoint(from, edge.fromPort), edge.fromPort, portPoint(to, edge.toPort), edge.toPort);
}

function wouldCreateDataCycle(snapshot: CanvasSnapshot, from: string, to: string) {
    const outgoing = new Map<string, string[]>();
    for (const edge of snapshot.edges) {
        if (edge.role !== "data") continue;
        outgoing.set(edge.from, [...(outgoing.get(edge.from) || []), edge.to]);
    }
    for (const result of snapshot.nodes) {
        if (result.kind !== "result" || !result.producerActionNodeId) continue;
        outgoing.set(result.producerActionNodeId, [...(outgoing.get(result.producerActionNodeId) || []), result.nodeId]);
    }
    const queue = [to];
    const visited = new Set<string>();
    while (queue.length) {
        const nodeId = queue.shift()!;
        if (nodeId === from) return true;
        if (visited.has(nodeId)) continue;
        visited.add(nodeId);
        queue.push(...(outgoing.get(nodeId) || []));
    }
    return false;
}

export function getConnectionVerdict(
    snapshot: CanvasSnapshot,
    definitions: CanvasActionDefinition[],
    requestedFrom: string,
    requestedTo: string,
): ConnectionVerdict {
    const requestedSource = snapshot.nodes.find((node) => node.nodeId === requestedFrom);
    const source = canvasOutputNode(snapshot, requestedSource) || undefined;
    const target = snapshot.nodes.find((node) => node.nodeId === requestedTo);
    if (!source || !target) return { valid: false, issue: "missing-node", source, target };
    if (source.nodeId === target.nodeId) return { valid: false, issue: "same-node", source, target };
    if (target.kind === "result") return { valid: false, issue: "fixed-result", source, target };
    if (snapshot.edges.some((edge) => edge.from === source.nodeId && edge.to === target.nodeId && edge.fromPort === "right" && edge.toPort === "left")) {
        return { valid: false, issue: "duplicate", source, target };
    }

    const dataType = source.mediaType || "unknown";
    if (target.kind !== "action") {
        return { valid: true, source, target, role: "relation", fromPortId: "relation", toPortId: "relation", dataType, order: 0 };
    }
    if (!["resource", "result", "input"].includes(source.kind)) {
        return { valid: false, issue: "incompatible-type", source, target, dataType };
    }
    const definition = definitions.find((candidate) => candidate.actionId === target.actionDefinitionId);
    const compatiblePorts = definition?.inputs.filter((candidate) => candidate.mediaTypes.includes(dataType)) || [];
    if (!compatiblePorts.length) return { valid: false, issue: "incompatible-type", source, target, dataType };
    const port = compatiblePorts.find((candidate) => {
        const used = snapshot.edges.filter((edge) => edge.role === "data" && edge.to === target.nodeId && edge.toPortId === candidate.portId).length;
        return used < candidate.max;
    });
    if (!port) return { valid: false, issue: "input-full", source, target, dataType };
    if (wouldCreateDataCycle(snapshot, source.nodeId, target.nodeId)) return { valid: false, issue: "cycle", source, target, dataType };
    const order = snapshot.edges.filter((edge) => edge.role === "data" && edge.to === target.nodeId && edge.toPortId === port.portId).length;
    return { valid: true, source, target, role: "data", fromPortId: "output", toPortId: port.portId, dataType, order };
}

export function appendCanvasEdge(
    snapshot: CanvasSnapshot,
    definitions: CanvasActionDefinition[],
    input: { from: string; to: string; edgeId?: string; note?: string },
): CanvasSnapshot {
    if (snapshot.edges.length >= MAX_EDGES) return snapshot;
    const verdict = getConnectionVerdict(snapshot, definitions, input.from, input.to);
    if (!verdict.valid || !verdict.source || !verdict.target || !verdict.role || !verdict.fromPortId || !verdict.toPortId || !verdict.dataType) return snapshot;
    return {
        ...snapshot,
        edges: [...snapshot.edges, {
            edgeId: input.edgeId || createEdgeId(),
            from: verdict.source.nodeId,
            to: verdict.target.nodeId,
            fromPort: "right",
            toPort: "left",
            fromPortId: verdict.fromPortId,
            toPortId: verdict.toPortId,
            dataType: verdict.dataType,
            role: verdict.role,
            order: verdict.order || 0,
            note: input.note || "",
        }],
    };
}

export function getCanvasBounds(nodes: CanvasNode[]): CanvasBounds | null {
    if (!nodes.length) return null;
    const minX = Math.min(...nodes.map((node) => node.x));
    const minY = Math.min(...nodes.map((node) => node.y));
    const maxX = Math.max(...nodes.map((node) => node.x + node.width));
    const maxY = Math.max(...nodes.map((node) => node.y + node.height));
    return { minX, minY, maxX, maxY, width: maxX - minX, height: maxY - minY };
}

export function viewportForBounds(bounds: CanvasBounds | null, width: number, height: number, maximumScale = 1.35): CanvasViewport {
    if (!bounds) return { x: 24, y: 24, scale: 1 };
    const scale = Math.max(0.25, Math.min(maximumScale, Math.min((width - 96) / Math.max(1, bounds.width), (height - 96) / Math.max(1, bounds.height))));
    return {
        scale,
        x: (width - bounds.width * scale) / 2 - bounds.minX * scale,
        y: (height - bounds.height * scale) / 2 - bounds.minY * scale,
    };
}

export function getConnectedNodeIds(snapshot: CanvasSnapshot, seedIds: string[]) {
    const selected = new Set(seedIds);
    if (!seedIds.length) return selected;
    for (const edge of snapshot.edges) {
        if (selected.has(edge.from) || selected.has(edge.to)) {
            selected.add(edge.from);
            selected.add(edge.to);
        }
    }
    return selected;
}

export function getExecutionActionIds(snapshot: CanvasSnapshot, targetNodeIds: string[]) {
    if (!targetNodeIds.length) return new Set(snapshot.nodes.filter((node) => node.kind === "action").map((node) => node.nodeId));
    const actions = new Set<string>();
    const visit = (nodeId: string) => {
        const node = snapshot.nodes.find((candidate) => candidate.nodeId === nodeId);
        if (!node) return;
        const actionId = node.kind === "result" ? node.producerActionNodeId : node.kind === "action" ? node.nodeId : undefined;
        if (actionId && !actions.has(actionId)) {
            actions.add(actionId);
            for (const edge of snapshot.edges.filter((candidate) => candidate.role === "data" && candidate.to === actionId)) visit(edge.from);
        }
    };
    targetNodeIds.forEach(visit);
    return actions;
}

export function copyCanvasSubgraph(snapshot: CanvasSnapshot, seedIds: string[]): CanvasClipboard | null {
    const included = new Set(seedIds);
    for (const node of snapshot.nodes) {
        if (node.kind === "result" && node.producerActionNodeId && included.has(node.producerActionNodeId)) included.add(node.nodeId);
        if (node.kind === "action" && included.has(node.nodeId)) {
            const result = snapshot.nodes.find((candidate) => candidate.kind === "result" && candidate.producerActionNodeId === node.nodeId);
            if (result) included.add(result.nodeId);
        }
    }
    const nodes = snapshot.nodes.filter((node) => included.has(node.nodeId));
    if (!nodes.length) return null;
    return {
        nodes: nodes.map((node) => ({ ...node, parameters: { ...(node.parameters || {}) } })),
        edges: snapshot.edges.filter((edge) => included.has(edge.from) && included.has(edge.to)).map((edge) => ({ ...edge })),
    };
}

export function pasteCanvasSubgraph(
    snapshot: CanvasSnapshot,
    clipboard: CanvasClipboard,
    createId: (prefix: string) => string,
    offset = 42,
) {
    const nodeIds = new Map(clipboard.nodes.map((node) => [node.nodeId, createId(node.kind === "action" ? "canvas-action" : node.kind === "result" ? "canvas-result" : "canvas-node")]));
    const nodes = clipboard.nodes.map((node) => ({
        ...node,
        nodeId: nodeIds.get(node.nodeId)!,
        x: node.x + offset,
        y: node.y + offset,
        producerActionNodeId: node.producerActionNodeId ? nodeIds.get(node.producerActionNodeId) : undefined,
        operationId: undefined,
        operationState: undefined,
    }));
    const edges = clipboard.edges.map((edge) => ({
        ...edge,
        edgeId: createId("canvas-edge"),
        from: nodeIds.get(edge.from)!,
        to: nodeIds.get(edge.to)!,
    }));
    return {
        snapshot: { ...snapshot, nodes: [...snapshot.nodes, ...nodes], edges: [...snapshot.edges, ...edges] },
        nodeIds: nodes.map((node) => node.nodeId),
    };
}

export function isCanvasActionConfigured(node: CanvasNode, definition: CanvasActionDefinition) {
    const parameters = node.parameters || {};
    if (["frame_pick", "time_range"].includes(String(definition.parameterEditor))) {
        return Number.isInteger(parameters.frameIndex)
            || Number.isInteger(parameters.startFrameIndex)
            || Number.isInteger(parameters.startSampleIndex);
    }
    if (definition.parameterEditor === "psd_composition") return Array.isArray(parameters.layers) && parameters.layers.length > 0;
    if (definition.parameterEditor === "psd_layers") return Array.isArray(parameters.layerEdits) && parameters.layerEdits.length > 0;
    return !definition.requiresPrompt || Boolean(node.prompt?.trim());
}

export function getCanvasPreflight(snapshot: CanvasSnapshot, definitions: CanvasActionDefinition[], runtime: CanvasGraphRuntime) {
    const issues: CanvasPreflightIssue[] = [];
    for (const node of snapshot.nodes) {
        if (node.kind !== "action") continue;
        const definition = definitions.find((item) => item.actionId === node.actionDefinitionId);
        if (!definition) continue;
        for (const port of definition.inputs) {
            const count = snapshot.edges.filter((edge) => edge.role === "data" && edge.to === node.nodeId && edge.toPortId === port.portId).length;
            if (count < port.min) issues.push({ severity: "error", code: "missing-input", nodeId: node.nodeId, detail: port.portId });
        }
        if (definition.requiresPrompt && !node.prompt?.trim()) issues.push({ severity: "error", code: "missing-prompt", nodeId: node.nodeId });
        if (definition.parameterEditor && !isCanvasActionConfigured(node, definition)) {
            issues.push({ severity: "error", code: "missing-configuration", nodeId: node.nodeId });
        }
        const state = runtime.nodeStates[node.nodeId] || {};
        if (state.state === "failed") issues.push({ severity: "error", code: "failed", nodeId: node.nodeId, detail: String(state.error || "") });
        const executedRevision = Number(state.configurationRevision || 0);
        if (state.state === "succeeded" && executedRevision > 0 && executedRevision < Number(node.configurationRevision || 1)) {
            issues.push({ severity: "warning", code: "stale", nodeId: node.nodeId });
        }
    }
    return issues;
}

export function alignCanvasNodes(snapshot: CanvasSnapshot, nodeIds: string[], mode: "left" | "top" | "horizontal" | "vertical") {
    const selected = snapshot.nodes.filter((node) => nodeIds.includes(node.nodeId));
    if (selected.length < 2) return snapshot;
    const nodes = snapshot.nodes.map((node) => {
        if (!nodeIds.includes(node.nodeId)) return node;
        if (mode === "left") return { ...node, x: Math.min(...selected.map((item) => item.x)) };
        if (mode === "top") return { ...node, y: Math.min(...selected.map((item) => item.y)) };
        return node;
    });
    if (mode === "horizontal" || mode === "vertical") {
        const axis = mode === "horizontal" ? "x" : "y";
        const size = mode === "horizontal" ? "width" : "height";
        const ordered = [...selected].sort((left, right) => left[axis] - right[axis]);
        const start = ordered[0][axis];
        const end = ordered.at(-1)![axis] + ordered.at(-1)![size];
        const total = ordered.reduce((sum, item) => sum + item[size], 0);
        const gap = Math.max(24, (end - start - total) / Math.max(1, ordered.length - 1));
        let cursor = start;
        const positions = new Map<string, number>();
        for (const item of ordered) {
            positions.set(item.nodeId, cursor);
            cursor += item[size] + gap;
        }
        return { ...snapshot, nodes: nodes.map((node) => positions.has(node.nodeId) ? { ...node, [axis]: positions.get(node.nodeId)! } : node) };
    }
    return { ...snapshot, nodes };
}

export function layoutCanvasGraph(snapshot: CanvasSnapshot, nodeIds: string[] = []) {
    const included = new Set(nodeIds.length ? nodeIds : snapshot.nodes.map((node) => node.nodeId));
    const selected = snapshot.nodes.filter((node) => included.has(node.nodeId));
    if (selected.length < 2) return snapshot;
    const minimumX = Math.min(...selected.map((node) => node.x));
    const minimumY = Math.min(...selected.map((node) => node.y));
    const depth = new Map(selected.map((node) => [node.nodeId, 0]));
    const topology = [
        ...snapshot.edges.filter((edge) => edge.role === "data").map((edge) => ({ from: edge.from, to: edge.to })),
        ...selected
            .filter((node) => node.kind === "result" && node.producerActionNodeId)
            .map((node) => ({ from: node.producerActionNodeId!, to: node.nodeId })),
    ];
    for (let pass = 0; pass < selected.length; pass += 1) {
        let changed = false;
        for (const edge of topology) {
            if (!included.has(edge.from) || !included.has(edge.to)) continue;
            const nextDepth = Math.min(selected.length, (depth.get(edge.from) || 0) + 1);
            if (nextDepth > (depth.get(edge.to) || 0)) {
                depth.set(edge.to, nextDepth);
                changed = true;
            }
        }
        if (!changed) break;
    }
    const columns = new Map<number, CanvasNode[]>();
    for (const node of selected) {
        const column = depth.get(node.nodeId) || 0;
        columns.set(column, [...(columns.get(column) || []), node]);
    }
    const positions = new Map<string, { x: number; y: number }>();
    for (const [column, nodes] of [...columns.entries()].sort(([left], [right]) => left - right)) {
        nodes.sort((left, right) => left.y - right.y || left.x - right.x).forEach((node, row) => {
            positions.set(node.nodeId, { x: minimumX + column * 390, y: minimumY + row * 270 });
        });
    }
    return {
        ...snapshot,
        nodes: snapshot.nodes.map((node) => positions.has(node.nodeId) ? { ...node, ...positions.get(node.nodeId)! } : node),
    };
}
