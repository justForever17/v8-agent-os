import { memo, useEffect, useMemo, useRef, useState } from "react";
import { Animated, Easing, Pressable, StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { Button } from "@/src/components/ui/button";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

export const VoiceCard = memo(function VoiceCard({
    text,
    speaking,
    onSpeak,
}: {
    text: string;
    speaking: boolean;
    onSpeak: () => void;
}) {
    const { colors, t } = useUiPrefs();
    const [expanded, setExpanded] = useState(false);
    const [hasPlayed, setHasPlayed] = useState(false);
    const wave = useRef(Array.from({ length: 8 }, () => new Animated.Value(0.32))).current;
    const pulse = useRef(new Animated.Value(0)).current;
    const barHeights = useMemo(() => [10, 14, 18, 12, 20, 13, 17, 11], []);

    useEffect(() => {
        if (!speaking) {
            wave.forEach((value) => value.stopAnimation());
            wave.forEach((value) => value.setValue(0.32));
            pulse.stopAnimation();
            pulse.setValue(0);
            return;
        }

        const loops = wave.map((value, index) =>
            Animated.loop(
                Animated.sequence([
                    Animated.timing(value, {
                        toValue: 1,
                        duration: 240 + index * 30,
                        easing: Easing.inOut(Easing.ease),
                        useNativeDriver: false,
                    }),
                    Animated.timing(value, {
                        toValue: 0.28,
                        duration: 260 + (index % 3) * 40,
                        easing: Easing.inOut(Easing.ease),
                        useNativeDriver: false,
                    }),
                ]),
            ),
        );
        const pulseLoop = Animated.loop(
            Animated.sequence([
                Animated.timing(pulse, {
                    toValue: 1,
                    duration: 420,
                    easing: Easing.inOut(Easing.ease),
                    useNativeDriver: true,
                }),
                Animated.timing(pulse, {
                    toValue: 0,
                    duration: 420,
                    easing: Easing.inOut(Easing.ease),
                    useNativeDriver: true,
                }),
            ]),
        );
        loops.forEach((loop) => loop.start());
        pulseLoop.start();
        return () => {
            loops.forEach((loop) => loop.stop());
            wave.forEach((value) => value.setValue(0.32));
            pulseLoop.stop();
            pulse.setValue(0);
        };
    }, [pulse, speaking, wave]);

    const pulseScale = pulse.interpolate({
        inputRange: [0, 1],
        outputRange: [1, 1.08],
    });
    const pulseOpacity = pulse.interpolate({
        inputRange: [0, 1],
        outputRange: [0.18, 0.32],
    });

    return (
        <View style={styles.wrap}>
            <View
                style={[
                    styles.card,
                    {
                        backgroundColor: colors.surface,
                        borderColor: speaking ? "rgba(59,130,246,0.28)" : colors.border,
                    },
                ]}
            >
                <Pressable
                    style={styles.mainAction}
                    onPress={() => {
                        setHasPlayed(true);
                        onSpeak();
                    }}
                >
                    <Animated.View
                        style={[
                            styles.actionIconGlow,
                            {
                                backgroundColor: colors.primary,
                                opacity: speaking ? pulseOpacity : 0,
                                transform: [{ scale: speaking ? pulseScale : 1 }],
                            },
                        ]}
                    />
                    <View style={[styles.actionIcon, { backgroundColor: colors.primary }]}>
                        <MaterialCommunityIcons
                            name={speaking ? "stop" : hasPlayed ? "restart" : "play"}
                            size={18}
                            color="#FFFFFF"
                        />
                    </View>
                    <View style={styles.body}>
                        <Text style={[styles.title, { color: colors.text }]}>
                            {speaking
                                ? t("src.components.chat.voicecard.playing_voice")
                                : hasPlayed
                                    ? t("src.components.chat.voicecard.voice_clip")
                                    : t("src.components.chat.voicecard.tap_to_play_voice")}
                        </Text>
                        {speaking ? (
                            <>
                                <Text style={[styles.subtitle, { color: colors.textMuted }]}>
                                    {t("src.components.chat.voicecard.playing")}
                                </Text>
                                <View style={styles.waveRow}>
                                    {wave.map((value, index) => (
                                        <Animated.View
                                            key={String(index)}
                                            style={[
                                                styles.waveBar,
                                                {
                                                    backgroundColor: colors.primary,
                                                    height: value.interpolate({
                                                        inputRange: [0.28, 1],
                                                        outputRange: [6, barHeights[index]],
                                                    }),
                                                    opacity: value.interpolate({
                                                        inputRange: [0.28, 1],
                                                        outputRange: [0.45, 1],
                                                    }),
                                                },
                                            ]}
                                        />
                                    ))}
                                </View>
                            </>
                        ) : (
                            <Text style={[styles.subtitle, { color: colors.textMuted }]} numberOfLines={2}>
                                {text}
                            </Text>
                        )}
                    </View>
                </Pressable>

                <Button
                    variant="ghost"
                    size="icon"
                    onPress={() => setExpanded((current) => !current)}
                    style={[styles.expandButton, { borderColor: colors.border }]}
                >
                    <MaterialCommunityIcons
                        name={expanded ? "chevron-up" : "chevron-down"}
                        size={16}
                        color={colors.textSoft}
                    />
                </Button>
            </View>

            {expanded ? (
                <View style={[styles.transcriptCard, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                    <Text style={[styles.transcriptText, { color: colors.textMuted }]}>{text}</Text>
                </View>
            ) : null}
        </View>
    );
});

const styles = StyleSheet.create({
    wrap: {
        gap: 8,
    },
    card: {
        borderWidth: 1,
        borderRadius: radii.xl,
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.md,
    },
    mainAction: {
        flex: 1,
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.md,
    },
    actionIconGlow: {
        position: "absolute",
        left: 0,
        width: 44,
        height: 44,
        borderRadius: 22,
    },
    actionIcon: {
        width: 44,
        height: 44,
        borderRadius: 22,
        alignItems: "center",
        justifyContent: "center",
        shadowColor: "#7C3AED",
        shadowOpacity: 0.22,
        shadowRadius: 12,
        shadowOffset: { width: 0, height: 8 },
        elevation: 4,
    },
    body: {
        flex: 1,
        gap: 2,
    },
    title: {
        fontSize: 15,
        lineHeight: 20,
        fontWeight: "700",
    },
    subtitle: {
        fontSize: 13,
        lineHeight: 20,
    },
    waveRow: {
        height: 20,
        flexDirection: "row",
        alignItems: "flex-end",
        gap: 3,
        marginTop: 4,
    },
    waveBar: {
        width: 4,
        borderRadius: 999,
    },
    expandButton: {
        width: 40,
        height: 40,
        borderWidth: 1,
    },
    transcriptCard: {
        borderWidth: 1,
        borderRadius: radii.lg,
        paddingHorizontal: spacing.md,
        paddingVertical: spacing.sm,
    },
    transcriptText: {
        fontSize: 13,
        lineHeight: 20,
    },
});
