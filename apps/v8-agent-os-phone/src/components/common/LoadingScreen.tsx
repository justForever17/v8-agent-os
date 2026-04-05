import { ActivityIndicator, StyleSheet, Text, View } from "react-native";

import { colors } from "@/src/theme/tokens";

export function LoadingScreen({ label = "正在准备手机端工作区…" }: { label?: string }) {
    return (
        <View style={styles.container}>
            <ActivityIndicator size="large" color={colors.primary} />
            <Text style={styles.label}>{label}</Text>
        </View>
    );
}

const styles = StyleSheet.create({
    container: {
        flex: 1,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: colors.backgroundDeep,
        gap: 14,
    },
    label: {
        color: colors.textMuted,
        fontSize: 15,
    },
});
