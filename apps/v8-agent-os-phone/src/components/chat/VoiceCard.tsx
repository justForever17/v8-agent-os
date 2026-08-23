import { memo, useEffect, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useIsFocused } from "@react-navigation/native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import Animated, {
    cancelAnimation,
    Easing,
    useAnimatedStyle,
    useReducedMotion,
    useSharedValue,
    withRepeat,
    withTiming,
    type SharedValue,
} from "react-native-reanimated";

import { Button } from "@/src/components/ui/button";
import { useAppVisibility } from "@/src/hooks/use-app-visibility";
import { voiceWavePhase } from "@/src/lib/motion-policy";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

const BAR_HEIGHTS = [10, 14, 18, 12, 20, 13, 17, 11] as const;

const VoiceWaveBar = memo(function VoiceWaveBar({
    clock,
    color,
    height,
    phase,
}: {
    clock: SharedValue<number>;
    color: string;
    height: number;
    phase: number;
}) {
    const animatedStyle = useAnimatedStyle(() => {
        const sample = 0.34 + Math.abs(Math.sin((clock.value + phase) * Math.PI * 2)) * 0.66;
        return {
            opacity: 0.45 + sample * 0.55,
            transform: [
                { translateY: height * (1 - sample) / 2 },
                { scaleY: sample },
            ],
        };
    }, [height, phase]);

    return (
        <Animated.View
            style={[styles.waveBar, { backgroundColor: color, height }, animatedStyle]}
        />
    );
});

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
    const clock = useSharedValue(0);
    const reduceMotion = useReducedMotion();
    const isFocused = useIsFocused();
    const appVisible = useAppVisibility();
    const animationEnabled = speaking && isFocused && appVisible && !reduceMotion;

    useEffect(() => {
        cancelAnimation(clock);
        clock.value = 0;
        if (!animationEnabled) return undefined;
        clock.value = withRepeat(
            withTiming(1, { duration: 1100, easing: Easing.linear }),
            -1,
            false,
        );
        return () => cancelAnimation(clock);
    }, [animationEnabled, clock]);

    const pulseStyle = useAnimatedStyle(() => {
        const pulse = (Math.sin(clock.value * Math.PI * 2) + 1) / 2;
        return {
            opacity: animationEnabled ? 0.18 + pulse * 0.14 : 0,
            transform: [{ scale: animationEnabled ? 1 + pulse * 0.08 : 1 }],
        };
    }, [animationEnabled]);

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
                            },
                            pulseStyle,
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
                                    {BAR_HEIGHTS.map((height, index) => (
                                        <VoiceWaveBar
                                            key={String(index)}
                                            clock={clock}
                                            color={colors.primary}
                                            height={height}
                                            phase={voiceWavePhase(index, BAR_HEIGHTS.length)}
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
