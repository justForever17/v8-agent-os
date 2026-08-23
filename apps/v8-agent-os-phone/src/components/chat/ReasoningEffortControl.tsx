import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PanResponder, StyleSheet, Text, View, type LayoutChangeEvent } from "react-native";
import Animated, {
    Easing,
    cancelAnimation,
    runOnJS,
    useAnimatedStyle,
    useReducedMotion,
    useSharedValue,
    withDelay,
    withTiming,
    type SharedValue,
} from "react-native-reanimated";

import type { ThemeColors, ThemeMode } from "@/src/theme/tokens";

const PANEL_WIDTH = 316;
const PANEL_HEIGHT = 78;
const RAIL_HEIGHT = 26;
const THUMB_SIZE = 23;

function stopForIndex(index: number, count: number) {
    if (count <= 1) return 0;
    const safeIndex = Math.max(0, Math.min(count - 1, Math.round(index)));
    return safeIndex / (count - 1);
}

function nearestIndex(position: number, count: number) {
    if (count <= 1) return 0;
    const normalized = Math.max(0, Math.min(1, position));
    return Math.max(0, Math.min(count - 1, Math.round(normalized * (count - 1))));
}

function xForPosition(position: number, railWidth: number) {
    const inset = THUMB_SIZE / 2;
    return inset + Math.max(0, Math.min(1, position)) * Math.max(1, railWidth - inset * 2);
}

function positionForX(rawX: number, railWidth: number) {
    const inset = THUMB_SIZE / 2;
    return (rawX - inset) / Math.max(1, railWidth - inset * 2);
}

const FILL_COLUMNS = 52;
const FILL_ROWS = 7;
const FILL_LAYER_OFFSETS = [-0.06, -0.03, 0, 0.03, 0.06] as const;

function stableNoise(seed: number) {
    let value = seed | 0;
    value = Math.imul(value ^ (value >>> 16), 0x45d9f3b);
    value = Math.imul(value ^ (value >>> 16), 0x45d9f3b);
    value ^= value >>> 16;
    return (value >>> 0) / 0xffffffff;
}

const FILL_TEMPLATE = Array.from({ length: FILL_COLUMNS * FILL_ROWS }, (_, index) => {
    const column = Math.floor(index / FILL_ROWS);
    const row = index % FILL_ROWS;
    const seed = (column + 1) * 73856093 ^ (row + 1) * 19349663;
    const distance = 1 - column / Math.max(1, FILL_COLUMNS - 1);
    if (stableNoise(seed) < 0.07 + distance * 0.1) return null;
    return {
        x: column / Math.max(1, FILL_COLUMNS - 1),
        y: (row + 0.72) / FILL_ROWS,
        size: 1.8 + stableNoise(seed ^ 0x85ebca6b) * 0.55,
        opacity: 0.46 + stableNoise(seed ^ 0xc2b2ae35) * 0.42 - distance * 0.18,
        violet: stableNoise(seed ^ 0x27d4eb2f) > 0.72,
        layer: Math.min(FILL_LAYER_OFFSETS.length - 1, Math.floor(stableNoise(seed ^ 0x9e3779b9) * FILL_LAYER_OFFSETS.length)),
    };
}).filter((cell): cell is NonNullable<typeof cell> => Boolean(cell));

function FillRevealLayer({
    layer,
    progress,
    opacity,
    trailWidth,
    right,
}: {
    layer: number;
    progress: SharedValue<number>;
    opacity: SharedValue<number>;
    trailWidth: number;
    right: number;
}) {
    const offset = FILL_LAYER_OFFSETS[layer] || 0;
    const revealStyle = useAnimatedStyle(() => {
        const normalized = Math.max(0, Math.min(1, (progress.value - offset) / Math.max(0.01, 1 - offset)));
        return { width: normalized * trailWidth, opacity: opacity.value };
    }, [offset, opacity, trailWidth]);
    return (
        <Animated.View pointerEvents="none" style={[styles.fillReveal, { right }, revealStyle]}>
            <View style={[styles.fillGrid, { width: trailWidth }]}>
                {FILL_TEMPLATE.filter((cell) => cell.layer === layer).map((cell, index) => (
                    <View
                        key={`${layer}:${index}`}
                        style={{
                            position: "absolute",
                            left: cell.x * trailWidth,
                            top: cell.y * (RAIL_HEIGHT - 4),
                            width: cell.size,
                            height: cell.size,
                            opacity: cell.opacity,
                            backgroundColor: cell.violet ? "#8B5CF6" : "#D8B4FE",
                        }}
                    />
                ))}
            </View>
        </Animated.View>
    );
}

export type PhoneReasoningEffortControlProps<Level extends string> = {
    levels: readonly Level[];
    value: Level;
    onValueCommit: (level: Level) => void | Promise<void>;
    colors: ThemeColors;
    themeMode: ThemeMode;
    label: string;
    ariaLabel: string;
    labelFormatter: (level: Level) => string;
    disabled?: boolean;
};

export function PhoneReasoningEffortControl<Level extends string>({
    levels,
    value,
    onValueCommit,
    colors,
    themeMode,
    label,
    ariaLabel,
    labelFormatter,
    disabled = false,
}: PhoneReasoningEffortControlProps<Level>) {
    const effectiveLevels = useMemo(
        () => Array.from(new Set(levels.map((level) => String(level || "").trim()).filter(Boolean))) as Level[],
        [levels],
    );
    const safeLevels = effectiveLevels.length ? effectiveLevels : [value];
    const controlledIndex = Math.max(0, safeLevels.indexOf(value));
    const [railWidth, setRailWidth] = useState(PANEL_WIDTH - 38);
    const [draftIndex, setDraftIndex] = useState(controlledIndex);
    const [dragging, setDragging] = useState(false);
    const [pendingValue, setPendingValue] = useState<Level | null>(null);
    const [showFill, setShowFill] = useState(false);
    const reduceMotion = useReducedMotion();
    const latestDragXRef = useRef(xForPosition(stopForIndex(controlledIndex, safeLevels.length), railWidth));
    const dragStartXRef = useRef(latestDragXRef.current);
    const committedIndexRef = useRef(controlledIndex);
    const pendingValueRef = useRef<Level | null>(null);
    const valueRef = useRef(value);
    const fillCycleRef = useRef(0);
    const thumbX = useSharedValue(xForPosition(stopForIndex(controlledIndex, safeLevels.length), railWidth));
    const fillProgress = useSharedValue(0);
    const fillOpacity = useSharedValue(0);
    const maxTransition = useSharedValue(controlledIndex === safeLevels.length - 1 ? 1 : 0);
    const isMax = safeLevels.length > 1 && draftIndex === safeLevels.length - 1;
    const settledIsMax = isMax && !dragging;

    useEffect(() => {
        valueRef.current = value;
    }, [value]);

    useEffect(() => {
        if (pendingValue) {
            if (value === pendingValue) {
                pendingValueRef.current = null;
                setPendingValue(null);
                committedIndexRef.current = controlledIndex;
            }
            return;
        }
        if (dragging) return;
        setDraftIndex(controlledIndex);
        committedIndexRef.current = controlledIndex;
        const nextX = xForPosition(stopForIndex(controlledIndex, safeLevels.length), railWidth);
        latestDragXRef.current = nextX;
        cancelAnimation(thumbX);
        thumbX.value = reduceMotion ? nextX : withTiming(nextX, {
            duration: 180,
            easing: Easing.bezier(0.22, 1, 0.36, 1),
        });
    }, [controlledIndex, dragging, pendingValue, railWidth, reduceMotion, safeLevels.length, thumbX, value]);

    useEffect(() => {
        cancelAnimation(maxTransition);
        const target = settledIsMax ? 1 : 0;
        maxTransition.value = reduceMotion ? target : withTiming(target, {
            duration: settledIsMax ? 220 : 170,
            easing: Easing.bezier(0.16, 1, 0.3, 1),
        });
    }, [maxTransition, reduceMotion, settledIsMax]);

    const finishFill = useCallback((cycle: number) => {
        if (fillCycleRef.current === cycle) setShowFill(false);
    }, []);

    const startFill = useCallback(() => {
        const cycle = fillCycleRef.current + 1;
        fillCycleRef.current = cycle;
        cancelAnimation(fillProgress);
        cancelAnimation(fillOpacity);
        fillProgress.value = 0;
        fillOpacity.value = reduceMotion ? 0 : 1;
        if (reduceMotion) {
            setShowFill(false);
            return;
        }
        setShowFill(true);
        fillProgress.value = withDelay(55, withTiming(1, { duration: 980, easing: Easing.linear }));
        fillOpacity.value = withDelay(1105, withTiming(0, { duration: 180, easing: Easing.out(Easing.quad) }, (finished) => {
            if (finished) runOnJS(finishFill)(cycle);
        }));
    }, [fillOpacity, fillProgress, finishFill, reduceMotion]);

    const stopFill = useCallback(() => {
        fillCycleRef.current += 1;
        cancelAnimation(fillProgress);
        cancelAnimation(fillOpacity);
        fillProgress.value = 0;
        fillOpacity.value = 0;
        setShowFill(false);
    }, [fillOpacity, fillProgress]);

    useEffect(() => {
        if (reduceMotion) stopFill();
    }, [reduceMotion, stopFill]);

    useEffect(() => () => {
        cancelAnimation(fillProgress);
        cancelAnimation(fillOpacity);
    }, [fillOpacity, fillProgress]);

    const updateFromX = useCallback((rawX: number) => {
        const clamped = Math.max(THUMB_SIZE / 2, Math.min(railWidth - THUMB_SIZE / 2, rawX));
        latestDragXRef.current = clamped;
        thumbX.value = clamped;
        setDraftIndex(nearestIndex(positionForX(clamped, railWidth), safeLevels.length));
    }, [railWidth, safeLevels.length, thumbX]);

    const commitIndex = useCallback((index: number) => {
        const safeIndex = Math.max(0, Math.min(safeLevels.length - 1, index));
        const nextX = xForPosition(stopForIndex(safeIndex, safeLevels.length), railWidth);
        const previousCommittedIndex = committedIndexRef.current;
        committedIndexRef.current = safeIndex;
        latestDragXRef.current = nextX;
        setDraftIndex(safeIndex);
        if (safeIndex === safeLevels.length - 1 && previousCommittedIndex !== safeIndex) startFill();
        else if (safeIndex !== safeLevels.length - 1) stopFill();
        cancelAnimation(thumbX);
        thumbX.value = reduceMotion ? nextX : withTiming(nextX, {
            duration: 180,
            easing: Easing.bezier(0.22, 1, 0.36, 1),
        });
        const next = safeLevels[safeIndex];
        if (next && next !== value) {
            pendingValueRef.current = next;
            setPendingValue(next);
            void Promise.resolve(onValueCommit(next)).then(() => {
                setTimeout(() => {
                    if (pendingValueRef.current === next && valueRef.current !== next) {
                        pendingValueRef.current = null;
                        setPendingValue(null);
                    }
                }, 250);
            }).catch(() => {
                if (pendingValueRef.current === next) {
                    pendingValueRef.current = null;
                    setPendingValue(null);
                }
            });
        }
    }, [onValueCommit, railWidth, reduceMotion, safeLevels, startFill, stopFill, thumbX, value]);

    const commitFromX = useCallback((rawX: number) => {
        commitIndex(nearestIndex(positionForX(rawX, railWidth), safeLevels.length));
        setDragging(false);
    }, [commitIndex, railWidth, safeLevels.length]);

    const panResponder = useMemo(() => PanResponder.create({
        onStartShouldSetPanResponder: () => !disabled,
        onMoveShouldSetPanResponder: () => !disabled,
        onPanResponderGrant: (event) => {
            stopFill();
            setDragging(true);
            dragStartXRef.current = event.nativeEvent.locationX;
            latestDragXRef.current = event.nativeEvent.locationX;
            updateFromX(event.nativeEvent.locationX);
        },
        onPanResponderMove: (_event, gestureState) => updateFromX(dragStartXRef.current + gestureState.dx),
        onPanResponderRelease: () => commitFromX(latestDragXRef.current),
        onPanResponderTerminate: () => commitFromX(latestDragXRef.current),
    }), [commitFromX, disabled, stopFill, updateFromX]);

    const thumbStyle = useAnimatedStyle(() => ({
        transform: [
            { translateX: thumbX.value - THUMB_SIZE / 2 },
            { translateY: -THUMB_SIZE / 2 },
            { scale: dragging ? 0.96 : 1 },
        ],
    }), [dragging]);
    const normalLabelStyle = useAnimatedStyle(() => ({
        opacity: 1 - maxTransition.value,
        transform: [{ translateY: -2 * maxTransition.value }],
    }));
    const maxLabelStyle = useAnimatedStyle(() => ({
        opacity: maxTransition.value,
        transform: [{ translateY: 2 * (1 - maxTransition.value) }, { scale: 0.94 + 0.06 * maxTransition.value }],
    }));

    const handleLayout = (event: LayoutChangeEvent) => {
        const nextWidth = Math.max(1, event.nativeEvent.layout.width);
        setRailWidth(nextWidth);
        const nextX = xForPosition(stopForIndex(draftIndex, safeLevels.length), nextWidth);
        latestDragXRef.current = nextX;
        thumbX.value = nextX;
    };
    const currentLevel = safeLevels[draftIndex] || safeLevels[0] || value;
    const currentLabel = labelFormatter(currentLevel);
    const thumbCenter = xForPosition(stopForIndex(draftIndex, safeLevels.length), railWidth);
    const fillEnd = Math.max(2, thumbCenter - THUMB_SIZE * 0.34);
    const trailWidth = Math.max(1, Math.min(railWidth * 0.64, fillEnd - 2));
    const fillRight = Math.max(0, railWidth - fillEnd);
    const panelBackground = themeMode === "dark" ? "rgba(8,10,16,0.99)" : "rgba(255,255,255,0.98)";
    const railBackground = themeMode === "dark" ? "rgba(17,19,27,0.99)" : "rgba(239,241,247,0.99)";
    const edge = themeMode === "dark" ? "rgba(211,220,255,0.23)" : "rgba(15,23,42,0.14)";
    const railEdge = themeMode === "dark" ? "rgba(216,223,255,0.08)" : "rgba(15,23,42,0.09)";

    return (
        <View style={[styles.panel, { backgroundColor: panelBackground, borderColor: edge }]}>
            <View style={styles.head}>
                <View style={styles.labelRow}>
                    <Text style={[styles.title, { color: colors.text }]}>{label}</Text>
                    <View style={styles.valueSlot}>
                        <Animated.Text style={[styles.value, { color: colors.text }, normalLabelStyle]}>{currentLabel}</Animated.Text>
                        <Animated.Text style={[styles.value, styles.maxValue, maxLabelStyle]}>{currentLabel}</Animated.Text>
                    </View>
                </View>
                <View style={[styles.help, { borderColor: colors.text }]}>
                    <Text style={[styles.helpText, { color: colors.text }]}>?</Text>
                </View>
            </View>
            <View
                style={[styles.rail, { backgroundColor: railBackground, borderColor: railEdge }]}
                onLayout={handleLayout}
                accessible
                accessibilityRole="adjustable"
                accessibilityLabel={ariaLabel}
                accessibilityValue={{ min: 0, max: safeLevels.length - 1, now: draftIndex, text: currentLabel }}
                accessibilityActions={[{ name: "increment" }, { name: "decrement" }]}
                onAccessibilityAction={(event) => {
                    const delta = event.nativeEvent.actionName === "increment" ? 1 : -1;
                    const index = Math.max(0, Math.min(safeLevels.length - 1, draftIndex + delta));
                    commitIndex(index);
                }}
                {...panResponder.panHandlers}
            >
                {showFill ? FILL_LAYER_OFFSETS.map((_, layer) => (
                    <FillRevealLayer
                        key={layer}
                        layer={layer}
                        progress={fillProgress}
                        opacity={fillOpacity}
                        trailWidth={trailWidth}
                        right={fillRight}
                    />
                )) : null}
                {safeLevels.map((level, index) => (
                    <View
                        key={`${level}:${index}`}
                        pointerEvents="none"
                        style={[
                            styles.tick,
                            {
                                left: xForPosition(stopForIndex(index, safeLevels.length), railWidth),
                                backgroundColor: themeMode === "dark" ? "rgba(215,221,245,0.34)" : "rgba(100,116,139,0.45)",
                                opacity: settledIsMax ? 0.15 : 1,
                            },
                        ]}
                    />
                ))}
                {!showFill && !dragging ? (
                    <Text
                        pointerEvents="none"
                        numberOfLines={1}
                        style={[styles.railLabel, { color: colors.textMuted }]}
                    >
                        {currentLabel}
                    </Text>
                ) : null}
                <Animated.View
                    pointerEvents="none"
                    style={[
                        styles.thumb,
                        {
                            backgroundColor: themeMode === "dark" ? "#F5F6FB" : "#FFFFFF",
                            shadowOpacity: settledIsMax ? 0.48 : 0.26,
                            shadowColor: settledIsMax ? "#C5A9FF" : "#000000",
                        },
                        thumbStyle,
                    ]}
                >
                    <View style={styles.thumbDot} />
                </Animated.View>
            </View>
        </View>
    );
}

const styles = StyleSheet.create({
    panel: {
        width: "100%",
        maxWidth: PANEL_WIDTH,
        height: PANEL_HEIGHT,
        paddingHorizontal: 14,
        paddingTop: 13,
        paddingBottom: 10,
        borderWidth: 1,
        borderRadius: 15,
        overflow: "hidden",
        shadowColor: "#000000",
        shadowOpacity: 0.22,
        shadowRadius: 24,
        shadowOffset: { width: 0, height: 12 },
        elevation: 16,
    },
    head: {
        height: 15,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
    },
    labelRow: {
        minWidth: 0,
        flexDirection: "row",
        alignItems: "baseline",
        gap: 6,
    },
    title: {
        fontSize: 13,
        lineHeight: 15,
        fontWeight: "700",
        letterSpacing: -0.28,
    },
    valueSlot: {
        position: "relative",
        minWidth: 62,
        height: 15,
    },
    value: {
        position: "absolute",
        left: 0,
        top: 0,
        fontSize: 13,
        lineHeight: 15,
        fontWeight: "700",
        letterSpacing: -0.28,
    },
    maxValue: {
        color: "#A77CFF",
    },
    help: {
        width: 15,
        height: 15,
        borderWidth: 1,
        borderRadius: 999,
        alignItems: "center",
        justifyContent: "center",
        opacity: 0.82,
    },
    helpText: {
        fontSize: 9,
        lineHeight: 11,
        fontWeight: "700",
    },
    rail: {
        position: "relative",
        height: RAIL_HEIGHT,
        marginTop: 14,
        overflow: "hidden",
        borderWidth: 1,
        borderRadius: 8,
    },
    fillReveal: {
        position: "absolute",
        top: 0,
        zIndex: 1,
        height: RAIL_HEIGHT,
        overflow: "hidden",
    },
    fillGrid: {
        position: "absolute",
        top: 0,
        right: 0,
        height: RAIL_HEIGHT,
    },
    tick: {
        position: "absolute",
        top: "50%",
        width: 3,
        height: 3,
        marginLeft: -1.5,
        marginTop: -1.5,
        borderRadius: 999,
    },
    railLabel: {
        position: "absolute",
        top: 8,
        right: 34,
        left: 34,
        zIndex: 3,
        fontSize: 10,
        lineHeight: 12,
        fontWeight: "700",
        textAlign: "center",
        opacity: 0.72,
    },
    thumb: {
        position: "absolute",
        top: "50%",
        left: 0,
        width: THUMB_SIZE,
        height: THUMB_SIZE,
        borderRadius: 6,
        alignItems: "center",
        justifyContent: "center",
        shadowRadius: 10,
        shadowOffset: { width: 0, height: 5 },
        elevation: 8,
    },
    thumbDot: {
        width: 5,
        height: 5,
        borderRadius: 999,
        backgroundColor: "rgba(10,12,19,0.38)",
    },
});
