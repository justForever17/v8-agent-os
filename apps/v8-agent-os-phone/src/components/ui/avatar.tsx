import React, { forwardRef, memo } from "react";
import {
    Image,
    StyleSheet,
    Text,
    View,
    type ImageProps,
    type TextProps,
    type ViewProps,
} from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";

type AvatarRootProps = ViewProps & {
    size?: number;
};

type AvatarImageProps = Omit<ImageProps, "source"> & {
    uri?: string | null;
};

type AvatarFallbackProps = TextProps & {
    label?: string | null;
};

type LegacyAvatarProps = {
    uri?: string | null;
    label?: string | null;
    size?: number;
};

const AvatarRootContext = React.createContext<{ size: number } | null>(null);

export const Avatar = Object.assign(
    memo(function LegacyAvatar({ uri, label, size = 40 }: LegacyAvatarProps) {
        return (
            <AvatarRoot size={size}>
                {uri ? <AvatarImage uri={uri} /> : <AvatarFallback label={label} />}
            </AvatarRoot>
        );
    }),
    {
        Root: forwardRef<View, AvatarRootProps>(function AvatarRoot({ size = 40, style, children, ...rest }, ref) {
            return (
                <AvatarRootContext.Provider value={{ size }}>
                    <View
                        ref={ref}
                        {...rest}
                        style={[
                            styles.root,
                            {
                                width: size,
                                height: size,
                                borderRadius: size / 2,
                            },
                            style,
                        ]}
                    >
                        {children}
                    </View>
                </AvatarRootContext.Provider>
            );
        }),
        Image: forwardRef<Image, AvatarImageProps>(function AvatarImage({ uri, style, ...rest }, ref) {
            const context = React.useContext(AvatarRootContext);
            const size = context?.size ?? 40;
            if (!uri) return null;
            return (
                <Image
                    ref={ref}
                    source={{ uri }}
                    {...rest}
                    style={[
                        styles.image,
                        {
                            width: size,
                            height: size,
                            borderRadius: size / 2,
                        },
                        style,
                    ]}
                />
            );
        }),
        Fallback: forwardRef<Text, AvatarFallbackProps>(function AvatarFallback({ label, style, children, ...rest }, ref) {
            const { colors } = useUiPrefs();
            const context = React.useContext(AvatarRootContext);
            const size = context?.size ?? 40;
            const fallback = String(children ?? label ?? "?").trim().slice(0, 1).toUpperCase() || "?";

            return (
                <View
                    style={[
                        styles.fallback,
                        {
                            width: size,
                            height: size,
                            borderRadius: size / 2,
                            backgroundColor: colors.surfaceMuted,
                            borderColor: colors.border,
                        },
                    ]}
                >
                    <Text
                        ref={ref}
                        {...rest}
                        style={[
                            styles.fallbackText,
                            {
                                color: colors.text,
                                fontSize: Math.max(12, Math.floor(size * 0.34)),
                            },
                            style,
                        ]}
                    >
                        {fallback}
                    </Text>
                </View>
            );
        }),
    },
);

const AvatarRoot = Avatar.Root;
const AvatarImage = Avatar.Image;
const AvatarFallback = Avatar.Fallback;

export { AvatarRoot, AvatarImage, AvatarFallback };

const styles = StyleSheet.create({
    root: {
        overflow: "hidden",
        flexShrink: 0,
    },
    image: {
        resizeMode: "cover",
    },
    fallback: {
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    fallbackText: {
        fontWeight: "700",
    },
});
