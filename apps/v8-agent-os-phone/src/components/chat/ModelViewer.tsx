import { memo, useState } from "react";
import { Alert, Pressable, StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { WebView } from "react-native-webview";

import { Card } from "@/src/components/ui/card";
import { downloadUrlToUserSelectedFile } from "@/src/lib/file-transfer";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";

function scriptString(value: string) {
    return JSON.stringify(value).replace(/<\/script/gi, "<\\/script");
}

export const ModelViewer = memo(function ModelViewer({
    src,
    filename,
}: {
    src: string;
    filename?: string;
}) {
    const { colors, themeMode, t } = useUiPrefs();
    const { adminBaseUrl, authorizedFetch } = useAppSession();
    const [previewFailed, setPreviewFailed] = useState(false);
    const background = themeMode === "dark" ? "#0f172a" : "#f8fafc";
    const labelBackground = themeMode === "dark" ? "rgba(15,23,42,0.78)" : "rgba(255,255,255,0.82)";
    const labelText = themeMode === "dark" ? "#E2E8F0" : "#334155";
    const modelSrcLiteral = scriptString(src);
    const displayFilename = filename || "model.glb";
    const handleDownload = async () => {
        try {
            const saved = await downloadUrlToUserSelectedFile(src, {
                filename: displayFilename,
                mimeType: "model/gltf-binary",
                prefix: "model",
                adminBaseUrl,
                authorizedFetch,
            });
            Alert.alert(
                t("src.components.chat.downloadfilecard.downloaded"),
                saved.shared
                    ? `${t("src.components.chat.downloadfilecard.opened_the_system_share_save_to_files_sheet")}：${saved.filename}`
                    : saved.userVisible
                    ? `${t("src.components.chat.downloadfilecard.saved_to_the_folder_you_selected")}：${saved.filename}`
                    : `${t("src.components.chat.downloadfilecard.saved_to_app_sandbox")}：${saved.uri}`,
            );
        } catch (error) {
            Alert.alert(t("src.components.chat.downloadfilecard.download_failed"), error instanceof Error ? error.message : t("src.components.chat.modelviewer.unable_to_save_3d_file"));
        }
    };
    const html = `
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
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
      id="viewer"
      camera-controls
      auto-rotate
      shadow-intensity="1"
      exposure="1"
      ar="false"
      touch-action="pan-y"
    ></model-viewer>
    <script type="module">
      const modelSrc = ${modelSrcLiteral};
      const viewer = document.getElementById("viewer");
      const cdns = [
        "https://unpkg.com/@google/model-viewer/dist/model-viewer.min.js",
        "https://cdn.jsdelivr.net/npm/@google/model-viewer/dist/model-viewer.min.js"
      ];

      function reportError(message) {
        window.ReactNativeWebView?.postMessage(JSON.stringify({ type: "error", message: String(message || "") }));
      }

      async function loadRuntime() {
        let lastError = null;
        for (const url of cdns) {
          try {
            await import(url);
            return;
          } catch (error) {
            lastError = error;
          }
        }
        throw lastError || new Error("model-viewer runtime failed to load");
      }

      viewer.addEventListener("error", (event) => reportError(event?.detail?.message || "3D model failed to load"));
      loadRuntime()
        .then(() => {
          viewer.src = modelSrc;
        })
        .catch((error) => reportError(error?.message || error));

      setTimeout(() => {
        if (!customElements.get("model-viewer")) {
          reportError("model-viewer runtime timed out");
        }
      }, 8000);
    </script>
  </body>
</html>`;

    return (
        <Card style={[styles.card, { backgroundColor: background, borderColor: colors.border }]}>
            <View style={styles.viewerWrap}>
                <WebView
                    originWhitelist={["*"]}
                    source={{ html }}
                    style={styles.webview}
                    onError={() => setPreviewFailed(true)}
                    onHttpError={() => setPreviewFailed(true)}
                    onMessage={(event) => {
                        try {
                            const payload = JSON.parse(String(event.nativeEvent.data || "{}"));
                            if (payload.type === "error") {
                                setPreviewFailed(true);
                            }
                        } catch {
                            setPreviewFailed(true);
                        }
                    }}
                />
                {previewFailed ? (
                    <View style={[styles.failedOverlay, { backgroundColor: labelBackground }]}>
                        <MaterialCommunityIcons name="cube-off-outline" size={22} color={colors.warning} />
                        <Text style={[styles.failedTitle, { color: labelText }]}>
                            {t("src.components.chat.modelviewer.3d_preview_unavailable")}
                        </Text>
                        <Text style={[styles.failedText, { color: labelText }]}>
                            {t("src.components.chat.modelviewer.the_model_url_phone_network_or_model_viewer_runtime_may_be_unavailable_download_still_works")}
                        </Text>
                    </View>
                ) : null}
                <View style={[styles.label, { backgroundColor: labelBackground, borderColor: colors.border }]}>
                    <MaterialCommunityIcons name="cube-outline" size={12} color={labelText} />
                    <Text style={[styles.labelText, { color: labelText }]} numberOfLines={1}>
                        {displayFilename || t("src.components.chat.modelviewer.3d_preview")}
                    </Text>
                </View>
                <View style={[styles.downloadWrap, { backgroundColor: labelBackground, borderColor: colors.border }]}>
                    <Pressable
                        accessibilityRole="button"
                        accessibilityLabel={t("src.components.chat.modelviewer.download_3d_file")}
                        hitSlop={8}
                        onPress={() => void handleDownload()}
                        style={styles.downloadButton}
                    >
                        <MaterialCommunityIcons name="download" size={15} color={labelText} />
                    </Pressable>
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
    failedOverlay: {
        position: "absolute",
        left: 12,
        right: 12,
        top: 48,
        bottom: 12,
        borderRadius: 14,
        alignItems: "center",
        justifyContent: "center",
        gap: 6,
        paddingHorizontal: 14,
    },
    failedTitle: {
        fontSize: 12,
        fontWeight: "800",
    },
    failedText: {
        fontSize: 10,
        lineHeight: 14,
        textAlign: "center",
        opacity: 0.82,
    },
    label: {
        position: "absolute",
        top: 8,
        left: 8,
        right: 46,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        borderWidth: 1,
        borderRadius: 999,
        paddingHorizontal: 8,
        paddingVertical: 5,
    },
    labelText: {
        flexShrink: 1,
        fontSize: 10,
        fontWeight: "700",
        letterSpacing: 0.6,
        textTransform: "uppercase",
    },
    downloadWrap: {
        position: "absolute",
        top: 8,
        right: 8,
        borderWidth: 1,
        borderRadius: 999,
    },
    downloadButton: {
        width: 30,
        height: 30,
        alignItems: "center",
        justifyContent: "center",
    },
});
