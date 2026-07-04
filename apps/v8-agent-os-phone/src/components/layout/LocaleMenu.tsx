import React from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { ChevronDown } from "lucide-react-native";

import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuLabel,
    DropdownMenuRadioGroup,
    DropdownMenuRadioItem,
    DropdownMenuTrigger,
} from "@/src/components/ui/dropdown-menu";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii } from "@/src/theme/tokens";

const LOCALE_OPTIONS = [
    { value: "zh-CN", flag: "🇨🇳", labelKey: "shared.locale.zh_cn_native" },
    { value: "en", flag: "🇺🇸", labelKey: "shared.locale.en_native" },
] as const;

export function LocaleMenu({
    variant = "compact",
}: {
    variant?: "compact" | "default";
}) {
    const { locale, setLocale, colors, t } = useUiPrefs();
    const current = LOCALE_OPTIONS.find((item) => item.value === locale) || LOCALE_OPTIONS[0];
    const compact = variant === "compact";

    return (
        <DropdownMenu>
            <DropdownMenuTrigger
                accessibilityLabel={t("shared.locale.switch_language")}
                accessibilityRole="button"
                style={[
                    styles.trigger,
                    compact ? styles.triggerCompact : styles.triggerDefault,
                    {
                        backgroundColor: "transparent",
                        borderColor: "transparent",
                    },
                ]}
            >
                <Text style={compact ? styles.flagCompact : styles.flagDefault}>{current.flag}</Text>
                {!compact ? (
                    <Text style={[styles.label, { color: colors.textMuted }]} numberOfLines={1}>
                        {t(current.labelKey)}
                    </Text>
                ) : null}
                <ChevronDown color={colors.textSoft} size={compact ? 12 : 14} strokeWidth={2.2} />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" sideOffset={8} style={{ minWidth: compact ? 176 : 196 }}>
                <DropdownMenuLabel>{t("shared.locale.switch_language")}</DropdownMenuLabel>
                <DropdownMenuRadioGroup
                    value={locale}
                    onValueChange={(next) => void setLocale(next as "zh-CN" | "en")}
                >
                    {LOCALE_OPTIONS.map((option) => (
                        <DropdownMenuRadioItem key={option.value} value={option.value}>
                            <View style={styles.optionRow}>
                                <Text style={styles.optionFlag}>{option.flag}</Text>
                                <View style={styles.optionTextWrap}>
                                    <Text style={[styles.optionLabel, { color: colors.text }]}>
                                        {t(option.labelKey)}
                                    </Text>
                                    <Text style={[styles.optionMeta, { color: colors.textSoft }]}>
                                        {option.value}
                                    </Text>
                                </View>
                            </View>
                        </DropdownMenuRadioItem>
                    ))}
                </DropdownMenuRadioGroup>
            </DropdownMenuContent>
        </DropdownMenu>
    );
}

const styles = StyleSheet.create({
    trigger: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        borderWidth: StyleSheet.hairlineWidth,
        shadowColor: "#0F172A",
        shadowOpacity: 0,
        shadowRadius: 14,
        shadowOffset: { width: 0, height: 4 },
        elevation: 0,
    },
    triggerCompact: {
        width: 40,
        height: 32,
        gap: 2,
        borderRadius: 11,
    },
    triggerDefault: {
        minWidth: 128,
        height: 40,
        paddingHorizontal: 12,
        gap: 8,
        borderRadius: radii.lg,
    },
    flagCompact: {
        fontSize: 15,
    },
    flagDefault: {
        fontSize: 18,
    },
    label: {
        fontSize: 13,
        fontWeight: "700",
        flexShrink: 1,
    },
    optionRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
        flex: 1,
    },
    optionFlag: {
        fontSize: 18,
    },
    optionTextWrap: {
        flex: 1,
        gap: 1,
    },
    optionLabel: {
        fontSize: 14,
        lineHeight: 18,
        fontWeight: "700",
    },
    optionMeta: {
        fontSize: 11,
        lineHeight: 14,
        letterSpacing: 0.3,
        textTransform: "uppercase",
    },
});
