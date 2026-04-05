import { PropsWithChildren } from "react";
import { StyleSheet, View, type StyleProp, type ViewStyle } from "react-native";

import { colors, radii } from "@/src/theme/tokens";

export function GlassCard({
    children,
    style,
}: PropsWithChildren<{ style?: StyleProp<ViewStyle> }>) {
    return <View style={[styles.card, style]}>{children}</View>;
}

const styles = StyleSheet.create({
    card: {
        borderRadius: radii.lg,
        backgroundColor: colors.surfaceStrong,
        borderWidth: 1,
        borderColor: colors.border,
        padding: 16,
        shadowColor: "#0F172A",
        shadowOpacity: 0.06,
        shadowRadius: 14,
        shadowOffset: { width: 0, height: 8 },
        elevation: 2,
    },
});
