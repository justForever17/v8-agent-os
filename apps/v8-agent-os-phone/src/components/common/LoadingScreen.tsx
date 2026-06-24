import { ActivityIndicator, Image, StyleSheet, Text, View } from "react-native";

import { PhoneWordmark } from "@/src/components/layout/PhoneTopbar";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors } from "@/src/theme/tokens";
import { PremiumBackground } from "./PremiumBackground";

const BRAND_MARK = require("../../../assets/images/brand-mark.png");

export function LoadingScreen({ label }: { label?: string }) {
    const { colors: palette, t } = useUiPrefs();
    const subtitle = label || t("src.components.common.loadingscreen.preparing_your_runtime_workspace");

    return (
        <PremiumBackground>
            <View style={styles.container}>
                <View style={[styles.markWrap, { backgroundColor: "rgba(15,23,42,0.74)", borderColor: "rgba(255,255,255,0.08)" }]}>
                    <Image source={BRAND_MARK} style={styles.mark} />
                </View>
                <View style={styles.wordmarkWrap}>
                    <PhoneWordmark dark={true} text="V8 Agent OS" fontSize={31} />
                </View>
                <Text style={[styles.label, { color: "rgba(255,255,255,0.6)" }]}>{subtitle}</Text>
                <View style={styles.progressRow}>
                    <ActivityIndicator size="small" color={colors.primary} />
                    <Text style={[styles.progressText, { color: "rgba(255,255,255,0.46)" }]}>
                        {t("src.components.common.loadingscreen.system_booting")}
                    </Text>
                </View>
            </View>
        </PremiumBackground>
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
