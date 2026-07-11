"use client";

import { memo, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink, LayoutPanelTop, Maximize2, RefreshCw } from "lucide-react";
import type { McpAppViewRef } from "@v8/session-realtime";

function escapeAttr(value: string) {
    return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function buildHostHtml(innerHtml: string) {
    const srcdoc = escapeAttr(innerHtml);
    return `<!doctype html><html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/><style>html,body,#host{margin:0;width:100%;height:100%;background:transparent;overflow:hidden;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}iframe{width:100%;height:100%;border:0;background:white}</style></head><body><div id="host"><iframe id="app" sandbox="allow-scripts allow-forms allow-popups" srcdoc="${srcdoc}"></iframe></div><script>const appFrame=document.getElementById('app');window.addEventListener('message',(event)=>{try{window.parent.postMessage({source:'v8-mcp-app',payload:event.data},'*')}catch(error){}});window.__v8DeliverMcpAppRpc=(payload)=>{try{appFrame.contentWindow&&appFrame.contentWindow.postMessage(payload,'*')}catch(error){}};window.addEventListener('message',(event)=>{if(event.data&&event.data.source==='v8-mcp-app-host'){window.__v8DeliverMcpAppRpc(event.data.payload)}});</script></body></html>`;
}

function safeFigmaUrl(value?: string) {
    try {
        const url = new URL(String(value || ""));
        if (url.protocol !== "https:" || !["figma.com", "www.figma.com"].includes(url.hostname.toLowerCase())) return "";
        if (!/^\/(?:design|file|proto|board)\/[A-Za-z0-9_-]+\/?$/.test(url.pathname)) return "";
        url.username = "";
        url.password = "";
        for (const key of Array.from(url.searchParams.keys())) {
            if (key !== "node-id") url.searchParams.delete(key);
        }
        return url.toString();
    } catch {
        return "";
    }
}

function FigmaCanvas({ mcpApp }: { mcpApp: McpAppViewRef }) {
    const shellRef = useRef<HTMLDivElement | null>(null);
    const [revision, setRevision] = useState(0);
    const grantExpired = Boolean(mcpApp.expiresAt && Date.parse(mcpApp.expiresAt) <= Date.now());
    const externalUrl = grantExpired ? "" : safeFigmaUrl(mcpApp.externalUrl);
    const embedUrl = externalUrl
        ? `https://www.figma.com/embed?embed_host=v8-agent-os&url=${encodeURIComponent(externalUrl)}`
        : "";

    return (
        <div ref={shellRef} className="mt-1 w-[min(78vw,1280px)] max-w-[calc(100vw-5rem)] overflow-hidden rounded-[6px] border border-border/70 bg-background p-0.5 shadow-[0_8px_28px_rgba(15,23,42,0.08)]">
            <div className="flex h-8 items-center gap-2 px-2 text-[11px] text-muted-foreground">
                <span className="size-2 bg-[#A259FF]" />
                <span className="font-medium text-foreground">{mcpApp.title || "Figma Canvas"}</span>
                <span className="min-w-0 flex-1 truncate">实时预览 · 修改通过已授权 Figma MCP 完成</span>
                <button type="button" onClick={() => setRevision((value) => value + 1)} className="rounded-sm p-1 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" aria-label="刷新 Figma 画布"><RefreshCw className="size-3.5" /></button>
                <button type="button" onClick={() => void shellRef.current?.requestFullscreen?.()} className="rounded-sm p-1 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" aria-label="全屏显示 Figma 画布"><Maximize2 className="size-3.5" /></button>
                {externalUrl ? <a href={externalUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-sm p-1 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">在 Figma 打开<ExternalLink className="size-3" /></a> : null}
            </div>
            <div className="h-[min(68vh,680px)] min-h-[480px] overflow-hidden border-t border-border/60 bg-[#f5f5f5]">
                {embedUrl ? (
                    <iframe key={revision} title={mcpApp.title || "Figma Canvas"} src={embedUrl} className="h-full w-full border-0" allowFullScreen sandbox="allow-scripts allow-same-origin allow-forms allow-popups" referrerPolicy="no-referrer" />
                ) : (
                    <div className="flex h-full items-center justify-center px-6 text-sm text-muted-foreground">{grantExpired ? "Figma 插件授权已过期，请重新 @ 插件。" : "未收到可验证的 Figma file/frame URL。"}</div>
                )}
            </div>
        </div>
    );
}

export const McpAppFrame = memo(function McpAppFrame({ mcpApp }: { mcpApp: McpAppViewRef }) {
    const frameRef = useRef<HTMLIFrameElement | null>(null);
    const [html, setHtml] = useState("");
    const [error, setError] = useState("");
    const [collapsed, setCollapsed] = useState(false);

    useEffect(() => {
        if (mcpApp.renderer === "figma") return;
        let cancelled = false;
        async function loadResource() {
            setError("");
            setHtml("");
            try {
                const query = new URLSearchParams({ serverName: mcpApp.serverName || "", uri: mcpApp.resourceUri });
                const response = await fetch(`/api/mcp-apps/resources/read?${query.toString()}`, { cache: "no-store" });
                const rawText = await response.text();
                const payload = rawText ? JSON.parse(rawText) as Record<string, unknown> : {};
                if (!response.ok) throw new Error(String(payload?.detail || payload?.error || response.statusText));
                if (!cancelled) {
                    const nextHtml = String(payload.html || "");
                    if (!nextHtml.trim()) throw new Error("MCP App 没有返回可展示内容。");
                    setHtml(buildHostHtml(nextHtml));
                }
            } catch (err) {
                if (!cancelled) setError(err instanceof Error ? err.message : String(err));
            }
        }
        void loadResource();
        return () => { cancelled = true; };
    }, [mcpApp.renderer, mcpApp.resourceUri, mcpApp.serverName]);

    useEffect(() => {
        if (mcpApp.renderer === "figma") return;
        async function handleMessage(event: MessageEvent) {
            const data = event.data;
            if (!data || data.source !== "v8-mcp-app") return;
            try {
                const response = await fetch(`/api/mcp-apps/instances/${encodeURIComponent(mcpApp.appInstanceId)}/rpc`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data.payload) });
                const rpcResult = await response.json();
                if (rpcResult?.result?.action === "openLink" && typeof rpcResult.result.url === "string") window.open(rpcResult.result.url, "_blank", "noopener,noreferrer");
                frameRef.current?.contentWindow?.postMessage({ source: "v8-mcp-app-host", payload: rpcResult }, "*");
            } catch (err) {
                frameRef.current?.contentWindow?.postMessage({ source: "v8-mcp-app-host", payload: { jsonrpc: "2.0", error: { code: -32603, message: err instanceof Error ? err.message : String(err) } } }, "*");
            }
        }
        window.addEventListener("message", handleMessage);
        return () => window.removeEventListener("message", handleMessage);
    }, [mcpApp.appInstanceId, mcpApp.renderer]);

    const srcDoc = useMemo(() => html, [html]);
    if (mcpApp.renderer === "figma") return <FigmaCanvas mcpApp={mcpApp} />;

    return (
        <div className="mt-1 overflow-hidden rounded-[6px] border border-border/70 bg-background/70 p-0.5">
            <button type="button" className="flex h-8 w-full items-center justify-between gap-2 px-2 text-left text-xs" onClick={() => setCollapsed((value) => !value)}>
                <span className="flex min-w-0 items-center gap-2"><LayoutPanelTop className="size-3.5 shrink-0 text-primary" /><span className="font-medium text-foreground">MCP App</span><span className="truncate text-[11px] text-muted-foreground">{mcpApp.resourceUri}</span></span>
                {collapsed ? <ChevronRight className="size-4 text-muted-foreground" /> : <ChevronDown className="size-4 text-muted-foreground" />}
            </button>
            {!collapsed ? <div className="h-80 border-t border-border/50">{error ? <div className="p-3 text-xs text-red-600">{error}</div> : srcDoc ? <iframe ref={frameRef} title={`MCP App ${mcpApp.appInstanceId}`} className="h-full w-full bg-transparent" sandbox="allow-scripts allow-forms allow-popups" srcDoc={srcDoc} /> : <div className="flex h-full items-center justify-center text-xs text-muted-foreground">Loading MCP App...</div>}</div> : null}
        </div>
    );
});
