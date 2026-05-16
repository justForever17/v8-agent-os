"use client";

import { memo, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, LayoutPanelTop } from "lucide-react";

type McpAppViewRef = {
    appInstanceId: string;
    serverName?: string;
    resourceUri: string;
    toolInvocationId?: string;
    status?: string;
};

function escapeAttr(value: string) {
    return value
        .replace(/&/g, "&amp;")
        .replace(/"/g, "&quot;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}

function buildHostHtml(innerHtml: string) {
    const srcdoc = escapeAttr(innerHtml);
    return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    html,body,#host{margin:0;width:100%;height:100%;background:transparent;overflow:hidden;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;}
    iframe{width:100%;height:100%;border:0;background:white;border-radius:14px;}
  </style>
</head>
<body>
  <div id="host"><iframe id="app" sandbox="allow-scripts allow-forms allow-popups" srcdoc="${srcdoc}"></iframe></div>
  <script>
    const appFrame = document.getElementById('app');
    window.addEventListener('message', (event) => {
      try { window.parent.postMessage({ source: 'v8-mcp-app', payload: event.data }, '*'); } catch (error) {}
    });
    window.__v8DeliverMcpAppRpc = (payload) => {
      try { appFrame.contentWindow && appFrame.contentWindow.postMessage(payload, '*'); } catch (error) {}
    };
    window.addEventListener('message', (event) => {
      if (event.data && event.data.source === 'v8-mcp-app-host') {
        window.__v8DeliverMcpAppRpc(event.data.payload);
      }
    });
  </script>
</body>
</html>`;
}

export const McpAppFrame = memo(function McpAppFrame({ mcpApp }: { mcpApp: McpAppViewRef }) {
    const frameRef = useRef<HTMLIFrameElement | null>(null);
    const [html, setHtml] = useState("");
    const [error, setError] = useState("");
    const [collapsed, setCollapsed] = useState(false);

    useEffect(() => {
        let cancelled = false;
        async function loadResource() {
            setError("");
            setHtml("");
            try {
                const query = new URLSearchParams({
                    serverName: mcpApp.serverName || "",
                    uri: mcpApp.resourceUri,
                });
                const response = await fetch(`/api/mcp-apps/resources/read?${query.toString()}`, { cache: "no-store" });
                const payload = await response.json();
                if (!response.ok) {
                    throw new Error(String(payload?.detail || payload?.error || response.statusText));
                }
                if (!cancelled) {
                    setHtml(buildHostHtml(String(payload.html || "")));
                }
            } catch (err) {
                if (!cancelled) {
                    setError(err instanceof Error ? err.message : String(err));
                }
            }
        }
        loadResource();
        return () => {
            cancelled = true;
        };
    }, [mcpApp.resourceUri, mcpApp.serverName]);

    useEffect(() => {
        async function handleMessage(event: MessageEvent) {
            const data = event.data;
            if (!data || data.source !== "v8-mcp-app") {
                return;
            }
            try {
                const response = await fetch(`/api/mcp-apps/instances/${encodeURIComponent(mcpApp.appInstanceId)}/rpc`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(data.payload),
                });
                const rpcResult = await response.json();
                if (rpcResult?.result?.action === "openLink" && typeof rpcResult.result.url === "string") {
                    window.open(rpcResult.result.url, "_blank", "noopener,noreferrer");
                }
                frameRef.current?.contentWindow?.postMessage({ source: "v8-mcp-app-host", payload: rpcResult }, "*");
            } catch (err) {
                const rpcError = { jsonrpc: "2.0", error: { code: -32603, message: err instanceof Error ? err.message : String(err) } };
                frameRef.current?.contentWindow?.postMessage({ source: "v8-mcp-app-host", payload: rpcError }, "*");
            }
        }
        window.addEventListener("message", handleMessage);
        return () => window.removeEventListener("message", handleMessage);
    }, [mcpApp.appInstanceId]);

    const srcDoc = useMemo(() => html, [html]);

    return (
        <div className="mt-1 overflow-hidden rounded-xl border border-border/60 bg-background/70">
            <button
                type="button"
                className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs"
                onClick={() => setCollapsed((value) => !value)}
            >
                <span className="flex min-w-0 items-center gap-2">
                    <LayoutPanelTop className="h-3.5 w-3.5 shrink-0 text-primary" />
                    <span className="font-semibold text-foreground">MCP App</span>
                    <span className="truncate text-[11px] text-muted-foreground">{mcpApp.resourceUri}</span>
                </span>
                {collapsed ? <ChevronRight className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
            </button>
            {!collapsed ? (
                <div className="h-80 border-t border-border/50">
                    {error ? (
                        <div className="p-3 text-xs text-red-600">{error}</div>
                    ) : srcDoc ? (
                        <iframe
                            ref={frameRef}
                            title={`MCP App ${mcpApp.appInstanceId}`}
                            className="h-full w-full bg-transparent"
                            sandbox="allow-scripts allow-forms allow-popups"
                            srcDoc={srcDoc}
                        />
                    ) : (
                        <div className="flex h-full items-center justify-center text-xs text-muted-foreground">Loading MCP App...</div>
                    )}
                </div>
            ) : null}
        </div>
    );
});
