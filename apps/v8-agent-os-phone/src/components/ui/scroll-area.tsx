import React, { forwardRef } from "react";
import {
    ScrollView,
    StyleSheet,
    View,
    type ScrollViewProps,
    type ViewProps,
} from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";

export const ScrollArea = forwardRef<ScrollView, ScrollViewProps>(function ScrollArea(
    { children, style, contentContainerStyle, ...rest },
    ref,
) {
    return (
        <View style={[styles.root, style]}>
            <ScrollView
                ref={ref}
                showsVerticalScrollIndicator={false}
                showsHorizontalScrollIndicator={false}
                contentContainerStyle={contentContainerStyle}
                {...rest}
            >
                {children}
            </ScrollView>
        </View>
    );
});

export const ScrollBar = forwardRef<View, ViewProps & { orientation?: "vertical" | "horizontal" }>(function ScrollBar(
    { orientation = "vertical", style, ...rest },
    ref,
) {
    const { colors } = useUiPrefs();
    return (
        <View
            ref={ref}
            {...rest}
            style={[
                orientation === "vertical" ? styles.verticalTrack : styles.horizontalTrack,
                { backgroundColor: colors.border },
                style,
            ]}
        />
    );
});

const styles = StyleSheet.create({
    root: {
        overflow: "hidden",
    },
    verticalTrack: {
        position: "absolute",
        right: 1,
        top: 0,
        bottom: 0,
        width: 2.5,
        borderRadius: 999,
        opacity: 0.4,
    },
    horizontalTrack: {
        position: "absolute",
        left: 0,
        right: 0,
        bottom: 1,
        height: 2.5,
        borderRadius: 999,
        opacity: 0.4,
    },
});
