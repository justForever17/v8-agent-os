"use client";

import { memo, useEffect, useMemo, useRef, useState } from "react";
import { ExternalLink, LayoutPanelTop, Maximize2, RefreshCw } from "lucide-react";
import type { McpAppViewRef } from "@v8/session-realtime";

import { createUiAppDocument } from "@/lib/workbench";
import { useT } from "@/components/providers/LocaleProvider";
import { useWorkbenchStore } from "@/store/workbench-store";


type McpAppResource = {
    html: string;
    csp: Record<string, unknown>;
    permissions: Record<string, unknown>;
};

function recordOf(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function safeOrigins(value: unknown) {
    if (!Array.isArray(value)) return [];
    return value.flatMap((item) => {
        try {
            const url = new URL(String(item || ""));
            return url.protocol === "https:" || url.protocol === "http:" ? [url.origin] : [];
        } catch {
            return [];
        }
    });
}

function buildCsp(csp: Record<string, unknown>) {
    const connect = safeOrigins(csp.connectDomains || csp.connect_domains);
    const resources = safeOrigins(csp.resourceDomains || csp.resource_domains);
    const frames = safeOrigins(csp.frameDomains || csp.frame_domains);
    return [
        "default-src 'none'",
        `script-src 'unsafe-inline' ${resources.join(" ")}`.trim(),
        `style-src 'unsafe-inline' ${resources.join(" ")}`.trim(),
        `img-src data: blob: ${resources.join(" ")}`.trim(),
        `font-src data: ${resources.join(" ")}`.trim(),
        `media-src data: blob: ${resources.join(" ")}`.trim(),
        `connect-src ${connect.length ? connect.join(" ") : "'none'"}`,
        `frame-src ${frames.length ? frames.join(" ") : "'none'"}`,
        "form-action 'none'",
        "base-uri 'none'",
    ].join("; ");
}

function injectMcpAppHost(html: string, appInstanceId: string, csp: Record<string, unknown>) {
    const safeInstanceId = JSON.stringify(appInstanceId);
    const bootstrap = `<script>(function(){const id=${safeInstanceId};window.__v8McpAppInstanceId=id;window.v8McpAppPostMessage=function(message){window.parent.postMessage(Object.assign({},message,{appInstanceId:id}),"*")};try{const original=window.parent.postMessage.bind(window.parent);window.parent.postMessage=function(message,targetOrigin,transfer){return original(Object.assign({},message,{appInstanceId:id}),targetOrigin||"*",transfer)}}catch(_error){}})();</script>`;
    const meta = `<meta http-equiv="Content-Security-Policy" content="${buildCsp(csp).replace(/"/g, "&quot;")}">`;
    if (/<head[\s>]/i.test(html)) {
        return html.replace(/<head([^>]*)>/i, `<head$1>${meta}${bootstrap}`);
    }
    return `<!doctype html><html><head><meta charset="utf-8">${meta}${bootstrap}</head><body>${html}</body></html>`;
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

function FigmaCanvasRenderer({ mcpApp }: { mcpApp: McpAppViewRef }) {
    const t = useT();
    const [revision, setRevision] = useState(0);
    const grantExpired = Boolean(mcpApp.expiresAt && Date.parse(mcpApp.expiresAt) <= Date.now());
    const externalUrl = grantExpired ? "" : safeFigmaUrl(mcpApp.externalUrl);
    const allowedFrameOrigins = safeOrigins(mcpApp.allowedFrameOrigins);
    const figmaFrameAllowed = allowedFrameOrigins.includes("https://www.figma.com");
    const embedUrl = externalUrl && figmaFrameAllowed
        ? `https://www.figma.com/embed?embed_host=v8-agent-os&url=${encodeURIComponent(externalUrl)}`
        : "";
    return (
        <div className="flex h-full min-h-0 flex-col bg-background">
            <div className="flex h-8 shrink-0 items-center gap-2 border-b border-border/60 px-2 text-[11px] text-muted-foreground">
                <span className="h-2 w-2 bg-[#A259FF]" />
                <span className="min-w-0 flex-1 truncate">{t("web.mcpApp.figma.live")}</span>
                <button type="button" onClick={() => setRevision((value) => value + 1)} className="rounded-sm p-1 hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary" aria-label={t("web.mcpApp.figma.refresh")}><RefreshCw className="h-3.5 w-3.5" /></button>
                {externalUrl ? <a href={externalUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-sm px-1.5 py-1 hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary">{t("web.mcpApp.figma.open")}<ExternalLink className="h-3 w-3" /></a> : null}
            </div>
            <div className="min-h-0 flex-1 bg-[#f5f5f5]">
                {embedUrl ? (
                    <iframe key={revision} title={mcpApp.title || "Figma Canvas"} src={embedUrl} className="h-full w-full border-0" allowFullScreen sandbox="allow-scripts allow-same-origin allow-forms allow-popups" referrerPolicy="no-referrer" />
                ) : (
                    <div className="flex h-full items-center justify-center px-6 text-sm text-muted-foreground">{grantExpired ? t("web.mcpApp.figma.expired") : !figmaFrameAllowed ? t("web.mcpApp.figma.blocked") : t("web.mcpApp.figma.missingUrl")}</div>
                )}
            </div>
        </div>
    );
}

export function McpAppRenderer({ mcpApp }: { mcpApp: McpAppViewRef }) {
    const t = useT();
    const frameRef = useRef<HTMLIFrameElement | null>(null);
    const setMode = useWorkbenchStore((state) => state.setMode);
    const [resource, setResource] = useState<McpAppResource | null>(null);
    const [error, setError] = useState("");

    useEffect(() => {
        if (mcpApp.renderer === "figma") return;
        let cancelled = false;
        setError("");
        setResource(null);
        const query = new URLSearchParams({ serverName: mcpApp.serverName || "", uri: mcpApp.resourceUri });
        void fetch(`/api/mcp-apps/resources/read?${query.toString()}`, { cache: "no-store" })
            .then(async (response) => {
                const payload = await response.json().catch(() => ({})) as Record<string, unknown>;
                if (!response.ok) throw new Error(String(payload.detail || payload.error || response.statusText));
                const html = String(payload.html || "");
                if (!html.trim()) throw new Error(t("web.mcpApp.empty"));
                if (!cancelled) {
                    setResource({
                        html,
                        csp: { ...recordOf(payload.csp), ...recordOf(mcpApp.csp) },
                        permissions: { ...recordOf(payload.permissions), ...recordOf(mcpApp.permissions) },
                    });
                }
            })
            .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); });
        return () => { cancelled = true; };
    }, [mcpApp.csp, mcpApp.permissions, mcpApp.renderer, mcpApp.resourceUri, mcpApp.serverName, t]);

    useEffect(() => {
        if (mcpApp.renderer === "figma") return;
        const handleMessage = async (event: MessageEvent) => {
            if (event.source !== frameRef.current?.contentWindow) return;
            const data = recordOf(event.data);
            const params = recordOf(data.params);
            const instanceId = String(data.appInstanceId || params.appInstanceId || "");
            // event.source is the primary iframe identity. A supplied appInstanceId must match;
            // standard MCP Apps that omit it are bound to this frame by the host.
            if (instanceId && instanceId !== mcpApp.appInstanceId) return;
            const rpcPayload = data.payload && typeof data.payload === "object" ? data.payload : data;
            try {
                const response = await fetch(`/api/mcp-apps/instances/${encodeURIComponent(mcpApp.appInstanceId)}/rpc`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(rpcPayload),
                });
                const rpcResult = await response.json();
                const result = recordOf(rpcResult.result);
                if (["open-link", "openLink"].includes(String(result.action || "")) && typeof result.url === "string") {
                    window.open(result.url, "_blank", "noopener,noreferrer");
                }
                if (result.displayMode === "fullscreen") setMode("focus");
                if (result.displayMode === "inline") setMode("split");
                frameRef.current?.contentWindow?.postMessage({ ...rpcResult, appInstanceId: mcpApp.appInstanceId }, "*");
            } catch (reason) {
                frameRef.current?.contentWindow?.postMessage({
                    jsonrpc: "2.0",
                    appInstanceId: mcpApp.appInstanceId,
                    error: { code: -32603, message: reason instanceof Error ? reason.message : String(reason) },
                }, "*");
            }
        };
        window.addEventListener("message", handleMessage);
        return () => window.removeEventListener("message", handleMessage);
    }, [mcpApp.appInstanceId, mcpApp.renderer, setMode]);

    useEffect(() => {
        if (mcpApp.renderer === "figma") return;
        return () => {
            void fetch(`/api/mcp-apps/instances/${encodeURIComponent(mcpApp.appInstanceId)}/rpc`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ jsonrpc: "2.0", id: `teardown-${Date.now()}`, method: "ui/teardown", params: {} }),
                keepalive: true,
            }).catch(() => undefined);
        };
    }, [mcpApp.appInstanceId, mcpApp.renderer]);

    if (mcpApp.renderer === "figma") return <FigmaCanvasRenderer mcpApp={mcpApp} />;
    if (error) return <div className="flex h-full items-center justify-center px-6 text-sm text-destructive">{error}</div>;
    if (!resource) return <div className="flex h-full items-center justify-center text-xs text-muted-foreground">{t("web.mcpApp.loading")}</div>;
    const srcDoc = injectMcpAppHost(resource.html, mcpApp.appInstanceId, resource.csp);
    return <iframe ref={frameRef} title={mcpApp.title || `MCP App ${mcpApp.appInstanceId}`} className="h-full w-full border-0 bg-transparent" sandbox="allow-scripts allow-forms allow-popups" srcDoc={srcDoc} />;
}

export const McpAppFrame = memo(function McpAppFrame({ mcpApp }: { mcpApp: McpAppViewRef }) {
    const t = useT();
    const openDocument = useWorkbenchStore((state) => state.openDocument);
    const document = useMemo(() => createUiAppDocument(mcpApp), [mcpApp]);
    return (
        <button
            data-v8-context-open-workbench
            type="button"
            onClick={() => openDocument(document, { activate: true, mode: "split" })}
            className="mt-1 flex h-9 w-full max-w-md items-center gap-2 rounded-[5px] border border-border/65 bg-background/70 px-2.5 text-left text-xs transition-colors hover:border-primary/35 hover:bg-muted/35 focus-visible:ring-2 focus-visible:ring-primary"
        >
            <LayoutPanelTop className="h-3.5 w-3.5 shrink-0 text-primary" />
            <span className="min-w-0 flex-1 truncate font-medium">{document.title}</span>
            <span className="text-[10px] text-muted-foreground">{t("web.mcpApp.openWorkbench")}</span>
            <Maximize2 className="h-3.5 w-3.5 text-muted-foreground" />
        </button>
    );
});
