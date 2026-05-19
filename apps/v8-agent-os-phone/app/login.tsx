import { useEffect, useMemo, useState } from "react";
import {
    ActivityIndicator,
    Image,
    KeyboardAvoidingView,
    Platform,
    Pressable,
    StyleSheet,
    Text,
    TextInput,
    View,
} from "react-native";
import { Redirect, router, type Href } from "expo-router";
import { LinearGradient } from "expo-linear-gradient";
import { SafeAreaView } from "react-native-safe-area-context";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { GlassCard } from "@/src/components/common/GlassCard";
import { LocaleMenu } from "@/src/components/layout/LocaleMenu";
import { PhoneWordmark } from "@/src/components/layout/PhoneTopbar";
import {
    type AdminConnectionProfile,
    readActiveAdminConnectionProfileId,
    readAdminConnectionProfiles,
} from "@/src/lib/admin-connection-profiles";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, radii, spacing } from "@/src/theme/tokens";

const BRAND_MARK = require("../assets/images/brand-mark.png");

type Mode = "login" | "register";

export default function LoginScreen() {
    const { status, adminBaseUrl, signIn, signUp, user } = useAppSession();
    const { t } = useUiPrefs();
    const defaultWebBaseUrl = useMemo(() => {
        if (Platform.OS !== "web" || typeof window === "undefined") {
            return "";
        }
        const hostname = window.location.hostname || "";
        if (hostname === "localhost" || hostname === "127.0.0.1" || hostname === "[::1]") {
            return "http://127.0.0.1:9528";
        }
        return "";
    }, []);
    const [mode, setMode] = useState<Mode>("login");
    const [baseUrl, setBaseUrl] = useState(adminBaseUrl || defaultWebBaseUrl);
    const [login, setLogin] = useState("");
    const [password, setPassword] = useState("");
    const [name, setName] = useState("");
    const [email, setEmail] = useState("");
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const [profiles, setProfiles] = useState<AdminConnectionProfile[]>([]);
    const [activeProfileId, setActiveProfileId] = useState("");

    useEffect(() => {
        if (adminBaseUrl) {
            setBaseUrl(adminBaseUrl);
            return;
        }
        if (defaultWebBaseUrl) {
            setBaseUrl((current) => current || defaultWebBaseUrl);
        }
    }, [adminBaseUrl, defaultWebBaseUrl]);

    useEffect(() => {
        let cancelled = false;
        const hydrateSavedConnection = async () => {
            const [profiles, activeId] = await Promise.all([
                readAdminConnectionProfiles(),
                readActiveAdminConnectionProfileId(),
            ]);
            const activeProfile = profiles.find((profile) => profile.id === activeId) || profiles[0];
            if (cancelled) {
                return;
            }
            setProfiles(profiles);
            setActiveProfileId(activeId || "");
            if (!adminBaseUrl && !defaultWebBaseUrl && activeProfile?.adminBaseUrl) {
                setBaseUrl((current) => current || activeProfile.adminBaseUrl);
            }
        };
        void hydrateSavedConnection();
        return () => {
            cancelled = true;
        };
    }, [adminBaseUrl, defaultWebBaseUrl]);

    const pageTitle = useMemo(
        () => (mode === "login" ? t("app.login.welcome_back") : t("app.login.create_account")),
        [mode, t],
    );
    const pageSubtitle = useMemo(
        () =>
            mode === "login"
                ? t("app.login.sign_in_with_the_same_account_you_use_on_web_phone_connects_to_admin_as_the_user_surface_bff")
                : t("app.login.this_mirrors_the_web_sign_up_flow_after_registration_you_will_be_signed_in_automatically_and_enter_the_same_user_runtime_lane_as_web"),
        [mode, t],
    );

    if (status === "authenticated") {
        return <Redirect href={(user?.mustChangePassword ? "/settings" : "/chat") as Href} />;
    }

    const resetError = () => setError("");

    const validate = () => {
        if (!baseUrl.trim()) {
            setError(t("app.login.please_enter_a_reachable_admin_url"));
            return false;
        }
        if (!login.trim() || !password.trim()) {
            setError(t("app.login.please_enter_your_login_and_password"));
            return false;
        }
        if (mode === "register" && !name.trim()) {
            setError(t("app.login.display_name_is_required_when_registering"));
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
            if (mode === "login") {
                await signIn({ adminBaseUrl: baseUrl, login, password });
                return;
            }
            await signUp({
                adminBaseUrl: baseUrl,
                login,
                password,
                name,
                email: email.trim() || undefined,
            });
        } catch (nextError) {
            setError(
                nextError instanceof Error
                    ? nextError.message
                    : mode === "login"
                        ? t("app.login.sign_in_failed")
                        : t("app.login.registration_failed"),
            );
        } finally {
            setBusy(false);
        }
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
                                    <Text style={styles.brandSub}>Phone</Text>
                                </View>
                            </View>
                            <LocaleMenu variant="default" />
                        </View>
                        <Text style={styles.title}>{pageTitle}</Text>
                        <Text style={styles.subtitle}>{pageSubtitle}</Text>
                    </View>

                    <GlassCard>
                        <View style={styles.modeSwitch}>
                            <Pressable
                                style={[styles.modeButton, mode === "login" && styles.modeButtonActive]}
                                onPress={() => {
                                    setMode("login");
                                    resetError();
                                }}
                            >
                                <Text style={[styles.modeText, mode === "login" && styles.modeTextActive]}>{t("app.login.sign_in")}</Text>
                            </Pressable>
                            <Pressable
                                style={[styles.modeButton, mode === "register" && styles.modeButtonActive]}
                                onPress={() => {
                                    setMode("register");
                                    resetError();
                                }}
                            >
                                <Text style={[styles.modeText, mode === "register" && styles.modeTextActive]}>{t("app.login.register")}</Text>
                            </Pressable>
                        </View>

                        <View style={styles.form}>
                            <View style={styles.field}>
                                <Text style={styles.label}>{t("app.login.admin_url")}</Text>
                                <TextInput
                                    autoCapitalize="none"
                                    autoCorrect={false}
                                    value={baseUrl}
                                    onChangeText={(next) => {
                                        setBaseUrl(next);
                                        resetError();
                                    }}
                                    placeholder={defaultWebBaseUrl || "http://192.168.x.x:9528"}
                                    placeholderTextColor={colors.textSoft}
                                    style={styles.input}
                                />
                            </View>

                            {mode === "register" ? (
                                <View style={styles.field}>
                                    <Text style={styles.label}>{t("app.login.display_name")}</Text>
                                    <TextInput
                                        value={name}
                                        onChangeText={(next) => {
                                            setName(next);
                                            resetError();
                                        }}
                                        placeholder={t("app.login.how_should_we_address_you")}
                                        placeholderTextColor={colors.textSoft}
                                        style={styles.input}
                                    />
                                </View>
                            ) : null}

                            <View style={styles.field}>
                                <Text style={styles.label}>{t("app.login.login")}</Text>
                                <TextInput
                                    autoCapitalize="none"
                                    autoCorrect={false}
                                    value={login}
                                    onChangeText={(next) => {
                                        setLogin(next);
                                        resetError();
                                    }}
                                    placeholder={mode === "login" ? t("app.login.use_the_same_login_or_email_as_web") : t("app.login.choose_a_login_name")}
                                    placeholderTextColor={colors.textSoft}
                                    style={styles.input}
                                />
                            </View>

                            {mode === "register" ? (
                                <View style={styles.field}>
                                    <Text style={styles.label}>{t("app.login.email")}</Text>
                                    <TextInput
                                        autoCapitalize="none"
                                        autoCorrect={false}
                                        keyboardType="email-address"
                                        value={email}
                                        onChangeText={(next) => {
                                            setEmail(next);
                                            resetError();
                                        }}
                                        placeholder={t("app.login.optional_useful_for_avatar_sync_and_notifications")}
                                        placeholderTextColor={colors.textSoft}
                                        style={styles.input}
                                    />
                                </View>
                            ) : null}

                            <View style={styles.field}>
                                <Text style={styles.label}>{mode === "login" ? t("app.login.password") : t("app.login.set_password")}</Text>
                                <TextInput
                                    secureTextEntry
                                    value={password}
                                    onChangeText={(next) => {
                                        setPassword(next);
                                        resetError();
                                    }}
                                    placeholder="••••••"
                                    placeholderTextColor={colors.textSoft}
                                    style={styles.input}
                                />
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
                                        <Text style={styles.submitText}>
                                            {mode === "login"
                                                ? t("app.login.sign_in_to_v8_os_phone")
                                                : t("app.login.create_account_and_enter_v8_os_phone")}
                                        </Text>
                                    )}
                                </LinearGradient>
                            </Pressable>
                        </View>
                    </GlassCard>

                    {profiles.length > 0 ? (
                        <GlassCard style={styles.savedConnectionsCard}>
                            <View style={styles.savedHeaderRow}>
                                <Text style={styles.savedTitle}>{t("src.screens.connectscreen.saved_targets")}</Text>
                                <Text style={styles.savedHint}>{t("src.screens.connectscreen.reconnect")}</Text>
                            </View>
                            <View style={styles.savedProfilesList}>
                                {profiles.slice(0, 3).map((profile) => {
                                    const active = profile.id === activeProfileId || profile.adminBaseUrl === baseUrl;
                                    return (
                                        <Pressable
                                            key={profile.id}
                                            style={[styles.savedProfileCard, active && styles.savedProfileCardActive]}
                                            onPress={() => {
                                                setBaseUrl(profile.adminBaseUrl);
                                                resetError();
                                            }}
                                        >
                                            <View style={styles.savedProfileBody}>
                                                <Text style={styles.savedProfileTitle} numberOfLines={1}>
                                                    {profile.label || profile.adminBaseUrl}
                                                </Text>
                                                <Text style={styles.savedProfileUrl} numberOfLines={1}>
                                                    {profile.adminBaseUrl}
                                                </Text>
                                            </View>
                                            {active ? (
                                                <View style={styles.savedCurrentBadge}>
                                                    <Text style={styles.savedCurrentBadgeText}>{t("src.screens.connectscreen.current")}</Text>
                                                </View>
                                            ) : null}
                                        </Pressable>
                                    );
                                })}
                            </View>
                        </GlassCard>
                    ) : null}

                    <Pressable style={styles.connectHint} onPress={() => router.push("/connect" as Href)}>
                        <MaterialCommunityIcons name="lan-connect" size={16} color={colors.textMuted} />
                        <Text style={styles.connectHintText}>{t("app.login.check_the_connection_first")}</Text>
                    </Pressable>
                </KeyboardAvoidingView>
            </SafeAreaView>
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
    brandSub: {
        color: colors.textMuted,
        fontSize: 16,
        fontWeight: "800",
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
    modeSwitch: {
        flexDirection: "row",
        alignSelf: "stretch",
        borderRadius: radii.lg,
        backgroundColor: colors.surfaceMuted,
        borderWidth: 1,
        borderColor: colors.border,
        padding: 4,
        marginBottom: spacing.lg,
    },
    modeButton: {
        flex: 1,
        alignItems: "center",
        justifyContent: "center",
        borderRadius: radii.md,
        minHeight: 42,
    },
    modeButtonActive: {
        backgroundColor: colors.surface,
    },
    modeText: {
        color: colors.textMuted,
        fontSize: 14,
        fontWeight: "700",
    },
    modeTextActive: {
        color: colors.text,
    },
    form: {
        gap: spacing.lg,
    },
    field: {
        gap: 8,
    },
    label: {
        color: colors.text,
        fontSize: 14,
        fontWeight: "700",
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
    savedConnectionsCard: {
        gap: spacing.md,
        backgroundColor: colors.surface,
    },
    savedHeaderRow: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: spacing.sm,
    },
    savedTitle: {
        color: colors.text,
        fontSize: 16,
        fontWeight: "900",
    },
    savedHint: {
        color: colors.textSoft,
        fontSize: 12,
        fontWeight: "800",
    },
    savedProfilesList: {
        gap: spacing.sm,
    },
    savedProfileCard: {
        minHeight: 68,
        borderRadius: radii.md,
        borderWidth: 1,
        borderColor: colors.border,
        backgroundColor: colors.surface,
        paddingHorizontal: 12,
        paddingVertical: 10,
        flexDirection: "row",
        alignItems: "center",
        gap: spacing.sm,
    },
    savedProfileCardActive: {
        borderColor: "rgba(124,58,237,0.34)",
        backgroundColor: "rgba(124,58,237,0.035)",
    },
    savedProfileBody: {
        flex: 1,
        gap: 4,
        minWidth: 0,
    },
    savedProfileTitle: {
        color: colors.text,
        fontSize: 14,
        fontWeight: "900",
    },
    savedProfileUrl: {
        color: colors.textMuted,
        fontSize: 12,
        fontWeight: "600",
    },
    savedCurrentBadge: {
        borderRadius: radii.pill,
        paddingHorizontal: 8,
        paddingVertical: 4,
        backgroundColor: "rgba(16,185,129,0.12)",
    },
    savedCurrentBadgeText: {
        color: colors.success,
        fontSize: 10,
        fontWeight: "900",
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
});
