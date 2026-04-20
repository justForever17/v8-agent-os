import { memo, useMemo, useState } from "react";
import {
    Alert,
    Pressable,
    StyleSheet,
    Text,
    View,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { WebView } from "react-native-webview";

import { Card, CardContent } from "@/src/components/ui/card";
import { saveTextToUserSelectedFile } from "@/src/lib/file-transfer";
import { useUiPrefs } from "@/src/providers/ui-prefs";

function scriptString(value: string) {
    return JSON.stringify(value).replace(/<\/script/gi, "<\\/script");
}

export const MermaidRenderer = memo(function MermaidRenderer({
    code,
}: {
    code: string;
}) {
    const { colors, themeMode, t } = useUiPrefs();
    const [scale, setScale] = useState(1);
    const [hasError, setHasError] = useState(false);
    const [renderedSvg, setRenderedSvg] = useState("");
    const [saved, setSaved] = useState(false);

    const handleDownload = async () => {
        const svg = renderedSvg.trim();
        const content = svg || code;
        if (!content.trim()) {
            return;
        }
        const useSvg = Boolean(svg);
        try {
            const savedFile = await saveTextToUserSelectedFile(content, {
                filename: useSvg ? `mermaid-chart-${Date.now()}.svg` : `mermaid-source-${Date.now()}.mmd`,
                mimeType: useSvg ? "image/svg+xml" : "text/plain",
            });
            setSaved(true);
            Alert.alert(
                t("src.components.chat.mermaidrenderer.chart_downloaded"),
                savedFile.shared
                    ? `${t("src.components.chat.downloadfilecard.opened_the_system_share_save_to_files_sheet")}：${savedFile.filename}`
                    : savedFile.userVisible
                    ? `${t("src.components.chat.downloadfilecard.saved_to_the_folder_you_selected")}：${savedFile.filename}`
                    : `${t("src.components.chat.downloadfilecard.saved_to_app_sandbox")}：${savedFile.uri}`,
            );
            setTimeout(() => setSaved(false), 1600);
        } catch (error) {
            Alert.alert(t("src.components.chat.downloadfilecard.download_failed"), error instanceof Error ? error.message : t("src.components.chat.mermaidrenderer.unable_to_save_mermaid_chart"));
        }
    };

    const html = useMemo(() => {
        const background = themeMode === "dark" ? "#0d1117" : "#ffffff";
        const foreground = themeMode === "dark" ? "#e5e7eb" : "#111827";
        const mermaidTheme = themeMode === "dark" ? "dark" : "default";
        const sourceLiteral = scriptString(code);
        return `
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
    <style>
      html, body {
        margin: 0;
        padding: 0;
        background: ${background};
        color: ${foreground};
        overflow: auto;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      .wrap {
        padding: 16px;
        display: flex;
        justify-content: center;
      }
      .chart {
        transform: scale(${scale});
        transform-origin: center top;
      }
      pre {
        white-space: pre-wrap;
        word-break: break-word;
        font-size: 12px;
        line-height: 1.6;
      }
      .error {
        color: #ef4444;
      }
    </style>
  </head>
  <body>
    <div class="wrap">
      <div id="chart" class="chart"></div>
    </div>
    <script>
      const source = ${sourceLiteral};
      const cdns = [
        "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js",
        "https://unpkg.com/mermaid@11/dist/mermaid.min.js"
      ];

      function showError(error) {
        document.body.innerHTML = "";
        const pre = document.createElement("pre");
        pre.className = "error";
        pre.textContent = String(error || "Mermaid failed to render.") + "\\n\\n" + source;
        document.body.appendChild(pre);
        window.ReactNativeWebView?.postMessage(JSON.stringify({ type: "error", message: String(error || "") }));
      }

      function loadScript(index) {
        if (index >= cdns.length) {
          showError("Mermaid runtime failed to load. Please check the phone network or CDN access.");
          return;
        }
        const script = document.createElement("script");
        script.src = cdns[index];
        script.onload = render;
        script.onerror = () => loadScript(index + 1);
        document.head.appendChild(script);
      }

      function render() {
        try {
          if (!window.mermaid) {
            showError("Mermaid runtime is unavailable.");
            return;
          }
          window.mermaid.initialize({
            startOnLoad: false,
            theme: "${mermaidTheme}",
            securityLevel: "loose",
            fontFamily: "inherit"
          });
          window.mermaid.render("mermaid-chart-" + Date.now(), source)
            .then(({ svg }) => {
              document.getElementById("chart").innerHTML = svg;
              window.ReactNativeWebView?.postMessage(JSON.stringify({ type: "rendered", svg }));
            })
            .catch(showError);
        } catch (error) {
          showError(error);
        }
      }

      loadScript(0);
      setTimeout(() => {
        if (!document.getElementById("chart").innerHTML.trim() && !document.querySelector("pre.error")) {
          showError("Mermaid rendering timed out.");
        }
      }, 8000);
    </script>
  </body>
</html>`;
    }, [code, scale, themeMode]);

    return (
        <Card style={styles.card}>
            <View style={[styles.header, { borderBottomColor: colors.border, backgroundColor: colors.surfaceStrong }]}>
                <Text style={[styles.title, { color: colors.textMuted }]}>{t("src.components.chat.mermaidrenderer.mermaid_chart")}</Text>
                <View style={styles.actions}>
                    <Pressable
                        style={[styles.iconButton, { borderColor: colors.border, backgroundColor: colors.surface }]}
                        onPress={() => void handleDownload()}
                    >
                        <MaterialCommunityIcons name={saved ? "check" : "download"} size={16} color={saved ? colors.success : colors.textMuted} />
                    </Pressable>
                    <Pressable
                        style={[styles.iconButton, { borderColor: colors.border, backgroundColor: colors.surface }]}
                        onPress={() => setScale((value) => Math.max(0.5, Number((value - 0.1).toFixed(2))))}
                    >
                        <MaterialCommunityIcons name="magnify-minus-outline" size={16} color={colors.textMuted} />
                    </Pressable>
                    <Text style={[styles.scaleLabel, { color: colors.textMuted }]}>{Math.round(scale * 100)}%</Text>
                    <Pressable
                        style={[styles.iconButton, { borderColor: colors.border, backgroundColor: colors.surface }]}
                        onPress={() => setScale((value) => Math.min(2, Number((value + 0.1).toFixed(2))))}
                    >
                        <MaterialCommunityIcons name="magnify-plus-outline" size={16} color={colors.textMuted} />
                    </Pressable>
                </View>
            </View>
            <CardContent style={styles.content}>
                <View style={[styles.viewport, { backgroundColor: themeMode === "dark" ? "#0d1117" : "#ffffff" }]}>
                    <WebView
                        originWhitelist={["*"]}
                        source={{ html }}
                        style={styles.webview}
                        onMessage={(event) => {
                            try {
                                const payload = JSON.parse(String(event.nativeEvent.data || "{}"));
                                if (payload.type === "error") {
                                    setHasError(true);
                                    setRenderedSvg("");
                                } else if (payload.type === "rendered" && typeof payload.svg === "string") {
                                    setRenderedSvg(payload.svg);
                                    setHasError(false);
                                }
                            } catch {
                                setHasError(true);
                            }
                        }}
                    />
                </View>
                {hasError ? (
                    <View style={[styles.errorBox, { borderColor: colors.danger, backgroundColor: `${colors.danger}14` }]}>
                        <Text style={[styles.errorTitle, { color: colors.danger }]}>
                            {t("src.components.chat.mermaidrenderer.chart_rendering_failed_and_fell_back_to_raw_content")}
                        </Text>
                        <Text style={[styles.errorCode, { color: colors.textMuted }]}>{code}</Text>
                    </View>
                ) : null}
            </CardContent>
        </Card>
    );
});

const styles = StyleSheet.create({
    card: {
        borderRadius: 18,
        overflow: "hidden",
    },
    header: {
        minHeight: 42,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        paddingHorizontal: 12,
        paddingVertical: 8,
        borderBottomWidth: 1,
    },
    title: {
        fontSize: 12,
        fontWeight: "600",
    },
    actions: {
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
    },
    iconButton: {
        width: 28,
        height: 28,
        borderRadius: 14,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    scaleLabel: {
        width: 40,
        textAlign: "center",
        fontSize: 11,
        fontWeight: "600",
    },
    content: {
        gap: 10,
        paddingHorizontal: 0,
        paddingVertical: 0,
    },
    viewport: {
        minHeight: 220,
    },
    webview: {
        minHeight: 220,
        backgroundColor: "transparent",
    },
    errorBox: {
        marginHorizontal: 14,
        marginBottom: 14,
        borderWidth: 1,
        borderRadius: 14,
        paddingHorizontal: 12,
        paddingVertical: 10,
        gap: 8,
    },
    errorTitle: {
        fontSize: 12,
        fontWeight: "600",
    },
    errorCode: {
        fontSize: 11,
        lineHeight: 17,
        fontFamily: "monospace",
    },
});
