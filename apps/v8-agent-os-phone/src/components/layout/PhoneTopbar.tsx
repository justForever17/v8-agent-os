import React, { useEffect, useMemo, useRef, useState } from "react";
import {
    Animated,
    Easing,
    Image,
    LayoutChangeEvent,
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
    "rgba(255,255,255,0.14)",
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

function WordmarkText({
    color = "#FFFFFF",
    text = "V8 OS",
    fontSize = 18.5,
    style,
    onLayout,
}: {
    color?: string;
    text?: string;
    fontSize?: number;
    style?: object;
    onLayout?: (event: LayoutChangeEvent) => void;
}) {
    const lineHeight = Math.round(fontSize * 1.18);
    return (
        <Text
            allowFontScaling={false}
            numberOfLines={1}
            onLayout={onLayout}
            style={[styles.wordmarkText, style, { color, fontSize, lineHeight }]}
        >
            {text}
        </Text>
    );
}

export function PhoneWordmark({
    dark,
    text = "V8 OS",
    fontSize = 18.5,
}: {
    dark: boolean;
    text?: string;
    fontSize?: number;
}) {
    const shine = useRef(new Animated.Value(0)).current;
    const gradientFlow = useRef(new Animated.Value(0)).current;
    const [textWidth, setTextWidth] = useState(Math.max(Math.ceil(text.length * fontSize * 0.68), 90));
    const lineHeight = Math.round(fontSize * 1.18);

    useEffect(() => {
        const loop = Animated.loop(
            Animated.timing(shine, {
                toValue: 1,
                duration: 3800,
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
                duration: 8600,
                easing: Easing.linear,
                useNativeDriver: true,
            }),
        );
        loop.start();
        return () => loop.stop();
    }, [gradientFlow]);

    const gradientWidth = Math.max(Math.round(textWidth * 2.6), 220);
    const gradientTravel = Math.max(gradientWidth - textWidth, 84);
    const shineWidth = Math.max(Math.round(textWidth * 0.74), 64);
    const translateX = shine.interpolate({
        inputRange: [0, 0.16, 0.28, 0.58, 0.72, 1],
        outputRange: [textWidth + 24, textWidth + 24, textWidth * 0.72, -shineWidth * 0.2, -shineWidth * 0.68, -shineWidth],
    });
    const opacity = shine.interpolate({
        inputRange: [0, 0.16, 0.28, 0.58, 0.72, 1],
        outputRange: [0, 0, 0.94, 0.84, 0, 0],
    });
    const gradientTranslateX = gradientFlow.interpolate({
        inputRange: [0, 1],
        outputRange: [0, -gradientTravel],
    });

    const handleMeasure = (event: LayoutChangeEvent) => {
        const nextWidth = Math.max(90, Math.ceil(event.nativeEvent.layout.width));
        if (nextWidth && Math.abs(nextWidth - textWidth) > 1) {
            setTextWidth(nextWidth);
        }
    };

    const glowColor = dark ? "rgba(99,102,241,0.20)" : "rgba(99,102,241,0.14)";

    return (
        <View style={[styles.wordmark, { width: textWidth, height: lineHeight }]}>
            <WordmarkText
                color="rgba(255,255,255,0.01)"
                text={text}
                fontSize={fontSize}
                onLayout={handleMeasure}
                style={styles.wordmarkMeasure}
            />
            <WordmarkText color={glowColor} text={text} fontSize={fontSize} style={styles.wordmarkGlow} />
            <MaskedView style={styles.wordmarkLayer} maskElement={<WordmarkText text={text} fontSize={fontSize} />}>
                <Animated.View
                    pointerEvents="none"
                    style={[
                        styles.wordmarkGradient,
                        {
                            width: gradientWidth,
                            transform: [{ translateX: gradientTranslateX }],
                        },
                    ]}
                >
                    <LinearGradient
                        colors={WORDMARK_COLORS}
                        locations={[0, 0.16, 0.34, 0.52, 0.7, 0.86, 1]}
                        start={{ x: 0, y: 0.5 }}
                        end={{ x: 1, y: 0.5 }}
                        style={StyleSheet.absoluteFill}
                    />
                </Animated.View>
            </MaskedView>
            <MaskedView style={styles.wordmarkLayer} maskElement={<WordmarkText text={text} fontSize={fontSize} />}>
                <Animated.View
                    pointerEvents="none"
                    style={[
                        styles.wordmarkShine,
                        {
                            width: shineWidth,
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

function BrandArea({
    colors,
    themeMode,
    onBrandPress,
}: {
    colors: ReturnType<typeof useUiPrefs>["colors"];
    themeMode: "light" | "dark";
    onBrandPress?: () => void;
}) {
    const content = (
        <>
            <View style={[styles.brandMarkWrap, { borderColor: `${colors.border}A6`, backgroundColor: colors.surfaceStrong }]}>
                <Image source={BRAND_MARK} style={styles.brandMark} />
            </View>
            <PhoneWordmark dark={themeMode === "dark"} />
        </>
    );

    if (!onBrandPress) {
        return <View style={styles.brandSide}>{content}</View>;
    }

    return (
        <Pressable
            accessibilityRole="button"
            accessibilityLabel="V8 OS"
            hitSlop={8}
            onPress={onBrandPress}
            style={({ pressed }) => [styles.brandSide, styles.brandPressable, pressed && styles.brandPressableActive]}
        >
            {content}
        </Pressable>
    );
}

export function PhoneTopbar({
    actions,
    userImageUri,
    onProfilePress,
    onBrandPress,
}: {
    actions: PhoneTopbarAction[];
    userImageUri?: string;
    onProfilePress?: () => void;
    onBrandPress?: () => void;
}) {
    const { locale, toggleLocale, colors, themeMode, voiceEnabled, t } = useUiPrefs();
    const actionMap = useMemo(() => new Map(actions.map((action) => [action.key, action])), [actions]);
    const orderedActions = ACTION_ORDER
        .map((key) => actionMap.get(key))
        .filter((item): item is PhoneTopbarAction => Boolean(item));
    const localeLabel = locale === "en" ? "EN" : "中";

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
            <BrandArea colors={colors} themeMode={themeMode} onBrandPress={onBrandPress} />

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

                <Pressable
                    accessibilityLabel={t("语言切换", "Toggle language")}
                    accessibilityRole="button"
                    onPress={() => void toggleLocale()}
                    style={[
                        styles.localeButton,
                        {
                            backgroundColor: themeMode === "dark" ? "rgba(15,23,42,0.52)" : "rgba(255,255,255,0.78)",
                            borderColor: `${colors.border}A6`,
                        },
                    ]}
                >
                    <Text style={[styles.localeText, { color: colors.textMuted }]}>{localeLabel}</Text>
                </Pressable>

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
                    accessibilityRole="button"
                    accessibilityLabel={t("用户资料", "User profile")}
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
        gap: 6,
        minWidth: 0,
        flexGrow: 1,
        flexShrink: 0,
    },
    brandPressable: {
        paddingRight: 4,
        borderRadius: 14,
    },
    brandPressableActive: {
        opacity: 0.82,
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
        height: 22,
        justifyContent: "center",
        alignSelf: "center",
        overflow: "visible",
        minWidth: 90,
        flexShrink: 0,
    },
    wordmarkLayer: {
        ...StyleSheet.absoluteFillObject,
    },
    wordmarkText: {
        fontWeight: "900",
        letterSpacing: -0.72,
        includeFontPadding: false,
        textAlignVertical: "center",
    },
    wordmarkMeasure: {
        position: "absolute",
        left: 0,
        top: 0,
        opacity: 0,
        minWidth: 90,
    },
    wordmarkGradient: {
        height: "100%",
    },
    wordmarkGlow: {
        position: "absolute",
        left: 0,
        top: 1.4,
        opacity: 0.88,
        textShadowColor: "rgba(99,102,241,0.18)",
        textShadowOffset: { width: 0, height: 0 },
        textShadowRadius: 18,
    },
    wordmarkShine: {
        height: "100%",
    },
    actions: {
        flexDirection: "row",
        alignItems: "center",
        gap: 5,
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
        width: 36,
        height: 36,
        borderRadius: 12,
        alignItems: "center",
        justifyContent: "center",
        borderWidth: 1,
        shadowColor: "#0F172A",
        shadowOpacity: 0.04,
        shadowRadius: 8,
        shadowOffset: { width: 0, height: 3 },
        elevation: 1,
    },
    localeText: {
        fontSize: 10.5,
        fontWeight: "800",
        letterSpacing: 0.4,
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
