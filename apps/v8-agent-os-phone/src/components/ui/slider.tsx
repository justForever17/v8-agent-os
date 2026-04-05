import React, { forwardRef, useCallback, useEffect, useMemo, useState } from "react";
import {
    LayoutChangeEvent,
    StyleSheet,
    View,
    type GestureResponderEvent,
    type ViewProps,
} from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";

export type SliderProps = ViewProps & {
    value?: number[];
    defaultValue?: number[];
    min?: number;
    max?: number;
    step?: number;
    disabled?: boolean;
    onValueChange?: (value: number[]) => void;
};

export const Slider = forwardRef<View, SliderProps>(function Slider(
    {
        value,
        defaultValue = [0],
        min = 0,
        max = 100,
        step = 1,
        disabled = false,
        onValueChange,
        style,
        ...rest
    },
    ref,
) {
    const { colors } = useUiPrefs();
    const [trackWidth, setTrackWidth] = useState(0);
    const [internalValue, setInternalValue] = useState(defaultValue[0] ?? min);
    const currentValue = value?.[0] ?? internalValue;

    useEffect(() => {
        if (value?.[0] == null) return;
        setInternalValue(value[0]);
    }, [value]);

    const percent = useMemo(() => {
        if (max <= min) return 0;
        return Math.min(1, Math.max(0, (currentValue - min) / (max - min)));
    }, [currentValue, max, min]);

    const updateValue = useCallback((next: number) => {
        const stepped = Math.round(next / step) * step;
        const clamped = Math.min(max, Math.max(min, stepped));
        if (value == null) {
            setInternalValue(clamped);
        }
        onValueChange?.([clamped]);
    }, [max, min, onValueChange, step, value]);

    const handleGesture = useCallback((event: GestureResponderEvent) => {
        if (disabled || trackWidth <= 0) return;
        const x = event.nativeEvent.locationX;
        const ratio = Math.min(1, Math.max(0, x / trackWidth));
        updateValue(min + ratio * (max - min));
    }, [disabled, max, min, trackWidth, updateValue]);

    const handleLayout = useCallback((event: LayoutChangeEvent) => {
        setTrackWidth(event.nativeEvent.layout.width);
    }, []);

    return (
        <View ref={ref} {...rest} style={[styles.root, style]}>
            <View
                onLayout={handleLayout}
                onStartShouldSetResponder={() => !disabled}
                onMoveShouldSetResponder={() => !disabled}
                onResponderGrant={handleGesture}
                onResponderMove={handleGesture}
                style={[styles.track, { backgroundColor: colors.surfaceMuted, opacity: disabled ? 0.5 : 1 }]}
            >
                <View style={[styles.range, { width: `${percent * 100}%`, backgroundColor: colors.primary }]} />
                <View
                    style={[
                        styles.thumb,
                        {
                            left: Math.max(0, percent * trackWidth - 10),
                            borderColor: colors.primary,
                            backgroundColor: colors.surface,
                        },
                    ]}
                />
            </View>
        </View>
    );
});

const styles = StyleSheet.create({
    root: {
        width: "100%",
        justifyContent: "center",
    },
    track: {
        height: 8,
        width: "100%",
        borderRadius: 999,
        overflow: "visible",
        justifyContent: "center",
    },
    range: {
        position: "absolute",
        left: 0,
        top: 0,
        bottom: 0,
        borderRadius: 999,
    },
    thumb: {
        position: "absolute",
        top: -6,
        width: 20,
        height: 20,
        borderRadius: 999,
        borderWidth: 2,
        shadowColor: "#0F172A",
        shadowOpacity: 0.12,
        shadowRadius: 8,
        shadowOffset: { width: 0, height: 2 },
        elevation: 4,
    },
});
