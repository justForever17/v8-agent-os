import React from "react";
import {
    ActivityIndicator,
    Image,
    Modal,
    PanResponder,
    Pressable,
    StyleSheet,
    Text,
    View,
    useWindowDimensions,
} from "react-native";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { ImageManipulator, SaveFormat } from "expo-image-manipulator";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useUiPrefs } from "@/src/providers/ui-prefs";
import { radii, spacing } from "@/src/theme/tokens";

export type AvatarCropSource = {
    uri: string;
    width: number;
    height: number;
    fileName?: string | null;
    mimeType?: string | null;
};

type Point = { x: number; y: number };

function distanceBetweenTouches(touches: readonly { pageX: number; pageY: number }[]): number {
    if (touches.length < 2) return 0;
    return Math.hypot(touches[0].pageX - touches[1].pageX, touches[0].pageY - touches[1].pageY);
}

export function AvatarCropModal({
    source,
    busy = false,
    onCancel,
    onConfirm,
}: {
    source: AvatarCropSource | null;
    busy?: boolean;
    onCancel: () => void;
    onConfirm: (file: { uri: string; name: string; type: string }) => Promise<void> | void;
}) {
    const { width: windowWidth } = useWindowDimensions();
    const insets = useSafeAreaInsets();
    const { colors, t } = useUiPrefs();
    const cropSize = Math.max(220, Math.min(windowWidth - spacing.xl * 2, 360));
    const [zoom, setZoomState] = React.useState(1);
    const [offset, setOffsetState] = React.useState<Point>({ x: 0, y: 0 });
    const [processing, setProcessing] = React.useState(false);
    const zoomRef = React.useRef(1);
    const offsetRef = React.useRef<Point>({ x: 0, y: 0 });
    const dragStartRef = React.useRef<Point>({ x: 0, y: 0 });
    const pinchStartRef = React.useRef<{ distance: number; zoom: number } | null>(null);

    const baseScale = source
        ? Math.max(cropSize / Math.max(1, source.width), cropSize / Math.max(1, source.height))
        : 1;

    const clampOffset = React.useCallback((next: Point, nextZoom = zoomRef.current): Point => {
        if (!source) return { x: 0, y: 0 };
        const renderedWidth = source.width * baseScale * nextZoom;
        const renderedHeight = source.height * baseScale * nextZoom;
        const maxX = Math.max(0, (renderedWidth - cropSize) / 2);
        const maxY = Math.max(0, (renderedHeight - cropSize) / 2);
        return {
            x: Math.max(-maxX, Math.min(maxX, next.x)),
            y: Math.max(-maxY, Math.min(maxY, next.y)),
        };
    }, [baseScale, cropSize, source]);

    const commitOffset = React.useCallback((next: Point, nextZoom = zoomRef.current) => {
        const clamped = clampOffset(next, nextZoom);
        offsetRef.current = clamped;
        setOffsetState(clamped);
    }, [clampOffset]);

    const commitZoom = React.useCallback((next: number) => {
        const clampedZoom = Math.max(1, Math.min(3, next));
        zoomRef.current = clampedZoom;
        setZoomState(clampedZoom);
        commitOffset(offsetRef.current, clampedZoom);
    }, [commitOffset]);

    React.useEffect(() => {
        zoomRef.current = 1;
        offsetRef.current = { x: 0, y: 0 };
        setZoomState(1);
        setOffsetState({ x: 0, y: 0 });
        setProcessing(false);
    }, [source?.uri]);

    const panResponder = React.useMemo(() => PanResponder.create({
        onStartShouldSetPanResponder: () => !busy && !processing,
        onMoveShouldSetPanResponder: (_event, gesture) => !busy && !processing && (Math.abs(gesture.dx) > 2 || Math.abs(gesture.dy) > 2),
        onPanResponderGrant: (event) => {
            dragStartRef.current = offsetRef.current;
            const touches = event.nativeEvent.touches;
            pinchStartRef.current = touches.length >= 2
                ? { distance: distanceBetweenTouches(touches), zoom: zoomRef.current }
                : null;
        },
        onPanResponderMove: (event, gesture) => {
            const touches = event.nativeEvent.touches;
            if (touches.length >= 2) {
                const distance = distanceBetweenTouches(touches);
                if (!pinchStartRef.current || pinchStartRef.current.distance <= 0) {
                    pinchStartRef.current = { distance, zoom: zoomRef.current };
                    return;
                }
                commitZoom(pinchStartRef.current.zoom * (distance / pinchStartRef.current.distance));
                return;
            }
            pinchStartRef.current = null;
            commitOffset({
                x: dragStartRef.current.x + gesture.dx,
                y: dragStartRef.current.y + gesture.dy,
            });
        },
        onPanResponderRelease: () => {
            pinchStartRef.current = null;
        },
        onPanResponderTerminate: () => {
            pinchStartRef.current = null;
        },
    }), [busy, commitOffset, commitZoom, processing]);

    const confirmCrop = React.useCallback(async () => {
        if (!source || busy || processing) return;
        setProcessing(true);
        try {
            const effectiveScale = baseScale * zoomRef.current;
            const cropLength = Math.min(source.width, source.height, cropSize / effectiveScale);
            const originX = Math.max(0, Math.min(
                source.width - cropLength,
                (source.width - cropLength) / 2 - offsetRef.current.x / effectiveScale,
            ));
            const originY = Math.max(0, Math.min(
                source.height - cropLength,
                (source.height - cropLength) / 2 - offsetRef.current.y / effectiveScale,
            ));
            const context = ImageManipulator.manipulate(source.uri);
            context.crop({
                originX: Math.round(originX),
                originY: Math.round(originY),
                width: Math.max(1, Math.floor(cropLength)),
                height: Math.max(1, Math.floor(cropLength)),
            }).resize({ width: 512, height: 512 });
            const rendered = await context.renderAsync();
            const saved = await rendered.saveAsync({ compress: 0.9, format: SaveFormat.JPEG });
            await onConfirm({
                uri: saved.uri,
                name: `avatar-${Date.now()}.jpg`,
                type: "image/jpeg",
            });
        } finally {
            setProcessing(false);
        }
    }, [baseScale, busy, cropSize, onConfirm, processing, source]);

    const renderedWidth = source ? source.width * baseScale : cropSize;
    const renderedHeight = source ? source.height * baseScale : cropSize;
    const isBusy = busy || processing;

    return (
        <Modal
            animationType="fade"
            transparent
            visible={Boolean(source)}
            statusBarTranslucent
            onRequestClose={() => { if (!isBusy) onCancel(); }}
        >
            <View style={[styles.backdrop, { backgroundColor: colors.overlay, paddingTop: insets.top + spacing.lg, paddingBottom: insets.bottom + spacing.lg }]}>
                <View style={[styles.card, { backgroundColor: colors.surfaceStrong, borderColor: colors.border }]}>
                    <View style={styles.header}>
                        <View style={styles.headerCopy}>
                            <Text style={[styles.title, { color: colors.text }]}>{t("src.components.ui.avatarcropmodal.crop_avatar")}</Text>
                            <Text style={[styles.description, { color: colors.textMuted }]}>{t("src.components.ui.avatarcropmodal.drag_and_zoom")}</Text>
                        </View>
                        <Pressable
                            accessibilityRole="button"
                            accessibilityLabel={t("src.components.ui.avatarcropmodal.cancel")}
                            disabled={isBusy}
                            hitSlop={10}
                            onPress={onCancel}
                            style={({ pressed }) => [styles.iconButton, pressed && styles.pressed, isBusy && styles.disabled]}
                        >
                            <MaterialCommunityIcons name="close" size={21} color={colors.textMuted} />
                        </Pressable>
                    </View>

                    <View
                        {...panResponder.panHandlers}
                        accessibilityLabel={t("src.components.ui.avatarcropmodal.crop_area")}
                        style={[styles.viewport, { width: cropSize, height: cropSize, backgroundColor: colors.backgroundDeep }]}
                    >
                        {source ? (
                            <Image
                                resizeMode="cover"
                                source={{ uri: source.uri }}
                                style={{
                                    position: "absolute",
                                    width: renderedWidth,
                                    height: renderedHeight,
                                    left: (cropSize - renderedWidth) / 2,
                                    top: (cropSize - renderedHeight) / 2,
                                    transform: [
                                        { translateX: offset.x },
                                        { translateY: offset.y },
                                        { scale: zoom },
                                    ],
                                }}
                            />
                        ) : null}
                        <View pointerEvents="none" style={styles.cropGuide} />
                        <View pointerEvents="none" style={styles.gridVertical} />
                        <View pointerEvents="none" style={styles.gridHorizontal} />
                    </View>

                    <View style={styles.zoomRow}>
                        <Pressable
                            accessibilityRole="button"
                            accessibilityLabel={t("src.components.ui.avatarcropmodal.zoom_out")}
                            disabled={isBusy || zoom <= 1}
                            onPress={() => commitZoom(zoomRef.current - 0.2)}
                            style={({ pressed }) => [styles.zoomButton, { borderColor: colors.border }, pressed && styles.pressed, (isBusy || zoom <= 1) && styles.disabled]}
                        >
                            <MaterialCommunityIcons name="minus" size={18} color={colors.text} />
                        </Pressable>
                        <View style={styles.zoomValue}>
                            <MaterialCommunityIcons name="magnify" size={16} color={colors.textMuted} />
                            <Text style={[styles.zoomText, { color: colors.textMuted }]}>{Math.round(zoom * 100)}%</Text>
                        </View>
                        <Pressable
                            accessibilityRole="button"
                            accessibilityLabel={t("src.components.ui.avatarcropmodal.zoom_in")}
                            disabled={isBusy || zoom >= 3}
                            onPress={() => commitZoom(zoomRef.current + 0.2)}
                            style={({ pressed }) => [styles.zoomButton, { borderColor: colors.border }, pressed && styles.pressed, (isBusy || zoom >= 3) && styles.disabled]}
                        >
                            <MaterialCommunityIcons name="plus" size={18} color={colors.text} />
                        </Pressable>
                    </View>

                    <View style={styles.actions}>
                        <Pressable
                            accessibilityRole="button"
                            disabled={isBusy}
                            onPress={onCancel}
                            style={({ pressed }) => [styles.secondaryButton, { borderColor: colors.border }, pressed && styles.pressed, isBusy && styles.disabled]}
                        >
                            <Text style={[styles.secondaryText, { color: colors.text }]}>{t("src.components.ui.avatarcropmodal.cancel")}</Text>
                        </Pressable>
                        <Pressable
                            accessibilityRole="button"
                            disabled={isBusy}
                            onPress={() => void confirmCrop()}
                            style={({ pressed }) => [styles.primaryButton, { backgroundColor: colors.primary }, pressed && styles.pressed, isBusy && styles.disabled]}
                        >
                            {isBusy ? <ActivityIndicator size="small" color="#FFFFFF" /> : <MaterialCommunityIcons name="crop" size={17} color="#FFFFFF" />}
                            <Text style={styles.primaryText}>{t("src.components.ui.avatarcropmodal.use_crop")}</Text>
                        </Pressable>
                    </View>
                </View>
            </View>
        </Modal>
    );
}

const styles = StyleSheet.create({
    backdrop: {
        flex: 1,
        alignItems: "center",
        justifyContent: "center",
        paddingHorizontal: spacing.lg,
    },
    card: {
        width: "100%",
        maxWidth: 420,
        borderRadius: radii.lg,
        borderWidth: 1,
        padding: spacing.lg,
        gap: spacing.lg,
        shadowColor: "#000000",
        shadowOpacity: 0.18,
        shadowRadius: 24,
        shadowOffset: { width: 0, height: 12 },
        elevation: 12,
    },
    header: {
        flexDirection: "row",
        alignItems: "flex-start",
        gap: spacing.md,
    },
    headerCopy: {
        flex: 1,
        gap: 4,
    },
    title: {
        fontSize: 19,
        fontWeight: "900",
    },
    description: {
        fontSize: 13,
        lineHeight: 19,
    },
    iconButton: {
        width: 36,
        height: 36,
        alignItems: "center",
        justifyContent: "center",
        borderRadius: 18,
    },
    viewport: {
        alignSelf: "center",
        overflow: "hidden",
        borderRadius: radii.md,
    },
    cropGuide: {
        position: "absolute",
        top: 2,
        right: 2,
        bottom: 2,
        left: 2,
        borderRadius: 999,
        borderWidth: 2,
        borderColor: "rgba(255,255,255,0.92)",
        shadowColor: "#000000",
        shadowOpacity: 0.3,
        shadowRadius: 4,
        shadowOffset: { width: 0, height: 1 },
    },
    gridVertical: {
        position: "absolute",
        top: 2,
        bottom: 2,
        left: "50%",
        width: StyleSheet.hairlineWidth,
        backgroundColor: "rgba(255,255,255,0.28)",
    },
    gridHorizontal: {
        position: "absolute",
        left: 2,
        right: 2,
        top: "50%",
        height: StyleSheet.hairlineWidth,
        backgroundColor: "rgba(255,255,255,0.28)",
    },
    zoomRow: {
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        gap: spacing.md,
    },
    zoomButton: {
        width: 40,
        height: 40,
        borderRadius: 20,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    zoomValue: {
        minWidth: 84,
        flexDirection: "row",
        justifyContent: "center",
        alignItems: "center",
        gap: 6,
    },
    zoomText: {
        fontSize: 13,
        fontWeight: "800",
        fontVariant: ["tabular-nums"],
    },
    actions: {
        flexDirection: "row",
        gap: spacing.sm,
    },
    secondaryButton: {
        flex: 1,
        minHeight: 44,
        borderRadius: radii.md,
        borderWidth: 1,
        alignItems: "center",
        justifyContent: "center",
    },
    secondaryText: {
        fontSize: 14,
        fontWeight: "800",
    },
    primaryButton: {
        flex: 1,
        minHeight: 44,
        borderRadius: radii.md,
        flexDirection: "row",
        alignItems: "center",
        justifyContent: "center",
        gap: 7,
    },
    primaryText: {
        color: "#FFFFFF",
        fontSize: 14,
        fontWeight: "900",
    },
    pressed: {
        opacity: 0.72,
        transform: [{ scale: 0.98 }],
    },
    disabled: {
        opacity: 0.5,
    },
});
