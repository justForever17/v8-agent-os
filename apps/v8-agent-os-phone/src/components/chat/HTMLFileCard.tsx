import { memo, useMemo, useState } from "react";
import {
    Alert,
    Modal,
    Pressable,
    StyleSheet,
    Text,
    View,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { WebView } from "react-native-webview";

import { Button } from "@/src/components/ui/button";
import { Card, CardContent } from "@/src/components/ui/card";
import { downloadUrlToUserSelectedFile } from "@/src/lib/file-transfer";
import { useAppSession } from "@/src/providers/app-session";
import { useUiPrefs } from "@/src/providers/ui-prefs";

export const HTMLFileCard = memo(function HTMLFileCard({
    url,
    filename,
    filesize,
}: {
    url: string;
    filename?: string;
    filesize?: string;
}) {
    const { colors, t } = useUiPrefs();
    const { adminBaseUrl, authorizedFetch } = useAppSession();
    const [isOpen, setIsOpen] = useState(false);

    const safeUrl = useMemo(() => {
        try {
            return new URL(url).toString();
        } catch {
            return url;
        }
    }, [url]);

    const displayFilename = filename || decodeURIComponent(url.split("/").pop()?.split("?")[0] || "document.html");
    const displaySize = filesize || "HTML";
    const handleDownload = async () => {
        try {
            const saved = await downloadUrlToUserSelectedFile(safeUrl, {
                filename: displayFilename,
                mimeType: "text/html",
                prefix: "html",
                adminBaseUrl,
                authorizedFetch,
            });
            Alert.alert(
                t("已下载", "Downloaded"),
                saved.shared
                    ? `${t("已打开系统分享/保存到文件面板", "Opened the system share / Save to Files sheet")}：${saved.filename}`
                    : saved.userVisible
                    ? `${t("文件已保存到你选择的系统文件夹", "Saved to the folder you selected")}：${saved.filename}`
                    : `${t("文件已保存到应用沙盒", "Saved to app sandbox")}：${saved.uri}`,
            );
        } catch (error) {
            Alert.alert(t("下载失败", "Download failed"), error instanceof Error ? error.message : t("无法保存 HTML 文件", "Unable to save HTML file"));
        }
    };

    return (
        <>
            <Card style={styles.card}>
                <CardContent style={styles.cardContent}>
                    <View style={[styles.iconBox, { backgroundColor: "rgba(59,130,246,0.12)" }]}>
                        <MaterialCommunityIcons name="view-dashboard-outline" size={26} color="#2563EB" />
                    </View>

                    <View style={styles.meta}>
                        <Text style={[styles.title, { color: colors.text }]} numberOfLines={1}>
                            {displayFilename}
                        </Text>
                        <Text style={[styles.subtitle, { color: colors.textMuted }]}>{displaySize}</Text>
                    </View>

                    <View style={styles.actions}>
                        <Button variant="ghost" size="icon" onPress={() => setIsOpen(true)}>
                            <MaterialCommunityIcons name="arrow-expand" size={16} color={colors.textMuted} />
                        </Button>
                        <Button variant="ghost" size="icon" onPress={() => void handleDownload()}>
                            <MaterialCommunityIcons name="download" size={16} color={colors.textMuted} />
                        </Button>
                    </View>
                </CardContent>
            </Card>

            <Modal visible={isOpen} transparent animationType="fade" onRequestClose={() => setIsOpen(false)}>
                <View style={[styles.overlay, { backgroundColor: colors.overlay }]}>
                    <Pressable style={StyleSheet.absoluteFill} onPress={() => setIsOpen(false)} />
                    <View style={[styles.modalCard, { backgroundColor: colors.surface, borderColor: colors.border }]}>
                        <View style={[styles.modalHeader, { borderBottomColor: colors.border, backgroundColor: colors.surfaceStrong }]}>
                            <View style={styles.modalTitleWrap}>
                                <View style={[styles.modalIconBox, { backgroundColor: "rgba(59,130,246,0.12)" }]}>
                                    <MaterialCommunityIcons name="view-dashboard-outline" size={18} color="#2563EB" />
                                </View>
                                <Text style={[styles.modalTitle, { color: colors.text }]} numberOfLines={1}>
                                    {displayFilename}
                                </Text>
                            </View>
                            <View style={styles.modalActions}>
                                <Button variant="outline" size="sm" onPress={() => void handleDownload()}>
                                    {t("下载", "Download")}
                                </Button>
                                <Button variant="ghost" size="icon" onPress={() => setIsOpen(false)}>
                                    <MaterialCommunityIcons name="close" size={18} color={colors.text} />
                                </Button>
                            </View>
                        </View>
                        <View style={styles.viewerWrap}>
                            <WebView source={{ uri: safeUrl }} style={styles.webview} />
                        </View>
                    </View>
                </View>
            </Modal>
        </>
    );
});

const styles = StyleSheet.create({
    card: {
        borderRadius: 18,
    },
    cardContent: {
        flexDirection: "row",
        alignItems: "center",
        gap: 12,
        paddingHorizontal: 12,
        paddingVertical: 12,
    },
    iconBox: {
        width: 48,
        height: 48,
        borderRadius: 14,
        alignItems: "center",
        justifyContent: "center",
    },
    meta: {
        flex: 1,
        gap: 4,
    },
    title: {
        fontSize: 14,
        fontWeight: "600",
    },
    subtitle: {
        fontSize: 12,
    },
    actions: {
        flexDirection: "row",
        alignItems: "center",
        gap: 4,
    },
    overlay: {
        flex: 1,
        justifyContent: "center",
        paddingHorizontal: 12,
        paddingVertical: 24,
    },
    modalCard: {
        borderWidth: 1,
        borderRadius: 22,
        overflow: "hidden",
        maxHeight: "90%",
    },
    modalHeader: {
        minHeight: 54,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 12,
        paddingHorizontal: 14,
        paddingVertical: 10,
        borderBottomWidth: 1,
    },
    modalTitleWrap: {
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
        flex: 1,
    },
    modalIconBox: {
        width: 34,
        height: 34,
        borderRadius: 12,
        alignItems: "center",
        justifyContent: "center",
    },
    modalTitle: {
        flex: 1,
        fontSize: 15,
        fontWeight: "700",
    },
    modalActions: {
        flexDirection: "row",
        alignItems: "center",
        gap: 6,
    },
    viewerWrap: {
        width: "100%",
        minHeight: 520,
    },
    webview: {
        minHeight: 520,
        backgroundColor: "transparent",
    },
});
