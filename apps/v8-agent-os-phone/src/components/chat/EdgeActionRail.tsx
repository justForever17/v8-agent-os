import { memo, useEffect, useMemo, useRef, type ReactNode } from "react";
import { Animated, PanResponder, Pressable, StyleSheet, View } from "react-native";

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
    const closedOffset = side === "left" ? -(expandedWidth - collapsedPeekWidth) : expandedWidth - collapsedPeekWidth;

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

    return (
        <View pointerEvents="box-none" style={StyleSheet.absoluteFill}>
            {open ? <Pressable style={StyleSheet.absoluteFill} onPress={onClose} /> : null}
            <Animated.View
                {...panResponder.panHandlers}
                style={[
                    styles.rail,
                    side === "left" ? styles.leftRail : styles.rightRail,
                    {
                        top,
                        width: expandedWidth,
                        transform: [{ translateX }],
                        backgroundColor: themeMode === "dark" ? "rgba(24,24,27,0.82)" : "rgba(255,255,255,0.86)",
                        borderColor: `${colors.border}CC`,
                    },
                ]}
            >
                <Pressable
                    style={[
                        styles.peekHandle,
                        side === "left" ? styles.leftHandle : styles.rightHandle,
                        { borderColor: `${colors.border}CC`, backgroundColor: colors.surfaceStrong },
                    ]}
                    onPress={open ? onClose : onOpen}
                    hitSlop={10}
                />
                <View style={styles.content}>
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
        minHeight: 48,
        borderWidth: 1,
        borderRadius: radii.pill,
        paddingHorizontal: 4,
        paddingVertical: 4,
        shadowColor: "#0F172A",
        shadowOpacity: 0.10,
        shadowRadius: 18,
        shadowOffset: { width: 0, height: 8 },
        elevation: 5,
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
    peekHandle: {
        position: "absolute",
        top: 7,
        width: 18,
        height: 34,
        borderWidth: 1,
    },
    leftHandle: {
        right: -9,
        borderTopRightRadius: 999,
        borderBottomRightRadius: 999,
        borderLeftWidth: 0,
    },
    rightHandle: {
        left: -9,
        borderTopLeftRadius: 999,
        borderBottomLeftRadius: 999,
        borderRightWidth: 0,
    },
});
