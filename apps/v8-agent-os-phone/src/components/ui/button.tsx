import React, { memo } from "react";
import {
    ActivityIndicator,
    Pressable,
    StyleSheet,
    Text,
    View,
    type PressableProps,
    type StyleProp,
    type TextStyle,
    type ViewStyle,
} from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

type ButtonVariant = "default" | "destructive" | "outline" | "secondary" | "ghost" | "link";
type ButtonSize = "default" | "sm" | "lg" | "icon";

export type ButtonProps = PressableProps & {
    children: React.ReactNode;
    variant?: ButtonVariant;
    size?: ButtonSize;
    loading?: boolean;
    textStyle?: StyleProp<TextStyle>;
};

export const Button = memo(function Button({
    children,
    variant = "default",
    size = "default",
    loading = false,
    disabled,
    style,
    textStyle,
    ...rest
}: ButtonProps) {
    const { colors, themeMode } = useUiPrefs();
    const resolvedDisabled = Boolean(disabled || loading);
    const styleResolver = typeof style === "function" ? style : undefined;
    const staticStyle = typeof style === "function" ? undefined : style;

    const tone = getTone(variant, themeMode, colors);
    const sizeStyle = sizeStyles[size];

    return (
        <Pressable
            {...rest}
            disabled={resolvedDisabled}
            style={(state) => {
                const externalStyle = styleResolver ? styleResolver(state) : staticStyle;
                return [
                    styles.base,
                    sizeStyle,
                    {
                        backgroundColor: tone.backgroundColor,
                        borderColor: tone.borderColor,
                        opacity: resolvedDisabled ? 0.5 : state.pressed ? 0.86 : 1,
                    },
                    externalStyle,
                ] as StyleProp<ViewStyle>;
            }}
        >
            {loading ? (
                <ActivityIndicator size="small" color={tone.textColor} />
            ) : typeof children === "string" ? (
                <Text style={[styles.text, { color: tone.textColor }, textStyle]}>{children}</Text>
            ) : (
                <View style={styles.content}>{children}</View>
            )}
        </Pressable>
    );
});

function getTone(variant: ButtonVariant, themeMode: "light" | "dark", colors: ReturnType<typeof useUiPrefs>["colors"]) {
    switch (variant) {
        case "destructive":
            return {
                backgroundColor: colors.danger,
                borderColor: colors.danger,
                textColor: "#FFFFFF",
            };
        case "secondary":
            return {
                backgroundColor: themeMode === "dark" ? "rgba(30,41,59,0.92)" : "rgba(241,245,249,0.96)",
                borderColor: themeMode === "dark" ? "rgba(148,163,184,0.2)" : "rgba(226,232,240,0.96)",
                textColor: colors.text,
            };
        case "outline":
            return {
                backgroundColor: colors.surface,
                borderColor: colors.border,
                textColor: colors.text,
            };
        case "ghost":
            return {
                backgroundColor: "transparent",
                borderColor: "transparent",
                textColor: colors.text,
            };
        case "link":
            return {
                backgroundColor: "transparent",
                borderColor: "transparent",
                textColor: colors.primary,
            };
        case "default":
        default:
            return {
                backgroundColor: colors.primary,
                borderColor: colors.primary,
                textColor: "#FFFFFF",
            };
    }
}

const sizeStyles = StyleSheet.create({
    default: {
        minHeight: 40,
        paddingHorizontal: 16,
        paddingVertical: 8,
        borderRadius: 12,
    },
    sm: {
        minHeight: 36,
        paddingHorizontal: 12,
        paddingVertical: 6,
        borderRadius: 12,
    },
    lg: {
        minHeight: 44,
        paddingHorizontal: 20,
        paddingVertical: 10,
        borderRadius: 12,
    },
    icon: {
        width: 40,
        height: 40,
        paddingHorizontal: 0,
        paddingVertical: 0,
        borderRadius: 12,
    },
});

const styles = StyleSheet.create({
    base: {
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
        flexDirection: "row",
        gap: spacing.xs,
    },
    text: {
        fontSize: 14,
        lineHeight: 18,
        fontWeight: "500",
    },
    content: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        gap: spacing.xs,
    },
});
