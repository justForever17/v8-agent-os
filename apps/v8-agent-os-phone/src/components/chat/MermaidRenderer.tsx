import { memo, useMemo, useState } from "react";
import {
    Pressable,
    StyleSheet,
    Text,
    View,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { WebView } from "react-native-webview";

import { Card, CardContent } from "@/src/components/ui/card";
import { useUiPrefs } from "@/src/providers/ui-prefs";

function escapeHtml(value: string) {
    return value
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
}

export const MermaidRenderer = memo(function MermaidRenderer({
    code,
}: {
    code: string;
}) {
    const { colors, themeMode, t } = useUiPrefs();
    const [scale, setScale] = useState(1);
    const [hasError, setHasError] = useState(false);

    const html = useMemo(() => {
        const safeCode = escapeHtml(code);
        const background = themeMode === "dark" ? "#0d1117" : "#ffffff";
        const foreground = themeMode === "dark" ? "#e5e7eb" : "#111827";
        const mermaidTheme = themeMode === "dark" ? "dark" : "default";
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
    </style>
    <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
  </head>
  <body>
    <div class="wrap">
      <div id="chart" class="chart"></div>
    </div>
    <script>
      const source = \`${safeCode}\`;
      mermaid.initialize({
        startOnLoad: false,
        theme: "${mermaidTheme}",
        securityLevel: "loose",
        fontFamily: "inherit"
      });

      mermaid.render("mermaid-chart", source)
        .then(({ svg }) => {
          document.getElementById("chart").innerHTML = svg;
        })
        .catch((error) => {
          document.body.innerHTML = "<pre>" + String(error) + "\\n\\n" + source + "</pre>";
          window.ReactNativeWebView?.postMessage(JSON.stringify({ type: "error" }));
        });
    </script>
  </body>
</html>`;
    }, [code, scale, themeMode]);

    return (
        <Card style={styles.card}>
            <View style={[styles.header, { borderBottomColor: colors.border, backgroundColor: colors.surfaceStrong }]}>
                <Text style={[styles.title, { color: colors.textMuted }]}>{t("Mermaid 图表", "Mermaid chart")}</Text>
                <View style={styles.actions}>
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
                            {t("图表渲染失败，已回退到原始内容。", "Chart rendering failed and fell back to raw content.")}
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
