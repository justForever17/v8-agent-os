import { Image, StyleSheet, View } from "react-native";
import { useVideoPlayer, VideoView } from "expo-video";

export function PhoneBackgroundMedia({
    uri,
    mediaType,
}: {
    uri: string;
    mediaType: "image" | "video";
}) {
    const videoUri = mediaType === "video" ? uri : "";
    const player = useVideoPlayer(videoUri || null, (nextPlayer) => {
        nextPlayer.loop = true;
        nextPlayer.muted = true;
        if (videoUri) nextPlayer.play();
    });

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
