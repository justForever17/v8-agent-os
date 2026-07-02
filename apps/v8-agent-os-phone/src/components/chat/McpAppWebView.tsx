import { memo, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Linking, Pressable, StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import WebView from "react-native-webview";

import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";

export type McpAppViewRef = {
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
      try {
        window.ReactNativeWebView && window.ReactNativeWebView.postMessage(JSON.stringify(event.data));
      } catch (error) {
        window.ReactNativeWebView && window.ReactNativeWebView.postMessage(JSON.stringify({ jsonrpc: '2.0', error: { message: String(error && error.message || error) } }));
      }
    });
    window.__v8DeliverMcpAppRpc = (payload) => {
      try {
        appFrame.contentWindow && appFrame.contentWindow.postMessage(payload, '*');
      } catch (error) {}
    };
  </script>
</body>
</html>`;
}

export const McpAppWebView = memo(function McpAppWebView({ mcpApp }: { mcpApp: McpAppViewRef }) {
    const { authorizedFetch } = useAppSession();
    const { colors, t } = useUiPrefs();
    const webViewRef = useRef<WebView>(null);
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
                const response = await authorizedFetch(`/api/client/mcp-apps/resources/read?${query.toString()}`);
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
    }, [authorizedFetch, mcpApp.resourceUri, mcpApp.serverName]);

    const source = useMemo(() => ({ html, baseUrl: "https://v8-mcp-app.local/" }), [html]);

    const handleMessage = async (event: { nativeEvent: { data?: string } }) => {
        const raw = String(event.nativeEvent.data || "");
        if (!raw.trim()) {
            return;
        }
        try {
            const payload = JSON.parse(raw);
            const response = await authorizedFetch(`/api/client/mcp-apps/instances/${encodeURIComponent(mcpApp.appInstanceId)}/rpc`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            const rpcResult = await response.json();
            if (rpcResult?.result?.action === "openLink" && typeof rpcResult.result.url === "string") {
                Linking.openURL(rpcResult.result.url).catch(() => undefined);
            }
            webViewRef.current?.injectJavaScript(
                `window.__v8DeliverMcpAppRpc(${JSON.stringify(rpcResult)}); true;`,
            );
        } catch (err) {
            const rpcError = { jsonrpc: "2.0", error: { code: -32603, message: err instanceof Error ? err.message : String(err) } };
            webViewRef.current?.injectJavaScript(
                `window.__v8DeliverMcpAppRpc(${JSON.stringify(rpcError)}); true;`,
            );
        }
    };

    return (
        <View style={[styles.card, { borderColor: colors.border, backgroundColor: colors.surface }]}>
            <Pressable style={styles.header} onPress={() => setCollapsed((value) => !value)}>
                <View style={styles.titleRow}>
                    <MaterialCommunityIcons name="webhook" size={15} color={colors.primary} />
                    <Text style={[styles.title, { color: colors.text }]}>
                        {t("src.components.chat.mcp_app.title")}
                    </Text>
                    <Text style={[styles.subtitle, { color: colors.textMuted }]} numberOfLines={1}>
                        {mcpApp.resourceUri}
                    </Text>
                </View>
                <MaterialCommunityIcons
                    name={collapsed ? "chevron-right" : "chevron-down"}
                    size={18}
                    color={colors.textSoft}
                />
            </Pressable>
            {!collapsed ? (
                <View style={[styles.body, { borderColor: colors.border }]}>
                    {error ? (
                        <Text style={[styles.error, { color: colors.danger || "#DC2626" }]}>{error}</Text>
                    ) : html ? (
                        <WebView
                            ref={webViewRef}
                            originWhitelist={["*"]}
                            source={source}
                            javaScriptEnabled
                            domStorageEnabled={false}
                            allowsInlineMediaPlayback={false}
                            mediaPlaybackRequiresUserAction
                            onMessage={handleMessage}
                            style={styles.webview}
                        />
                    ) : (
                        <View style={styles.loading}>
                            <ActivityIndicator size="small" color={colors.primary} />
                            <Text style={[styles.loadingText, { color: colors.textMuted }]}>
                                {t("src.components.chat.mcp_app.loading")}
                            </Text>
                        </View>
                    )}
                </View>
            ) : null}
        </View>
    );
});

const styles = StyleSheet.create({
    card: {
        width: "100%",
        borderWidth: 1,
        borderRadius: 14,
        overflow: "hidden",
        marginTop: 4,
    },
    header: {
        minHeight: 34,
        paddingHorizontal: 10,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 8,
    },
    titleRow: {
        flex: 1,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        minWidth: 0,
    },
    title: {
        fontSize: 12,
        fontWeight: "700",
    },
    subtitle: {
        flex: 1,
        fontSize: 10,
    },
    body: {
        height: 320,
        borderTopWidth: StyleSheet.hairlineWidth,
    },
    webview: {
        flex: 1,
        backgroundColor: "transparent",
    },
    loading: {
        flex: 1,
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
    },
    loadingText: {
        fontSize: 11,
    },
    error: {
        padding: 12,
        fontSize: 11,
        lineHeight: 17,
    },
});
