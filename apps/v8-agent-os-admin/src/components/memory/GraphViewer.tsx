"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { forceCollide, forceX, forceY } from "d3-force-3d";
import { Link2, Loader2, Plus, Search, Sparkles, Trash2, Unlink2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/use-toast";
import { useT } from "@/components/providers/LocaleProvider";
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
  loading: () => <div className="flex h-[620px] items-center justify-center rounded-3xl border border-border/60 bg-muted/10">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
});
interface GraphNode {
  id: string;
  label: string;
  type: string;
  color: string;
  val: number;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
}
interface GraphLink {
  source: string | GraphNode;
  target: string | GraphNode;
  label: string;
  confidence: number;
}
interface GraphData {
  nodes: GraphNode[];
  links: GraphLink[];
}
interface GraphRelation {
  direction: "out" | "in";
  subject: string;
  predicate: string;
  object: string;
  confidence: number;
}
interface GraphViewerProps {
  filterNode?: string;
}
interface CanvasSize {
  width: number;
  height: number;
}
const DEFAULT_SIZE: CanvasSize = { width: 1200, height: 620 };
function hashNodeId(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = hash * 31 + value.charCodeAt(index) >>> 0;
  }
  return hash;
}
function seedGraphData(graph: GraphData): GraphData {
  const nodes = [...(graph.nodes || [])].map((node) => ({ ...node }));
  const links = [...(graph.links || [])].map((link) => ({ ...link }));
  const orderedIds = [...nodes.map((node) => node.id)].sort((left, right) => left.localeCompare(right));
  const indexById = new Map(orderedIds.map((id, index) => [id, index]));
  nodes.forEach((node) => {
    if (Number.isFinite(node.x) && Number.isFinite(node.y)) {
      node.vx = 0;
      node.vy = 0;
      return;
    }
    const stableIndex = indexById.get(node.id) ?? 0;
    const hash = hashNodeId(node.id);
    const ring = Math.floor(stableIndex / 10);
    const positionInRing = stableIndex % 10;
    const ringSize = Math.max(1, Math.min(10, orderedIds.length - ring * 10));
    const angle = positionInRing / ringSize * Math.PI * 2 + (hash % 37 / 37 - 0.5) * 0.16;
    const radius = 56 + ring * 56 + hash % 13;
    node.x = Math.cos(angle) * radius;
    node.y = Math.sin(angle) * radius;
    node.vx = 0;
    node.vy = 0;
  });
  return { nodes, links };
}
export default function GraphViewer({ filterNode = "" }: GraphViewerProps) {
  const { toast } = useToast();
  const t = useT();
  const [data, setData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [relations, setRelations] = useState<GraphRelation[]>([]);
  const [menuPosition, setMenuPosition] = useState<{
    x: number;
    y: number;
  } | null>(null);
  const [graphSize, setGraphSize] = useState<CanvasSize>(DEFAULT_SIZE);
  const [connectTarget, setConnectTarget] = useState("");
  const [connectPredicate, setConnectPredicate] = useState("RELATED_TO");
  const [menuMode, setMenuMode] = useState<"root" | "connect" | "disconnect">("root");
  const [mutating, setMutating] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const labelBoxesRef = useRef<Array<{
    x: number;
    y: number;
    w: number;
    h: number;
  }>>([]);
  const guidedFilterRef = useRef("");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const fgRef = useRef<any>(null);
  const normalizedFilter = filterNode.trim().toLowerCase();
  const selectedNode = useMemo(() => data?.nodes.find((node) => node.id === selectedNodeId) || null, [data?.nodes, selectedNodeId]);
  const firstRenderableNodeId = useMemo(() => data?.nodes?.[0]?.id ?? null, [data?.nodes]);
  const selectedNeighborhood = useMemo(() => {
    if (!selectedNodeId || !data?.links?.length) {
      return new Set<string>();
    }
    const related = new Set<string>([selectedNodeId]);
    data.links.forEach((link) => {
      const sourceId = typeof link.source === "string" ? link.source : link.source.id;
      const targetId = typeof link.target === "string" ? link.target : link.target.id;
      if (sourceId === selectedNodeId) {
        related.add(targetId);
      }
      if (targetId === selectedNodeId) {
        related.add(sourceId);
      }
    });
    return related;
  }, [data?.links, selectedNodeId]);
  const hoveredNeighborhood = useMemo(() => {
    if (!hoveredNodeId || !data?.links?.length) {
      return new Set<string>();
    }
    const related = new Set<string>([hoveredNodeId]);
    data.links.forEach((link) => {
      const sourceId = typeof link.source === "string" ? link.source : link.source.id;
      const targetId = typeof link.target === "string" ? link.target : link.target.id;
      if (sourceId === hoveredNodeId) {
        related.add(targetId);
      }
      if (targetId === hoveredNodeId) {
        related.add(sourceId);
      }
    });
    return related;
  }, [data?.links, hoveredNodeId]);
  const matchedNodeIds = useMemo(() => {
    if (!normalizedFilter) {
      return new Set<string>();
    }
    return new Set((data?.nodes || []).
    filter((node) => node.id.toLowerCase().includes(normalizedFilter) || node.label.toLowerCase().includes(normalizedFilter)).
    map((node) => node.id));
  }, [data?.nodes, normalizedFilter]);
  const nodeDegrees = useMemo(() => {
    const degrees = new Map<string, number>();
    (data?.nodes || []).forEach((node) => degrees.set(node.id, 0));
    (data?.links || []).forEach((link) => {
      const sourceId = typeof link.source === "string" ? link.source : link.source.id;
      const targetId = typeof link.target === "string" ? link.target : link.target.id;
      degrees.set(sourceId, (degrees.get(sourceId) || 0) + 1);
      degrees.set(targetId, (degrees.get(targetId) || 0) + 1);
    });
    return degrees;
  }, [data?.links, data?.nodes]);
  const getNodeScale = useCallback((nodeId: string) => {
    const isSelected = nodeId === selectedNodeId;
    const isHovered = nodeId === hoveredNodeId;
    const isMatched = matchedNodeIds.has(nodeId);
    const isSelectedNeighbor = selectedNeighborhood.has(nodeId);
    if (isSelected)
    return 1.28;
    if (isHovered)
    return 1.14;
    if (isMatched)
    return 1.08;
    if (isSelectedNeighbor)
    return 1.02;
    return 0.94;
  }, [hoveredNodeId, matchedNodeIds, selectedNeighborhood, selectedNodeId]);
  const getLabelOpacity = useCallback((nodeId: string) => {
    const isSelected = nodeId === selectedNodeId;
    const isHovered = nodeId === hoveredNodeId;
    const isMatched = matchedNodeIds.has(nodeId);
    const isRelated = selectedNeighborhood.has(nodeId) || hoveredNeighborhood.has(nodeId);
    const hasSearchFocus = matchedNodeIds.size > 0;
    if (isSelected)
    return 0.98;
    if (isHovered)
    return 0.9;
    if (isMatched)
    return 0.72;
    if (isRelated)
    return 0.3;
    if (hasSearchFocus)
    return 0;
    return 0.02;
  }, [hoveredNeighborhood, hoveredNodeId, matchedNodeIds, selectedNeighborhood, selectedNodeId]);
  const targetSuggestions = useMemo(() => {
    if (!connectTarget.trim()) {
      return (data?.nodes || []).filter((node) => node.id !== selectedNodeId).slice(0, 6);
    }
    const keyword = connectTarget.trim().toLowerCase();
    return (data?.nodes || []).
    filter((node) => node.id !== selectedNodeId).
    filter((node) => node.id.toLowerCase().includes(keyword) || node.label.toLowerCase().includes(keyword)).
    slice(0, 6);
  }, [connectTarget, data?.nodes, selectedNodeId]);
  const closeMenu = useCallback(() => {
    setMenuMode("root");
    setMenuPosition(null);
    setRelations([]);
    setConnectTarget("");
    setConnectPredicate("RELATED_TO");
    setSelectedNodeId(null);
  }, []);
  const loadGraph = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/memory/graph");
      if (!res.ok) {
        throw new Error(`Load failed: ${res.status}`);
      }
      const json = await res.json();
      setData(seedGraphData(json));
    }
    catch (err) {
      console.error("Failed to load full graph:", err);
      toast({
        title: t("components.memory.GraphViewer.k0a0a7635"),
        description: t("components.memory.GraphViewer.kc39f4757"),
        variant: "destructive"
      });
    } finally
    {
      setLoading(false);
    }
  }, [t, toast]);
  const loadEntityRelations = useCallback(async (entityId: string) => {
    try {
      const res = await fetch(`/api/memory/graph?entity=${encodeURIComponent(entityId)}`);
      if (!res.ok) {
        throw new Error(`Relation query failed: ${res.status}`);
      }
      const json = await res.json();
      setRelations(Array.isArray(json?.relations) ? json.relations : []);
    }
    catch (error) {
      console.error("Failed to query entity relations:", error);
      setRelations([]);
      toast({
        title: t("components.memory.GraphViewer.k4581cc9f"),
        description: t("components.memory.GraphViewer.ka695eebd"),
        variant: "destructive"
      });
    }
  }, [t, toast]);
  useEffect(() => {
    void loadGraph();
  }, [loadGraph]);
  useEffect(() => {
    const element = containerRef.current;
    if (!element || typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry)
      return;
      const width = Math.max(420, Math.floor(entry.contentRect.width));
      const height = Math.max(620, Math.floor(entry.contentRect.height));
      setGraphSize({ width, height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!data?.nodes.length || !fgRef.current) {
      return;
    }
    fgRef.current.d3Force("charge")?.strength?.((node: GraphNode) => {
      const degree = nodeDegrees.get(node.id) || 0;
      return -180 - Math.min(degree * 10, 120);
    });
    fgRef.current.d3Force("link")?.distance?.((link: GraphLink) => {
      const sourceId = typeof link.source === "string" ? link.source : link.source.id;
      const targetId = typeof link.target === "string" ? link.target : link.target.id;
      const sourceDegree = nodeDegrees.get(sourceId) || 0;
      const targetDegree = nodeDegrees.get(targetId) || 0;
      return 106 + Math.min((sourceDegree + targetDegree) * 3, 54);
    });
    fgRef.current.d3Force("link")?.strength?.((link: GraphLink) => {
      const sourceId = typeof link.source === "string" ? link.source : link.source.id;
      const targetId = typeof link.target === "string" ? link.target : link.target.id;
      const sourceDegree = nodeDegrees.get(sourceId) || 0;
      const targetDegree = nodeDegrees.get(targetId) || 0;
      return Math.max(0.08, 0.16 - Math.min((sourceDegree + targetDegree) * 0.003, 0.06));
    });
    fgRef.current.d3Force("collision", forceCollide().
    radius((node: GraphNode) => {
      const degree = nodeDegrees.get(node.id) || 0;
      return Math.max((node.val || 3) * 2.25 + Math.min(degree * 1.15, 10), 20);
    }).
    strength(0.96));
    fgRef.current.d3Force("center-x", forceX(0).strength(0.14));
    fgRef.current.d3Force("center-y", forceY(0).strength(0.14));
    fgRef.current.d3ReheatSimulation?.();
    fgRef.current.zoomToFit?.(520, 110);
  }, [data, graphSize.height, graphSize.width, nodeDegrees]);
  useEffect(() => {
    if (!selectedNodeId) {
      return;
    }
    void loadEntityRelations(selectedNodeId);
  }, [loadEntityRelations, selectedNodeId]);
  useEffect(() => {
    if (!fgRef.current || !data?.nodes.length) {
      return;
    }
    if (!normalizedFilter) {
      guidedFilterRef.current = "";
      return;
    }
    if (guidedFilterRef.current === normalizedFilter || matchedNodeIds.size === 0) {
      return;
    }
    const matchedNodes = data.nodes.filter((node) => matchedNodeIds.has(node.id) && Number.isFinite(node.x) && Number.isFinite(node.y));
    if (matchedNodes.length === 0) {
      return;
    }
    const centerX = matchedNodes.reduce((sum, node) => sum + (node.x || 0), 0) / matchedNodes.length;
    const centerY = matchedNodes.reduce((sum, node) => sum + (node.y || 0), 0) / matchedNodes.length;
    fgRef.current.centerAt?.(centerX, centerY, 680);
    const currentZoom = typeof fgRef.current.zoom === "function" ? fgRef.current.zoom() : 1;
    const targetZoom = matchedNodes.length === 1 ? 1.28 : 1.08;
    if (typeof currentZoom === "number" && currentZoom < targetZoom) {
      fgRef.current.zoom?.(targetZoom, 680);
    }
    guidedFilterRef.current = normalizedFilter;
  }, [data?.nodes, matchedNodeIds, normalizedFilter]);
  useEffect(() => {
    const handlePointerDown = (event: MouseEvent) => {
      if (!menuRef.current) {
        return;
      }
      if (!menuRef.current.contains(event.target as Node)) {
        closeMenu();
      }
    };
    window.addEventListener("pointerdown", handlePointerDown);
    return () => window.removeEventListener("pointerdown", handlePointerDown);
  }, [closeMenu]);
  const refreshGraphState = useCallback(async () => {
    await loadGraph();
    if (selectedNodeId) {
      await loadEntityRelations(selectedNodeId);
    }
  }, [loadEntityRelations, loadGraph, selectedNodeId]);
  const handleDeleteNode = useCallback(async () => {
    if (!selectedNode) {
      return;
    }
    if (!window.confirm(t("components.memory.GraphViewer.k30907e46", {
      selectedNode_id: selectedNode.id
    }))) {
      return;
    }
    setMutating(true);
    try {
      const res = await fetch("/api/memory/graph", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "delete_entity", name: selectedNode.id })
      });
      if (!res.ok) {
        throw new Error(`Delete failed: ${res.status}`);
      }
      toast({
        title: t("components.memory.GraphViewer.k99a1de8e"),
        description: t("components.memory.GraphViewer.k45db347e", {
          selectedNode_label: selectedNode.label
        })
      });
      closeMenu();
      await loadGraph();
    }
    catch (error) {
      console.error("Failed to delete node:", error);
      toast({
        title: t("components.memory.GraphViewer.k0915ccdf"),
        description: t("components.memory.GraphViewer.k70149133"),
        variant: "destructive"
      });
    } finally
    {
      setMutating(false);
    }
  }, [closeMenu, loadGraph, selectedNode, t, toast]);
  const handleCreateRelation = useCallback(async () => {
    if (!selectedNode) {
      return;
    }
    const target = connectTarget.trim().toLowerCase();
    const predicate = connectPredicate.trim().toUpperCase();
    if (!target || !predicate) {
      toast({
        title: t("components.memory.GraphViewer.kcffa7b82"),
        description: t("components.memory.GraphViewer.kcee3c374"),
        variant: "destructive"
      });
      return;
    }
    setMutating(true);
    try {
      const res = await fetch("/api/memory/graph", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "add_relation",
          subject: selectedNode.id,
          predicate,
          object: target,
          confidence: 1.0,
          maintainerSource: "human_admin"
        })
      });
      if (!res.ok) {
        throw new Error(`Create relation failed: ${res.status}`);
      }
      toast({
        title: t("components.memory.GraphViewer.k15e9fe40"),
        description: `${selectedNode.id} -[${predicate}]-> ${target}`
      });
      setMenuMode("disconnect");
      setConnectTarget("");
      setConnectPredicate("RELATED_TO");
      await refreshGraphState();
    }
    catch (error) {
      console.error("Failed to create relation:", error);
      toast({
        title: t("components.memory.GraphViewer.k4375bb96"),
        description: t("components.memory.GraphViewer.k0a92a8cb"),
        variant: "destructive"
      });
    } finally
    {
      setMutating(false);
    }
  }, [connectPredicate, connectTarget, refreshGraphState, selectedNode, t, toast]);
  const handleDeleteRelation = useCallback(async (relation: GraphRelation) => {
    setMutating(true);
    try {
      const res = await fetch("/api/memory/graph", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "delete_relation",
          subject: relation.subject,
          predicate: relation.predicate,
          object: relation.object
        })
      });
      if (!res.ok) {
        throw new Error(`Delete relation failed: ${res.status}`);
      }
      toast({
        title: t("components.memory.GraphViewer.kf91b5e0e"),
        description: t("components.memory.GraphViewer.k9a809092", {
          relation_subject: relation.subject,
          relation_object: relation.object
        })
      });
      await refreshGraphState();
    }
    catch (error) {
      console.error("Failed to delete relation:", error);
      toast({
        title: t("components.memory.GraphViewer.kcea2a810"),
        description: t("components.memory.GraphViewer.k5b4be0f6"),
        variant: "destructive"
      });
    } finally
    {
      setMutating(false);
    }
  }, [refreshGraphState, t, toast]);
  const handleNodeClick = useCallback(async (node: object, event: MouseEvent) => {
    const nextNode = node as GraphNode;
    setSelectedNodeId(nextNode.id);
    setMenuMode("root");
    const containerRect = containerRef.current?.getBoundingClientRect();
    const posX = containerRect ? event.clientX - containerRect.left : 24;
    const posY = containerRect ? event.clientY - containerRect.top : 24;
    setMenuPosition({ x: Math.min(posX + 12, graphSize.width - 280), y: Math.min(posY + 12, graphSize.height - 220) });
    await loadEntityRelations(nextNode.id);
  }, [graphSize.height, graphSize.width, loadEntityRelations]);
  const handleBackgroundClick = useCallback(() => {
    if (menuPosition || selectedNodeId) {
      closeMenu();
    }
  }, [closeMenu, menuPosition, selectedNodeId]);
  const isLinkHighlighted = useCallback((link: GraphLink) => {
    const sourceId = typeof link.source === "string" ? link.source : link.source.id;
    const targetId = typeof link.target === "string" ? link.target : link.target.id;
    if (selectedNodeId) {
      return sourceId === selectedNodeId || targetId === selectedNodeId;
    }
    if (hoveredNodeId) {
      return sourceId === hoveredNodeId || targetId === hoveredNodeId;
    }
    if (!normalizedFilter) {
      return true;
    }
    return matchedNodeIds.has(sourceId) || matchedNodeIds.has(targetId) || link.label.toLowerCase().includes(normalizedFilter);
  }, [hoveredNodeId, matchedNodeIds, normalizedFilter, selectedNodeId]);
  if (loading) {
    return <div className="flex h-[620px] items-center justify-center rounded-3xl border border-border/60 bg-muted/10">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>;
  }
  if (!data || data.nodes.length === 0) {
    return <div className="flex h-[620px] flex-col items-center justify-center rounded-3xl border border-dashed border-border/70 bg-muted/10">
                <p className="mb-2 text-muted-foreground">{t("components.memory.GraphViewer.kc8e2fe51")}</p>
                <p className="text-xs text-muted-foreground/70">{t("components.memory.GraphViewer.k46f71c00")}</p>
            </div>;
  }
  return <div ref={containerRef} className="relative h-[620px] w-full overflow-hidden rounded-3xl border border-border/60 bg-[radial-gradient(circle_at_center,rgba(99,102,241,0.12),transparent_42%),radial-gradient(circle_at_24%_22%,rgba(56,189,248,0.12),transparent_28%),radial-gradient(circle_at_78%_18%,rgba(16,185,129,0.08),transparent_24%),linear-gradient(180deg,rgba(15,23,42,0.16),rgba(2,6,23,0.02))]">
            <div className="pointer-events-none absolute inset-0">
                <div className="absolute left-1/2 top-1/2 h-80 w-80 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary/10 blur-3xl" />
            </div>

            <div className="absolute left-5 top-5 z-10 max-w-xs rounded-2xl border border-border/50 bg-background/70 px-4 py-3 backdrop-blur">
                <p className="text-xs uppercase tracking-[0.22em] text-muted-foreground/70">{t("components.memory.GraphViewer.ka9b2a770")}</p>
                <p className="mt-2 text-sm text-muted-foreground">
                    {t("components.memory.GraphViewer.kde12c84c")}
                </p>
            </div>

            <ForceGraph2D ref={fgRef} width={graphSize.width} height={graphSize.height} graphData={data} backgroundColor="rgba(0,0,0,0)" warmupTicks={36} cooldownTicks={150} enableNodeDrag={false} onBackgroundClick={handleBackgroundClick} onEngineStop={() => fgRef.current?.zoomToFit?.(480, 110)} onNodeHover={(node) => setHoveredNodeId(node ? (node as GraphNode).id : null)} onNodeClick={(node, event) => void handleNodeClick(node, event as MouseEvent)} nodeLabel={(node) => {
      const graphNode = node as GraphNode;
      const degree = nodeDegrees.get(graphNode.id) || 0;
      return t("components.memory.GraphViewer.nodeTooltip.links", {
        label: graphNode.label || graphNode.id,
        type: graphNode.type,
        count: degree
      });
    }} linkLabel={(link) => {
      const edge = link as GraphLink;
      return `${edge.label} · ${(edge.confidence ?? 1).toFixed(2)}`;
    }} linkWidth={(link) => {
      const edge = link as GraphLink;
      const highlighted = isLinkHighlighted(edge);
      if (selectedNodeId || hoveredNodeId) {
        return highlighted ? 1.9 : 0.35;
      }
      if (normalizedFilter) {
        return highlighted ? 1.4 : 0.18;
      }
      return highlighted ? 1.1 : 0.45;
    }} linkColor={(link) => {
      const edge = link as GraphLink;
      const highlighted = isLinkHighlighted(edge);
      if (selectedNodeId) {
        return highlighted ? "rgba(99,102,241,0.62)" : "rgba(148,163,184,0.08)";
      }
      if (hoveredNodeId) {
        return highlighted ? "rgba(56,189,248,0.48)" : "rgba(148,163,184,0.06)";
      }
      if (normalizedFilter) {
        return highlighted ? "rgba(236,72,153,0.46)" : "rgba(148,163,184,0.05)";
      }
      return highlighted ? "rgba(148,163,184,0.42)" : "rgba(148,163,184,0.12)";
    }} linkDirectionalArrowLength={4} linkDirectionalArrowRelPos={1} nodePointerAreaPaint={(node, color, ctx) => {
      const graphNode = node as GraphNode;
      const scale = getNodeScale(graphNode.id);
      const radius = Math.max(((graphNode.val || 3) + 6) * Math.max(scale, 1), 10);
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(graphNode.x || 0, graphNode.y || 0, radius, 0, 2 * Math.PI, false);
      ctx.fill();
    }} nodeCanvasObject={(node, ctx, globalScale) => {
      const graphNode = node as GraphNode;
      const label = graphNode.label || graphNode.id;
      const baseRadius = Math.max((graphNode.val || 3) * 1.6, 7);
      const matchesFilter = !normalizedFilter || matchedNodeIds.has(graphNode.id);
      const isSelected = graphNode.id === selectedNodeId;
      const isHovered = graphNode.id === hoveredNodeId;
      const isMatched = matchedNodeIds.has(graphNode.id);
      const isRelated = selectedNeighborhood.has(graphNode.id) || hoveredNeighborhood.has(graphNode.id);
      const scale = getNodeScale(graphNode.id);
      const radius = baseRadius * scale;
      const hasSearchFocus = matchedNodeIds.size > 0;
      if (graphNode.id === firstRenderableNodeId) {
        labelBoxesRef.current = [];
      }
      const baseAlpha = isSelected ?
      1 :
      isHovered ?
      0.98 :
      isMatched ?
      0.94 :
      isRelated ?
      0.74 :
      hasSearchFocus ?
      0.18 :
      matchesFilter ?
      0.82 :
      0.46;
      ctx.save();
      ctx.globalAlpha = baseAlpha;
      ctx.shadowColor = isSelected ?
      "rgba(99,102,241,0.72)" :
      isHovered ?
      "rgba(59,130,246,0.46)" :
      isMatched ?
      "rgba(236,72,153,0.32)" :
      isRelated ?
      "rgba(99,102,241,0.18)" :
      "rgba(15,23,42,0.14)";
      ctx.shadowBlur = isSelected ? 30 : isHovered ? 20 : isMatched ? 18 : isRelated ? 12 : 8;
      ctx.fillStyle = matchesFilter ? graphNode.color || "#6366f1" : "rgba(148,163,184,0.46)";
      ctx.beginPath();
      ctx.arc(graphNode.x || 0, graphNode.y || 0, radius, 0, 2 * Math.PI, false);
      ctx.fill();
      if (isSelected) {
        ctx.strokeStyle = "rgba(255,255,255,0.88)";
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.arc(graphNode.x || 0, graphNode.y || 0, radius + 3.5, 0, 2 * Math.PI, false);
        ctx.stroke();
      }
      if (isHovered || isMatched) {
        ctx.strokeStyle = isHovered ? "rgba(255,255,255,0.58)" : "rgba(236,72,153,0.44)";
        ctx.lineWidth = isHovered ? 1.2 : 1;
        ctx.beginPath();
        ctx.arc(graphNode.x || 0, graphNode.y || 0, radius + (isHovered ? 2.4 : 1.8), 0, 2 * Math.PI, false);
        ctx.stroke();
      }
      const labelOpacity = getLabelOpacity(graphNode.id);
      if (labelOpacity > 0.045) {
        const fontSize = Math.max(9 / globalScale, isSelected ? 12 : 10);
        ctx.font = `${isSelected ? 700 : 600} ${fontSize}px sans-serif`;
        const shouldForceLabel = isSelected || isHovered || isMatched;
        const textWidth = ctx.measureText(label).width;
        const labelX = graphNode.x || 0;
        const labelY = (graphNode.y || 0) + radius + 6;
        const labelHeight = fontSize + 4;
        const labelBox = {
          x: labelX - textWidth / 2 - 6,
          y: labelY - 2,
          w: textWidth + 12,
          h: labelHeight + 4
        };
        const overlaps = labelBoxesRef.current.some((box) => labelBox.x < box.x + box.w &&
        labelBox.x + labelBox.w > box.x &&
        labelBox.y < box.y + box.h &&
        labelBox.y + labelBox.h > box.y);
        if (!shouldForceLabel && overlaps) {
          ctx.restore();
          return;
        }
        labelBoxesRef.current.push(labelBox);
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.fillStyle = `rgba(248,250,252,${Math.min(labelOpacity, 0.96)})`;
        ctx.shadowColor = `rgba(15,23,42,${Math.min(labelOpacity * 0.55, 0.35)})`;
        ctx.shadowBlur = 10;
        ctx.fillText(label, labelX, labelY);
      }
      ctx.restore();
    }} />

            {menuPosition && selectedNode ? <div ref={menuRef} className="absolute z-20 w-[260px] rounded-3xl border border-border/60 bg-background/90 p-4 shadow-2xl shadow-black/20 backdrop-blur-xl animate-in fade-in-0 zoom-in-95" style={{ left: menuPosition.x, top: menuPosition.y }}>
                    <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                            <p className="truncate text-sm font-semibold">{selectedNode.label}</p>
                            <p className="mt-1 text-xs text-muted-foreground">
                                {t("components.memory.GraphViewer.selectedNode.relationsCount", {
              type: selectedNode.type,
              count: relations.length
            })}
                            </p>
                        </div>
                        <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={closeMenu}>
                            {t("components.memory.GraphViewer.kabf558c9")}
                        </Button>
                    </div>

                    {menuMode === "root" ? <div className="mt-4 grid gap-2">
                            <Button variant="outline" className="justify-start" onClick={() => setMenuMode("connect")}>
                                <Link2 className="mr-2 h-4 w-4" />
                                {t("components.memory.GraphViewer.k71619908")}
                            </Button>
                            <Button variant="outline" className="justify-start" onClick={() => setMenuMode("disconnect")}>
                                <Unlink2 className="mr-2 h-4 w-4" />
                                {t("components.memory.GraphViewer.k4f94aab1")}
                            </Button>
                            <Button variant="destructive" className="justify-start" onClick={() => void handleDeleteNode()} disabled={mutating}>
                                <Trash2 className="mr-2 h-4 w-4" />
                                {t("components.memory.GraphViewer.keb9d3a3d")}
                            </Button>
                        </div> : null}

                    {menuMode === "connect" ? <div className="mt-4 space-y-3">
                            <div className="grid gap-2">
                                <label className="text-xs text-muted-foreground">{t("components.memory.GraphViewer.kda08b61d")}</label>
                                <Input value={connectTarget} onChange={(event) => setConnectTarget(event.target.value)} placeholder={t("components.memory.GraphViewer.k427230ad")} />
                            </div>
                            <div className="grid gap-2">
                                <label className="text-xs text-muted-foreground">{t("components.memory.GraphViewer.k75f25045")}</label>
                                <Input value={connectPredicate} onChange={(event) => setConnectPredicate(event.target.value.toUpperCase())} placeholder={t("components.memory.GraphViewer.k07a651fc")} className="font-mono" />
                            </div>

                            <div className="rounded-2xl border border-border/50 bg-muted/20 p-3">
                                <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
                                    <Search className="h-3.5 w-3.5" />
                                    {t("components.memory.GraphViewer.k09f0c48a")}
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    {targetSuggestions.length === 0 ? <span className="text-xs text-muted-foreground">{t("components.memory.GraphViewer.ka0e87e6f")}</span> : targetSuggestions.map((node) => <button key={node.id} type="button" className="rounded-full border border-border/60 px-2.5 py-1 text-xs transition hover:border-primary/40 hover:bg-primary/5" onClick={() => setConnectTarget(node.id)}>
                                                {node.label}
                                            </button>)}
                                </div>
                            </div>

                            <div className="flex items-center justify-between gap-2">
                                <Button variant="ghost" size="sm" onClick={() => setMenuMode("root")}>
                                    {t("components.memory.GraphViewer.k8d9b4100")}
                                </Button>
                                <div className="flex flex-wrap justify-end gap-2">
                                    <Button variant="outline" size="sm" onClick={() => void handleCreateRelation()} disabled={mutating || !connectTarget.trim()}>
                                        <Plus className="mr-2 h-4 w-4" />
                                        {t("admin.generated.25f51fce")}
                                    </Button>
                                    <Button size="sm" onClick={() => void handleCreateRelation()} disabled={mutating}>
                                        <Sparkles className="mr-2 h-4 w-4" />
                                        {t("components.memory.GraphViewer.kf5843a88")}
                                    </Button>
                                </div>
                            </div>
                        </div> : null}

                    {menuMode === "disconnect" ? <div className="mt-4 space-y-3">
                            <div className="max-h-56 space-y-2 overflow-y-auto pr-1">
                                {relations.length === 0 ? <div className="rounded-2xl border border-dashed px-3 py-4 text-xs text-muted-foreground">
                                        {t("components.memory.GraphViewer.kf70b8961")}
                                    </div> : relations.map((relation, index) => {
            const counterpart = relation.direction === "out" ? relation.object : relation.subject;
            return <div key={`${relation.subject}-${relation.predicate}-${relation.object}-${index}`} className="rounded-2xl border border-border/50 bg-muted/20 px-3 py-3">
                                                <div className="text-xs text-muted-foreground">{relation.predicate}</div>
                                                <div className="mt-1 text-sm font-medium break-all">{counterpart}</div>
                                                <div className="mt-3 flex justify-end">
                                                    <Button variant="outline" size="sm" className="text-xs" onClick={() => void handleDeleteRelation(relation)} disabled={mutating}>
                                                        <Unlink2 className="mr-2 h-3.5 w-3.5" />
                                                        {t("components.memory.GraphViewer.k96f8f8c3")}
                                                    </Button>
                                                </div>
                                            </div>;
          })}
                            </div>
                            <div className="flex justify-start">
                                <Button variant="ghost" size="sm" onClick={() => setMenuMode("root")}>
                                    {t("components.memory.GraphViewer.k8d9b4100")}
                                </Button>
                            </div>
                        </div> : null}
                </div> : null}
        </div>;
}
