import { useCallback, useEffect, useMemo, useState } from "react";
import { LayoutChangeEvent, Pressable, StyleSheet, Text, View } from "react-native";
import { useAudioPlayer, useAudioPlayerStatus } from "expo-audio";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { resolveAdminAssetUrl } from "@/src/lib/admin-client";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";
import type { MusicTrack } from "@/src/types/admin";

function clamp(value: number, min: number, max: number) {
    return Math.min(max, Math.max(min, value));
}

export function MusicShelf({
    adminBaseUrl,
    tracks,
}: {
    adminBaseUrl: string;
    tracks: MusicTrack[];
}) {
    const { colors: palette, t } = useUiPrefs();
    const player = useAudioPlayer();
    const playerStatus = useAudioPlayerStatus(player);
    const [currentTrackIndex, setCurrentTrackIndex] = useState(0);
    const [volume, setVolume] = useState(0.7);
    const [muted, setMuted] = useState(false);
    const [trackWidth, setTrackWidth] = useState(0);

    const safeTracks = useMemo(() => tracks.filter((track) => String(track.url || "").trim()), [tracks]);
    const currentTrack = safeTracks[currentTrackIndex] || null;

    useEffect(() => {
        if (!currentTrack && currentTrackIndex !== 0) {
            setCurrentTrackIndex(0);
        }
    }, [currentTrack, currentTrackIndex]);

    useEffect(() => {
        player.volume = muted ? 0 : volume;
    }, [muted, player, volume]);

    useEffect(() => {
        if (!playerStatus.didJustFinish || safeTracks.length <= 1) {
            return;
        }
        setCurrentTrackIndex((current) => (current + 1) % safeTracks.length);
    }, [playerStatus.didJustFinish, safeTracks.length]);

    useEffect(() => {
        return () => {
            try {
                player.pause();
            } catch {
                // ignore cleanup failure
            }
        };
    }, [player]);

    const playTrack = useCallback((index: number) => {
        const nextTrack = safeTracks[index];
        if (!nextTrack) return;
        const source = resolveAdminAssetUrl(adminBaseUrl, nextTrack.url || "");
        if (!source) return;

        setCurrentTrackIndex(index);
        player.replace({ uri: source });
        player.play();
    }, [adminBaseUrl, player, safeTracks]);

    const togglePlayback = useCallback(() => {
        if (!currentTrack && safeTracks.length > 0) {
            playTrack(currentTrackIndex);
            return;
        }
        if (playerStatus.playing) {
            player.pause();
            return;
        }
        if (currentTrack) {
            if (!playerStatus.isLoaded) {
                playTrack(currentTrackIndex);
                return;
            }
            player.play();
        }
    }, [currentTrack, currentTrackIndex, playTrack, player, playerStatus.isLoaded, playerStatus.playing, safeTracks.length]);

    const stepTrack = useCallback((direction: -1 | 1) => {
        if (safeTracks.length === 0) return;
        const nextIndex = (currentTrackIndex + direction + safeTracks.length) % safeTracks.length;
        playTrack(nextIndex);
    }, [currentTrackIndex, playTrack, safeTracks.length]);

    const handleVolumeTrackLayout = useCallback((event: LayoutChangeEvent) => {
        setTrackWidth(event.nativeEvent.layout.width);
    }, []);

    const handleVolumePress = useCallback((locationX: number) => {
        if (!trackWidth) return;
        const next = clamp(locationX / trackWidth, 0, 1);
        setMuted(next <= 0.01);
        setVolume(next);
    }, [trackWidth]);

    if (!currentTrack) {
        return (
            <View style={styles.emptyShell}>
                <Text style={[styles.emptyText, { color: palette.textMuted }]}>
                    {t("src.components.layout.musicshelf.no_tracks_available")}
                </Text>
            </View>
        );
    }

    const activeVolume = muted ? 0 : volume;

    return (
        <View style={[styles.card, { backgroundColor: palette.surfaceStrong, borderColor: palette.border }]}>
            <View style={styles.header}>
                <View style={[styles.coverTile, { backgroundColor: palette.primarySoft, borderColor: `${palette.primary}22` }]}>
                    <MaterialCommunityIcons name="music-note-outline" size={20} color={palette.primary} />
                </View>
                <View style={styles.meta}>
                    <Text style={[styles.title, { color: palette.text }]} numberOfLines={1}>
                        {currentTrack.title || t("src.components.layout.musicshelf.untitled_track")}
                    </Text>
                    <Text style={[styles.nowPlaying, { color: palette.textMuted }]}>
                        {playerStatus.playing ? t("src.components.layout.musicshelf.now_playing") : t("src.components.layout.musicshelf.ready")}
                    </Text>
                </View>
                <Pressable
                    onPress={() => setMuted((current) => !current)}
                    style={styles.muteButton}
                >
                    <MaterialCommunityIcons
                        name={muted || activeVolume <= 0.01 ? "volume-off" : "volume-high"}
                        size={18}
                        color={palette.textMuted}
                    />
                </Pressable>
            </View>

            <View style={styles.controlsRow}>
                <View style={styles.transportRow}>
                    <Pressable
                        onPress={() => stepTrack(-1)}
                        disabled={safeTracks.length <= 1}
                        style={({ pressed }) => [styles.secondaryAction, safeTracks.length <= 1 && styles.disabled, pressed && styles.pressed]}
                    >
                        <MaterialCommunityIcons name="skip-previous" size={18} color={palette.textMuted} />
                    </Pressable>
                    <Pressable
                        onPress={togglePlayback}
                        style={({ pressed }) => [styles.playAction, { backgroundColor: palette.primary }, pressed && styles.pressed]}
                    >
                        <MaterialCommunityIcons
                            name={playerStatus.playing ? "pause" : "play"}
                            size={22}
                            color="#FFFFFF"
                        />
                    </Pressable>
                    <Pressable
                        onPress={() => stepTrack(1)}
                        disabled={safeTracks.length <= 1}
                        style={({ pressed }) => [styles.secondaryAction, safeTracks.length <= 1 && styles.disabled, pressed && styles.pressed]}
                    >
                        <MaterialCommunityIcons name="skip-next" size={18} color={palette.textMuted} />
                    </Pressable>
                </View>

                <View style={styles.volumeRow}>
                    <Pressable
                        onLayout={handleVolumeTrackLayout}
                        onPress={(event) => handleVolumePress(event.nativeEvent.locationX)}
                        style={[styles.volumeTrack, { backgroundColor: palette.surface, borderColor: palette.border }]}
                    >
                        <View style={[styles.volumeFill, { width: `${activeVolume * 100}%`, backgroundColor: palette.primary }]} />
                        <View style={[styles.volumeThumb, { left: `${activeVolume * 100}%`, borderColor: palette.primary }]} />
                    </Pressable>
                </View>
            </View>
        </View>
    );
}

const styles = StyleSheet.create({
    emptyShell: {
        paddingTop: 6,
    },
    emptyText: {
        fontSize: 12,
        lineHeight: 18,
    },
    card: {
        borderWidth: 1,
        borderRadius: 20,
        paddingHorizontal: 12,
        paddingVertical: 12,
        gap: 12,
        shadowColor: "#0F172A",
        shadowOpacity: 0.06,
        shadowRadius: 14,
        shadowOffset: { width: 0, height: 7 },
        elevation: 2,
    },
    header: {
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
    },
    coverTile: {
        width: 40,
        height: 40,
        borderRadius: 14,
        alignItems: "center",
        justifyContent: "center",
        borderWidth: 1,
    },
    meta: {
        flex: 1,
        minWidth: 0,
    },
    title: {
        fontSize: 14,
        fontWeight: "900",
    },
    nowPlaying: {
        marginTop: 2,
        fontSize: 10,
        fontWeight: "700",
        letterSpacing: 0.9,
    },
    muteButton: {
        width: 24,
        height: 24,
        alignItems: "center",
        justifyContent: "center",
    },
    controlsRow: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
    },
    transportRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
    },
    secondaryAction: {
        width: 28,
        height: 28,
        borderRadius: 14,
        alignItems: "center",
        justifyContent: "center",
    },
    playAction: {
        width: 42,
        height: 42,
        borderRadius: 21,
        alignItems: "center",
        justifyContent: "center",
        shadowColor: "#7C3AED",
        shadowOpacity: 0.25,
        shadowRadius: 10,
        shadowOffset: { width: 0, height: 5 },
        elevation: 3,
    },
    volumeRow: {
        flex: 1,
        alignItems: "flex-end",
        justifyContent: "center",
    },
    volumeTrack: {
        width: 78,
        height: 8,
        borderRadius: radii.pill,
        borderWidth: 1,
        justifyContent: "center",
        overflow: "visible",
    },
    volumeFill: {
        height: "100%",
        borderRadius: radii.pill,
    },
    volumeThumb: {
        position: "absolute",
        top: -6,
        width: 20,
        height: 20,
        marginLeft: -10,
        borderRadius: 10,
        borderWidth: 3,
        backgroundColor: "#FFFFFF",
        shadowColor: "#7C3AED",
        shadowOpacity: 0.14,
        shadowRadius: 8,
        shadowOffset: { width: 0, height: 3 },
        elevation: 2,
    },
    pressed: {
        opacity: 0.82,
    },
    disabled: {
        opacity: 0.4,
    },
});
