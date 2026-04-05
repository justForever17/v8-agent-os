import React, { useEffect, useMemo, useRef } from "react";
import {
    Animated,
    Easing,
    Image,
    Pressable,
    StyleSheet,
    Text,
    View,
} from "react-native";
import type { LucideIcon } from "lucide-react-native";
import { Monitor, MoonStar, SunMedium, Volume2, VolumeX, Workflow } from "lucide-react-native";
import MaskedView from "@react-native-masked-view/masked-view";
import { LinearGradient } from "expo-linear-gradient";

import { useUiPrefs } from "@/src/providers/ui-prefs";

const BRAND_MARK = require("../../../assets/images/brand-mark.png");

export type PhoneTopbarAction = {
    key: string;
    icon?: string;
    onPress: () => void;
    badge?: number;
    indicatorColor?: string;
    disabled?: boolean;
    tone?: "default" | "primary" | "accent";
};

const ACTION_ORDER = ["desktop-live", "rpa", "voice", "theme"] as const;
const WORDMARK_COLORS = ["#8B5CF6", "#38BDF8", "#34D399", "#F59E0B", "#FB7185", "#A855F7", "#8B5CF6"] as const;
const WORDMARK_SHINE_COLORS = [
    "rgba(255,255,255,0)",
    "rgba(255,255,255,0.16)",
    "rgba(255,255,255,0.98)",
    "rgba(255,255,255,0.22)",
    "rgba(255,255,255,0)",
] as const;

function iconColor(tone: PhoneTopbarAction["tone"] | undefined, palette: ReturnType<typeof useUiPrefs>["colors"]) {
    if (tone === "primary") return palette.primary;
    if (tone === "accent") return palette.accent;
    return palette.textMuted;
}

function isRoundAction(key: string) {
    return key === "voice" || key === "theme";
}

function WordmarkMask({ style, color = "#FFFFFF" }: { style?: object; color?: string }) {
    return (
        <Text allowFontScaling={false} style={[styles.wordmarkMask, style, { color }]}>
            V8 OS
        </Text>
    );
}

function V8Wordmark({ dark }: { dark: boolean }) {
    const shine = useRef(new Animated.Value(0)).current;
    const gradientFlow = useRef(new Animated.Value(0)).current;

    useEffect(() => {
        const loop = Animated.loop(
            Animated.timing(shine, {
                toValue: 1,
                duration: 3900,
                easing: Easing.bezier(0.22, 1, 0.36, 1),
                useNativeDriver: true,
            }),
        );
        loop.start();
        return () => loop.stop();
    }, [shine]);

    useEffect(() => {
        const loop = Animated.loop(
            Animated.timing(gradientFlow, {
                toValue: 1,
                duration: 8200,
                easing: Easing.linear,
                useNativeDriver: true,
            }),
        );
        loop.start();
        return () => loop.stop();
    }, [gradientFlow]);

    const translateX = shine.interpolate({
        inputRange: [0, 0.18, 0.28, 0.55, 0.7, 1],
        outputRange: [96, 96, 78, -32, -52, -68],
    });
    const opacity = shine.interpolate({
        inputRange: [0, 0.18, 0.28, 0.55, 0.7, 1],
        outputRange: [0, 0, 0.98, 0.85, 0, 0],
    });
    const gradientTranslateX = gradientFlow.interpolate({
        inputRange: [0, 1],
        outputRange: [0, -92],
    });

    return (
        <View style={styles.wordmark}>
            <WordmarkMask
                style={[
                    styles.wordmarkGlow,
                    { color: dark ? "rgba(99,102,241,0.28)" : "rgba(99,102,241,0.22)" },
                ]} 
            />
            <MaskedView style={styles.wordmarkLayer} maskElement={<WordmarkMask />}>
                <Animated.View
                    pointerEvents="none"
                    style={[
                        styles.wordmarkGradient,
                        {
                            transform: [{ translateX: gradientTranslateX }],
                        },
                    ]}
                >
                    <LinearGradient
                        colors={WORDMARK_COLORS}
                        start={{ x: 0, y: 0 }}
                        end={{ x: 1, y: 1 }}
                        style={StyleSheet.absoluteFill}
                    />
                </Animated.View>
            </MaskedView>
            <MaskedView style={styles.wordmarkLayer} maskElement={<WordmarkMask />}>
                <Animated.View
                    pointerEvents="none"
                    style={[
                        styles.wordmarkShine,
                        {
                            opacity,
                            transform: [{ translateX }],
                        },
                    ]}
                >
                    <LinearGradient
                        colors={WORDMARK_SHINE_COLORS}
                        start={{ x: 0, y: 0 }}
                        end={{ x: 1, y: 0 }}
                        style={StyleSheet.absoluteFill}
                    />
                </Animated.View>
            </MaskedView>
        </View>
    );
}

function resolveActionIcon(key: string, voiceEnabled: boolean, themeMode: "light" | "dark"): LucideIcon {
    switch (key) {
        case "desktop-live":
            return Monitor;
        case "rpa":
            return Workflow;
        case "voice":
            return voiceEnabled ? Volume2 : VolumeX;
        case "theme":
            return themeMode === "dark" ? MoonStar : SunMedium;
        default:
            return Monitor;
    }
}

function TopbarButton({
    action,
    colors,
    voiceEnabled,
    themeMode,
}: {
    action: PhoneTopbarAction;
    colors: ReturnType<typeof useUiPrefs>["colors"];
    voiceEnabled: boolean;
    themeMode: "light" | "dark";
}) {
    const Icon = resolveActionIcon(action.key, voiceEnabled, themeMode);
    const color = iconColor(action.tone, colors);
    const round = isRoundAction(action.key);
    const surfaceStyle = action.tone === "primary"
        ? {
            backgroundColor: colors.primarySoft,
            borderColor: `${colors.primary}52`,
            borderWidth: 1,
        }
        : action.tone === "accent"
            ? {
                backgroundColor: colors.accentSoft,
                borderColor: `${colors.accent}44`,
                borderWidth: 1,
            }
            : {
                backgroundColor: colors.surfaceStrong,
                borderColor: `${colors.border}CC`,
                borderWidth: 1,
            };

    return (
        <Pressable
            key={action.key}
            onPress={action.onPress}
            disabled={action.disabled}
            style={({ pressed }) => [
                styles.actionButton,
                round ? styles.actionButtonRound : styles.actionButtonSoftSquare,
                surfaceStyle,
                { opacity: action.disabled ? 0.45 : pressed ? 0.78 : 1 },
                pressed && action.tone === "default" && {
                    backgroundColor: colors.surfaceStrong,
                    borderColor: `${colors.border}F2`,
                    borderWidth: 1,
                },
            ]}
        >
            <Icon size={16} color={color} strokeWidth={2} />
            {action.badge && action.badge > 0 ? (
                <View style={[styles.badge, { backgroundColor: colors.danger }]}>
                    <Text style={styles.badgeText}>{action.badge > 99 ? "99+" : action.badge}</Text>
                </View>
            ) : null}
            {action.indicatorColor ? (
                <View style={[styles.indicatorDot, { backgroundColor: action.indicatorColor }]} />
            ) : null}
        </Pressable>
    );
}

export function PhoneTopbar({
    actions,
    userImageUri,
    onProfilePress,
}: {
    actions: PhoneTopbarAction[];
    userImageUri?: string;
    onProfilePress?: () => void;
}) {
    const { locale, setLocale, colors, themeMode, voiceEnabled, t } = useUiPrefs();
    const actionMap = useMemo(() => new Map(actions.map((action) => [action.key, action])), [actions]);
    const orderedActions = ACTION_ORDER
        .map((key) => actionMap.get(key))
        .filter((item): item is PhoneTopbarAction => Boolean(item));

    const localeOptions = [
        { key: "zh-CN" as const, label: "中" },
        { key: "en" as const, label: "EN" },
    ];

    return (
        <View
            style={[
                styles.topbar,
                {
                    backgroundColor: themeMode === "dark" ? "rgba(10,15,26,0.94)" : "rgba(255,255,255,0.95)",
                    borderBottomColor: `${colors.border}CC`,
                },
            ]}
        >
            <View style={styles.brandSide}>
                <View style={[styles.brandMarkWrap, { borderColor: `${colors.border}A6`, backgroundColor: colors.surfaceStrong }]}>
                    <Image source={BRAND_MARK} style={styles.brandMark} />
                </View>
                <V8Wordmark dark={themeMode === "dark"} />
            </View>

            <View style={styles.actions}>
                {orderedActions
                    .filter((action) => action.key === "desktop-live" || action.key === "rpa")
                    .map((action) => (
                        <TopbarButton
                            key={action.key}
                            action={action}
                            colors={colors}
                            voiceEnabled={voiceEnabled}
                            themeMode={themeMode}
                        />
                    ))}

                <View
                    accessibilityLabel={t("语言切换", "Language switcher")}
                    style={[
                        styles.localeButton,
                        {
                            backgroundColor: themeMode === "dark" ? "rgba(15,23,42,0.52)" : "rgba(255,255,255,0.78)",
                            borderColor: `${colors.border}A6`,
                        },
                    ]}
                >
                    {localeOptions.map((option) => {
                        const active = option.key === locale;
                        return (
                            <Pressable
                                key={option.key}
                                onPress={() => void setLocale(option.key)}
                                style={[
                                    styles.localeOption,
                                    active && {
                                        backgroundColor: colors.primary,
                                        shadowColor: colors.primary,
                                        shadowOpacity: 0.18,
                                        shadowRadius: 6,
                                        shadowOffset: { width: 0, height: 2 },
                                        elevation: 2,
                                    },
                                ]}
                            >
                                <Text
                                    style={[
                                        styles.localeText,
                                        { color: active ? "#FFFFFF" : colors.textMuted },
                                    ]}
                                >
                                    {option.label}
                                </Text>
                            </Pressable>
                        );
                    })}
                </View>

                {orderedActions
                    .filter((action) => action.key === "voice" || action.key === "theme")
                    .map((action) => (
                        <TopbarButton
                            key={action.key}
                            action={action}
                            colors={colors}
                            voiceEnabled={voiceEnabled}
                            themeMode={themeMode}
                        />
                    ))}

                <Pressable
                    style={({ pressed }) => [
                        styles.profileButton,
                        {
                            backgroundColor: themeMode === "dark" ? "rgba(15,23,42,0.5)" : "rgba(255,255,255,0.78)",
                            borderColor: `${colors.border}A6`,
                            opacity: pressed ? 0.82 : 1,
                        },
                    ]}
                    onPress={onProfilePress}
                >
                    {userImageUri ? (
                        <Image source={{ uri: userImageUri }} style={styles.profileImage} />
                    ) : (
                        <Image source={BRAND_MARK} style={styles.profileImage} />
                    )}
                </Pressable>
            </View>
        </View>
    );
}

const styles = StyleSheet.create({
    topbar: {
        minHeight: 56,
        paddingHorizontal: 12,
        paddingVertical: 7,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        borderBottomWidth: StyleSheet.hairlineWidth,
        shadowColor: "#0F172A",
        shadowOpacity: 0.04,
        shadowRadius: 10,
        shadowOffset: { width: 0, height: 3 },
        elevation: 1,
    },
    brandSide: {
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
        minWidth: 0,
        flexShrink: 1,
    },
    brandMark: {
        width: 32,
        height: 32,
        borderRadius: 8,
    },
    brandMarkWrap: {
        width: 32,
        height: 32,
        borderRadius: 10,
        overflow: "hidden",
        borderWidth: 1,
    },
    wordmark: {
        width: 92,
        height: 23,
        justifyContent: "center",
    },
    wordmarkLayer: {
        ...StyleSheet.absoluteFillObject,
    },
    wordmarkGradient: {
        width: 184,
        height: "100%",
    },
    wordmarkMask: {
        minWidth: 86,
        fontSize: 18,
        fontWeight: "900",
        letterSpacing: -0.8,
        lineHeight: 22,
        includeFontPadding: false,
    },
    wordmarkGlow: {
        position: "absolute",
        left: 0,
        top: 1.5,
        opacity: 1,
        textShadowColor: "rgba(99,102,241,0.24)",
        textShadowOffset: { width: 0, height: 1 },
        textShadowRadius: 14,
    },
    wordmarkShine: {
        width: 92,
        height: "100%",
    },
    actions: {
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
        flexShrink: 0,
    },
    actionButton: {
        width: 36,
        height: 36,
        alignItems: "center",
        justifyContent: "center",
        shadowColor: "#0F172A",
        shadowOpacity: 0.04,
        shadowRadius: 8,
        shadowOffset: { width: 0, height: 3 },
        elevation: 1,
    },
    actionButtonRound: {
        borderRadius: 18,
    },
    actionButtonSoftSquare: {
        borderRadius: 12,
    },
    localeButton: {
        height: 36,
        borderRadius: 999,
        paddingHorizontal: 4,
        flexDirection: "row",
        alignItems: "center",
        gap: 2,
        borderWidth: 1,
        shadowColor: "#0F172A",
        shadowOpacity: 0.04,
        shadowRadius: 8,
        shadowOffset: { width: 0, height: 3 },
        elevation: 1,
    },
    localeOption: {
        minWidth: 29,
        height: 28,
        borderRadius: 999,
        paddingHorizontal: 9,
        alignItems: "center",
        justifyContent: "center",
    },
    localeText: {
        fontSize: 11,
        fontWeight: "800",
        letterSpacing: 0.6,
    },
    profileButton: {
        width: 36,
        height: 36,
        borderRadius: 18,
        overflow: "hidden",
        borderWidth: 1,
        shadowColor: "#0F172A",
        shadowOpacity: 0.04,
        shadowRadius: 8,
        shadowOffset: { width: 0, height: 3 },
        elevation: 1,
    },
    profileImage: {
        width: "100%",
        height: "100%",
    },
    badge: {
        position: "absolute",
        top: -4,
        right: -4,
        minWidth: 15,
        height: 15,
        borderRadius: 8,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 4,
    },
    badgeText: {
        color: "#FFFFFF",
        fontSize: 9,
        fontWeight: "900",
    },
    indicatorDot: {
        position: "absolute",
        top: 7,
        right: 7,
        width: 8,
        height: 8,
        borderRadius: 999,
        borderWidth: 1,
        borderColor: "#FFFFFF",
        shadowColor: "#10B981",
        shadowOpacity: 0.55,
        shadowRadius: 6,
        shadowOffset: { width: 0, height: 0 },
        elevation: 2,
    },
});
