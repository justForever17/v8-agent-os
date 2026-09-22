export interface GalaxyNode { id: string; label: string; type: string; val?: number }
export interface GalaxyLink { relationId: string; source: string; target: string; label: string; scope: string; confidence: number; version?: string; evidenceRefs?: string[] }
export interface GalaxyCluster {
    clusterId: string; scopeKind: "global" | "workspace"; workspaceKey: string | null; label: string; writeScope?: string;
    nodes: GalaxyNode[]; links: GalaxyLink[];
    meta: { totalEntities: number; totalRelations: number; renderedEntities: number; renderedRelations: number; truncated: boolean; version: string };
}
export interface Orbit { id: string; ring: number; slot: number; capacity: number; orbitRadius: number; radius: number }
export interface Point { x: number; y: number }
export interface CameraState extends Point { zoom: number }
export interface CameraVelocity extends Point { zoom: number }
export const visualNodeId = (clusterId: string, entityId: string) => JSON.stringify([clusterId, entityId]);

/** Closed-form critical damping remains stable at 30/60 Hz and preserves
 * velocity when the user interrupts a focus with another target. Zoom moves
 * in log space, so approaching and leaving a node feel symmetric. */
export function stepCameraSpring(current: CameraState, target: CameraState, velocity: CameraVelocity, seconds: number, frequency = 13) {
    const dt = Math.max(0, Math.min(seconds, .05));
    const decay = Math.exp(-frequency * dt);
    const axis = (value: number, goal: number, speed: number) => {
        const offset = value - goal;
        const coefficient = speed + frequency * offset;
        return { value: goal + (offset + coefficient * dt) * decay, speed: (speed - frequency * coefficient * dt) * decay };
    };
    const x = axis(current.x, target.x, velocity.x), y = axis(current.y, target.y, velocity.y);
    const zoom = axis(Math.log(Math.max(.05, current.zoom)), Math.log(Math.max(.05, target.zoom)), velocity.zoom);
    const camera = { x: x.value, y: y.value, zoom: Math.exp(zoom.value) };
    const nextVelocity = { x: x.speed, y: y.speed, zoom: zoom.speed };
    const settled = Math.hypot(camera.x - target.x, camera.y - target.y) < .035
        && Math.abs(Math.log(camera.zoom / target.zoom)) < .0004
        && Math.hypot(x.speed, y.speed) < .08 && Math.abs(zoom.speed) < .001;
    return { camera: settled ? { ...target } : camera, velocity: settled ? { x: 0, y: 0, zoom: 0 } : nextVelocity, settled };
}

/** Same angular velocity per ring, fixed phase and separated annuli guarantee nonintersection. */
export function allocateOrbits(ids: string[], previous: Orbit[] = []): Orbit[] {
    const result: Orbit[] = [];
    const occupied = new Set<string>();
    for (const item of previous) if (ids.includes(item.id)) { result.push(item); occupied.add(`${item.ring}:${item.slot}`); }
    for (const id of ids.filter(id => !result.some(item => item.id === id)).sort()) {
        if (id === "global") { result.push({ id, ring: -1, slot: 0, capacity: 1, orbitRadius: 0, radius: 80 }); continue; }
        let ring = 0;
        for (;;) {
            const orbitRadius = 190 + ring * 164;
            const capacity = Math.floor(Math.PI / Math.asin(148 / (2 * orbitRadius)));
            const used = result.filter(item => item.ring === ring).map(item => item.slot);
            const free = Array.from({ length: capacity }, (_, slot) => slot).filter(slot => !occupied.has(`${ring}:${slot}`));
            // Spread a small set around the centre, while retaining every
            // existing slot when a later page or data refresh adds a cluster.
            const separation = (slot: number) => used.length ? Math.min(...used.map(other => {
                const distance = Math.abs(slot - other); return Math.min(distance, capacity - distance);
            })) : 0;
            const slot = free.sort((a, b) => separation(b) - separation(a) || a - b)[0];
            if (slot !== undefined) { result.push({ id, ring, slot, capacity, orbitRadius, radius: 66 }); occupied.add(`${ring}:${slot}`); break; }
            ring++;
        }
    }
    return result;
}
export function clusterCenter(orbit: Orbit, seconds: number): Point {
    const angle = orbit.slot * Math.PI * 2 / orbit.capacity + seconds * Math.PI * 2 / (240 + Math.max(0, orbit.ring) * 120);
    return { x: Math.cos(angle) * orbit.orbitRadius, y: Math.sin(angle) * orbit.orbitRadius };
}
export function localNode(index: number, count: number, radius: number): Point {
    const angle = index * Math.PI * (3 - Math.sqrt(5));
    const distance = Math.sqrt((index + .5) / Math.max(count, 1)) * (radius - 14);
    return { x: Math.cos(angle) * distance, y: Math.sin(angle) * distance };
}
export function transformNode(local: Point, orbit: Orbit, seconds: number): Point {
    const center = clusterCenter(orbit, seconds);
    const angle = seconds * Math.PI * 2 / (orbit.ring < 0 ? 180 : 150 + orbit.ring * 30);
    return { x: center.x + local.x * Math.cos(angle) - local.y * Math.sin(angle), y: center.y + local.x * Math.sin(angle) + local.y * Math.cos(angle) };
}
export function inverseNode(world: Point, orbit: Orbit, seconds: number): Point {
    const center = clusterCenter(orbit, seconds);
    const angle = -seconds * Math.PI * 2 / (orbit.ring < 0 ? 180 : 150 + orbit.ring * 30);
    const x = world.x - center.x, y = world.y - center.y;
    const point = { x: x * Math.cos(angle) - y * Math.sin(angle), y: x * Math.sin(angle) + y * Math.cos(angle) };
    const scale = Math.min(1, (orbit.radius - 12) / Math.max(Math.hypot(point.x, point.y), 1));
    return { x: point.x * scale, y: point.y * scale };
}
