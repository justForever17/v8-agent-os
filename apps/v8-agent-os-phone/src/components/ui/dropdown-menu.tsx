import React, { createContext, forwardRef, useContext, useMemo, useRef, useState } from "react";
import {
    Dimensions,
    Modal,
    Pressable,
    StyleSheet,
    Text,
    View,
    type LayoutRectangle,
    type PressableStateCallbackType,
    type PressableProps,
    type TextProps,
    type ViewStyle,
    type ViewProps,
} from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

type DropdownMenuContextValue = {
    open: boolean;
    setOpen: (next: boolean) => void;
    triggerLayout: LayoutRectangle | null;
    setTriggerLayout: (layout: LayoutRectangle | null) => void;
};

type DropdownMenuRadioContextValue = {
    value?: string;
    onValueChange?: (value: string) => void;
};

const DropdownMenuContext = createContext<DropdownMenuContextValue | null>(null);
const DropdownMenuRadioContext = createContext<DropdownMenuRadioContextValue | null>(null);

export const DropdownMenu = forwardRef<View, ViewProps>(function DropdownMenu({ children, ...rest }, ref) {
    const [open, setOpen] = useState(false);
    const [triggerLayout, setTriggerLayout] = useState<LayoutRectangle | null>(null);
    const value = useMemo(() => ({ open, setOpen, triggerLayout, setTriggerLayout }), [open, triggerLayout]);

    return (
        <DropdownMenuContext.Provider value={value}>
            <View ref={ref} {...rest}>
                {children}
            </View>
        </DropdownMenuContext.Provider>
    );
});

export const DropdownMenuTrigger = forwardRef<View, PressableProps>(function DropdownMenuTrigger(
    { children, onPress, ...rest },
    ref,
) {
    const context = useContext(DropdownMenuContext);
    const localRef = useRef<View | null>(null);
    if (!context) {
        throw new Error("DropdownMenuTrigger must be used within DropdownMenu");
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
            onPress={(event) => {
                onPress?.(event);
                if (!event.defaultPrevented) {
                    localRef.current?.measureInWindow((x, y, width, height) => {
                        context.setTriggerLayout({ x, y, width, height });
                        context.setOpen(true);
                    });
                }
            }}
        >
            {renderPressableChildren(children, { pressed: false, hovered: false })}
        </Pressable>
    );
});

export function DropdownMenuPortal({ children }: { children: React.ReactNode }) {
    return <>{children}</>;
}

export function DropdownMenuGroup({ children }: { children: React.ReactNode }) {
    return <>{children}</>;
}

export function DropdownMenuSub({ children }: { children: React.ReactNode }) {
    return <>{children}</>;
}

export const DropdownMenuContent = forwardRef<View, ViewProps & { align?: "start" | "end"; screenPadding?: number; sideOffset?: number }>(function DropdownMenuContent(
    { align = "start", children, screenPadding = 8, style, sideOffset = 4, ...rest },
    ref,
) {
    const context = useContext(DropdownMenuContext);
    const { colors } = useUiPrefs();
    if (!context || !context.open || !context.triggerLayout) {
        return null;
    }

    const top = context.triggerLayout.y + context.triggerLayout.height + sideOffset;
    const flattenedStyle = StyleSheet.flatten(style) as ViewStyle | undefined;
    const screenWidth = Dimensions.get("window").width;
    const styleWidth = Number(flattenedStyle?.width || flattenedStyle?.minWidth || 180);
    const measuredWidth = Number.isFinite(styleWidth) && styleWidth > 0 ? styleWidth : 180;
    const preferredLeft = align === "end"
        ? context.triggerLayout.x + context.triggerLayout.width - measuredWidth
        : context.triggerLayout.x;
    const maxLeft = Math.max(screenPadding, screenWidth - measuredWidth - screenPadding);
    const left = Math.min(Math.max(screenPadding, preferredLeft), maxLeft);

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
                            backgroundColor: colors.surface,
                            borderColor: colors.border,
                        },
                        style,
                    ]}
                >
                    {children}
                </View>
            </Pressable>
        </Modal>
    );
});

export const DropdownMenuItem = forwardRef<View, PressableProps & { inset?: boolean }>(function DropdownMenuItem(
    { children, inset, style, onPress, ...rest },
    ref,
) {
    const context = useContext(DropdownMenuContext);
    const { colors } = useUiPrefs();
    const staticStyle = typeof style === "function" ? undefined : style;
    const styleResolver = typeof style === "function" ? style : undefined;
    return (
        <Pressable
            ref={ref}
            {...rest}
            onPress={(event) => {
                onPress?.(event);
                if (!event.defaultPrevented) {
                    context?.setOpen(false);
                }
            }}
            style={({ pressed }) => [
                styles.item,
                { paddingLeft: inset ? 32 : 10, backgroundColor: pressed ? colors.surfaceMuted : "transparent" },
                styleResolver ? styleResolver({ pressed, hovered: false }) : staticStyle,
            ]}
        >
            {typeof children === "string"
                ? <Text style={[styles.itemText, { color: colors.text }]}>{children}</Text>
                : renderPressableChildren(children, { pressed: false, hovered: false })}
        </Pressable>
    );
});

export const DropdownMenuCheckboxItem = forwardRef<
    View,
    PressableProps & { checked?: boolean; children: React.ReactNode }
>(function DropdownMenuCheckboxItem({ checked, children, style, ...rest }, ref) {
    const { colors } = useUiPrefs();
    return (
        <DropdownMenuItem
            ref={ref}
            {...rest}
            style={style}
        >
            <Text style={[styles.indicator, { color: colors.text }]}>{checked ? "✓" : ""}</Text>
            {typeof children === "string" ? <Text style={[styles.itemText, { color: colors.text }]}>{children}</Text> : children}
        </DropdownMenuItem>
    );
});

export const DropdownMenuRadioGroup = function DropdownMenuRadioGroup({
    value,
    onValueChange,
    children,
}: {
    value?: string;
    onValueChange?: (value: string) => void;
    children: React.ReactNode;
}) {
    const contextValue = useMemo(() => ({ value, onValueChange }), [onValueChange, value]);
    return <DropdownMenuRadioContext.Provider value={contextValue}>{children}</DropdownMenuRadioContext.Provider>;
};

export const DropdownMenuRadioItem = forwardRef<
    View,
    PressableProps & { value: string; children: React.ReactNode }
>(function DropdownMenuRadioItem({ value, children, onPress, style, ...rest }, ref) {
    const radio = useContext(DropdownMenuRadioContext);
    const context = useContext(DropdownMenuContext);
    const { colors } = useUiPrefs();
    const checked = radio?.value === value;
    const staticStyle = typeof style === "function" ? undefined : style;
    const styleResolver = typeof style === "function" ? style : undefined;

    return (
        <Pressable
            ref={ref}
            {...rest}
            onPress={(event) => {
                onPress?.(event);
                if (!event.defaultPrevented) {
                    radio?.onValueChange?.(value);
                    context?.setOpen(false);
                }
            }}
            style={({ pressed }) => [
                styles.item,
                { paddingLeft: 32, backgroundColor: pressed ? colors.surfaceMuted : "transparent" },
                styleResolver ? styleResolver({ pressed, hovered: false }) : staticStyle,
            ]}
        >
            <Text style={[styles.indicator, { color: colors.text }]}>{checked ? "●" : ""}</Text>
            {typeof children === "string"
                ? <Text style={[styles.itemText, { color: colors.text }]}>{children}</Text>
                : renderPressableChildren(children, { pressed: false, hovered: false })}
        </Pressable>
    );
});

export const DropdownMenuLabel = forwardRef<Text, TextProps & { inset?: boolean }>(function DropdownMenuLabel(
    { inset, style, ...rest },
    ref,
) {
    const { colors } = useUiPrefs();
    return <Text ref={ref} {...rest} style={[styles.label, { color: colors.text, paddingLeft: inset ? 32 : 10 }, style]} />;
});

export const DropdownMenuSeparator = forwardRef<View, ViewProps>(function DropdownMenuSeparator({ style, ...rest }, ref) {
    const { colors } = useUiPrefs();
    return <View ref={ref} {...rest} style={[styles.separator, { backgroundColor: colors.border }, style]} />;
});

export function DropdownMenuShortcut({ children, style, ...rest }: TextProps) {
    const { colors } = useUiPrefs();
    return (
        <Text {...rest} style={[styles.shortcut, { color: colors.textSoft }, style]}>
            {children}
        </Text>
    );
}

export const DropdownMenuSubTrigger = forwardRef<View, PressableProps & { inset?: boolean }>(function DropdownMenuSubTrigger(
    { children, inset, style, ...rest },
    ref,
) {
    const { colors } = useUiPrefs();
    const staticStyle = typeof style === "function" ? undefined : style;
    const styleResolver = typeof style === "function" ? style : undefined;
    return (
        <Pressable
            ref={ref}
            {...rest}
            style={({ pressed }) => [
                styles.item,
                { paddingLeft: inset ? 32 : 10, backgroundColor: pressed ? colors.surfaceMuted : "transparent" },
                styleResolver ? styleResolver({ pressed, hovered: false }) : staticStyle,
            ]}
        >
            {typeof children === "string"
                ? <Text style={[styles.itemText, { color: colors.text }]}>{children}</Text>
                : renderPressableChildren(children, { pressed: false, hovered: false })}
            <Text style={[styles.chevron, { color: colors.textSoft }]}>›</Text>
        </Pressable>
    );
});

export const DropdownMenuSubContent = forwardRef<View, ViewProps>(function DropdownMenuSubContent({ style, ...rest }, ref) {
    const { colors } = useUiPrefs();
    return <View ref={ref} {...rest} style={[styles.content, { backgroundColor: colors.surface, borderColor: colors.border }, style]} />;
});

const styles = StyleSheet.create({
    content: {
        position: "absolute",
        minWidth: 160,
        maxWidth: 280,
        borderRadius: radii.sm,
        borderWidth: 1,
        paddingVertical: 4,
        shadowColor: "#0F172A",
        shadowOpacity: 0.12,
        shadowRadius: 16,
        shadowOffset: { width: 0, height: 8 },
        elevation: 8,
    },
    item: {
        minHeight: 36,
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
        paddingRight: 10,
        paddingVertical: 8,
        borderRadius: radii.sm,
        marginHorizontal: 4,
    },
    itemText: {
        flex: 1,
        fontSize: 14,
        lineHeight: 18,
    },
    indicator: {
        width: 12,
        textAlign: "center",
        fontSize: 12,
    },
    label: {
        paddingVertical: 8,
        paddingRight: 10,
        fontSize: 14,
        lineHeight: 18,
        fontWeight: "600",
    },
    separator: {
        height: StyleSheet.hairlineWidth,
        marginHorizontal: 4,
        marginVertical: 4,
    },
    shortcut: {
        marginLeft: "auto",
        fontSize: 11,
        letterSpacing: 0.4,
    },
    chevron: {
        marginLeft: "auto",
        fontSize: 16,
        lineHeight: 16,
    },
});

function renderPressableChildren(
    children: React.ReactNode | ((state: PressableStateCallbackType) => React.ReactNode),
    state: PressableStateCallbackType,
) {
    return typeof children === "function" ? children(state) : children;
}
