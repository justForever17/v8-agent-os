import { useEffect, useRef } from "react";
import { Animated, Easing, StyleSheet, View, useWindowDimensions } from "react-native";

function SpotlightBlob({ color }: { color: string }) {
    return (
        <View style={styles.spotlightParent}>
            <View style={[styles.spotlightOuter, { backgroundColor: color }]} />
            <View style={[styles.spotlightMiddle, { backgroundColor: color }]} />
            <View style={[styles.spotlightInner, { backgroundColor: color }]} />
        </View>
    );
}

export function PremiumBackground({ children }: { children?: React.ReactNode }) {
    const { width, height } = useWindowDimensions();
    const gridAnim = useRef(new Animated.Value(0)).current;
    const spotAAnim = useRef(new Animated.Value(0)).current;
    const spotBAnim = useRef(new Animated.Value(0)).current;

    const step = 45;
    const gridWidth = width + step * 2;
    const gridHeight = height + step * 2;
    const cols = Math.ceil(gridWidth / step);
    const rows = Math.ceil(gridHeight / step);

    useEffect(() => {
        const gridLoop = Animated.loop(
            Animated.timing(gridAnim, {
                toValue: 1,
                duration: 12000,
                easing: Easing.linear,
                useNativeDriver: true,
            })
        );
        const spotALoop = Animated.loop(
            Animated.timing(spotAAnim, {
                toValue: 1,
                duration: 22000,
                easing: Easing.linear,
                useNativeDriver: true,
            })
        );
        const spotBLoop = Animated.loop(
            Animated.timing(spotBAnim, {
                toValue: 1,
                duration: 28000,
                easing: Easing.linear,
                useNativeDriver: true,
            })
        );

        gridLoop.start();
        spotALoop.start();
        spotBLoop.start();

        return () => {
            gridLoop.stop();
            spotALoop.stop();
            spotBLoop.stop();
        };
    }, [gridAnim, spotAAnim, spotBAnim]);

    const gridTranslateX = gridAnim.interpolate({
        inputRange: [0, 1],
        outputRange: [0, -step],
    });
    const gridTranslateY = gridAnim.interpolate({
        inputRange: [0, 1],
        outputRange: [0, -step],
    });

    const spotATranslateX = spotAAnim.interpolate({
        inputRange: [0, 0.25, 0.5, 0.75, 1],
        outputRange: [-80, 80, 100, -60, -80],
    });
    const spotATranslateY = spotAAnim.interpolate({
        inputRange: [0, 0.25, 0.5, 0.75, 1],
        outputRange: [-60, -100, 60, 100, -60],
    });

    const spotBTranslateX = spotBAnim.interpolate({
        inputRange: [0, 0.25, 0.5, 0.75, 1],
        outputRange: [100, -60, -100, 80, 100],
    });
    const spotBTranslateY = spotBAnim.interpolate({
        inputRange: [0, 0.25, 0.5, 0.75, 1],
        outputRange: [80, 100, -80, -60, 80],
    });

    return (
        <View style={styles.container}>
            {/* Spotlight Blobs */}
            <Animated.View
                style={[
                    styles.spotlightWrap,
                    {
                        left: width * 0.2,
                        top: height * 0.3,
                        transform: [{ translateX: spotATranslateX }, { translateY: spotATranslateY }],
                    },
                ]}
            >
                <SpotlightBlob color="#6366F1" />
            </Animated.View>

            <Animated.View
                style={[
                    styles.spotlightWrap,
                    {
                        left: width * 0.6,
                        top: height * 0.5,
                        transform: [{ translateX: spotBTranslateX }, { translateY: spotBTranslateY }],
                    },
                ]}
            >
                <SpotlightBlob color="#06B6D4" />
            </Animated.View>

            {/* Moving Grid Container */}
            <View style={StyleSheet.absoluteFill} pointerEvents="none">
                <Animated.View
                    style={[
                        styles.gridContainer,
                        {
                            width: gridWidth,
                            height: gridHeight,
                            transform: [{ translateX: gridTranslateX }, { translateY: gridTranslateY }],
                        },
                    ]}
                >
                    {Array.from({ length: cols }).map((_, i) => (
                        <View
                            key={`col-${i}`}
                            style={[
                                styles.gridLineVertical,
                                { left: i * step },
                            ]}
                        />
                    ))}
                    {Array.from({ length: rows }).map((_, i) => (
                        <View
                            key={`row-${i}`}
                            style={[
                                styles.gridLineHorizontal,
                                { top: i * step },
                            ]}
                        />
                    ))}
                </Animated.View>
            </View>

            {/* Foreground Content */}
            {children}
        </View>
    );
}

const styles = StyleSheet.create({
    container: {
        flex: 1,
        backgroundColor: "#06070D",
        position: "relative",
        overflow: "hidden",
    },
    spotlightWrap: {
        position: "absolute",
        width: 1,
        height: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    spotlightParent: {
        position: "absolute",
        alignItems: "center",
        justifyContent: "center",
    },
    spotlightOuter: {
        position: "absolute",
        width: 320,
        height: 320,
        borderRadius: 160,
        opacity: 0.045,
    },
    spotlightMiddle: {
        position: "absolute",
        width: 200,
        height: 200,
        borderRadius: 100,
        opacity: 0.075,
    },
    spotlightInner: {
        position: "absolute",
        width: 100,
        height: 100,
        borderRadius: 50,
        opacity: 0.12,
    },
    gridContainer: {
        position: "absolute",
        left: 0,
        top: 0,
    },
    gridLineVertical: {
        position: "absolute",
        top: 0,
        bottom: 0,
        width: 1,
        backgroundColor: "rgba(255, 255, 255, 0.024)",
    },
    gridLineHorizontal: {
        position: "absolute",
        left: 0,
        right: 0,
        height: 1,
        backgroundColor: "rgba(255, 255, 255, 0.024)",
    },
});
