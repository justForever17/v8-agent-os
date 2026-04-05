import React, { memo } from "react";
import {
    StyleSheet,
    Text,
    View,
    type TextStyle,
    type ViewProps,
    type ViewStyle,
} from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline";

export type BadgeProps = ViewProps & {
    children: React.ReactNode;
    variant?: BadgeVariant;
    textStyle?: TextStyle;
};

export const Badge = memo(function Badge({
    children,
    variant = "default",
    style,
    textStyle,
    ...rest
}: BadgeProps) {
    const { colors, themeMode } = useUiPrefs();
    const tone = getTone(variant, themeMode, colors);

    return (
        <View {...rest} style={[styles.badge, tone.container, style]}>
            <Text style={[styles.text, { color: tone.textColor }, textStyle]}>{children}</Text>
        </View>
    );
});

function getTone(variant: BadgeVariant, themeMode: "light" | "dark", colors: ReturnType<typeof useUiPrefs>["colors"]) {
    switch (variant) {
        case "secondary":
            return {
                container: {
                    backgroundColor: themeMode === "dark" ? "rgba(30,41,59,0.92)" : "rgba(241,245,249,0.96)",
                    borderColor: "transparent",
                } satisfies ViewStyle,
                textColor: colors.text,
            };
        case "destructive":
            return {
                container: {
                    backgroundColor: colors.danger,
                    borderColor: "transparent",
                } satisfies ViewStyle,
                textColor: "#FFFFFF",
            };
        case "outline":
            return {
                container: {
                    backgroundColor: "transparent",
                    borderColor: colors.border,
                } satisfies ViewStyle,
                textColor: colors.text,
            };
        case "default":
        default:
            return {
                container: {
                    backgroundColor: colors.primary,
                    borderColor: "transparent",
                } satisfies ViewStyle,
                textColor: "#FFFFFF",
            };
    }
}

const styles = StyleSheet.create({
    badge: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        alignSelf: "flex-start",
        minHeight: 20,
        borderRadius: radii.pill,
        borderWidth: 1,
        paddingHorizontal: 10,
        paddingVertical: 3,
    },
    text: {
        fontSize: 11,
        lineHeight: 14,
        fontWeight: "600",
    },
});
