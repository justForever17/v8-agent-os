import { useEffect, useState } from "react";
import {
    ActivityIndicator,
    Alert,
    Image,
    Pressable,
    ScrollView,
    StyleSheet,
    Text,
    TextInput,
    View,
} from "react-native";
import { router } from "expo-router";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import * as ImagePicker from "expo-image-picker";

import { GlassCard } from "@/src/components/common/GlassCard";
import { resolveAdminAssetUrl } from "@/src/lib/admin-client";
import { getCurrentProfile, updateProfile, uploadUserAvatar } from "@/src/lib/phone-api";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";
import { colors, radii, spacing } from "@/src/theme/tokens";

export function ProfileMenuOverlay({
    visible,
    onClose,
}: {
    visible: boolean;
    onClose: () => void;
}) {
    const { status, user, adminBaseUrl, signOut, authorizedFetch, refreshUser, updateCurrentUser } = useAppSession();
    const { t } = useUiPrefs();
    const [name, setName] = useState(user?.name || "");
    const [email, setEmail] = useState(user?.email || "");
    const [image, setImage] = useState(user?.image || "");
    const [profileBusy, setProfileBusy] = useState(false);
    const [avatarBusy, setAvatarBusy] = useState(false);
    const [profileMessage, setProfileMessage] = useState("");
    const [loadingProfile, setLoadingProfile] = useState(false);

    useEffect(() => {
        setName(user?.name || "");
        setEmail(user?.email || "");
        setImage(user?.image || "");
    }, [user?.email, user?.image, user?.name]);

    useEffect(() => {
        if (!visible || status !== "authenticated") {
            return;
        }
        let cancelled = false;
        const hydrateProfile = async () => {
            setLoadingProfile(true);
            try {
                const profile = await getCurrentProfile(authorizedFetch);
                if (!cancelled && profile) {
                    setName(profile.name || "");
                    setEmail(profile.email || "");
                    setImage(profile.image || "");
                    await updateCurrentUser(profile);
                }
            } catch {
                // Keep using the existing session user info here to avoid a hard failure.
            } finally {
                if (!cancelled) {
                    setLoadingProfile(false);
                }
            }
        };
        void hydrateProfile();
        return () => {
            cancelled = true;
        };
    }, [authorizedFetch, status, updateCurrentUser, visible]);

    const avatarUri = image ? resolveAdminAssetUrl(adminBaseUrl, image) : "";

    const saveProfile = async () => {
        setProfileBusy(true);
        setProfileMessage("");
        try {
            const updated = await updateProfile(authorizedFetch, {
                name: name.trim() || undefined,
                email: email.trim() || undefined,
                image: image || undefined,
            });
            if (updated) {
                await updateCurrentUser(updated);
            } else {
                await refreshUser();
            }
            setProfileMessage(t("src.screens.settingsscreen.profile_synced_to_the_current_user_runtime_lane"));
        } catch (error) {
            Alert.alert(t("src.components.chat.mediaviewerlightbox.save_failed"), error instanceof Error ? error.message : t("src.screens.settingsscreen.unable_to_update_the_profile"));
        } finally {
            setProfileBusy(false);
        }
    };

    const pickAvatar = async () => {
        setAvatarBusy(true);
        setProfileMessage("");
        try {
            const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
            if (!permission.granted) {
                throw new Error(t("src.screens.settingsscreen.photo_library_permission_is_required_to_upload_an_avatar"));
            }
            const result = await ImagePicker.launchImageLibraryAsync({
                mediaTypes: ["images"],
                quality: 0.9,
                allowsEditing: true,
                aspect: [1, 1],
            });
            if (result.canceled || !result.assets[0]) {
                return;
            }
            const asset = result.assets[0];
            const uploaded = await uploadUserAvatar(authorizedFetch, {
                uri: asset.uri,
                name: asset.fileName || `avatar-${Date.now()}.jpg`,
                type: asset.mimeType || "image/jpeg",
            });
            if (!uploaded.url) {
                throw new Error(t("src.screens.settingsscreen.the_avatar_upload_succeeded_but_no_url_was_returned"));
            }
            setImage(uploaded.url);
            const updated = await updateProfile(authorizedFetch, {
                name: name.trim() || undefined,
                email: email.trim() || undefined,
                image: uploaded.url,
            });
            if (updated) {
                await updateCurrentUser(updated);
            } else {
                await refreshUser();
            }
            setProfileMessage(t("src.screens.settingsscreen.avatar_updated"));
        } catch (error) {
            Alert.alert(t("src.screens.settingsscreen.avatar_update_failed"), error instanceof Error ? error.message : t("src.screens.settingsscreen.unable_to_update_the_avatar"));
        } finally {
            setAvatarBusy(false);
        }
    };

    const handleSignOut = async () => {
        try {
            await signOut();
            onClose();
            router.replace("/login");
        } catch (error) {
            Alert.alert("退出失败", error instanceof Error ? error.message : "无法退出登录");
        }
    };

    if (!visible) return null;

    return (
        <View style={StyleSheet.absoluteFillObject} pointerEvents="auto">
            {/* 半透明背景点击遮罩 */}
            <Pressable style={styles.backdrop} onPress={onClose} />
            <View style={styles.modalContainer} pointerEvents="box-none">
                <GlassCard style={styles.card}>
                    <View style={styles.header}>
                        <MaterialCommunityIcons name="account-circle-outline" size={20} color={colors.primaryDeep} />
                        <Text style={styles.title}>个人中心</Text>
                        <Pressable style={styles.closeButton} onPress={onClose}>
                            <MaterialCommunityIcons name="close" size={20} color={colors.textMuted} />
                        </Pressable>
                    </View>

                    <ScrollView
                        style={styles.scrollView}
                        contentContainerStyle={styles.scrollContent}
                        showsVerticalScrollIndicator={false}
                    >
                        {/* 用户头像与基本信息 */}
                        <View style={styles.userRow}>
                            {avatarUri ? (
                                <Image source={{ uri: avatarUri }} style={styles.userAvatarImage} />
                            ) : (
                                <View style={styles.userAvatar}>
                                    <Text style={styles.userAvatarText}>{(user?.name || user?.login || "V").slice(0, 1).toUpperCase()}</Text>
                                </View>
                            )}
                            <View style={styles.userBody}>
                                <Text style={styles.userName}>{user?.name || user?.login || t("src.screens.connectscreen.unknown_user")}</Text>
                                <Text style={styles.userMeta}>{user?.email || t("src.screens.connectscreen.no_email_provided")}</Text>
                            </View>
                        </View>

                        {/* 修改 Profile */}
                        <View style={styles.section}>
                            <View style={styles.sectionTitleRow}>
                                <Text style={styles.sectionTitle}>{t("src.screens.settingsscreen.profile")}</Text>
                                {loadingProfile ? <ActivityIndicator color={colors.primary} size="small" /> : null}
                            </View>

                            <View style={styles.profileEditContainer}>
                                <Pressable style={styles.avatarPicker} onPress={() => void pickAvatar()} disabled={avatarBusy}>
                                    {avatarUri ? (
                                        <Image source={{ uri: avatarUri }} style={styles.avatarPickerImage} />
                                    ) : (
                                        <View style={styles.avatarPickerFallback}>
                                            <Text style={styles.avatarPickerText}>{(name || user?.login || "V").slice(0, 1).toUpperCase()}</Text>
                                        </View>
                                    )}
                                    <View style={styles.avatarPickerBadge}>
                                        {avatarBusy ? (
                                            <ActivityIndicator color="#FFFFFF" size="small" />
                                        ) : (
                                            <MaterialCommunityIcons name="camera-plus-outline" size={12} color="#FFFFFF" />
                                        )}
                                    </View>
                                </Pressable>

                                <View style={styles.profileFields}>
                                    <View style={styles.field}>
                                        <Text style={styles.fieldLabel}>{t("src.screens.settingsscreen.display_name")}</Text>
                                        <TextInput
                                            value={name}
                                            onChangeText={setName}
                                            placeholder={t("src.screens.settingsscreen.your_display_name")}
                                            placeholderTextColor={colors.textSoft}
                                            style={styles.input}
                                        />
                                    </View>
                                    <View style={styles.field}>
                                        <Text style={styles.fieldLabel}>初始身份</Text>
                                        <TextInput
                                            value={email}
                                            editable={false}
                                            style={[styles.input, { backgroundColor: "rgba(148, 163, 184, 0.08)", color: colors.textMuted }]}
                                        />
                                    </View>
                                </View>
                            </View>

                            {profileMessage ? <Text style={styles.successText}>{profileMessage}</Text> : null}
                            
                            <Pressable style={[styles.primaryButton, profileBusy && styles.disabled]} onPress={() => void saveProfile()}>
                                {profileBusy ? <ActivityIndicator color="#FFFFFF" /> : <Text style={styles.primaryButtonText}>{t("src.screens.settingsscreen.save_profile")}</Text>}
                            </Pressable>
                        </View>

                        {/* 退出登录按钮 */}
                        <Pressable style={styles.logoutButton} onPress={() => void handleSignOut()}>
                            <MaterialCommunityIcons name="logout" size={16} color={colors.danger} />
                            <Text style={styles.logoutButtonText}>{t("src.screens.settingsscreen.sign_out")}</Text>
                        </Pressable>
                    </ScrollView>
                </GlassCard>
            </View>
        </View>
    );
}

const styles = StyleSheet.create({
    backdrop: {
        ...StyleSheet.absoluteFillObject,
        backgroundColor: "rgba(15, 23, 42, 0.42)",
        zIndex: 100,
    },
    modalContainer: {
        ...StyleSheet.absoluteFillObject,
        justifyContent: "flex-end",
        zIndex: 101,
    },
    card: {
        backgroundColor: "rgba(255, 255, 255, 0.95)",
        borderTopLeftRadius: 24,
        borderTopRightRadius: 24,
        padding: 16,
        maxHeight: "85%",
        shadowOpacity: 0.15,
        shadowRadius: 15,
        shadowOffset: { width: 0, height: -5 },
        elevation: 10,
    },
    header: {
        flexDirection: "row",
        alignItems: "center",
        paddingBottom: 12,
        borderBottomWidth: 1,
        borderColor: "rgba(148, 163, 184, 0.12)",
    },
    title: {
        flex: 1,
        color: colors.text,
        fontSize: 16,
        fontWeight: "800",
        marginLeft: 8,
    },
    closeButton: {
        padding: 4,
    },
    scrollView: {
        marginTop: 10,
    },
    scrollContent: {
        paddingBottom: 24,
        gap: 16,
    },
    userRow: {
        flexDirection: "row",
        alignItems: "center",
        backgroundColor: "rgba(148, 163, 184, 0.06)",
        padding: 12,
        borderRadius: 16,
        gap: 12,
    },
    userAvatarImage: {
        width: 48,
        height: 48,
        borderRadius: 24,
    },
    userAvatar: {
        width: 48,
        height: 48,
        borderRadius: 24,
        backgroundColor: colors.primarySoft,
        alignItems: "center",
        justifyContent: "center",
    },
    userAvatarText: {
        color: colors.primary,
        fontSize: 18,
        fontWeight: "900",
    },
    userBody: {
        flex: 1,
        gap: 2,
    },
    userName: {
        color: colors.text,
        fontSize: 15,
        fontWeight: "800",
    },
    userMeta: {
        color: colors.textMuted,
        fontSize: 12,
    },
    section: {
        backgroundColor: "rgba(255, 255, 255, 0.5)",
        borderRadius: 16,
        padding: 12,
        borderWidth: 1,
        borderColor: "rgba(148, 163, 184, 0.08)",
        gap: 12,
    },
    sectionTitleRow: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
    },
    sectionTitle: {
        color: colors.text,
        fontSize: 13,
        fontWeight: "800",
    },
    profileEditContainer: {
        flexDirection: "row",
        gap: 12,
        alignItems: "flex-start",
    },
    avatarPicker: {
        position: "relative",
    },
    avatarPickerImage: {
        width: 56,
        height: 56,
        borderRadius: 28,
    },
    avatarPickerFallback: {
        width: 56,
        height: 56,
        borderRadius: 28,
        backgroundColor: "rgba(148, 163, 184, 0.08)",
        borderWidth: 1,
        borderColor: "rgba(148, 163, 184, 0.15)",
        alignItems: "center",
        justifyContent: "center",
    },
    avatarPickerText: {
        color: colors.textSoft,
        fontSize: 20,
        fontWeight: "800",
    },
    avatarPickerBadge: {
        position: "absolute",
        right: 0,
        bottom: 0,
        width: 20,
        height: 20,
        borderRadius: 10,
        backgroundColor: colors.primary,
        alignItems: "center",
        justifyContent: "center",
        borderWidth: 1.5,
        borderColor: "#FFFFFF",
    },
    profileFields: {
        flex: 1,
        gap: 8,
    },
    field: {
        gap: 4,
    },
    fieldLabel: {
        fontSize: 11,
        fontWeight: "700",
        color: colors.textMuted,
    },
    input: {
        borderWidth: 1,
        borderColor: "rgba(148, 163, 184, 0.2)",
        borderRadius: 8,
        paddingHorizontal: 10,
        paddingVertical: 6,
        fontSize: 12,
        color: colors.text,
        backgroundColor: "#FFFFFF",
    },
    successText: {
        fontSize: 11,
        color: "#10B981",
        fontWeight: "700",
        textAlign: "center",
    },
    primaryButton: {
        backgroundColor: colors.primary,
        borderRadius: 10,
        height: 36,
        alignItems: "center",
        justifyContent: "center",
    },
    primaryButtonText: {
        color: "#FFFFFF",
        fontSize: 13,
        fontWeight: "800",
    },
    logoutButton: {
        flexDirection: "row",
        borderWidth: 1,
        borderColor: "rgba(239, 68, 68, 0.15)",
        borderRadius: 12,
        height: 38,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(239, 68, 68, 0.04)",
        gap: 6,
    },
    logoutButtonText: {
        color: colors.danger,
        fontSize: 13,
        fontWeight: "800",
    },
    disabled: {
        opacity: 0.6,
    },
});
