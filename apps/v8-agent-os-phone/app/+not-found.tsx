import { Link, Stack } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { GlassCard } from "@/src/components/common/GlassCard";
import { colors, spacing } from "@/src/theme/tokens";

export default function NotFoundScreen() {
    return (
        <>
            <Stack.Screen options={{ title: "Not Found" }} />
            <LinearGradient colors={[colors.background, "#FFF7ED"]} style={styles.gradient}>
                <SafeAreaView style={styles.safeArea}>
                    <View style={styles.container}>
                        <GlassCard>
                            <Text style={styles.title}>这个页面不存在</Text>
                            <Text style={styles.body}>当前手机端只承接 web 用户面的正式入口，请返回首页继续使用。</Text>
                            <Link href="/" style={styles.link}>
                                <Text style={styles.linkText}>返回首页</Text>
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
