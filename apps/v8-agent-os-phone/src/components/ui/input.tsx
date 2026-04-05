import React, { forwardRef } from "react";
import { StyleSheet, TextInput, type TextInputProps } from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";

export type InputProps = TextInputProps;

export const Input = forwardRef<TextInput, InputProps>(function Input({ style, placeholderTextColor, ...rest }, ref) {
    const { colors } = useUiPrefs();
    return (
        <TextInput
            ref={ref}
            placeholderTextColor={placeholderTextColor ?? colors.textSoft}
            {...rest}
            style={[
                styles.input,
                {
                    backgroundColor: colors.surface,
                    borderColor: colors.border,
                    color: colors.text,
                },
                style,
            ]}
        />
    );
});

const styles = StyleSheet.create({
    input: {
        width: "100%",
        minHeight: 40,
        borderRadius: radii.sm,
        borderWidth: 1,
        paddingHorizontal: 12,
        paddingVertical: 8,
        fontSize: 14,
        lineHeight: 18,
    },
});
