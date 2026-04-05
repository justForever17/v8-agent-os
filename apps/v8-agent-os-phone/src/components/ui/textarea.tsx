import React, { forwardRef } from "react";
import { StyleSheet, TextInput, type TextInputProps } from "react-native";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";

export type TextareaProps = TextInputProps;

export const Textarea = forwardRef<TextInput, TextareaProps>(function Textarea(
    { style, multiline = true, textAlignVertical = "top", placeholderTextColor, ...rest },
    ref,
) {
    const { colors } = useUiPrefs();
    return (
        <TextInput
            ref={ref}
            multiline={multiline}
            textAlignVertical={textAlignVertical}
            placeholderTextColor={placeholderTextColor ?? colors.textSoft}
            {...rest}
            style={[
                styles.textarea,
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
    textarea: {
        width: "100%",
        minHeight: 80,
        borderRadius: radii.sm,
        borderWidth: 1,
        paddingHorizontal: 12,
        paddingVertical: 10,
        fontSize: 14,
        lineHeight: 20,
    },
});
