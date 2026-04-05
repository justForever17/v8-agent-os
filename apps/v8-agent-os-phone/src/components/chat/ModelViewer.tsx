import { memo } from "react";
import { StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { WebView } from "react-native-webview";

import { Card } from "@/src/components/ui/card";
import { useUiPrefs } from "@/src/providers/ui-prefs";

function escapeHtml(value: string) {
    return value
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;");
}

export const ModelViewer = memo(function ModelViewer({
    src,
}: {
    src: string;
}) {
    const { colors, themeMode, t } = useUiPrefs();
    const background = themeMode === "dark" ? "#0f172a" : "#f8fafc";
    const labelBackground = themeMode === "dark" ? "rgba(15,23,42,0.78)" : "rgba(255,255,255,0.82)";
    const labelText = themeMode === "dark" ? "#E2E8F0" : "#334155";
    const safeSrc = escapeHtml(src);
    const html = `
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
    <script type="module" src="https://unpkg.com/@google/model-viewer/dist/model-viewer.min.js"></script>
    <style>
      html, body {
        margin: 0;
        padding: 0;
        width: 100%;
        height: 100%;
        overflow: hidden;
        background: ${background};
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      model-viewer {
        width: 100%;
        height: 100%;
        background: ${background};
      }
    </style>
  </head>
  <body>
    <model-viewer
      src="${safeSrc}"
      camera-controls
      auto-rotate
      shadow-intensity="1"
      exposure="1"
      ar="false"
      touch-action="pan-y"
    ></model-viewer>
  </body>
</html>`;

    return (
        <Card style={[styles.card, { backgroundColor: background, borderColor: colors.border }]}>
            <View style={styles.viewerWrap}>
                <WebView
                    originWhitelist={["*"]}
                    source={{ html }}
                    style={styles.webview}
                />
                <View style={[styles.label, { backgroundColor: labelBackground, borderColor: colors.border }]}>
                    <MaterialCommunityIcons name="cube-outline" size={12} color={labelText} />
                    <Text style={[styles.labelText, { color: labelText }]}>{t("3D 预览", "3D preview")}</Text>
                </View>
            </View>
        </Card>
    );
});

const styles = StyleSheet.create({
    card: {
        borderRadius: 16,
        overflow: "hidden",
        borderWidth: 1,
    },
    viewerWrap: {
        position: "relative",
        width: "100%",
        aspectRatio: 16 / 9,
    },
    webview: {
        flex: 1,
        backgroundColor: "transparent",
    },
    label: {
        position: "absolute",
        top: 8,
        right: 8,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        borderWidth: 1,
        borderRadius: 999,
        paddingHorizontal: 8,
        paddingVertical: 5,
    },
    labelText: {
        fontSize: 10,
        fontWeight: "700",
        letterSpacing: 0.6,
        textTransform: "uppercase",
    },
});
