import { memo, useState } from "react";
import { ActivityIndicator, Image, Pressable, StyleSheet, Text, View } from "react-native";
import { Linking } from "react-native";
import { WebView } from "react-native-webview";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { MediaViewerLightbox, type MediaItem } from "@/src/components/chat/MediaViewerLightbox";
import { usePreparedPhoneMediaSource } from "@/src/lib/phone-media-source";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

export const MediaPlayer = memo(function MediaPlayer({
    src,
    type,
    title,
    candidates,
}: {
    src: string;
    type: "video" | "audio";
    title?: string;
    candidates?: string[];
}) {
    const { colors, t } = useUiPrefs();
    const displayTitle = title || src.split("/").pop()?.split("?")[0] || t("媒体文件", "Media file");
    const {
        candidateSources,
        resolvedSrc,
        previewBlocked,
        loading,
        error,
        advanceCandidate,
    } = usePreparedPhoneMediaSource({ src, candidates, title: displayTitle });
    const [open, setOpen] = useState(false);
    const items: MediaItem[] = type === "video" && resolvedSrc
        ? [{ type: "video", src: resolvedSrc, name: displayTitle, candidates: candidateSources }]
        : [];

    if (type === "audio") {
        if (previewBlocked || !resolvedSrc) {
            return (
                <View style={[styles.videoWrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                    <View style={styles.blockedWrap}>
                        <MaterialCommunityIcons name="music-off" size={28} color={colors.warning} />
                        <Text style={[styles.audioTitle, { color: colors.text }]} numberOfLines={1}>{displayTitle}</Text>
                        <Text style={[styles.blockedText, { color: colors.textMuted }]}>
                            {previewBlocked
                                ? t("当前音频预览地址仍是本机回环地址，手机端无法直接访问。请改用可达的 Admin 地址后重试。", "The audio preview URL still points to localhost, which is unreachable from the phone. Use a reachable Admin URL and try again.")
                                : (error || t("当前音频内容暂不可达。", "The audio content is currently unavailable."))}
                        </Text>
                    </View>
                </View>
            );
        }
        return (
            <View style={[styles.audioCard, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                <View style={[styles.audioIcon, { backgroundColor: colors.primarySoft }]}>
                    <MaterialCommunityIcons name="music-note-outline" size={18} color={colors.primaryDeep} />
                </View>
                <View style={styles.audioBody}>
                    <Text style={[styles.audioTitle, { color: colors.text }]} numberOfLines={1}>{displayTitle}</Text>
                    <Text style={[styles.audioSubtitle, { color: colors.textMuted }]} numberOfLines={1}>
                        {t("点击打开音频", "Tap to open audio")}
                    </Text>
                </View>
                <Pressable onPress={() => void Linking.openURL(resolvedSrc)} style={styles.openButton}>
                    <MaterialCommunityIcons name="open-in-new" size={16} color={colors.textSoft} />
                </Pressable>
            </View>
        );
    }

    if (loading) {
        return (
            <View style={[styles.videoWrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                <View style={styles.loadingWrap}>
                    <ActivityIndicator size="small" color={colors.primary} />
                    <Text style={[styles.audioTitle, { color: colors.text }]} numberOfLines={1}>{displayTitle}</Text>
                    <Text style={[styles.blockedText, { color: colors.textMuted }]}>
                        {t("正在准备媒体内容…", "Preparing media content...")}
                    </Text>
                </View>
            </View>
        );
    }

    if (previewBlocked) {
        return (
            <View style={[styles.videoWrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                <View style={styles.blockedWrap}>
                    <MaterialCommunityIcons name="video-off-outline" size={28} color={colors.warning} />
                    <Text style={[styles.audioTitle, { color: colors.text }]} numberOfLines={1}>{displayTitle}</Text>
                    <Text style={[styles.blockedText, { color: colors.textMuted }]}>
                        {t("当前视频预览地址仍是本机回环地址，手机端无法直接访问。请改用可达的 Admin 地址后重试。", "The preview URL still points to localhost, which is unreachable from the phone. Use a reachable Admin URL and try again.")}
                    </Text>
                </View>
            </View>
        );
    }

    if (!resolvedSrc) {
        return (
            <View style={[styles.videoWrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                <View style={styles.blockedWrap}>
                    <MaterialCommunityIcons name="video-off-outline" size={28} color={colors.warning} />
                    <Text style={[styles.audioTitle, { color: colors.text }]} numberOfLines={1}>{displayTitle}</Text>
                    <Text style={[styles.blockedText, { color: colors.textMuted }]}>
                        {error || t("当前媒体内容暂不可达。", "The media content is currently unavailable.")}
                    </Text>
                </View>
            </View>
        );
    }

    return (
        <>
            <Pressable onPress={() => setOpen(true)}>
                <View style={[styles.videoWrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                    <WebView
                        source={{ html: buildMediaHtml(resolvedSrc) }}
                        style={styles.video}
                        allowsInlineMediaPlayback
                        mediaPlaybackRequiresUserAction={false}
                        allowFileAccess
                        allowFileAccessFromFileURLs
                        allowUniversalAccessFromFileURLs
                        onError={advanceCandidate}
                        onHttpError={advanceCandidate}
                    />
                    <View style={[styles.videoFooter, { borderTopColor: colors.border }]}>
                        <Text style={[styles.audioTitle, { color: colors.text }]} numberOfLines={1}>{displayTitle}</Text>
                    </View>
                </View>
            </Pressable>
            <MediaViewerLightbox items={items} isOpen={open} onClose={() => setOpen(false)} />
        </>
    );
});

export const ImagePreview = memo(function ImagePreview({
    src,
    alt,
    candidates,
}: {
    src: string;
    alt?: string;
    candidates?: string[];
}) {
    const { colors, t } = useUiPrefs();
    const [open, setOpen] = useState(false);
    const { candidateSources, resolvedSrc, previewBlocked, loading, error, advanceCandidate } = usePreparedPhoneMediaSource({
        src,
        candidates,
        title: alt,
    });
    const items: MediaItem[] = resolvedSrc
        ? [{ type: "image", src: resolvedSrc, name: alt, candidates: candidateSources }]
        : [];

    if (loading) {
        return (
            <View style={[styles.imageWrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                <View style={styles.loadingWrap}>
                    <ActivityIndicator size="small" color={colors.primary} />
                    <Text style={[styles.audioSubtitle, { color: colors.textMuted }]}>
                        {t("正在准备图片内容…", "Preparing image content...")}
                    </Text>
                </View>
            </View>
        );
    }

    if (!resolvedSrc) {
        return (
            <View style={[styles.imageWrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                <View style={styles.loadingWrap}>
                    <MaterialCommunityIcons name="image-off-outline" size={22} color={colors.warning} />
                    <Text style={[styles.audioSubtitle, { color: colors.textMuted }]}>
                        {previewBlocked
                            ? t("当前图片预览地址仍是本机回环地址，手机端无法直接访问。请改用可达的 Admin 地址后重试。", "The image preview URL still points to localhost, which is unreachable from the phone. Use a reachable Admin URL and try again.")
                            : (error || t("当前图片内容暂不可达。", "The image content is currently unavailable."))}
                    </Text>
                </View>
            </View>
        );
    }

    return (
        <>
            <Pressable
                onPress={() => setOpen(true)}
                style={[styles.imageWrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}
            >
                <Image source={{ uri: resolvedSrc }} style={styles.imagePreview} resizeMode="cover" onError={advanceCandidate} />
                <View style={styles.imageOverlay}>
                    <MaterialCommunityIcons name="magnify-plus-outline" size={22} color="#FFFFFF" />
                </View>
            </Pressable>
            <MediaViewerLightbox items={items} isOpen={open} onClose={() => setOpen(false)} />
        </>
    );
});

function buildMediaHtml(src: string) {
    return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
    <style>
      html, body { margin:0; padding:0; width:100%; height:100%; overflow:hidden; background:#000; }
      video { width:100%; height:100%; object-fit:cover; }
    </style>
  </head>
  <body>
    <video id="player" controls playsinline preload="metadata"></video>
    <script>
      document.getElementById("player").src = ${JSON.stringify(src)};
    </script>
  </body>
</html>`;
}

const styles = StyleSheet.create({
    audioCard: {
        borderWidth: 1,
        borderRadius: radii.lg,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.sm,
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
    },
    audioIcon: {
        width: 40,
        height: 40,
        borderRadius: 20,
        alignItems: "center",
        justifyContent: "center",
    },
    audioBody: {
        flex: 1,
        gap: 2,
    },
    audioTitle: {
        fontSize: 13,
        lineHeight: 18,
        fontWeight: "700",
    },
    audioSubtitle: {
        fontSize: 11,
        lineHeight: 16,
    },
    openButton: {
        width: 32,
        height: 32,
        alignItems: "center",
        justifyContent: "center",
    },
    videoWrap: {
        overflow: "hidden",
        borderWidth: 1,
        borderRadius: radii.lg,
    },
    video: {
        width: "100%",
        height: 200,
        backgroundColor: "#000000",
    },
    videoFooter: {
        borderTopWidth: 1,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.sm,
    },
    blockedWrap: {
        minHeight: 200,
        alignItems: "center",
        justifyContent: "center",
        gap: spacing.sm,
        paddingHorizontal: spacing.lg,
        paddingVertical: spacing.lg,
    },
    blockedText: {
        fontSize: 12,
        lineHeight: 18,
        textAlign: "center",
    },
    loadingWrap: {
        minHeight: 160,
        alignItems: "center",
        justifyContent: "center",
        gap: spacing.sm,
        paddingHorizontal: spacing.lg,
        paddingVertical: spacing.lg,
    },
    imageWrap: {
        position: "relative",
        overflow: "hidden",
        borderWidth: 1,
        borderRadius: radii.lg,
    },
    imagePreview: {
        width: "100%",
        height: 220,
    },
    imageOverlay: {
        position: "absolute",
        right: spacing.sm,
        bottom: spacing.sm,
        width: 36,
        height: 36,
        borderRadius: 18,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(15,23,42,0.44)",
    },
});
