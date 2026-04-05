import { memo, useMemo, useState } from "react";
import { Image, Pressable, StyleSheet, Text, View } from "react-native";
import { Linking } from "react-native";
import { WebView } from "react-native-webview";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { MediaViewerLightbox, type MediaItem } from "@/src/components/chat/MediaViewerLightbox";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

export const MediaPlayer = memo(function MediaPlayer({
    src,
    type,
    title,
}: {
    src: string;
    type: "video" | "audio";
    title?: string;
}) {
    const { colors, t } = useUiPrefs();
    const displayTitle = title || src.split("/").pop()?.split("?")[0] || t("媒体文件", "Media file");

    if (type === "audio") {
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
                <Pressable onPress={() => void Linking.openURL(src)} style={styles.openButton}>
                    <MaterialCommunityIcons name="open-in-new" size={16} color={colors.textSoft} />
                </Pressable>
            </View>
        );
    }

    return (
        <View style={[styles.videoWrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
            <WebView
                source={{ html: buildMediaHtml(src) }}
                style={styles.video}
                allowsInlineMediaPlayback
                mediaPlaybackRequiresUserAction={false}
            />
            <View style={[styles.videoFooter, { borderTopColor: colors.border }]}>
                <Text style={[styles.audioTitle, { color: colors.text }]} numberOfLines={1}>{displayTitle}</Text>
            </View>
        </View>
    );
});

export const ImagePreview = memo(function ImagePreview({
    src,
    alt,
}: {
    src: string;
    alt?: string;
}) {
    const { colors } = useUiPrefs();
    const [open, setOpen] = useState(false);
    const items = useMemo<MediaItem[]>(() => [{ type: "image", src, name: alt }], [alt, src]);

    return (
        <>
            <Pressable
                onPress={() => setOpen(true)}
                style={[styles.imageWrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}
            >
                <Image source={{ uri: src }} style={styles.imagePreview} resizeMode="cover" />
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
    <video src="${src}" controls playsinline preload="metadata"></video>
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
