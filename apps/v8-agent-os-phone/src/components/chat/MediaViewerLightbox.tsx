import { memo, useEffect, useState } from "react";
import { Image, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { WebView } from "react-native-webview";
import { MaterialCommunityIcons } from "@expo/vector-icons";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

export type MediaItem = {
    type: "image" | "video";
    src: string;
    name?: string;
};

export const MediaViewerLightbox = memo(function MediaViewerLightbox({
    items,
    initialIndex = 0,
    isOpen,
    onClose,
}: {
    items: MediaItem[];
    initialIndex?: number;
    isOpen: boolean;
    onClose: () => void;
}) {
    const { colors } = useUiPrefs();
    const [currentIndex, setCurrentIndex] = useState(initialIndex);

    useEffect(() => {
        if (isOpen) {
            setCurrentIndex(initialIndex);
        }
    }, [initialIndex, isOpen]);

    if (!isOpen || items.length === 0) {
        return null;
    }

    const current = items[currentIndex];

    return (
        <Modal transparent visible animationType="fade" onRequestClose={onClose}>
            <View style={styles.overlay}>
                <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
                <View style={styles.topBar}>
                    <Text style={styles.topBarText}>
                        {items.length > 1 ? `${currentIndex + 1} / ${items.length}` : ""}
                        {current.name ? `  ${current.name}` : ""}
                    </Text>
                    <Pressable style={styles.iconButton} onPress={onClose}>
                        <MaterialCommunityIcons name="close" size={24} color="#FFFFFF" />
                    </Pressable>
                </View>

                <View style={styles.contentWrap}>
                    {current.type === "video" ? (
                        <WebView
                            source={{ html: buildMediaHtml(current.src, "video") }}
                            style={styles.webView}
                            allowsInlineMediaPlayback
                            mediaPlaybackRequiresUserAction={false}
                        />
                    ) : (
                        <Image source={{ uri: current.src }} style={styles.image} resizeMode="contain" />
                    )}
                </View>

                {items.length > 1 ? (
                    <>
                        <Pressable
                            style={[styles.navButton, styles.leftButton, currentIndex === 0 && styles.navButtonDisabled]}
                            disabled={currentIndex === 0}
                            onPress={() => setCurrentIndex((value) => Math.max(0, value - 1))}
                        >
                            <MaterialCommunityIcons name="chevron-left" size={28} color={currentIndex === 0 ? colors.textSoft : "#FFFFFF"} />
                        </Pressable>
                        <Pressable
                            style={[styles.navButton, styles.rightButton, currentIndex >= items.length - 1 && styles.navButtonDisabled]}
                            disabled={currentIndex >= items.length - 1}
                            onPress={() => setCurrentIndex((value) => Math.min(items.length - 1, value + 1))}
                        >
                            <MaterialCommunityIcons name="chevron-right" size={28} color={currentIndex >= items.length - 1 ? colors.textSoft : "#FFFFFF"} />
                        </Pressable>
                    </>
                ) : null}
            </View>
        </Modal>
    );
});

function buildMediaHtml(src: string, type: "video" | "audio") {
    return `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
    <style>
      html, body { margin:0; padding:0; background:#000; width:100%; height:100%; overflow:hidden; }
      body { display:flex; align-items:center; justify-content:center; }
      ${type} { width:100%; height:100%; max-width:100%; max-height:100%; }
    </style>
  </head>
  <body>
    <${type} src="${src}" controls ${type === "video" ? "autoplay playsinline" : "autoplay"} />
  </body>
</html>`;
}

const styles = StyleSheet.create({
    overlay: {
        flex: 1,
        backgroundColor: "rgba(0,0,0,0.88)",
        justifyContent: "center",
    },
    topBar: {
        position: "absolute",
        left: 0,
        right: 0,
        top: 0,
        zIndex: 10,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "space-between",
        paddingHorizontal: spacing.lg,
        paddingVertical: spacing.md,
    },
    topBarText: {
        color: "#FFFFFF",
        fontSize: 13,
        lineHeight: 18,
        fontWeight: "500",
    },
    iconButton: {
        width: 40,
        height: 40,
        borderRadius: 20,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(255,255,255,0.12)",
    },
    contentWrap: {
        marginHorizontal: spacing.lg,
        minHeight: 260,
        borderRadius: radii.lg,
        overflow: "hidden",
    },
    image: {
        width: "100%",
        height: 420,
    },
    webView: {
        width: "100%",
        height: 420,
        backgroundColor: "#000000",
    },
    navButton: {
        position: "absolute",
        top: "50%",
        marginTop: -24,
        width: 48,
        height: 48,
        borderRadius: 24,
        alignItems: "center",
        justifyContent: "center",
        backgroundColor: "rgba(255,255,255,0.12)",
    },
    leftButton: {
        left: spacing.md,
    },
    rightButton: {
        right: spacing.md,
    },
    navButtonDisabled: {
        opacity: 0.35,
    },
});
