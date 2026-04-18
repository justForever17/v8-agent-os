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
import { PhoneWordmark } from "@/src/components/layout/PhoneTopbar";
import { readActiveAdminConnectionProfileId, readAdminConnectionProfiles } from "@/src/lib/admin-connection-profiles";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, radii, spacing } from "@/src/theme/tokens";

const BRAND_MARK = require("../assets/images/brand-mark.png");

type Mode = "login" | "register";

export default function LoginScreen() {
    const { status, adminBaseUrl, signIn, signUp, user } = useAppSession();
    const { t, locale, toggleLocale } = useUiPrefs();
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
        if (adminBaseUrl || defaultWebBaseUrl) {
            return undefined;
        }
        let cancelled = false;
        const hydrateSavedConnection = async () => {
            const [profiles, activeId] = await Promise.all([
                readAdminConnectionProfiles(),
                readActiveAdminConnectionProfileId(),
            ]);
            const activeProfile = profiles.find((profile) => profile.id === activeId) || profiles[0];
            if (!cancelled && activeProfile?.adminBaseUrl) {
                setBaseUrl((current) => current || activeProfile.adminBaseUrl);
            }
        };
        void hydrateSavedConnection();
        return () => {
            cancelled = true;
        };
    }, [adminBaseUrl, defaultWebBaseUrl]);

    const pageTitle = useMemo(
        () => (mode === "login" ? t("欢迎回来", "Welcome back") : t("创建账号", "Create account")),
        [mode, t],
    );
    const pageSubtitle = useMemo(
        () =>
            mode === "login"
                ? t("使用和 Web 端完全一致的用户账号进入 V8 OS Phone。手机端直接连接 Admin 作为用户面 BFF。", "Sign in with the same account you use on Web. Phone connects to Admin as the user-surface BFF.")
                : t("这里复刻 Web 端注册链路。创建完成后会自动登录，并进入与 Web 同一条用户主链。", "This mirrors the Web sign-up flow. After registration, you will be signed in automatically and enter the same user runtime lane as Web."),
        [mode, t],
    );

    if (status === "authenticated") {
        return <Redirect href={(user?.mustChangePassword ? "/settings" : "/chat") as Href} />;
    }

    const resetError = () => setError("");

    const validate = () => {
        if (!baseUrl.trim()) {
            setError(t("请填写可访问的 Admin 地址", "Please enter a reachable Admin URL"));
            return false;
        }
        if (!login.trim() || !password.trim()) {
            setError(t("请填写登录名和密码", "Please enter your login and password"));
            return false;
        }
        if (mode === "register" && !name.trim()) {
            setError(t("注册时需要填写昵称", "Display name is required when registering"));
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
                        ? t("登录失败", "Sign-in failed")
                        : t("注册失败", "Registration failed"),
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
                            <Pressable
                                accessibilityLabel={t("语言切换", "Toggle language")}
                                accessibilityRole="button"
                                onPress={() => void toggleLocale()}
                                style={styles.localeToggle}
                            >
                                <Text style={styles.localeToggleText}>{locale === "en" ? "EN" : "中"}</Text>
                            </Pressable>
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
                                <Text style={[styles.modeText, mode === "login" && styles.modeTextActive]}>{t("登录", "Sign in")}</Text>
                            </Pressable>
                            <Pressable
                                style={[styles.modeButton, mode === "register" && styles.modeButtonActive]}
                                onPress={() => {
                                    setMode("register");
                                    resetError();
                                }}
                            >
                                <Text style={[styles.modeText, mode === "register" && styles.modeTextActive]}>{t("注册", "Register")}</Text>
                            </Pressable>
                        </View>

                        <View style={styles.form}>
                            <View style={styles.field}>
                                <Text style={styles.label}>{t("Admin 地址", "Admin URL")}</Text>
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
                                    <Text style={styles.label}>{t("昵称", "Display name")}</Text>
                                    <TextInput
                                        value={name}
                                        onChangeText={(next) => {
                                            setName(next);
                                            resetError();
                                        }}
                                        placeholder={t("怎么称呼你？", "How should we address you?")}
                                        placeholderTextColor={colors.textSoft}
                                        style={styles.input}
                                    />
                                </View>
                            ) : null}

                            <View style={styles.field}>
                                <Text style={styles.label}>{t("登录名", "Login")}</Text>
                                <TextInput
                                    autoCapitalize="none"
                                    autoCorrect={false}
                                    value={login}
                                    onChangeText={(next) => {
                                        setLogin(next);
                                        resetError();
                                    }}
                                    placeholder={mode === "login" ? t("输入 Web 端同一登录名或邮箱", "Use the same login or email as Web") : t("设置一个登录名", "Choose a login name")}
                                    placeholderTextColor={colors.textSoft}
                                    style={styles.input}
                                />
                            </View>

                            {mode === "register" ? (
                                <View style={styles.field}>
                                    <Text style={styles.label}>{t("邮箱", "Email")}</Text>
                                    <TextInput
                                        autoCapitalize="none"
                                        autoCorrect={false}
                                        keyboardType="email-address"
                                        value={email}
                                        onChangeText={(next) => {
                                            setEmail(next);
                                            resetError();
                                        }}
                                        placeholder={t("可选，便于同步头像与通知", "Optional, useful for avatar sync and notifications")}
                                        placeholderTextColor={colors.textSoft}
                                        style={styles.input}
                                    />
                                </View>
                            ) : null}

                            <View style={styles.field}>
                                <Text style={styles.label}>{mode === "login" ? t("密码", "Password") : t("设置密码", "Set password")}</Text>
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
                                                ? t("登录并进入 V8 OS Phone", "Sign in to V8 OS Phone")
                                                : t("创建账号并进入 V8 OS Phone", "Create account and enter V8 OS Phone")}
                                        </Text>
                                    )}
                                </LinearGradient>
                            </Pressable>
                        </View>
                    </GlassCard>

                    <Pressable style={styles.connectHint} onPress={() => router.push("/connect" as Href)}>
                        <MaterialCommunityIcons name="lan-connect" size={16} color={colors.textMuted} />
                        <Text style={styles.connectHintText}>{t("先检查连接地址", "Check the connection first")}</Text>
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
    localeToggle: {
        width: 36,
        height: 36,
        borderRadius: 12,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(255,255,255,0.78)",
        borderWidth: 1,
        borderColor: colors.border,
        shadowColor: "#0F172A",
        shadowOpacity: 0.04,
        shadowRadius: 8,
        shadowOffset: { width: 0, height: 3 },
        elevation: 1,
    },
    localeToggleText: {
        color: colors.textMuted,
        fontSize: 10.5,
        fontWeight: "800",
        letterSpacing: 0.4,
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
