import React, { forwardRef } from "react";
import { StyleSheet, Text, type TextProps } from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";

export const Label = forwardRef<Text, TextProps>(function Label({ style, ...rest }, ref) {
    const { colors } = useUiPrefs();
    return <Text ref={ref} {...rest} style={[styles.label, { color: colors.text }, style]} />;
});

const styles = StyleSheet.create({
    label: {
        fontSize: 14,
        lineHeight: 18,
        fontWeight: "500",
    },
});
