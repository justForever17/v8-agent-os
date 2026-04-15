import { memo, useEffect, useState } from "react";
import { ActivityIndicator, Image, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { WebView } from "react-native-webview";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { usePreparedPhoneMediaSource } from "@/src/lib/phone-media-source";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

export type MediaItem = {
    type: "image" | "video";
    src: string;
    name?: string;
    candidates?: string[];
};

export const MediaViewerLightbox = memo(function MediaViewerLightbox({
    items,
    initialIndex = 0,
    isOpen,
    onClose,
}: {
    items: MediaItem[];
    initialIndex?: number;
    isOpen: boolean;
    onClose: () => void;
}) {
    const { colors, t } = useUiPrefs();
    const [currentIndex, setCurrentIndex] = useState(initialIndex);
    const boundedIndex = Math.min(Math.max(initialIndex, 0), Math.max(items.length - 1, 0));
    const clampedCurrentIndex = Math.min(Math.max(currentIndex, 0), Math.max(items.length - 1, 0));
    const current = items[clampedCurrentIndex] ?? items[0] ?? { type: "image" as const, src: "", name: "" };
    const {
        candidateSources,
        resolvedSrc,
        previewBlocked,
        loading,
        error,
        advanceCandidate,
    } = usePreparedPhoneMediaSource({
        src: current.src,
        candidates: current.candidates,
        title: current.name,
    });

    useEffect(() => {
        if (isOpen) {
            setCurrentIndex(boundedIndex);
        }
    }, [boundedIndex, isOpen]);

    if (!isOpen || items.length === 0) {
        return null;
    }

    return (
        <Modal transparent visible animationType="fade" onRequestClose={onClose}>
            <View style={styles.overlay}>
                <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
                <View style={styles.topBar}>
                    <Text style={styles.topBarText}>
                        {items.length > 1 ? `${clampedCurrentIndex + 1} / ${items.length}` : ""}
                        {current.name ? `  ${current.name}` : ""}
                    </Text>
                    <Pressable style={styles.iconButton} onPress={onClose}>
                        <MaterialCommunityIcons name="close" size={24} color="#FFFFFF" />
                    </Pressable>
                </View>

                <View style={styles.contentWrap}>
                    {loading ? (
                        <View style={styles.blockedWrap}>
                            <ActivityIndicator size="small" color={colors.primary} />
                            <Text style={styles.blockedTitle}>
                                {t("正在准备媒体内容", "Preparing media")}
                            </Text>
                            <Text style={styles.blockedText}>
                                {t("正在检查可预览的媒体地址。", "Checking for a previewable media URL.")}
                            </Text>
                        </View>
                    ) : current.type === "video" && !previewBlocked && resolvedSrc ? (
                        <WebView
                            source={{ html: buildMediaHtml(resolvedSrc, "video") }}
                            style={styles.webView}
                            allowsInlineMediaPlayback
                            mediaPlaybackRequiresUserAction={false}
                            allowFileAccess
                            allowFileAccessFromFileURLs
                            allowUniversalAccessFromFileURLs
                            onError={advanceCandidate}
                            onHttpError={advanceCandidate}
                        />
                    ) : current.type === "video" ? (
                        <View style={styles.blockedWrap}>
                            <MaterialCommunityIcons name="video-off-outline" size={36} color={colors.warning} />
                            <Text style={styles.blockedTitle}>
                                {t("视频预览不可达", "Video preview unavailable")}
                            </Text>
                            <Text style={styles.blockedText}>
                                {previewBlocked
                                    ? t("当前视频地址仍指向 localhost/127.0.0.1，手机端无法直接打开。", "The video URL still points to localhost/127.0.0.1, so the phone cannot open it directly.")
                                    : (error || t("当前视频内容暂不可用。", "The video content is currently unavailable."))}
                            </Text>
                        </View>
                    ) : resolvedSrc ? (
                        <Image
                            source={{ uri: resolvedSrc }}
                            style={styles.image}
                            resizeMode="contain"
                            onError={advanceCandidate}
                        />
                    ) : (
                        <View style={styles.blockedWrap}>
                            <MaterialCommunityIcons name="image-off-outline" size={36} color={colors.warning} />
                            <Text style={styles.blockedTitle}>
                                {t("图片预览不可达", "Image preview unavailable")}
                            </Text>
                            <Text style={styles.blockedText}>
                                {previewBlocked
                                    ? t("当前图片地址仍指向 localhost/127.0.0.1，手机端无法直接打开。", "The image URL still points to localhost/127.0.0.1, so the phone cannot open it directly.")
                                    : (error || t("当前图片内容暂不可用。", "The image content is currently unavailable."))}
                            </Text>
                        </View>
                    )}
                </View>

                {items.length > 1 ? (
                    <>
                        <Pressable
                            style={[styles.navButton, styles.leftButton, clampedCurrentIndex === 0 && styles.navButtonDisabled]}
                            disabled={clampedCurrentIndex === 0}
                            onPress={() => setCurrentIndex((value) => Math.max(0, value - 1))}
                        >
                            <MaterialCommunityIcons name="chevron-left" size={28} color={clampedCurrentIndex === 0 ? colors.textSoft : "#FFFFFF"} />
                        </Pressable>
                        <Pressable
                            style={[styles.navButton, styles.rightButton, clampedCurrentIndex >= items.length - 1 && styles.navButtonDisabled]}
                            disabled={clampedCurrentIndex >= items.length - 1}
                            onPress={() => setCurrentIndex((value) => Math.min(items.length - 1, value + 1))}
                        >
                            <MaterialCommunityIcons name="chevron-right" size={28} color={clampedCurrentIndex >= items.length - 1 ? colors.textSoft : "#FFFFFF"} />
                        </Pressable>
                    </>
                ) : null}
            </View>
        </Modal>
    );
});

function buildMediaHtml(src: string, type: "video" | "audio") {
    return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
    <style>
      html, body { margin:0; padding:0; background:#000; width:100%; height:100%; overflow:hidden; }
      body { display:flex; align-items:center; justify-content:center; }
      ${type} { width:100%; height:100%; max-width:100%; max-height:100%; }
    </style>
  </head>
  <body>
    <${type} id="player" controls ${type === "video" ? "autoplay playsinline" : "autoplay"} />
    <script>
      document.getElementById("player").src = ${JSON.stringify(src)};
    </script>
  </body>
</html>`;
}

const styles = StyleSheet.create({
    overlay: {
        flex: 1,
        backgroundColor: "rgba(0,0,0,0.88)",
        justifyContent: "center",
    },
    topBar: {
        position: "absolute",
        left: 0,
        right: 0,
        top: 0,
        zIndex: 10,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        paddingHorizontal: spacing.lg,
        paddingVertical: spacing.md,
    },
    topBarText: {
        color: "#FFFFFF",
        fontSize: 13,
        lineHeight: 18,
        fontWeight: "500",
    },
    iconButton: {
        width: 40,
        height: 40,
        borderRadius: 20,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(255,255,255,0.12)",
    },
    contentWrap: {
        marginHorizontal: spacing.lg,
        minHeight: 260,
        borderRadius: radii.lg,
        overflow: "hidden",
    },
    image: {
        width: "100%",
        height: 420,
    },
    webView: {
        width: "100%",
        height: 420,
        backgroundColor: "#000000",
    },
    blockedWrap: {
        width: "100%",
        height: 420,
        alignItems: "center",
        justifyContent: "center",
        gap: spacing.sm,
        paddingHorizontal: spacing.xl,
    },
    blockedTitle: {
        color: "#FFFFFF",
        fontSize: 16,
        fontWeight: "800",
        textAlign: "center",
    },
    blockedText: {
        color: "rgba(255,255,255,0.78)",
        fontSize: 12,
        lineHeight: 18,
        textAlign: "center",
    },
    navButton: {
        position: "absolute",
        top: "50%",
        marginTop: -24,
        width: 48,
        height: 48,
        borderRadius: 24,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(255,255,255,0.12)",
    },
    leftButton: {
        left: spacing.md,
    },
    rightButton: {
        right: spacing.md,
    },
    navButtonDisabled: {
        opacity: 0.35,
    },
});
