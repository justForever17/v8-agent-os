import { memo, useEffect, useMemo, useRef, type ReactNode } from "react";
import { Animated, PanResponder, Pressable, StyleSheet, View } from "react-native";

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
                        backgroundColor: "transparent",
                        borderColor: "transparent",
                    },
                ]}
            >
                {!open ? (
                    <Pressable
                        accessibilityRole="button"
                        style={[
                            styles.collapsedPressTarget,
                            side === "left" ? styles.collapsedPressTargetLeft : styles.collapsedPressTargetRight,
                            { width: collapsedPeekWidth + 18 },
                        ]}
                        onPress={onOpen}
                        hitSlop={8}
                    />
                ) : null}
                <Pressable
                    style={[
                        styles.peekHandle,
                        side === "left" ? styles.leftHandle : styles.rightHandle,
                        { borderColor: "transparent", backgroundColor: "transparent" },
                    ]}
                    onPress={open ? onClose : onOpen}
                    hitSlop={10}
                />
                <View pointerEvents={open ? "auto" : "none"} style={styles.content}>
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
        borderWidth: 0,
        borderRadius: radii.pill,
        paddingHorizontal: 2,
        paddingVertical: 2,
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
    collapsedPressTarget: {
        position: "absolute",
        top: 0,
        bottom: 0,
        zIndex: 4,
        backgroundColor: "transparent",
    },
    collapsedPressTargetLeft: {
        right: 0,
    },
    collapsedPressTargetRight: {
        left: 0,
    },
    peekHandle: {
        position: "absolute",
        top: 7,
        width: 20,
        height: 38,
        borderWidth: 0,
        zIndex: 5,
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
