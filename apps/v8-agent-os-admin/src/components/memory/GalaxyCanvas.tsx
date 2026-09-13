"use client";
import { useEffect, useRef } from "react";
import { allocateOrbits, clusterCenter, inverseNode, localNode, transformNode, visualNodeId, type GalaxyCluster, type GalaxyNode, type Orbit, type Point } from "./galaxy-layout";

type Camera = Point & { zoom: number };
type CameraTransition = { from: Camera; to: Camera; started: number; duration: number };
type Props = { clusters: GalaxyCluster[]; selected: string | null; paused: boolean; reduced: boolean; label: string;
    onCluster: (id: string) => void; onNode: (cluster: GalaxyCluster, node: GalaxyNode) => void; onBackground: () => boolean };

export default function GalaxyCanvas({ clusters, selected, paused, reduced, label, onCluster, onNode, onBackground }: Props) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const clock = useRef(0);
    const orbitsRef = useRef<Orbit[]>([]);
    const offsets = useRef(new Map<string, Point>());
    const camera = useRef<Camera>({ x: 0, y: 0, zoom: 1 });
    const cameraTransition = useRef<CameraTransition | null>(null);
    const viewport = useRef({ width: 0, height: 0, selected: null as string | null, manual: false, clusterIds: "" });
    const handlers = useRef({ onCluster, onNode, onBackground });
    useEffect(() => { handlers.current = { onCluster, onNode, onBackground }; }, [onCluster, onNode, onBackground]);
    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        const orbits = allocateOrbits(clusters.map(item => item.clusterId), orbitsRef.current);
        orbitsRef.current = orbits;
        const ids = new Set(clusters.flatMap(cluster => cluster.nodes.map(node => visualNodeId(cluster.clusterId, node.id))));
        for (const key of offsets.current.keys()) if (!ids.has(key)) offsets.current.delete(key);
        for (const cluster of clusters) {
            const orbit = orbits.find(item => item.id === cluster.clusterId)!;
            cluster.nodes.forEach((node, index) => { const key = visualNodeId(cluster.clusterId, node.id); if (!offsets.current.has(key)) offsets.current.set(key, localNode(index, cluster.nodes.length, orbit.radius)); });
        }
        const viewState = viewport.current;
        const firstView = viewState.width === 0;
        const selectionChanged = viewport.current.selected !== selected;
        const clusterIds = JSON.stringify(clusters.map(cluster => cluster.clusterId));
        const clustersChanged = viewport.current.clusterIds !== clusterIds;
        viewport.current.selected = selected;
        viewport.current.clusterIds = clusterIds;
        let width = viewport.current.width, height = viewport.current.height;
        let visible = true, frame = 0, last = 0, painted = 0;
        let hover: { id: string; entered: number; entry: Point; origin: Camera; zoomed: boolean } | null = null;
        let manual = viewport.current.manual;
        let drag: { start: Point; previous: Point; node?: { cluster: GalaxyCluster; node: GalaxyNode; orbit: Orbit }; moved: boolean } | null = null;
        let transition = cameraTransition.current;
        let nodePositions: { cluster: GalaxyCluster; node: GalaxyNode; orbit: Orbit; point: Point }[] = [];
        const colors = ["#8b6ee8", "#559f9a", "#c48a55", "#6995c8", "#b578a6", "#83a460"];
        const overview = (): Camera => ({ x: 0, y: 0, zoom: Math.min(width, height) / (2 * (Math.max(100, ...orbits.map(item => item.orbitRadius + item.radius)) + 24)) });
        const screen = (point: Point): Point => ({ x: (point.x - camera.current.x) * camera.current.zoom + width / 2, y: (point.y - camera.current.y) * camera.current.zoom + height / 2 });
        const world = (point: Point): Point => ({ x: (point.x - width / 2) / camera.current.zoom + camera.current.x, y: (point.y - height / 2) / camera.current.zoom + camera.current.y });
        const pointOf = (event: PointerEvent) => { const rect = canvas.getBoundingClientRect(); return { x: event.clientX - rect.left, y: event.clientY - rect.top }; };
        const centerOf = (id: string) => { const orbit = orbits.find(item => item.id === id); return orbit ? clusterCenter(orbit, clock.current) : { x: 0, y: 0 }; };
        const request = () => { if (!frame && visible && !document.hidden) frame = requestAnimationFrame(draw); };
        const moveCamera = (to: Camera, duration = 360) => { transition = reduced ? null : { from: { ...camera.current }, to, started: performance.now(), duration }; if (reduced) camera.current = to; request(); };
        const draw = (now: number) => {
            frame = 0;
            const elapsed = last ? Math.min((now - last) / 1000, .05) : 0;
            last = now;
            const moving = !paused && !reduced && !selected && !hover && !manual && !drag && !transition;
            if (moving) clock.current += elapsed;
            if (hover && !hover.zoomed && now - hover.entered >= 200 && !selected && !manual) {
                hover.zoomed = true;
                moveCamera({ ...centerOf(hover.id), zoom: hover.origin.zoom * 1.45 }, 280);
            }
            if (transition) {
                const progress = Math.min(1, (now - transition.started) / transition.duration);
                const eased = 1 - (1 - progress) ** 3;
                const arc = Math.sin(progress * Math.PI) * Math.min(22, Math.hypot(transition.to.x - transition.from.x, transition.to.y - transition.from.y) * .12);
                camera.current = { x: transition.from.x + (transition.to.x - transition.from.x) * eased, y: transition.from.y + (transition.to.y - transition.from.y) * eased + arc, zoom: transition.from.zoom + (transition.to.zoom - transition.from.zoom) * eased };
                if (progress === 1) { transition = null; last = now; }
            }
            if (now - painted >= (transition ? 1000 / 60 : 1000 / 30) || (!moving && !transition && !(hover && !hover.zoomed))) {
                painted = now;
                ctx.clearRect(0, 0, width, height);
                nodePositions = [];
                const dark = document.documentElement.classList.contains("dark");
                const labelBoxes: { x: number; y: number; w: number; h: number }[] = [];
                ctx.font = '12px system-ui, sans-serif';
                for (const [index, cluster] of clusters.entries()) {
                    const orbit = orbits.find(item => item.id === cluster.clusterId)!;
                    const center = screen(clusterCenter(orbit, clock.current));
                    const radius = orbit.radius * camera.current.zoom;
                    const color = colors[index % colors.length];
                    ctx.beginPath(); ctx.arc(center.x, center.y, radius, 0, Math.PI * 2); ctx.fillStyle = color + (selected === cluster.clusterId ? "20" : "0c"); ctx.fill(); ctx.strokeStyle = color + "50"; ctx.lineWidth = 1; ctx.stroke();
                    const positions = new Map<string, Point>();
                    cluster.nodes.forEach((node, nodeIndex) => {
                        const key = visualNodeId(cluster.clusterId, node.id);
                        const local = offsets.current.get(key) || localNode(nodeIndex, cluster.nodes.length, orbit.radius);
                        const point = screen(transformNode(local, orbit, clock.current));
                        positions.set(node.id, point); nodePositions.push({ cluster, node, orbit, point });
                    });
                    ctx.lineWidth = .7; ctx.strokeStyle = color + "50";
                    for (const link of cluster.links) { const from = positions.get(link.source), to = positions.get(link.target); if (!from || !to) continue; ctx.beginPath(); ctx.moveTo(from.x, from.y); ctx.lineTo(to.x, to.y); ctx.stroke(); }
                    for (const node of cluster.nodes) { const point = positions.get(node.id)!; ctx.beginPath(); ctx.arc(point.x, point.y, Math.max(2, Math.min(5, 3 * camera.current.zoom)), 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill(); }
                    const text = cluster.label.length > 28 ? cluster.label.slice(0, 26) + "…" : cluster.label;
                    const labelWidth = ctx.measureText(text).width + 12;
                    const box = { x: center.x - labelWidth / 2, y: center.y + radius + 8, w: labelWidth, h: 20 };
                    if (selected === cluster.clusterId || !labelBoxes.some(other => box.x < other.x + other.w && box.x + box.w > other.x && box.y < other.y + other.h && box.y + box.h > other.y)) {
                        labelBoxes.push(box); ctx.fillStyle = dark ? "#d5d8e0" : "#475162"; ctx.textAlign = "center"; ctx.fillText(text, center.x, box.y + 12);
                    }
                    if (selected === cluster.clusterId) for (const node of cluster.nodes.slice(0, 12)) { const point = positions.get(node.id)!; ctx.textAlign = "left"; ctx.fillStyle = dark ? "#d5d8e0" : "#475162"; ctx.fillText(node.label.slice(0, 24), point.x + 7, point.y + 4); }
                }
            }
            // Re-evaluate after the last camera frame; the pre-frame `moving`
            // value still referred to the transition that has just completed.
            if ((!paused && !reduced && !selected && !hover && !manual && !drag) || transition || (hover && !hover.zoomed)) request();
        };
        const resize = () => {
            const rect = canvas.getBoundingClientRect(); width = Math.max(1, rect.width); height = Math.max(1, rect.height);
            const dpr = Math.min(devicePixelRatio || 1, 2); canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            const resized = viewport.current.width !== width || viewport.current.height !== height;
            viewport.current.width = width; viewport.current.height = height;
            if (firstView || (resized && !selected && !manual && !transition)) camera.current = overview();
            request();
        };
        const hitCluster = (point: Point) => orbits.find(orbit => { const center = screen(clusterCenter(orbit, clock.current)); return Math.hypot(center.x - point.x, center.y - point.y) <= orbit.radius * camera.current.zoom + 5; });
        const pointerMove = (event: PointerEvent) => {
            const point = pointOf(event);
            if (drag) {
                if (Math.hypot(point.x - drag.start.x, point.y - drag.start.y) > 4) drag.moved = true;
                if (drag.moved) { manual = true; transition = null; hover = null;
                    if (drag.node) offsets.current.set(visualNodeId(drag.node.cluster.clusterId, drag.node.node.id), inverseNode(world(point), drag.node.orbit, clock.current));
                    else { camera.current.x -= (point.x - drag.previous.x) / camera.current.zoom; camera.current.y -= (point.y - drag.previous.y) / camera.current.zoom; }
                }
                drag.previous = point; request(); return;
            }
            if (event.pointerType !== "mouse" || selected || manual) return;
            const hit = hitCluster(point);
            if (hover) {
                // Keep the latch through camera motion; only real pointer departure releases it.
                const enteredOrbit = orbits.find(item => item.id === hover!.id)!;
                if (hit?.id === hover.id || Math.hypot(point.x - hover.entry.x, point.y - hover.entry.y) <= enteredOrbit.radius * hover.origin.zoom + 12) return;
                const origin = hover.origin; hover = null; moveCamera(origin, 280); return;
            }
            if (hit && !transition) { hover = { id: hit.id, entered: performance.now(), entry: point, origin: { ...camera.current }, zoomed: false }; request(); }
        };
        const pointerDown = (event: PointerEvent) => { canvas.focus({ preventScroll: true }); const point = pointOf(event); const node = selected ? nodePositions.find(item => item.cluster.clusterId === selected && Math.hypot(item.point.x - point.x, item.point.y - point.y) < 10) : undefined; drag = { start: point, previous: point, node, moved: false }; canvas.setPointerCapture(event.pointerId); };
        const pointerUp = (event: PointerEvent) => {
            if (!drag) return;
            const previous = drag; drag = null; if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
            if (!previous.moved) { const point = pointOf(event); if (previous.node) handlers.current.onNode(previous.node.cluster, previous.node.node); else { const hit = hitCluster(point); if (hit) handlers.current.onCluster(hit.id); else if (handlers.current.onBackground()) { manual = false; hover = null; moveCamera(overview()); } } }
            last = 0; request();
        };
        const cancelPointer = () => { drag = null; last = 0; request(); };
        const leave = () => { if (!selected && !drag && hover) { const origin = hover.origin; hover = null; moveCamera(origin, 280); } };
        const wheel = (event: WheelEvent) => { if (document.activeElement !== canvas) return; event.preventDefault(); manual = true; hover = null; transition = null; camera.current.zoom = Math.max(.15, Math.min(5, camera.current.zoom * Math.exp(-event.deltaY * .001))); request(); };
        const key = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); if (handlers.current.onBackground()) { manual = false; hover = null; moveCamera(overview()); } } else if (event.key === "+" || event.key === "-") { event.preventDefault(); manual = true; camera.current.zoom *= event.key === "+" ? 1.2 : 1 / 1.2; request(); } };
        const visibility = () => { if (!visible || document.hidden) { cancelAnimationFrame(frame); frame = 0; last = 0; } else request(); };
        const observer = new ResizeObserver(resize); observer.observe(canvas);
        const intersection = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; visibility(); }); intersection.observe(canvas);
        const themeObserver = new MutationObserver(request); themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
        document.addEventListener("visibilitychange", visibility);
        canvas.addEventListener("pointermove", pointerMove); canvas.addEventListener("pointerdown", pointerDown); canvas.addEventListener("pointerup", pointerUp); canvas.addEventListener("pointercancel", cancelPointer); canvas.addEventListener("pointerleave", leave); canvas.addEventListener("wheel", wheel, { passive: false }); canvas.addEventListener("keydown", key);
        resize();
        if (selectionChanged) {
            manual = false;
            moveCamera(selected ? { ...centerOf(selected), zoom: Math.min(width, height) / 230 } : overview());
        } else if (clustersChanged && !selected && !manual) moveCamera(overview());
        if (reduced && transition) { camera.current = transition.to; transition = null; }
        return () => { cameraTransition.current = transition; viewState.manual = manual; cancelAnimationFrame(frame); observer.disconnect(); intersection.disconnect(); themeObserver.disconnect(); document.removeEventListener("visibilitychange", visibility); canvas.removeEventListener("pointermove", pointerMove); canvas.removeEventListener("pointerdown", pointerDown); canvas.removeEventListener("pointerup", pointerUp); canvas.removeEventListener("pointercancel", cancelPointer); canvas.removeEventListener("pointerleave", leave); canvas.removeEventListener("wheel", wheel); canvas.removeEventListener("keydown", key); };
    }, [clusters, selected, paused, reduced]);
    return <canvas ref={canvasRef} tabIndex={0} role="img" aria-label={label} className="h-[clamp(340px,58dvh,640px)] w-full touch-none rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />;
}
