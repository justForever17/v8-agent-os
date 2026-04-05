import React, { forwardRef, memo } from "react";
import {
    StyleSheet,
    Text,
    View,
    type TextProps,
    type ViewProps,
} from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

type CardProps = ViewProps;
type CardSectionProps = ViewProps;
type CardTextProps = TextProps;

export const Card = forwardRef<View, CardProps>(function Card({ style, ...rest }, ref) {
    const { colors } = useUiPrefs();
    return (
        <View
            ref={ref}
            {...rest}
            style={[
                styles.card,
                {
                    backgroundColor: colors.surfaceStrong,
                    borderColor: colors.border,
                },
                style,
            ]}
        />
    );
});

export const CardHeader = forwardRef<View, CardSectionProps>(function CardHeader({ style, ...rest }, ref) {
    return <View ref={ref} {...rest} style={[styles.header, style]} />;
});

export const CardTitle = forwardRef<Text, CardTextProps>(function CardTitle({ style, ...rest }, ref) {
    const { colors } = useUiPrefs();
    return <Text ref={ref} {...rest} style={[styles.title, { color: colors.text }, style]} />;
});

export const CardDescription = forwardRef<Text, CardTextProps>(function CardDescription({ style, ...rest }, ref) {
    const { colors } = useUiPrefs();
    return <Text ref={ref} {...rest} style={[styles.description, { color: colors.textMuted }, style]} />;
});

export const CardContent = forwardRef<View, CardSectionProps>(function CardContent({ style, ...rest }, ref) {
    return <View ref={ref} {...rest} style={[styles.content, style]} />;
});

export const CardFooter = forwardRef<View, CardSectionProps>(function CardFooter({ style, ...rest }, ref) {
    return <View ref={ref} {...rest} style={[styles.footer, style]} />;
});

export const CardSection = memo(function CardSection({ style, ...rest }: CardSectionProps) {
    return <View {...rest} style={[styles.section, style]} />;
});

const styles = StyleSheet.create({
    card: {
        borderRadius: radii.lg,
        borderWidth: 1,
        shadowColor: "#0F172A",
        shadowOpacity: 0.06,
        shadowRadius: 12,
        shadowOffset: { width: 0, height: 4 },
        elevation: 2,
    },
    header: {
        gap: 6,
        paddingHorizontal: 24,
        paddingVertical: 24,
    },
    title: {
        fontSize: 22,
        fontWeight: "600",
        lineHeight: 26,
        letterSpacing: -0.35,
    },
    description: {
        fontSize: 14,
        lineHeight: 20,
    },
    content: {
        paddingHorizontal: 24,
        paddingBottom: 24,
    },
    footer: {
        flexDirection: "row",
        alignItems: "center",
        paddingHorizontal: 24,
        paddingBottom: 24,
        paddingTop: 0,
    },
    section: {
        gap: spacing.sm,
    },
});
