import { memo, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Linking, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import WebView from "react-native-webview";
import { SafeAreaView } from "react-native-safe-area-context";
import type { McpAppViewRef } from "@v8/session-realtime";

import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";

function escapeAttr(value: string) {
    return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function buildHostHtml(innerHtml: string) {
    const srcdoc = escapeAttr(innerHtml);
    return `<!doctype html><html><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/><style>html,body,#host{margin:0;width:100%;height:100%;background:transparent;overflow:hidden;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}iframe{width:100%;height:100%;border:0;background:white}</style></head><body><div id="host"><iframe id="app" sandbox="allow-scripts allow-forms allow-popups" srcdoc="${srcdoc}"></iframe></div><script>const appFrame=document.getElementById('app');window.addEventListener('message',(event)=>{try{window.ReactNativeWebView&&window.ReactNativeWebView.postMessage(JSON.stringify(event.data))}catch(error){}});window.__v8DeliverMcpAppRpc=(payload)=>{try{appFrame.contentWindow&&appFrame.contentWindow.postMessage(payload,'*')}catch(error){}};</script></body></html>`;
}

function safeFigmaUrl(value?: string) {
    try {
        const url = new URL(String(value || ""));
        if (url.protocol !== "https:" || !["figma.com", "www.figma.com"].includes(url.hostname.toLowerCase())) return "";
        if (!/^\/(?:design|file|proto|board)\/[A-Za-z0-9_-]+\/?$/.test(url.pathname)) return "";
        for (const key of Array.from(url.searchParams.keys())) if (key !== "node-id") url.searchParams.delete(key);
        return url.toString();
    } catch {
        return "";
    }
}

function FigmaCanvasModal({ mcpApp }: { mcpApp: McpAppViewRef }) {
    const { colors, themeMode, t } = useUiPrefs();
    const [open, setOpen] = useState(false);
    const [revision, setRevision] = useState(0);
    const [error, setError] = useState("");
    const grantExpired = Boolean(mcpApp.expiresAt && Date.parse(mcpApp.expiresAt) <= Date.now());
    const externalUrl = grantExpired ? "" : safeFigmaUrl(mcpApp.externalUrl);
    const embedUrl = externalUrl ? `https://www.figma.com/embed?embed_host=v8-agent-os-phone&url=${encodeURIComponent(externalUrl)}` : "";

    return (
        <>
            <View style={[styles.figmaSummary, { borderColor: colors.border, backgroundColor: colors.surface }]}>
                <View style={styles.figmaSummaryText}>
                    <View style={styles.titleRow}><View style={styles.figmaMark} /><Text style={[styles.title, { color: colors.text }]}>{mcpApp.title || t("src.components.chat.mcp_app.figma_canvas_title")}</Text></View>
                    <Text style={[styles.subtitle, { color: colors.textMuted }]} numberOfLines={2}>{t("src.components.chat.mcp_app.figma_canvas_description")}</Text>
                </View>
                <Pressable onPress={() => setOpen(true)} style={[styles.openButton, { borderColor: colors.border }]}><MaterialCommunityIcons name="arrow-expand" size={15} color={colors.primary} /><Text style={[styles.openButtonText, { color: colors.text }]}>{t("src.components.chat.mcp_app.open_canvas")}</Text></Pressable>
            </View>
            <Modal visible={open} animationType="slide" presentationStyle="fullScreen" onRequestClose={() => setOpen(false)}>
                <SafeAreaView style={[styles.modalRoot, { backgroundColor: themeMode === "dark" ? "#111111" : "#FFFFFF" }]} edges={["top", "bottom", "left", "right"]}>
                    <View style={[styles.modalToolbar, { borderColor: colors.border }]}>
                        <Pressable onPress={() => setOpen(false)} hitSlop={10} style={styles.toolbarButton} accessibilityRole="button" accessibilityLabel={t("src.components.chat.mcp_app.close_canvas")}><MaterialCommunityIcons name="close" size={22} color={colors.text} /></Pressable>
                        <Text style={[styles.modalTitle, { color: colors.text }]} numberOfLines={1}>{mcpApp.title || "Figma Canvas"}</Text>
                        <Pressable onPress={() => { setError(""); setRevision((value) => value + 1); }} hitSlop={10} style={styles.toolbarButton} accessibilityRole="button" accessibilityLabel={t("src.components.chat.mcp_app.reload_after_login")}><MaterialCommunityIcons name="refresh" size={20} color={colors.textMuted} /></Pressable>
                        <Pressable onPress={() => externalUrl && Linking.openURL(externalUrl)} hitSlop={10} style={styles.toolbarButton} accessibilityRole="link" accessibilityLabel={t("src.components.chat.mcp_app.complete_login_in_browser")}><MaterialCommunityIcons name="open-in-new" size={20} color={colors.primary} /></Pressable>
                    </View>
                    {error || !embedUrl ? (
                        <View style={styles.modalError}>
                            <MaterialCommunityIcons name="account-lock-outline" size={30} color={colors.textMuted} />
                                <Text selectable style={[styles.error, { color: colors.textMuted }]}>{grantExpired ? t("src.components.chat.mcp_app.authorization_expired") : error || t("src.components.chat.mcp_app.figma_url_missing")}</Text>
                                {externalUrl ? <Pressable onPress={() => Linking.openURL(externalUrl)} style={[styles.openButton, { borderColor: colors.border }]}><Text style={[styles.openButtonText, { color: colors.text }]}>{t("src.components.chat.mcp_app.complete_login_in_browser")}</Text></Pressable> : null}
                                {embedUrl ? <Pressable onPress={() => { setError(""); setRevision((value) => value + 1); }}><Text style={{ color: colors.primary }}>{t("src.components.chat.mcp_app.reload_after_login")}</Text></Pressable> : null}
                        </View>
                    ) : (
                        <WebView
                            key={revision}
                            source={{ uri: embedUrl }}
                            originWhitelist={["https://www.figma.com", "https://figma.com"]}
                            javaScriptEnabled
                            domStorageEnabled
                            thirdPartyCookiesEnabled
                            sharedCookiesEnabled
                            allowsInlineMediaPlayback
                                onShouldStartLoadWithRequest={(request) => {
                                    try {
                                        const url = new URL(request.url);
                                        return url.protocol === "https:" && ["figma.com", "www.figma.com"].includes(url.hostname.toLowerCase());
                                    } catch {
                                        return false;
                                    }
                                }}
                                onError={(event) => setError(event.nativeEvent.description || t("src.components.chat.mcp_app.figma_embed_failed"))}
                                onHttpError={(event) => setError(t("src.components.chat.mcp_app.figma_embed_http_failed", { status: event.nativeEvent.statusCode }))}
                            style={styles.webview}
                        />
                    )}
                </SafeAreaView>
            </Modal>
        </>
    );
}

export const McpAppWebView = memo(function McpAppWebView({ mcpApp }: { mcpApp: McpAppViewRef }) {
    const { authorizedFetch } = useAppSession();
    const { colors, t } = useUiPrefs();
    const webViewRef = useRef<WebView>(null);
    const [html, setHtml] = useState("");
    const [error, setError] = useState("");
    const [collapsed, setCollapsed] = useState(false);

    useEffect(() => {
        if (mcpApp.renderer === "figma") return;
        let cancelled = false;
        async function loadResource() {
            setError(""); setHtml("");
            try {
                const query = new URLSearchParams({ serverName: mcpApp.serverName || "", uri: mcpApp.resourceUri });
                const response = await authorizedFetch(`/api/client/mcp-apps/resources/read?${query.toString()}`);
                const payload = await response.json();
                if (!response.ok) throw new Error(String(payload?.detail || payload?.error || response.statusText));
                if (!cancelled) setHtml(buildHostHtml(String(payload.html || "")));
            } catch (err) { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); }
        }
        void loadResource();
        return () => { cancelled = true; };
    }, [authorizedFetch, mcpApp.renderer, mcpApp.resourceUri, mcpApp.serverName]);

    const source = useMemo(() => ({ html, baseUrl: "https://v8-mcp-app.local/" }), [html]);
    const handleMessage = async (event: { nativeEvent: { data?: string } }) => {
        const raw = String(event.nativeEvent.data || "");
        if (!raw.trim()) return;
        try {
            const response = await authorizedFetch(`/api/client/mcp-apps/instances/${encodeURIComponent(mcpApp.appInstanceId)}/rpc`, { method: "POST", headers: { "Content-Type": "application/json" }, body: raw });
            const rpcResult = await response.json();
            if (rpcResult?.result?.action === "openLink" && typeof rpcResult.result.url === "string") Linking.openURL(rpcResult.result.url).catch(() => undefined);
            webViewRef.current?.injectJavaScript(`window.__v8DeliverMcpAppRpc(${JSON.stringify(rpcResult)}); true;`);
        } catch (err) {
            webViewRef.current?.injectJavaScript(`window.__v8DeliverMcpAppRpc(${JSON.stringify({ jsonrpc: "2.0", error: { code: -32603, message: err instanceof Error ? err.message : String(err) } })}); true;`);
        }
    };

    if (mcpApp.renderer === "figma") return <FigmaCanvasModal mcpApp={mcpApp} />;

    return (
        <View style={[styles.card, { borderColor: colors.border, backgroundColor: colors.surface }]}>
            <Pressable style={styles.header} onPress={() => setCollapsed((value) => !value)}>
                <View style={styles.titleRow}><MaterialCommunityIcons name="webhook" size={15} color={colors.primary} /><Text style={[styles.title, { color: colors.text }]}>{t("src.components.chat.mcp_app.title")}</Text><Text style={[styles.subtitle, { color: colors.textMuted }]} numberOfLines={1}>{mcpApp.resourceUri}</Text></View>
                <MaterialCommunityIcons name={collapsed ? "chevron-right" : "chevron-down"} size={18} color={colors.textSoft} />
            </Pressable>
            {!collapsed ? <View style={[styles.body, { borderColor: colors.border }]}>{error ? <Text style={[styles.error, { color: colors.danger || "#DC2626" }]}>{error}</Text> : html ? <WebView ref={webViewRef} originWhitelist={["*"]} source={source} javaScriptEnabled domStorageEnabled={false} onMessage={handleMessage} style={styles.webview} /> : <View style={styles.loading}><ActivityIndicator size="small" color={colors.primary} /></View>}</View> : null}
        </View>
    );
});

const styles = StyleSheet.create({
    card: { width: "100%", borderWidth: StyleSheet.hairlineWidth, borderRadius: 6, overflow: "hidden", marginTop: 4 },
    header: { minHeight: 32, paddingHorizontal: 8, flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 8 },
    titleRow: { flex: 1, flexDirection: "row", alignItems: "center", gap: 6, minWidth: 0 },
    title: { fontSize: 12, fontWeight: "700" },
    subtitle: { flex: 1, fontSize: 10 },
    body: { height: 320, borderTopWidth: StyleSheet.hairlineWidth },
    webview: { flex: 1, backgroundColor: "transparent" },
    loading: { flex: 1, alignItems: "center", justifyContent: "center" },
    error: { padding: 12, fontSize: 11, lineHeight: 17 },
    figmaSummary: { width: "100%", borderWidth: StyleSheet.hairlineWidth, borderRadius: 6, padding: 8, marginTop: 4, flexDirection: "row", alignItems: "center", gap: 10 },
    figmaSummaryText: { flex: 1, minWidth: 0, gap: 3 },
    figmaMark: { width: 8, height: 8, backgroundColor: "#A259FF" },
    openButton: { minHeight: 44, borderWidth: StyleSheet.hairlineWidth, borderRadius: 5, paddingHorizontal: 10, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 5 },
    openButtonText: { fontSize: 12, fontWeight: "700" },
    modalRoot: { flex: 1 },
    modalToolbar: { height: 46, paddingHorizontal: 12, borderBottomWidth: StyleSheet.hairlineWidth, flexDirection: "row", alignItems: "center", gap: 14 },
    toolbarButton: { width: 44, height: 44, alignItems: "center", justifyContent: "center", borderCurve: "continuous" },
    modalTitle: { flex: 1, fontSize: 14, fontWeight: "700" },
    modalError: { flex: 1, alignItems: "center", justifyContent: "center", gap: 14, padding: 24 },
});
