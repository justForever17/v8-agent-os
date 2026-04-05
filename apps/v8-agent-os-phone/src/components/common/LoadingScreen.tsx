import { ActivityIndicator, Image, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";

import { PhoneWordmark } from "@/src/components/layout/PhoneTopbar";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors } from "@/src/theme/tokens";

const BRAND_MARK = require("../../../assets/images/brand-mark.png");

export function LoadingScreen({ label }: { label?: string }) {
    const { colors: palette, themeMode, t } = useUiPrefs();
    const subtitle = label || t("正在准备你的运行时工作区…", "Preparing your runtime workspace...");

    return (
        <LinearGradient
            colors={themeMode === "dark" ? ["#050816", "#0B1223", "#101B38"] : ["#FCFCFF", "#F7F8FF", "#EEF4FF"]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={styles.container}
        >
            <View style={[styles.markWrap, { backgroundColor: themeMode === "dark" ? "rgba(15,23,42,0.74)" : "rgba(255,255,255,0.82)", borderColor: `${palette.border}A6` }]}>
                <Image source={BRAND_MARK} style={styles.mark} />
            </View>
            <View style={styles.wordmarkWrap}>
                <PhoneWordmark dark={themeMode === "dark"} text="V8 Agent OS" fontSize={31} />
            </View>
            <Text style={[styles.label, { color: palette.textMuted }]}>{subtitle}</Text>
            <View style={styles.progressRow}>
                <ActivityIndicator size="small" color={colors.primary} />
                <Text style={[styles.progressText, { color: palette.textMuted }]}>{t("系统启动中", "System booting")}</Text>
            </View>
        </LinearGradient>
    );
}

const styles = StyleSheet.create({
    container: {
        flex: 1,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 32,
        gap: 18,
    },
    markWrap: {
        width: 76,
        height: 76,
        borderRadius: 24,
        alignItems: "center",
        justifyContent: "center",
        borderWidth: 1,
        shadowColor: "#0F172A",
        shadowOpacity: 0.08,
        shadowRadius: 16,
        shadowOffset: { width: 0, height: 10 },
        elevation: 4,
    },
    mark: {
        width: 64,
        height: 64,
        borderRadius: 20,
    },
    wordmarkWrap: {
        minHeight: 40,
        alignItems: "center",
        justifyContent: "center",
    },
    label: {
        fontSize: 15,
        lineHeight: 22,
        textAlign: "center",
    },
    progressRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
    },
    progressText: {
        fontSize: 12,
        fontWeight: "600",
        letterSpacing: 0.2,
    },
});
