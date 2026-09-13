import { Image, StyleSheet, View } from "react-native";
import { useVideoPlayer, VideoView } from "expo-video";
import { useIsFocused } from "@react-navigation/native";
import { useAppVisibility } from "@/src/hooks/use-app-visibility";

function BackgroundVideo({ uri }: { uri: string }) {
    // Expo owns release on source change/unmount. No consumer cleanup may call
    // the shared player after that release (including profile tree replacement).
    const player = useVideoPlayer(uri, (nextPlayer) => {
        nextPlayer.loop = true;
        nextPlayer.muted = true;
        nextPlayer.play();
    });
    return <VideoView player={player} nativeControls={false} contentFit="cover"
        style={StyleSheet.absoluteFillObject} surfaceType="textureView" />;
}

export function PhoneBackgroundMedia({
    uri,
    mediaType,
}: {
    uri: string;
    mediaType: "image" | "video";
}) {
    const focused = useIsFocused();
    const visible = useAppVisibility();
    if (!uri) return null;
    return (
        <View pointerEvents="none" style={StyleSheet.absoluteFillObject}>
            {mediaType === "video" ? (
                focused && visible ? <BackgroundVideo key={uri} uri={uri} /> : null
            ) : (
                <Image source={{ uri }} resizeMode="cover" style={StyleSheet.absoluteFillObject} />
            )}
        </View>
    );
}
