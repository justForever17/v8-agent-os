import { Link, Stack } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { GlassCard } from "@/src/components/common/GlassCard";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, spacing } from "@/src/theme/tokens";

export default function NotFoundScreen() {
    const { t } = useUiPrefs();

    return (
        <>
            <Stack.Screen options={{ title: t("app.not_found.not_found") }} />
            <LinearGradient colors={[colors.background, "#FFF7ED"]} style={styles.gradient}>
                <SafeAreaView style={styles.safeArea}>
                    <View style={styles.container}>
                        <GlassCard>
                            <Text style={styles.title}>{t("app.not_found.this_page_does_not_exist")}</Text>
                            <Text style={styles.body}>
                                {t("app.not_found.this_phone_shell_only_exposes_the_formal_web_user_entry_points_return_home_to_continue")}
                            </Text>
                            <Link href="/" style={styles.link}>
                                <Text style={styles.linkText}>{t("app.not_found.back_home")}</Text>
                            </Link>
                        </GlassCard>
                    </View>
                </SafeAreaView>
            </LinearGradient>
        </>
    );
}

const styles = StyleSheet.create({
    gradient: {
        flex: 1,
    },
    safeArea: {
        flex: 1,
    },
    container: {
        flex: 1,
        justifyContent: "center",
        paddingHorizontal: spacing.xl,
    },
    title: {
        color: colors.text,
        fontSize: 22,
        fontWeight: "900",
        marginBottom: spacing.sm,
    },
    body: {
        color: colors.textMuted,
        fontSize: 14,
        lineHeight: 22,
        marginBottom: spacing.lg,
    },
    link: {
        alignSelf: "flex-start",
    },
    linkText: {
        color: colors.primary,
        fontSize: 14,
        fontWeight: "800",
    },
});
