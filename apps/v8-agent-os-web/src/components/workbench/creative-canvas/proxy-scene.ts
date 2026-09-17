import type { CanvasEdge, CanvasResource, CanvasSnapshot } from "./types";

export type SceneVector = [number, number, number];
export type ScenePose = { leftArm: number; rightArm: number; leftLeg: number; rightLeg: number };
export type SceneMotionKey = { time: number; position: SceneVector; rotation: SceneVector; pose?: ScenePose };
export type SceneEntity = {
    entityId: string;
    name: string;
    kind: "character" | "object" | "environment";
    shape: "capsule" | "box" | "sphere";
    proxyColor: string;
    size: SceneVector;
    position: SceneVector;
    rotation: SceneVector;
    appearance: string;
    material: string;
    motion: SceneMotionKey[];
};
export type SceneCameraKey = { time: number; position: SceneVector; target: SceneVector };
export type ProxyScene = {
    schema: "v8.proxy_scene.v1";
    durationSeconds: number;
    fps: number;
    width: number;
    height: number;
    background: string;
    style: string;
    entities: SceneEntity[];
    camera: { projection: "perspective"; fov: number; keyframes: SceneCameraKey[] };
};
export const SCENE_ROLES = ["front", "left", "right", "back", "face", "detail", "material", "style", "scale"] as const;
export type SceneReference = {
    resource: CanvasResource;
    entityId: string;
    bindingKey: string;
    semanticRole: typeof SCENE_ROLES[number];
    purpose: string;
    resourceDigest: string;
};
export const SCENE_ACTION = "creative_media.render_proxy_scene_control_pack";
export const SCENE_VIDEO_ACTION = "creative_media.generate_video_from_scene";
export const EMPTY_POSE: ScenePose = { leftArm: 0, rightArm: 0, leftLeg: 0, rightLeg: 0 };

export function newSceneEntity(entityId: string, index: number): SceneEntity {
    return {
        entityId, name: "", kind: "object", shape: "box",
        proxyColor: ["#e75b43", "#4285e8", "#e7b643", "#53a978", "#a36fde", "#de69a2", "#795548", "#00838f", "#546e7a", "#9e9d24", "#6a1b9a", "#ad1457"][index % 12],
        size: [0.8, 1, 0.8], position: [index % 2 ? 1.5 : -1.5, 0.5, 0], rotation: [0, 0, 0],
        appearance: "", material: "", motion: [],
    };
}

export function createProxyScene(names: string[] = []): ProxyScene {
    return {
        schema: "v8.proxy_scene.v1", durationSeconds: 4, fps: 24, width: 640, height: 360,
        background: "#ece9e0", style: "", entities: [0, 1].map((index) => ({ ...newSceneEntity(crypto.randomUUID(), index), name: names[index] || "" })),
        camera: { projection: "perspective", fov: 45, keyframes: [
            { time: 0, position: [0, 3, 8], target: [0, 0.8, 0] },
            { time: 4, position: [2, 2.5, 7], target: [0, 0.8, 0] },
        ] },
    };
}

export function interpolateVector(a: SceneVector, b: SceneVector, amount: number): SceneVector {
    return a.map((value, index) => value + (b[index] - value) * amount) as SceneVector;
}

export function updateSceneEntity(entity: SceneEntity, patch: Partial<SceneEntity>): SceneEntity {
    // Moving the base transform moves the whole authored trajectory. Explicit
    // keyframe edits instead replace motion, leaving the other keys intact.
    const motion = patch.motion || entity.motion.map((key) => ({
        ...key,
        position: patch.position ? key.position.map((value, i) => value + patch.position![i] - entity.position[i]) as SceneVector : key.position,
        rotation: patch.rotation ? key.rotation.map((value, i) => value + patch.rotation![i] - entity.rotation[i]) as SceneVector : key.rotation,
    }));
    return { ...entity, ...patch, motion };
}

export function keyInterval<T extends { time: number }>(keys: T[], time: number): [T, T, number] | null {
    const ordered = [...keys].sort((a, b) => a.time - b.time);
    if (!ordered.length) return null;
    if (time <= ordered[0].time) return [ordered[0], ordered[0], 0];
    const right = ordered.findIndex((key) => key.time >= time);
    if (right < 0) return [ordered[ordered.length - 1], ordered[ordered.length - 1], 0];
    const a = ordered[Math.max(0, right - 1)];
    const b = ordered[right];
    return [a, b, b.time === a.time ? 0 : (time - a.time) / (b.time - a.time)];
}

export function sampleEntity(entity: SceneEntity, time: number) {
    const interval = keyInterval(entity.motion, time);
    if (!interval) return { position: entity.position, rotation: entity.rotation, pose: EMPTY_POSE };
    const [a, b, t] = interval;
    const pose = Object.fromEntries(Object.keys(EMPTY_POSE).map((name) => {
        const key = name as keyof ScenePose;
        return [key, (a.pose?.[key] || 0) + ((b.pose?.[key] || 0) - (a.pose?.[key] || 0)) * t];
    })) as ScenePose;
    return { position: interpolateVector(a.position, b.position, t), rotation: interpolateVector(a.rotation, b.rotation, t), pose };
}

/** One transaction owns both scene edits and all reference edges. No execution here. */
export function saveProxyScene(snapshot: CanvasSnapshot, nodeId: string, scene: ProxyScene, references: SceneReference[], makeId: () => string): CanvasSnapshot {
    const action = snapshot.nodes.find((node) => node.nodeId === nodeId && node.actionDefinitionId === SCENE_ACTION);
    if (!action) return snapshot;
    const nodes = snapshot.nodes.map((node) => node.nodeId === nodeId ? {
        ...node, parameters: { ...node.parameters, scene }, configurationRevision: (node.configurationRevision || 1) + 1,
    } : node);
    const edges: CanvasEdge[] = snapshot.edges.filter((edge) => !(edge.to === nodeId && edge.role === "data"));
    references.forEach((reference, index) => {
        const resource = reference.resource;
        let input = nodes.find((node) => node.kind === "resource" && node.origin === resource.origin && node.resourceId === resource.id);
        if (!input) {
            input = { nodeId: makeId(), kind: "resource", origin: resource.origin, resourceId: resource.id,
                title: resource.name, mediaType: "image", x: action.x - 350, y: action.y + index * 235, width: 280, height: 190 };
            nodes.push(input);
        }
        edges.push({ edgeId: makeId(), from: input.nodeId, to: nodeId, fromPort: "right", toPort: "left",
            fromPortId: "output", toPortId: "references", dataType: "image", role: "data", order: index, note: "",
            entityId: reference.entityId, bindingKey: reference.bindingKey, semanticRole: reference.semanticRole,
            purpose: reference.purpose, resourceDigest: reference.resourceDigest });
    });
    return { ...snapshot, nodes, edges };
}

export function sceneReferences(snapshot: CanvasSnapshot, nodeId: string, resolve: (nodeId: string) => CanvasResource | null): SceneReference[] {
    return snapshot.edges.filter((edge) => edge.to === nodeId && edge.role === "data").map((edge) => {
        const resource = resolve(edge.from);
        return { resource: resource || { id: edge.from, origin: "source" as const, name: "", mimeType: "image/png", availability: "unavailable" as const },
            entityId: edge.entityId || "", bindingKey: edge.bindingKey || edge.edgeId,
            semanticRole: (edge.semanticRole || "front") as SceneReference["semanticRole"],
            purpose: edge.purpose || "", resourceDigest: edge.resourceDigest || "" };
    });
}

export async function sceneReferenceDigest(resource: CanvasResource, signal: AbortSignal): Promise<string> {
    const projection = resource.projectionRecord || {};
    const rawUrl = String(projection.contentUrl || projection.content_url || projection.downloadUrl || projection.download_url || resource.url || "");
    const url = rawUrl.replace(/^\/api\/client\/workspace\/resource/, "/api/workspace/resource");
    if (resource.availability === "unavailable" || !url) throw new Error("reference_unavailable");
    // Preview endpoints may resize/transcode; bind the original governed bytes.
    const response = await fetch(url, { signal, cache: "no-store" });
    if (!response.ok) throw new Error("reference_unavailable");
    const bytes = await response.arrayBuffer();
    if (!bytes.byteLength) throw new Error("reference_unavailable");
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    if (signal.aborted) throw new DOMException("Aborted", "AbortError");
    return [...new Uint8Array(digest)].map((value) => value.toString(16).padStart(2, "0")).join("");
}

/** A library projection and its original source are the same selectable image. */
export function sceneImageChoices(resources: CanvasResource[], sessionId: string): CanvasResource[] {
    const candidates = resources.filter((resource) => resource.sessionId === sessionId && resource.mediaType === "image" && resource.availability !== "unavailable" && resource.adoptedByCurrentSession !== false);
    candidates.sort((a, b) => Number(a.origin === "workspace_asset") - Number(b.origin === "workspace_asset"));
    const choices = new Map<string, CanvasResource>();
    for (const resource of candidates) {
        const projection = resource.projectionRecord;
        const key = resource.origin === "workspace_asset" && projection?.originId && projection?.originKind
            ? `${projection.originKind}:${projection.originId}` : `${resource.origin}:${resource.id}`;
        if (!choices.has(key)) choices.set(key, resource);
    }
    return [...choices.values()];
}
