import { Image, StyleSheet, View } from "react-native";
import { useVideoPlayer, VideoView } from "expo-video";
import { useEffect } from "react";
import { useIsFocused } from "@react-navigation/native";
import { useAppVisibility } from "@/src/hooks/use-app-visibility";

export function PhoneBackgroundMedia({
    uri,
    mediaType,
}: {
    uri: string;
    mediaType: "image" | "video";
}) {
    const focused = useIsFocused();
    const visible = useAppVisibility();
    const videoUri = mediaType === "video" && focused && visible ? uri : "";
    const player = useVideoPlayer(videoUri || null, (nextPlayer) => {
        nextPlayer.loop = true;
        nextPlayer.muted = true;
        if (videoUri) nextPlayer.play();
    });
    useEffect(() => {
        if (videoUri) player.play();
        else player.pause();
        return () => player.pause();
    }, [player, videoUri]);

    if (!uri) return null;
    return (
        <View pointerEvents="none" style={StyleSheet.absoluteFillObject}>
            {mediaType === "video" ? (
                <VideoView
                    player={player}
                    nativeControls={false}
                    contentFit="cover"
                    style={StyleSheet.absoluteFillObject}
                    surfaceType="textureView"
                />
            ) : (
                <Image source={{ uri }} resizeMode="cover" style={StyleSheet.absoluteFillObject} />
            )}
        </View>
    );
}
