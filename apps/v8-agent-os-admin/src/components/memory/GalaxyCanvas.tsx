"use client";
import { useEffect, useRef } from "react";
import { allocateOrbits, clusterCenter, inverseNode, localNode, stepCameraSpring, transformNode, visualNodeId, type CameraState, type CameraVelocity, type GalaxyCluster, type GalaxyNode, type Orbit, type Point } from "./galaxy-layout";

type Camera = CameraState;
type CameraTransition = { to: Camera; frequency: number };
type Props = { clusters: GalaxyCluster[]; selected: string | null; selectedNode?: string | null; paused: boolean; reduced: boolean; label: string;
    onCluster: (id: string) => void; onNode: (cluster: GalaxyCluster, node: GalaxyNode) => void; onBackground: () => boolean };

export default function GalaxyCanvas({ clusters, selected, selectedNode = null, paused, reduced, label, onCluster, onNode, onBackground }: Props) {
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const clock = useRef(0);
    const orbitsRef = useRef<Orbit[]>([]);
    const offsets = useRef(new Map<string, Point>());
    const camera = useRef<Camera>({ x: 0, y: 0, zoom: 1 });
    const cameraTransition = useRef<CameraTransition | null>(null);
    const cameraVelocity = useRef<CameraVelocity>({ x: 0, y: 0, zoom: 0 });
    const viewport = useRef({ width: 0, height: 0, selected: null as string | null, selectedNode: null as string | null, manual: false, clusterIds: "" });
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
        const nodeSelectionChanged = viewport.current.selectedNode !== selectedNode;
        const clusterIds = JSON.stringify(clusters.map(cluster => cluster.clusterId));
        const clustersChanged = viewport.current.clusterIds !== clusterIds;
        viewport.current.selected = selected;
        viewport.current.selectedNode = selectedNode;
        viewport.current.clusterIds = clusterIds;
        let width = viewport.current.width, height = viewport.current.height;
        let visible = true, frame = 0, last = 0, painted = 0;
        let hover: { id: string; entered: number; entry: Point; origin: Camera; zoomed: boolean } | null = null;
        let manual = viewport.current.manual;
        let drag: { start: Point; previous: Point; node?: { cluster: GalaxyCluster; node: GalaxyNode; orbit: Orbit }; moved: boolean } | null = null;
        let transition = cameraTransition.current;
        let nodePositions: { cluster: GalaxyCluster; node: GalaxyNode; orbit: Orbit; point: Point }[] = [];
        let hoveredNode: string | null = null;
        const colors = ["#818cf8", "#4fb6a8", "#d5a06a", "#67a9db", "#b78cca", "#92b67a"];
        const sprites = new Map<string, HTMLCanvasElement>();
        const textWidths = new Map<string, number>();
        const nodeSprite = (color: string, dark: boolean) => {
            const key = `${color}:${dark}`;
            if (sprites.has(key)) return sprites.get(key)!;
            const sprite = document.createElement("canvas"); sprite.width = sprite.height = 64;
            const painter = sprite.getContext("2d")!;
            const glow = painter.createRadialGradient(32, 32, 2, 32, 32, 30);
            glow.addColorStop(0, color + "9c"); glow.addColorStop(.28, color + "36"); glow.addColorStop(1, color + "00");
            painter.fillStyle = glow; painter.fillRect(0, 0, 64, 64);
            painter.beginPath(); painter.arc(32, 32, 9, 0, Math.PI * 2); painter.fillStyle = color; painter.fill();
            painter.beginPath(); painter.arc(30.5, 30.5, 3.5, 0, Math.PI * 2); painter.fillStyle = dark ? "#eef2ff" : "#ffffff"; painter.fill();
            sprites.set(key, sprite); return sprite;
        };
        const overview = (): Camera => ({ x: 0, y: 0, zoom: Math.min(width, height) / (2 * (Math.max(100, ...orbits.map(item => item.orbitRadius + item.radius)) + 24)) });
        const screen = (point: Point): Point => ({ x: (point.x - camera.current.x) * camera.current.zoom + width / 2, y: (point.y - camera.current.y) * camera.current.zoom + height / 2 });
        const world = (point: Point): Point => ({ x: (point.x - width / 2) / camera.current.zoom + camera.current.x, y: (point.y - height / 2) / camera.current.zoom + camera.current.y });
        const pointOf = (event: PointerEvent) => { const rect = canvas.getBoundingClientRect(); return { x: event.clientX - rect.left, y: event.clientY - rect.top }; };
        const centerOf = (id: string) => { const orbit = orbits.find(item => item.id === id); return orbit ? clusterCenter(orbit, clock.current) : { x: 0, y: 0 }; };
        const request = () => { if (!frame && visible && !document.hidden) frame = requestAnimationFrame(draw); };
        const moveCamera = (to: Camera, duration = 480) => {
            const target = { ...to, zoom: Math.max(.15, Math.min(5, to.zoom)) };
            if (!transition && !reduced) {
                const dx = target.x - camera.current.x, dy = target.y - camera.current.y;
                const distance = Math.hypot(dx, dy), impulse = Math.min(150, distance * .7);
                // A small perpendicular impulse creates a restrained arc. A
                // retarget keeps its current velocity instead of restarting.
                cameraVelocity.current = { x: distance ? -dy / distance * impulse : 0, y: distance ? dx / distance * impulse : 0, zoom: 0 };
            }
            transition = reduced ? null : { to: target, frequency: 6.2 / (duration / 1000) };
            if (reduced) { camera.current = target; cameraVelocity.current = { x: 0, y: 0, zoom: 0 }; }
            request();
        };
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
                const step = stepCameraSpring(camera.current, transition.to, cameraVelocity.current, elapsed || 1 / 60, transition.frequency);
                camera.current = step.camera; cameraVelocity.current = step.velocity;
                if (step.settled) { transition = null; last = now; }
            }
            if (now - painted >= (transition ? 1000 / 60 : 1000 / 30) || (!moving && !transition && !(hover && !hover.zoomed))) {
                painted = now;
                ctx.clearRect(0, 0, width, height);
                nodePositions = [];
                const dark = document.documentElement.classList.contains("dark");
                const labelBoxes: { x: number; y: number; w: number; h: number }[] = [];
                ctx.font = '12px system-ui, sans-serif';
                const ambient = ctx.createRadialGradient(width / 2, height / 2, 0, width / 2, height / 2, Math.max(width, height) * .7);
                ambient.addColorStop(0, dark ? "#171b321f" : "#f2f3ff80"); ambient.addColorStop(1, dark ? "#10121d00" : "#ffffff00");
                ctx.fillStyle = ambient; ctx.fillRect(0, 0, width, height);
                const origin = screen({ x: 0, y: 0 });
                ctx.strokeStyle = dark ? "#a5b4fc0b" : "#7c87b512"; ctx.lineWidth = .7;
                for (const radius of new Set(orbits.filter(item => item.orbitRadius).map(item => item.orbitRadius))) {
                    ctx.beginPath(); ctx.arc(origin.x, origin.y, radius * camera.current.zoom, 0, Math.PI * 2); ctx.stroke();
                }
                for (const [index, cluster] of clusters.entries()) {
                    const orbit = orbits.find(item => item.id === cluster.clusterId)!;
                    const center = screen(clusterCenter(orbit, clock.current));
                    const radius = orbit.radius * camera.current.zoom;
                    const color = colors[index % colors.length];
                    const active = selected === cluster.clusterId || hover?.id === cluster.clusterId;
                    const halo = ctx.createRadialGradient(center.x, center.y, radius * .05, center.x, center.y, radius * 1.1);
                    halo.addColorStop(0, color + (active ? "20" : "0d")); halo.addColorStop(.65, color + (active ? "0d" : "07")); halo.addColorStop(1, color + "00");
                    ctx.fillStyle = halo; ctx.fillRect(center.x - radius * 1.1, center.y - radius * 1.1, radius * 2.2, radius * 2.2);
                    if (active) { ctx.beginPath(); ctx.arc(center.x, center.y, radius + 3, 0, Math.PI * 2); ctx.strokeStyle = color + "38"; ctx.lineWidth = .8; ctx.stroke(); }
                    const positions = new Map<string, Point>();
                    cluster.nodes.forEach((node, nodeIndex) => {
                        const key = visualNodeId(cluster.clusterId, node.id);
                        const local = offsets.current.get(key) || localNode(nodeIndex, cluster.nodes.length, orbit.radius);
                        const point = screen(transformNode(local, orbit, clock.current));
                        positions.set(node.id, point); nodePositions.push({ cluster, node, orbit, point });
                    });
                    for (const link of cluster.links) {
                        const from = positions.get(link.source), to = positions.get(link.target); if (!from || !to) continue;
                        const emphasized = selected === cluster.clusterId && (link.source === selectedNode || link.target === selectedNode);
                        ctx.lineWidth = emphasized ? 1.2 : .65; ctx.strokeStyle = color + (emphasized ? "aa" : active ? "60" : "36");
                        const bend = Math.min(12, Math.hypot(to.x - from.x, to.y - from.y) * .07);
                        ctx.beginPath(); ctx.moveTo(from.x, from.y); ctx.quadraticCurveTo((from.x + to.x) / 2, (from.y + to.y) / 2 - bend, to.x, to.y); ctx.stroke();
                    }
                    for (const node of cluster.nodes) {
                        const point = positions.get(node.id)!;
                        const focused = selected === cluster.clusterId && selectedNode === node.id;
                        const hovered = hoveredNode === visualNodeId(cluster.clusterId, node.id);
                        const size = Math.max(3.5, Math.min(7.2, (3 + Math.sqrt(Math.max(1, node.val || 1)) * .35) * Math.sqrt(camera.current.zoom)));
                        const glowSize = size * (focused || hovered ? 6.3 : 4.8);
                        ctx.drawImage(nodeSprite(color, dark), point.x - glowSize / 2, point.y - glowSize / 2, glowSize, glowSize);
                        if (focused || hovered) { ctx.beginPath(); ctx.arc(point.x, point.y, size + 3, 0, Math.PI * 2); ctx.strokeStyle = color + "b8"; ctx.lineWidth = 1; ctx.stroke(); }
                    }
                    const text = cluster.label.length > 28 ? cluster.label.slice(0, 26) + "…" : cluster.label;
                    if (!textWidths.has(text)) textWidths.set(text, ctx.measureText(text).width);
                    const labelWidth = textWidths.get(text)! + 18;
                    const box = { x: center.x - labelWidth / 2, y: center.y + radius + 8, w: labelWidth, h: 20 };
                    if (selected === cluster.clusterId || !labelBoxes.some(other => box.x < other.x + other.w && box.x + box.w > other.x && box.y < other.y + other.h && box.y + box.h > other.y)) {
                        labelBoxes.push(box); ctx.fillStyle = dark ? "#171c2bc7" : "#ffffffcc"; ctx.beginPath(); ctx.roundRect(box.x, box.y - 2, box.w, box.h, 10); ctx.fill();
                        ctx.fillStyle = dark ? "#d5d8e0" : "#475162"; ctx.textAlign = "center"; ctx.fillText(text, center.x, box.y + 12);
                    }
                    const priorityLabels = cluster.nodes.filter(node => node.id === selectedNode || visualNodeId(cluster.clusterId, node.id) === hoveredNode);
                    const labelIds = new Set<string>();
                    const labelNodes = [...priorityLabels, ...cluster.nodes].filter(node => { if (labelIds.has(node.id)) return false; labelIds.add(node.id); return true; }).slice(0, 12);
                    if (selected === cluster.clusterId) for (const node of labelNodes) {
                        const point = positions.get(node.id)!; const nodeText = node.label.slice(0, 24);
                        const key = `node:${nodeText}`; if (!textWidths.has(key)) textWidths.set(key, ctx.measureText(nodeText).width);
                        const nodeBox = { x: point.x + 10, y: point.y - 11, w: textWidths.get(key)! + 10, h: 19 };
                        if (node.id !== selectedNode && labelBoxes.some(other => nodeBox.x < other.x + other.w && nodeBox.x + nodeBox.w > other.x && nodeBox.y < other.y + other.h && nodeBox.y + nodeBox.h > other.y)) continue;
                        labelBoxes.push(nodeBox); ctx.fillStyle = dark ? "#171c2bd9" : "#ffffffdd"; ctx.beginPath(); ctx.roundRect(nodeBox.x, nodeBox.y, nodeBox.w, nodeBox.h, 6); ctx.fill();
                        ctx.textAlign = "left"; ctx.fillStyle = dark ? "#e0e6f4" : "#394358"; ctx.fillText(nodeText, nodeBox.x + 5, nodeBox.y + 13);
                    }
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
            if (event.pointerType !== "mouse") return;
            if (selected) {
                const hitNode = nodePositions.find(item => item.cluster.clusterId === selected && Math.hypot(item.point.x - point.x, item.point.y - point.y) < 11);
                const nextHover = hitNode ? visualNodeId(hitNode.cluster.clusterId, hitNode.node.id) : null;
                if (nextHover !== hoveredNode) { hoveredNode = nextHover; request(); }
                canvas.style.cursor = hitNode ? "pointer" : "grab";
                return;
            }
            if (manual) return;
            const hit = hitCluster(point);
            canvas.style.cursor = hit ? "pointer" : "grab";
            if (hover) {
                // Keep the latch through camera motion; only real pointer departure releases it.
                const enteredOrbit = orbits.find(item => item.id === hover!.id)!;
                if (hit?.id === hover.id || Math.hypot(point.x - hover.entry.x, point.y - hover.entry.y) <= enteredOrbit.radius * hover.origin.zoom + 12) return;
                const origin = hover.origin; hover = null; moveCamera(origin, 280); return;
            }
            if (hit) { hover = { id: hit.id, entered: performance.now(), entry: point, origin: { ...(transition?.to || camera.current) }, zoomed: false }; request(); }
        };
        const pointerDown = (event: PointerEvent) => { canvas.focus({ preventScroll: true }); const point = pointOf(event); const node = selected ? nodePositions.find(item => item.cluster.clusterId === selected && Math.hypot(item.point.x - point.x, item.point.y - point.y) < 10) : undefined; drag = { start: point, previous: point, node, moved: false }; canvas.setPointerCapture(event.pointerId); };
        const pointerUp = (event: PointerEvent) => {
            if (!drag) return;
            const previous = drag; drag = null; if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
            if (!previous.moved) { const point = pointOf(event); if (previous.node) handlers.current.onNode(previous.node.cluster, previous.node.node); else { const hit = hitCluster(point); if (hit) handlers.current.onCluster(hit.id); else if (handlers.current.onBackground()) { manual = false; hover = null; moveCamera(overview()); } } }
            last = 0; request();
        };
        const cancelPointer = () => { drag = null; last = 0; request(); };
        const leave = () => { hoveredNode = null; if (!selected && !drag && hover) { const origin = hover.origin; hover = null; moveCamera(origin, 380); } else request(); };
        const wheel = (event: WheelEvent) => { if (document.activeElement !== canvas) return; event.preventDefault(); manual = true; hover = null; const target = transition?.to || camera.current; moveCamera({ ...target, zoom: target.zoom * Math.exp(-event.deltaY * .001) }, 210); };
        const key = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); if (handlers.current.onBackground()) { manual = false; hover = null; moveCamera(overview()); } } else if (event.key === "+" || event.key === "-") { event.preventDefault(); manual = true; const target = transition?.to || camera.current; moveCamera({ ...target, zoom: target.zoom * (event.key === "+" ? 1.2 : 1 / 1.2) }, 240); } };
        const visibility = () => { if (!visible || document.hidden) { cancelAnimationFrame(frame); frame = 0; last = 0; } else request(); };
        const observer = new ResizeObserver(resize); observer.observe(canvas);
        const intersection = new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; visibility(); }); intersection.observe(canvas);
        const themeObserver = new MutationObserver(request); themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
        document.addEventListener("visibilitychange", visibility);
        canvas.addEventListener("pointermove", pointerMove); canvas.addEventListener("pointerdown", pointerDown); canvas.addEventListener("pointerup", pointerUp); canvas.addEventListener("pointercancel", cancelPointer); canvas.addEventListener("pointerleave", leave); canvas.addEventListener("wheel", wheel, { passive: false }); canvas.addEventListener("keydown", key);
        resize();
        if (selected && selectedNode && (selectionChanged || nodeSelectionChanged)) {
            const orbit = orbits.find(item => item.id === selected);
            const local = offsets.current.get(visualNodeId(selected, selectedNode));
            if (orbit && local) { manual = false; moveCamera({ ...transformNode(local, orbit, clock.current), zoom: Math.min(width, height) / 175 }, 500); }
        } else if (selectionChanged || nodeSelectionChanged) {
            manual = false;
            moveCamera(selected ? { ...centerOf(selected), zoom: Math.min(width, height) / 230 } : overview());
        } else if (clustersChanged && !selected && !manual) moveCamera(overview());
        if (reduced && transition) { camera.current = transition.to; transition = null; }
        return () => { cameraTransition.current = transition; viewState.manual = manual; cancelAnimationFrame(frame); observer.disconnect(); intersection.disconnect(); themeObserver.disconnect(); document.removeEventListener("visibilitychange", visibility); canvas.removeEventListener("pointermove", pointerMove); canvas.removeEventListener("pointerdown", pointerDown); canvas.removeEventListener("pointerup", pointerUp); canvas.removeEventListener("pointercancel", cancelPointer); canvas.removeEventListener("pointerleave", leave); canvas.removeEventListener("wheel", wheel); canvas.removeEventListener("keydown", key); };
    }, [clusters, selected, selectedNode, paused, reduced]);
    return <canvas ref={canvasRef} tabIndex={0} role="img" aria-label={label} className="h-[clamp(340px,58dvh,640px)] w-full touch-none rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" />;
}
