import React, { createContext, forwardRef, useContext, useMemo } from "react";
import {
    Modal,
    Pressable,
    StyleSheet,
    Text,
    View,
    type PressableStateCallbackType,
    type PressableProps,
    type TextProps,
    type ViewProps,
} from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

type DialogContextValue = {
    open: boolean;
    onOpenChange?: (open: boolean) => void;
};

const DialogContext = createContext<DialogContextValue | null>(null);

export const Dialog = forwardRef<View, ViewProps & { open: boolean; onOpenChange?: (open: boolean) => void }>(
    function Dialog({ open, onOpenChange, children, ...rest }, ref) {
        const value = useMemo(() => ({ open, onOpenChange }), [onOpenChange, open]);
        return (
            <DialogContext.Provider value={value}>
                <View ref={ref} {...rest}>
                    {children}
                </View>
            </DialogContext.Provider>
        );
    },
);

export const DialogTrigger = forwardRef<View, PressableProps>(function DialogTrigger(
    { onPress, children, ...rest },
    ref,
) {
    const context = useContext(DialogContext);
    if (!context) {
        throw new Error("DialogTrigger must be used within Dialog");
    }
    return (
        <Pressable
            ref={ref}
            {...rest}
            onPress={(event) => {
                onPress?.(event);
                if (!event.defaultPrevented) {
                    context.onOpenChange?.(true);
                }
            }}
        >
            {renderPressableChildren(children, { pressed: false, hovered: false } as any)}
        </Pressable>
    );
});

export const DialogPortal = function DialogPortal({ children }: { children: React.ReactNode }) {
    return <>{children}</>;
};

export const DialogOverlay = forwardRef<View, PressableProps>(function DialogOverlay({ style, ...rest }, ref) {
    const { colors } = useUiPrefs();
    const staticStyle = typeof style === "function" ? undefined : style;
    const styleResolver = typeof style === "function" ? style : undefined;
    return (
        <Pressable
            ref={ref}
            {...rest}
            style={(state) => [
                StyleSheet.absoluteFillObject,
                {
                    backgroundColor: colors.overlay,
                },
                styleResolver ? styleResolver(state) : staticStyle,
            ]}
        />
    );
});

export const DialogClose = forwardRef<View, PressableProps>(function DialogClose(
    { onPress, children, ...rest },
    ref,
) {
    const context = useContext(DialogContext);
    if (!context) {
        throw new Error("DialogClose must be used within Dialog");
    }
    return (
        <Pressable
            ref={ref}
            {...rest}
            onPress={(event) => {
                onPress?.(event);
                if (!event.defaultPrevented) {
                    context.onOpenChange?.(false);
                }
            }}
        >
            {renderPressableChildren(children, { pressed: false, hovered: false } as any)}
        </Pressable>
    );
});

export const DialogContent = forwardRef<View, ViewProps & { showCloseButton?: boolean }>(function DialogContent(
    { style, children, showCloseButton = true, ...rest },
    ref,
) {
    const context = useContext(DialogContext);
    const { colors } = useUiPrefs();
    if (!context) {
        throw new Error("DialogContent must be used within Dialog");
    }

    return (
        <Modal transparent visible={context.open} animationType="fade" onRequestClose={() => context.onOpenChange?.(false)}>
            <View style={styles.overlayWrap}>
                <DialogOverlay onPress={() => context.onOpenChange?.(false)} />
                <View
                    ref={ref}
                    {...rest}
                    style={[
                        styles.content,
                        {
                            backgroundColor: colors.surface,
                            borderColor: colors.border,
                        },
                        style,
                    ]}
                >
                    {children}
                    {showCloseButton ? (
                        <DialogClose style={styles.closeButton}>
                            <Text style={[styles.closeLabel, { color: colors.textMuted }]}>×</Text>
                        </DialogClose>
                    ) : null}
                </View>
            </View>
        </Modal>
    );
});

export const DialogHeader = forwardRef<View, ViewProps>(function DialogHeader({ style, ...rest }, ref) {
    return <View ref={ref} {...rest} style={[styles.header, style]} />;
});

export const DialogFooter = forwardRef<View, ViewProps>(function DialogFooter({ style, ...rest }, ref) {
    return <View ref={ref} {...rest} style={[styles.footer, style]} />;
});

export const DialogTitle = forwardRef<Text, TextProps>(function DialogTitle({ style, ...rest }, ref) {
    const { colors } = useUiPrefs();
    return <Text ref={ref} {...rest} style={[styles.title, { color: colors.text }, style]} />;
});

export const DialogDescription = forwardRef<Text, TextProps>(function DialogDescription({ style, ...rest }, ref) {
    const { colors } = useUiPrefs();
    return <Text ref={ref} {...rest} style={[styles.description, { color: colors.textMuted }, style]} />;
});

const styles = StyleSheet.create({
    overlayWrap: {
        flex: 1,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 16,
    },
    content: {
        width: "100%",
        maxWidth: 520,
        borderRadius: radii.lg,
        borderWidth: 1,
        padding: 24,
        gap: spacing.md,
        shadowColor: "#0F172A",
        shadowOpacity: 0.12,
        shadowRadius: 24,
        shadowOffset: { width: 0, height: 10 },
        elevation: 8,
    },
    closeButton: {
        position: "absolute",
        right: 16,
        top: 16,
        width: 28,
        height: 28,
        alignItems: "center",
        justifyContent: "center",
        borderRadius: radii.sm,
    },
    closeLabel: {
        fontSize: 24,
        lineHeight: 24,
        fontWeight: "400",
    },
    header: {
        gap: 6,
    },
    footer: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "flex-end",
        gap: spacing.sm,
    },
    title: {
        fontSize: 18,
        lineHeight: 22,
        fontWeight: "600",
    },
    description: {
        fontSize: 14,
        lineHeight: 20,
    },
});

function renderPressableChildren(children: React.ReactNode | ((state: PressableStateCallbackType) => React.ReactNode), state: PressableStateCallbackType) {
    return typeof children === "function" ? children(state) : children;
}
