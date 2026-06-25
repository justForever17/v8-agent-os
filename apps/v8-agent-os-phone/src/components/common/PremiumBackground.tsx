import { useEffect, useRef } from "react";
import { Animated, Easing, StyleSheet, View, DimensionValue } from "react-native";
import { LinearGradient } from "expo-linear-gradient";

interface StarCoord {
    top: DimensionValue;
    left: DimensionValue;
}

const STARS_1_COORDS: StarCoord[] = [
    { top: "8%", left: "12%" }, { top: "25%", left: "5%" }, { top: "40%", left: "22%" },
    { top: "12%", left: "55%" }, { top: "6%", left: "85%" }, { top: "30%", left: "70%" },
    { top: "52%", left: "10%" }, { top: "65%", left: "32%" }, { top: "72%", left: "82%" },
    { top: "82%", left: "15%" }, { top: "90%", left: "45%" }, { top: "60%", left: "58%" },
    { top: "42%", left: "92%" }, { top: "78%", left: "68%" }, { top: "95%", left: "88%" },
];

const STARS_2_COORDS: StarCoord[] = [
    { top: "4%", left: "32%" }, { top: "18%", left: "42%" }, { top: "35%", left: "12%" },
    { top: "15%", left: "74%" }, { top: "28%", left: "96%" }, { top: "55%", left: "48%" },
    { top: "74%", left: "4%" }, { top: "80%", left: "38%" }, { top: "64%", left: "86%" },
    { top: "86%", left: "62%" }, { top: "93%", left: "25%" }, { top: "48%", left: "78%" },
];

const STARS_3_COORDS: StarCoord[] = [
    { top: "16%", left: "28%" }, { top: "34%", left: "64%" }, { top: "45%", left: "6%" },
    { top: "58%", left: "34%" }, { top: "68%", left: "50%" }, { top: "76%", left: "92%" },
    { top: "88%", left: "75%" }, { top: "22%", left: "88%" }, { top: "2%", left: "60%" },
];

export function PremiumBackground({ children }: { children?: React.ReactNode }) {
    const twinkle1 = useRef(new Animated.Value(0)).current;
    const twinkle2 = useRef(new Animated.Value(0)).current;
    const twinkle3 = useRef(new Animated.Value(0)).current;

    const m1Val = useRef(new Animated.Value(0)).current;
    const m2Val = useRef(new Animated.Value(0)).current;
    const m3Val = useRef(new Animated.Value(0)).current;

    useEffect(() => {
        const runTwinkle = (val: Animated.Value, duration: number, delay = 0) => {
            Animated.loop(
                Animated.sequence([
                    Animated.delay(delay),
                    Animated.timing(val, {
                        toValue: 1,
                        duration: duration / 2,
                        easing: Easing.inOut(Easing.ease),
                        useNativeDriver: true,
                    }),
                    Animated.timing(val, {
                        toValue: 0,
                        duration: duration / 2,
                        easing: Easing.inOut(Easing.ease),
                        useNativeDriver: true,
                    }),
                ])
            ).start();
        };

        const runMeteor = (val: Animated.Value, duration: number, delay = 0) => {
            Animated.loop(
                Animated.sequence([
                    Animated.delay(delay),
                    Animated.timing(val, {
                        toValue: 1,
                        duration: duration,
                        easing: Easing.linear,
                        useNativeDriver: true,
                    }),
                ])
            ).start();
        };

        runTwinkle(twinkle1, 3000);
        runTwinkle(twinkle2, 5000, 1000);
        runTwinkle(twinkle3, 7000, 2000);

        runMeteor(m1Val, 8000);
        runMeteor(m2Val, 12000, 4000);
        runMeteor(m3Val, 10000, 2000);
    }, [twinkle1, twinkle2, twinkle3, m1Val, m2Val, m3Val]);

    const opacity1 = twinkle1.interpolate({
        inputRange: [0, 1],
        outputRange: [0.2, 1.0],
    });
    const opacity2 = twinkle2.interpolate({
        inputRange: [0, 1],
        outputRange: [0.2, 1.0],
    });
    const opacity3 = twinkle3.interpolate({
        inputRange: [0, 1],
        outputRange: [0.2, 1.0],
    });

    const m1ContainerStyle = {
        position: "absolute" as const,
        top: "10%" as DimensionValue,
        right: -80,
        transform: [{ rotate: "-35deg" as const }],
    };
    const m1AnimatedStyle = {
        opacity: m1Val.interpolate({
            inputRange: [0, 0.05, 0.12, 0.15, 1],
            outputRange: [0, 1, 1, 0, 0],
        }),
        transform: [
            {
                translateX: m1Val.interpolate({
                    inputRange: [0, 0.15, 1],
                    outputRange: [0, -1200, -1200],
                }),
            },
        ],
    };

    const m2ContainerStyle = {
        position: "absolute" as const,
        top: "30%" as DimensionValue,
        right: -80,
        transform: [{ rotate: "-35deg" as const }],
    };
    const m2AnimatedStyle = {
        opacity: m2Val.interpolate({
            inputRange: [0, 0.05, 0.12, 0.15, 1],
            outputRange: [0, 1, 1, 0, 0],
        }),
        transform: [
            {
                translateX: m2Val.interpolate({
                    inputRange: [0, 0.15, 1],
                    outputRange: [0, -1200, -1200],
                }),
            },
        ],
    };

    const m3ContainerStyle = {
        position: "absolute" as const,
        top: "50%" as DimensionValue,
        right: -80,
        transform: [{ rotate: "-35deg" as const }],
    };
    const m3AnimatedStyle = {
        opacity: m3Val.interpolate({
            inputRange: [0, 0.05, 0.12, 0.15, 1],
            outputRange: [0, 1, 1, 0, 0],
        }),
        transform: [
            {
                translateX: m3Val.interpolate({
                    inputRange: [0, 0.15, 1],
                    outputRange: [0, -1200, -1200],
                }),
            },
        ],
    };

    return (
        <View style={styles.container}>
            {/* Sky Background */}
            <View style={styles.skyCanvas} />

            {/* Moon */}
            <View style={styles.moon}>
                <View style={styles.moonBody} />
                <View style={styles.moonMask} />
            </View>

            {/* Twinkling Star Layers */}
            <Animated.View style={[StyleSheet.absoluteFill, { opacity: opacity1 }]} pointerEvents="none">
                {STARS_1_COORDS.map((coord, i) => (
                    <View key={`star1-${i}`} style={[styles.star, styles.star1, coord]} />
                ))}
            </Animated.View>

            <Animated.View style={[StyleSheet.absoluteFill, { opacity: opacity2 }]} pointerEvents="none">
                {STARS_2_COORDS.map((coord, i) => (
                    <View key={`star2-${i}`} style={[styles.star, styles.star2, coord]} />
                ))}
            </Animated.View>

            <Animated.View style={[StyleSheet.absoluteFill, { opacity: opacity3 }]} pointerEvents="none">
                {STARS_3_COORDS.map((coord, i) => (
                    <View key={`star3-${i}`} style={[styles.star, styles.star3, coord]} />
                ))}
            </Animated.View>

            {/* Shooting Stars */}
            <View style={m1ContainerStyle} pointerEvents="none">
                <Animated.View style={m1AnimatedStyle}>
                    <View style={styles.meteorPoint} />
                    <LinearGradient
                        colors={["rgba(255, 255, 255, 0.9)", "rgba(255, 255, 255, 0.0)"]}
                        start={{ x: 0, y: 0.5 }}
                        end={{ x: 1, y: 0.5 }}
                        style={styles.meteorTail}
                    />
                </Animated.View>
            </View>

            <View style={m2ContainerStyle} pointerEvents="none">
                <Animated.View style={m2AnimatedStyle}>
                    <View style={styles.meteorPoint} />
                    <LinearGradient
                        colors={["rgba(255, 255, 255, 0.9)", "rgba(255, 255, 255, 0.0)"]}
                        start={{ x: 0, y: 0.5 }}
                        end={{ x: 1, y: 0.5 }}
                        style={styles.meteorTail}
                    />
                </Animated.View>
            </View>

            <View style={m3ContainerStyle} pointerEvents="none">
                <Animated.View style={m3AnimatedStyle}>
                    <View style={styles.meteorPoint} />
                    <LinearGradient
                        colors={["rgba(255, 255, 255, 0.9)", "rgba(255, 255, 255, 0.0)"]}
                        start={{ x: 0, y: 0.5 }}
                        end={{ x: 1, y: 0.5 }}
                        style={styles.meteorTail}
                    />
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
        backgroundColor: "#050505",
        position: "relative",
        overflow: "hidden",
    },
    skyCanvas: {
        ...StyleSheet.absoluteFillObject,
        backgroundColor: "#050505",
    },
    star: {
        position: "absolute",
        backgroundColor: "#FFFFFF",
        borderRadius: 99,
    },
    star1: {
        width: 1.5,
        height: 1.5,
    },
    star2: {
        width: 2.0,
        height: 2.0,
    },
    star3: {
        width: 2.5,
        height: 2.5,
    },
    moon: {
        position: "absolute",
        top: "12%",
        right: "12%",
        width: 64,
        height: 64,
        borderRadius: 32,
        shadowColor: "#fdfbd3",
        shadowOffset: { width: 0, height: 0 },
        shadowOpacity: 0.36,
        shadowRadius: 16,
        elevation: 6,
        zIndex: 10,
    },
    moonBody: {
        width: "100%",
        height: "100%",
        borderRadius: 32,
        backgroundColor: "#fdfbd3",
    },
    moonMask: {
        position: "absolute",
        top: -12,
        left: -12,
        width: "100%",
        height: "100%",
        borderRadius: 32,
        backgroundColor: "#050505",
    },
    meteorPoint: {
        width: 2.5,
        height: 2.5,
        borderRadius: 99,
        backgroundColor: "#FFFFFF",
        shadowColor: "#FFFFFF",
        shadowOffset: { width: 0, height: 0 },
        shadowOpacity: 0.8,
        shadowRadius: 6,
        elevation: 2,
    },
    meteorTail: {
        position: "absolute",
        left: 2.5,
        top: 0.75,
        width: 80,
        height: 1,
    },
});
