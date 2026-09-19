import { X } from "lucide-react-native";
import { Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { RpaPanelContent } from "@/src/components/rpa/RpaPanelContent";
import { useUiPrefs } from "@/src/providers/ui-prefs";

export function PhoneRpaOverlay({ visible, onClose }: { visible: boolean; onClose: () => void }) {
    const { colors, themeMode, t } = useUiPrefs();

    return (
        <Modal
            visible={visible}
            transparent
            animationType="fade"
            presentationStyle="overFullScreen"
            statusBarTranslucent
            onRequestClose={onClose}
        >
            <SafeAreaView style={styles.safeArea} edges={["top", "right", "bottom", "left"]}>
                <View
                    style={[styles.backdrop, { backgroundColor: themeMode === "dark" ? "rgba(0,0,0,0.58)" : "rgba(15,23,42,0.28)" }]}
                >
                    <Pressable accessibilityRole="button" accessibilityLabel={t("phone.navigation.close")}
                        onPress={onClose} style={StyleSheet.absoluteFill} />
                    <View
                        style={[
                            styles.panel,
                            {
                                backgroundColor: colors.backgroundDeep,
                                borderColor: colors.border,
                                shadowOpacity: themeMode === "dark" ? 0.46 : 0.18,
                            },
                        ]}
                    >
                        <View style={[styles.header, { borderBottomColor: colors.border }]}>
                            <Text style={[styles.title, { color: colors.text }]}>{t("src.screens.rpascreen.title")}</Text>
                            <Pressable
                                accessibilityRole="button"
                                accessibilityLabel={t("phone.navigation.close")}
                                hitSlop={8}
                                onPress={onClose}
                                style={({ pressed }) => [
                                    styles.closeButton,
                                    { backgroundColor: pressed ? colors.surfaceMuted : "transparent" },
                                ]}
                            >
                                <X size={18} color={colors.textMuted} />
                            </Pressable>
                        </View>
                        <RpaPanelContent embedded />
                    </View>
                </View>
            </SafeAreaView>
        </Modal>
    );
}

const styles = StyleSheet.create({
    safeArea: { flex: 1 },
    backdrop: {
        flex: 1,
        alignItems: "center",
        justifyContent: "center",
        padding: 12,
    },
    panel: {
        width: "100%",
        maxWidth: 680,
        maxHeight: "100%",
        flexShrink: 1,
        overflow: "hidden",
        borderWidth: StyleSheet.hairlineWidth,
        borderRadius: 24,
        shadowColor: "#020617",
        shadowRadius: 34,
        shadowOffset: { width: 0, height: 18 },
        elevation: 18,
        borderCurve: "continuous",
    },
    header: {
        minHeight: 48,
        paddingHorizontal: 16,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        borderBottomWidth: StyleSheet.hairlineWidth,
    },
    title: { flex: 1, minWidth: 0, fontSize: 16, fontWeight: "900", paddingVertical: 10 },
    closeButton: {
        width: 44,
        height: 44,
        borderRadius: 17,
        alignItems: "center",
        justifyContent: "center",
    },
});
