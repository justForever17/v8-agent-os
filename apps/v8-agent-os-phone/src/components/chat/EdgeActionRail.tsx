import { memo, useEffect, useMemo, useRef, type ReactNode } from "react";
import { Animated, PanResponder, Pressable, StyleSheet, View, Platform } from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";

export const EdgeActionRail = memo(function EdgeActionRail({
    side,
    open,
    expandedWidth,
    collapsedPeekWidth = 24,
    top = 12,
    children,
    onOpen,
    onClose,
}: {
    side: "left" | "right";
    open: boolean;
    expandedWidth: number;
    collapsedPeekWidth?: number;
    top?: number;
    children: ReactNode;
    onOpen: () => void;
    onClose: () => void;
}) {
    const { colors, themeMode } = useUiPrefs();
    const progress = useRef(new Animated.Value(open ? 1 : 0)).current;
    const closedOffset = side === "left" ? -expandedWidth : expandedWidth;
    const railColor = themeMode === "dark" ? "rgba(148, 163, 184, 0.48)" : "rgba(100, 116, 139, 0.48)";

    useEffect(() => {
        Animated.timing(progress, {
            toValue: open ? 1 : 0,
            duration: 180,
            useNativeDriver: true,
        }).start();
    }, [open, progress]);

    const panResponder = useMemo(() => PanResponder.create({
        onMoveShouldSetPanResponder: (_, gesture) => Math.abs(gesture.dx) > 10 && Math.abs(gesture.dx) > Math.abs(gesture.dy),
        onPanResponderRelease: (_, gesture) => {
            if (side === "left") {
                if (gesture.dx > 18) onOpen();
                if (gesture.dx < -18) onClose();
            } else {
                if (gesture.dx < -18) onOpen();
                if (gesture.dx > 18) onClose();
            }
        },
    }), [onClose, onOpen, side]);

    const translateX = progress.interpolate({
        inputRange: [0, 1],
        outputRange: [closedOffset, 0],
    });
    const collapsedHandleOpacity = progress.interpolate({
        inputRange: [0, 0.72, 1],
        outputRange: [1, 0.18, 0],
    });

    return (
        <View pointerEvents="box-none" style={StyleSheet.absoluteFill}>
            {open ? <Pressable style={StyleSheet.absoluteFill} onPress={onClose} /> : null}
            <Animated.View
                {...panResponder.panHandlers}
                pointerEvents={open ? "none" : "auto"}
                style={[
                    styles.edgeHandle,
                    side === "left" ? styles.leftEdgeHandle : styles.rightEdgeHandle,
                    {
                        top: top + 2,
                        width: 38,
                        borderColor: colors.border,
                        backgroundColor: themeMode === "dark" ? "rgba(24, 24, 27, 0.76)" : "rgba(255, 255, 255, 0.78)",
                        shadowColor: themeMode === "dark" ? "#000000" : "#0F172A",
                        opacity: collapsedHandleOpacity,
                        ...Platform.select({
                            web: { backdropFilter: "blur(12px)", WebkitBackdropFilter: "blur(12px)" } as any,
                        }),
                    },
                ]}
            >
                <Pressable
                    accessibilityRole="button"
                    style={StyleSheet.absoluteFill}
                    onPress={onOpen}
                    hitSlop={{ top: 10, bottom: 10, left: 16, right: 16 }}
                />
                <View
                    style={[
                        styles.handleAccent,
                        side === "left" ? styles.leftHandleAccent : styles.rightHandleAccent,
                        { backgroundColor: railColor },
                    ]}
                />
            </Animated.View>
            <Animated.View
                {...panResponder.panHandlers}
                style={[
                    styles.rail,
                    side === "left" ? styles.leftRail : styles.rightRail,
                    {
                        top: top + 2,
                        width: expandedWidth,
                        transform: [{ translateX }],
                        backgroundColor: "transparent",
                        borderColor: "transparent",
                    },
                ]}
            >
                <View pointerEvents={open ? "auto" : "none"} style={[styles.content, !open && styles.contentClosed]}>
                    {children}
                </View>
            </Animated.View>
        </View>
    );
});

const styles = StyleSheet.create({
    rail: {
        position: "absolute",
        zIndex: 30,
        minHeight: 38,
        borderWidth: 0,
        borderRadius: radii.pill,
        paddingHorizontal: 0,
        paddingVertical: 0,
        shadowOpacity: 0,
        elevation: 0,
    },
    leftRail: {
        left: 0,
    },
    rightRail: {
        right: 0,
    },
    content: {
        flex: 1,
        minWidth: 0,
        justifyContent: "center",
    },
    contentClosed: {
        opacity: 0,
    },
    edgeHandle: {
        position: "absolute",
        zIndex: 31,
        height: 38,
        borderWidth: 1,
        borderRadius: 10,
        flexDirection: "row",
        alignItems: "center",
        shadowOpacity: 0.08,
        shadowRadius: 3,
        shadowOffset: { width: 0, height: 1.5 },
        elevation: 2,
    },
    leftEdgeHandle: {
        left: 5,
        justifyContent: "center",
        alignItems: "center",
    },
    rightEdgeHandle: {
        right: 5,
        justifyContent: "center",
        alignItems: "center",
    },
    handleAccent: {
        width: 3,
        height: 19,
        borderRadius: 999,
    },
    leftHandleAccent: {},
    rightHandleAccent: {},
});
