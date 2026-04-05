import React, { createContext, forwardRef, useContext, useMemo, useRef, useState } from "react";
import {
    Modal,
    Pressable,
    StyleSheet,
    Text,
    View,
    type LayoutRectangle,
    type PressableStateCallbackType,
    type PressableProps,
    type TextProps,
    type ViewProps,
} from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

type TooltipContextValue = {
    open: boolean;
    setOpen: (next: boolean) => void;
    triggerLayout: LayoutRectangle | null;
    setTriggerLayout: (layout: LayoutRectangle | null) => void;
};

const TooltipContext = createContext<TooltipContextValue | null>(null);

export function TooltipProvider({ children }: { children: React.ReactNode }) {
    return <>{children}</>;
}

export const Tooltip = forwardRef<View, ViewProps>(function Tooltip({ children, ...rest }, ref) {
    const [open, setOpen] = useState(false);
    const [triggerLayout, setTriggerLayout] = useState<LayoutRectangle | null>(null);
    const value = useMemo(() => ({ open, setOpen, triggerLayout, setTriggerLayout }), [open, triggerLayout]);

    return (
        <TooltipContext.Provider value={value}>
            <View ref={ref} {...rest}>
                {children}
            </View>
        </TooltipContext.Provider>
    );
});

export const TooltipTrigger = forwardRef<View, PressableProps>(function TooltipTrigger(
    { children, onLongPress, onPressOut, ...rest },
    ref,
) {
    const context = useContext(TooltipContext);
    const localRef = useRef<View | null>(null);
    if (!context) {
        throw new Error("TooltipTrigger 必须放在 Tooltip 内使用");
    }

    return (
        <Pressable
            ref={(node) => {
                localRef.current = node as unknown as View | null;
                if (typeof ref === "function") {
                    ref(node);
                } else if (ref) {
                    ref.current = node;
                }
            }}
            {...rest}
            onLongPress={(event) => {
                onLongPress?.(event);
                localRef.current?.measureInWindow((x, y, width, height) => {
                    context.setTriggerLayout({ x, y, width, height });
                    context.setOpen(true);
                });
            }}
            onPressOut={(event) => {
                onPressOut?.(event);
                context.setOpen(false);
            }}
        >
            {renderPressableChildren(children, { pressed: false, hovered: false })}
        </Pressable>
    );
});

export const TooltipContent = forwardRef<View, ViewProps & { sideOffset?: number }>(function TooltipContent(
    { children, sideOffset = 4, style, ...rest },
    ref,
) {
    const context = useContext(TooltipContext);
    const { colors } = useUiPrefs();
    if (!context || !context.open || !context.triggerLayout) {
        return null;
    }

    const top = Math.max(8, context.triggerLayout.y - sideOffset - 40);
    const left = Math.max(8, context.triggerLayout.x + context.triggerLayout.width / 2 - 80);

    return (
        <Modal transparent visible animationType="fade" onRequestClose={() => context.setOpen(false)}>
            <Pressable style={StyleSheet.absoluteFill} onPress={() => context.setOpen(false)}>
                <View
                    ref={ref}
                    {...rest}
                    style={[
                        styles.content,
                        {
                            top,
                            left,
                            backgroundColor: colors.primary,
                        },
                        style,
                    ]}
                >
                    {typeof children === "string" ? (
                        <Text style={styles.contentText}>{children}</Text>
                    ) : (
                        children
                    )}
                </View>
            </Pressable>
        </Modal>
    );
});

export const TooltipBubble = forwardRef<Text, TextProps & { text: string }>(function TooltipBubble(
    { text, style, ...rest },
    ref,
) {
    return (
        <Text ref={ref} {...rest} style={[styles.contentText, style]}>
            {text}
        </Text>
    );
});

const styles = StyleSheet.create({
    content: {
        position: "absolute",
        minWidth: 120,
        maxWidth: 220,
        borderRadius: radii.sm,
        paddingHorizontal: spacing.sm,
        paddingVertical: spacing.xs,
    },
    contentText: {
        color: "#FFFFFF",
        fontSize: 12,
        lineHeight: 16,
        fontWeight: "500",
    },
});

function renderPressableChildren(
    children: React.ReactNode | ((state: PressableStateCallbackType) => React.ReactNode),
    state: PressableStateCallbackType,
) {
    return typeof children === "function" ? children(state) : children;
}
