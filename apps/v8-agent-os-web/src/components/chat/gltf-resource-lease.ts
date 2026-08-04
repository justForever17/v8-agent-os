import type { Object3D } from "three";

type GltfScene = Object3D;

type LeaseEntry = {
    refs: number;
    scene: GltfScene;
    clear: () => void;
    releaseTimer: ReturnType<typeof setTimeout> | null;
    releasedAt: number;
};

const MAX_IDLE_GLTF_RESOURCES = 8;
const GLTF_RESOURCE_RELEASE_DELAY_MS = 15_000;
const leases = new Map<string, LeaseEntry>();

function disposeOnce(value: unknown, disposed: Set<unknown>) {
    if (!value || (typeof value !== "object" && typeof value !== "function") || disposed.has(value)) return;
    disposed.add(value);
    const candidate = value as { dispose?: () => void };
    candidate.dispose?.();
}

function disposeSceneResources(scene: GltfScene) {
    const disposed = new Set<unknown>();
    scene.traverse((object) => {
        const renderable = object as Object3D & { geometry?: unknown; material?: unknown };
        disposeOnce(renderable.geometry, disposed);
        const materials = Array.isArray(renderable.material) ? renderable.material : [renderable.material];
        for (const material of materials) {
            if (!material || typeof material !== "object") continue;
            for (const value of Object.values(material)) {
                if (value && typeof value === "object" && "isTexture" in value) disposeOnce(value, disposed);
            }
            disposeOnce(material, disposed);
        }
    });
}

function releaseEntry(url: string, entry: LeaseEntry) {
    if (entry.refs > 0 || leases.get(url) !== entry) return;
    if (entry.releaseTimer) clearTimeout(entry.releaseTimer);
    entry.releaseTimer = null;
    leases.delete(url);
    entry.clear();
    disposeSceneResources(entry.scene);
}

function enforceIdleLimit() {
    const idle = [...leases.entries()]
        .filter(([, entry]) => entry.refs === 0)
        .sort((left, right) => left[1].releasedAt - right[1].releasedAt);
    while (idle.length > MAX_IDLE_GLTF_RESOURCES) {
        const [url, entry] = idle.shift()!;
        releaseEntry(url, entry);
    }
}

export function acquireGltfResourceLease({
    url,
    scene,
    clear,
    releaseDelayMs = GLTF_RESOURCE_RELEASE_DELAY_MS,
}: {
    url: string;
    scene: GltfScene;
    clear: () => void;
    releaseDelayMs?: number;
}) {
    let entry = leases.get(url);
    if (!entry) {
        entry = { refs: 0, scene, clear, releaseTimer: null, releasedAt: 0 };
        leases.set(url, entry);
    } else {
        entry.scene = scene;
        entry.clear = clear;
    }
    if (entry.releaseTimer) clearTimeout(entry.releaseTimer);
    entry.releaseTimer = null;
    entry.refs += 1;
    let released = false;
    return () => {
        if (released) return;
        released = true;
        entry!.refs = Math.max(0, entry!.refs - 1);
        if (entry!.refs > 0) return;
        entry!.releasedAt = Date.now();
        entry!.releaseTimer = setTimeout(() => releaseEntry(url, entry!), Math.max(0, releaseDelayMs));
        enforceIdleLimit();
    };
}

export function flushIdleGltfResourceLeases() {
    for (const [url, entry] of [...leases.entries()]) {
        if (entry.refs === 0) releaseEntry(url, entry);
    }
}

export function getGltfResourceLeaseStats() {
    const entries = [...leases.values()];
    return {
        active: entries.filter((entry) => entry.refs > 0).length,
        idle: entries.filter((entry) => entry.refs === 0).length,
        total: entries.length,
    };
}
