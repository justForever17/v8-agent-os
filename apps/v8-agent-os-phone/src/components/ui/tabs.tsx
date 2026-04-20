import React, { createContext, forwardRef, useContext, useMemo } from "react";
import {
    Pressable,
    StyleSheet,
    Text,
    View,
    type PressableStateCallbackType,
    type PressableProps,
    type ViewProps,
} from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

type TabsContextValue = {
    value: string;
    onValueChange?: (value: string) => void;
};

const TabsContext = createContext<TabsContextValue | null>(null);

export const Tabs = forwardRef<View, ViewProps & { value: string; onValueChange?: (value: string) => void }>(
    function Tabs({ value, onValueChange, children, ...rest }, ref) {
        const contextValue = useMemo(() => ({ value, onValueChange }), [onValueChange, value]);
        return (
            <TabsContext.Provider value={contextValue}>
                <View ref={ref} {...rest}>
                    {children}
                </View>
            </TabsContext.Provider>
        );
    },
);

export const TabsList = forwardRef<View, ViewProps>(function TabsList({ style, ...rest }, ref) {
    const { colors, themeMode } = useUiPrefs();
    return (
        <View
            ref={ref}
            {...rest}
            style={[
                styles.list,
                {
                    backgroundColor: themeMode === "dark" ? "rgba(30,41,59,0.72)" : "rgba(241,245,249,0.9)",
                },
                style,
            ]}
        />
    );
});

export const TabsTrigger = forwardRef<View, PressableProps & { value: string }>(function TabsTrigger(
    { style, value, children, ...rest },
    ref,
) {
    const context = useContext(TabsContext);
    const { colors, themeMode } = useUiPrefs();
    if (!context) {
        throw new Error("TabsTrigger must be used within Tabs");
    }
    const active = context.value === value;

    const staticStyle = typeof style === "function" ? undefined : style;
    const styleResolver = typeof style === "function" ? style : undefined;

    return (
        <Pressable
            ref={ref}
            {...rest}
            onPress={(event) => {
                rest.onPress?.(event);
                if (!event.defaultPrevented) {
                    context.onValueChange?.(value);
                }
            }}
            style={({ pressed }) => [
                styles.trigger,
                {
                    backgroundColor: active
                        ? colors.surface
                        : "transparent",
                    shadowOpacity: active ? 0.08 : 0,
                    borderColor: active
                        ? (themeMode === "dark" ? "rgba(148,163,184,0.18)" : "rgba(226,232,240,0.94)")
                        : "transparent",
                    opacity: pressed ? 0.88 : 1,
                },
                styleResolver ? styleResolver({ pressed, hovered: false }) : staticStyle,
            ]}
        >
            {typeof children === "string" ? (
                <Text style={[styles.triggerLabel, { color: active ? colors.text : colors.textMuted }]}>
                    {children}
                </Text>
            ) : typeof children === "function" ? (
                children({ pressed: false, hovered: false } satisfies PressableStateCallbackType)
            ) : (
                children
            )}
        </Pressable>
    );
});

export const TabsContent = forwardRef<View, ViewProps & { value: string }>(function TabsContent(
    { value, children, ...rest },
    ref,
) {
    const context = useContext(TabsContext);
    if (!context) {
        throw new Error("TabsContent must be used within Tabs");
    }
    if (context.value !== value) {
        return null;
    }
    return (
        <View ref={ref} {...rest} style={[styles.content, rest.style]}>
            {children}
        </View>
    );
});

const styles = StyleSheet.create({
    list: {
        minHeight: 40,
        flexDirection: "row",
        alignItems: "center",
        borderRadius: radii.md,
        padding: 4,
        gap: 4,
    },
    trigger: {
        minHeight: 32,
        paddingHorizontal: 12,
        paddingVertical: 6,
        borderRadius: radii.sm,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
        shadowColor: "#0F172A",
        shadowRadius: 8,
        shadowOffset: { width: 0, height: 2 },
    },
    content: {
        marginTop: spacing.sm,
    },
    triggerLabel: {
        fontSize: 14,
        lineHeight: 18,
        fontWeight: "500",
    },
});
