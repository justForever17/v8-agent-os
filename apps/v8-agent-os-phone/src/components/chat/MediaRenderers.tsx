import { memo, useEffect, useState } from "react";
import { ActivityIndicator, Image, Pressable, StyleSheet, Text, View } from "react-native";
import { WebView } from "react-native-webview";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useAudioPlayer, useAudioPlayerStatus } from "expo-audio";

import { MediaViewerLightbox, type MediaItem } from "@/src/components/chat/MediaViewerLightbox";
import { usePreparedPhoneMediaSource } from "@/src/lib/phone-media-source";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

const InlineAudioPlayback = memo(function InlineAudioPlayback({
    src,
    title,
    isMusic,
}: {
    src: string;
    title: string;
    isMusic: boolean;
}) {
    const { colors, t } = useUiPrefs();
    const player = useAudioPlayer();
    const status = useAudioPlayerStatus(player);
    useEffect(() => {
        player.pause();
        player.replace({ uri: src });
        return () => player.pause();
    }, [player, src]);
    const duration = Number(status.duration || 0);
    const currentTime = Number(status.currentTime || 0);
    const progress = duration > 0 ? Math.max(0, Math.min(100, currentTime / duration * 100)) : 0;
    return (
        <View style={[styles.audioCard, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
            <Pressable
                accessibilityRole="button"
                onPress={() => {
                    if (status.playing) player.pause();
                    else player.play();
                }}
                style={[styles.audioIcon, { backgroundColor: colors.primarySoft }]}
            >
                <MaterialCommunityIcons name={status.playing ? "pause" : "play"} size={20} color={colors.primaryDeep} />
            </Pressable>
            <View style={styles.audioBody}>
                <Text style={[styles.audioTitle, { color: colors.text }]} numberOfLines={1}>{title}</Text>
                <View style={[styles.audioTrack, { backgroundColor: colors.border }]}>
                    <View style={[styles.audioTrackProgress, { backgroundColor: colors.primary, width: `${progress}%` }]} />
                </View>
                <Text style={[styles.audioSubtitle, { color: colors.textMuted }]} numberOfLines={1}>
                    {isMusic ? t("src.components.chat.mediarenderers.tap_to_open_music_artifact") : t("src.components.chat.mediarenderers.tap_to_open_audio")}
                </Text>
            </View>
            <Pressable
                onPress={() => {
                    const seekable = player as typeof player & { seekTo?: (position: number) => void };
                    seekable.seekTo?.(0);
                }}
                style={styles.openButton}
            >
                <MaterialCommunityIcons name="replay" size={16} color={colors.textSoft} />
            </Pressable>
        </View>
    );
});

export const MediaPlayer = memo(function MediaPlayer({
    src,
    type,
    title,
    candidates,
    variant = "audio",
}: {
    src: string;
    type: "video" | "audio";
    title?: string;
    candidates?: string[];
    variant?: "audio" | "music";
}) {
    const { colors, t } = useUiPrefs();
    const displayTitle = title || src.split("/").pop()?.split("?")[0] || t("src.components.chat.mediarenderers.media_file");
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
        const isMusic = variant === "music";
        if (previewBlocked || !resolvedSrc) {
            return (
                <View style={[styles.videoWrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                    <View style={styles.blockedWrap}>
                        <MaterialCommunityIcons name={isMusic ? "album" : "music-off"} size={28} color={colors.warning} />
                        <Text style={[styles.audioTitle, { color: colors.text }]} numberOfLines={1}>{displayTitle}</Text>
                        <Text style={[styles.blockedText, { color: colors.textMuted }]}>
                            {previewBlocked
                                ? t("src.components.chat.mediarenderers.the_audio_preview_url_still_points_to_localhost_which_is_unreachable_from_the_phone_use_a_reachable_admin_url_and_try_again")
                                : (error || t("src.components.chat.mediarenderers.the_audio_content_is_currently_unavailable"))}
                        </Text>
                    </View>
                </View>
            );
        }
        return <InlineAudioPlayback src={resolvedSrc} title={displayTitle} isMusic={isMusic} />;
    }

    if (loading) {
        return (
            <View style={[styles.videoWrap, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                <View style={styles.loadingWrap}>
                    <ActivityIndicator size="small" color={colors.primary} />
                    <Text style={[styles.audioTitle, { color: colors.text }]} numberOfLines={1}>{displayTitle}</Text>
                    <Text style={[styles.blockedText, { color: colors.textMuted }]}>
                        {t("src.components.chat.mediarenderers.preparing_media_content")}
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
                        {t("src.components.chat.mediarenderers.the_preview_url_still_points_to_localhost_which_is_unreachable_from_the_phone_use_a_reachable_admin_url_and_try_again")}
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
                        {error || t("src.components.chat.mediarenderers.the_media_content_is_currently_unavailable")}
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
                        {t("src.components.chat.mediarenderers.preparing_image_content")}
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
                            ? t("src.components.chat.mediarenderers.the_image_preview_url_still_points_to_localhost_which_is_unreachable_from_the_phone_use_a_reachable_admin_url_and_try_again")
                            : (error || t("src.components.chat.mediarenderers.the_image_content_is_currently_unavailable"))}
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
    audioTrack: {
        height: 3,
        borderRadius: 999,
        overflow: "hidden",
    },
    audioTrackProgress: {
        height: "100%",
        borderRadius: 999,
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
