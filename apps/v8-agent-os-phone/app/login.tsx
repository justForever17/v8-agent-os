import { useEffect, useMemo, useState } from "react";
import {
    ActivityIndicator,
    Image,
    KeyboardAvoidingView,
    Modal,
    Platform,
    Pressable,
    StyleSheet,
    Text,
    TextInput,
    View,
} from "react-native";
import { Redirect, router, useLocalSearchParams, type Href } from "expo-router";
import { CameraView, useCameraPermissions, type BarcodeScanningResult } from "expo-camera";
import * as Linking from "expo-linking";
import { LinearGradient } from "expo-linear-gradient";
import { SafeAreaView } from "react-native-safe-area-context";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { GlassCard } from "@/src/components/common/GlassCard";
import { LocaleMenu } from "@/src/components/layout/LocaleMenu";
import { PhoneWordmark } from "@/src/components/layout/PhoneTopbar";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, radii, spacing } from "@/src/theme/tokens";

const BRAND_MARK = require("../assets/images/brand-mark.png");

export default function LoginScreen() {
    const { status, pairDevice } = useAppSession();
    const { t } = useUiPrefs();
    const incomingUrl = Linking.useURL();
    const [cameraPermission, requestCameraPermission] = useCameraPermissions();
    const { pairingUri: pairingUriParam } = useLocalSearchParams<{ pairingUri?: string }>();
    const [pairingUri, setPairingUri] = useState("");
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [scannerOpen, setScannerOpen] = useState(false);
    const [scanLocked, setScanLocked] = useState(false);

    useEffect(() => {
        const nextPairingUri = String(pairingUriParam || incomingUrl || "");
        if (!nextPairingUri.includes("://pair?")) {
            return;
        }
        setPairingUri(nextPairingUri);
        setError("");
    }, [incomingUrl, pairingUriParam]);

    const pageTitle = useMemo(() => t("app.login.connect_this_device"), [t]);
    const pageSubtitle = useMemo(() => t("app.login.open_or_paste_the_single_use_link_created_by_your_v8_os_owner"), [t]);

    if (status === "authenticated") {
        return <Redirect href={"/chat" as Href} />;
    }

    const resetError = () => setError("");

    const validate = () => {
        if (!pairingUri.trim()) {
            setError(t("app.login.please_enter_a_pairing_link"));
            return false;
        }
        return true;
    };

    const submit = async () => {
        if (!validate()) {
            return;
        }
        setBusy(true);
        setError("");
        try {
            await pairDevice({ pairingUri });
        } catch (nextError) {
            setError(
                nextError instanceof Error
                    ? nextError.message
                    : t("app.login.pairing_failed"),
            );
        } finally {
            setBusy(false);
        }
    };

    const openScanner = async () => {
        resetError();
        const permission = cameraPermission?.granted ? cameraPermission : await requestCameraPermission();
        if (!permission.granted) {
            setError(t("app.login.camera_permission_required"));
            return;
        }
        setScanLocked(false);
        setScannerOpen(true);
    };

    const handlePairingQrScanned = (result: BarcodeScanningResult) => {
        if (scanLocked) {
            return;
        }
        setScanLocked(true);
        const nextPairingUri = String(result.data || "").trim();
        setScannerOpen(false);
        if (!nextPairingUri.includes("://pair?")) {
            setError(t("app.login.invalid_pairing_link"));
            return;
        }
        setPairingUri(nextPairingUri);
        setError("");
    };

    return (
        <LinearGradient
            colors={["#EEF2FF", "#FFF7ED"]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={styles.gradient}
        >
            <SafeAreaView style={styles.safeArea}>
                <KeyboardAvoidingView
                    style={styles.keyboard}
                    behavior={Platform.OS === "ios" ? "padding" : undefined}
                >
                    <View style={styles.header}>
                        <View style={styles.headerTopRow}>
                            <View style={styles.brandRow}>
                                <Image source={BRAND_MARK} style={styles.brandMark} />
                                <View style={styles.brandTextWrap}>
                                    <PhoneWordmark dark={false} />
                                </View>
                            </View>
                            <LocaleMenu variant="default" />
                        </View>
                        <Text style={styles.title}>{pageTitle}</Text>
                        <Text style={styles.subtitle}>{pageSubtitle}</Text>
                    </View>

                    <GlassCard>
                        <View style={styles.form}>
                            <View style={styles.field}>
                                <View style={styles.labelRow}>
                                    <Text style={styles.label}>{t("app.login.pairing_link")}</Text>
                                    {Platform.OS === "web" ? null : (
                                        <Pressable style={styles.scanButton} onPress={() => void openScanner()}>
                                            <MaterialCommunityIcons name="qrcode-scan" size={16} color={colors.primaryDeep} />
                                            <Text style={styles.scanButtonText}>{t("app.login.scan_pairing_qr")}</Text>
                                        </Pressable>
                                    )}
                                </View>
                                <TextInput
                                    autoCapitalize="none"
                                    autoCorrect={false}
                                    value={pairingUri}
                                    onChangeText={(next) => {
                                        setPairingUri(next);
                                        resetError();
                                    }}
                                    placeholder="v8agentosphone://pair?..."
                                    placeholderTextColor={colors.textSoft}
                                    multiline
                                    style={[styles.input, styles.pairingInput]}
                                />
                                <Text style={styles.fieldHint}>{t("app.login.pairing_link_hint")}</Text>
                            </View>

                            {error ? (
                                <View style={styles.errorRow}>
                                    <MaterialCommunityIcons name="alert-circle-outline" size={16} color={colors.danger} />
                                    <Text style={styles.error}>{error}</Text>
                                </View>
                            ) : null}

                            <Pressable disabled={busy} onPress={() => void submit()} style={[styles.submit, busy && styles.disabled]}>
                                <LinearGradient
                                    colors={[colors.primary, colors.primaryDeep]}
                                    start={{ x: 0, y: 0 }}
                                    end={{ x: 1, y: 1 }}
                                    style={styles.submitGradient}
                                >
                                    {busy ? (
                                        <ActivityIndicator color="#FFFFFF" />
                                    ) : (
                                        <Text style={styles.submitText}>{t("app.login.connect_and_enter_v8_os_phone")}</Text>
                                    )}
                                </LinearGradient>
                            </Pressable>
                        </View>
                    </GlassCard>

                    <Pressable style={styles.connectHint} onPress={() => router.push("/connect" as Href)}>
                        <MaterialCommunityIcons name="lan-connect" size={16} color={colors.textMuted} />
                        <Text style={styles.connectHintText}>{t("app.login.check_the_connection_first")}</Text>
                    </Pressable>
                </KeyboardAvoidingView>
            </SafeAreaView>
            <Modal visible={scannerOpen} animationType="slide" transparent onRequestClose={() => setScannerOpen(false)}>
                <View style={styles.scannerOverlay}>
                    <View style={styles.scannerCard}>
                        <CameraView
                            active={scannerOpen}
                            facing="back"
                            barcodeScannerSettings={{ barcodeTypes: ["qr"] }}
                            onBarcodeScanned={scannerOpen && !scanLocked ? handlePairingQrScanned : undefined}
                            style={styles.scannerCamera}
                        />
                        <View style={styles.scannerShade}>
                            <View style={styles.scannerFrame} />
                            <Text style={styles.scannerTitle}>{t("app.login.scan_pairing_qr_title")}</Text>
                            <Text style={styles.scannerHint}>{t("app.login.point_camera_at_pairing_qr")}</Text>
                            <Pressable style={styles.scannerCancel} onPress={() => setScannerOpen(false)}>
                                <Text style={styles.scannerCancelText}>{t("app.login.cancel_scan")}</Text>
                            </Pressable>
                        </View>
                    </View>
                </View>
            </Modal>
        </LinearGradient>
    );
}

const styles = StyleSheet.create({
    gradient: {
        flex: 1,
    },
    safeArea: {
        flex: 1,
    },
    keyboard: {
        flex: 1,
        justifyContent: "center",
        paddingHorizontal: spacing.xl,
        gap: spacing.xl,
    },
    header: {
        gap: 10,
    },
    headerTopRow: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
    },
    brandRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 12,
        flexShrink: 1,
        minWidth: 0,
    },
    brandMark: {
        width: 42,
        height: 42,
        borderRadius: 14,
    },
    brandTextWrap: {
        flexDirection: "row",
        alignItems: "baseline",
        gap: 6,
        flexShrink: 1,
        minWidth: 0,
    },
    title: {
        color: colors.text,
        fontSize: 28,
        lineHeight: 36,
        fontWeight: "900",
    },
    subtitle: {
        color: colors.textMuted,
        fontSize: 15,
        lineHeight: 22,
    },
    form: {
        gap: spacing.lg,
    },
    field: {
        gap: 8,
    },
    labelRow: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 10,
    },
    label: {
        color: colors.text,
        fontSize: 14,
        fontWeight: "700",
    },
    scanButton: {
        minHeight: 32,
        borderRadius: radii.pill,
        borderWidth: 1,
        borderColor: "rgba(124,58,237,0.22)",
        backgroundColor: "rgba(124,58,237,0.06)",
        paddingHorizontal: 10,
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
    },
    scanButtonText: {
        color: colors.primaryDeep,
        fontSize: 12,
        fontWeight: "900",
    },
    input: {
        borderWidth: 1,
        borderColor: colors.border,
        borderRadius: radii.md,
        backgroundColor: colors.surface,
        paddingHorizontal: 14,
        paddingVertical: 14,
        color: colors.text,
        fontSize: 16,
    },
    pairingInput: {
        minHeight: 92,
        textAlignVertical: "top",
        paddingTop: spacing.md,
    },
    fieldHint: {
        color: colors.textMuted,
        fontSize: 12,
        lineHeight: 18,
    },
    errorRow: {
        flexDirection: "row",
        alignItems: "center",
        gap: 8,
    },
    error: {
        flex: 1,
        color: colors.danger,
        fontSize: 13,
        lineHeight: 20,
    },
    submit: {
        borderRadius: radii.lg,
        overflow: "hidden",
    },
    submitGradient: {
        minHeight: 54,
        borderRadius: radii.lg,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: 18,
    },
    submitText: {
        color: "#FFFFFF",
        fontSize: 16,
        fontWeight: "800",
        textAlign: "center",
    },
    disabled: {
        opacity: 0.7,
    },
    connectHint: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
    },
    connectHintText: {
        color: colors.textMuted,
        fontSize: 13,
        fontWeight: "700",
    },
    scannerOverlay: {
        flex: 1,
        backgroundColor: "rgba(15,23,42,0.64)",
        justifyContent: "center",
        padding: spacing.xl,
    },
    scannerCard: {
        minHeight: 480,
        borderRadius: radii.xl,
        overflow: "hidden",
        backgroundColor: "#020617",
    },
    scannerCamera: {
        ...StyleSheet.absoluteFillObject,
    },
    scannerShade: {
        flex: 1,
        alignItems: "center",
        justifyContent: "center",
        padding: spacing.xl,
        backgroundColor: "rgba(2,6,23,0.22)",
    },
    scannerFrame: {
        width: 238,
        height: 238,
        borderRadius: 26,
        borderWidth: 3,
        borderColor: "rgba(255,255,255,0.92)",
        backgroundColor: "rgba(255,255,255,0.02)",
    },
    scannerTitle: {
        marginTop: spacing.xl,
        color: "#FFFFFF",
        fontSize: 20,
        fontWeight: "900",
    },
    scannerHint: {
        marginTop: 8,
        color: "rgba(255,255,255,0.78)",
        fontSize: 13,
        textAlign: "center",
        lineHeight: 20,
    },
    scannerCancel: {
        marginTop: spacing.xl,
        minHeight: 42,
        borderRadius: radii.pill,
        paddingHorizontal: 20,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(255,255,255,0.16)",
        borderWidth: 1,
        borderColor: "rgba(255,255,255,0.24)",
    },
    scannerCancelText: {
        color: "#FFFFFF",
        fontSize: 14,
        fontWeight: "900",
    },
});
