import { memo } from "react";
import { Alert, StyleSheet, Text, View } from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { Button } from "@/src/components/ui/button";
import { Card, CardContent } from "@/src/components/ui/card";
import { downloadUrlToUserSelectedFile } from "@/src/lib/file-transfer";
import { useUiPrefs } from "@/src/providers/ui-prefs";

export const DownloadFileCard = memo(function DownloadFileCard({
    url,
    filename,
    mimeType,
}: {
    url: string;
    filename?: string;
    mimeType?: string;
}) {
    const { colors, t } = useUiPrefs();
    const displayFilename = filename || decodeURIComponent(url.split("/").pop()?.split("?")[0] || "file");
    const displayType = mimeType || t("文件", "File");
    const handleDownload = async () => {
        try {
            const saved = await downloadUrlToUserSelectedFile(url, {
                filename: displayFilename,
                mimeType,
                prefix: "file",
            });
            Alert.alert(
                t("已下载", "Downloaded"),
                saved.userVisible
                    ? `${t("文件已保存到你选择的系统文件夹", "Saved to the folder you selected")}：${saved.filename}`
                    : `${t("文件已保存到应用沙盒", "Saved to app sandbox")}：${saved.uri}`,
            );
        } catch (error) {
            Alert.alert(t("下载失败", "Download failed"), error instanceof Error ? error.message : t("无法保存文件", "Unable to save file"));
        }
    };

    return (
        <Card style={styles.card}>
            <CardContent style={styles.cardContent}>
                <View style={[styles.iconBox, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                    <MaterialCommunityIcons name="file-download-outline" size={22} color={colors.primary} />
                </View>
                <View style={styles.meta}>
                    <Text style={[styles.title, { color: colors.text }]} numberOfLines={1}>
                        {displayFilename}
                    </Text>
                    <Text style={[styles.subtitle, { color: colors.textMuted }]} numberOfLines={1}>
                        {displayType}
                    </Text>
                </View>
                <Button variant="outline" size="sm" onPress={() => void handleDownload()}>
                    {t("下载", "Download")}
                </Button>
            </CardContent>
        </Card>
    );
});

const styles = StyleSheet.create({
    card: {
        borderRadius: 18,
    },
    cardContent: {
        minHeight: 58,
        flexDirection: "row",
        alignItems: "center",
        gap: 10,
        paddingHorizontal: 10,
        paddingVertical: 9,
    },
    iconBox: {
        width: 38,
        height: 38,
        borderRadius: 13,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    meta: {
        flex: 1,
        gap: 2,
        minWidth: 0,
    },
    title: {
        fontSize: 13,
        fontWeight: "700",
    },
    subtitle: {
        fontSize: 11,
        lineHeight: 14,
    },
});
