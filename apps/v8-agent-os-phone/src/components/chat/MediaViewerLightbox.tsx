import { memo, useEffect, useState } from "react";
import { ActivityIndicator, Alert, Image, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { WebView } from "react-native-webview";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import * as Clipboard from "expo-clipboard";

import { downloadUrlToUserSelectedFile } from "@/src/lib/file-transfer";
import { usePreparedPhoneMediaSource } from "@/src/lib/phone-media-source";
import { useAppSession } from "@/src/providers/app-session";
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
    const { adminBaseUrl, authorizedFetch } = useAppSession();
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

    const openMediaMenu = () => {
        const url = resolvedSrc || current.src;
        Alert.alert(
            t("src.components.chat.mediaviewerlightbox.media_actions"),
            current.name || url,
            [
                {
                    text: t("src.components.chat.mediaviewerlightbox.save_to_folder"),
                    onPress: () => {
                        void (async () => {
                            if (!url) {
                                throw new Error(t("src.components.chat.mediaviewerlightbox.no_media_url_is_available_to_save"));
                            }
                            const saved = await downloadUrlToUserSelectedFile(url, {
                                prefix: "media",
                                filename: current.name,
                                adminBaseUrl,
                                authorizedFetch,
                            });
                            Alert.alert(
                                t("src.components.chat.mediaviewerlightbox.saved"),
                                saved.shared
                                    ? `${t("src.components.chat.downloadfilecard.opened_the_system_share_save_to_files_sheet")}：${saved.filename}`
                                    : saved.userVisible
                                    ? `${t("src.components.chat.downloadfilecard.saved_to_the_folder_you_selected")}：${saved.filename}`
                                    : `${t("src.components.chat.downloadfilecard.saved_to_app_sandbox")}：${saved.uri}`,
                            );
                        })().catch((error) => {
                            Alert.alert(t("src.components.chat.mediaviewerlightbox.save_failed"), error instanceof Error ? error.message : t("src.components.chat.mediaviewerlightbox.unable_to_save_media"));
                        });
                    },
                },
                {
                    text: t("src.components.chat.mediaviewerlightbox.copy_link"),
                    onPress: () => {
                        void Clipboard.setStringAsync(url || "");
                    },
                },
                { text: t("src.components.chat.mediaviewerlightbox.cancel"), style: "cancel" },
            ],
        );
    };

    return (
        <Modal transparent visible animationType="fade" onRequestClose={onClose}>
            <View style={styles.overlay}>
                <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
                <View style={styles.topBar}>
                    <Text style={styles.topBarText}>
                        {items.length > 1 ? `${clampedCurrentIndex + 1} / ${items.length}` : ""}
                        {current.name ? `  ${current.name}` : ""}
                    </Text>
                    <View style={styles.topActions}>
                        <Pressable style={styles.iconButton} onPress={openMediaMenu}>
                            <MaterialCommunityIcons name="dots-vertical" size={22} color="#FFFFFF" />
                        </Pressable>
                        <Pressable style={styles.iconButton} onPress={onClose}>
                            <MaterialCommunityIcons name="close" size={24} color="#FFFFFF" />
                        </Pressable>
                    </View>
                </View>

                <Pressable style={styles.contentWrap} onLongPress={openMediaMenu}>
                    {loading ? (
                        <View style={styles.blockedWrap}>
                            <ActivityIndicator size="small" color={colors.primary} />
                            <Text style={styles.blockedTitle}>
                                {t("src.components.chat.mediaviewerlightbox.preparing_media")}
                            </Text>
                            <Text style={styles.blockedText}>
                                {t("src.components.chat.mediaviewerlightbox.checking_for_a_previewable_media_url")}
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
                                {t("src.components.chat.mediaviewerlightbox.video_preview_unavailable")}
                            </Text>
                            <Text style={styles.blockedText}>
                                {previewBlocked
                                    ? t("src.components.chat.mediaviewerlightbox.the_video_url_still_points_to_localhost_127_0_0_1_so_the_phone_cannot_open_it_directly")
                                    : (error || t("src.components.chat.mediaviewerlightbox.the_video_content_is_currently_unavailable"))}
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
                                {t("src.components.chat.mediaviewerlightbox.image_preview_unavailable")}
                            </Text>
                            <Text style={styles.blockedText}>
                                {previewBlocked
                                    ? t("src.components.chat.mediaviewerlightbox.the_image_url_still_points_to_localhost_127_0_0_1_so_the_phone_cannot_open_it_directly")
                                    : (error || t("src.components.chat.mediaviewerlightbox.the_image_content_is_currently_unavailable"))}
                            </Text>
                        </View>
                    )}
                </Pressable>

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
        flex: 1,
        marginRight: spacing.sm,
    },
    topActions: {
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
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
